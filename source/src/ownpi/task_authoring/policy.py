from __future__ import annotations

from dataclasses import dataclass

from ownpi.runtime.native_tools.loop_outcome import is_loop_cancellation
from ownpi.runtime.native_tools.loop_engine.contracts import (
    NativeLoopDirective,
    NativeLoopSnapshot,
    NativeLoopTurnPlan,
)
from ownpi.runtime.native_tools.spec import NativeToolResult
from ownpi.runtime.native_tools.tool_invocation import build_tool_invocations
from ownpi.task_authoring.contracts import TaskSpecArtifactRef
from ownpi.task_authoring.tool_handlers import (
    TaskSpecAuthoringToolContext,
    artifact_from_write_result,
)
from ownpi.task_authoring.tool_specs import WRITE_TASK_SPEC_TOOL


NO_TOOL_CORRECTION = (
    "The authoring turn did not call a tool. Do not reply with prose. "
    "If path metadata is needed, call inspect_user_path; otherwise call "
    "write_task_spec with the completed Markdown now."
)
@dataclass
class TaskSpecAuthoringPolicy:
    context: TaskSpecAuthoringToolContext
    last_tool_failure_class: str = ""
    last_tool_failure_message: str = ""
    artifact: TaskSpecArtifactRef | None = None

    def tool_choice(self, *, turn_index: int) -> str:
        del turn_index
        return "auto"

    def on_provider_error(
        self,
        error: BaseException,
        *,
        turn_index: int,
        tool_call_count: int,
    ) -> NativeLoopDirective:
        del turn_index, tool_call_count
        if is_loop_cancellation(error):
            raise error
        return NativeLoopDirective.fail(
            failure_class="provider_error",
            message=str(error),
        )

    def plan_turn(self, snapshot: NativeLoopSnapshot) -> NativeLoopTurnPlan:
        calls = build_tool_invocations(snapshot.response)
        if not calls:
            return NativeLoopTurnPlan(
                directive=NativeLoopDirective.continue_with(
                    reason="missing_tool_call",
                    correction_text=NO_TOOL_CORRECTION,
                    correction_index=snapshot.turn_index,
                )
            )
        return NativeLoopTurnPlan(calls=calls)

    def after_results(
        self,
        snapshot: NativeLoopSnapshot,
        results: tuple[NativeToolResult, ...],
    ) -> NativeLoopDirective:
        for result in results:
            if result.status != "completed":
                self.last_tool_failure_class = str(
                    result.metadata.get("failure_class") or "tool_runtime_error"
                )
                self.last_tool_failure_message = str(
                    result.data.get("message") or result.output_text
                )
                if self.last_tool_failure_class == "persistence_error":
                    return NativeLoopDirective.fail(
                        failure_class=self.last_tool_failure_class,
                        message=self.last_tool_failure_message,
                    )
            if result.tool_name != WRITE_TASK_SPEC_TOOL.name or result.status != "completed":
                continue
            self.artifact = artifact_from_write_result(result)
            if self.artifact is None:
                return NativeLoopDirective.fail(
                    failure_class="invalid_persistence_result",
                    message="write_task_spec completed without an artifact reference",
                )
            return NativeLoopDirective.complete(reason="task_spec_written")
        return NativeLoopDirective.continue_with()

__all__ = [
    "NO_TOOL_CORRECTION",
    "TaskSpecAuthoringPolicy",
]
