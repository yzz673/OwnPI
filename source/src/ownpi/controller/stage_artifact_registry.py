"""Mechanical stage artifact identities used by routing and projections."""

from __future__ import annotations

from ownpi.controller.stages import Stage


_ARTIFACT_KINDS: dict[Stage, str] = {
    Stage.LITERATURE_GROUNDING: "grounding_report",
    Stage.RESEARCH_FRAMING: "research_contract",
    Stage.METHOD_DESIGN: "method_design_bundle",
    Stage.EXPERIMENT_DESIGN: "experiment_plan",
    Stage.IMPLEMENTATION_EXECUTION: "implementation_bundle",
    Stage.ANALYSIS_DECISION: "analysis_feedback_route",
    Stage.OUTPUT_SYNTHESIS: "final_deliverable",
}


def artifact_kind_for_stage(stage: Stage) -> str:
    try:
        return _ARTIFACT_KINDS[stage]
    except KeyError as exc:
        raise ValueError(f"Unsupported stage artifact identity: {stage!r}") from exc


__all__ = ["artifact_kind_for_stage"]
