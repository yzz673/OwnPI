from __future__ import annotations

from ownpi.controller.state_store import WorkflowStateStore
from ownpi.runtime.events import now_utc_iso
from ownpi.runtime.rollout.stage_business_events import emit_stage_prep
from ownpi.runtime.stage_prep.models import (
    StagePrepRecord,
    StagePrepRequest,
    StagePrepResult,
)


def build_stage_prep_record(
    *,
    status: str,
    prep_id: str,
    request: StagePrepRequest,
    state_store: WorkflowStateStore,
    attempt: int,
    created_at: str,
    failure_class: str = '',
    failure_message: str = '',
) -> StagePrepRecord:
    workflow = state_store.state.workflow_state
    return StagePrepRecord(
        prep_id=prep_id,
        status=status,  # type: ignore[arg-type]
        stage=request.stage.value,
        level=request.level.value,
        attempt=attempt,
        query_chain_id=str(workflow.current_query_chain_id or ''),
        query_chain_depth=int(workflow.current_query_chain_depth or 0),
        continuation_index=int(request.continuation_index),
        entry_origin=request.entry_context.origin,
        source_resume_id=request.entry_context.source_resume_id,
        continuation_kind=request.entry_context.continuation_kind,
        created_at=created_at,
        updated_at=now_utc_iso(),
        failure_class=failure_class,
        failure_message=failure_message[:500],
    )


def activate_stage_prep_result(
    *,
    result: StagePrepResult,
    state_store: WorkflowStateStore,
) -> None:
    if result.handoff is None:
        raise ValueError('cannot activate stage prep without a handoff')
    state = state_store.state.stage_prep_state
    state.active_handoff = result.handoff
    append_stage_prep_history_once(state.history, result.record)


def record_stage_prep_event(
    *,
    state_store: WorkflowStateStore,
    event_type: str,
    record: StagePrepRecord,
) -> None:
    if event_type in {'stage_prep_completed', 'stage_prep_failed'}:
        emit_stage_prep(
            getattr(state_store, 'run_event_store', None),
            kind=event_type,
            prep_record=record.to_dict(),
        )


def append_stage_prep_history_once(
    history: list[StagePrepRecord],
    record: StagePrepRecord,
) -> None:
    for index, item in enumerate(history):
        if item.prep_id == record.prep_id:
            history[index] = record
            return
    history.append(record)
    del history[:-64]


__all__ = [
    'activate_stage_prep_result',
    'append_stage_prep_history_once',
    'build_stage_prep_record',
    'record_stage_prep_event',
]
