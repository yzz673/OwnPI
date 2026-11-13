from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from ownpi.controller.state_store import WorkflowStateStore
from ownpi.controller.stages import Stage
from ownpi.schemas.runtime_human_clarification import HumanClarificationQuestion, HumanClarificationSession
from ownpi.schemas.runtime_permission import ToolPermissionSession


class StagedRuntimeHost(Protocol):
    """Engine surface required by StagedRuntimeLoop.

    This Protocol makes the staged loop boundary explicit without turning the
    engine's private helpers into a public service API in this pass.
    """

    _finalization_service: Any
    _stage_kernel: Any
    _user_config: Any

    def _attach_execution_runtime_service(
        self,
        *,
        state_store: WorkflowStateStore,
        run_dir: Path,
    ) -> Any: ...

    def _build_plan_mode_stage_entry_loop(self, request: Any) -> Any: ...

    def _active_controller_clarification_handoff(self, *, state_store: WorkflowStateStore) -> Any | None: ...

    def _clear_controller_clarification_handoff(
        self,
        *,
        state_store: WorkflowStateStore,
        consumed_by_continuation_index: int | None = None,
    ) -> None: ...

    def _emit(self, message: str) -> None: ...

    def _emit_runtime_status(
        self,
        *,
        state_store: WorkflowStateStore,
        event_kind: str,
        component: str,
        operation: str,
    ) -> None: ...

    def _ensure_gate_human_clarification_session(
        self,
        *,
        state_store: WorkflowStateStore,
        run_dir: Path,
        saved_files: list[str],
        stage: Stage,
        attempt: int,
        report: Any,
    ) -> Any: ...

    def _is_continuation_runtime_continuation_kind(self, continuation_kind: str) -> bool: ...

    def _is_full_tier_runtime(self, workflow_state: Any) -> bool: ...

    def _materialize_consumed_resume_session(
        self,
        *,
        state_store: WorkflowStateStore,
        session: HumanClarificationSession | ToolPermissionSession | None,
    ) -> None: ...

    def _pause_for_human_clarification(
        self,
        *,
        state_store: WorkflowStateStore,
        run_dir: Path,
        saved_files: list[str],
        stage: Stage,
        attempt: int,
        continuation_index: int,
        continuation_kind: str,
        source: str,
        reason: str,
        questions: list[HumanClarificationQuestion],
    ) -> HumanClarificationSession: ...
