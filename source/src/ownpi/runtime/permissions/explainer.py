from __future__ import annotations

from typing import Any

from ownpi.runtime.permissions.models import (
    AskForApproval,
    ApprovalsReviewer,
    EffectivePermissions,
    PermissionPreset,
)
from ownpi.runtime.permissions.presets import permission_preset_selection


_PRESET_LABELS = {
    PermissionPreset.ASK_FOR_APPROVAL: 'Ask for approval',
    PermissionPreset.APPROVE_FOR_ME: 'Approve for me',
    PermissionPreset.FULL_ACCESS: 'Full Access',
}


def permission_preset_label(preset: PermissionPreset | str) -> str:
    normalized = preset if isinstance(preset, PermissionPreset) else PermissionPreset(str(preset))
    return _PRESET_LABELS[normalized]


def canonical_permission_summary(
    effective_permissions: EffectivePermissions | dict[str, Any],
) -> dict[str, Any]:
    effective = _coerce_effective_permissions(effective_permissions)
    profile = effective.profile
    approval_preset = _permission_preset(effective)
    primary_profile_matches = (
        effective.active_profile_id
        == permission_preset_selection(approval_preset).profile_id
    )
    return {
        'preset': approval_preset.value if primary_profile_matches else 'advanced',
        'preset_label': (
            permission_preset_label(approval_preset)
            if primary_profile_matches
            else 'Advanced profile'
        ),
        'approval_preset': approval_preset.value,
        'active_profile_id': effective.active_profile_id,
        'approval_policy': effective.approval_policy.value,
        'approvals_reviewer': effective.approvals_reviewer.value,
        'workspace_roots': [str(path) for path in effective.workspace_roots],
        'policy_hash': effective.policy_hash,
        'sandbox_enforcement': profile.enforcement.value,
        'filesystem_kind': profile.file_system.kind.value,
        'network_policy': profile.network.value,
        'filesystem_entries': [
            {
                'path_kind': entry.path.kind.value,
                'path': entry.path.value,
                'subpath': entry.path.subpath,
                'access': entry.access.value,
                'skip_if_missing': entry.skip_if_missing,
            }
            for entry in profile.file_system.entries
        ],
        'approved_overlay': (
            effective.to_dict().get('approved_overlay')
            if effective.approved_overlay is not None
            else None
        ),
    }


def build_permission_explanation(
    effective_permissions: EffectivePermissions | dict[str, Any],
    *,
    session_permission_grants: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    permission_events: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    latest_environment_receipts: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    tool_summaries: list[Any] | tuple[Any, ...] | None = None,
    latest_execution_contracts: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
) -> dict[str, Any]:
    latest_receipt = (
        dict(latest_environment_receipts[-1])
        if latest_environment_receipts and isinstance(latest_environment_receipts[-1], dict)
        else {}
    )
    latest_contract = (
        dict(latest_execution_contracts[-1])
        if latest_execution_contracts and isinstance(latest_execution_contracts[-1], dict)
        else {}
    )
    grants = [
        _session_grant_card(item)
        for item in (session_permission_grants or ())
        if isinstance(item, dict)
    ]
    events = [
        _permission_event_card(item)
        for item in (permission_events or ())
        if isinstance(item, dict)
    ]
    return {
        'effective_permissions': canonical_permission_summary(effective_permissions),
        'session_permission_grants': grants[-8:],
        'session_permission_grant_count': len(grants),
        'recent_permission_events': events[-8:],
        'permission_event_count': len(events),
        'sandbox_status': _sandbox_status_from_receipt(latest_receipt),
        'latest_environment_receipt': _environment_receipt_card(latest_receipt),
        'tool_summaries': [_tool_summary_dict(item) for item in (tool_summaries or ())][:16],
        'execution_contract_status': _execution_contract_status(latest_contract),
    }


