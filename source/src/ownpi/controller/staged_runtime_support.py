from __future__ import annotations

from pathlib import Path
from typing import Callable

from ownpi.controller.staged_runtime_host import StagedRuntimeHost
from ownpi.controller.state_store import WorkflowStateStore
from ownpi.controller.stages import Stage
from ownpi.runtime.context.entry import RuntimeEntryContext
from ownpi.runtime.continuation_directive import RuntimeContinuationDirective
from ownpi.controller.runtime_control_signal import (
    RuntimeControlRequested,
    clear_runtime_control_request,
    consume_runtime_control_request,
    read_runtime_control_request,
    runtime_control_lock,
)
from ownpi.schemas.runtime_human_clarification import HumanClarificationSession
from ownpi.schemas.runtime_permission import ToolPermissionSession


def resolve_stage_entry_context(
    *,
    pending: RuntimeEntryContext,
    stage_is_resuming: bool,
    matched_directive: RuntimeContinuationDirective | None,
    active_session: HumanClarificationSession | ToolPermissionSession | None,
    resuming_continuation: bool,
) -> RuntimeEntryContext:
    if pending.origin != 'live' or not stage_is_resuming:
        return pending
    return RuntimeEntryContext(
        origin='resume',
        source_resume_id=(
            matched_directive.source_record_id
            if matched_directive is not None
            else (
                active_session.session_id
                if resuming_continuation and active_session is not None
                else ''
            )
        ),
        continuation_kind=(
            matched_directive.execution_kind
            if matched_directive is not None
            else (
                active_session.continuation_kind
                if resuming_continuation and active_session is not None
                else ''
            )
        ),
    )


def build_runtime_control_poll(
    *,
    host: StagedRuntimeHost,
    state_store: WorkflowStateStore,
    run_dir: Path,
    saved_files: list[str],
    current_stage: Stage,
    allow_direct: bool = False,
) -> Callable[[str], None]:
    def poll_runtime_control(_reason: str) -> None:
        _consume_runtime_control_request(
            host=host,
            state_store=state_store,
            run_dir=run_dir,
            saved_files=saved_files,
            current_stage=current_stage,
            allow_direct=allow_direct,
        )

    return poll_runtime_control


def _consume_runtime_control_request(
    *,
    host: StagedRuntimeHost,
    state_store: WorkflowStateStore,
    run_dir: Path,
    saved_files: list[str],
    current_stage: Stage,
    allow_direct: bool,
) -> bool:
    with runtime_control_lock(run_dir):
        request = read_runtime_control_request(run_dir)
        is_full_runtime = host._is_full_tier_runtime(state_store.state.workflow_state)
        if request is None or not (is_full_runtime or allow_direct):
            return is_full_runtime
        workflow_state = state_store.state.workflow_state
        if not consume_runtime_control_request(
            run_dir, request, event_store=state_store.run_event_store,
        ):
            clear_runtime_control_request(run_dir, request_id=request.request_id)
            return False
        lifecycle_reason = (
            "user_pause_requested"
            if request.action == "pause"
            else "user_interrupt_requested"
        )
        workflow_state.pause_reason = request.reason or lifecycle_reason
        workflow_state.scratchpad["runtime_failure_reason"] = lifecycle_reason
        workflow_state.scratchpad["runtime_control_action"] = request.action
        raise RuntimeControlRequested(request)


__all__ = [
    'build_runtime_control_poll',
    'resolve_stage_entry_context',
]
