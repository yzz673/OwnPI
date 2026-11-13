from __future__ import annotations

from ownpi.llm.base import LLMClient
from ownpi.task_authoring.contracts import (
    TaskSpecAuthoringOutcome,
    TaskSpecAuthoringRequest,
)
from ownpi.task_authoring.loop import run_task_spec_authoring_loop
from ownpi.task_authoring.persistence import (
    build_run_task_artifact_layout,
)
from ownpi.task_authoring.tool_handlers import TaskSpecAuthoringToolContext
from ownpi.runtime.rollout.resume import ResumedConversation
from ownpi.utils.io import ensure_dir
from ownpi.runtime.native_tools.loop_outcome import is_loop_cancellation
from ownpi.controller.runtime_control_signal import RuntimeControlRequested


class TaskSpecAuthoringService:
    def __init__(self, llm_client: LLMClient) -> None:
        self._llm_client = llm_client

    def author(self, *, request: TaskSpecAuthoringRequest,
               replay: ResumedConversation | None = None) -> TaskSpecAuthoringOutcome:
        layout = build_run_task_artifact_layout(request.run_dir)
        try:
            ensure_dir(layout.run_dir)
            context = TaskSpecAuthoringToolContext(
                request_id=request.request_id,
                raw_user_message=request.raw_user_message,
                project_root=request.project_root,
                layout=layout,
            )
            result = run_task_spec_authoring_loop(
                client=self._llm_client,
                request=request,
                context=context,
                replay=replay,
            )
            return result.outcome
        except Exception as error:
            if is_loop_cancellation(error) or isinstance(error, RuntimeControlRequested):
                raise
            outcome = TaskSpecAuthoringOutcome(
                status="failed",
                request_id=request.request_id,
                failure_class="authoring_service_error",
                failure_message=str(error)[:500],
            )
            return outcome


__all__ = ["TaskSpecAuthoringService"]
