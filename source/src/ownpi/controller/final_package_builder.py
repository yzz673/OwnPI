from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from ownpi.controller.state_store import WorkflowStateStore
from ownpi.controller.stages import (
    GateDecision,
    Stage,
    is_completed_gate_decision,
)
from ownpi.runtime.memory.external.summary import (
    build_external_knowledge_receipt_summary,
)
from ownpi.runtime.memory.internal.map_contributor import (
    internal_memory_repository_snapshot,
)
from ownpi.runtime.memory.internal.summary import (
    build_internal_memory_receipt_summary,
)
from ownpi.runtime.permissions.models import EffectivePermissions
from ownpi.runtime.projections.run_outcome_inspection import (
    classify_non_success_run_status,
)
from ownpi.runtime.research_state_resource import research_state_path
from ownpi.runtime.state_models import calculate_total_stage_attempts
from ownpi.runtime.contracts import ArtifactRecord
from ownpi.schemas.artifacts import (
    FinalPackage,
    RunArtifacts,
    RunOutcome,
    StageRunSummary,
    GateTrace,
)
from ownpi.schemas.runtime_human_clarification import (
    CONTROLLER_CLARIFICATION_HANDOFF_KEY,
    active_controller_clarification_handoff,
)
from ownpi.schemas.workflow_plan import ExecutionTier


_RUNTIME_FAILURE_OUTCOMES = {
    "native_tool_local_runtime_failure": (
        "runtime_local_failure",
        "local_runtime_failure",
        "native tool loop failed because local runtime processing could not continue",
    ),
    "native_tool_provider_or_protocol_failure": (
        "runtime_provider_or_protocol_failure",
        "runtime_provider_failure",
        "provider-native tool loop failed before producing an agent task artifact",
    ),
}


@dataclass(frozen=True)
class FinalPackageBuildResult:
    package: FinalPackage


