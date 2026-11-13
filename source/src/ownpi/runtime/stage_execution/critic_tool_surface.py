from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ownpi.runtime.memory.map_builder import (
    build_runtime_memory_map,
)
from ownpi.runtime.memory.map_contracts import MemoryMap
from ownpi.runtime.native_tools.hosted_web_search import WebSearchExecutionMode
from ownpi.runtime.stage_execution.critic_profile import StageCriticRoleProfile
from ownpi.runtime.tools.definitions import ToolDefinition
from ownpi.runtime.tools.planning import (
    PlannedTools,
    ToolEnvironmentSnapshot,
    ToolPlanContext,
    allow_human_question_from_runtime_state,
    plan_tools,
)
from ownpi.runtime.tools.router import ToolRouter


@dataclass(frozen=True)
class CriticToolSurface:
    tool_router: ToolRouter
    memory_map: MemoryMap | None


def build_critic_tool_surface(
    *,
    state: Any,
    profile: StageCriticRoleProfile,
    agent_id: str,
    scoped_definitions: tuple[ToolDefinition, ...],
    base_memory_map: MemoryMap | None,
    supports_image_input: bool,
    web_search_execution_mode: WebSearchExecutionMode = WebSearchExecutionMode.LOCAL_FALLBACK,
) -> CriticToolSurface:
    planned = plan_tools(
        ToolPlanContext.from_runtime_state(
            state,
            tool_search_enabled=True,
            supports_image_input=supports_image_input,
            allow_human_question=allow_human_question_from_runtime_state(state),
            web_search_execution_mode=web_search_execution_mode,
            actor_kind=profile.actor_kind,
            scoped_definitions=scoped_definitions,
            environment=ToolEnvironmentSnapshot.from_runtime_state(state),
        )
    )
    definitions = profile.filter_definitions(planned.definitions)
    tool_router = ToolRouter.build(
        PlannedTools(
            definitions=definitions,
            hosted_specs=planned.hosted_specs,
            disabled_reasons=planned.disabled_reasons,
        ),
        tool_search_enabled=True,
    )
    prior_sections = base_memory_map.sections if base_memory_map is not None else ()
    internal_contribution = prior_sections[0] if prior_sections else None
    memory_map = build_runtime_memory_map(
        state=state,
        agent_id=agent_id,
        role=profile.actor_kind,
        operation=f"{profile.actor_kind}_review",
        tool_router=tool_router,
        internal=internal_contribution,
    )
    return CriticToolSurface(tool_router=tool_router, memory_map=memory_map)


__all__ = ["CriticToolSurface", "build_critic_tool_surface"]
