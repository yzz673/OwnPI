"""Common control boundaries, independent of a loop's business completion rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from ownpi.llm.provider_retry import RetryCancelled

if TYPE_CHECKING:
    from ownpi.schemas.runtime_human_clarification import HumanClarificationSession
    from ownpi.runtime.native_tools.core_loop_models import NativeToolLoopResult
    from ownpi.runtime.native_tools.executor import NativeToolExecutionOutcome
    from ownpi.runtime.native_tools.loop_engine.contracts import NativeLoopResult


@dataclass(frozen=True)
class LoopOutcome:
    kind: Literal['business', 'waiting', 'cancelled', 'failed']
    reason: str


def classify_loop_outcome(status: str, reason: str = '', failure_class: str = '') -> LoopOutcome:
    """Classify execution, not business success; callers retain the original result."""
    if 'user_cancelled' in (status, reason, failure_class):
        return LoopOutcome('cancelled', reason or status)
    if status in {'failed', 'provider_or_protocol_failure', 'tool_result_projection_failure', 'local_runtime_failure'}:
        return LoopOutcome('failed', reason or failure_class or status)
    if status in {'awaiting_user_interaction', 'human_input_pending'} or reason in {
        'awaiting_user_interaction', 'human_input_pending',
    }:
        return LoopOutcome('waiting', reason or status)
    return LoopOutcome('business', reason or status)


def kernel_outcome(result: NativeLoopResult) -> LoopOutcome:
    return classify_loop_outcome(result.status, result.terminal_reason, result.failure_class)


def core_outcome(result: NativeToolLoopResult) -> LoopOutcome:
    return classify_loop_outcome(result.status, result.stop_reason, result.failure_class)


def is_loop_cancellation(error: BaseException) -> bool:
    return isinstance(error, RetryCancelled)


def pending_interaction(session: HumanClarificationSession | None) -> HumanClarificationSession | None:
    return session if session is not None and session.status == 'pending' else None


def pending_tool_outcome(
    outcomes: tuple[NativeToolExecutionOutcome, ...],
) -> NativeToolExecutionOutcome | None:
    for outcome in outcomes:
        invocation = outcome.invocation
        if invocation is not None:
            if pending_interaction(invocation.pending_interaction_session) is not None:
                return outcome
    return None


def tool_execution_cancelled(outcomes: tuple[NativeToolExecutionOutcome, ...]) -> bool:
    return any(outcome.native_error is not None
               and outcome.native_error.failure_class == 'tool_execution_cancelled'
               for outcome in outcomes)
