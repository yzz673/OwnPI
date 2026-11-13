from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

from ownpi.controller.stages import Stage
from ownpi.runtime.context.entry import RuntimeEntryContext
from ownpi.runtime.native_tools.plan_mode.gate import PlanModeFeatureGate
from ownpi.runtime.native_tools.plan_mode.stage_entry import (
    PlanModeStageEntryOrchestrator,
    StageEntryPlanRequest,
)
from ownpi.schemas.runtime_human_clarification import (
    HumanClarificationQuestion,
    HumanClarificationSession,
    HumanQuestionOption,
)


if TYPE_CHECKING:
    from ownpi.runtime.rollout.resume_entry import StageResumeEntry


@dataclass(frozen=True)
class PlanModePlacementResult:
    status: str
    approved_stage_plan_path: str = ""
    approved_stage_plan_sha256: str = ""
    failure_reason: str = ""
    consumed_resume_session: bool = False


class PlanModeStageEntryPlacement:
    """Bridge one staged-runtime entry to the isolated Plan Mode subsystem."""

    def __init__(self, *, host: Any) -> None:
        self._host = host

    def execute(
        self,
        *,
        run_dir: Path,
        state_store: Any,
        saved_files: list[str],
        stage: Stage,
        attempt: int,
        entry_context: RuntimeEntryContext,
        active_resume_session: object | None,
        resume_entry: StageResumeEntry | None = None,
    ) -> PlanModePlacementResult:
        host = self._host
        workflow = state_store.state.workflow_state
        task_context = state_store.state.task_context
        plan_resume_session = (
            active_resume_session
            if isinstance(active_resume_session, HumanClarificationSession)
            and (
                active_resume_session.continuation_kind == "plan_mode_stage_entry"
                or (
                    active_resume_session.origin_kind == 'tool'
                    and active_resume_session.actor_kind == 'plan_mode'
                )
            )
            else None
        )
        if plan_resume_session is not None and (
            plan_resume_session.stage != stage.value
            or int(plan_resume_session.attempt or 0) != attempt
        ):
            return self._fail(
                run_dir=run_dir,
                state_store=state_store,
                saved_files=saved_files,
                stage=stage,
                attempt=attempt,
                reason="Plan Mode resume session does not match the current stage entry.",
            )
        if plan_resume_session is not None and plan_resume_session.status == "pending":
            return PlanModePlacementResult(status="paused")
        if plan_resume_session is not None and plan_resume_session.status != "answered":
            return self._fail(
                run_dir=run_dir,
                state_store=state_store,
                saved_files=saved_files,
                stage=stage,
                attempt=attempt,
                reason=(
                    "Plan Mode resume session must be pending or answered, got "
                    f"{plan_resume_session.status!r}."
                ),
            )
        task_spec_ref = task_context.task_spec_ref
        if task_spec_ref is None:
            return self._fail(
                run_dir=run_dir,
                state_store=state_store,
                saved_files=saved_files,
                stage=stage,
                attempt=attempt,
                reason="Plan Mode stage entry requires a canonical task specification.",
            )
        result = PlanModeStageEntryOrchestrator(
            gate=PlanModeFeatureGate(enabled=True),
            loop_factory=host._build_plan_mode_stage_entry_loop,
        ).run(
            StageEntryPlanRequest(
                run_dir=run_dir,
                project_root=Path(state_store.project_root).expanduser().resolve(),
                stage=stage,
                level=workflow.stage_runtime_profile,
                attempt=attempt,
                raw_message_path=Path(task_context.raw_user_message_ref.path).resolve(),
                task_spec_path=Path(task_spec_ref.path).resolve(),
                state_store=state_store,
                entry_context=entry_context,
                cancel_checker=getattr(host, '_runtime_cancel_checker', None),
                resume_conversation_id=resume_entry.plan_conversation_id if resume_entry else None,
                resume_read_session=resume_entry.facts.read_session if resume_entry else None,
                resume_boundary=resume_entry.plan_resume_boundary if resume_entry else None,
            ),
            resume_session=plan_resume_session,
        )
        if result.status == 'user_cancelled':
            return PlanModePlacementResult(status=result.status)
        if result.status == "approved" and result.blueprint is not None:
            if result.blueprint.plan_path not in saved_files:
                saved_files.append(result.blueprint.plan_path)
            if not result.reused:
                state_store.record_stage_event(
                    event_type="plan_mode_stage_entry_approved",
                    stage=stage,
                    attempt=attempt,
                    payload={
                        "entry_id": result.blueprint.entry_id,
                        "plan_path": result.blueprint.plan_path,
                        "content_sha256": result.blueprint.content_sha256,
                        "revision": result.blueprint.revision,
                    },
                    status="success",
                )
            from ownpi.runtime.rollout.stage_business_events import emit_plan_approved

            plan_path = Path(result.blueprint.plan_path).expanduser().resolve()
            emit_plan_approved(
                getattr(state_store, 'run_event_store', None),
                entry_id=result.blueprint.entry_id,
                stage=result.blueprint.stage,
                attempt=result.blueprint.attempt,
                plan_path=str(plan_path.relative_to(Path(run_dir).resolve())),
                content_sha256=result.blueprint.content_sha256,
                revision=result.blueprint.revision,
            )
            consumed_resume = plan_resume_session is not None
            if consumed_resume:
                host._clear_controller_clarification_handoff(
                    state_store=state_store,
                    consumed_by_continuation_index=plan_resume_session.continuation_index,
                )
            return PlanModePlacementResult(
                status="approved",
                approved_stage_plan_path=result.blueprint.plan_path,
                approved_stage_plan_sha256=result.blueprint.content_sha256,
                consumed_resume_session=consumed_resume,
            )
        if result.status in {"approval_requested", "human_input_requested"}:
            if result.visible_text.strip():
                host._emit(f"[plan-mode:{stage.value}] {result.visible_text.strip()}")
            if result.plan_text.strip():
                host._emit(
                    f"[plan-mode:{stage.value}] Proposed Stage Blueprint:\n{result.plan_text.strip()}"
                )
            if result.status == 'human_input_requested':
                session = state_store.current_human_clarification_session()
                if session is None or session.session_id != (result.pending_boundary or {}).get('session_id'):
                    raise ValueError('Plan Mode interaction boundary has no matching tool session')
            else:
                host._pause_for_human_clarification(
                    state_store=state_store,
                    run_dir=run_dir,
                    saved_files=saved_files,
                    stage=stage,
                    attempt=attempt,
                    continuation_index=int(
                        workflow.scratchpad.get("continuation_index", 0) or 0
                    ),
                    continuation_kind="plan_mode_stage_entry",
                    actor_kind='plan_mode', actor_id='plan_mode', conversation_lane=stage.value,
                    source="plan_mode",
                    reason="Plan Mode requires Human approval before Stage Prep.",
                    questions=[_pending_question(result.status, pending_boundary=result.pending_boundary)],
                )
            state_store.record_stage_event(
                event_type="plan_mode_stage_entry_paused",
                stage=stage,
                attempt=attempt,
                payload={"entry_id": result.entry_id, "status": result.status},
                status="paused_for_human",
            )
            return PlanModePlacementResult(status="paused")
        return self._fail(
            run_dir=run_dir,
            state_store=state_store,
            saved_files=saved_files,
            stage=stage,
            attempt=attempt,
            reason=f"plan_mode_failed: {result.failure_class}: {result.failure_message}",
        )

    def _fail(
        self,
        *,
        run_dir: Path,
        state_store: Any,
        saved_files: list[str],
        stage: Stage,
        attempt: int,
        reason: str,
    ) -> PlanModePlacementResult:
        state_store.record_error(
            message=reason,
            stage=stage,
            attempt=attempt,
            agent_id="plan_mode_stage_entry",
        )
        state_store.state.workflow_state.scratchpad["runtime_failure_reason"] = reason
        return PlanModePlacementResult(status="failed", failure_reason=reason)