class FinalPackageBuilder:
    """Builds final package payloads without writing run files."""

    def build(
        self,
        *,
        state_store: WorkflowStateStore,
        run_dir: Path,
        saved_files: list[str],
        stage_runs: list[StageRunSummary] | None = None,
        final_status: str,
        final_decision: GateDecision | str,
        terminal_stage: Stage | None,
        effective_permissions: EffectivePermissions | None = None,
    ) -> FinalPackageBuildResult:
        workflow_state = state_store.state.workflow_state
        if stage_runs is None:
            stage_runs = stage_summaries_from_gates(state_store)
        raw_final_decision_value = (
            final_decision.value
            if isinstance(final_decision, GateDecision)
            else str(final_decision)
        )
        final_deliverable_ref = None
        stage_runtime_profile = str(
            getattr(workflow_state.stage_runtime_profile, "value", "") or "task_level"
        )
        final_deliverable = None
        run_outcome = self.derive_run_outcome(
            workflow_state=workflow_state,
            artifacts=state_store.state.artifact_state.recent_artifacts,
            stage_runs=stage_runs,
            final_status=final_status,
            final_decision=raw_final_decision_value,
        )
        (
            effective_final_decision_value,
            decision_mapping_applied,
            decision_mapping_reason,
        ) = self.resolve_effective_final_decision(
            stage_runs=stage_runs,
            raw_final_decision=raw_final_decision_value,
            run_outcome=run_outcome,
        )
        run_outcome_payload = run_outcome.to_dict() if run_outcome is not None else {}
        delivery_status = (
            str(run_outcome_payload.get("delivery_status") or "").strip() or None
        )
        delivery_class = (
            str(run_outcome_payload.get("delivery_class") or "").strip() or None
        )
        context_runtime = (
            dict(workflow_state.scratchpad.get("context_runtime", {}))
            if isinstance(workflow_state.scratchpad.get("context_runtime", {}), dict)
            else {}
        )
        tool_call_history = list(
            getattr(state_store.state.tool_state, "tool_call_history", []) or []
        )
        compact_execution_summary = build_compact_execution_summary(
            tool_call_history=tool_call_history,
            context_runtime=context_runtime,
        )
        resolved_terminal_stage = (
            terminal_stage
            or (stage_runs[-1].stage if stage_runs else None)
            or workflow_state.current_stage
        )
        resolved_terminal_stage_value = (
            resolved_terminal_stage.value
            if isinstance(resolved_terminal_stage, Stage)
            else None
        )
        pending_tool_permission = getattr(
            workflow_state, "pending_tool_permission", None
        )
        run_summary = {
            "status": final_status,
            "current_stage": resolved_terminal_stage_value,
            "final_decision": effective_final_decision_value,
            "completed_stages": [
                item.stage.value
                for item in stage_runs
                if is_completed_gate_decision(item.decision)
            ],
            "total_stage_attempts": calculate_total_stage_attempts(workflow_state),
            "execution_tier": workflow_state.execution_tier.value,
            "stage_runtime_profile": stage_runtime_profile,
            "feedback_summary": build_feedback_summary(state_store),
            "clarification_reason": (
                workflow_state.pending_human_clarification.reason
                if workflow_state.pending_human_clarification is not None
                and workflow_state.pending_human_clarification.reason
                else None
            ),
            "pending_human_clarification": (
                workflow_state.pending_human_clarification.to_dict()
                if workflow_state.pending_human_clarification is not None
                else None
            ),
            "pending_tool_permission": (
                pending_tool_permission.to_dict()
                if pending_tool_permission is not None
                else None
            ),
            "human_clarification_history": [
                item.to_dict()
                for item in workflow_state.human_clarification_history[-6:]
            ],
            "controller_clarification_handoff": active_controller_clarification_handoff(
                workflow_state.scratchpad.get(CONTROLLER_CLARIFICATION_HANDOFF_KEY)
            ),
            "tool_permission_history": [
                item.to_dict()
                for item in getattr(workflow_state, "tool_permission_history", [])[-6:]
            ],
            "clarification_resume_count": int(
                workflow_state.clarification_resume_count or 0
            ),
            "pause_reason": getattr(workflow_state, "pause_reason", None),
            "memory_summary": {
                "internal_status": state_store.state.memory_state.internal_memory_status,
                "internal_repository_identity": (
                    state_store.state.memory_state.internal_memory_repository_identity
                ),
                "external_status": state_store.state.memory_state.external_knowledge_status,
                "external_release_id": (
                    state_store.state.memory_state.external_knowledge_pin.release_id
                    if state_store.state.memory_state.external_knowledge_pin is not None
                    else None
                ),
            },
            "llm_trace_summary": build_llm_trace_summary(
                workflow_state.scratchpad.get("llm_trace")
            ),
            "tool_runtime_overview": {
                "invocation_count": compact_execution_summary["tool_invocation_count"],
                "top_tool_counts": dict(compact_execution_summary["tools"]),
                "native_loaded_tool_count": len(
                    state_store.state.tool_state.native_loaded_tool_refs
                ),
                "native_search_receipt_count": len(
                    state_store.state.tool_state.native_tool_search_receipts
                ),
                "native_loaded_tool_names": [
                    str(item.get("canonical_name", "") or "")
                    for item in state_store.state.tool_state.native_loaded_tool_refs
                    if isinstance(item, dict)
                    and str(item.get("canonical_name", "") or "").strip()
                ],
            },
            "compact_execution_summary": compact_execution_summary,
            "runtime_failure_reason": workflow_state.scratchpad.get(
                "runtime_failure_reason"
            ),
            "runtime_failure_details": (
                dict(workflow_state.scratchpad.get("runtime_failure_details", {}))
                if isinstance(
                    workflow_state.scratchpad.get("runtime_failure_details", {}), dict
                )
                else None
            ),
            "delivery_status": delivery_status,
            "delivery_class": delivery_class,
            "raw_final_decision": raw_final_decision_value,
            "effective_final_decision": effective_final_decision_value,
            "decision_mapping_applied": decision_mapping_applied,
            "decision_mapping_reason": decision_mapping_reason,
            "run_outcome": run_outcome_payload or None,
            "internal_memory_live_summary": build_internal_memory_receipt_summary(
                snapshot=internal_memory_repository_snapshot(state_store.state),
                tool_call_history=tool_call_history,
                repository_identity_history=(
                    state_store.state.memory_state.internal_memory_repository_identity_history
                ),
            ),
            "external_knowledge_live_summary": build_external_knowledge_receipt_summary(
                pin=state_store.state.memory_state.external_knowledge_pin,
                tool_call_history=tool_call_history,
            ),
        }

        updated_saved_files = list(dict.fromkeys(saved_files))
        run_artifacts = RunArtifacts(
            run_dir=str(run_dir), saved_files=updated_saved_files
        ).to_dict()

        if final_deliverable_ref:
            run_artifacts["final_deliverable_ref"] = final_deliverable_ref
        run_artifacts["saved_files"] = list(dict.fromkeys(updated_saved_files))
        artifact_manifest = build_artifact_manifest(
            run_dir=run_dir,
            saved_files=run_artifacts.get("saved_files", []),
            run_artifacts=run_artifacts,
            final_deliverable=final_deliverable,
        )
        evidence_manifest = build_evidence_manifest(
            final_deliverable=final_deliverable,
            claim_boundary=run_outcome_payload.get("claim_boundary")
            if run_outcome_payload
            else None,
        )
        runtime_diagnostics = build_runtime_diagnostics(
            run_summary=run_summary,
            run_artifacts=run_artifacts,
            delivery_status=delivery_status,
            delivery_class=delivery_class,
        )
        return FinalPackageBuildResult(
            package=FinalPackage(
                status=final_status,
                task_id=str(state_store.state.run_metadata.task_id or ""),
                current_stage=resolved_terminal_stage_value,
                final_decision=effective_final_decision_value,
                run_summary=run_summary,
                stage_runs=stage_runs,
                run_artifacts=run_artifacts,
                final_deliverable=final_deliverable,
                run_outcome=run_outcome,
                raw_final_decision=raw_final_decision_value,
                decision_mapping_applied=decision_mapping_applied,
                decision_mapping_reason=decision_mapping_reason,
                delivery_status=delivery_status,
                delivery_class=delivery_class,
                artifact_manifest=artifact_manifest,
                evidence_manifest=evidence_manifest,
                runtime_diagnostics=runtime_diagnostics,
            ),
        )

    def resolve_effective_final_decision(
        self,
        *,
        stage_runs: list[StageRunSummary],
        raw_final_decision: str,
        run_outcome: RunOutcome | None,
    ) -> tuple[str, bool, str | None]:
        raw_value = str(raw_final_decision or "").strip()
        if run_outcome is not None and run_outcome.outcome_class == 'user_stopped':
            return 'stopped', raw_value != 'stopped', 'runtime_control:stopped'
        if not is_completed_gate_decision(raw_value):
            return raw_value, False, None
        if run_outcome is None:
            return raw_value, False, None
        if (
            raw_value == GateDecision.ACCEPT.value
            and _stage_runs_have_residual_findings(stage_runs)
        ):
            return (
                GateDecision.ADVANCE_WITH_RESIDUAL_FINDINGS.value,
                True,
                "cross_stage_review_mapping:accept_to_residual_findings",
            )
        return raw_value, False, None

    def derive_run_outcome(
        self,
        *,
        workflow_state: Any,
        artifacts: list[ArtifactRecord],
        stage_runs: list[StageRunSummary],
        final_status: str,
        final_decision: str,
    ) -> RunOutcome:
        if final_status == 'stopped':
            return RunOutcome(
                task_success=False,
                outcome='stopped',
                outcome_class='user_stopped',
                rationale=['user stopped the run; resume can continue it'],
            )
        bounded_review_advance = (
            final_decision == GateDecision.ADVANCE_WITH_RESIDUAL_FINDINGS.value
            or _stage_runs_have_residual_findings(stage_runs)
        )
        package_quality = derive_package_quality(
            final_decision=(
                GateDecision.ADVANCE_WITH_RESIDUAL_FINDINGS.value
                if bounded_review_advance
                else final_decision
            )
        )
        claim_boundary = None
        if final_status == "completed" and is_completed_gate_decision(final_decision):
            terminal_stage = stage_runs[-1].stage.value if stage_runs else None
            terminal_label = (
                f"terminal stage {terminal_stage or 'unknown'} completed with residual Reviewer findings"
                if bounded_review_advance
                else f"terminal stage {terminal_stage or 'unknown'} completed and was accepted"
            )
            if bounded_review_advance:
                return RunOutcome(
                    task_success=True,
                    outcome="success_with_caveats",
                    outcome_class="successful_completion_with_caveats",
                    package_quality=package_quality,
                    claim_boundary=claim_boundary,
                    rationale=[
                        terminal_label,
                        "the terminal Reviewer budget was exhausted without a clean-review claim",
                    ],
                )
            return RunOutcome(
                task_success=True,
                outcome="success",
                outcome_class="successful_completion",
                package_quality=package_quality,
                claim_boundary=claim_boundary,
                rationale=[
                    terminal_label,
                    "benchmark scoring is intentionally left to the external evaluator",
                ],
            )

        implementation_payload = latest_stage_payload_from_artifacts(
            artifacts=artifacts,
            stage=Stage.IMPLEMENTATION_EXECUTION,
        )
        execution_result = (
            implementation_payload.get("execution_result", {})
            if isinstance(implementation_payload, dict)
            else {}
        )
        run_status = (
            str(execution_result.get("run_status") or "").strip()
            if isinstance(execution_result, dict)
            else ""
        )

        if final_decision == GateDecision.FAIL.value:
            runtime_failure_details = (
                dict(workflow_state.scratchpad.get("runtime_failure_details", {}))
                if isinstance(
                    workflow_state.scratchpad.get("runtime_failure_details", {}), dict
                )
                else {}
            )
            runtime_failure_outcome = _runtime_failure_run_outcome(
                runtime_failure_details=runtime_failure_details,
                package_quality=package_quality,
                claim_boundary=claim_boundary,
            )
            if runtime_failure_outcome is not None:
                return runtime_failure_outcome
            if str(runtime_failure_details.get("terminal_reason") or "").strip() == (
                "blocked:user_denied_required_execution"
            ):
                return RunOutcome(
                    task_success=False,
                    outcome="blocked",
                    outcome_class="blocked_by_user_permission",
                    blocking_stage=(
                        str(runtime_failure_details.get("stage") or "").strip()
                        or Stage.IMPLEMENTATION_EXECUTION.value
                    ),
                    blocking_run_status="user_denied_required_execution",
                    package_quality=package_quality,
                    claim_boundary=claim_boundary,
                    rationale=["user denied required execution permission"],
                )
            outcome, outcome_class = classify_non_success_run_status(
                run_status or "failed"
            )
            return RunOutcome(
                task_success=False,
                outcome=outcome,
                outcome_class=outcome_class,
                blocking_stage=Stage.IMPLEMENTATION_EXECUTION.value
                if run_status
                else None,
                blocking_run_status=run_status or None,
                package_quality=package_quality,
                claim_boundary=claim_boundary,
                rationale=["controller finalized the run as fail"],
            )

        if final_decision == GateDecision.CLARIFY_HUMAN.value:
            return RunOutcome(
                task_success=False,
                outcome="awaiting_human_clarification",
                outcome_class="needs_human_input",
                blocking_stage=workflow_state.current_stage.value
                if workflow_state.current_stage is not None
                else None,
                blocking_run_status=run_status or None,
                package_quality=package_quality,
                claim_boundary=claim_boundary,
                rationale=[
                    "run is paused_for_human and waiting for missing external input"
                ],
            )

        if final_decision == "await_permission":
            return RunOutcome(
                task_success=False,
                outcome="awaiting_tool_permission",
                outcome_class="needs_human_permission",
                blocking_stage=workflow_state.current_stage.value
                if workflow_state.current_stage is not None
                else None,
                blocking_run_status=run_status or None,
                package_quality=package_quality,
                claim_boundary=claim_boundary,
                rationale=[
                    "run is paused_for_human and waiting for interactive tool permission"
                ],
            )

        return RunOutcome(
            task_success=False,
            outcome="in_progress",
            outcome_class="non_terminal_or_unclassified",
            blocking_stage=workflow_state.current_stage.value
            if workflow_state.current_stage is not None
            else None,
            blocking_run_status=run_status or None,
            package_quality=package_quality,
            claim_boundary=claim_boundary,
            rationale=["run has not reached a successful terminal outcome"],
        )


