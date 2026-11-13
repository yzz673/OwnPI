"""Run facts, distinct from provider items and model-visible history."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ownpi.runtime.native_tools.provider_json import FrozenProviderJson


BUSINESS_EVENT_TYPES = frozenset({
    'execution_registered', 'execution_binding_updated', 'execution_state_confirmed',
    'execution_readback_recorded',
    'interaction_requested', 'interaction_answer_accepted', 'interaction_closed',
    'delivery_recorded', 'child_admitted', 'child_completion_committed', 'hook_context_registered',
    'stage_attempt_started', 'stage_prep_completed', 'stage_prep_failed',
    'stage_gate_decided', 'stage_loop_advanced', 'critic_loop_transition',
    'revisit_transition', 'plan_approved', 'runtime_control_consumed',
    'feedback_delivered', 'run_initialized', 'run_finalized', 'workflow_plan_selected',
    'feedback_submitted', 'child_execution_started', 'child_execution_finished',
    'child_worker_reclaimed', 'child_resources_released', 'process_owner_bound', 'child_resource_bound',
    'child_resource_locator_observed',
    'direct_request_started', 'direct_request_completed', 'direct_input_delivered',
    'direct_session_bound', 'direct_request_failed',
    'answer_protocol_enabled', 'answer_published',
})


@dataclass(frozen=True)
class BusinessEvent:
    kind: str
    event_id: str
    run_id: str
    data: FrozenProviderJson
    timestamp: str

    def __post_init__(self) -> None:
        if self.kind not in BUSINESS_EVENT_TYPES:
            raise ValueError(f'Unsupported business event: {self.kind}')
        if not self.event_id.strip() or not self.run_id.strip():
            raise ValueError('Business event requires event and run identities')

    def payload(self) -> dict[str, Any]:
        return {'type': self.kind, 'event_id': self.event_id, 'run_id': self.run_id,
                'schema_version': 1, 'data': self.data.to_object()}

    @classmethod
    def from_payload(cls, payload: dict[str, Any], timestamp: str) -> BusinessEvent:
        if payload.get('schema_version') != 1 or not isinstance(payload.get('data'), dict):
            raise ValueError('Invalid business event schema')
        for name in ('type', 'event_id', 'run_id'):
            if not isinstance(payload.get(name), str):
                raise ValueError(f'Invalid business event {name}')
        return cls(payload['type'], payload['event_id'], payload['run_id'],
                   FrozenProviderJson.from_value(payload['data']), timestamp)
