from __future__ import annotations

import subprocess
from pathlib import Path

from ownpi.runtime.permissions.bash_policy import (
    BashAnalysis,
    analyze_argv_network_intent,
    analyze_bash_command,
    bash_analysis_metadata,
    extract_network_intent,
)
from ownpi.runtime.permissions.models import (
    EffectivePermissions,
    FileSystemAccessMode,
    NetworkSandboxPolicy,
)
from ownpi.runtime.permissions.policy import FileSystemPolicyMatcher
from ownpi.runtime.sandbox import SandboxCommandRequest, SandboxManager
from ownpi.runtime.sandbox.contracts import SandboxWrappedCommand
from ownpi.runtime.environment.contracts import (
    EnvironmentDescriptor,
    EnvironmentExecRequest,
    SandboxPolicy,
    build_sandbox_provider_metadata,
)
from ownpi.runtime.environment.local_env import (
    build_local_env_overrides,
    dependency_recovery_hint,
    local_env_metadata,
    rewrite_argv_for_local_env,
    rewrite_shell_command_for_local_env,
)
from ownpi.runtime.environment.process_capture import (
    normalize_python_command,
    shell_path,
)
from ownpi.runtime.environment.providers import (
    EnvironmentProvider,
    EnvironmentSessionLaunch,
    ResolvedEnvironmentSelection,
)
from ownpi.runtime.environment.subprocess_env import build_subprocess_env


_ALLOWED_PROXY_ENV_KEYS = {
    'HTTPS_PROXY',
    'https_proxy',
    'HTTP_PROXY',
    'http_proxy',
    'NO_PROXY',
    'no_proxy',
    'SSL_CERT_FILE',
    'NODE_EXTRA_CA_CERTS',
    'REQUESTS_CA_BUNDLE',
    'CURL_CA_BUNDLE',
}


