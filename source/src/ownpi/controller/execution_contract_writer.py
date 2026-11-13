from __future__ import annotations

from typing import Any


def execution_contract_payload(contract: dict[str, Any] | Any, *, contract_hash: str = '') -> dict[str, Any]:
    if hasattr(contract, 'to_dict'):
        payload = dict(contract.to_dict())
    elif isinstance(contract, dict):
        payload = dict(contract)
    else:
        payload = {}
    if not payload:
        return {}
    if contract_hash:
        payload['execution_contract_hash'] = contract_hash
    payload.setdefault('approval', {})
    payload.setdefault('enforcement_expectations', {})
    return payload


def exec_policy_payload(decision: dict[str, Any] | Any | None, *, policy_hash: str = '') -> dict[str, Any]:
    if decision is None:
        return {}
    if hasattr(decision, 'to_dict'):
        payload = dict(decision.to_dict())
    elif isinstance(decision, dict):
        payload = dict(decision)
    else:
        payload = {}
    if not payload:
        return {}
    if policy_hash:
        payload['exec_policy_hash'] = policy_hash
    payload.setdefault('compiler_version', '')
    payload.setdefault('blocked_policy_reasons', [])
    return payload


def record_execution_contract_compiled(
    store: Any,
    contract: dict[str, Any] | Any,
    *,
    status: str,
    stage: str = '',
    attempt: int = 0,
    query_chain_id: str = '',
    query_chain_depth: int = 0,
    continuation_index: int = 0,
    request_id: str = '',
    tool_name: str = '',
    contract_hash: str = '',
    exec_policy_decision: dict[str, Any] | Any | None = None,
    exec_policy_hash_value: str = '',
    effective_permissions: Any | None = None,
) -> None:
    payload = execution_contract_payload(contract, contract_hash=contract_hash)
    permissions_payload = (
        effective_permissions.to_dict()
        if effective_permissions is not None and hasattr(effective_permissions, 'to_dict')
        else {}
    )
    if payload and permissions_payload:
        payload['effective_permissions_observation'] = permissions_payload
    if payload:
        payload['authorization_evidence_only'] = True
        store.state.environment_state.latest_execution_contract = dict(payload)
    store.record_exec_policy_decision(
        exec_policy_decision,
        status=status,
        stage=stage,
        attempt=attempt,
        query_chain_id=query_chain_id,
        query_chain_depth=query_chain_depth,
        continuation_index=continuation_index,
        request_id=request_id,
        tool_name=tool_name,
        policy_hash=exec_policy_hash_value,
    )
    store.record_environment_mutation(
        event_name='execution_contract_compiled',
        status=status,
        mutation_kind='request_created',
        stage=stage,
        attempt=attempt,
        query_chain_id=query_chain_id,
        query_chain_depth=query_chain_depth,
        continuation_index=continuation_index,
        request_id=request_id or None,
        tool_name=tool_name or None,
        payload=payload,
        replay_policy='replay_requires_rebuild',
        mutation_refs=(
            {
                'ref_type': 'execution_contract',
                'ref_id': str(payload.get('contract_id', '') or ''),
                'summary': str(payload.get('tool_name', '') or ''),
            },
        ),
    )


def record_exec_policy_decision(
    store: Any,
    decision: dict[str, Any] | Any | None,
    *,
    status: str,
    stage: str = '',
    attempt: int = 0,
    query_chain_id: str = '',
    query_chain_depth: int = 0,
    continuation_index: int = 0,
    request_id: str = '',
    tool_name: str = '',
    policy_hash: str = '',
) -> None:
    payload = exec_policy_payload(decision, policy_hash=policy_hash)
    if not payload:
        return
    store.state.environment_state.latest_exec_policy_decision = dict(payload)
    store.record_environment_mutation(
        event_name='exec_policy_decision_recorded',
        status=status,
        mutation_kind='decision_recorded',
        stage=stage,
        attempt=attempt,
        query_chain_id=query_chain_id,
        query_chain_depth=query_chain_depth,
        continuation_index=continuation_index,
        request_id=request_id or None,
        tool_name=tool_name or None,
        payload=payload,
        replay_policy='replay_requires_rebuild',
        mutation_refs=(
            {
                'ref_type': 'exec_policy_decision',
                'ref_id': str(payload.get('decision_id', '') or ''),
                'summary': str(payload.get('tool_name', '') or ''),
            },
        ),
    )
