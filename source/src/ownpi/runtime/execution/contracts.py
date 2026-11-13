from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any

from ownpi.runtime.environment.contracts import (
    EnvironmentDescriptor,
    SandboxPolicy,
    build_environment_policy_hash,
)
from ownpi.runtime.environment.enforcement import (
    classify_enforcement_status,
    compile_enforcement_capabilities,
)
from ownpi.runtime.turn_spine import stable_payload_hash
from ownpi.runtime.permissions.input_identity import permission_input_hash


_SECRET_KEY_PARTS = ('token', 'secret', 'password', 'api_key', 'apikey', 'credential', 'auth')


@dataclass(frozen=True)
class ApprovalContract:
    behavior: str
    granted: bool
    source: str = ''
    session_id: str | None = None
    decision_scope: str = ''
    reason: str = ''

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> 'ApprovalContract':
        payload = data or {}
        return cls(
            behavior=str(payload.get('behavior', 'deny') or 'deny'),
            granted=bool(payload.get('granted', False)),
            source=str(payload.get('source', '') or ''),
            session_id=(str(payload['session_id']) if payload.get('session_id') is not None else None),
            decision_scope=str(payload.get('decision_scope', '') or ''),
            reason=str(payload.get('reason', '') or ''),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionAttemptContract:
    contract_id: str
    request_id: str
    tool_name: str
    exec_policy_decision_id: str
    exec_policy_hash: str
    policy_compiler_version: str
    permission_class: str
    backend_kind: str
    execution_class: str
    requested_at: str
    tool_summary: dict[str, Any]
    input_hash: str
    sanitized_input_preview: dict[str, Any]
    permission_decision: dict[str, Any]
    approval: ApprovalContract
    environment_selection: dict[str, Any]
    descriptor: dict[str, Any]
    sandbox_policy: dict[str, Any]
    provider_kind: str
    provider_config_hash: str
    enforcement_expectations: dict[str, Any]
    canonical_action_hash: str = ''
    canonical_effect_hash: str = ''
    base_policy_hash: str = ''
    approved_overlay_hash: str = ''
    authorization_evidence_only: bool = True
    blocked_policy_reasons: tuple[str, ...] = ()
    status: str = 'compiled'

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> 'ExecutionAttemptContract':
        payload = data or {}
        return cls(
            contract_id=str(payload.get('contract_id', '') or ''),
            request_id=str(payload.get('request_id', '') or ''),
            tool_name=str(payload.get('tool_name', '') or ''),
            exec_policy_decision_id=str(payload.get('exec_policy_decision_id', '') or ''),
            exec_policy_hash=str(payload.get('exec_policy_hash', '') or ''),
            policy_compiler_version=str(payload.get('policy_compiler_version', '') or ''),
            permission_class=str(payload.get('permission_class', '') or ''),
            backend_kind=str(payload.get('backend_kind', '') or ''),
            execution_class=str(payload.get('execution_class', '') or ''),
            requested_at=str(payload.get('requested_at', '') or ''),
            tool_summary=dict(payload.get('tool_summary', {})) if isinstance(payload.get('tool_summary'), dict) else {},
            input_hash=str(payload.get('input_hash', '') or ''),
            sanitized_input_preview=(
                dict(payload.get('sanitized_input_preview', {}))
                if isinstance(payload.get('sanitized_input_preview'), dict)
                else {}
            ),
            permission_decision=(
                dict(payload.get('permission_decision', {}))
                if isinstance(payload.get('permission_decision'), dict)
                else {}
            ),
            approval=ApprovalContract.from_dict(
                payload.get('approval') if isinstance(payload.get('approval'), dict) else {}
            ),
            environment_selection=(
                dict(payload.get('environment_selection', {}))
                if isinstance(payload.get('environment_selection'), dict)
                else {}
            ),
            descriptor=dict(payload.get('descriptor', {})) if isinstance(payload.get('descriptor'), dict) else {},
            sandbox_policy=(
                dict(payload.get('sandbox_policy', {}))
                if isinstance(payload.get('sandbox_policy'), dict)
                else {}
            ),
            provider_kind=str(payload.get('provider_kind', '') or ''),
            provider_config_hash=str(payload.get('provider_config_hash', '') or ''),
            enforcement_expectations=(
                dict(payload.get('enforcement_expectations', {}))
                if isinstance(payload.get('enforcement_expectations'), dict)
                else {}
            ),
            canonical_action_hash=str(payload.get('canonical_action_hash', '') or ''),
            canonical_effect_hash=str(payload.get('canonical_effect_hash', '') or ''),
            base_policy_hash=str(payload.get('base_policy_hash', '') or ''),
            approved_overlay_hash=str(payload.get('approved_overlay_hash', '') or ''),
            authorization_evidence_only=bool(
                payload.get('authorization_evidence_only', True)
            ),
            blocked_policy_reasons=tuple(
                str(item) for item in payload.get('blocked_policy_reasons', []) if str(item).strip()
            )
            if not isinstance(payload.get('blocked_policy_reasons'), str)
            else (str(payload.get('blocked_policy_reasons')).strip(),),
            status=str(payload.get('status', 'compiled') or 'compiled'),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload['approval'] = self.approval.to_dict()
        payload['blocked_policy_reasons'] = list(self.blocked_policy_reasons)
        return payload


def build_execution_attempt_contract(
    *,
    request_id: str,
    tool_name: str,
    input_payload: dict[str, Any],
    requested_at: str,
    tool_summary: Any,
    permission_decision: Any,
    approval: ApprovalContract,
    environment_selection: Any,
    provider_config: dict[str, Any] | None = None,
    status: str = 'compiled',
    exec_policy_decision: Any | None = None,
    exec_policy_hash_value: str = '',
    effective_permissions: Any | None = None,
) -> ExecutionAttemptContract:
    summary_payload = _to_dict(tool_summary)
    permission_payload = _to_dict(permission_decision)
    exec_policy_payload = _to_dict(exec_policy_decision)
    descriptor = _descriptor_from_selection(environment_selection)
    sandbox_policy = _sandbox_policy_from_selection(environment_selection)
    provider_kind = str(
        getattr(environment_selection, 'provider_kind', '')
        or descriptor.get('provider_kind')
        or ''
    )
    execution_class = str(
        getattr(environment_selection, 'execution_class', '')
        or summary_payload.get('execution_class')
        or ''
    )
    backend_kind = str(
        getattr(environment_selection, 'backend_kind', '')
        or summary_payload.get('backend_kind')
        or ''
    )
    permission_class = str(
        permission_payload.get('permission_class')
        or summary_payload.get('permission_class')
        or ''
    )
    blocked_policy_reasons = _blocked_policy_reasons(
        selection=environment_selection,
        sandbox_policy=sandbox_policy,
        descriptor=descriptor,
    )
    provider_config_hash = stable_payload_hash(provider_config or {})
    effective_permissions_payload = _to_dict(effective_permissions)
    approved_overlay = effective_permissions_payload.get('approved_overlay')
    approved_overlay_hash = (
        stable_payload_hash(approved_overlay)
        if isinstance(approved_overlay, dict) and approved_overlay
        else ''
    )
    canonical_action_hash = str(
        permission_payload.get('canonical_action_hash', '') or ''
    )
    canonical_effect_hash = str(
        permission_payload.get('canonical_effect_hash', '') or ''
    )
    base_policy_hash = str(
        permission_payload.get('base_policy_hash', '') or ''
    )
    base_payload = {
        'request_id': request_id,
        'tool_name': tool_name,
        'input_hash': permission_input_hash(input_payload),
        'exec_policy_decision_id': str(exec_policy_payload.get('decision_id', '') or ''),
        'exec_policy_hash': str(exec_policy_hash_value or exec_policy_payload.get('exec_policy_hash', '') or ''),
        'permission_decision': permission_payload,
        'approval': approval.to_dict(),
        'environment_selection': _environment_selection_payload(environment_selection),
        'descriptor': descriptor,
        'sandbox_policy': sandbox_policy,
        'provider_config_hash': provider_config_hash,
        'canonical_action_hash': canonical_action_hash,
        'canonical_effect_hash': canonical_effect_hash,
        'base_policy_hash': base_policy_hash,
        'approved_overlay_hash': approved_overlay_hash,
        'authorization_evidence_only': True,
    }
    contract_id = f'exec-contract-{hashlib.sha256(json.dumps(base_payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()[:16]}'
    return ExecutionAttemptContract(
        contract_id=contract_id,
        request_id=str(request_id or ''),
        tool_name=str(tool_name or ''),
        exec_policy_decision_id=str(exec_policy_payload.get('decision_id', '') or ''),
        exec_policy_hash=str(exec_policy_hash_value or exec_policy_payload.get('exec_policy_hash', '') or ''),
        policy_compiler_version=str(exec_policy_payload.get('compiler_version', '') or ''),
        permission_class=permission_class,
        backend_kind=backend_kind,
        execution_class=execution_class,
        requested_at=requested_at,
        tool_summary=summary_payload,
        input_hash=permission_input_hash(input_payload),
        sanitized_input_preview=sanitize_input_preview(
            input_payload,
            secret_fields=summary_payload.get('secret_input_fields', ()),
        ),
        permission_decision=permission_payload,
        approval=approval,
        environment_selection=_environment_selection_payload(environment_selection),
        descriptor=descriptor,
        sandbox_policy=sandbox_policy,
        provider_kind=provider_kind,
        provider_config_hash=provider_config_hash,
        enforcement_expectations=_enforcement_expectations(
            provider_kind=provider_kind,
            descriptor=descriptor,
            sandbox_policy=sandbox_policy,
            blocked_policy_reasons=blocked_policy_reasons,
        ),
        canonical_action_hash=canonical_action_hash,
        canonical_effect_hash=canonical_effect_hash,
        base_policy_hash=base_policy_hash,
        approved_overlay_hash=approved_overlay_hash,
        authorization_evidence_only=True,
        blocked_policy_reasons=blocked_policy_reasons,
        status=str(status or 'compiled'),
    )


def execution_contract_hash(contract: ExecutionAttemptContract | dict[str, Any]) -> str:
    payload = contract.to_dict() if isinstance(contract, ExecutionAttemptContract) else dict(contract)
    return stable_payload_hash(payload)


def sanitize_input_preview(
    input_payload: dict[str, Any],
    *,
    secret_fields: Any = (),
    max_items: int = 24,
    max_text: int = 160,
) -> dict[str, Any]:
    secret_keys = {str(item).casefold() for item in secret_fields if str(item).strip()}
    preview: dict[str, Any] = {}
    for index, key in enumerate(sorted(input_payload.keys(), key=str)):
        if index >= max_items:
            preview['...'] = f'+{len(input_payload) - max_items} keys'
            break
        preview[str(key)] = _sanitize_value(
            key=str(key),
            value=input_payload[key],
            secret_keys=secret_keys,
            max_text=max_text,
        )
    return preview


def _sanitize_value(*, key: str, value: Any, secret_keys: set[str], max_text: int) -> Any:
    normalized_key = key.casefold()
    if normalized_key == 'env_overrides' or normalized_key in secret_keys or any(part in normalized_key for part in _SECRET_KEY_PARTS):
        return '<redacted>'
    if isinstance(value, dict):
        return {
            str(child_key): _sanitize_value(
                key=str(child_key),
                value=child_value,
                secret_keys=secret_keys,
                max_text=max_text,
            )
            for child_key, child_value in list(value.items())[:16]
        }
    if isinstance(value, (list, tuple)):
        return [
            _sanitize_value(key=key, value=item, secret_keys=secret_keys, max_text=max_text)
            for item in list(value)[:16]
        ]
    if isinstance(value, str):
        return value if len(value) <= max_text else value[: max(0, max_text - 18)] + '...[truncated]'
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def _to_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, 'to_dict') and callable(value.to_dict):
        return dict(value.to_dict())
    if isinstance(value, dict):
        return dict(value)
    return {}


def _descriptor_from_selection(selection: Any) -> dict[str, Any]:
    descriptor = getattr(selection, 'descriptor', None)
    if isinstance(descriptor, EnvironmentDescriptor):
        return descriptor.to_dict()
    if hasattr(descriptor, 'to_dict') and callable(descriptor.to_dict):
        return dict(descriptor.to_dict())
    if isinstance(descriptor, dict):
        return dict(descriptor)
    return {}


def _sandbox_policy_from_selection(selection: Any) -> dict[str, Any]:
    policy = getattr(selection, 'sandbox_policy', None)
    if isinstance(policy, SandboxPolicy):
        return policy.to_dict()
    if hasattr(policy, 'to_dict') and callable(policy.to_dict):
        return dict(policy.to_dict())
    if isinstance(policy, dict):
        return dict(policy)
    return {}


def _environment_selection_payload(selection: Any) -> dict[str, Any]:
    return {
        'provider_kind': str(getattr(selection, 'provider_kind', '') or ''),
        'selection_reason': str(getattr(selection, 'selection_reason', '') or ''),
        'environment_affinity': str(getattr(selection, 'environment_affinity', '') or ''),
        'execution_class': str(getattr(selection, 'execution_class', '') or ''),
        'backend_kind': str(getattr(selection, 'backend_kind', '') or ''),
        'target_name': str(getattr(selection, 'target_name', '') or ''),
    }


def _blocked_policy_reasons(
    *,
    selection: Any,
    sandbox_policy: dict[str, Any],
    descriptor: dict[str, Any],
) -> tuple[str, ...]:
    reasons: list[str] = []
    if descriptor.get('provider_kind') == 'host_local' and bool(sandbox_policy.get('requires_isolation', False)):
        reasons.append('host_local_requires_isolation')
    return tuple(dict.fromkeys(reasons))


def _enforcement_expectations(
    *,
    provider_kind: str,
    descriptor: dict[str, Any],
    sandbox_policy: dict[str, Any],
    blocked_policy_reasons: tuple[str, ...],
) -> dict[str, Any]:
    allow_network = bool(sandbox_policy.get('allow_network', True))
    descriptor_obj = EnvironmentDescriptor.from_dict(descriptor)
    sandbox_policy_obj = SandboxPolicy.from_receipt_dict(sandbox_policy)
    capabilities = compile_enforcement_capabilities(
        descriptor=descriptor_obj,
        sandbox_policy=sandbox_policy_obj,
        provider_kind=provider_kind,
    )
    return {
        'provider_kind': provider_kind,
        'sandbox_enforcement_status': (
            'blocked' if blocked_policy_reasons else classify_enforcement_status(capabilities)
        ),
        'effective_read_roots': [
            str(item)
            for item in sandbox_policy.get('readable_host_roots', [])
            if str(item).strip()
        ],
        'effective_write_roots': [
            str(item)
            for item in sandbox_policy.get('writable_host_roots', [])
            if str(item).strip()
        ],
        'allow_network': allow_network,
        'enforcement_capabilities': [item.to_dict() for item in capabilities],
        'unsupported_policy_reasons': [
            item.reason
            for item in capabilities
            if item.requested and not item.enforced and item.enforcement_layer == 'unsupported'
        ],
        'preflight_only_policy_reasons': [
            item.reason
            for item in capabilities
            if item.requested and not item.enforced and item.enforcement_layer == 'preflight'
        ],
        'environment_policy_hash': build_environment_policy_hash(
            descriptor=descriptor_obj,
            sandbox_policy=sandbox_policy_obj,
        ),
    }
