from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import shlex
from typing import TYPE_CHECKING, Any

from ownpi.runtime.environment.command_backend import run_local_command
from ownpi.runtime.limits.recovery import DEFAULT_REMOTE_SHELL_TIMEOUT_SECONDS
from ownpi.runtime.permissions.models import (
    EffectivePermissions,
    FileSystemSandboxKind,
    NetworkSandboxPolicy,
    SandboxEnforcement,
)
from ownpi.tools.backends.remote_workflow.profiles import RemoteProfile
from ownpi.tools.backends.remote_workflow.results import WorkflowResult

if TYPE_CHECKING:
    from ownpi.runtime.tools.definitions import ToolExecutionContext


class RemoteSandboxProtocolUnavailableError(RuntimeError):
    """Raised when remote execution cannot enforce the active restricted profile."""


class RemoteWorkflowGateway:
    """Route every remote workflow process through the canonical environment gateway."""

    def __init__(self, *, context: ToolExecutionContext, profile: RemoteProfile) -> None:
        effective_permissions = getattr(context, 'effective_permissions', None)
        if not isinstance(effective_permissions, EffectivePermissions):
            raise RemoteSandboxProtocolUnavailableError(
                'remote workflow requires canonical effective permissions'
            )
        if not _allows_advisory_remote_transport(effective_permissions):
            raise RemoteSandboxProtocolUnavailableError(
                'restricted remote workflow is blocked because the configured SSH provider '
                'does not expose a compatible remote sandbox enforcement protocol'
            )
        self._effective_permissions = effective_permissions
        self._profile = profile
        self._remote_context = _remote_gateway_context(context, profile)
        self._transfer_context = _transfer_gateway_context(context, profile)

    @property
    def effective_permissions(self) -> EffectivePermissions:
        return self._effective_permissions

    async def run_remote_bash(
        self,
        script: str,
        *,
        name: str,
        timeout_seconds: int | None = None,
    ) -> WorkflowResult:
        result = await run_local_command(
            context=self._remote_context,
            tool_name=name,
            shell_command=script,
            cwd=Path(getattr(self._remote_context, 'project_root', self._profile.local_root)),
            timeout_seconds=_bounded_timeout(timeout_seconds),
            metadata={
                'remote_workflow_gateway': True,
                'remote_profile': self._profile.name,
                'transport_kind': 'ssh_remote',
            },
            command_display_override=f'remote:{self._profile.name} {name}',
        )
        return _workflow_result(
            result,
            command_name=name,
            profile=self._profile,
            effective_permissions=self._effective_permissions,
        )

    async def run_transfer(
        self,
        command: list[str],
        *,
        name: str,
        timeout_seconds: int | None = None,
    ) -> WorkflowResult:
        result = await run_local_command(
            context=self._transfer_context,
            tool_name=name,
            command=command,
            cwd=self._profile.local_root,
            timeout_seconds=_bounded_timeout(timeout_seconds),
            metadata={
                'remote_workflow_gateway': True,
                'remote_profile': self._profile.name,
                'transport_kind': 'rsync_over_ssh',
            },
            command_display_override=f'remote:{self._profile.name} {name}',
        )
        return _workflow_result(
            result,
            command_name=name,
            profile=self._profile,
            effective_permissions=self._effective_permissions,
        )


def rsync_rsh(profile: RemoteProfile) -> str:
    parts = [
        'ssh',
        '-p',
        str(profile.port),
        '-o',
        'ServerAliveInterval=60',
        '-o',
        f'ConnectTimeout={profile.connect_timeout_seconds}',
        '-o',
        f'StrictHostKeyChecking={profile.strict_host_key_checking}',
    ]
    return shlex.join(parts)


def remote_arg(profile: RemoteProfile, remote_path: str) -> str:
    return f'{profile.target}:{remote_path}'