def _pending_question(
    status: str,
    *,
    pending_boundary: dict[str, object] | None,
) -> HumanClarificationQuestion:
    if status == "approval_requested":
        return HumanClarificationQuestion(
            question_id="plan_approval",
            header="Approve stage blueprint",
            question=(
                "Review the proposed stage blueprint and approve it, or request a revision before Stage Prep begins."
            ),
            options=(
                HumanQuestionOption(
                    "Approve", "Proceed to Stage Prep with this blueprint."
                ),
                HumanQuestionOption(
                    "Revise", "Return to Plan Mode and revise the blueprint."
                ),
            ),
            source="plan_mode",
        )
    boundary_data = (
        pending_boundary.get("data") if isinstance(pending_boundary, dict) else None
    )
    raw_questions = (
        boundary_data.get("questions") if isinstance(boundary_data, dict) else None
    )
    if isinstance(raw_questions, list) and raw_questions:
        first = raw_questions[0]
        if isinstance(first, dict):
            try:
                return HumanClarificationQuestion.from_wire_strict(
                    first,
                    question_index=1,
                    source="plan_mode",
                )
            except ValueError:
                pass
    return HumanClarificationQuestion(
        question_id="plan_mode_input",
        header="Plan Mode needs input",
        question=(
            "Provide the missing information so Plan Mode can continue planning this stage."
        ),
        options=(),
        source="plan_mode",
    )


__all__ = ["PlanModePlacementResult", "PlanModeStageEntryPlacement"]
