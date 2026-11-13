from __future__ import annotations

from pathlib import Path
import re

from ownpi.runtime.environment.contracts import EnvironmentExecRequest
from ownpi.runtime.environment.host_local import HostLocalEnvironmentProvider
from ownpi.runtime.environment.providers import ResolvedEnvironmentSelection
from ownpi.runtime.environment.subprocess_env import build_subprocess_env
from ownpi.runtime.permissions.models import SandboxEnforcement
from ownpi.runtime.sandbox.contracts import SandboxWrappedCommand


_ENV_KEY_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
_SENSITIVE_ENV_KEY_PARTS = ('KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'PASS', 'CREDENTIAL')
_LOCAL_SSH_ENV_STRIP_KEYS = (
    'HTTP_PROXY',
    'HTTPS_PROXY',
    'ALL_PROXY',
    'NO_PROXY',
    'http_proxy',
    'https_proxy',
    'all_proxy',
    'no_proxy',
)


class SSHRemoteEnvironmentProvider(HostLocalEnvironmentProvider):
    kind = 'ssh_remote'

    def __init__(
        self,
        *,
        project_root: Path,
        selection: ResolvedEnvironmentSelection,
        runtime_config: dict[str, object] | None,
    ) -> None:
        super().__init__(project_root=project_root, selection=selection, runtime_config=runtime_config)
        self._runtime_config = dict(runtime_config or {})

    def prepare(self, *, descriptor, sandbox_policy) -> None:  # type: ignore[override]
        super().prepare(descriptor=descriptor, sandbox_policy=sandbox_policy)
        host = str(self._runtime_config.get('host', '') or '').strip()
        if not host:
            raise RuntimeError('ssh_remote target host is not configured')

    def _wrapped_command(self, request: EnvironmentExecRequest) -> SandboxWrappedCommand:
        effective_permissions = self._effective_permissions(request)
        if effective_permissions.profile.enforcement is not SandboxEnforcement.DISABLED:
            raise RuntimeError(
                'ssh_remote restricted execution requires a compatible remote sandbox protocol'
            )
        host = str(self._runtime_config.get('host', '') or '').strip()
        user = str(self._runtime_config.get('user', '') or '').strip()
        port = int(self._runtime_config.get('port', 22) or 22)
        remote_workspace_root = str(
            self._runtime_config.get('remote_workspace_root', self._descriptor.remote_workspace_root or '') or ''
        ).strip()
        effective_env = self._remote_env(request)
        for key in effective_env:
            _validate_env_key(key)
        if effective_env and request.pty_mode == 'pty':
            raise RuntimeError('ssh_remote env overrides are not supported in pty mode without argv exposure')
        remote_command = (
            '/bin/sh -s'
            if effective_env
            else _remote_command(
                request=request,
                cwd=request.cwd or self._descriptor.workdir or self._descriptor.workspace_root,
                workspace_root=self._descriptor.workspace_root,
                remote_workspace_root=remote_workspace_root,
            )
        )
        ssh_target = f'{user}@{host}' if user else host
        command = ['ssh', '-p', str(port), ssh_target, remote_command]
        if request.pty_mode == 'pty':
            command.insert(1, '-tt')
        # SSH transport does not attest the remote command's sandbox backend.
        return SandboxWrappedCommand(tuple(command), 'unknown')

    def _build_env(self, request: EnvironmentExecRequest) -> dict[str, str]:
        del request
        env = build_subprocess_env()
        env.pop('SSH_ASKPASS', None)
        for key in _LOCAL_SSH_ENV_STRIP_KEYS:
            env.pop(key, None)
        return env

    def _stdin_text(self, request: EnvironmentExecRequest) -> str | None:
        if not self._remote_env(request):
            return request.stdin_text
        remote_workspace_root = str(
            self._runtime_config.get('remote_workspace_root', self._descriptor.remote_workspace_root or '') or ''
        ).strip()
        return _remote_script(
            request=request,
            cwd=request.cwd or self._descriptor.workdir or self._descriptor.workspace_root,
            workspace_root=self._descriptor.workspace_root,
            remote_workspace_root=remote_workspace_root,
            env_overrides=self._remote_env(request),
        )

    def _provider_metadata(self, *, request: EnvironmentExecRequest) -> dict[str, object]:
        metadata = super()._provider_metadata(request=request)
        metadata['sandbox_enforcement_status'] = 'disabled'
        metadata['sandbox_enforcement_note'] = (
            'remote execution is available only under the explicit Full Access profile '
            'until a compatible remote sandbox protocol is configured'
        )
        metadata.update(
            {
                'credential_mode': 'ssh_config_or_agent',
                'remote_target': self._descriptor.remote_target or '',
                'remote_workspace_root': self._descriptor.remote_workspace_root or '',
                'secret_values_recorded': False,
                'env_override_names': sorted(str(key) for key in self._remote_env(request)),
            }
        )
        return metadata

    def _remote_env(self, request: EnvironmentExecRequest) -> dict[str, str]:
        return {**self._proxy_env(), **request.env_overrides}


def _remote_command(
    *,
    request: EnvironmentExecRequest,
    cwd: str,
    workspace_root: str,
    remote_workspace_root: str,
) -> str:
    remote_cwd = _map_remote_path(
        cwd,
        workspace_root=workspace_root,
        remote_workspace_root=remote_workspace_root,
    ) or remote_workspace_root or '.'
    inner_command = _inner_command(request=request, workspace_root=workspace_root, remote_workspace_root=remote_workspace_root)
    if not inner_command:
        raise RuntimeError('ssh_remote provider requires command or shell_command')
    return f"cd {_shell_quote(remote_cwd)} && {inner_command}"


def _remote_script(
    *,
    request: EnvironmentExecRequest,
    cwd: str,
    workspace_root: str,
    remote_workspace_root: str,
    env_overrides: dict[str, str],
) -> str:
    remote_cwd = _map_remote_path(
        cwd,
        workspace_root=workspace_root,
        remote_workspace_root=remote_workspace_root,
    ) or remote_workspace_root or '.'
    lines = ['set -eu', f'cd {_shell_quote(remote_cwd)}']
    for key, value in env_overrides.items():
        _validate_env_key(key)
        mapped_value = _map_remote_path(value, workspace_root=workspace_root, remote_workspace_root=remote_workspace_root)
        lines.append(f'export {key}={_shell_quote(mapped_value)}')
    inner_command = _inner_command(request=request, workspace_root=workspace_root, remote_workspace_root=remote_workspace_root)
    if not inner_command:
        raise RuntimeError('ssh_remote provider requires command or shell_command')
    lines.append(f'exec /bin/sh -lc {_shell_quote(inner_command)}')
    return '\n'.join(lines) + '\n'


def _inner_command(*, request: EnvironmentExecRequest, workspace_root: str, remote_workspace_root: str) -> str:
    if request.shell_command is not None:
        return request.shell_command
    return ' '.join(
        _shell_quote(_map_remote_path(part, workspace_root=workspace_root, remote_workspace_root=remote_workspace_root))
        for part in request.command
    )


def _shell_quote(value: str) -> str:
    escaped = value.replace("'", "'\\''")
    return f"'{escaped}'"


def _validate_env_key(key: str) -> bool:
    normalized = str(key or '').strip()
    if not _ENV_KEY_RE.match(normalized):
        raise RuntimeError(f'invalid ssh env override key: {key!r}')
    upper = normalized.upper()
    if any(part in upper for part in _SENSITIVE_ENV_KEY_PARTS):
        raise RuntimeError(f'ssh_remote refuses to inline sensitive env override: {normalized}')
    return True


def _map_remote_path(value: str, *, workspace_root: str, remote_workspace_root: str) -> str:
    text = str(value or '')
    if not workspace_root or not remote_workspace_root:
        return text
    workspace = str(Path(workspace_root).expanduser().resolve())
    if text == workspace:
        return remote_workspace_root
    if text.startswith(f'{workspace}/'):
        return f'{remote_workspace_root}{text[len(workspace):]}'
    return text
