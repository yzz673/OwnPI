from __future__ import annotations

import shutil
import sys
from pathlib import Path
import re

from ownpi.runtime.environment.contracts import EnvironmentDescriptor, EnvironmentExecRequest, SandboxPolicy
from ownpi.runtime.environment.host_local import HostLocalEnvironmentProvider
from ownpi.runtime.environment.providers import ResolvedEnvironmentSelection
from ownpi.runtime.environment.subprocess_env import build_subprocess_env
from ownpi.runtime.sandbox.contracts import SandboxWrappedCommand


_ENV_KEY_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
_SENSITIVE_ENV_KEY_PARTS = ('KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'PASS', 'CREDENTIAL')
_TRANSPORT_SENSITIVE_ENV_KEYS = {
    'PATH',
    'HOME',
    'SHELL',
    'PWD',
    'USER',
    'LOGNAME',
    'DOCKER_HOST',
    'DOCKER_CONTEXT',
    'DOCKER_CONFIG',
}
_TRANSPORT_SENSITIVE_ENV_PREFIXES = ('LD_', 'DYLD_')


class DockerSandboxEnvironmentProvider(HostLocalEnvironmentProvider):
    kind = 'docker_sandbox'

    def __init__(
        self,
        *,
        project_root: Path,
        selection: ResolvedEnvironmentSelection,
        runtime_config: dict[str, object] | None,
    ) -> None:
        super().__init__(project_root=project_root, selection=selection, runtime_config=runtime_config)
        self._runtime_config = dict(runtime_config or {})

    def prepare(self, *, descriptor: EnvironmentDescriptor, sandbox_policy: SandboxPolicy) -> None:
        super().prepare(descriptor=descriptor, sandbox_policy=sandbox_policy)
        if not bool(self._runtime_config.get('enabled', False)):
            raise RuntimeError('docker_sandbox provider is disabled by runtime.environment.docker.enabled')
        if sandbox_policy.blocked_host_roots:
            raise RuntimeError(
                'docker_sandbox cannot enforce canonical filesystem deny entries'
            )
        if shutil.which('docker') is None:
            raise RuntimeError('docker CLI is unavailable')

    def _wrapped_command(self, request: EnvironmentExecRequest) -> SandboxWrappedCommand:
        image = str(self._runtime_config.get('image', '') or '').strip()
        if not image:
            raise RuntimeError('runtime.environment.docker.image must be configured for docker_sandbox')
        mounts = _docker_mounts(self._descriptor, self._sandbox_policy)
        workdir = _map_container_path(request.cwd or self._descriptor.workdir or self._descriptor.workspace_root or '', mounts) or '/workspace'
        translated = [
            'docker',
            'run',
            '--rm',
            '--workdir',
            workdir,
            '--network',
            'bridge' if self._sandbox_policy.allow_network else 'none',
        ]
        for source, target, mode in mounts:
            translated.extend(['--volume', f'{source}:{target}:{mode}'])
        for key in self._container_env(request, mounts):
            _validate_env_key(key)
            translated.extend(['--env', key])
        translated.append(image)
        translated.extend(_container_command_list(request, mounts))
        return SandboxWrappedCommand(tuple(translated), 'external:docker')

    def _build_env(self, request: EnvironmentExecRequest) -> dict[str, str]:
        mounts = _docker_mounts(self._descriptor, self._sandbox_policy)
        mapped_env = self._container_env(request, mounts)
        return build_subprocess_env(extra_env=mapped_env)

    def _container_env(
        self,
        request: EnvironmentExecRequest,
        mounts: list[tuple[str, str, str]],
    ) -> dict[str, str]:
        mapped_env: dict[str, str] = {}
        for key, value in {**self._proxy_env(), **request.env_overrides}.items():
            _validate_env_key(key)
            mapped_env[key] = _map_container_path(value, mounts)
        if self._descriptor.temp_artifact_root:
            mapped_env['OWNPI_TEMP_ARTIFACT_ROOT'] = _map_container_path(
                self._descriptor.temp_artifact_root,
                mounts,
            )
        return mapped_env


def _docker_mounts(descriptor: EnvironmentDescriptor, sandbox_policy: SandboxPolicy | None = None, *, mount_mode: str | None = None) -> list[tuple[str, str, str]]:
    workspace_root = str(Path(descriptor.workspace_root or Path.cwd()).resolve())
    read_roots = [
        str(Path(item).expanduser().resolve())
        for item in (sandbox_policy.readable_host_roots if sandbox_policy is not None else ())
        if str(item).strip()
    ]
    write_roots = [
        str(Path(item).expanduser().resolve())
        for item in (sandbox_policy.writable_host_roots if sandbox_policy is not None else ())
        if str(item).strip()
    ]
    if not read_roots and not write_roots:
        read_roots = [workspace_root]
        if mount_mode != 'ro' and (
            sandbox_policy is None or sandbox_policy.allow_workspace_write
        ):
            write_roots = [workspace_root]
    mounts: list[tuple[str, str, str]] = []
    normalized_write_roots = {
        str(Path(item).expanduser().resolve()) for item in write_roots
    }
    for raw_source in [*read_roots, *write_roots]:
        source = str(Path(raw_source).expanduser().resolve())
        mode = 'rw' if source in normalized_write_roots else 'ro'
        if any(source == existing for existing, _, _ in mounts):
            continue
        target = _mount_target(source, workspace_root=workspace_root, descriptor=descriptor, index=len(mounts))
        mounts.append((source, target, mode))
    # A host path may be reachable through several ancestor bind mounts. Apply
    # every descendant override at every alias, not only at its preferred path.
    overrides: dict[str, tuple[str, str, str]] = {
        target: (source, target, mode) for source, target, mode in mounts
    }
    for ancestor, target, _ in mounts:
        for source, _, mode in mounts:
            if source == ancestor or not Path(source).is_relative_to(ancestor):
                continue
            alias = str(Path(target) / Path(source).relative_to(ancestor))
            overrides[alias] = (source, alias, mode)
    return sorted(overrides.values(), key=lambda item: len(Path(item[1]).parts))


def _mount_target(source: str, *, workspace_root: str, descriptor: EnvironmentDescriptor, index: int) -> str:
    if source == workspace_root:
        return '/workspace'
    if descriptor.temp_artifact_root and source == str(Path(descriptor.temp_artifact_root).expanduser().resolve()):
        return '/neuro-run'
    try:
        workspace_relative = Path(source).relative_to(Path(workspace_root))
    except ValueError:
        workspace_relative = None
    if workspace_relative is not None:
        return str(Path('/workspace') / workspace_relative)
    return f'/mnt/neuro-{index}'


def _map_container_path(value: str, mounts: list[tuple[str, str, str]]) -> str:
    text = str(value or '')
    comparable = (
        str(Path(text).expanduser().resolve())
        if Path(text).is_absolute()
        else text
    )
    for source, target, _mode in sorted(mounts, key=lambda item: len(item[0]), reverse=True):
        if comparable == source:
            return target
        if Path(comparable).is_absolute() and Path(comparable).is_relative_to(source):
            return str(Path(target) / Path(comparable).relative_to(source))
    return text


def _container_command_list(request: EnvironmentExecRequest, mounts: list[tuple[str, str, str]]) -> list[str]:
    if request.shell_command is not None:
        return ['/bin/sh', '-lc', request.shell_command]
    command = [_map_container_path(part, mounts) for part in request.command]
    if command and Path(command[0]).is_absolute() and Path(command[0]).name.startswith('python'):
        command[0] = 'python'
    if command and command[0] == sys.executable:
        command[0] = 'python'
    return command


def _validate_env_key(key: str) -> None:
    normalized = str(key or '').strip()
    if not _ENV_KEY_RE.match(normalized):
        raise RuntimeError(f'invalid docker env override key: {key!r}')
    upper = normalized.upper()
    if any(part in upper for part in _SENSITIVE_ENV_KEY_PARTS):
        raise RuntimeError(f'docker_sandbox refuses to inline sensitive env override: {normalized}')
    if upper in _TRANSPORT_SENSITIVE_ENV_KEYS or upper.startswith(_TRANSPORT_SENSITIVE_ENV_PREFIXES):
        raise RuntimeError(f'docker_sandbox refuses transport-sensitive env override: {normalized}')
