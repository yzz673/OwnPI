from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from ownpi.stage_ids import canonical_stage_id


class ToolCallBatchPolicy(StrEnum):
    COMPATIBLE = 'compatible'
    TURN_EXCLUSIVE = 'turn_exclusive'

def _read_continuation_index(data: dict[str, Any], *fallback_maps: dict[str, Any]) -> int:
    for payload in (data, *fallback_maps):
        if not isinstance(payload, dict):
            continue
        if payload.get('continuation_index') is not None:
            return int(payload.get('continuation_index', 0) or 0)
    return 0


def _read_query_chain_id(data: dict[str, Any], *fallback_maps: dict[str, Any]) -> str:
    for payload in (data, *fallback_maps):
        if not isinstance(payload, dict):
            continue
        if payload.get('query_chain_id') is not None:
            return str(payload.get('query_chain_id', '') or '').strip()
    return ''


def _read_query_chain_depth(data: dict[str, Any], *fallback_maps: dict[str, Any]) -> int:
    for payload in (data, *fallback_maps):
        if not isinstance(payload, dict):
            continue
        if payload.get('query_chain_depth') is not None:
            return int(payload.get('query_chain_depth', 0) or 0)
    return 0


@dataclass(frozen=True)
class ToolDescriptorSummary:
    name: str
    kind: str = 'generic'
    description: str | None = None
    detail: str | None = None
    aliases: tuple[str, ...] = ()
    search_hint: str | None = None
    search_keywords: tuple[str, ...] = ()
    max_prompt_result_chars: int = 900
    max_result_size_chars: int = 0
    aggregate_budget_weight: int = 1
    result_identity_fields: tuple[str, ...] = ()
    working_set_priority: str = 'normal'
    read_only: bool = False
    concurrency_safe: bool = False
    batch_policy: ToolCallBatchPolicy = ToolCallBatchPolicy.COMPATIBLE
    interrupt_behavior: str = 'block'
    permission_class: str = 'default'
    permission_matcher_kind: str = 'default'
    observable_input_fields: tuple[str, ...] = ()
    secret_input_fields: tuple[str, ...] = ()
    invocation_mode: str = 'agent_requested'
    result_family: str = 'generic'
    writes_workspace: bool = False
    requires_network: bool = False
    produces_artifact_refs: bool = False
    toolset_family: str = 'generic'
    backend_kind: str = 'generic'
    environment_affinity: str = 'host_preferred'
    execution_class: str = 'read_only'
    side_effect_level: str = 'none'
    isolation_requirement: str = 'in_process'
    credential_mode: str = 'none'
    resume_strategy: str = 'stateless'
    destructive_capable: bool = False
    destructive_kinds: tuple[str, ...] = ()
    schema_cache_variant: str = ''
    strict_schema: bool = True
    action_primitives: tuple[str, ...] = ()
    primary_intent: str = ''
    grounding_lane: str = 'open'
    input_contract_family: str = ''
    selection_disambiguators: tuple[str, ...] = ()
    preferred_predecessors: tuple[str, ...] = ()
    preferred_successors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.batch_policy, ToolCallBatchPolicy):
            object.__setattr__(
                self,
                'batch_policy',
                ToolCallBatchPolicy(str(self.batch_policy or ToolCallBatchPolicy.COMPATIBLE)),
            )
        if not str(self.interrupt_behavior or '').strip():
            object.__setattr__(self, 'interrupt_behavior', 'block')

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'ToolDescriptorSummary':
        return cls(
            name=str(data.get('name', '')),
            kind=str(data.get('kind', 'generic')),
            description=(str(data['description']) if data.get('description') is not None else None),
            detail=(str(data['detail']) if data.get('detail') is not None else None),
            aliases=_coerce_string_tuple(data.get('aliases')),
            search_hint=(str(data['search_hint']) if data.get('search_hint') is not None else None),
            search_keywords=_coerce_string_tuple(data.get('search_keywords')),
            max_prompt_result_chars=int(data.get('max_prompt_result_chars', 900) or 0),
            max_result_size_chars=int(data.get('max_result_size_chars', data.get('max_prompt_result_chars', 0)) or 0),
            aggregate_budget_weight=int(data.get('aggregate_budget_weight', 1) or 1),
            result_identity_fields=_coerce_string_tuple(data.get('result_identity_fields')),
            working_set_priority=str(data.get('working_set_priority', 'normal') or 'normal'),
            read_only=bool(data.get('read_only', False)),
            concurrency_safe=bool(data.get('concurrency_safe', False)),
            batch_policy=ToolCallBatchPolicy(
                str(data.get('batch_policy', ToolCallBatchPolicy.COMPATIBLE))
            ),
            interrupt_behavior=str(data.get('interrupt_behavior', 'block') or 'block'),
            permission_class=str(data.get('permission_class', 'default')),
            permission_matcher_kind=str(data.get('permission_matcher_kind', 'default') or 'default'),
            observable_input_fields=_coerce_string_tuple(data.get('observable_input_fields')),
            secret_input_fields=_coerce_string_tuple(data.get('secret_input_fields')),
            invocation_mode=str(data.get('invocation_mode', 'agent_requested')),
            result_family=str(data.get('result_family', 'generic') or 'generic'),
            writes_workspace=bool(data.get('writes_workspace', False)),
            requires_network=bool(data.get('requires_network', False)),
            produces_artifact_refs=bool(data.get('produces_artifact_refs', False)),
            toolset_family=str(data.get('toolset_family', 'generic') or 'generic'),
            backend_kind=str(data.get('backend_kind', 'generic') or 'generic'),
            environment_affinity=str(data.get('environment_affinity', 'host_preferred') or 'host_preferred'),
            execution_class=str(data.get('execution_class', 'read_only') or 'read_only'),
            side_effect_level=str(data.get('side_effect_level', 'none') or 'none'),
            isolation_requirement=str(data.get('isolation_requirement', 'in_process') or 'in_process'),
            credential_mode=str(data.get('credential_mode', 'none') or 'none'),
            resume_strategy=str(data.get('resume_strategy', 'stateless') or 'stateless'),
            destructive_capable=bool(data.get('destructive_capable', False)),
            destructive_kinds=_coerce_string_tuple(data.get('destructive_kinds')),
            schema_cache_variant=str(data.get('schema_cache_variant', '') or ''),
            strict_schema=bool(data.get('strict_schema', True)),
            action_primitives=_coerce_string_tuple(data.get('action_primitives')),
            primary_intent=str(data.get('primary_intent', '') or ''),
            grounding_lane=str(data.get('grounding_lane', 'open') or 'open'),
            input_contract_family=str(data.get('input_contract_family', '') or ''),
            selection_disambiguators=_coerce_string_tuple(data.get('selection_disambiguators')),
            preferred_predecessors=_coerce_string_tuple(data.get('preferred_predecessors')),
            preferred_successors=_coerce_string_tuple(data.get('preferred_successors')),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary_text(self) -> str:
        return self.description or self.detail or self.name


@dataclass(frozen=True)
class ToolABI:
    tool_name: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    schema_name: str = ''
    description: str | None = None
    protocol_version: str = 'claude_code_tool_use_v1'
    strict: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'ToolABI':
        input_schema = data.get('input_schema')
        return cls(
            tool_name=str(data.get('tool_name', data.get('name', '')) or ''),
            input_schema=dict(input_schema) if isinstance(input_schema, dict) else {},
            schema_name=str(data.get('schema_name', '') or ''),
            description=(str(data['description']) if data.get('description') is not None else None),
            protocol_version=str(data.get('protocol_version', 'claude_code_tool_use_v1') or 'claude_code_tool_use_v1'),
            strict=bool(data.get('strict', True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            'tool_name': self.tool_name,
            'input_schema': dict(self.input_schema),
            'schema_name': self.schema_name,
            'description': self.description,
            'protocol_version': self.protocol_version,
            'strict': self.strict,
        }


@dataclass(frozen=True)
class ToolPermissionDecision:
    behavior: str
    granted: bool
    reason: str = ''
    tool_name: str | None = None
    permission_class: str | None = None
    human_prompt: str | None = None
    continuation_kind: str = 'continuation_runtime'
    base_permission_class: str | None = None
    destructive_action: bool = False
    destructive_reason: str = ''
    destructive_kinds: tuple[str, ...] = ()
    decision_id: str = ''
    decision_steps: list[dict[str, Any]] = field(default_factory=list)
    safety_check_kind: str = ''
    permission_matcher_kind: str = 'default'
    canonical_action_hash: str = ''
    canonical_effect_hash: str = ''
    base_policy_hash: str = ''

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'ToolPermissionDecision':
        return cls(
            behavior=str(data.get('behavior', 'deny') or 'deny'),
            granted=bool(data.get('granted', False)),
            reason=str(data.get('reason', '') or ''),
            tool_name=(str(data['tool_name']) if data.get('tool_name') is not None else None),
            permission_class=(str(data['permission_class']) if data.get('permission_class') is not None else None),
            human_prompt=(str(data['human_prompt']) if data.get('human_prompt') is not None else None),
            continuation_kind=str(data.get('continuation_kind', 'continuation_runtime') or 'continuation_runtime'),
            base_permission_class=(
                str(data['base_permission_class']) if data.get('base_permission_class') is not None else None
            ),
            destructive_action=bool(data.get('destructive_action', False)),
            destructive_reason=str(data.get('destructive_reason', '') or ''),
            destructive_kinds=_coerce_string_tuple(data.get('destructive_kinds')),
            decision_id=str(data.get('decision_id', '') or ''),
            decision_steps=[
                dict(item) for item in data.get('decision_steps', []) if isinstance(item, dict)
            ],
            safety_check_kind=str(data.get('safety_check_kind', '') or ''),
            permission_matcher_kind=str(data.get('permission_matcher_kind', 'default') or 'default'),
            canonical_action_hash=str(data.get('canonical_action_hash', '') or ''),
            canonical_effect_hash=str(data.get('canonical_effect_hash', '') or ''),
            base_policy_hash=str(data.get('base_policy_hash', '') or ''),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ToolPermissionEvent:
    event_id: str
    request_id: str
    tool_name: str
    permission_class: str
    behavior: str
    granted: bool
    reason: str = ''
    stage: str = ''
    attempt: int = 0
    query_chain_id: str = ''
    query_chain_depth: int = 0
    continuation_index: int = 0
    requested_by: str = 'agent'
    session_id: str | None = None
    resolution: str | None = None
    decision_scope: str | None = None
    base_permission_class: str | None = None
    destructive_action: bool = False
    destructive_reason: str = ''
    destructive_kinds: tuple[str, ...] = ()
    decision_id: str = ''
    decision_steps: list[dict[str, Any]] = field(default_factory=list)
    safety_check_kind: str = ''
    permission_matcher_kind: str = 'default'
    created_at: str | None = None
    answered_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'ToolPermissionEvent':
        return cls(
            event_id=str(data.get('event_id', '')),
            request_id=str(data.get('request_id', '')),
            tool_name=str(data.get('tool_name', '')),
            permission_class=str(data.get('permission_class', 'default') or 'default'),
            behavior=str(data.get('behavior', 'deny') or 'deny'),
            granted=bool(data.get('granted', False)),
            reason=str(data.get('reason', '') or ''),
            stage=canonical_stage_id(data.get('stage', '') or ''),
            attempt=int(data.get('attempt', 0) or 0),
            query_chain_id=_read_query_chain_id(data),
            query_chain_depth=_read_query_chain_depth(data),
            continuation_index=_read_continuation_index(data),
            requested_by=str(data.get('requested_by', 'agent') or 'agent'),
            session_id=(str(data['session_id']) if data.get('session_id') is not None else None),
            resolution=(str(data['resolution']) if data.get('resolution') is not None else None),
            decision_scope=(str(data['decision_scope']) if data.get('decision_scope') is not None else None),
            base_permission_class=(
                str(data['base_permission_class']) if data.get('base_permission_class') is not None else None
            ),
            destructive_action=bool(data.get('destructive_action', False)),
            destructive_reason=str(data.get('destructive_reason', '') or ''),
            destructive_kinds=_coerce_string_tuple(data.get('destructive_kinds')),
            decision_id=str(data.get('decision_id', '') or ''),
            decision_steps=[
                dict(item) for item in data.get('decision_steps', []) if isinstance(item, dict)
            ],
            safety_check_kind=str(data.get('safety_check_kind', '') or ''),
            permission_matcher_kind=str(data.get('permission_matcher_kind', 'default') or 'default'),
            created_at=(str(data['created_at']) if data.get('created_at') is not None else None),
            answered_at=(str(data['answered_at']) if data.get('answered_at') is not None else None),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ToolCall:
    request_id: str
    tool_name: str
    input: dict[str, Any] = field(default_factory=dict)
    reason: str = ''
    expected_output: str = ''
    stage: str = ''
    attempt: int = 0
    query_chain_id: str = ''
    query_chain_depth: int = 0
    continuation_index: int = 0
    requested_by: str = 'agent'
    created_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'ToolCall':
        input_payload = data.get('input')
        return cls(
            request_id=str(data.get('request_id', '')),
            tool_name=str(data.get('tool_name', '')),
            input=dict(input_payload) if isinstance(input_payload, dict) else {},
            reason=str(data.get('reason', '')),
            expected_output=str(data.get('expected_output', '')),
            stage=canonical_stage_id(data.get('stage', '')),
            attempt=int(data.get('attempt', 0) or 0),
            query_chain_id=_read_query_chain_id(data),
            query_chain_depth=_read_query_chain_depth(data),
            continuation_index=_read_continuation_index(data),
            requested_by=str(data.get('requested_by', 'agent') or 'agent'),
            created_at=(str(data['created_at']) if data.get('created_at') is not None else None),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ToolProtocolViolationRecord:
    violation_id: str
    violation_kind: str
    message: str
    tool_name: str = ''
    request_id: str | None = None
    stage: str = ''
    attempt: int = 0
    query_chain_id: str = ''
    query_chain_depth: int = 0
    continuation_index: int = 0
    input_payload: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'ToolProtocolViolationRecord':
        payload = data.get('input_payload')
        return cls(
            violation_id=str(data.get('violation_id', '')),
            violation_kind=str(data.get('violation_kind', 'unknown') or 'unknown'),
            message=str(data.get('message', '') or ''),
            tool_name=str(data.get('tool_name', '') or ''),
            request_id=(str(data['request_id']) if data.get('request_id') is not None else None),
            stage=canonical_stage_id(data.get('stage', '') or ''),
            attempt=int(data.get('attempt', 0) or 0),
            query_chain_id=_read_query_chain_id(data),
            query_chain_depth=_read_query_chain_depth(data),
            continuation_index=_read_continuation_index(data),
            input_payload=dict(payload) if isinstance(payload, dict) else {},
            created_at=(str(data['created_at']) if data.get('created_at') is not None else None),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ToolInvocationRecord:
    request_id: str
    tool_name: str
    status: str
    granted: bool
    stage: str = ''
    attempt: int = 0
    query_chain_id: str = ''
    query_chain_depth: int = 0
    continuation_index: int = 0
    permission_session_id: str | None = None
    interaction_session_id: str | None = None
    awaiting_user_interaction: bool = False
    input_digest: str = ''
    validation_code: str | None = None
    permission_class: str | None = None
    base_permission_class: str | None = None
    destructive_action: bool = False
    destructive_reason: str = ''
    destructive_kinds: tuple[str, ...] = ()
    result_summary: str = ''
    result_ref: str | None = None
    environment_provider_kind: str | None = None
    environment_selection_reason: str = ''
    environment_receipt_id: str | None = None
    warning: str | None = None
    error: str | None = None
    duration: float | None = None
    context_effects: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None
    finished_at: str | None = None
    invocation_receipt_id: str | None = None
    permission_decision_id: str | None = None
    projection_id: str | None = None
    evidence_lane: str = 'control'
    raw_result_hash: str = ''
    output_hash: str = ''
    observable_input: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'ToolInvocationRecord':
        effects = data.get('context_effects')
        effect_map = dict(effects) if isinstance(effects, dict) else {}
        duration = data.get('duration')
        return cls(
            request_id=str(data.get('request_id', '')),
            tool_name=str(data.get('tool_name', '')),
            status=str(data.get('status', 'unknown')),
            granted=bool(data.get('granted', False)),
            stage=canonical_stage_id(data.get('stage', effect_map.get('stage', '')) or ''),
            attempt=int(data.get('attempt', effect_map.get('attempt', 0)) or 0),
            query_chain_id=_read_query_chain_id(data, effect_map),
            query_chain_depth=_read_query_chain_depth(data, effect_map),
            continuation_index=_read_continuation_index(data, effect_map),
            permission_session_id=(
                str(data['permission_session_id']) if data.get('permission_session_id') is not None else None
            ),
            interaction_session_id=(
                str(data['interaction_session_id']) if data.get('interaction_session_id') is not None else None
            ),
            awaiting_user_interaction=bool(data.get('awaiting_user_interaction', False)),
            input_digest=str(data.get('input_digest', '')),
            validation_code=(str(data['validation_code']) if data.get('validation_code') is not None else None),
            permission_class=(str(data['permission_class']) if data.get('permission_class') is not None else None),
            base_permission_class=(
                str(data['base_permission_class']) if data.get('base_permission_class') is not None else None
            ),
            destructive_action=bool(data.get('destructive_action', False)),
            destructive_reason=str(data.get('destructive_reason', '') or ''),
            destructive_kinds=_coerce_string_tuple(data.get('destructive_kinds')),
            result_summary=str(data.get('result_summary', '')),
            result_ref=(str(data['result_ref']) if data.get('result_ref') is not None else None),
            environment_provider_kind=(str(data['environment_provider_kind']) if data.get('environment_provider_kind') is not None else None),
            environment_selection_reason=str(data.get('environment_selection_reason', '') or ''),
            environment_receipt_id=(str(data['environment_receipt_id']) if data.get('environment_receipt_id') is not None else None),
            warning=(str(data['warning']) if data.get('warning') is not None else None),
            error=(str(data['error']) if data.get('error') is not None else None),
            duration=(float(duration) if duration is not None else None),
            context_effects=effect_map,
            created_at=(str(data['created_at']) if data.get('created_at') is not None else None),
            finished_at=(str(data['finished_at']) if data.get('finished_at') is not None else None),
            invocation_receipt_id=(
                str(data['invocation_receipt_id']) if data.get('invocation_receipt_id') is not None else None
            ),
            permission_decision_id=(
                str(data['permission_decision_id']) if data.get('permission_decision_id') is not None else None
            ),
            projection_id=(str(data['projection_id']) if data.get('projection_id') is not None else None),
            evidence_lane=str(data.get('evidence_lane', 'control') or 'control'),
            raw_result_hash=str(data.get('raw_result_hash', '') or ''),
            output_hash=str(data.get('output_hash', '') or ''),
            observable_input=(
                dict(data.get('observable_input', {}))
                if isinstance(data.get('observable_input'), dict)
                else {}
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PermissionStateUpdate:
    update_id: str
    source: str
    scope: dict[str, Any] = field(default_factory=dict)
    before: dict[str, Any] = field(default_factory=dict)
    after: dict[str, Any] = field(default_factory=dict)
    diff: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'PermissionStateUpdate':
        return cls(
            update_id=str(data.get('update_id', '') or ''),
            source=str(data.get('source', '') or ''),
            scope=dict(data.get('scope', {})) if isinstance(data.get('scope'), dict) else {},
            before=dict(data.get('before', {})) if isinstance(data.get('before'), dict) else {},
            after=dict(data.get('after', {})) if isinstance(data.get('after'), dict) else {},
            diff=dict(data.get('diff', {})) if isinstance(data.get('diff'), dict) else {},
            created_at=(str(data['created_at']) if data.get('created_at') is not None else None),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LLMUsageReceipt:
    receipt_id: str
    scope: dict[str, Any] = field(default_factory=dict)
    model: str = ''
    request_kind: str = ''
    estimated_rendered_input_tokens: int = 0
    provider_prompt_tokens: int | None = None
    provider_completion_tokens: int | None = None
    provider_total_tokens: int | None = None
    provider_cache_read_tokens: int | None = None
    provider_cache_write_tokens: int | None = None
    estimate_vs_provider_delta: int | None = None
    usage_source: str = 'estimated_only'
    created_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'LLMUsageReceipt':
        def _optional_int(key: str) -> int | None:
            return int(data[key]) if data.get(key) is not None else None

        return cls(
            receipt_id=str(data.get('receipt_id', '') or ''),
            scope=dict(data.get('scope', {})) if isinstance(data.get('scope'), dict) else {},
            model=str(data.get('model', '') or ''),
            request_kind=str(data.get('request_kind', '') or ''),
            estimated_rendered_input_tokens=int(data.get('estimated_rendered_input_tokens', 0) or 0),
            provider_prompt_tokens=_optional_int('provider_prompt_tokens'),
            provider_completion_tokens=_optional_int('provider_completion_tokens'),
            provider_total_tokens=_optional_int('provider_total_tokens'),
            provider_cache_read_tokens=_optional_int('provider_cache_read_tokens'),
            provider_cache_write_tokens=_optional_int('provider_cache_write_tokens'),
            estimate_vs_provider_delta=_optional_int('estimate_vs_provider_delta'),
            usage_source=str(data.get('usage_source', 'estimated_only') or 'estimated_only'),
            created_at=(str(data['created_at']) if data.get('created_at') is not None else None),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ToolObservation:
    observation_id: str
    source_type: str
    source_name: str
    stage: str
    attempt: int
    query_chain_id: str = ''
    query_chain_depth: int = 0
    continuation_index: int = 0
    summary: str = ''
    payload_ref: str | None = None
    created_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'ToolObservation':
        return cls(
            observation_id=str(data.get('observation_id', '')),
            source_type=str(data.get('source_type', 'tool_result')),
            source_name=str(data.get('source_name', '')),
            stage=canonical_stage_id(data.get('stage', '')),
            attempt=int(data.get('attempt', 0) or 0),
            query_chain_id=_read_query_chain_id(data),
            query_chain_depth=_read_query_chain_depth(data),
            continuation_index=_read_continuation_index(data),
            summary=str(data.get('summary', '')),
            payload_ref=(str(data['payload_ref']) if data.get('payload_ref') is not None else None),
            created_at=(str(data['created_at']) if data.get('created_at') is not None else None),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _coerce_string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    cleaned = [str(item).strip() for item in value if str(item).strip()]
    return tuple(dict.fromkeys(cleaned))