def render_permission_explanation(explanation: dict[str, Any]) -> str:
    effective = (
        explanation.get('effective_permissions', {})
        if isinstance(explanation.get('effective_permissions'), dict)
        else {}
    )
    sandbox = explanation.get('sandbox_status', {})
    sandbox = sandbox if isinstance(sandbox, dict) else {}
    lines = [
        '# Permissions',
        f"- preset: {effective.get('preset_label', '<unknown>')}",
        f"- active_profile: {effective.get('active_profile_id', '<unknown>')}",
        f"- approval_policy: {effective.get('approval_policy', '<unknown>')}",
        f"- approval_reviewer: {effective.get('approvals_reviewer', '<unknown>')}",
        f"- sandbox_enforcement: {effective.get('sandbox_enforcement', '<unknown>')}",
        f"- filesystem: {effective.get('filesystem_kind', '<unknown>')}",
        f"- network: {effective.get('network_policy', '<unknown>')}",
        f"- policy_hash: {effective.get('policy_hash', '<unknown>')}",
    ]
    workspace_roots = effective.get('workspace_roots', [])
    if isinstance(workspace_roots, list) and workspace_roots:
        lines.append(f"- workspace_roots: {_join_preview(workspace_roots)}")
    if sandbox:
        lines.extend(
            [
                f"- sandbox_backend: {sandbox.get('provider_kind', '<none>')}",
                f"- backend_enforcement: {sandbox.get('sandbox_enforcement_status', '<unknown>')}",
            ]
        )
        capabilities = [
            dict(item)
            for item in sandbox.get('enforcement_capabilities', [])
            if isinstance(item, dict)
        ]
        if capabilities:
            rendered = [
                f"{item.get('category')}:{item.get('enforcement_layer')}:"
                f"{'enforced' if item.get('enforced') else 'not_enforced'}"
                for item in capabilities[:6]
            ]
            lines.append(f"- backend_capabilities: {_join_preview(rendered)}")
        blocked = sandbox.get('blocked_policy_reasons', [])
        if isinstance(blocked, list) and blocked:
            lines.append(f"- blocked_policy_reasons: {_join_preview(blocked)}")
    else:
        lines.append('- sandbox_backend: <not observed>')
        lines.append('- backend_enforcement: <not observed>')
        lines.append('- backend_capabilities: <not observed>')

    grants = [
        dict(item)
        for item in explanation.get('session_permission_grants', [])
        if isinstance(item, dict)
    ]
    lines.append(f"## Session Grants ({int(explanation.get('session_permission_grant_count', len(grants)) or 0)})")
    if not grants:
        lines.append('- none')
    for item in grants:
        exact = 'exact-input' if item.get('exact_input_bound') else 'bounded-overlay'
        lines.append(
            f"- {item.get('tool_name', '<unknown>')} action={item.get('action', '<unknown>')} "
            f"reviewer={item.get('reviewer', '<unknown>')} binding={exact}"
        )

    events = [
        dict(item)
        for item in explanation.get('recent_permission_events', [])
        if isinstance(item, dict)
    ]
    lines.append(f"## Recent Permission Events ({int(explanation.get('permission_event_count', len(events)) or 0)})")
    if not events:
        lines.append('- none')
    for item in events:
        lines.append(
            f"- {item.get('event_id', '<unknown>')} tool={item.get('tool_name', '<unknown>')} "
            f"decision={item.get('decision', '<unknown>')} scope={item.get('decision_scope') or '<none>'}"
        )
        reason = str(item.get('reason', '') or '').strip()
        if reason:
            lines.append(f"  - reason={reason}")

    receipt = explanation.get('latest_environment_receipt', {})
    lines.append('## Latest Enforcement Receipt')
    if isinstance(receipt, dict) and receipt:
        lines.append(
            f"- {receipt.get('receipt_id', '<unknown>')} provider={receipt.get('provider_kind', '<unknown>')} "
            f"status={receipt.get('status', '<unknown>')} tool={receipt.get('tool_name', '<unknown>')}"
        )
    else:
        lines.append('- none observed')
    contract = explanation.get('execution_contract_status', {})
    if isinstance(contract, dict) and contract:
        lines.append(
            f"- execution_contract: {contract.get('contract_id', '<unknown>')} "
            f"approval={contract.get('approval_behavior', '<unknown>')} "
            f"granted={bool(contract.get('approval_granted', False))}"
        )
        contract_blocked = contract.get('blocked_policy_reasons', [])
        if isinstance(contract_blocked, list) and contract_blocked:
            lines.append(f"- execution_contract_blocked: {_join_preview(contract_blocked)}")
    return '\n'.join(lines)


def _coerce_effective_permissions(
    value: EffectivePermissions | dict[str, Any],
) -> EffectivePermissions:
    if isinstance(value, EffectivePermissions):
        return value
    if not isinstance(value, dict):
        raise TypeError('effective_permissions must be EffectivePermissions or a canonical mapping')
    return EffectivePermissions.from_dict(value)


def _permission_preset(effective: EffectivePermissions) -> PermissionPreset:
    if effective.approval_policy is AskForApproval.NEVER:
        return PermissionPreset.FULL_ACCESS
    if effective.approvals_reviewer is ApprovalsReviewer.AUTO_REVIEW:
        return PermissionPreset.APPROVE_FOR_ME
    return PermissionPreset.ASK_FOR_APPROVAL


