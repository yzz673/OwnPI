from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from ownpi.llm.factory import build_llm_client
from ownpi.runtime.context.manager import ContextRuntimeManager
from ownpi.runtime.memory.map_builder import (
    build_runtime_memory_map,
)
from ownpi.runtime.native_tools.executor import NativeToolExecutor
from ownpi.runtime.native_tools.spec import NativeToolSpec
from ownpi.runtime.native_tools.hosted_web_search import WebSearchExecutionMode
from ownpi.runtime.native_tools.loop_engine.contracts import NativeLoopIdentity, NativeLoopInvocation, NativeLoopPaths, NativeLoopServices
from ownpi.runtime.native_tools.loop_engine.provider import (
    NativeProviderTurnRunner, create_client_session,
)
from ownpi.runtime.native_tools.loop_engine.session_bindings import history_session_bindings
from ownpi.runtime.native_tools.history_recording import confirm_visible_file_pages, record_tool_context, record_tool_delivery
from ownpi.runtime.rollout.resume import find_conversation
from ownpi.runtime.rollout.resume_delivery import deliver_interaction_results
from ownpi.schemas.runtime import NativeConversationRuntimeConfig
from ownpi.runtime.native_tools.plan_mode.adapter import PlanModeLoopAdapter
from ownpi.runtime.native_tools.plan_mode.contracts import PlanModeLoopRequest
from ownpi.runtime.native_tools.plan_mode.executor import PlanModeToolExecutor
from ownpi.runtime.native_tools.plan_mode.runtime_loop import PlanModeRuntimeLoop
from ownpi.runtime.native_tools.plan_mode.stage_entry import StageEntryPlanRequest
from ownpi.runtime.p_assembly import (
    PAssemblyBindings,
    PAssemblyRequest,
    PLoopKind,
    PSourceInputs,
    assemble_native_loop_p,
)
from ownpi.runtime.tools.planning import (
    ToolEnvironmentSnapshot,
    ToolPlanContext,
    allow_human_question_from_runtime_state,
    plan_tools,
)
from ownpi.runtime.tools.router import ToolRouter
from ownpi.runtime.tools.runtime import ToolRuntimeContext
from ownpi.skills.snapshot import build_skill_catalog_snapshot


@dataclass(frozen=True)
class PlanModeStageEntryDependencies:
    user_config: Any
    tool_runtime: Any
    hook_engine: Any
    app_paths: Any
    process_owner_manager: Any


