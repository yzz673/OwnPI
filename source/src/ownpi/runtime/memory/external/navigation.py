from __future__ import annotations

from collections.abc import Iterable

from ownpi.runtime.tools.definitions import ToolDefinition


EXTERNAL_KNOWLEDGE_SEARCH_TOOL = "external_knowledge_search"
EXTERNAL_KNOWLEDGE_READ_TOOL = "external_knowledge_read"
EXTERNAL_KNOWLEDGE_RESULT_FAMILY = "external_knowledge"

EXTERNAL_KNOWLEDGE_SCOPES = (
    "method_knowledge.methods",
    "method_knowledge.analysis_design",
    "method_knowledge.controls",
    "method_knowledge.failure_patterns",
    "research_strategy.novelty",
    "research_strategy.evidence_strength",
    "research_strategy.claim_boundaries",
    "research_strategy.scientific_organization",
)

EXTERNAL_KNOWLEDGE_HIERARCHY_GUIDE = (
    "Search returns candidate stable refs. Read one candidate, inspect its body, "
    "content_kind, content_lineage, and related_refs, then read a relevant next-level "
    "ref or request a returned relation with cursor pagination only when more evidence "
    "is needed. Method Knowledge traverses subtarget -> knowledge/pattern -> "
    "pattern_case_relevance -> canonical_case. Research Strategy traverses dimension -> "
    "strategy -> contributed_to_strategy_synthesis, with paper-support relations where "
    "returned. Stop at any level once the current decision has sufficient evidence."
)


def external_knowledge_definitions(
    definitions: Iterable[ToolDefinition],
) -> tuple[ToolDefinition, ...]:
    return tuple(
        definition
        for definition in definitions
        if definition.result_family == EXTERNAL_KNOWLEDGE_RESULT_FAMILY
    )


def has_canonical_external_knowledge_tools(
    definitions: Iterable[ToolDefinition],
) -> bool:
    by_name = {
        definition.name: definition
        for definition in external_knowledge_definitions(definitions)
    }
    return all(
        name in by_name and by_name[name].read_only
        for name in (
            EXTERNAL_KNOWLEDGE_SEARCH_TOOL,
            EXTERNAL_KNOWLEDGE_READ_TOOL,
        )
    )


def render_external_knowledge_hierarchy_guide() -> str:
    return f"  hierarchy: {EXTERNAL_KNOWLEDGE_HIERARCHY_GUIDE}"


__all__ = [
    "EXTERNAL_KNOWLEDGE_HIERARCHY_GUIDE",
    "EXTERNAL_KNOWLEDGE_READ_TOOL",
    "EXTERNAL_KNOWLEDGE_RESULT_FAMILY",
    "EXTERNAL_KNOWLEDGE_SCOPES",
    "EXTERNAL_KNOWLEDGE_SEARCH_TOOL",
    "external_knowledge_definitions",
    "has_canonical_external_knowledge_tools",
    "render_external_knowledge_hierarchy_guide",
]