def is_full_tier_runtime(workflow_state: Any) -> bool:
    return workflow_state.execution_tier == ExecutionTier.FULL


def latest_stage_payload_from_artifacts(
    *, artifacts: list[Any], stage: Stage
) -> dict[str, Any] | None:
    relevant = [
        artifact for artifact in reversed(artifacts)
        if artifact.stage_context.get("stage") == stage.value
    ]
    for artifact in sorted(
        relevant,
        key=lambda item: int(item.stage_context.get("attempt", 0) or 0),
        reverse=True,
    ):
        for file_path in reversed(artifact.source_paths):
            path = Path(file_path)
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            stage_payload = payload.get("payload")
            if isinstance(stage_payload, dict):
                return stage_payload
            if isinstance(payload.get("execution_result"), dict):
                return payload
    return None


def _runtime_failure_run_outcome(
    *,
    runtime_failure_details: dict[str, Any],
    package_quality: str | None,
    claim_boundary: str | None,
) -> RunOutcome | None:
    terminal_reason = str(runtime_failure_details.get("terminal_reason") or "").strip()
    operation = terminal_reason or _first_runtime_failure_operation(
        runtime_failure_details
    )
    precise_failure_class = str(
        runtime_failure_details.get("failure_class") or ""
    ).strip()
    mapped = _RUNTIME_FAILURE_OUTCOMES.get(operation)
    if mapped is None:
        return None
    outcome, outcome_class, rationale_text = mapped
    if precise_failure_class in {
        "transient_provider_failure",
        "deterministic_provider_input_failure",
        "provider_protocol_failure",
        "local_runtime_failure",
    }:
        outcome_class = precise_failure_class
    stage = str(runtime_failure_details.get("stage") or "").strip() or None
    return RunOutcome(
        task_success=False,
        outcome=outcome,
        outcome_class=outcome_class,
        blocking_stage=stage,
        blocking_run_status=None,
        package_quality=package_quality,
        claim_boundary=claim_boundary,
        rationale=[
            rationale_text,
            "runtime/provider failure is separated from agent scientific or engineering task quality",
        ],
    )


