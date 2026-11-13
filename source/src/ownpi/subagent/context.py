"""Assembly boundary for an isolated subagent execution context."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from ownpi.runtime.p_assembly import (
    PAssemblyBindings,
    PAssemblyRequest,
    PAssemblyResult,
    PLoopKind,
    PSourceInputs,
    assemble_native_loop_p,
)
from ownpi.runtime.tools.runtime import ToolRuntimeContext
from ownpi.runtime.tools.role_policy import tool_role_allowed
from ownpi.skills.snapshot import SkillCatalogSnapshot
from ownpi.controller.stages import Stage
from ownpi.schemas.workflow_plan import StageRuntimeProfile


@dataclass
class SubagentLiveSession:
    """In-process conversation ownership across child continuation segments."""

    session: Any = None
    resume_conversation_id: str | None = None


@dataclass(frozen=True)
class SubagentContextBundle:
    """The three context components owned by one subagent child."""

    runtime_context: ToolRuntimeContext
    p: PAssemblyResult
    task_message: str = ''
    continuation_message: str = ''
    live: SubagentLiveSession = field(default_factory=SubagentLiveSession, compare=False)


class SubagentContextAssembler:
    """Reuse Core context machinery with an isolated child scope."""

    def assemble(
        self,
        *,
        core_context: ToolRuntimeContext,
        task_message: str,
        child_state_store: Any,
        child_run_dir: Path,
        child_id: str,
        level: StageRuntimeProfile,
        stage: Stage,
        memory_map: Any,
        tool_router: Any | None = None,
    ) -> SubagentContextBundle:
        message = str(task_message or "").strip()
        if not message:
            raise ValueError("subagent task message must be non-empty")
        if child_state_store is None:
            raise ValueError("subagent child state store is required")
        if not str(child_id or "").strip():
            raise ValueError("subagent child id must be non-empty")
        if memory_map is None:
            raise ValueError("subagent memory map is required")
        run_dir = Path(child_run_dir).expanduser().resolve()
        child_context = replace(
            core_context,
            state_store=child_state_store,
            run_dir=run_dir,
            shared_resource_run_dir=(
                core_context.shared_resource_run_dir or core_context.run_dir
            ),
            actor_kind="subagent",
            actor_id=str(child_id).strip(),
            process_owner_actor_id=f"{child_id}:{uuid4().hex}",
            tool_router=tool_router or core_context.tool_router,
            continuation_index=0,
            rollout_parent_id=core_context.rollout_conversation_id,
            rollout_conversation_id='',
            rollout_history=None,
        )
        from ownpi.subagent.tools import tool_definitions_for_actor

        child_base_definitions = tuple(
            definition
            for definition in child_context.tool_router.planned_definitions
            if tool_role_allowed(definition.name, 'subagent')
            and "tool_search" not in {
                definition.name.casefold(),
                *(alias.casefold() for alias in definition.aliases),
            }
        )
        from ownpi.runtime.tools.planning import PlannedTools

        child_router = child_context.tool_router.build(
            PlannedTools(
                definitions=child_base_definitions,
                hosted_specs=child_context.tool_router.hosted_specs,
                disabled_reasons=child_context.tool_router.disabled_reasons,
            ),
            tool_search_enabled=child_context.tool_router.tool_search_enabled,
        )
        child_context = replace(
            child_context,
            tool_router=child_router.with_scoped_definitions(
                tool_definitions_for_actor("subagent"),
                tool_search_enabled=child_router.tool_search_enabled,
            ),
        )
        skill_snapshot = core_context.skill_catalog_snapshot
        if not isinstance(skill_snapshot, SkillCatalogSnapshot):
            raise TypeError("subagent requires the parent skill catalog snapshot")
        source_inputs = PSourceInputs.from_runtime_state(
            child_state_store.state,
            skill_catalog_snapshot=skill_snapshot,
            tool_router=child_context.tool_router,
            memory_map=memory_map,
        )
        source_inputs = replace(
            source_inputs, task_message=message,
            run_dir=run_dir if source_inputs.task_context is None else source_inputs.run_dir,
        )
        p = assemble_native_loop_p(
            request=PAssemblyRequest(
                loop_kind=PLoopKind.SUBAGENT,
                level=level,
                stage=stage,
                stage_loop_index=int(child_state_store.state.workflow_state.scratchpad.get("stage_loop_index", 1)),
                bindings=PAssemblyBindings.from_runtime_state(
                    child_state_store.state,
                    app_home=str(
                        getattr(core_context.app_paths, "app_home", "") or ""
                    ),
                    current_working_directory=str(run_dir),
                ),
            ),
            source_inputs=source_inputs,
        )
        return SubagentContextBundle(
            runtime_context=child_context,
            p=p,
            task_message=message,
        )


__all__ = ["SubagentContextAssembler", "SubagentContextBundle"]
