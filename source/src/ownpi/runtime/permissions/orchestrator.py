from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from ownpi.runtime.permissions.command_session import build_exec_command_match
from ownpi.runtime.permissions.input_identity import permission_input_hash
from ownpi.runtime.permissions.patch_session import build_patch_match
from ownpi.runtime.permissions.mcp_session import build_mcp_match
from ownpi.runtime.permissions.models import (
    AdditionalPermissionProfile,
    ApprovalReceipt,
    AskForApproval,
    ApprovalsReviewer,
    EffectivePermissions,
    FileSystemAccessMode,
    FileSystemPath,
    FileSystemSandboxEntry,
    FileSystemSandboxKind,
    GranularApprovalConfig,
    PermissionRequest,
    PermissionPreset,
)
from ownpi.runtime.permissions.policy import (
    FileSystemPolicyMatcher,
    merge_additional_permissions,
    normalized_and_canonical_candidates,
    permission_policy_hash,
)
from ownpi.runtime.permissions.presets import permission_preset_selection
from ownpi.runtime.permissions.profiles import materialize_profile, resolve_profile


@dataclass(frozen=True)
class FileSystemAccessRequest:
    path: Path
    access: FileSystemAccessMode


@dataclass(frozen=True)
class PermissionOperation:
    request_id: str
    tool_name: str
    action: str
    input_payload: Mapping[str, Any]
    environment: Mapping[str, Any]
    file_system_accesses: tuple[FileSystemAccessRequest, ...] = ()
    network_required: bool = False
    process_required: bool = False
    process_argv: tuple[str, ...] = ()
    remote_required: bool = False
    network_endpoints: tuple[str, ...] = ()
    remote_target: str = ''
    remote_workdir: str = ''
    effect_hash: str = ''
    session_identity: str = ''
    approval_required_reason: str = ''


@dataclass(frozen=True)
class PermissionEvaluation:
    allowed: bool
    effective_permissions: EffectivePermissions
    operation: PermissionOperation
    approval_request: PermissionRequest | None = None
    denial_reason: str = ''


def _escalated_filesystem_entry() -> FileSystemSandboxEntry:
    """Represent an explicit Codex-style unsandboxed retry request."""
    return FileSystemSandboxEntry(
        path=FileSystemPath.path(Path('/')),
        access=FileSystemAccessMode.WRITE,
    )


def allows_sandbox_approval_requests(permissions: EffectivePermissions) -> bool:
    return permissions.approval_policy in {
        AskForApproval.UNLESS_TRUSTED,
        AskForApproval.ON_REQUEST,
    } or (
        permissions.approval_policy is AskForApproval.GRANULAR
        and permissions.granular_approval.sandbox_approval
    )


def sandbox_denial_next_action(permissions: EffectivePermissions | None) -> str:
    guidance = (
        'Adjust the command or target to fit the active sandbox. '
        'Inspect any partial effects before trying again.'
    )
    # Shell output cannot prove which path was denied. Do not suggest bypassing
    # the only enforcement of an explicit denied-read restriction.
    if permissions is not None and allows_sandbox_approval_requests(permissions):
        if not any(
            entry.access is FileSystemAccessMode.DENY
            for entry in permissions.profile.file_system.entries
        ):
            guidance += (
                ' If additional access is necessary, submit a new request with '
                'sandbox_permissions=require_escalated and a justification; '
                'approval is required before execution.'
            )
    return guidance


