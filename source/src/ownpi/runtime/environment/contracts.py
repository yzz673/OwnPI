from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
import json
from typing import Any

from ownpi.runtime.permissions.models import (
    EffectivePermissions,
    FileSystemAccessMode,
    FileSystemPathKind,
    FileSystemSandboxKind,
    NetworkSandboxPolicy,
)


EMPTY_TUPLE: tuple[str, ...] = ()


class EnvironmentProviderKind(str, Enum):
    HOST_LOCAL = 'host_local'
    DOCKER_SANDBOX = 'docker_sandbox'
    SSH_REMOTE = 'ssh_remote'


def normalize_environment_provider_kind(value: Any) -> str:
    try:
        return EnvironmentProviderKind(str(value or '').strip()).value
    except ValueError as exc:
        raise ValueError(f'unknown environment provider: {value!r}') from exc


def _coerce_string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return EMPTY_TUPLE
    if isinstance(value, str):
        stripped = value.strip()
        return (stripped,) if stripped else EMPTY_TUPLE
    return tuple(str(item).strip() for item in value if str(item).strip())


def _coerce_string_mapping(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items() if str(key).strip()}


@dataclass(frozen=True)
class EnvironmentDescriptor:
    provider_kind: str
    workspace_root: str = ''
    workdir: str = ''
    env_policy: str = 'scrubbed_inherit'
    env_name: str = ''
    python_executable: str = ''
    activation_kind: str = ''
    image_ref: str | None = None
    remote_target: str | None = None
    remote_workspace_root: str | None = None
    ephemeral: bool = False
    sandboxed: bool = False
    host_managed: bool = False
    temp_artifact_root: str | None = None
    mounts: tuple[str, ...] = EMPTY_TUPLE

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> 'EnvironmentDescriptor':
        payload = data or {}
        raw_env_policy = payload.get('env_policy')
        env_policy = (
            str(raw_env_policy).strip()
            if raw_env_policy is not None and str(raw_env_policy).strip()
            else 'scrubbed_inherit'
        )
        return cls(
            provider_kind=normalize_environment_provider_kind(
                payload.get('provider_kind', 'host_local') or 'host_local'
            ),
            workspace_root=str(payload.get('workspace_root', '') or ''),
            workdir=str(payload.get('workdir', '') or ''),
            env_policy=env_policy,
            env_name=str(payload.get('env_name', '') or ''),
            python_executable=str(payload.get('python_executable', '') or ''),
            activation_kind=str(payload.get('activation_kind', '') or ''),
            image_ref=(str(payload['image_ref']) if payload.get('image_ref') is not None else None),
            remote_target=(str(payload['remote_target']) if payload.get('remote_target') is not None else None),
            remote_workspace_root=(
                str(payload['remote_workspace_root'])
                if payload.get('remote_workspace_root') is not None
                else None
            ),
            ephemeral=bool(payload.get('ephemeral', False)),
            sandboxed=bool(payload.get('sandboxed', False)),
            host_managed=bool(payload.get('host_managed', False)),
            temp_artifact_root=(
                str(payload['temp_artifact_root']) if payload.get('temp_artifact_root') is not None else None
            ),
            mounts=_coerce_string_tuple(payload.get('mounts')),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SandboxPolicy:
    allow_host: bool = True
    requires_isolation: bool = False
    allow_network: bool = True
    allow_workspace_write: bool = True
    readable_host_roots: tuple[str, ...] = EMPTY_TUPLE
    writable_host_roots: tuple[str, ...] = EMPTY_TUPLE
    blocked_host_roots: tuple[str, ...] = EMPTY_TUPLE
    enforcement_mode: str = 'preflight'
    remote_required: bool = False
    reason: str = ''

    @classmethod
    def from_receipt_dict(cls, data: dict[str, Any] | None) -> 'SandboxPolicy':
        """Deserialize observation-only enforcement evidence from a receipt."""
        payload = data or {}
        return cls(
            allow_host=bool(payload.get('allow_host', True)),
            requires_isolation=bool(payload.get('requires_isolation', False)),
            allow_network=bool(payload.get('allow_network', True)),
            allow_workspace_write=bool(payload.get('allow_workspace_write', True)),
            readable_host_roots=_coerce_string_tuple(payload.get('readable_host_roots')),
            writable_host_roots=_coerce_string_tuple(payload.get('writable_host_roots')),
            blocked_host_roots=_coerce_string_tuple(payload.get('blocked_host_roots')),
            enforcement_mode=str(payload.get('enforcement_mode', 'preflight') or 'preflight'),
            remote_required=bool(payload.get('remote_required', False)),
            reason=str(payload.get('reason', '') or ''),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload['readable_host_roots'] = list(self.readable_host_roots)
        payload['writable_host_roots'] = list(self.writable_host_roots)
        payload['blocked_host_roots'] = list(self.blocked_host_roots)
        return payload


def build_permission_enforcement_projection(
    *,
    effective_permissions: EffectivePermissions,
    provider_kind: str,
    requires_isolation: bool = False,
    remote_required: bool = False,
    host_managed: bool = False,
    reason: str = '',
) -> SandboxPolicy:
    """Project the active canonical profile into backend enforcement inputs."""
    entries = tuple(
        entry
        for entry in effective_permissions.profile.file_system.entries
        if entry.path.kind is FileSystemPathKind.PATH
    )
    readable_roots = tuple(
        dict.fromkeys(entry.path.value for entry in entries if entry.access.can_read)
    )
    writable_roots = tuple(
        dict.fromkeys(
            entry.path.value
            for entry in entries
            if entry.access is FileSystemAccessMode.WRITE
        )
    )
    blocked_roots = tuple(
        dict.fromkeys(
            entry.path.value
            for entry in entries
            if entry.access is FileSystemAccessMode.DENY
        )
    )
    unrestricted = (
        effective_permissions.profile.file_system.kind
        is FileSystemSandboxKind.UNRESTRICTED
    )
    return SandboxPolicy(
        allow_host=(provider_kind == 'host_local' and not host_managed),
        requires_isolation=requires_isolation,
        allow_network=(
            effective_permissions.profile.network is NetworkSandboxPolicy.ENABLED
        ),
        allow_workspace_write=bool(unrestricted or writable_roots),
        readable_host_roots=readable_roots,
        writable_host_roots=writable_roots,
        blocked_host_roots=blocked_roots,
        enforcement_mode='canonical_profile',
        remote_required=remote_required,
        reason=reason,
    )


@dataclass(frozen=True)
class EnvironmentExecRequest:
    request_id: str
    tool_name: str
    command: tuple[str, ...] = EMPTY_TUPLE
    shell_command: str | None = None
    cwd: str = ''
    env_overrides: dict[str, str] = field(default_factory=dict)
    timeout_seconds: int = 120
    execution_class: str = 'command_exec'
    pty_mode: str = 'pipe'
    pty_columns: int | None = None
    pty_rows: int | None = None
    stdout_path: str | None = None
    stderr_path: str | None = None
    artifact_output_policy: str = 'persist_streams'
    stdin_text: str | None = None
    run_id: str = ''
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'EnvironmentExecRequest':
        return cls(
            request_id=str(data.get('request_id', '') or ''),
            tool_name=str(data.get('tool_name', '') or ''),
            command=_coerce_string_tuple(data.get('command')),
            shell_command=(str(data['shell_command']) if data.get('shell_command') is not None else None),
            cwd=str(data.get('cwd', '') or ''),
            env_overrides=_coerce_string_mapping(data.get('env_overrides')),
            timeout_seconds=max(1, int(data.get('timeout_seconds', 120) or 120)),
            execution_class=str(data.get('execution_class', 'command_exec') or 'command_exec'),
            pty_mode=str(data.get('pty_mode', 'pipe') or 'pipe'),
            pty_columns=(int(data['pty_columns']) if data.get('pty_columns') is not None else None),
            pty_rows=(int(data['pty_rows']) if data.get('pty_rows') is not None else None),
            stdout_path=(str(data['stdout_path']) if data.get('stdout_path') is not None else None),
            stderr_path=(str(data['stderr_path']) if data.get('stderr_path') is not None else None),
            artifact_output_policy=str(data.get('artifact_output_policy', 'persist_streams') or 'persist_streams'),
            stdin_text=(str(data['stdin_text']) if data.get('stdin_text') is not None else None),
            run_id=str(data.get('run_id', '') or ''),
            metadata=dict(data.get('metadata', {})) if isinstance(data.get('metadata'), dict) else {},
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EnvironmentExecutionReceipt:
    receipt_id: str
    provider_kind: str
    request_kind: str
    tool_name: str
    status: str
    descriptor: EnvironmentDescriptor
    sandbox_policy: SandboxPolicy
    selection_reason: str = ''
    environment_affinity: str = 'host_preferred'
    execution_class: str = 'command_exec'
    session_id: str | None = None
    command_display: str = ''
    started_at: str | None = None
    heartbeat_at: str | None = None
    completed_at: str | None = None
    elapsed_ms: int = 0
    exit_code: int | None = None
    heartbeat_count: int = 0
    stdout_preview: str = ''
    stderr_preview: str = ''
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    timeout_class: str | None = None
    pty_mode: str = 'pipe'
    pty_columns: int | None = None
    pty_rows: int | None = None
    network_mode: str = 'full'
    writable_roots: tuple[str, ...] = EMPTY_TUPLE
    artifact_refs: tuple[str, ...] = EMPTY_TUPLE
    provider_metadata: dict[str, Any] = field(default_factory=dict)
    environment_policy_hash: str = ''
    authorization_evidence_only: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'EnvironmentExecutionReceipt':
        payload = data or {}
        return cls(
            receipt_id=str(payload.get('receipt_id', '') or ''),
            provider_kind=str(payload.get('provider_kind', 'host_local') or 'host_local'),
            request_kind=str(payload.get('request_kind', 'exec') or 'exec'),
            tool_name=str(payload.get('tool_name', '') or ''),
            status=str(payload.get('status', 'unknown') or 'unknown'),
            descriptor=EnvironmentDescriptor.from_dict(payload.get('descriptor') if isinstance(payload.get('descriptor'), dict) else {}),
            sandbox_policy=SandboxPolicy.from_receipt_dict(
                payload.get('sandbox_policy')
                if isinstance(payload.get('sandbox_policy'), dict)
                else {}
            ),
            selection_reason=str(payload.get('selection_reason', '') or ''),
            environment_affinity=str(payload.get('environment_affinity', 'host_preferred') or 'host_preferred'),
            execution_class=str(payload.get('execution_class', 'command_exec') or 'command_exec'),
            session_id=(str(payload['session_id']) if payload.get('session_id') is not None else None),
            command_display=str(payload.get('command_display', '') or ''),
            started_at=(str(payload['started_at']) if payload.get('started_at') is not None else None),
            heartbeat_at=(str(payload['heartbeat_at']) if payload.get('heartbeat_at') is not None else None),
            completed_at=(str(payload['completed_at']) if payload.get('completed_at') is not None else None),
            elapsed_ms=max(0, int(payload.get('elapsed_ms', 0) or 0)),
            exit_code=(int(payload['exit_code']) if payload.get('exit_code') is not None else None),
            heartbeat_count=max(0, int(payload.get('heartbeat_count', 0) or 0)),
            stdout_preview=str(payload.get('stdout_preview', '') or ''),
            stderr_preview=str(payload.get('stderr_preview', '') or ''),
            stdout_truncated=bool(payload.get('stdout_truncated', False)),
            stderr_truncated=bool(payload.get('stderr_truncated', False)),
            timeout_class=(str(payload['timeout_class']) if payload.get('timeout_class') is not None else None),
            pty_mode=str(payload.get('pty_mode', 'pipe') or 'pipe'),
            pty_columns=(int(payload['pty_columns']) if payload.get('pty_columns') is not None else None),
            pty_rows=(int(payload['pty_rows']) if payload.get('pty_rows') is not None else None),
            network_mode=str(payload.get('network_mode', 'full') or 'full'),
            writable_roots=_coerce_string_tuple(payload.get('writable_roots')),
            artifact_refs=_coerce_string_tuple(payload.get('artifact_refs')),
            provider_metadata=(dict(payload.get('provider_metadata', {})) if isinstance(payload.get('provider_metadata'), dict) else {}),
            environment_policy_hash=str(payload.get('environment_policy_hash', '') or ''),
            authorization_evidence_only=bool(
                payload.get('authorization_evidence_only', True)
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        policy_hash = self.environment_policy_hash or build_environment_policy_hash(
            descriptor=self.descriptor,
            sandbox_policy=self.sandbox_policy,
        )
        return {
            'receipt_id': self.receipt_id,
            'provider_kind': self.provider_kind,
            'request_kind': self.request_kind,
            'tool_name': self.tool_name,
            'status': self.status,
            'descriptor': self.descriptor.to_dict(),
            'sandbox_policy': self.sandbox_policy.to_dict(),
            'selection_reason': self.selection_reason,
            'environment_affinity': self.environment_affinity,
            'execution_class': self.execution_class,
            'session_id': self.session_id,
            'command_display': self.command_display,
            'started_at': self.started_at,
            'heartbeat_at': self.heartbeat_at,
            'completed_at': self.completed_at,
            'elapsed_ms': self.elapsed_ms,
            'exit_code': self.exit_code,
            'heartbeat_count': self.heartbeat_count,
            'stdout_preview': self.stdout_preview,
            'stderr_preview': self.stderr_preview,
            'stdout_truncated': self.stdout_truncated,
            'stderr_truncated': self.stderr_truncated,
            'timeout_class': self.timeout_class,
            'pty_mode': self.pty_mode,
            'pty_columns': self.pty_columns,
            'pty_rows': self.pty_rows,
            'network_mode': self.network_mode,
            'writable_roots': list(self.writable_roots),
            'artifact_refs': list(self.artifact_refs),
            'provider_metadata': dict(self.provider_metadata),
            'environment_policy_hash': policy_hash,
        }


@dataclass(frozen=True)
class EnvironmentExecOutcome:
    receipt: EnvironmentExecutionReceipt
    stdout_text: str = ''
    stderr_text: str = ''
    combined_output: str = ''

    def to_dict(self) -> dict[str, Any]:
        return {
            'receipt': self.receipt.to_dict(),
            'stdout_text': self.stdout_text,
            'stderr_text': self.stderr_text,
            'combined_output': self.combined_output,
        }


def build_environment_policy_hash(
    *,
    descriptor: EnvironmentDescriptor,
    sandbox_policy: SandboxPolicy,
) -> str:
    payload = {
        'descriptor': descriptor.to_dict(),
        'sandbox_policy': sandbox_policy.to_dict(),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()[:16]


def build_sandbox_provider_metadata(
    *,
    descriptor: EnvironmentDescriptor,
    sandbox_policy: SandboxPolicy,
    provider_kind: str,
    blocked_policy_reasons: tuple[str, ...] = EMPTY_TUPLE,
    enforcement_status: str | None = None,
) -> dict[str, Any]:
    from ownpi.runtime.environment.enforcement import (
        classify_enforcement_status,
        compile_enforcement_capabilities,
    )

    readable_roots = sandbox_policy.readable_host_roots
    writable_roots = sandbox_policy.writable_host_roots
    capabilities = compile_enforcement_capabilities(
        descriptor=descriptor,
        sandbox_policy=sandbox_policy,
        provider_kind=provider_kind,
    )
    status = str(enforcement_status or '').strip() or (
        'blocked' if blocked_policy_reasons else classify_enforcement_status(capabilities)
    )
    unsupported_reasons = [
        item.reason
        for item in capabilities
        if item.requested and not item.enforced and item.enforcement_layer == 'unsupported'
    ]
    preflight_only_reasons = [
        item.reason
        for item in capabilities
        if item.requested and not item.enforced and item.enforcement_layer == 'preflight'
    ]
    return {
        'sandbox_enforcement_status': status,
        'blocked_policy_reasons': list(blocked_policy_reasons),
        'effective_read_roots': list(readable_roots),
        'effective_write_roots': list(writable_roots),
        'enforcement_mode': sandbox_policy.enforcement_mode,
        'enforcement_capabilities': [item.to_dict() for item in capabilities],
        'unsupported_policy_reasons': unsupported_reasons,
        'preflight_only_policy_reasons': preflight_only_reasons,
    }
