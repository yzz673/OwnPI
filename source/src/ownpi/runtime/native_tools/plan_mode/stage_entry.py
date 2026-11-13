from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Literal

from ownpi.controller.stages import Stage
from ownpi.runtime.context.entry import RuntimeEntryContext
from ownpi.runtime.native_tools.plan_mode.contracts import PlanDecision
from ownpi.runtime.native_tools.plan_mode.gate import PlanModeFeatureGate
from ownpi.runtime.native_tools.plan_mode.runtime_loop import PlanModeRuntimeLoop
from ownpi.schemas.runtime_human_clarification import (
    HumanClarificationSession,
    human_answer_text,
)
from ownpi.schemas.workflow_plan import StageRuntimeProfile
from ownpi.utils.io import write_text_atomic


PlanEntryStatus = Literal[
    "user_cancelled",
    "disabled",
    "approved",
    "approval_requested",
    "human_input_requested",
    "failed",
]
PlanLoopFactory = Callable[["StageEntryPlanRequest"], PlanModeRuntimeLoop]


@dataclass(frozen=True)
class StageEntryPlanRequest:
    run_dir: Path
    project_root: Path
    stage: Stage
    level: StageRuntimeProfile
    attempt: int
    raw_message_path: Path
    task_spec_path: Path
    state_store: object
    entry_context: RuntimeEntryContext = RuntimeEntryContext()
    cancel_checker: Callable[[], bool] | None = None
    resume_conversation_id: str | None = None
    resume_read_session: object | None = None
    resume_boundary: Literal['human_input_requested', 'approval_requested'] | None = None

    @property
    def entry_id(self) -> str:
        return f"{self.stage.value}:attempt-{self.attempt:03d}"