class PermissionOrchestrator:
    def compile_effective_permissions(
        self,
        *,
        preset: PermissionPreset | str,
        workspace_roots: tuple[Path, ...],
        granular_approval: GranularApprovalConfig | None = None,
    ) -> EffectivePermissions:
        selection = permission_preset_selection(preset)
        granular = granular_approval or GranularApprovalConfig()
        profile = materialize_profile(
            resolve_profile(selection.profile_id),
            workspace_roots=workspace_roots,
        )
        return EffectivePermissions(
            active_profile_id=selection.profile_id,
            profile=profile,
            approval_policy=selection.approval_policy,
            approvals_reviewer=selection.approvals_reviewer,
            workspace_roots=workspace_roots,
            policy_hash=permission_policy_hash(
                profile,
                approval_policy=selection.approval_policy,
                approvals_reviewer=selection.approvals_reviewer,
                granular_approval=granular,
            ),
            granular_approval=granular,
        )

    def evaluate(
        self,
        operation: PermissionOperation,
        *,
        effective_permissions: EffectivePermissions,
    ) -> PermissionEvaluation:
        escalation_requested = (
            str(
                operation.input_payload.get(
                    'sandbox_permissions', 'use_default'
                )
                or 'use_default'
            )
            == 'require_escalated'
        )
        if (
            escalation_requested
            and effective_permissions.approval_policy is not AskForApproval.GRANULAR
            and not allows_sandbox_approval_requests(effective_permissions)
        ):
            return PermissionEvaluation(
                allowed=False,
                effective_permissions=effective_permissions,
                operation=operation,
                denial_reason=(
                    'sandbox_permissions=require_escalated is unavailable when the '
                    'active approval policy does not allow approval requests; use '
                    'sandbox_permissions=use_default'
                ),
            )
        if (
            effective_permissions.approval_policy is AskForApproval.GRANULAR
            and (
                operation.network_required
                or operation.remote_required
                or operation.input_payload.get('sandbox_permissions') == 'require_escalated'
            )
            and not effective_permissions.granular_approval.sandbox_approval
        ):
            return PermissionEvaluation(
                allowed=False,
                effective_permissions=effective_permissions,
                operation=operation,
                denial_reason='granular approval policy denies sandbox approval requests',
            )
        profile = effective_permissions.profile
        matcher = FileSystemPolicyMatcher(profile.file_system)
        requested_entries: list[FileSystemSandboxEntry] = []
        hard_denials: list[str] = []
        for access_request in operation.file_system_accesses:
            decision = matcher.decide(
                access_request.path,
                requested_access=access_request.access,
            )
            if decision.allowed:
                continue
            if decision.effective_access is FileSystemAccessMode.DENY:
                hard_denials.append(str(decision.normalized_path))
                continue
            requested_entries.append(
                FileSystemSandboxEntry(
                    path=FileSystemPath.path(
                        normalized_and_canonical_candidates(decision.normalized_path)[-1]
                    ),
                    access=access_request.access,
                )
            )
        if hard_denials:
            return PermissionEvaluation(
                allowed=False,
                effective_permissions=effective_permissions,
                operation=operation,
                denial_reason='explicit filesystem deny: ' + ', '.join(hard_denials),
            )
        escalation_needed = escalation_requested and not (
            profile.file_system.kind is FileSystemSandboxKind.UNRESTRICTED
            or FileSystemPolicyMatcher(profile.file_system).can_write(Path('/'))
        )
        network_overlay = operation.network_required and profile.network.value != 'enabled'
        overlay = AdditionalPermissionProfile(
            file_system_entries=tuple(requested_entries)
            + ((_escalated_filesystem_entry(),) if escalation_needed else ()),
            network_enabled=True if (network_overlay or escalation_needed) else None,
        )
        if overlay.is_empty and not operation.approval_required_reason:
            return PermissionEvaluation(True, effective_permissions, operation)
        if effective_permissions.approval_policy is AskForApproval.NEVER:
            if overlay.is_empty:
                return PermissionEvaluation(True, effective_permissions, operation)
            return PermissionEvaluation(
                allowed=False,
                effective_permissions=effective_permissions,
                operation=operation,
                denial_reason='approval policy is never; additional permissions were not granted',
            )
        effect_identity = {
            'session_identity': operation.session_identity,
            'network_required': operation.network_required,
            'network_endpoints': operation.network_endpoints,
            'process_required': operation.process_required,
            'remote_required': operation.remote_required,
            'remote_target': operation.remote_target,
        }
        session_match_components: tuple[str, ...] = ()
        if operation.tool_name == 'exec_command':
            session_match = build_exec_command_match(
                input_payload=operation.input_payload,
                environment={
                    'project_root': str(effective_permissions.workspace_roots[0])
                    if effective_permissions.workspace_roots else '',
                    **operation.environment,
                },
                effective_permissions={'workspace_roots': [str(path) for path in effective_permissions.workspace_roots]},
                effects={
                    'filesystem': [(str(item.path), item.access.value) for item in operation.file_system_accesses],
                    **effect_identity,
                    # The command itself is already bound with conservative canonicalization.
                    'process_argv': (
                        () if operation.process_argv == (str(operation.input_payload.get('cmd', '') or '').strip(),)
                        else operation.process_argv
                    ),
                    'remote_required': operation.remote_required,
                    'remote_target': operation.remote_target,
                    'remote_workdir': operation.remote_workdir,
                },
            )
        elif operation.tool_name == 'patch_apply' and effective_permissions.workspace_roots:
            patch_match = build_patch_match(
                    input_payload=operation.input_payload,
                    project_root=effective_permissions.workspace_roots[0],
                )
            session_match = patch_match[:3] if patch_match is not None else None
            session_match_components = patch_match[3] if patch_match is not None else ()
        else:
            session_match = None
        if session_match is None:
            session_match = build_mcp_match(
                tool_name=operation.tool_name,
                input_payload=operation.input_payload,
                effects={
                    **effect_identity,
                    'effect_hash': operation.effect_hash,
                    'filesystem': [(str(item.path), item.access.value) for item in operation.file_system_accesses],
                    'process_argv': operation.process_argv,
                    'remote_workdir': operation.remote_workdir,
                },
            )
        if operation.tool_name == 'patch_apply' and (
            operation.approval_required_reason or operation.network_required
            or operation.process_required or operation.remote_required
        ):
            session_match = None
            session_match_components = ()
        if session_match is not None and session_match[0] == 'mcp_remote_tool' and (
            effective_permissions.approvals_reviewer is not ApprovalsReviewer.USER
            or not allows_sandbox_approval_requests(effective_permissions)
            or operation.approval_required_reason
        ):
            session_match = None
        request = PermissionRequest(
            request_id=operation.request_id,
            tool_name=operation.tool_name,
            action=operation.action,
            normalized_input_hash=permission_input_hash(operation.input_payload),
            environment_hash=stable_payload_hash(operation.environment),
            base_policy_hash=effective_permissions.policy_hash,
            effect_hash=operation.effect_hash,
            requested_overlay=overlay,
            reason=(
                operation.approval_required_reason
                or 'operation requires permissions outside the active profile'
            ),
            command_prefix=tuple(
                str(item)
                for item in operation.input_payload.get('prefix_rule', [])
                if str(item).strip()
            )
            if isinstance(operation.input_payload.get('prefix_rule'), list)
            else (),
            session_match_kind=session_match[0] if session_match else '',
            session_match_version=session_match[1] if session_match else None,
            session_match_digest=session_match[2] if session_match else '',
            session_match_components=session_match_components,
        )
        if request.session_match_kind in {
            'exec_command_exact', 'exec_command_prefix', 'patch_path_exact', 'mcp_remote_tool'
        }:
            request = replace(request, session_match_digest=stable_payload_hash({
                'operation': request.session_match_digest,
                'overlay': request.to_dict()['requested_overlay'],
            }))
        return PermissionEvaluation(
            allowed=False,
            effective_permissions=effective_permissions,
            operation=operation,
            approval_request=request,
        )

    def apply_approval(
        self,
        evaluation: PermissionEvaluation,
        receipt: ApprovalReceipt,
    ) -> PermissionEvaluation:
        request = evaluation.approval_request
        if request is None:
            raise ValueError('permission evaluation has no approval request')
        if request.base_policy_hash != evaluation.effective_permissions.policy_hash:
            raise ValueError('approval request is bound to a different permission policy')
        expected = approval_fingerprint(request)
        if receipt.request_id != request.request_id or receipt.fingerprint != expected:
            raise ValueError('approval receipt does not match the permission request fingerprint')
        if receipt.reviewer is not evaluation.effective_permissions.approvals_reviewer:
            raise ValueError('approval receipt reviewer does not match the configured reviewer')
        if not receipt.approved:
            return replace(
                evaluation,
                denial_reason=receipt.reason or 'permission request denied',
            )
        overlay = receipt.approved_overlay or request.requested_overlay
        if not _overlay_is_subset(overlay, request.requested_overlay):
            raise ValueError('approval receipt grants permissions outside the requested overlay')
        base_permissions = evaluation.effective_permissions
        profile = merge_additional_permissions(base_permissions.profile, overlay)
        updated = replace(
            base_permissions,
            profile=profile,
            approved_overlay=overlay,
            policy_hash=permission_policy_hash(
                profile,
                approval_policy=base_permissions.approval_policy,
                approvals_reviewer=base_permissions.approvals_reviewer,
                granular_approval=base_permissions.granular_approval,
            ),
        )
        rechecked = self.revalidate_for_execution(
            evaluation,
            effective_permissions=updated,
        )
        if not rechecked.allowed:
            return PermissionEvaluation(
                allowed=False,
                effective_permissions=updated,
                operation=evaluation.operation,
                denial_reason='approved overlay did not satisfy the original operation',
            )
        return rechecked

    def revalidate_for_execution(
        self,
        evaluation: PermissionEvaluation,
        *,
        effective_permissions: EffectivePermissions,
    ) -> PermissionEvaluation:
        request = evaluation.approval_request
        if request is not None and request.session_match_kind == 'patch_path_exact':
            roots = effective_permissions.workspace_roots
            current_match = build_patch_match(
                input_payload=evaluation.operation.input_payload, project_root=roots[0],
            ) if roots else None
            if current_match is None or current_match[3] != request.session_match_components:
                return replace(
                    evaluation, allowed=False, effective_permissions=effective_permissions,
                    denial_reason='patch path identity changed before execution',
                )
        operation = replace(evaluation.operation, approval_required_reason='')
        rechecked = self.evaluate(operation, effective_permissions=effective_permissions)
        return replace(rechecked, operation=evaluation.operation)


