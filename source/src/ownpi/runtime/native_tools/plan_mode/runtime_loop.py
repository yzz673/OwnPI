from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ownpi.runtime.native_tools.plan_mode.adapter import PlanModeLoopAdapter
from ownpi.runtime.native_tools.plan_mode.contracts import (
    PlanDecision,
    PlanModeRuntimeResult,
)


@dataclass
class PlanModeRuntimeLoop:
    """Reusable planning lifecycle around one shared Plan Mode adapter."""

    adapter: PlanModeLoopAdapter
    continuation_ref: str = "plan-mode-continuation"
    _status: str = "idle"
    _latest_plan_text: str = ""
    _latest_visible_text: str = ""
    _pending_boundary: dict[str, Any] | None = None

    def start(self) -> PlanModeRuntimeResult:
        if self._status not in {"idle", "continue_planning"}:
            raise RuntimeError(f"Plan Mode cannot start from status: {self._status}")
        self._status = "running"
        return self._run_segment()

    def resume_human_answer(self) -> PlanModeRuntimeResult:
        if self._status != "human_input_requested":
            raise RuntimeError("Plan Mode has no pending Human input")
        self.adapter.deliver_answers()
        self._status = "running"
        return self._run_segment()

    def submit_decision(self, decision: PlanDecision) -> PlanModeRuntimeResult:
        if self._status != "approval_requested":
            raise RuntimeError("Plan Mode has no pending approval decision")
        if decision not in {"approved", "revise", "continue_planning"}:
            raise ValueError(f"unsupported Plan Mode decision: {decision}")
        self.adapter.deliver_answers()
        if decision == "approved":
            self._status = "approved"
            return self._result("approved")
        self.adapter.append_human_answer(
            "The Human requested a revised Stage Blueprint. Reassess the current "
            "progress and proposal before returning a new <proposed_plan>."
            if decision == "revise"
            else "The Human requested additional planning before approval."
        )
        self._status = "continue_planning"
        return self._run_segment()

    def pending_handle(self) -> dict[str, Any]:
        return {
            "conversation_id": self.adapter.session.history.conversation_id,
            "status": self._status,
            "boundary": "human_input" if self._status == "human_input_requested" else "approval",
        }

    def restore_boundary(self, status: str) -> None:
        if status not in {"human_input_requested", "approval_requested", "continue_planning"}:
            raise ValueError("Plan Mode can restore only a pending Human boundary")
        parsed = self.adapter.latest_proposed_plan()
        if status == "approval_requested" and not parsed.plan_text:
            raise ValueError("Plan Mode approval state is missing its proposed plan")
        self._status = status
        self._latest_plan_text = parsed.plan_text
        self._latest_visible_text = parsed.visible_text

    def close(self) -> bool:
        return self.adapter.continuation.close()

    def _run_segment(self) -> PlanModeRuntimeResult:
        segment = self.adapter.run_segment()
        self._latest_plan_text = segment.plan_text or self._latest_plan_text
        self._latest_visible_text = segment.visible_text or self._latest_visible_text
        if segment.status == 'user_cancelled':
            self._status = segment.status
            self._pending_boundary = segment.pending_boundary
            return self._result(segment.status, pending_boundary=segment.pending_boundary)
        if segment.status == "failed":
            self._status = "failed"
            return self._result(
                "failed",
                failure_class=segment.failure_class,
                failure_message=segment.failure_message,
            )
        if segment.status == "human_input_pending":
            self._status = "human_input_requested"
            self._pending_boundary = segment.pending_boundary
            return self._result(
                "human_input_requested",
                pending_boundary=segment.pending_boundary,
            )
        if segment.status == "plan_completed":
            self._status = "approval_requested"
            return self._result("approval_requested", metadata=segment.metadata)
        self._status = "failed"
        return self._result(
            "failed",
            failure_class="plan_mode_completed_without_plan",
            failure_message="Plan Mode ended without a proposed plan block",
        )

    def _result(
        self,
        status: str,
        *,
        pending_boundary: dict[str, Any] | None = None,
        failure_class: str = "",
        failure_message: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> PlanModeRuntimeResult:
        return PlanModeRuntimeResult(
            status=status,  # type: ignore[arg-type]
            plan_text=self._latest_plan_text,
            visible_text=self._latest_visible_text,
            pending_boundary=pending_boundary,
            continuation_ref=self.continuation_ref,
            failure_class=failure_class,
            failure_message=failure_message,
            metadata=dict(metadata or {}),
        )


__all__ = ["PlanModeRuntimeLoop"]