def ensure_local_root(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _allows_advisory_remote_transport(effective_permissions: EffectivePermissions) -> bool:
    profile = effective_permissions.profile
    return (
        profile.enforcement is SandboxEnforcement.DISABLED
        and profile.file_system.kind is FileSystemSandboxKind.UNRESTRICTED
        and profile.network is NetworkSandboxPolicy.ENABLED
    )


def _remote_gateway_context(context: ToolExecutionContext, profile: RemoteProfile) -> ToolExecutionContext:
    config_snapshot = _environment_config_with_profile(context, profile)
    project_root = Path(getattr(context, 'project_root', profile.local_root)).expanduser().resolve()
    return _replace_context(
        context,
        environment_config_snapshot=config_snapshot,
        environment_descriptor={
            'provider_kind': 'ssh_remote',
            'workspace_root': str(project_root),
            'workdir': str(project_root),
            'readable_roots': [],
            'writable_roots': [],
            'network_mode': 'full',
            'env_policy': 'scrubbed_inherit',
            'remote_target': profile.name,
            'remote_workspace_root': profile.remote_root,
            'sandboxed': False,
            'host_managed': False,
        },
        sandbox_policy={
            'allow_host': False,
            'requires_isolation': False,
            'allow_network': True,
            'allow_workspace_write': True,
            'enforcement_mode': 'canonical_profile',
            'remote_required': True,
            'reason': 'canonical_remote_workflow_gateway',
        },
        environment_provider_kind='ssh_remote',
        environment_selection_reason='canonical_remote_workflow_gateway',
        environment_affinity='remote_required',
        execution_class='remote_command',
    )


def _transfer_gateway_context(context: ToolExecutionContext, profile: RemoteProfile) -> ToolExecutionContext:
    return _replace_context(
        context,
        environment_descriptor={
            'provider_kind': 'host_local',
            'workspace_root': str(profile.local_root),
            'workdir': str(profile.local_root),
            'readable_roots': [],
            'writable_roots': [],
            'network_mode': 'full',
            'env_policy': 'scrubbed_inherit',
            'sandboxed': False,
            'host_managed': False,
        },
        sandbox_policy={
            'allow_host': True,
            'requires_isolation': False,
            'allow_network': True,
            'allow_workspace_write': True,
            'enforcement_mode': 'canonical_profile',
            'remote_required': False,
            'reason': 'canonical_remote_transfer_gateway',
        },
        environment_provider_kind='host_local',
        environment_selection_reason='canonical_remote_transfer_gateway',
        environment_affinity='host_required',
        execution_class='remote_command',
    )


def _environment_config_with_profile(
    context: ToolExecutionContext,
    profile: RemoteProfile,
) -> dict[str, Any]:
    snapshot = getattr(context, 'environment_config_snapshot', None)
    payload = dict(snapshot) if isinstance(snapshot, dict) else {}
    payload['ssh'] = {
        'targets': [
            {
                'name': profile.name,
                'host': profile.host,
                'user': profile.user,
                'port': profile.port,
                'remote_workspace_root': profile.remote_root,
            }
        ]
    }
    return payload


def _workflow_result(
    result: Any,
    *,
    command_name: str,
    profile: RemoteProfile,
    effective_permissions: EffectivePermissions,
) -> WorkflowResult:
    metadata = dict(result.metadata) if isinstance(result.metadata, dict) else {}
    receipt_id = str(metadata.get('environment_receipt_id', '') or '').strip()
    data: dict[str, Any] = {
        'profile': profile.name,
        'target': profile.target,
        'remote_root': profile.remote_root,
        'canonical_gateway': True,
        'permission_profile_id': effective_permissions.active_profile_id,
        'permission_policy_hash': effective_permissions.policy_hash,
    }
    if receipt_id:
        data['environment_receipt_id'] = receipt_id
    return WorkflowResult(
        ok=bool(result.ok),
        command=command_name,
        summary=(
            f'{command_name} completed through canonical environment gateway'
            if result.ok
            else str(result.error or f'{command_name} failed through canonical environment gateway')
        ),
        stdout=str(result.stdout or ''),
        stderr=str(result.stderr or ''),
        returncode=int(result.exit_code if result.exit_code is not None else (0 if result.ok else 1)),
        data=data,
        warnings=tuple(str(item) for item in result.warnings),
    )


def _bounded_timeout(timeout_seconds: int | None) -> int:
    return (
        max(1, int(timeout_seconds))
        if timeout_seconds is not None
        else DEFAULT_REMOTE_SHELL_TIMEOUT_SECONDS
    )


def _replace_context(context: ToolExecutionContext, **changes: Any) -> ToolExecutionContext:
    return replace(context, **changes)
