from __future__ import annotations

from dataclasses import dataclass

from ownpi.runtime.memory.map_contracts import MemoryMap
from ownpi.schemas.context import AgentContextTrace
from ownpi.skills.snapshot import SkillCatalogSnapshot


_SUMMARY_ONLY_RATIONALE = (
    'Summary catalogs were sufficient; no expanded skill or memory bodies were loaded.'
)


@dataclass(frozen=True)
class PreparedAgentContext:
    trace: AgentContextTrace
    memory_map: MemoryMap | None = None


def prepare_agent_context(
    *,
    skill_catalog_snapshot: SkillCatalogSnapshot,
    agent_id: str,
    role: str,
    selected_at_stage: str,
    memory_map: MemoryMap | None = None,
) -> PreparedAgentContext:
    """Prepare bounded source diagnostics without assembling provider context."""

    return PreparedAgentContext(
        trace=AgentContextTrace(
            agent_id=agent_id,
            role=role,
            selected_at_stage=selected_at_stage,
            rationale=_SUMMARY_ONLY_RATIONALE,
            available_skill_count=len(skill_catalog_snapshot.skills),
        ),
        memory_map=memory_map,
    )