@dataclass(frozen=True)
class ApprovedStageBlueprintRef:
    entry_id: str
    stage: str
    attempt: int
    plan_path: str
    content_sha256: str
    revision: int = 1
    status: str = "approved"

    def validate(self, *, run_dir: Path) -> None:
        path = Path(self.plan_path).expanduser().resolve()
        root = Path(run_dir).expanduser().resolve()
        if not path.is_relative_to(root):
            raise ValueError("approved stage blueprint must remain inside run_dir")
        if not path.is_file():
            raise FileNotFoundError(f"approved stage blueprint is missing: {path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != self.content_sha256:
            raise ValueError("approved stage blueprint hash mismatch")
        if self.status != "approved" or self.revision < 1:
            raise ValueError("approved stage blueprint ref has invalid lifecycle metadata")

    def to_dict(self) -> dict[str, object]:
        return {
            "entry_id": self.entry_id,
            "stage": self.stage,
            "attempt": self.attempt,
            "plan_path": self.plan_path,
            "content_sha256": self.content_sha256,
            "revision": self.revision,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "ApprovedStageBlueprintRef":
        return cls(
            entry_id=str(payload.get("entry_id", "")),
            stage=str(payload.get("stage", "")),
            attempt=int(payload.get("attempt", 0) or 0),
            plan_path=str(payload.get("plan_path", "")),
            content_sha256=str(payload.get("content_sha256", "")),
            revision=int(payload.get("revision", 1) or 1),
            status=str(payload.get("status", "approved")),
        )


@dataclass(frozen=True)
class StageEntryPlanResult:
    status: PlanEntryStatus
    blueprint: ApprovedStageBlueprintRef | None = None
    plan_text: str = ""
    visible_text: str = ""
    pending_boundary: dict[str, object] | None = None
    failure_class: str = ""
    failure_message: str = ""
    entry_id: str = ""
    reused: bool = False


class PlanModeStageEntryOrchestrator:
    """Owns the single Plan Mode placement before Stage Prep."""

    def __init__(
        self, *, gate: PlanModeFeatureGate, loop_factory: PlanLoopFactory
    ) -> None:
        self._gate = gate
        self._loop_factory = loop_factory

    def run(
        self,
        request: StageEntryPlanRequest,
        *,
        resume_session: HumanClarificationSession | None = None,
    ) -> StageEntryPlanResult:
        if not self._gate.enabled:
            return StageEntryPlanResult(status="disabled", entry_id=request.entry_id)
        loop = None
        try:
            layout = _layout(request)
            existing = (None if request.entry_context.is_resume
                        else _load_approved(layout=layout, request=request))
            if existing is not None:
                existing.validate(run_dir=request.run_dir)
                return StageEntryPlanResult(
                    status="approved",
                    blueprint=existing,
                    entry_id=request.entry_id,
                    reused=True,
                )
            if resume_session is not None and request.resume_conversation_id is None:
                if request.entry_context.is_resume:
                    raise ValueError('Plan Mode resume requires its selected conversation')
                handle = _pending_handle(request)
                conversation_id = handle.get('conversation_id')
                if not isinstance(conversation_id, str) or not conversation_id:
                    raise ValueError('Plan Mode resume requires its selected conversation')
                request = replace(request, resume_conversation_id=conversation_id)
            loop = self._loop_factory(request)
            if resume_session is not None:
                human_input = (request.resume_boundary == 'human_input_requested'
                               if request.entry_context.is_resume
                               else resume_session.origin_kind == 'tool'
                               or _pending_handle(request).get('boundary') == 'human_input')
                loop.restore_boundary('human_input_requested' if human_input else 'approval_requested')
                if human_input:
                    result = loop.resume_human_answer()
                else:
                    answer = _plan_answer(resume_session)
                    if not answer:
                        return StageEntryPlanResult(
                            status="failed",
                            entry_id=request.entry_id,
                            failure_class="plan_mode_missing_answer",
                            failure_message="Plan approval requires a non-empty Human answer.",
                        )
                    result = loop.submit_decision(_decision(answer))
            else:
                result = loop.start()
            if result.status == 'user_cancelled':
                return StageEntryPlanResult(
                    status=result.status, entry_id=request.entry_id,
                    pending_boundary=result.pending_boundary,
                )
            if result.status == "approved":
                return self._approve(
                    request=request, layout=layout, plan_text=result.plan_text
                )
            if result.status == "approval_requested":
                plan_text = result.plan_text.strip()
                if not plan_text:
                    return StageEntryPlanResult(
                        status="failed",
                        entry_id=request.entry_id,
                        failure_class="plan_mode_empty_blueprint",
                        failure_message="Plan Mode requested approval without a blueprint.",
                    )
                _store_pending_handle(request, loop.pending_handle())
                return StageEntryPlanResult(
                    status="approval_requested",
                    plan_text=plan_text,
                    visible_text=result.visible_text,
                    entry_id=request.entry_id,
                )
            if result.status == "human_input_requested":
                _store_pending_handle(request, loop.pending_handle())
                return StageEntryPlanResult(
                    status="human_input_requested",
                    plan_text=result.plan_text,
                    visible_text=result.visible_text,
                    pending_boundary=result.pending_boundary,
                    entry_id=request.entry_id,
                )
            return StageEntryPlanResult(
                status="failed",
                entry_id=request.entry_id,
                failure_class=result.failure_class or "plan_mode_failed",
                failure_message=result.failure_message
                or "Plan Mode failed before approval.",
            )
        except Exception as error:
            return StageEntryPlanResult(
                status="failed",
                entry_id=request.entry_id,
                failure_class=f"plan_mode_{type(error).__name__.lower()}",
                failure_message=str(error)[:500],
            )
        finally:
            if loop is not None:
                loop.close()

    def _approve(
        self,
        *,
        request: StageEntryPlanRequest,
        layout: "_PlanLayout",
        plan_text: str,
    ) -> StageEntryPlanResult:
        if not plan_text.strip():
            raise ValueError("approved stage blueprint must not be empty")
        write_text_atomic(layout.approved_path, plan_text.strip() + "\n")
        digest = hashlib.sha256(layout.approved_path.read_bytes()).hexdigest()
        ref = ApprovedStageBlueprintRef(
            entry_id=request.entry_id,
            stage=request.stage.value,
            attempt=request.attempt,
            plan_path=str(layout.approved_path),
            content_sha256=digest,
        )
        request.state_store.state.workflow_state.scratchpad.get('plan_mode_pending_handles', {}).pop(request.entry_id, None)
        return StageEntryPlanResult(
            status="approved",
            blueprint=ref,
            plan_text=plan_text,
            entry_id=request.entry_id,
        )


@dataclass(frozen=True)
class _PlanLayout:
    root: Path
    approved_path: Path


def _layout(request: StageEntryPlanRequest) -> _PlanLayout:
    root = (
        Path(request.run_dir).expanduser().resolve()
        / "stage-plans"
        / request.stage.value
        / f"attempt-{request.attempt:03d}"
    )
    root.mkdir(parents=True, exist_ok=True)
    return _PlanLayout(
        root=root,
        approved_path=root / "approved_stage_plan.md",
    )


def _load_approved(
    *, layout: _PlanLayout, request: StageEntryPlanRequest
) -> ApprovedStageBlueprintRef | None:
    if not layout.approved_path.is_file():
        return None
    return ApprovedStageBlueprintRef(
        entry_id=request.entry_id,
        stage=request.stage.value,
        attempt=request.attempt,
        plan_path=str(layout.approved_path),
        content_sha256=hashlib.sha256(layout.approved_path.read_bytes()).hexdigest(),
    )


def _store_pending_handle(request: StageEntryPlanRequest, payload: dict[str, object]) -> None:
    request.state_store.state.workflow_state.scratchpad.setdefault(
        'plan_mode_pending_handles', {},
    )[request.entry_id] = dict(payload)


def _pending_handle(request: StageEntryPlanRequest) -> dict[str, object]:
    return dict(request.state_store.state.workflow_state.scratchpad.get(
        'plan_mode_pending_handles', {},
    ).get(request.entry_id, {}))


def _plan_answer(session: HumanClarificationSession) -> str:
    for question in session.questions:
        answer = human_answer_text(session.answers.get(question.question_id))
        if answer:
            return answer.strip()
    return ""


def _decision(answer: str) -> PlanDecision:
    normalized = answer.strip().casefold().replace("-", "_")
    if normalized in {"approve", "approved", "yes", "同意", "批准"}:
        return "approved"
    return "revise"


__all__ = [
    "ApprovedStageBlueprintRef",
    "PlanModeStageEntryOrchestrator",
    "StageEntryPlanRequest",
    "StageEntryPlanResult",
]