def build_plan_mode_stage_entry_loop(
    *,
    request: StageEntryPlanRequest,
    dependencies: PlanModeStageEntryDependencies,
) -> PlanModeRuntimeLoop:
    user_config = dependencies.user_config
    profile_name = user_config.selected_llm_profile or user_config.default_llm_name
    config = user_config.get_llm_config(profile_name)
    client = build_llm_client(config)
    capabilities = getattr(client, "native_tool_provider_capabilities", None)
    web_mode = getattr(capabilities, "web_search_execution_mode", "local_fallback")
    plan_context = ToolPlanContext.from_runtime_state(
        request.state_store.state,
        tool_search_enabled=True,
        supports_image_input=bool(getattr(client, "supports_image_input", False)),
        allow_human_question=allow_human_question_from_runtime_state(
            request.state_store.state
        ),
        web_search_execution_mode=WebSearchExecutionMode(
            str(getattr(web_mode, "value", web_mode))
        ),
        actor_kind="plan_mode",
        environment=ToolEnvironmentSnapshot.from_runtime_state(
            request.state_store.state
        ),
    )
    planned = plan_tools(plan_context)
    tool_router = ToolRouter.build(planned, tool_search_enabled=True)
    tool_specs = tool_router.initial_provider_specs
    raw_user_message = request.raw_message_path.read_text(encoding="utf-8")
    task_spec = request.task_spec_path.read_text(encoding="utf-8")
    skill_snapshot = build_skill_catalog_snapshot(
        app_paths=dependencies.app_paths,
        project_root=request.project_root,
    )
    memory_map = build_runtime_memory_map(
        state=request.state_store.state,
        agent_id="plan_mode",
        role="plan_mode",
        operation="plan_mode_stage_entry",
        tool_router=tool_router,
    )
    p = assemble_native_loop_p(
        request=PAssemblyRequest(
            loop_kind=PLoopKind.PLAN_MODE_STAGE_ENTRY,
            level=request.level,
            stage=request.stage,
            bindings=PAssemblyBindings.from_runtime_state(
                request.state_store.state,
                app_home=str(dependencies.app_paths.app_home),
                current_working_directory=str(request.project_root),
            ),
        ),
        source_inputs=PSourceInputs.from_runtime_state(
            request.state_store.state,
            skill_catalog_snapshot=skill_snapshot,
            tool_router=tool_router,
            memory_map=memory_map,
        ),
    )
    tool_context = ToolRuntimeContext(
        state_store=request.state_store,
        raw_user_message=raw_user_message,
        task_spec_text=task_spec,
        project_root=request.project_root,
        run_dir=request.run_dir,
        stage=request.stage,
        attempt=request.attempt,
        query_chain_id=str(
            request.state_store.state.workflow_state.current_query_chain_id or ""
        ),
        query_chain_depth=int(
            request.state_store.state.workflow_state.current_query_chain_depth or 0
        ),
        continuation_index=int(
            request.state_store.state.workflow_state.scratchpad.get(
                "continuation_index", 0
            )
            or 0
        ),
        requested_by="plan_mode",
        resume_read_session=request.resume_read_session,
        user_config=user_config,
        actor_kind="plan_mode",
        actor_id="plan_mode",
        conversation_lane=request.stage.value,
        hook_engine=dependencies.hook_engine,
        app_paths=dependencies.app_paths,
        skill_catalog_snapshot=skill_snapshot,
        execution_runtime_service=request.state_store.execution_runtime_service,
        process_owner_manager=dependencies.process_owner_manager,
        tool_router=tool_router,
        cancel_checker=request.cancel_checker,
    )
    invocation = NativeLoopInvocation(
        services=NativeLoopServices(cancel_checker=request.cancel_checker),
        identity=NativeLoopIdentity(
            session_id=str(request.state_store.state.run_metadata.session_id or ""),
            task_id=str(request.state_store.state.run_metadata.task_id or ""),
            run_id=str(request.state_store.state.run_metadata.run_id or ""),
            actor_kind="plan_mode",
            actor_id="plan_mode",
        ),
        paths=NativeLoopPaths(
            project_root=request.project_root,
            run_dir=request.run_dir,
        ),
    )
    request_kind = f"native_plan_mode_stage_entry:{request.stage.value}"
    executor = PlanModeToolExecutor(
        executor=NativeToolExecutor(
            dependencies.tool_runtime,
            native_tool_strict=client.native_tool_provider_capabilities.native_tool_strict,
        ), context=tool_context,
    )
    metadata = request.state_store.state.run_metadata
    shared = create_client_session(
        client=client, directory=request.run_dir / 'rollouts', messages=list(p.ledger_messages()),
        metadata={'run_id': metadata.run_id, 'session_id': metadata.session_id, 'task_id': metadata.task_id,
                  'actor_kind': 'plan_mode', 'actor_id': 'plan_mode',
                  'stage': request.stage.value, 'attempt': request.attempt, 'workspace': str(request.project_root)},
        bindings=replace(
            history_session_bindings(),
            effective_permissions=lambda: executor.context.canonical_permissions.to_dict(),
            record_tool_delivery=lambda history, result, **kwargs: record_tool_delivery(
                history, result, context=executor.context, **kwargs,
            ),
            record_tool_context=lambda history, **kwargs: record_tool_context(
                history, context=executor.context, **kwargs,
            ),
            confirm_visible_file_pages=lambda items: confirm_visible_file_pages(items, context=executor.context),
        ),
        context_manager=ContextRuntimeManager(state=request.state_store.state, llm_config=config),
        compaction_config=NativeConversationRuntimeConfig.from_dict(
            request.state_store.state.runtime_preferences.native_conversation,
        ).conversation_compaction,
        replay=(find_conversation(request.run_dir, conversation_id=request.resume_conversation_id,
                                  read_session=request.resume_read_session)
                if request.resume_conversation_id else None),
    )
    executor.context = replace(tool_context, rollout_history=shared.history,
                               rollout_conversation_id=shared.history.conversation_id,
                               tool_result_truncation_policy=shared.history.policy)

    def deliver_answers() -> None:
        deliver_interaction_results(shared.history, context=executor.context,
                                    tool_runtime=dependencies.tool_runtime)
        executor.turn_outcomes = ()

    try:
        adapter = PlanModeLoopAdapter(
            session=shared, deliver_answers=deliver_answers,
            request=PlanModeLoopRequest(
                canonical_ledger_messages=p.ledger_messages(), tool_specs=tool_specs,
                invocation=invocation, request_kind=request_kind,
                human_boundary_tool_names=("human_question",),
            ),
            turn_runner=NativeProviderTurnRunner(client=client, request_kind=request_kind),
            executor=executor, execution_boundary=executor.execution_boundary, llm_client=client,
            batch_admission_specs=tuple(
                NativeToolSpec.from_tool_definition(definition)
                for definition in executor.context.tool_router.registry.definitions
            ),
        )
        return PlanModeRuntimeLoop(adapter=adapter)
    except BaseException as error:
        try:
            shared.history.close()
        except BaseException as close_error:
            error.add_note(f'Plan session construction close failed: {close_error!r}')
        raise


__all__ = [
    "PlanModeStageEntryDependencies",
    "build_plan_mode_stage_entry_loop",
]
