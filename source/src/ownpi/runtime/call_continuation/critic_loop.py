from __future__ import annotations

from dataclasses import replace

from ownpi.runtime.call_continuation.critic_policy import CriticLoopPolicy
from ownpi.runtime.state_models import (
    AnalysisTransitionState,
    CriticLoopState,
    FeedbackHandoffState,
    FeedbackState,
)
from ownpi.schemas.feedback import (
    ReviewTarget,
    CoreFeedbackIntentReceipt,
    FeedbackRecord,
    FeedbackRecordRef,
    NextStageReceipt,
)


def begin_critic_round(
    feedback_state: FeedbackState,
    *,
    policy: CriticLoopPolicy,
    stage: str,
    attempt: int,
    review_target: ReviewTarget,
) -> CriticLoopState:
    if review_target.stage != stage or review_target.attempt != attempt:
        raise ValueError('critic target does not match the active stage attempt')
    current = feedback_state.critic_loop
    if current.phase == 'idle':
        current = CriticLoopState(
            critic_role=policy.critic_kind,
            source_stage=stage,
            attempt=attempt,
        )
    if (
        current.critic_role != policy.critic_kind
        or current.source_stage != stage
        or current.attempt != attempt
        or (
            current.review_target is not None
            and (
                type(current.review_target) is not type(review_target)
                or current.review_target.run_id != review_target.run_id
                or current.review_target.loop_index != review_target.loop_index
            )
        )
    ):
        raise ValueError('cannot replace an active critic loop with a different identity')
    if current.phase == 'critic_pending':
        if current.review_target != review_target:
            raise ValueError('pending critic round target changed')
        return current
    if current.phase not in {'idle', 'review_requested'}:
        raise ValueError(f'cannot begin critic round from phase: {current.phase}')
    next_state = replace(
        current,
        phase='critic_pending',
        round=current.round + 1,
        review_target=review_target,
        feedback_record_id=None,
        core_intent_receipt=None,
        completion_reason=None,
    )
    feedback_state.active_handoff = FeedbackHandoffState()
    feedback_state.critic_loop = next_state
    return next_state


def record_critic_feedback(
    feedback_state: FeedbackState,
    *,
    record: FeedbackRecord,
    record_ref: FeedbackRecordRef,
) -> CriticLoopState:
    current = feedback_state.critic_loop
    expected_target = (
        current.review_target.identity
        if current.review_target is not None
        else ''
    )
    existing_ref = feedback_state.record_ref(record.record_id)
    if (
        current.critic_role != record.author_role
        or current.source_stage != record.source_stage
        or current.attempt != record.attempt
        or current.round != record.round
        or current.review_target is None
        or current.review_target.loop_index != record.stage_loop_index
        or expected_target != record.review_target
    ):
        raise ValueError('feedback record does not match the pending critic round')
    if (
        current.phase == 'feedback_submitted'
        and current.feedback_record_id == record.record_id
        and existing_ref == record_ref
    ):
        return current
    if current.phase != 'critic_pending':
        raise ValueError('feedback record does not match the pending critic round')
    if existing_ref is not None and existing_ref != record_ref:
        raise ValueError('feedback record id already has a different metadata ref')
    if existing_ref is None:
        feedback_state.record_refs.append(record_ref)
    next_state = replace(
        current,
        phase='feedback_submitted',
        feedback_record_id=record.record_id,
    )
    feedback_state.critic_loop = next_state
    return next_state


def begin_core_feedback(feedback_state: FeedbackState) -> CriticLoopState:
    current = feedback_state.critic_loop
    if current.phase == 'core_post_feedback':
        return current
    if current.phase != 'feedback_submitted' or not current.feedback_record_id:
        raise ValueError('Core feedback handling requires submitted feedback')
    feedback_state.active_handoff = FeedbackHandoffState(
        record_id=current.feedback_record_id,
        consumer_stage=current.source_stage,
        consumer_attempt=current.attempt,
        status='pending',
    )
    next_state = replace(current, phase='core_post_feedback')
    feedback_state.critic_loop = next_state
    return next_state