class HostLocalEnvironmentProvider(EnvironmentProvider):
    kind = 'host_local'

    def __init__(
        self,
        *,
        project_root: Path,
        selection: ResolvedEnvironmentSelection,
        runtime_config: dict[str, object] | None,
    ) -> None:
        del project_root
        self._selection = selection
        self._descriptor = selection.descriptor
        self._sandbox_policy = selection.sandbox_policy
        self._runtime_config = dict(runtime_config or {})

    def prepare(self, *, descriptor: EnvironmentDescriptor, sandbox_policy: SandboxPolicy) -> None:
        if self.kind == 'host_local' and sandbox_policy.requires_isolation:
            raise RuntimeError('host_local provider refuses execution because sandbox policy requires isolation')
        self._descriptor = descriptor
        self._sandbox_policy = sandbox_policy

    def prepare_session_launch(
        self,
        *,
        request: EnvironmentExecRequest,
    ) -> EnvironmentSessionLaunch:
        self._enforce_request_policy(request)
        wrapped = self._wrapped_command(request)
        return EnvironmentSessionLaunch(
            argv=wrapped.argv,
            cwd=request.cwd or self._descriptor.workdir or str(Path.cwd()),
            env=self._build_env(request),
            tty=request.pty_mode == 'pty',
            pipe_stdin=bool(request.metadata.get('interactive_stdin', False)),
            pty_columns=request.pty_columns,
            pty_rows=request.pty_rows,
            sandbox_backend=wrapped.sandbox_backend,
        )

    def _command_list(self, request: EnvironmentExecRequest) -> list[str]:
        return list(self._wrapped_command(request).argv)

    def _wrapped_command(self, request: EnvironmentExecRequest) -> SandboxWrappedCommand:
        if request.command:
            command = rewrite_argv_for_local_env(list(request.command), self._descriptor)
        else:
            if request.shell_command is None:
                raise ValueError('environment exec request must include command or shell_command')
            shell_command = rewrite_shell_command_for_local_env(
                request.shell_command,
                self._descriptor,
            )
            command = [shell_path(), '-lc', normalize_python_command(shell_command)]
        permissions_payload = request.metadata.get('effective_permissions')
        if not isinstance(permissions_payload, dict):
            raise RuntimeError('host_local command is missing canonical effective permissions')
        effective_permissions = EffectivePermissions.from_dict(permissions_payload)
        sandbox_request = SandboxCommandRequest(
            argv=tuple(command),
            cwd=Path(request.cwd or self._descriptor.workdir or Path.cwd()).absolute(),
            env={},
            effective_permissions=effective_permissions,
            timeout_seconds=request.timeout_seconds,
            stdin_text=request.stdin_text,
        )
        return SandboxManager().wrap_command_with_provenance(sandbox_request)

    def _build_env(self, request: EnvironmentExecRequest) -> dict[str, str]:
        requested_env = {**self._proxy_env(), **request.env_overrides}
        extra_env = build_local_env_overrides(
            descriptor=self._descriptor,
            runtime_config=self._runtime_config,
            request_env=requested_env,
        )
        return build_subprocess_env(extra_env=extra_env)

    def _stdin_text(self, request: EnvironmentExecRequest) -> str | None:
        return request.stdin_text

    def _proxy_env(self) -> dict[str, str]:
        proxy_env = self._runtime_config.get('proxy_env', {})
        if not isinstance(proxy_env, dict):
            return {}
        return {
            str(key): str(value)
            for key, value in proxy_env.items()
            if str(key) in _ALLOWED_PROXY_ENV_KEYS
        }

    def _enforce_request_policy(self, request: EnvironmentExecRequest) -> None:
        reasons: list[str] = []
        shell_analysis = self._shell_analysis(request)
        effective_permissions = self._effective_permissions(request)
        matcher = FileSystemPolicyMatcher(effective_permissions.profile.file_system)
        cwd = request.cwd or self._descriptor.workdir or str(Path.cwd())
        if not matcher.decide(
            cwd,
            requested_access=FileSystemAccessMode.READ,
        ).allowed:
            reasons.append('canonical_permission_denied_cwd')
        for output_path in (request.stdout_path, request.stderr_path):
            if output_path and not matcher.decide(
                output_path,
                requested_access=FileSystemAccessMode.WRITE,
                cwd=Path(cwd),
            ).allowed:
                reasons.append('canonical_permission_denied_output_path')
        if effective_permissions.profile.network is not NetworkSandboxPolicy.ENABLED and self._request_requires_network(
            request,
            shell_analysis=shell_analysis,
        ):
            reasons.append(
                'canonical_permission_network_denied: command requires network outside the active profile'
            )
        if reasons:
            unique = tuple(dict.fromkeys(reasons))
            raise RuntimeError(f"{unique[0]}: {', '.join(unique)}")

    @staticmethod
    def _effective_permissions(request: EnvironmentExecRequest) -> EffectivePermissions:
        payload = request.metadata.get('effective_permissions')
        if not isinstance(payload, dict):
            raise RuntimeError('host_local command is missing canonical effective permissions')
        return EffectivePermissions.from_dict(payload)

    def _effective_read_roots(self) -> tuple[str, ...]:
        return self._sandbox_policy.readable_host_roots

    def _effective_write_roots(self) -> tuple[str, ...]:
        return self._sandbox_policy.writable_host_roots

    def _network_mode(self) -> str:
        return 'full' if self._sandbox_policy.allow_network else 'none'

    def _request_requires_network(self, request: EnvironmentExecRequest, *, shell_analysis: BashAnalysis | None) -> bool:
        return (
            _request_requires_network(request)
            or self._selection.backend_kind in {'browser', 'mcp', 'web'}
            or self._command_requires_network(request, shell_analysis=shell_analysis)
        )

    def _shell_analysis(self, request: EnvironmentExecRequest) -> BashAnalysis | None:
        if request.shell_command is None:
            return None
        return analyze_bash_command(request.shell_command)

    def _command_requires_network(self, request: EnvironmentExecRequest, *, shell_analysis: BashAnalysis | None) -> bool:
        if shell_analysis is not None:
            return extract_network_intent(shell_analysis).requires_network
        return analyze_argv_network_intent(list(request.command)).requires_network

    def _network_hosts(self, request: EnvironmentExecRequest, *, shell_analysis: BashAnalysis | None) -> set[str]:
        if shell_analysis is not None:
            return set(extract_network_intent(shell_analysis).hosts)
        return set(analyze_argv_network_intent(list(request.command)).hosts)

    def _path_in_roots(self, path: str, roots: tuple[str, ...]) -> bool:
        if not roots:
            return False
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            base = self._descriptor.workdir or self._descriptor.workspace_root or str(Path.cwd())
            candidate = Path(base) / candidate
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate.absolute()
        for raw_root in roots:
            root_text = str(raw_root or '').strip()
            if not root_text:
                continue
            try:
                root = Path(root_text).expanduser().resolve()
            except OSError:
                root = Path(root_text).expanduser().absolute()
            if resolved == root or root in resolved.parents:
                return True
        return False

    def execution_metadata(
        self,
        *,
        request: EnvironmentExecRequest,
        stderr_text: str = '',
    ) -> dict[str, object]:
        injected_env = build_local_env_overrides(
            descriptor=self._descriptor,
            runtime_config=self._runtime_config,
            request_env={**self._proxy_env(), **request.env_overrides},
        )
        intent = (
            extract_network_intent(analyze_bash_command(request.shell_command))
            if request.shell_command is not None
            else analyze_argv_network_intent(list(request.command))
        )
        effective_command = request.shell_command or subprocess.list2cmdline(self._command_list(request))
        resource_materialization = bool(request.metadata.get('resource_materialization'))
        redacted_display = str(request.metadata.get('command_display_override', '') or '').strip()
        if resource_materialization:
            original_command = redacted_display or f'{request.tool_name} transport <redacted>'
            effective_command = original_command
            network_intent = {
                'requires_network': bool(getattr(intent, 'requires_network', False)),
                'hosts': [],
                'command_names': [request.tool_name],
                'redacted': True,
            }
        else:
            original_command = request.shell_command or subprocess.list2cmdline(list(request.command))
            network_intent = _network_intent_metadata(intent)
        return {
            'stdout_path': request.stdout_path,
            'stderr_path': request.stderr_path,
            'original_command': original_command,
            'effective_command': effective_command,
            'network_intent': network_intent,
            **local_env_metadata(
                descriptor=self._descriptor,
                runtime_config=self._runtime_config,
                injected_env=injected_env,
            ),
            **_execution_contract_metadata(request),
            **bash_analysis_metadata(request.shell_command),
            **build_sandbox_provider_metadata(
                descriptor=self._descriptor,
                sandbox_policy=self._sandbox_policy,
                provider_kind=self.kind,
                enforcement_status='not_applicable' if self._descriptor.host_managed else None,
            ),
            **_dependency_recovery_metadata(stderr_text, descriptor=self._descriptor),
        }