def approval_fingerprint(request: PermissionRequest) -> str:
    return stable_payload_hash(request.to_dict())


def additional_permissions_cover(
    granted: AdditionalPermissionProfile,
    requested: AdditionalPermissionProfile,
) -> bool:
    if requested.network_enabled is True and granted.network_enabled is not True:
        return False
    granted_entries = {
        (entry.path.kind, entry.path.value, entry.path.subpath, entry.access)
        for entry in granted.file_system_entries
    }
    return all(
        (entry.path.kind, entry.path.value, entry.path.subpath, entry.access)
        in granted_entries
        for entry in requested.file_system_entries
    )


def stable_payload_hash(payload: Mapping[str, Any]) -> str:
    serialized = json.dumps(
        payload,
        sort_keys=True,
        separators=(',', ':'),
        default=_json_default,
    )
    return hashlib.sha256(serialized.encode('utf-8')).hexdigest()


def _overlay_is_subset(
    granted: AdditionalPermissionProfile,
    requested: AdditionalPermissionProfile,
) -> bool:
    if granted.network_enabled is True and requested.network_enabled is not True:
        return False
    requested_entries = {
        (entry.path.kind, entry.path.value, entry.path.subpath, entry.access)
        for entry in requested.file_system_entries
    }
    return all(
        (entry.path.kind, entry.path.value, entry.path.subpath, entry.access) in requested_entries
        for entry in granted.file_system_entries
    )


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, 'value'):
        return value.value
    try:
        return asdict(value)
    except TypeError:
        return str(value)