def _session_grant_card(grant: dict[str, Any]) -> dict[str, Any]:
    return {
        'grant_id': str(grant.get('grant_id', '') or ''),
        'run_id': str(grant.get('run_id', '') or ''),
        'runtime_session_id': str(grant.get('runtime_session_id', '') or ''),
        'task_id': str(grant.get('task_id', '') or ''),
        'tool_name': str(grant.get('tool_name', '') or ''),
        'action': str(grant.get('action', '') or ''),
        'reviewer': str(grant.get('reviewer', '') or ''),
        'environment_hash': str(grant.get('environment_hash', '') or ''),
        'base_policy_hash': str(grant.get('base_policy_hash', '') or ''),
        'exact_input_bound': bool(grant.get('exact_input_hash')),
        'approved_overlay': (
            dict(grant.get('approved_overlay', {}))
            if isinstance(grant.get('approved_overlay'), dict)
            else {}
        ),
        'created_at': grant.get('created_at'),
    }


def _permission_event_card(event: dict[str, Any]) -> dict[str, Any]:
    resolution = str(event.get('resolution') or event.get('decision') or '').strip()
    if not resolution:
        resolution = 'allow' if bool(event.get('granted', False)) else 'deny'
    return {
        'event_id': str(event.get('event_id', '') or ''),
        'request_id': str(event.get('request_id', '') or ''),
        'tool_name': str(event.get('tool_name', '') or ''),
        'decision': resolution,
        'behavior': str(event.get('behavior', '') or ''),
        'granted': bool(event.get('granted', False)),
        'decision_scope': str(event.get('decision_scope', '') or ''),
        'reviewer': str(event.get('reviewer', '') or ''),
        'session_id': str(event.get('session_id', '') or ''),
        'reason': str(event.get('reason', '') or ''),
        'stage': str(event.get('stage', '') or ''),
        'attempt': int(event.get('attempt', 0) or 0),
        'continuation_index': int(event.get('continuation_index', 0) or 0),
        'created_at': event.get('created_at'),
    }


def _sandbox_status_from_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    if not receipt:
        return {}
    descriptor = receipt.get('descriptor', {}) if isinstance(receipt.get('descriptor'), dict) else {}
    metadata = receipt.get('provider_metadata', {}) if isinstance(receipt.get('provider_metadata'), dict) else {}
    return {
        'provider_kind': str(receipt.get('provider_kind') or descriptor.get('provider_kind') or '<none>'),
        'sandbox_enforcement_status': str(metadata.get('sandbox_enforcement_status', 'unknown') or 'unknown'),
        'enforcement_capabilities': [
            dict(item) for item in metadata.get('enforcement_capabilities', []) if isinstance(item, dict)
        ],
        'blocked_policy_reasons': [
            str(item) for item in metadata.get('blocked_policy_reasons', []) if str(item).strip()
        ],
    }


def _environment_receipt_card(receipt: dict[str, Any]) -> dict[str, Any]:
    if not receipt:
        return {}
    return {
        'receipt_id': str(receipt.get('receipt_id', '') or ''),
        'provider_kind': str(receipt.get('provider_kind', '') or ''),
        'request_kind': str(receipt.get('request_kind', '') or ''),
        'tool_name': str(receipt.get('tool_name', '') or ''),
        'status': str(receipt.get('status', '') or ''),
        'environment_policy_hash': str(receipt.get('environment_policy_hash', '') or ''),
    }


def _tool_summary_dict(item: Any) -> dict[str, Any]:
    if hasattr(item, 'to_dict'):
        return dict(item.to_dict())
    if isinstance(item, dict):
        return dict(item)
    return {'name': str(getattr(item, 'name', '') or '')}


def _execution_contract_status(contract: dict[str, Any]) -> dict[str, Any]:
    if not contract:
        return {}
    approval = contract.get('approval', {}) if isinstance(contract.get('approval'), dict) else {}
    return {
        'contract_id': str(contract.get('contract_id', '') or ''),
        'tool_name': str(contract.get('tool_name', '') or ''),
        'status': str(contract.get('status', '') or ''),
        'approval_behavior': str(approval.get('behavior', '') or ''),
        'approval_granted': bool(approval.get('granted', False)),
        'base_policy_hash': str(contract.get('base_policy_hash', '') or ''),
        'blocked_policy_reasons': [
            str(item) for item in contract.get('blocked_policy_reasons', []) if str(item).strip()
        ],
    }


def _join_preview(values: list[Any], *, limit: int = 4) -> str:
    normalized = [str(item) for item in values if str(item).strip()]
    suffix = f' (+{len(normalized) - limit})' if len(normalized) > limit else ''
    return ', '.join(normalized[:limit]) + suffix