def _first_runtime_failure_operation(runtime_failure_details: dict[str, Any]) -> str:
    step_results = runtime_failure_details.get("step_results")
    if not isinstance(step_results, list):
        return ""
    for item in step_results:
        if not isinstance(item, dict):
            continue
        operation = str(item.get("operation") or "").strip()
        if operation in _RUNTIME_FAILURE_OUTCOMES:
            return operation
    return ""


def derive_package_quality(*, final_decision: str) -> str | None:
    if not is_completed_gate_decision(final_decision):
        return None
    if final_decision == GateDecision.ADVANCE_WITH_RESIDUAL_FINDINGS.value:
        return "completed_with_residual_findings"
    return "accepted"


def stage_summaries_from_gates(state_store: WorkflowStateStore) -> list[StageRunSummary]:
    summaries: list[StageRunSummary] = []
    seen: set[str] = set()
    store = state_store.run_event_store
    for event in store.events if store is not None else ():
        if event.kind != "stage_gate_decided" or event.event_id in seen:
            continue
        seen.add(event.event_id)
        data = event.data.to_object()
        gate = GateTrace.from_dict(data["gate"])
        summaries.append(StageRunSummary(
            stage=gate.stage,
            attempt=gate.attempt,
            stage_run_id=data["stage_run_id"],
            decision=gate.decision,
            status=gate.terminal_status or (
                "completed" if is_completed_gate_decision(gate.decision) else gate.decision.value
            ),
            transition_reason=gate.reason or gate.limit_exceeded_reason or gate.decision.value,
            next_stage=gate.next_stage,
        ))
    return summaries


