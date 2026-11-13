from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ownpi.controller.stages import (
    GateDecision,
    Stage,
    gate_decision_from_value,
    stage_from_value,
)


def _require_exact_fields(
    data: dict[str, Any],
    *,
    allowed: set[str],
    label: str,
    required: set[str] | None = None,
) -> None:
    actual = {str(key) for key in data}
    unknown = sorted(actual - allowed)
    missing = sorted((allowed if required is None else required) - actual)
    if unknown or missing:
        raise ValueError(
            f"{label} fields mismatch: missing={missing}, unknown={unknown}"
        )




@dataclass(frozen=True)
class GateTrace:
    stage: Stage
    attempt: int
    decision: GateDecision
    next_stage: Stage | None
    counter_snapshot: dict[str, int]
    terminal_status: str | None = None
    reason: str | None = None
    limit_exceeded_reason: str | None = None
    stage_close_kind: str | None = None
    review_rounds_completed: int = 0
    latest_review_record_ref: str | None = None
    residual_review_record_ref: str | None = None
    revisit_metadata: dict[str, Any] = field(default_factory=dict)
    evidence_lane: str = "review"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GateTrace":
        raw_next_stage = data.get("next_stage")
        return cls(
            stage=stage_from_value(data["stage"]),
            attempt=max(1, int(data.get("attempt", 1))),
            decision=gate_decision_from_value(
                data.get("decision", GateDecision.FAIL.value)
            ),
            next_stage=stage_from_value(raw_next_stage) if raw_next_stage else None,
            counter_snapshot={
                str(key): int(value)
                for key, value in dict(data.get("counter_snapshot", {})).items()
            },
            terminal_status=(
                str(data["terminal_status"])
                if data.get("terminal_status") is not None
                else None
            ),
            reason=(
                str(data["reason"])
                if data.get("reason") is not None
                else (
                    str(data["limit_exceeded_reason"])
                    if data.get("limit_exceeded_reason") is not None
                    else None
                )
            ),
            limit_exceeded_reason=(
                str(data["limit_exceeded_reason"])
                if data.get("limit_exceeded_reason") is not None
                else (str(data["reason"]) if data.get("reason") is not None else None)
            ),
            stage_close_kind=(
                str(data["stage_close_kind"])
                if data.get("stage_close_kind") is not None
                else None
            ),
            review_rounds_completed=int(data.get("review_rounds_completed", 0) or 0),
            latest_review_record_ref=(
                str(data["latest_review_record_ref"])
                if data.get("latest_review_record_ref") is not None
                else None
            ),
            residual_review_record_ref=(
                str(data["residual_review_record_ref"])
                if data.get("residual_review_record_ref") is not None
                else None
            ),
            revisit_metadata=dict(data.get("revisit_metadata", {}))
            if isinstance(data.get("revisit_metadata"), dict)
            else {},
            evidence_lane=str(data.get("evidence_lane", "review") or "review"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "attempt": self.attempt,
            "decision": self.decision.value,
            "next_stage": self.next_stage.value
            if self.next_stage is not None
            else None,
            "counter_snapshot": dict(self.counter_snapshot),
            "terminal_status": self.terminal_status,
            "reason": self.reason or self.limit_exceeded_reason,
            "limit_exceeded_reason": self.limit_exceeded_reason,
            "stage_close_kind": self.stage_close_kind,
            "review_rounds_completed": self.review_rounds_completed,
            "latest_review_record_ref": self.latest_review_record_ref,
            "residual_review_record_ref": self.residual_review_record_ref,
            "revisit_metadata": dict(self.revisit_metadata),
            "evidence_lane": self.evidence_lane,
        }


@dataclass(frozen=True)
class StageRunSummary:
    stage: Stage
    attempt: int
    decision: GateDecision
    status: str
    stage_run_id: str | None = None
    transition_reason: str | None = None
    next_stage: Stage | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StageRunSummary":
        _require_exact_fields(
            data,
            allowed={
                "stage",
                "attempt",
                "decision",
                "status",
                "stage_run_id",
                "transition_reason",
                "next_stage",
            },
            label="stage run record",
        )
        raw_next_stage = data.get("next_stage")
        return cls(
            stage=stage_from_value(data["stage"]),
            attempt=int(data["attempt"]),
            decision=gate_decision_from_value(data["decision"]),
            status=str(data["status"]),
            stage_run_id=(
                str(data["stage_run_id"])
                if data.get("stage_run_id") is not None
                else None
            ),
            transition_reason=(
                str(data["transition_reason"])
                if data.get("transition_reason") is not None
                else None
            ),
            next_stage=stage_from_value(raw_next_stage) if raw_next_stage else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "attempt": self.attempt,
            "decision": self.decision.value,
            "status": self.status,
            "stage_run_id": self.stage_run_id,
            "transition_reason": self.transition_reason,
            "next_stage": self.next_stage.value
            if self.next_stage is not None
            else None,
        }


@dataclass(frozen=True)
class RunArtifacts:
    run_dir: str
    saved_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RunOutcome:
    task_success: bool
    outcome: str
    outcome_class: str
    blocking_stage: str | None = None
    blocking_run_status: str | None = None
    package_quality: str | None = None
    claim_boundary: str | None = None
    rationale: list[str] = field(default_factory=list)
    source: str = "derived"
    delivery_status: str | None = None
    delivery_class: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "RunOutcome | None":
        if not isinstance(data, dict):
            return None
        return cls(
            task_success=bool(data.get("task_success", False)),
            outcome=str(data.get("outcome") or "unknown"),
            outcome_class=str(data.get("outcome_class") or "unknown"),
            blocking_stage=(
                str(data["blocking_stage"])
                if data.get("blocking_stage") is not None
                else None
            ),
            blocking_run_status=(
                str(data["blocking_run_status"])
                if data.get("blocking_run_status") is not None
                else None
            ),
            package_quality=(
                str(data["package_quality"])
                if data.get("package_quality") is not None
                else None
            ),
            claim_boundary=(
                str(data["claim_boundary"])
                if data.get("claim_boundary") is not None
                else None
            ),
            rationale=[
                str(item) for item in data.get("rationale", []) if str(item).strip()
            ],
            source=str(data.get("source") or "derived"),
            delivery_status=(
                str(data["delivery_status"])
                if data.get("delivery_status") is not None
                else None
            ),
            delivery_class=(
                str(data["delivery_class"])
                if data.get("delivery_class") is not None
                else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        delivery_status, delivery_class = derive_delivery_boundary(
            task_success=self.task_success,
            outcome=self.outcome,
            outcome_class=self.outcome_class,
            explicit_delivery_status=self.delivery_status,
            explicit_delivery_class=self.delivery_class,
        )
        return {
            "task_success": self.task_success,
            "runtime_delivery_success": delivery_status
            in {"completed", "completed_with_caveats"},
            "delivery_status": delivery_status,
            "delivery_class": delivery_class,
            "outcome": self.outcome,
            "outcome_class": self.outcome_class,
            "blocking_stage": self.blocking_stage,
            "blocking_run_status": self.blocking_run_status,
            "package_quality": self.package_quality,
            "claim_boundary": self.claim_boundary,
            "rationale": list(self.rationale),
            "source": self.source,
        }


def derive_delivery_boundary(
    *,
    task_success: bool,
    outcome: str,
    outcome_class: str,
    explicit_delivery_status: str | None = None,
    explicit_delivery_class: str | None = None,
) -> tuple[str, str]:
    if explicit_delivery_status and explicit_delivery_class:
        return explicit_delivery_status, explicit_delivery_class

    normalized_class = str(outcome_class or "").strip()
    normalized_outcome = str(outcome or "").strip()
    if normalized_class == 'user_stopped':
        return 'stopped', 'stopped_by_user'
    if normalized_class == "successful_completion":
        return "completed", "user_deliverable_complete"
    if normalized_class == "successful_completion_with_caveats":
        return "completed_with_caveats", "user_deliverable_complete_with_claim_boundary"
    if normalized_class == "partial_deliverable":
        return "completed_with_caveats", "user_deliverable_complete_with_claim_boundary"
    if normalized_class == "downgraded_research_delivery":
        return "completed", "downgraded_research_delivery"
    if normalized_class in {
        "needs_human_input",
        "needs_human_permission",
        "blocked_by_user_permission",
    }:
        return "blocked", "missing_user_owned_input"
    if normalized_class == "external_prerequisite_missing":
        return "blocked", "permission_or_external_service_unavailable"
    if normalized_class in {
        "runtime_provider_failure",
        "runtime_llm_context_failure",
    }:
        return "failed", normalized_class
    if normalized_class in {
        "waiting_execution_reconciliation",
        "waiting_remote_reconciliation",
    }:
        return "failed", "execution_failed"
    if normalized_class in {"execution_failed", "execution_not_ready"}:
        return "failed", "execution_failed"
    if normalized_outcome == "cancelled":
        return "cancelled", "cancelled_by_user"
    if task_success:
        return "completed", "user_deliverable_complete"
    return "failed", "execution_failed"


@dataclass(frozen=True)
class FinalPackage:
    status: str
    task_id: str
    current_stage: str | None
    final_decision: str
    run_summary: dict[str, Any]
    stage_runs: list[StageRunSummary]
    run_artifacts: dict[str, Any]
    final_deliverable: dict[str, Any] | None = None
    run_outcome: RunOutcome | None = None
    raw_final_decision: str | None = None
    decision_mapping_applied: bool = False
    decision_mapping_reason: str | None = None
    delivery_status: str | None = None
    delivery_class: str | None = None
    artifact_manifest: list[dict[str, Any]] = field(default_factory=list)
    evidence_manifest: list[dict[str, Any]] = field(default_factory=list)
    runtime_diagnostics: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FinalPackage":
        _require_exact_fields(
            data,
            allowed={
                "status",
                "task_id",
                "current_stage",
                "final_decision",
                "delivery_status",
                "delivery_class",
                "raw_final_decision",
                "decision_mapping_applied",
                "decision_mapping_reason",
                "run_outcome",
                "artifact_manifest",
                "evidence_manifest",
                "runtime_diagnostics",
                "run_summary",
                "stage_runs",
                "final_deliverable",
                "run_artifacts",
            },
            label="final package",
        )
        return cls(
            status=str(data["status"]),
            task_id=str(data["task_id"]),
            current_stage=(
                str(data["current_stage"])
                if data["current_stage"] is not None
                else None
            ),
            final_decision=str(data["final_decision"]),
            delivery_status=(
                str(data["delivery_status"])
                if data["delivery_status"] is not None
                else None
            ),
            delivery_class=(
                str(data["delivery_class"])
                if data["delivery_class"] is not None
                else None
            ),
            raw_final_decision=(
                str(data["raw_final_decision"])
                if data["raw_final_decision"] is not None
                else None
            ),
            decision_mapping_applied=bool(data["decision_mapping_applied"]),
            decision_mapping_reason=(
                str(data["decision_mapping_reason"])
                if data["decision_mapping_reason"] is not None
                else None
            ),
            run_outcome=RunOutcome.from_dict(data["run_outcome"]),
            artifact_manifest=[dict(item) for item in data["artifact_manifest"]],
            evidence_manifest=[dict(item) for item in data["evidence_manifest"]],
            runtime_diagnostics=dict(data["runtime_diagnostics"]),
            run_summary=dict(data["run_summary"]),
            stage_runs=[StageRunSummary.from_dict(item) for item in data["stage_runs"]],
            final_deliverable=(
                dict(data["final_deliverable"])
                if isinstance(data["final_deliverable"], dict)
                else None
            ),
            run_artifacts=dict(data["run_artifacts"]),
        )

    def to_dict(self) -> dict[str, Any]:
        delivery_status = self.delivery_status
        delivery_class = self.delivery_class
        if self.run_outcome is not None:
            run_outcome_payload = self.run_outcome.to_dict()
            delivery_status = delivery_status or str(
                run_outcome_payload.get("delivery_status") or ""
            )
            delivery_class = delivery_class or str(
                run_outcome_payload.get("delivery_class") or ""
            )
        else:
            run_outcome_payload = None
        return {
            "status": self.status,
            "task_id": self.task_id,
            "current_stage": self.current_stage,
            "final_decision": self.final_decision,
            "delivery_status": delivery_status or None,
            "delivery_class": delivery_class or None,
            "raw_final_decision": self.raw_final_decision,
            "decision_mapping_applied": self.decision_mapping_applied,
            "decision_mapping_reason": self.decision_mapping_reason,
            "run_outcome": run_outcome_payload,
            "artifact_manifest": [dict(item) for item in self.artifact_manifest],
            "evidence_manifest": [dict(item) for item in self.evidence_manifest],
            "runtime_diagnostics": dict(self.runtime_diagnostics),
            "run_summary": dict(self.run_summary),
            "stage_runs": [item.to_dict() for item in self.stage_runs],
            "final_deliverable": self.final_deliverable,
            "run_artifacts": dict(self.run_artifacts),
        }