def _request_requires_network(request: EnvironmentExecRequest) -> bool:
    if request.execution_class in {'web', 'browser', 'mcp'}:
        return True
    return str(request.env_overrides.get('OWNPI_REQUIRES_NETWORK', '')).strip().lower() in {
        '1',
        'true',
        'yes',
        'on',
    }


def _network_intent_metadata(intent: object) -> dict[str, object]:
    return {
        'requires_network': bool(getattr(intent, 'requires_network', False)),
        'hosts': list(getattr(intent, 'hosts', ()) or ()),
        'command_names': list(getattr(intent, 'command_names', ()) or ()),
    }


def _dependency_recovery_metadata(stderr: str, *, descriptor: EnvironmentDescriptor) -> dict[str, object]:
    hint = dependency_recovery_hint(stderr=stderr, descriptor=descriptor)
    if not hint:
        return {}
    return {
        'recoverable_execution_error': True,
        'dependency_recovery_hint': hint,
    }


def _execution_contract_metadata(request: EnvironmentExecRequest) -> dict[str, object]:
    metadata = request.metadata if isinstance(request.metadata, dict) else {}
    payload: dict[str, object] = {}
    for key in ('execution_contract_id', 'execution_contract_hash', 'exec_policy_decision_id', 'exec_policy_hash'):
        value = str(metadata.get(key, '') or '').strip()
        if value:
            payload[key] = value
    return payload