def _stage_runs_have_residual_findings(stage_runs: list[StageRunSummary]) -> bool:
    return any(
        run.decision == GateDecision.ADVANCE_WITH_RESIDUAL_FINDINGS
        for run in stage_runs
    )


def build_artifact_manifest(
    *,
    run_dir: Path,
    saved_files: list[Any],
    run_artifacts: dict[str, Any],
    final_deliverable: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    seen: set[str] = set()
    canonical_research_state = research_state_path(run_dir)

    for raw_path in saved_files:
        path = str(raw_path or "").strip()
        if not path:
            continue
        candidate = Path(path).expanduser()
        resolved_candidate = (
            candidate if candidate.is_absolute() else run_dir / candidate
        ).resolve(strict=False)
        if resolved_candidate == canonical_research_state:
            continue
        _append_artifact_manifest_item(
            manifest,
            seen=seen,
            run_dir=run_dir,
            path=path,
            kind="runtime_file",
            stage=None,
            visibility=_artifact_visibility(path),
            description=_artifact_description(path),
        )

    return manifest


def _append_artifact_manifest_item(
    manifest: list[dict[str, Any]],
    *,
    seen: set[str],
    run_dir: Path,
    path: str,
    kind: str,
    stage: str | None,
    visibility: str,
    description: str,
) -> None:
    normalized_path = str(path).strip()
    if not normalized_path or normalized_path in seen:
        return
    seen.add(normalized_path)
    candidate = Path(normalized_path)
    exists = (
        candidate.exists()
        if candidate.is_absolute()
        else (run_dir / candidate).exists()
    )
    manifest.append(
        {
            "path": normalized_path,
            "kind": kind,
            "stage": stage,
            "visibility": visibility,
            "description": description,
            "exists": bool(exists),
        }
    )


def _artifact_visibility(path: str) -> str:
    lowered = str(path or "").casefold()
    if any(
        marker in lowered
        for marker in ("trace", "snapshot", "manifest", "run_summary")
    ):
        return "diagnostic"
    if "final_deliverable" in lowered or "output_synthesis" in lowered:
        return "user"
    return "diagnostic"


def _artifact_description(path: str) -> str:
    name = Path(str(path or "")).name
    if not name:
        return "runtime artifact"
    return name.replace("_", " ").replace("-", " ")


def build_evidence_manifest(
    *,
    final_deliverable: dict[str, Any] | None,
    claim_boundary: Any,
) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    if isinstance(final_deliverable, dict):
        evidence_refs = [
            str(item).strip()
            for item in final_deliverable.get("evidence_refs", []) or []
            if str(item).strip()
        ]
        limitations = [
            str(item).strip()
            for item in final_deliverable.get("limitations", []) or []
            if str(item).strip()
        ]
        for item in final_deliverable.get("key_findings", []) or []:
            claim = _claim_text(item)
            if claim:
                manifest.append(
                    {
                        "claim": claim,
                        "evidence_refs": _evidence_refs_from_claim(item)
                        or evidence_refs,
                        "support_level": "reported",
                        "limitations": limitations,
                    }
                )
    boundary = str(claim_boundary or "").strip()
    if boundary:
        manifest.append(
            {
                "claim": "claim_boundary",
                "evidence_refs": [],
                "support_level": "boundary",
                "limitations": [boundary],
            }
        )
    return _dedupe_evidence_manifest(manifest)


def _claim_text(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if not isinstance(item, dict):
        return ""
    for key in ("claim_text", "summary", "text", "claim", "label", "value"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def _evidence_refs_from_claim(item: Any) -> list[str]:
    if not isinstance(item, dict):
        return []
    refs: list[str] = []
    for key in ("evidence_refs", "evidence", "source", "sources", "artifact_refs"):
        raw = item.get(key)
        if isinstance(raw, list):
            refs.extend(str(value).strip() for value in raw if str(value).strip())
        elif str(raw or "").strip():
            refs.append(str(raw).strip())
    return list(dict.fromkeys(refs))


def _dedupe_evidence_manifest(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        claim = str(item.get("claim") or "").strip()
        support_level = str(item.get("support_level") or "").strip()
        key = (claim, support_level)
        if not claim or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def build_runtime_diagnostics(
    *,
    run_summary: dict[str, Any],
    run_artifacts: dict[str, Any],
    delivery_status: str | None,
    delivery_class: str | None,
) -> dict[str, Any]:
    return {
        "delivery_status": delivery_status,
        "delivery_class": delivery_class,
        "runtime_failure_reason": run_summary.get("runtime_failure_reason"),
        "runtime_failure_details": run_summary.get("runtime_failure_details"),
        "tool_runtime_overview": run_summary.get("tool_runtime_overview"),
        "compact_execution_summary": run_summary.get("compact_execution_summary"),
        "clarification_reason": run_summary.get("clarification_reason"),
        "pending_human_clarification": run_summary.get("pending_human_clarification"),
        "pending_tool_permission": run_summary.get("pending_tool_permission"),
    }


def build_compact_execution_summary(
    *,
    tool_call_history: list[Any],
    context_runtime: dict[str, Any],
) -> dict[str, Any]:
    del context_runtime
    tool_counts: dict[str, int] = {}
    for tool_name, count in Counter(
        str(getattr(item, "tool_name", "") or "").strip()
        for item in tool_call_history
        if str(getattr(item, "tool_name", "") or "").strip()
    ).most_common(8):
        tool_counts[tool_name] = int(count)

    return {
        "tool_invocation_count": len(tool_call_history),
        "tools": dict(tool_counts),
    }


def build_feedback_summary(state_store: WorkflowStateStore) -> dict[str, Any]:
    feedback_state = state_store.state.feedback_state
    records = [ref.record for ref in feedback_state.record_refs]
    role_counts = Counter(record.author_role for record in records)
    critic_loop = feedback_state.critic_loop
    route_receipt = feedback_state.analysis_transition.next_stage_receipt
    return {
        "record_count": len(records),
        "history_evidence_gaps": list(feedback_state.history_evidence_gaps),
        "records_by_role": {
            role: int(role_counts.get(role, 0))
            for role in ("reviewer", "analysis")
        },
        "critic_rounds_completed": {
            role: int(role_counts.get(role, 0)) for role in ("reviewer",)
        },
        "latest_critic": (
            {
                "role": critic_loop.critic_role,
                "source_stage": critic_loop.source_stage,
                "attempt": critic_loop.attempt,
                "round": critic_loop.round,
                "phase": critic_loop.phase,
                "feedback_record_id": critic_loop.feedback_record_id,
                "review_target": critic_loop.review_target.to_dict() if critic_loop.review_target else None,
                "completion_reason": critic_loop.completion_reason,
            }
            if critic_loop.phase != "idle"
            else None
        ),
        "analysis_route": route_receipt.to_dict()
        if route_receipt is not None
        else None,
        "active_handoff": feedback_state.active_handoff.to_dict(),
    }


def build_llm_trace_summary(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "llm_call_count": 0,
            "llm_success_count": 0,
            "llm_error_count": 0,
            "timeout_like_error_count": 0,
            "retried_call_count": 0,
            "total_elapsed_ms": 0.0,
            "max_prompt_char_count": 0,
            "top_heavy_operations": [],
        }
    summary_payload = payload.get("summary")
    if isinstance(summary_payload, dict):
        operation_stats = summary_payload.get("operation_stats", {})
        top_heavy = []
        if isinstance(operation_stats, dict):
            top_heavy = sorted(
                (
                    dict(item)
                    for item in operation_stats.values()
                    if isinstance(item, dict)
                ),
                key=lambda item: (
                    float(item.get("total_elapsed_ms", 0.0) or 0.0),
                    float(item.get("max_elapsed_ms", 0.0) or 0.0),
                ),
                reverse=True,
            )[:8]
        return {
            "llm_call_count": int(
                summary_payload.get("llm_call_count", payload.get("event_count", 0))
                or 0
            ),
            "llm_success_count": int(summary_payload.get("llm_success_count", 0) or 0),
            "llm_error_count": int(summary_payload.get("llm_error_count", 0) or 0),
            "timeout_like_error_count": int(
                summary_payload.get("timeout_like_error_count", 0) or 0
            ),
            "retried_call_count": int(
                summary_payload.get("retried_call_count", 0) or 0
            ),
            "total_elapsed_ms": round(
                float(summary_payload.get("total_elapsed_ms", 0.0) or 0.0), 2
            ),
            "max_prompt_char_count": int(
                summary_payload.get("max_prompt_char_count", 0) or 0
            ),
            "top_heavy_operations": [
                {
                    **item,
                    "total_elapsed_ms": round(
                        float(item.get("total_elapsed_ms", 0.0) or 0.0), 2
                    ),
                    "max_elapsed_ms": round(
                        float(item.get("max_elapsed_ms", 0.0) or 0.0), 2
                    ),
                }
                for item in top_heavy
            ],
        }
    raw_events = payload.get("events", [])
    events = [item for item in raw_events if isinstance(item, dict)]
    grouped: dict[str, dict[str, Any]] = {}
    for item in events:
        key = llm_operation_key(item)
        bucket = grouped.setdefault(
            key,
            {
                "operation_key": key,
                "call_count": 0,
                "total_elapsed_ms": 0.0,
                "max_elapsed_ms": 0.0,
                "max_prompt_char_count": 0,
                "error_count": 0,
            },
        )
        elapsed_ms = float(item.get("elapsed_ms", 0.0) or 0.0)
        prompt_chars = int(item.get("prompt_char_count", 0) or 0)
        bucket["call_count"] += 1
        bucket["total_elapsed_ms"] += elapsed_ms
        bucket["max_elapsed_ms"] = max(float(bucket["max_elapsed_ms"]), elapsed_ms)
        bucket["max_prompt_char_count"] = max(
            int(bucket["max_prompt_char_count"]), prompt_chars
        )
        if not bool(item.get("success", False)):
            bucket["error_count"] += 1
    timeout_like_count = sum(
        1
        for item in events
        if "timeout" in str(item.get("error_type", "")).lower()
        or "timed out" in str(item.get("error_message", "")).lower()
    )
    top_heavy = sorted(
        grouped.values(),
        key=lambda item: (
            float(item["total_elapsed_ms"]),
            float(item["max_elapsed_ms"]),
        ),
        reverse=True,
    )[:8]
    return {
        "llm_call_count": len(events),
        "llm_success_count": sum(
            1 for item in events if bool(item.get("success", False))
        ),
        "llm_error_count": sum(
            1 for item in events if not bool(item.get("success", False))
        ),
        "timeout_like_error_count": timeout_like_count,
        "retried_call_count": sum(
            1 for item in events if bool(item.get("retried", False))
        ),
        "total_elapsed_ms": round(
            sum(float(item.get("elapsed_ms", 0.0) or 0.0) for item in events), 2
        ),
        "max_prompt_char_count": max(
            (int(item.get("prompt_char_count", 0) or 0) for item in events), default=0
        ),
        "top_heavy_operations": [
            {
                **item,
                "total_elapsed_ms": round(float(item["total_elapsed_ms"]), 2),
                "max_elapsed_ms": round(float(item["max_elapsed_ms"]), 2),
            }
            for item in top_heavy
        ],
    }


def llm_operation_key(event: dict[str, Any]) -> str:
    parts = [
        str(event.get("component", "")).strip(),
        str(event.get("role", "")).strip(),
        str(event.get("agent_id", "")).strip(),
        str(event.get("stage", "")).strip(),
        str(event.get("operation", "")).strip(),
        str(event.get("request_kind", "")).strip(),
    ]
    normalized = [item for item in parts if item]
    return "/".join(normalized) or "unknown"
