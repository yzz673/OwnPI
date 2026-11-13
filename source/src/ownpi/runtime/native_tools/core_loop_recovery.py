from __future__ import annotations

from ownpi.runtime.native_tools.core_loop_models import NativeToolLoopResult
from ownpi.runtime.native_tools.core_loop_session import CoreLoopSession
from ownpi.runtime.rollout.resume_delivery import deliver_committed_results
from ownpi.runtime.tools.runtime import ToolRuntimeContext


def deliver_resumed_results(*, session: CoreLoopSession, context: ToolRuntimeContext) -> None:
    """Deliver committed rollout results without replaying tool execution."""
    if context.cancel_checker is not None and context.cancel_checker():
        _stop_recovery(session, 'user_cancelled')
        return
    if session.shared.resumed_from_rollout:
        deliver_committed_results(session, context=context)


def _stop_recovery(session: CoreLoopSession, status: str) -> None:
    session.terminal_result = NativeToolLoopResult(
        status=status, turns=0, stop_reason=status,
        tool_outcomes=tuple(session.execution.all_outcomes),
        auto_resume_safe=status != 'user_cancelled',
        recommended_recovery='wait_for_user_instruction' if status == 'user_cancelled' else 'resume',
    )
