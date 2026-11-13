from __future__ import annotations

DIRECT_QA = 'direct_qa'
LITERATURE_GROUNDING = 'literature_grounding'
RESEARCH_FRAMING = 'research_framing'
METHOD_DESIGN = 'method_design'
EXPERIMENT_DESIGN = 'experiment_design'
IMPLEMENTATION_EXECUTION = 'implementation_execution'
ANALYSIS_DECISION = 'analysis_decision'
OUTPUT_SYNTHESIS = 'output_synthesis'

ACTIVE_STAGE_IDS: tuple[str, ...] = (
    LITERATURE_GROUNDING,
    RESEARCH_FRAMING,
    METHOD_DESIGN,
    EXPERIMENT_DESIGN,
    IMPLEMENTATION_EXECUTION,
    ANALYSIS_DECISION,
)

ALL_STAGE_IDS: tuple[str, ...] = (
    DIRECT_QA,
    *ACTIVE_STAGE_IDS,
    OUTPUT_SYNTHESIS,
)

ALL_RUNTIME_STAGE_IDS: tuple[str, ...] = ALL_STAGE_IDS


def canonical_stage_id(value: object) -> str:
    return str(value or '').strip()


def canonical_gate_decision_id(value: object) -> str:
    return str(value or '').strip()
