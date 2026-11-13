"""Resume an admitted child with current tool services, not legacy child state."""

from __future__ import annotations

from typing import Any, Callable

from ownpi.runtime.memory.map_builder import build_runtime_memory_map
from ownpi.runtime.tools.planning import ToolPlanContext
from ownpi.runtime.tools.router import ToolRouter
from ownpi.runtime.tools.runtime import ToolRuntimeContext
from ownpi.skills.snapshot import build_skill_catalog_snapshot

from .factory import SubagentExecutionFactory
from .owner import SubagentOwner


def build_resumed_child_owner(
    facts: Any, *, state_store: Any, project_root: Any, user_config: Any,
    app_paths: Any, llm_client: Any, tool_runtime: Any,
    process_owner_manager: Any = None, cancel_checker: Callable[[], bool] | None = None,
) -> SubagentOwner:
    state = state_store.state
    router = ToolRouter.from_context(ToolPlanContext.from_runtime_state(state))
    context = ToolRuntimeContext(
        state_store=state_store,
        raw_user_message=state.task_context.read_raw_user_message(),
        task_spec_text=state.task_context.read_task_spec(),
        project_root=project_root, run_dir=facts.run_root,
        stage=state.workflow_state.current_stage, attempt=state.workflow_state.current_stage_attempt,
        tool_router=router, user_config=user_config, app_paths=app_paths,
        execution_runtime_service=state_store.execution_runtime_service,
        process_owner_manager=process_owner_manager, cancel_checker=cancel_checker,
        rollout_conversation_id=facts.conversation.metadata['parent_id'],
        resume_read_session=facts.read_session,
        skill_catalog_snapshot=build_skill_catalog_snapshot(app_paths=app_paths, project_root=project_root),
    )
    memory_map = build_runtime_memory_map(
        state=state, agent_id=facts.conversation.metadata['actor_id'],
        role='subagent', operation='subagent', tool_router=router,
    )
    factory = SubagentExecutionFactory(
        parent_context=context, child_root=facts.run_root / 'subagents',
        level=state.workflow_state.stage_runtime_profile, stage=context.stage, memory_map=memory_map,
    )
    owner = factory.build_owner(
        llm_client=llm_client, tool_runtime=tool_runtime, canonical_ledger_messages=[],
        tool_specs=router.initial_provider_specs, rollout_resume=True,
    )
    if facts.agent_relationships is not None:
        owner.install_historical_agents(facts.agent_relationships)
    return owner