def record_core_feedback_intent(
    feedback_state: FeedbackState,
    *,
    receipt: CoreFeedbackIntentReceipt,
) -> CriticLoopState:
    current = feedback_state.critic_loop
    existing = current.core_intent_receipt
    if existing is not None:
        if existing == receipt:
            return current
        raise ValueError('a different Core feedback intent is already committed')
    if current.phase == 'idle':
        if receipt.intent != 'request_review' or receipt.round != 0 or receipt.feedback_record_id:
            raise ValueError('initial Core intent must request review without feedback')
        next_state = replace(current, core_intent_receipt=receipt)
        feedback_state.critic_loop = next_state
        return next_state
    if (
        current.phase != 'core_post_feedback'
        or current.critic_role != receipt.critic_role
        or current.source_stage != receipt.source_stage
        or current.attempt != receipt.attempt
        or current.round != receipt.round
        or current.feedback_record_id != receipt.feedback_record_id
    ):
        raise ValueError('Core feedback intent does not match the active critic round')
    next_phase = (
        'review_requested'
        if receipt.intent == 'request_review'
        else 'completed'
    )
    next_state = replace(
        current,
        phase=next_phase,
        core_intent_receipt=receipt,
        completion_reason=(
            'core_completed_feedback'
            if receipt.intent == 'complete_feedback'
            else None
        ),
    )
    feedback_state.critic_loop = next_state
    return next_state


def critic_transition(feedback_state: FeedbackState, *, policy: CriticLoopPolicy) -> str:
    current = feedback_state.critic_loop
    if current.critic_role != policy.critic_kind:
        raise ValueError('critic transition does not match the active policy')
    if current.phase == 'review_requested':
        return 'next_critic_round'
    if current.phase == 'completed':
        return policy.clean_transition
    raise ValueError(f'critic loop is not terminal: {current.phase}')


def record_analysis_feedback(
    feedback_state: FeedbackState,
    *,
    record: FeedbackRecord,
    record_ref: FeedbackRecordRef,
) -> AnalysisTransitionState:
    if record.author_role != 'analysis' or record.round != 1:
        raise ValueError('Analysis feedback must use author_role=analysis and round=1')
    transition = feedback_state.analysis_transition
    if transition.attempt not in {0, record.attempt}:
        raise ValueError('Analysis feedback belongs to a different attempt')
    if transition.feedback_record_id is not None:
        existing_ref = feedback_state.record_ref(transition.feedback_record_id)
        if transition.feedback_record_id == record.record_id and existing_ref == record_ref:
            return transition
        raise ValueError('a different Analysis feedback record is already committed')
    existing_ref = feedback_state.record_ref(record.record_id)
    if existing_ref is not None and existing_ref != record_ref:
        raise ValueError('Analysis feedback record id has a conflicting ref')
    if existing_ref is None:
        feedback_state.record_refs.append(record_ref)
    next_state = AnalysisTransitionState(
        source_stage=record.source_stage,
        attempt=record.attempt,
        feedback_record_id=record.record_id,
    )
    feedback_state.analysis_transition = next_state
    return next_state


def record_next_stage_receipt(
    feedback_state: FeedbackState,
    *,
    receipt: NextStageReceipt,
) -> AnalysisTransitionState:
    current = feedback_state.analysis_transition
    if not current.feedback_record_id:
        raise ValueError('submit_feedback() must succeed before next_stage()')
    if current.next_stage_receipt is not None:
        if current.next_stage_receipt == receipt:
            return current
        raise ValueError('a different next-stage receipt is already committed')
    if (
        current.source_stage != receipt.source_stage
        or current.attempt != receipt.attempt
        or current.feedback_record_id != receipt.feedback_record_id
    ):
        raise ValueError('next-stage receipt does not match the Analysis feedback')
    next_state = replace(current, next_stage_receipt=receipt)
    feedback_state.analysis_transition = next_state
    return next_state


def reset_critic_loop(feedback_state: FeedbackState) -> None:
    feedback_state.critic_loop = CriticLoopState()


__all__ = [
    'begin_core_feedback',
    'begin_critic_round',
    'critic_transition',
    'record_analysis_feedback',
    'record_core_feedback_intent',
    'record_critic_feedback',
    'record_next_stage_receipt',
    'reset_critic_loop',
]
