from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import ownpi.stage_ids as stage_ids


class Stage(StrEnum):
    DIRECT_QA = stage_ids.DIRECT_QA
    LITERATURE_GROUNDING = stage_ids.LITERATURE_GROUNDING
    RESEARCH_FRAMING = stage_ids.RESEARCH_FRAMING
    METHOD_DESIGN = stage_ids.METHOD_DESIGN
    EXPERIMENT_DESIGN = stage_ids.EXPERIMENT_DESIGN
    IMPLEMENTATION_EXECUTION = stage_ids.IMPLEMENTATION_EXECUTION
    ANALYSIS_DECISION = stage_ids.ANALYSIS_DECISION
    OUTPUT_SYNTHESIS = stage_ids.OUTPUT_SYNTHESIS

    @classmethod
    def _missing_(cls, value: object) -> 'Stage | None':
        canonical = stage_ids.canonical_stage_id(value)
        if canonical != str(value or '').strip():
            return cls(canonical)
        return None


class GateDecision(StrEnum):
    ACCEPT = "accept"
    ADVANCE_WITH_RESIDUAL_FINDINGS = "advance_with_residual_findings"
    CLARIFY_HUMAN = "clarify_human"
    FAIL = "fail"
    REVISIT_LITERATURE_GROUNDING = "revisit_literature_grounding"
    REVISIT_IMPLEMENTATION_EXECUTION = "revisit_implementation_execution"
    REVISIT_EXPERIMENT_DESIGN = "revisit_experiment_design"
    REVISIT_METHOD_DESIGN = "revisit_method_design"
    REVISIT_RESEARCH_FRAMING = "revisit_research_framing"

    @classmethod
    def _missing_(cls, value: object) -> 'GateDecision | None':
        canonical = stage_ids.canonical_gate_decision_id(value)
        if canonical != str(value or '').strip():
            return cls(canonical)
        return None


COMPLETED_GATE_DECISIONS = frozenset(
    {
        GateDecision.ACCEPT,
        GateDecision.ADVANCE_WITH_RESIDUAL_FINDINGS,
    }
)


def is_completed_gate_decision(value: str | GateDecision | None) -> bool:
    return gate_decision_from_value(value) in COMPLETED_GATE_DECISIONS


ACTIVE_STAGE_ORDER: tuple[Stage, ...] = (
    Stage.LITERATURE_GROUNDING,
    Stage.RESEARCH_FRAMING,
    Stage.METHOD_DESIGN,
    Stage.EXPERIMENT_DESIGN,
    Stage.IMPLEMENTATION_EXECUTION,
    Stage.ANALYSIS_DECISION,
)

ORDINARY_STAGES: tuple[Stage, ...] = (
    Stage.LITERATURE_GROUNDING,
    Stage.RESEARCH_FRAMING,
    Stage.METHOD_DESIGN,
    Stage.EXPERIMENT_DESIGN,
    Stage.IMPLEMENTATION_EXECUTION,
    Stage.OUTPUT_SYNTHESIS,
)

STAGE_ORDER: tuple[Stage, ...] = ACTIVE_STAGE_ORDER


_ANALYSIS_REVISIT_ROUTES: tuple[tuple[Stage, GateDecision], ...] = (
    (Stage.LITERATURE_GROUNDING, GateDecision.REVISIT_LITERATURE_GROUNDING),
    (Stage.RESEARCH_FRAMING, GateDecision.REVISIT_RESEARCH_FRAMING),
    (Stage.METHOD_DESIGN, GateDecision.REVISIT_METHOD_DESIGN),
    (Stage.EXPERIMENT_DESIGN, GateDecision.REVISIT_EXPERIMENT_DESIGN),
    (
        Stage.IMPLEMENTATION_EXECUTION,
        GateDecision.REVISIT_IMPLEMENTATION_EXECUTION,
    ),
)


def analysis_revisit_targets() -> tuple[Stage, ...]:
    return tuple(stage for stage, _ in _ANALYSIS_REVISIT_ROUTES)


def analysis_revisit_decision_for_target(
    target: Stage | str,
) -> GateDecision | None:
    try:
        normalized = stage_from_value(target)
    except ValueError:
        return None
    return next(
        (
            decision
            for stage, decision in _ANALYSIS_REVISIT_ROUTES
            if stage == normalized
        ),
        None,
    )


def analysis_revisit_target_for_decision(
    decision: GateDecision | str,
) -> Stage | None:
    try:
        normalized = (
            decision if isinstance(decision, GateDecision) else GateDecision(str(decision))
        )
    except ValueError:
        return None
    return next(
        (
            stage
            for stage, route_decision in _ANALYSIS_REVISIT_ROUTES
            if route_decision == normalized
        ),
        None,
    )


@dataclass(frozen=True)
class TransitionOutcome:
    decision: GateDecision
    current_stage: Stage
    next_stage: Stage | None
    terminal_status: str | None
    limit_exceeded_reason: str | None = None


def stage_from_value(raw: str | Stage) -> Stage:
    if isinstance(raw, Stage):
        return raw
    return Stage(stage_ids.canonical_stage_id(raw))


def gate_decision_from_value(raw: str | GateDecision | None) -> GateDecision:
    if isinstance(raw, GateDecision):
        return raw
    value = stage_ids.canonical_gate_decision_id(raw or GateDecision.FAIL.value)
    try:
        return GateDecision(value)
    except ValueError:
        return GateDecision.FAIL


def artifact_kind_for_stage(stage: Stage) -> str:
    from ownpi.controller.stage_artifact_registry import (
        artifact_kind_for_stage as _artifact_kind_for_stage,
    )

    return _artifact_kind_for_stage(stage)
