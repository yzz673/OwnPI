"""B1/B2 workflow facts and projections backed by the shared run event log."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Protocol

from ownpi.runtime.rollout.business_events import BusinessEvent
from ownpi.runtime.rollout.business_reader import read_business_events
from ownpi.runtime.rollout.run_events import run_events_path
from ownpi.runtime.stage_prep.models import StagePrepRecord
from ownpi.schemas.feedback import FeedbackRecord
from ownpi.schemas.workflow_plan import RunWorkflowPlan
from ownpi.runtime.rollout.call_source import validate_call_source


class BusinessEventStore(Protocol):
    @property
    def events(self) -> tuple[BusinessEvent, ...]: ...

    def emit(self, kind: str, event_id: str, data: dict[str, Any]) -> BusinessEvent: ...


B1_KINDS = frozenset({
    'run_initialized', 'workflow_plan_selected',
    'stage_attempt_started', 'stage_prep_completed', 'stage_prep_failed',
    'stage_gate_decided', 'revisit_transition',
    'stage_loop_advanced',
    'critic_loop_transition',
})
B2_KINDS = frozenset({'plan_approved', 'feedback_delivered', 'feedback_submitted'})


def _required_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} must be a non-empty string')
    return value


def _positive_int(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f'{field} must be a positive integer')
    return value


def _emit(store: BusinessEventStore | None, kind: str, event_id: str,
          data: dict[str, Any]) -> BusinessEvent | None:
    if store is None:
        return None
    return store.emit(kind, event_id, data)


def emit_workflow_plan(store: BusinessEventStore | None, plan: RunWorkflowPlan) -> BusinessEvent | None:
    return _emit(store, 'workflow_plan_selected', 'workflow-plan-selected', plan.to_dict())


def emit_run_initialized(store: BusinessEventStore | None, *, session_id: str,
                         task_id: str, execution_tier: str,
                         stage_runtime_profile: str) -> BusinessEvent | None:
    return _emit(store, 'run_initialized', 'run-initialized', {
        'session_id': _required_text(session_id, 'session_id'),
        'task_id': _required_text(task_id, 'task_id'),
        'execution_tier': _required_text(execution_tier, 'execution_tier'),
        'stage_runtime_profile': _required_text(stage_runtime_profile, 'stage_runtime_profile'),
    })


def emit_stage_attempt_started(store: BusinessEventStore | None, *, run_id: str,
                               stage: str, attempt: int, query_chain_id: str,
                               query_chain_depth: int) -> BusinessEvent | None:
    _required_text(stage, 'stage')
    _positive_int(attempt, 'attempt')
    if isinstance(query_chain_depth, bool) or not isinstance(query_chain_depth, int) or query_chain_depth < 0:
        raise ValueError('query_chain_depth must be a non-negative integer')
    event_id = f'stage-attempt-started:{stage}:{attempt}'
    return _emit(store, 'stage_attempt_started', event_id, {
        'stage': stage, 'attempt': int(attempt), 'query_chain_id': query_chain_id,
        'query_chain_depth': int(query_chain_depth),
    })


def emit_stage_loop_advanced(store: BusinessEventStore | None, *, stage: str,
                             attempt: int, loop_index: int) -> BusinessEvent | None:
    _required_text(stage, 'stage')
    _positive_int(attempt, 'attempt')
    _positive_int(loop_index, 'loop_index')
    if loop_index < 2:
        raise ValueError('advanced stage loop index must be at least 2')
    return _emit(store, 'stage_loop_advanced',
                 f'stage-loop-advanced:{stage}:{attempt}:{loop_index}', {
                     'stage': stage, 'attempt': attempt, 'loop_index': loop_index,
                 })


def emit_critic_loop_transition(store: BusinessEventStore | None, *,
                                feedback_state: Any, stage: str, attempt: int,
                                stage_loop_index: int, action: str,
                                source: dict[str, str] | None = None) -> BusinessEvent:
    _required_text(stage, 'stage')
    _positive_int(attempt, 'attempt')
    _positive_int(stage_loop_index, 'stage_loop_index')
    critic = feedback_state.critic_loop
    if store is None:
        raise OSError('Critic persistence requires a run event writer')
    scope = f'{stage}:{attempt}:{stage_loop_index}'
    if action == 'core_intent_accepted':
        receipt = critic.core_intent_receipt
        if receipt is None or receipt.source_stage != stage or receipt.attempt != attempt:
            raise ValueError('Critic intent does not match the active attempt')
        data = {'action': action, 'stage': stage, 'attempt': attempt,
                'stage_loop_index': stage_loop_index, 'round': receipt.round,
                'source': validate_call_source(source)}
        if receipt.feedback_record_id:
            data['feedback_record_id'] = receipt.feedback_record_id
        identity = f'critic-intent:{scope}:{receipt.tool_call_id}'
    elif action == 'review_started':
        target = critic.review_target
        if target is None or target.stage != stage or target.attempt != attempt:
            raise ValueError('Reviewer start requires its review target')
        accepted = f'critic-intent:{scope}:{target.request_id}'
        _require_event(store, accepted)
        from ownpi.schemas.feedback import DirectReviewTarget

        data = {'action': action, 'accepted_event_id': accepted}
        if isinstance(target, DirectReviewTarget):
            data['target_kind'] = 'direct'
        else:
            data['contract_sha256'] = target.contract_sha256
        identity = review_event_id(stage, attempt, stage_loop_index, critic.round)
    elif action == 'core_feedback_started':
        record_id = _required_text(critic.feedback_record_id, 'feedback_record_id')
        _require_event(store, f'feedback-submitted:{record_id}')
        data = {'action': action, 'feedback_record_id': record_id}
        identity = f'core-feedback:{record_id}'
    else:
        raise ValueError(f'Unsupported critic action: {action}')
    return store.emit('critic_loop_transition', identity, data)


def review_event_id(stage: str, attempt: int, loop_index: int, round_index: int) -> str:
    return f'review-started:{stage}:{attempt}:{loop_index}:{round_index}'


def _require_event(store: BusinessEventStore, event_id: str) -> None:
    if not any(event.event_id == event_id for event in store.events):
        raise ValueError(f'Business source event is not committed: {event_id}')


def emit_feedback_submitted(store: BusinessEventStore | None, *, record: FeedbackRecord,
                            source: dict[str, str], run_dir: Path) -> BusinessEvent:
    if store is None:
        raise OSError('Feedback persistence requires a run event writer')
    path = Path(record.feedback_path).resolve()
    if sha256(path.read_bytes()).hexdigest() != record.content_hash:
        raise ValueError('Canonical feedback differs from its submitted version')
    data = {'record_id': record.record_id, 'source': validate_call_source(source),
            'path': str(path.relative_to(run_dir.resolve())), 'content_hash': record.content_hash}
    if record.author_role == 'reviewer':
        review = review_event_id(record.source_stage, record.attempt,
                                 record.stage_loop_index, record.round)
        _require_event(store, review)
        data['review_event_id'] = review
    else:
        data.update(author_role=record.author_role, stage=record.source_stage,
                    attempt=record.attempt, stage_loop_index=record.stage_loop_index,
                    review_target=record.review_target)
    return store.emit('feedback_submitted', f'feedback-submitted:{record.record_id}', data)


def emit_stage_prep(store: BusinessEventStore | None, *, kind: str,
                    prep_record: dict[str, Any], reused: bool = False) -> BusinessEvent | None:
    if kind not in {'stage_prep_completed', 'stage_prep_failed'}:
        raise ValueError(f'unsupported stage prep event: {kind}')
    prep_id = str(prep_record.get('prep_id', '')).strip()
    if not prep_id:
        raise ValueError('stage prep event requires prep_id')
    expected_status = 'completed' if kind == 'stage_prep_completed' else 'failed'
    if prep_record.get('status') != expected_status:
        raise ValueError(f'{kind} requires record status {expected_status!r}')
    _required_text(str(prep_record.get('stage', '')), 'record.stage')
    _positive_int(prep_record.get('attempt'), 'record.attempt')
    return _emit(store, kind, f'{kind}:{prep_id}', {
        'record': dict(prep_record), 'reused': bool(reused),
    })


def emit_stage_gate(store: BusinessEventStore | None, *, stage: str, attempt: int,
                    stage_run_id: str, gate: dict[str, Any]) -> BusinessEvent | None:
    _required_text(stage, 'stage')
    _positive_int(attempt, 'attempt')
    _required_text(stage_run_id, 'stage_run_id')
    if not isinstance(gate, dict):
        raise ValueError('gate must be an object')
    return _emit(store, 'stage_gate_decided', f'stage-gate:{stage_run_id}', {
        'stage': stage, 'attempt': int(attempt), 'stage_run_id': stage_run_id,
        'gate': dict(gate),
    })


def emit_revisit(store: BusinessEventStore | None, *, contract_id: str,
                 payload: dict[str, Any]) -> BusinessEvent | None:
    _required_text(contract_id, 'contract_id')
    if not isinstance(payload, dict):
        raise ValueError('revisit payload must be an object')
    payload_contract_id = payload.get('contract_id')
    if payload_contract_id is not None and payload_contract_id != contract_id:
        raise ValueError('revisit payload contract_id does not match event identity')
    return _emit(store, 'revisit_transition', f'revisit:{contract_id}', dict(payload))


def emit_plan_approved(store: BusinessEventStore | None, *, entry_id: str,
                      stage: str, attempt: int,
                      plan_path: str, content_sha256: str, revision: int) -> BusinessEvent | None:
    _required_text(entry_id, 'entry_id')
    _required_text(stage, 'stage')
    _positive_int(attempt, 'attempt')
    _required_text(plan_path, 'plan_path')
    if not isinstance(content_sha256, str) or len(content_sha256) != 64:
        raise ValueError('content_sha256 must be a SHA-256 hex digest')
    _positive_int(revision, 'revision')
    return _emit(store, 'plan_approved', f'plan-approved:{entry_id}:{revision}', {
        'entry_id': entry_id, 'stage': stage, 'attempt': int(attempt),
        'plan_path': plan_path,
        'content_sha256': content_sha256, 'revision': int(revision),
    })


def emit_feedback_delivered(store: BusinessEventStore | None, *, record_id: str,
                            target_stage: str, target_attempt: int) -> BusinessEvent:
    _required_text(record_id, 'record_id')
    _required_text(target_stage, 'target_stage')
    _positive_int(target_attempt, 'target_attempt')
    if store is None:
        raise OSError('Feedback delivery requires a run event writer')
    _require_event(store, f'feedback-submitted:{record_id}')
    payload: dict[str, Any] = {
        'record_id': record_id, 'target_stage': target_stage,
        'target_attempt': int(target_attempt),
    }
    return store.emit('feedback_delivered',
                 f'feedback-delivered:{record_id}:{target_stage}:{target_attempt}', payload)


@dataclass(frozen=True)
class StageBusinessProjection:
    events: tuple[BusinessEvent, ...]

    @property
    def run_identity(self) -> dict[str, str] | None:
        initialized = [event for event in self.events if event.kind == 'run_initialized']
        if not initialized:
            return None
        data = initialized[0].data.to_object()
        if any(event.data.to_object() != data for event in initialized[1:]):
            raise ValueError('Conflicting run initialization facts')
        required = ('session_id', 'task_id', 'execution_tier', 'stage_runtime_profile')
        if any(not isinstance(data.get(key), str) or not data[key] for key in required):
            raise ValueError('Run initialization fact lacks identity or runtime profile')
        return {key: data[key] for key in required}

    @property
    def workflow_plan(self) -> RunWorkflowPlan | None:
        plans = [event for event in self.events if event.kind == 'workflow_plan_selected']
        if not plans:
            return None
        plan = RunWorkflowPlan.from_dict(plans[0].data.to_object())
        if any(RunWorkflowPlan.from_dict(event.data.to_object()) != plan for event in plans[1:]):
            raise ValueError('Conflicting workflow plan facts')
        return plan

    @property
    def stage_prep(self) -> tuple[BusinessEvent, ...]:
        return tuple(event for event in self.events
                     if event.kind in {'stage_prep_completed', 'stage_prep_failed'})

    @property
    def gates(self) -> tuple[BusinessEvent, ...]:
        return tuple(event for event in self.events if event.kind == 'stage_gate_decided')

    @property
    def revisits(self) -> tuple[BusinessEvent, ...]:
        return tuple(event for event in self.events if event.kind == 'revisit_transition')

    @property
    def plans(self) -> tuple[BusinessEvent, ...]:
        return tuple(event for event in self.events if event.kind == 'plan_approved')

    @property
    def feedback(self) -> tuple[BusinessEvent, ...]:
        return tuple(event for event in self.events if event.kind == 'feedback_delivered')

    @property
    def critic_transitions(self) -> tuple[BusinessEvent, ...]:
        return tuple(event for event in self.events if event.kind == 'critic_loop_transition')

    def latest_stage_attempt(self, *, stage: str, attempt: int | None = None) -> BusinessEvent | None:
        matches = [
            event for event in self.events
            if event.kind == 'stage_attempt_started'
            and event.data.to_object().get('stage') == stage
            and (attempt is None or int(event.data.to_object().get('attempt', 0) or 0) == attempt)
        ]
        return matches[-1] if matches else None

    def latest_gate(self, *, stage_run_id: str | None = None) -> BusinessEvent | None:
        matches = [
            event for event in self.gates
            if stage_run_id is None
            or event.data.to_object().get('stage_run_id') == stage_run_id
        ]
        return matches[-1] if matches else None

    def latest_revisit(self, *, contract_id: str | None = None) -> BusinessEvent | None:
        matches = [
            event for event in self.revisits
            if contract_id is None
            or event.data.to_object().get('contract_id') == contract_id
        ]
        return matches[-1] if matches else None


def project_stage_business_events(events: Iterable[BusinessEvent]) -> StageBusinessProjection:
    selected: list[BusinessEvent] = []
    seen: dict[str, tuple[str, dict[str, Any]]] = {}
    for event in events:
        if event.kind not in B1_KINDS | B2_KINDS:
            continue
        identity = (event.kind, event.data.to_object())
        previous = seen.get(event.event_id)
        if previous is not None:
            if previous != identity:
                raise ValueError(f'Conflicting stage business event: {event.event_id}')
            continue
        seen[event.event_id] = identity
        selected.append(event)
    return StageBusinessProjection(tuple(selected))


def read_stage_business_events(
    run_dir: Path,
    *,
    run_id: str | None = None,
) -> StageBusinessProjection:
    """Read B1/B2 facts from the shared run event log only."""
    result = read_business_events(run_events_path(run_dir), run_id=run_id)
    return project_stage_business_events(result.events)


def latest_stage_prep_record(
    run_dir: Path,
    *,
    run_id: str | None = None,
    prep_id: str,
) -> StagePrepRecord | None:
    matches = [
        event for event in read_stage_business_events(run_dir, run_id=run_id).stage_prep
        if event.data.to_object().get('record', {}).get('prep_id') == prep_id
    ]
    if not matches:
        return None
    payload = matches[-1].data.to_object()
    record = payload.get('record')
    if not isinstance(record, dict):
        raise ValueError(f'stage prep event has no record payload: {prep_id}')
    return StagePrepRecord.from_dict(record)


def latest_stage_prep_record_for_scope(
    run_dir: Path,
    *,
    run_id: str | None = None,
    stage: str,
    attempt: int,
) -> StagePrepRecord | None:
    matches = [
        event for event in read_stage_business_events(run_dir, run_id=run_id).stage_prep
        if isinstance(event.data.to_object().get('record'), dict)
        and event.data.to_object()['record'].get('stage') == stage
        and int(event.data.to_object()['record'].get('attempt', 0) or 0) == attempt
    ]
    if not matches:
        return None
    record = matches[-1].data.to_object().get('record')
    if not isinstance(record, dict):
        raise ValueError(f'stage prep event has no record payload: {stage}/attempt-{attempt}')
    return StagePrepRecord.from_dict(record)


def latest_plan_approval(
    run_dir: Path,
    *,
    run_id: str | None = None,
    entry_id: str,
) -> dict[str, Any] | None:
    matches = [
        event for event in read_stage_business_events(run_dir, run_id=run_id).plans
        if event.data.to_object().get('entry_id') == entry_id
    ]
    return matches[-1].data.to_object() if matches else None


def feedback_delivery(
    run_dir: Path,
    *,
    run_id: str | None = None,
    record_id: str,
) -> dict[str, Any] | None:
    matches = [
        event for event in read_stage_business_events(run_dir, run_id=run_id).feedback
        if event.data.to_object().get('record_id') == record_id
    ]
    return matches[-1].data.to_object() if matches else None


__all__ = [
    'B1_KINDS', 'B2_KINDS', 'StageBusinessProjection',
    'emit_critic_loop_transition', 'emit_feedback_delivered', 'emit_plan_approved', 'emit_revisit',
    'emit_stage_attempt_started', 'emit_stage_gate', 'emit_stage_prep',
    'project_stage_business_events', 'read_stage_business_events',
    'latest_stage_prep_record', 'latest_plan_approval', 'feedback_delivery',
    'latest_stage_prep_record_for_scope',
]
