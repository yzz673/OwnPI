from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ownpi.controller.stages import Stage
from ownpi.runtime.native_tools.loop_outcome import pending_interaction


def raise_loop_cancellation(state_store: Any, *, stage: Stage, attempt: int, index: int) -> None:
    from ownpi.controller.runtime_control_signal import RuntimeControlRequest, RuntimeControlRequested
    from ownpi.runtime.events import now_utc_iso

    state_store.poll_runtime_control('native_tool_cancelled')
    raise RuntimeControlRequested(RuntimeControlRequest(
        request_id=f'native-cancel:{stage.value}:{attempt}:{index}',
        action='interrupt', requested_at=now_utc_iso(),
        reason='native tool loop cancelled by user',
    ))


@dataclass
class HumanClarificationPausedError(RuntimeError):
    stage: Stage
    attempt: int
    continuation_index: int
    reason: str
    session_id: str
    continuation_kind: str
    step_results: list[dict[str, Any]]
    pending_interactions: tuple[Any, ...] = ()

    def __str__(self) -> str:
        return self.reason


def raise_child_interactions(sessions: tuple[Any, ...]) -> None:
    if not sessions:
        return
    session = sessions[0]
    raise HumanClarificationPausedError(
        stage=Stage(session.stage), attempt=session.attempt,
        continuation_index=session.continuation_index, reason=session.reason,
        session_id=session.session_id, continuation_kind=session.continuation_kind,
        step_results=[], pending_interactions=sessions,
    )


@dataclass
class ToolPermissionPausedError(RuntimeError):
    stage: Stage
    attempt: int
    continuation_index: int
    reason: str
    session_id: str
    continuation_kind: str
    tool_name: str
    step_results: list[dict[str, Any]]

    def __str__(self) -> str:
        return self.reason


def raise_pending_actor_interaction(
    *,
    state_store: Any,
    stage: Stage,
    attempt: int,
    continuation_index: int,
    step_results: list[dict[str, Any]],
) -> None:
    workflow = state_store.state.workflow_state
    clarification = pending_interaction(workflow.pending_human_clarification)
    if (
        clarification is not None
        and clarification.stage == stage.value
        and clarification.attempt == attempt
        and clarification.continuation_index == continuation_index
    ):
        raise HumanClarificationPausedError(
            stage=stage,
            attempt=attempt,
            continuation_index=continuation_index,
            reason=clarification.reason or "Agent requested human clarification.",
            session_id=clarification.session_id,
            continuation_kind=clarification.continuation_kind,
            step_results=list(step_results),
        )
    permission = workflow.pending_tool_permission
    if (
        permission is not None
        and permission.status == "pending"
        and permission.stage == stage.value
        and permission.attempt == attempt
        and permission.continuation_index == continuation_index
    ):
        raise ToolPermissionPausedError(
            stage=stage,
            attempt=attempt,
            continuation_index=continuation_index,
            reason=permission.reason or f"Tool {permission.tool_name} requires approval.",
            session_id=permission.session_id,
            continuation_kind=permission.continuation_kind,
            tool_name=permission.tool_name,
            step_results=list(step_results),
        )


__all__ = [
    "HumanClarificationPausedError",
    "ToolPermissionPausedError",
    "raise_pending_actor_interaction",
]
