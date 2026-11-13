"""Single critic invocation, independent of stage advancement and session ownership."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ownpi.runtime.call_continuation.critic_loop import (
    begin_core_feedback, begin_critic_round, critic_transition,
)
from ownpi.runtime.call_continuation.critic_policy import reviewer_policy
from ownpi.runtime.rollout.stage_business_events import emit_critic_loop_transition
from ownpi.runtime.stage_execution.feedback_store import (
    feedback_path, load_feedback_record, verify_feedback_terminal_receipt,
)
from ownpi.schemas.feedback import ReviewTarget


@dataclass(frozen=True)
class CriticLoopBindings:
    state_store: Any
    run_dir: Path
    stage: str
    attempt: int
    review_target: Callable[[], ReviewTarget]
    run_core: Callable[[bool, Callable[[], None] | None], list[dict[str, Any]]]
    run_reviewer: Callable[[], Any]
    saved_files: list[str]
    reviewer_operation: str = 'reviewer_review'
    completion_safe_point: str = 'critic_loop_critic_complete'


def execute_critic_loop(bindings: CriticLoopBindings) -> tuple[str, list[dict[str, Any]]]:
    store = bindings.state_store
    feedback = store.state.feedback_state
    scratchpad = store.state.workflow_state.scratchpad
    policy = reviewer_policy()
    results: list[dict[str, Any]] = []

    def emit(action: str) -> None:
        emit_critic_loop_transition(
            store.run_event_store, feedback_state=feedback, stage=bindings.stage,
            attempt=bindings.attempt,
            stage_loop_index=int(scratchpad.get('stage_loop_index', 1)), action=action,
        )

    def start_review(safe_point: str) -> None:
        begin_critic_round(
            feedback, policy=policy, stage=bindings.stage, attempt=bindings.attempt,
            review_target=bindings.review_target(),
        )
        emit('review_started')
        store.poll_runtime_control(safe_point)

    while True:
        loop = feedback.critic_loop
        if loop.phase == 'idle':
            receipt = loop.core_intent_receipt
            initial_work = receipt is None
            if initial_work:
                results.extend(bindings.run_core(
                    True, lambda: start_review('initial_core_completed'),
                ))
            else:
                start_review('critic_round_started')
            continue
        if loop.phase in {'review_requested', 'completed'}:
            route = critic_transition(feedback, policy=policy)
            if route != 'next_critic_round':
                store.poll_runtime_control(bindings.completion_safe_point)
                return route, results
            start_review('critic_round_started')
            continue
        if loop.phase == 'core_post_feedback':
            results.extend(bindings.run_core(False, None))
            continue
        if loop.phase == 'feedback_submitted':
            begin_core_feedback(feedback)
            emit('core_feedback_started')
            scratchpad.pop('continuation_index', None)
            store.poll_runtime_control('core_feedback_started')
            continue
        if loop.phase != 'critic_pending':
            raise RuntimeError(f'unsupported reviewer critic loop phase: {loop.phase}')
        path = feedback_path(
            review_target=loop.review_target.identity, run_dir=bindings.run_dir,
            source_stage=bindings.stage, attempt=bindings.attempt,
            author_role='reviewer', round_index=loop.round,
            stage_loop_index=int(scratchpad.get('stage_loop_index', 1)),
        )
        interaction = store.state.workflow_state.pending_human_clarification
        if (interaction is None or interaction.actor_kind != 'reviewer'
                or interaction.stage != bindings.stage or interaction.attempt != bindings.attempt):
            scratchpad.pop('continuation_index', None)
        output = bindings.run_reviewer()
        persisted = feedback.critic_loop
        ref = feedback.record_ref(persisted.feedback_record_id)
        if persisted.phase != 'feedback_submitted' or ref is None:
            raise RuntimeError('Reviewer feedback was not durably submitted.')
        record, text = load_feedback_record(ref, run_dir=bindings.run_dir)
        if Path(record.feedback_path) != path.resolve():
            raise RuntimeError('Reviewer feedback path does not match the active round.')
        verify_feedback_terminal_receipt(record=record, content=text, run_dir=bindings.run_dir)
        if str(path) not in bindings.saved_files:
            bindings.saved_files.append(str(path))
        results.append({
            'kind': 'critic', 'operation': bindings.reviewer_operation,
            'agent_role': 'reviewer', 'artifact_type': 'reviewer_feedback',
            'status': output.status, 'file_path': str(path), 'summary': output.summary,
            'critic_kind': 'reviewer', 'critic_round': loop.round,
            'feedback_record_id': record.record_id,
        })
