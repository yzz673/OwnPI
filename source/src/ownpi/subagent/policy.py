"""Domain policy for the subagent native tool loop."""

from __future__ import annotations

from dataclasses import dataclass

from ownpi.runtime.native_tools.loop_engine.contracts import (
    NativeLoopDirective,
    NativeLoopSnapshot,
    NativeLoopTurnPlan,
)
from ownpi.runtime.native_tools.tool_invocation import build_tool_invocations
from ownpi.runtime.native_tools.spec import NativeToolSpec
from ownpi.runtime.native_tools.loop_outcome import is_loop_cancellation, pending_tool_outcome, tool_execution_cancelled
from ownpi.runtime.native_tools.executor import NativeToolExecutionOutcome


@dataclass
class SubagentLoopPolicy:
    """Keep looping until the completion tool has succeeded."""

    tool_specs: tuple[NativeToolSpec, ...]
    completion_tool_name: str = "complete_subagent"

    def tool_choice(self, *, turn_index: int) -> str:
        del turn_index
        return "auto"

    def plan_turn(self, snapshot: NativeLoopSnapshot) -> NativeLoopTurnPlan:
        calls = build_tool_invocations(snapshot.response)
        if calls:
            return NativeLoopTurnPlan(calls=calls)
        return NativeLoopTurnPlan(
            directive=NativeLoopDirective.complete(reason="provider_finished_without_tools")
        )

    def after_results(self, snapshot: NativeLoopSnapshot, results: tuple[object, ...]) -> NativeLoopDirective:
        del snapshot
        # The production executor returns invocation outcomes; plain tool results
        # used by callable executors have no interaction boundary.
        outcomes = tuple(
            result for result in results
            if isinstance(result, NativeToolExecutionOutcome)
        )
        if pending_tool_outcome(outcomes) is not None:
            return NativeLoopDirective.complete(reason='awaiting_user_interaction')
        if tool_execution_cancelled(outcomes):
            return NativeLoopDirective.complete(reason='user_cancelled')
        if any(
            outcome.ok and outcome.call.tool_name == self.completion_tool_name
            for outcome in outcomes
        ):
            return NativeLoopDirective.complete(reason="subagent_completion_tool_succeeded")
        if any(not outcome.ok for outcome in outcomes):
            # Tool failures are feedback for the child model. They do not establish
            # an external terminal condition and therefore must not be bounded here.
            return NativeLoopDirective.continue_with(reason="tool_failure_feedback")
        return NativeLoopDirective.continue_with(reason="completion_tool_not_called")

    def on_provider_error(self, error: BaseException, *, turn_index: int, tool_call_count: int) -> NativeLoopDirective:
        del turn_index, tool_call_count
        if is_loop_cancellation(error):
            return NativeLoopDirective.complete(reason='user_cancelled')
        return NativeLoopDirective.fail(
            failure_class="subagent_provider_failure", message=str(error)
        )
