"""Mechanical mapping from stable contract IDs to packaged Markdown paths."""

from importlib import resources
import re

from ownpi.static_semantic_contracts.taxonomy import (
    CORE_SEMANTIC_LEVELS,
    CORE_SEMANTIC_ROLES,
    role_contract_id,
    stage_contract_id,
)

COMMON_CONTRACT_IDS: tuple[str, ...] = (
    "common.action_boundaries",
    "common.evidence_integrity",
    "common.communication",
    "common.dependency_handling",
)

CONTRACT_PATHS: dict[str, str] = {
    "common.subagent_usage": "content/common/subagent_usage.md",
    "common.action_boundaries": "content/common/action_boundaries.md",
    "common.evidence_integrity": "content/common/evidence_integrity.md",
    "common.communication": "content/common/communication.md",
    "common.dependency_handling": "content/common/dependency_handling.md",
    "workflow.execution_waiting": "content/workflow/execution_waiting.md",
    "workflow.research_view_building": "content/stages/research_level/analysis_decision/substage-001.md",
    "workflow.paper_architecture_and_evidence_planning": "content/stages/research_level/output_synthesis/substage-001.md",
    "workflow.academic_paper_writing": "content/stages/research_level/output_synthesis/substage-001.md",
    "workflow.final_delivery": "content/stages/research_level/output_synthesis/substage-006.md",
    "authoring.task_spec": "content/authoring/task_spec.md",
    "authoring.feedback": "content/authoring/feedback.md",
    "authoring.subagent_feedback": "content/authoring/subagent_feedback.md",
    "runtime_mode.plan_mode": "content/runtime_modes/plan_mode.md",
    "continuity.compaction_and_handoff": (
        "content/continuity/compaction_and_handoff.md"
    ),
}
for _level in CORE_SEMANTIC_LEVELS:
    for _role in CORE_SEMANTIC_ROLES:
        CONTRACT_PATHS[role_contract_id(_role, _level)] = (
            f"content/roles/{_level}/{_role}.md"
        )
for _level in ("research_level",):
    for _stage in (
        "literature_grounding",
        "research_framing",
        "method_design",
        "experiment_design",
        "implementation_execution",
        "analysis_decision",
        "output_synthesis",
    ):
        CONTRACT_PATHS[stage_contract_id(_stage, _level)] = (
            f"content/stages/{_level}/{_stage}/stage.md"
        )

# Discover current substage files from the package. This keeps every substage
# readable through ``contract_read`` without hard-coding the current count.
_SUBSTAGE_PATTERN = re.compile(r"^substage-(\d{3})\.md$")
for _level in ("research_level",):
    for _stage in (
        "literature_grounding", "research_framing", "method_design",
        "experiment_design", "implementation_execution", "analysis_decision",
        "output_synthesis",
    ):
        _directory = resources.files("ownpi.static_semantic_contracts").joinpath(
            "content", "stages", _level, _stage
        )
        _numbers = sorted(
            int(match.group(1))
            for _node in _directory.iterdir()
            if (match := _SUBSTAGE_PATTERN.fullmatch(_node.name)) and _node.is_file()
        )
        if not _numbers:
            continue
        _stage_id = stage_contract_id(_stage, _level)
        CONTRACT_PATHS[_stage_id] = (
            f"content/stages/{_level}/{_stage}/substage-{_numbers[0]:03d}.md"
        )
        for _number in _numbers:
            CONTRACT_PATHS[f"{_stage_id}.substage-{_number:03d}"] = (
                f"content/stages/{_level}/{_stage}/substage-{_number:03d}.md"
            )
