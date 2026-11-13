from __future__ import annotations

from dataclasses import dataclass

from ownpi.runtime.native_tools.loop_outcome import is_loop_cancellation, pending_tool_outcome, tool_execution_cancelled
from ownpi.runtime.native_tools.core_loop_models import NativeToolLoopResult
from ownpi.runtime.native_tools.core_loop_session import CoreLoopSession
from ownpi.runtime.native_tools.loop_engine.contracts import (
    NativeLoopDirective,
    NativeLoopSnapshot,
    NativeLoopTurnPlan,
    NativeLoopToolValue,
)
from ownpi.runtime.native_tools.loop_engine.provider_recovery import provider_failure_details
from ownpi.runtime.native_tools.terminal_tools import (
    native_terminal_tools,
    terminal_tools_for_names,
)
from ownpi.runtime.tools.runtime import ToolRuntimeContext


@dataclass
class CoreLoopPolicy:
    session: CoreLoopSession
    context: ToolRuntimeContext

    def tool_choice(self, *, turn_index: int) -> str:
        del turn_index
        return "auto"

    def plan_turn(self, snapshot: NativeLoopSnapshot) -> NativeLoopTurnPlan:
        session = self.session
        turn_index = snapshot.turn_index
        response = snapshot.response
        plan = session.provider_decision.plan_turn(snapshot)
        if plan is not None:
            return plan
        session.terminal_result = NativeToolLoopResult(
            status="stopped_without_tool_calls",
            turns=turn_index,
            final_assistant_text=response.assistant_text,
            tool_outcomes=tuple(session.execution.all_outcomes),
            stop_reason="stopped_without_tool_calls",
        )
        return self._complete("stopped_without_tool_calls")

    def after_results(
        self,
        snapshot: NativeLoopSnapshot,
        results: tuple[NativeLoopToolValue, ...],
    ) -> NativeLoopDirective:
        del results
        session = self.session
        outcomes = session.execution.turn_outcomes
        if pending_tool_outcome(outcomes) is not None:
            boundary = 'awaiting_user_interaction'
            session.terminal_result = NativeToolLoopResult(
                status=boundary, turns=snapshot.turn_index,
                tool_outcomes=tuple(session.execution.all_outcomes), stop_reason=boundary,
                auto_resume_safe=True, recommended_recovery='resume',
            )
            return NativeLoopDirective.complete(reason=boundary)
        if (callable(self.context.cancel_checker) and self.context.cancel_checker()) or tool_execution_cancelled(outcomes):
            session.terminal_result = NativeToolLoopResult(
                status='user_cancelled', turns=snapshot.turn_index,
                tool_outcomes=tuple(session.execution.all_outcomes), stop_reason='user_cancelled',
                recommended_recovery='wait_for_user_instruction',
            )
            return NativeLoopDirective.complete(reason='user_cancelled')
        successful_control_names = {
            outcome.call.tool_name
            for outcome in outcomes
            if outcome.ok
        }
        for control in native_terminal_tools():
            if control.name in successful_control_names:
                self.context.state_store.poll_runtime_control(control.safe_point)
        for terminal in terminal_tools_for_names(session.domain_policy.terminal_tool_names):
            if terminal.name not in successful_control_names:
                continue
            session.terminal_result = NativeToolLoopResult(
                status=terminal.result_status,
                turns=snapshot.turn_index,
                tool_outcomes=tuple(session.execution.all_outcomes),
                stop_reason=terminal.result_status,
            )
            return NativeLoopDirective.complete(reason=terminal.result_status)
        return NativeLoopDirective.continue_with()

    def on_provider_error(
        self,
        error: BaseException,
        *,
        turn_index: int,
        tool_call_count: int,
    ) -> NativeLoopDirective:
        del tool_call_count
        if is_loop_cancellation(error):
            self.session.terminal_result = NativeToolLoopResult(
                status="user_cancelled",
                turns=turn_index,
                tool_outcomes=tuple(self.session.execution.all_outcomes),
                stop_reason="user_cancelled",
                failure_class="user_cancelled",
                failure_diagnostics={"message": str(error)[:400]},
                auto_resume_safe=False,
                recommended_recovery="wait_for_user_instruction",
            )
            return NativeLoopDirective.fail(
                failure_class="user_cancelled",
                message=str(error),
            )
        failure = provider_failure_details(error)
        self.session.terminal_result = NativeToolLoopResult(
            status=("local_runtime_failure" if failure["failure_class"] == "local_runtime_failure"
                    else "provider_or_protocol_failure"),
            turns=turn_index,
            tool_outcomes=tuple(self.session.execution.all_outcomes),
            failure_class=str(failure["failure_class"]),
            failure_diagnostics=dict(failure["failure_diagnostics"]),
            auto_resume_safe=bool(failure["auto_resume_safe"]),
            recommended_recovery=str(failure["recommended_recovery"]),
        )
        return NativeLoopDirective.fail(
            failure_class=str(failure["failure_class"]),
            message=str(error),
        )

    @staticmethod
    def _complete(reason: str) -> NativeLoopTurnPlan:
        return NativeLoopTurnPlan(directive=NativeLoopDirective.complete(reason=reason))
