from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from ownpi.runtime.execution.contracts import ApprovalContract
from ownpi.runtime.turn_spine import stable_payload_hash


POLICY_COMPILER_VERSION = 'exec_policy_v1'


@dataclass(frozen=True)
class ExecPolicyDecision:
    decision_id: str
    request_id: str
    tool_name: str
    granted: bool
    behavior: Literal['allow', 'ask', 'deny', 'interrupt']
    permission_decision: dict[str, Any]
    approval: dict[str, Any]
    environment_selection: dict[str, Any]
    sandbox_policy: dict[str, Any]
    enforcement_expectations: dict[str, Any]
    blocked_policy_reasons: tuple[str, ...] = ()
    replay_policy: Literal['replay_requires_rebuild', 'replay_as_observation'] = 'replay_requires_rebuild'
    compiler_version: str = POLICY_COMPILER_VERSION

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> 'ExecPolicyDecision':
        payload = data or {}
        behavior = str(payload.get('behavior', 'deny') or 'deny')
        if behavior not in {'allow', 'ask', 'deny', 'interrupt'}:
            behavior = 'deny'
        replay_policy = str(payload.get('replay_policy', 'replay_requires_rebuild') or 'replay_requires_rebuild')
        if replay_policy not in {'replay_requires_rebuild', 'replay_as_observation'}:
            replay_policy = 'replay_requires_rebuild'
        return cls(
            decision_id=str(payload.get('decision_id', '') or ''),
            request_id=str(payload.get('request_id', '') or ''),
            tool_name=str(payload.get('tool_name', '') or ''),
            granted=_coerce_bool(payload.get('granted', False)),
            behavior=behavior,  # type: ignore[arg-type]
            permission_decision=(
                dict(payload.get('permission_decision', {}))
                if isinstance(payload.get('permission_decision'), dict)
                else {}
            ),
            approval=dict(payload.get('approval', {})) if isinstance(payload.get('approval'), dict) else {},
            environment_selection=(
                dict(payload.get('environment_selection', {}))
                if isinstance(payload.get('environment_selection'), dict)
                else {}
            ),
            sandbox_policy=(
                dict(payload.get('sandbox_policy', {})) if isinstance(payload.get('sandbox_policy'), dict) else {}
            ),
            enforcement_expectations=(
                dict(payload.get('enforcement_expectations', {}))
                if isinstance(payload.get('enforcement_expectations'), dict)
                else {}
            ),
            blocked_policy_reasons=tuple(
                str(item) for item in payload.get('blocked_policy_reasons', []) if str(item).strip()
            )
            if not isinstance(payload.get('blocked_policy_reasons'), str)
            else (str(payload.get('blocked_policy_reasons')).strip(),),
            replay_policy=replay_policy,  # type: ignore[arg-type]
            compiler_version=str(payload.get('compiler_version', POLICY_COMPILER_VERSION) or POLICY_COMPILER_VERSION),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload['blocked_policy_reasons'] = list(self.blocked_policy_reasons)
        return payload


def compile_exec_policy(
    *,
    request_id: str,
    tool_name: str,
    permission_decision: Any,
    approval: ApprovalContract,
    environment_selection: Any,
    enforcement_expectations: dict[str, Any],
    blocked_policy_reasons: tuple[str, ...] = (),
) -> ExecPolicyDecision:
    permission_payload = _to_dict(permission_decision)
    behavior = str(permission_payload.get('behavior') or approval.behavior or 'deny')
    if behavior not in {'allow', 'ask', 'deny', 'interrupt'}:
        behavior = 'deny'
    granted = _coerce_bool(permission_payload.get('granted', approval.granted))
    selection_payload = _environment_selection_payload(environment_selection)
    sandbox_policy = _sandbox_policy_payload(environment_selection)
    base_payload = {
        'request_id': str(request_id or ''),
        'tool_name': str(tool_name or ''),
        'behavior': behavior,
        'granted': granted,
        'permission_decision_id': str(permission_payload.get('decision_id', '') or ''),
        'approval': approval.to_dict(),
        'environment_selection': selection_payload,
        'sandbox_policy': sandbox_policy,
        'enforcement_expectations': dict(enforcement_expectations),
        'blocked_policy_reasons': list(blocked_policy_reasons),
        'compiler_version': POLICY_COMPILER_VERSION,
    }
    return ExecPolicyDecision(
        decision_id=f'exec-policy-{stable_payload_hash(base_payload)[:16]}',
        request_id=str(request_id or ''),
        tool_name=str(tool_name or ''),
        granted=granted,
        behavior=behavior,  # type: ignore[arg-type]
        permission_decision=permission_payload,
        approval=approval.to_dict(),
        environment_selection=selection_payload,
        sandbox_policy=sandbox_policy,
        enforcement_expectations=dict(enforcement_expectations),
        blocked_policy_reasons=tuple(dict.fromkeys(blocked_policy_reasons)),
    )


def exec_policy_hash(decision: ExecPolicyDecision | dict[str, Any]) -> str:
    payload = decision.to_dict() if isinstance(decision, ExecPolicyDecision) else dict(decision)
    return stable_payload_hash(payload)


def _to_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, 'to_dict') and callable(value.to_dict):
        return dict(value.to_dict())
    if isinstance(value, dict):
        return dict(value)
    return {}


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {'true', '1', 'yes', 'y', 'on'}:
            return True
        if normalized in {'false', '0', 'no', 'n', 'off', ''}:
            return False
    if value is None:
        return False
    return bool(value)


def _environment_selection_payload(selection: Any) -> dict[str, Any]:
    descriptor = getattr(selection, 'descriptor', None)
    descriptor_payload = (
        dict(descriptor.to_dict())
        if hasattr(descriptor, 'to_dict') and callable(descriptor.to_dict)
        else (dict(descriptor) if isinstance(descriptor, dict) else {})
    )
    return {
        'provider_kind': str(getattr(selection, 'provider_kind', '') or descriptor_payload.get('provider_kind', '') or ''),
        'selection_reason': str(getattr(selection, 'selection_reason', '') or ''),
        'environment_affinity': str(getattr(selection, 'environment_affinity', '') or ''),
        'execution_class': str(getattr(selection, 'execution_class', '') or ''),
        'backend_kind': str(getattr(selection, 'backend_kind', '') or ''),
        'target_name': str(getattr(selection, 'target_name', '') or ''),
    }


def _sandbox_policy_payload(selection: Any) -> dict[str, Any]:
    policy = getattr(selection, 'sandbox_policy', None)
    if hasattr(policy, 'to_dict') and callable(policy.to_dict):
        return dict(policy.to_dict())
    if isinstance(policy, dict):
        return dict(policy)
    return {}
