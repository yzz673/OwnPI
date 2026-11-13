from __future__ import annotations

from pathlib import Path

from ownpi.runtime.state_models import FeedbackHandoffState, FeedbackState
from ownpi.runtime.stage_execution.feedback_store import load_feedback_record


def render_feedback_envelope(state: FeedbackState, *, record_id: str, run_dir: Path | None = None) -> str:
    ref = state.record_ref(record_id)
    if ref is None:
        raise ValueError('feedback handoff references an unknown record')
    record, content = load_feedback_record(ref, run_dir=run_dir)
    return '\n'.join(
        (
            f'Feedback Type: {record.author_role.upper()}',
            f'Source Stage: {record.source_stage}',
            f'Attempt: {record.attempt}',
            f'Round: {record.round}',
            f'Target: {record.review_target}',
            'Payload:',
            content,
        )
    )


def mark_feedback_handoff_consumed(
    state: FeedbackState,
    *,
    record_id: str,
    consumer_stage: str,
    consumer_attempt: int,
    consumed_at: str,
) -> FeedbackHandoffState:
    handoff = state.active_handoff
    if (
        handoff.record_id != record_id
        or handoff.consumer_stage != consumer_stage
        or handoff.consumer_attempt != consumer_attempt
    ):
        raise ValueError('feedback handoff consumer identity mismatch')
    if handoff.status == 'consumed':
        if handoff.consumed_at != consumed_at:
            raise ValueError('feedback handoff is already consumed')
        return handoff
    if handoff.status != 'pending':
        raise ValueError('feedback handoff is not pending')
    consumed = FeedbackHandoffState(
        record_id=record_id,
        consumer_stage=consumer_stage,
        consumer_attempt=consumer_attempt,
        status='consumed',
        consumed_at=consumed_at,
    )
    state.active_handoff = consumed
    return consumed


__all__ = ['mark_feedback_handoff_consumed', 'render_feedback_envelope']
