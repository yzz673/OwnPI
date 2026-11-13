from __future__ import annotations

from pathlib import Path
from typing import Any

from ownpi import __version__
from ownpi.app.paths import AppPaths
from ownpi.runtime.context.entry import RuntimeEntryContext
from ownpi.runtime.context.prefetch import consume_context_prefetch
from ownpi.runtime.dependency_paths import canonical_managed_dependency_path
from ownpi.runtime.environment import default_environment_config
from ownpi.runtime.contracts import (
    AgentInput,
    AgentSemanticContext,
    CriticView,
    ToolContext,
    WorkflowView,
)
from ownpi.runtime.permissions.runtime_config import compile_runtime_permissions
from ownpi.runtime.events import now_utc_iso
from ownpi.runtime.memory.map_builder import build_runtime_memory_map
from ownpi.runtime.memory.map_contracts import MemoryMapSection
from ownpi.runtime.memory.internal.map_contributor import (
    internal_memory_repository_snapshot,
)
from ownpi.runtime.native_tools.loaded_tools import (
    NativeLoadedToolRefV1,
    verified_native_loaded_tool_refs,
)
from ownpi.runtime.state_models import (
    ArtifactState,
    calculate_total_stage_attempts,
    ContextState,
    EnvironmentState,
    MemoryState,
    RemoteState,
    FeedbackState,
    RunMetadata,
    RuntimePreferences,
    RuntimeState,
    ToolState,
    WorkflowState,
)
from ownpi.runtime.task_context import (
    RuntimeTaskContext,
    read_runtime_raw_user_message,
    read_runtime_task_spec,
)
from ownpi.runtime.tools.planning import (
    ToolEnvironmentSnapshot,
    ToolPlanContext,
    allow_human_question_from_runtime_state,
)
from ownpi.runtime.tools.router import ToolRouter
from ownpi.static_semantic_contracts import SUPPORTED_STAGES, resolve_stage_contract
from ownpi.schemas.runtime import AgentInstanceConfig, LLMConfig, UserConfig
from ownpi.schemas.runtime_tooling import ToolABI, ToolDescriptorSummary
from ownpi.schemas.workflow_plan import (
    ExecutionTier,
    RunWorkflowPlan,
    StageRuntimeProfile,
)
from ownpi.utils.language import detect_response_language


def build_runtime_preferences(
    user_config: UserConfig,
    raw_user_message: str,
    *,
    stage_runtime_profile: StageRuntimeProfile = StageRuntimeProfile.TASK_LEVEL,
    ui_mode: str = 'user',
    app_paths: Any | None = None,
) -> RuntimePreferences:
    from ownpi.runtime.limits.workflow import resolve_review_policy

    llm_config = user_config.get_llm_config()
    effective_review_policy = resolve_review_policy(
        user_config.runtime.review_profile,
        stage_runtime_profile=stage_runtime_profile,
    )
    return RuntimePreferences(
        response_language=detect_response_language(raw_user_message),
        ui_mode=ui_mode,
        review_profile=effective_review_policy.selected_profile.value,
        developer_verbosity='detailed' if ui_mode == 'developer' else 'normal',
        timeout_policy={'request_timeout_seconds': llm_config.request_timeout_seconds},
        interaction=user_config.runtime.interaction.to_dict(),
        native_conversation=user_config.runtime.native_conversation.to_dict(),
        context_meter={
            'enabled': bool(user_config.runtime.ui.context_meter_enabled),
            'mode': str(user_config.runtime.ui.context_meter_mode),
            'width': int(user_config.runtime.ui.context_meter_width),
            'show_threshold': bool(user_config.runtime.ui.context_meter_show_threshold),
        },
        internal_memory={
            **user_config.runtime.internal_memory.to_dict(),
            'root': (
                str(app_paths.internal_memory_root)
                if app_paths is not None and hasattr(app_paths, 'internal_memory_root')
                else ''
            ),
        },
        external_knowledge=user_config.runtime.external_knowledge.to_dict(),
        llm_profiles={name: cfg.to_dict() for name, cfg in user_config.llms.items()},
    )


def build_initial_runtime_state(
    *,
    task_context: RuntimeTaskContext | None,
    project_workspace_root: str,
    run_workflow_plan: RunWorkflowPlan | None,
    user_config: UserConfig,
    session_id: str,
    task_id: str,
    run_id: str,
    execution_tier: ExecutionTier = ExecutionTier.FULL,
    stage_runtime_profile: StageRuntimeProfile = StageRuntimeProfile.RESEARCH_LEVEL,
    ui_mode: str = 'user',
    entry_mode: str = 'workflow',
    app_paths: Any | None = None,
) -> RuntimeState:
    timestamp = now_utc_iso()
    resolved_app_paths = app_paths or AppPaths.discover()
    managed_venv_path = canonical_managed_dependency_path(
        resolved_app_paths.managed_venv_path,
        label='managed venv path',
    )
    managed_non_python_dependency_root = canonical_managed_dependency_path(
        resolved_app_paths.managed_non_python_dependency_root,
        label='managed non-Python dependency root',
    )
    state = RuntimeState(
        task_context=task_context,
        project_workspace_root=project_workspace_root,
        managed_venv_path=managed_venv_path,
        managed_non_python_dependency_root=managed_non_python_dependency_root,
        workflow_state=WorkflowState(
            execution_tier=execution_tier,
            stage_runtime_profile=stage_runtime_profile,
            run_workflow_plan=run_workflow_plan,
            current_stage=(
                run_workflow_plan.entry_stage
                if run_workflow_plan is not None
                else None
            ),
        ),
        feedback_state=FeedbackState(),
            context_state=ContextState(),
            tool_state=ToolState(
                effective_permissions=compile_runtime_permissions(
                user_config.runtime.permissions,
                project_root=Path(project_workspace_root),
            ),
        ),
        remote_state=RemoteState(),
        memory_state=MemoryState(),
        artifact_state=ArtifactState(),
        environment_state=EnvironmentState(
            config_snapshot=(user_config.runtime.environment.to_dict() if hasattr(user_config.runtime, 'environment') else default_environment_config()),
            default_provider=(getattr(user_config.runtime.environment, 'default_provider', 'host_local') if hasattr(user_config.runtime, 'environment') else 'host_local'),
        ),
        runtime_preferences=build_runtime_preferences(
            user_config,
            task_context.read_raw_user_message() if task_context is not None else '',
            stage_runtime_profile=stage_runtime_profile,
            ui_mode=ui_mode,
            app_paths=app_paths,
        ),
        run_metadata=RunMetadata(
            run_id=run_id,
            created_at=timestamp,
            updated_at=timestamp,
            engine_version=__version__,
            session_id=session_id,
            task_id=task_id,
            entry_mode=entry_mode,
        ),
    )
    state.mcp_runtime_state.configured_servers = [
        item.to_dict() for item in user_config.runtime.mcp_servers
    ]
    return state


def _native_loaded_tool_refs(state: RuntimeState) -> tuple[NativeLoadedToolRefV1, ...]:
    refs: list[NativeLoadedToolRefV1] = []
    seen: set[tuple[str, str]] = set()
    for payload in verified_native_loaded_tool_refs(
        state.tool_state,
        stage=state.workflow_state.current_stage.value,
        attempt=state.workflow_state.current_stage_attempt,
    ):
        if not isinstance(payload, dict):
            continue
        canonical_name = str(payload.get('canonical_name', '') or '').strip()
        spec_digest = str(payload.get('spec_digest', '') or '').strip()
        search_receipt_ref = str(payload.get('search_receipt_ref', '') or '').strip()
        identity = (canonical_name.casefold(), spec_digest)
        if not canonical_name or not spec_digest or not search_receipt_ref or identity in seen:
            continue
        seen.add(identity)
        refs.append(
            NativeLoadedToolRefV1(
                canonical_name=canonical_name,
                spec_digest=spec_digest,
                search_receipt_ref=search_receipt_ref,
            )
        )
    return tuple(refs)


def build_agent_input(
    state: RuntimeState,
    agent_config: AgentInstanceConfig,
    *,
    operation: str = 'core_stage_round',
    project_root: Path | None = None,
    state_store: Any | None = None,
    app_paths: Any | None = None,
    internal_memory_map_contribution: MemoryMapSection | None = None,
    external_memory_map_contribution: MemoryMapSection | None = None,
    runtime_entry_context: RuntimeEntryContext | None = None,
    current_request: str | None = None,
) -> AgentInput:
    stage = state.workflow_state.current_stage.value
    attempt = state.workflow_state.current_stage_attempt
    consume_context_prefetch(state, max_wait_ms=0, state_store=state_store)
    if operation in {'direct_critic_core', 'direct_critic_reviewer'}:
        if current_request is None:
            raise ValueError('direct critic requires an in-memory current request')
        raw_user_message, task_spec_text = current_request, ''
    else:
        raw_user_message = read_runtime_raw_user_message(state)
        task_spec_text = read_runtime_task_spec(state)
    internal_memory_snapshot = internal_memory_repository_snapshot(state)
    base_tool_router = _base_tool_router_for_state(
        state,
        internal_memory_snapshot=internal_memory_snapshot,
    )
    available_tools, visible_tools = _materialize_session_tool_surface_snapshot(
        base_tool_router
    )
    memory_map = build_runtime_memory_map(
        state=state,
        agent_id=agent_config.id,
        role=agent_config.role,
        operation=operation,
        tool_router=base_tool_router,
        internal=internal_memory_map_contribution,
        external=external_memory_map_contribution,
        internal_memory_snapshot=internal_memory_snapshot,
    )
    active_handoff = state.stage_prep_state.active_handoff
    stage_contract = ''
    if state.workflow_state.current_stage in SUPPORTED_STAGES:
        if active_handoff is None:
            raise ValueError('staged AgentInput requires an active Stage Prep handoff')
        active_prep_record = state.stage_prep_state.active_record()
        if (
            active_prep_record is None
            or active_prep_record.stage != stage
            or active_prep_record.attempt != attempt
        ):
            raise ValueError('active Stage Prep handoff does not match the current stage attempt')
        stage_contract = resolve_stage_contract(
            state.workflow_state.current_stage,
            state.workflow_state.stage_runtime_profile,
        ).stage_contract
    active_feedback_record_id = (
        state.feedback_state.critic_loop.feedback_record_id
        or state.feedback_state.active_handoff.record_id
        or state.feedback_state.analysis_transition.feedback_record_id
    )
    current_feedback_ref = state.feedback_state.record_ref(active_feedback_record_id)
    workflow_view = WorkflowView(
        execution_tier=state.workflow_state.execution_tier,
        current_stage=state.workflow_state.current_stage,
        current_stage_attempt=state.workflow_state.current_stage_attempt,
        run_workflow_plan=state.workflow_state.run_workflow_plan,
        pending_human_clarification=(
            state.workflow_state.pending_human_clarification.to_dict()
            if state.workflow_state.pending_human_clarification is not None
            else None
        ),
        pending_tool_permission=(
            state.workflow_state.pending_tool_permission.to_dict()
            if state.workflow_state.pending_tool_permission is not None
            else None
        ),
        revisit_context=state.workflow_state.revisit_state.current_context_for(
            stage=state.workflow_state.current_stage,
            attempt=state.workflow_state.current_stage_attempt,
        ),
        stage_runtime_profile=state.workflow_state.stage_runtime_profile,
        relevant_history=list(state.context_state.recent_workflow_events[-8:]),
    )
    critic_view = CriticView(
        latest_feedback_record=(
            current_feedback_ref.to_dict()
            if current_feedback_ref is not None
            else None
        ),
        critic_loop=state.feedback_state.critic_loop.to_dict(),
        total_stage_attempts=calculate_total_stage_attempts(state.workflow_state),
    )
    active_tool_abis = _select_active_tool_abis(
        state=state, stage=stage, catalog_names={item.name for item in available_tools},
    )
    tool_context = ToolContext(
        available_tools=available_tools,
        active_tool_abis=tuple(active_tool_abis),
        native_loaded_tool_refs=_native_loaded_tool_refs(state),
        selection_summary=_build_base_tool_selection_summary(
            available_tools=available_tools,
            visible_tools=visible_tools,
        ),
        effective_permissions=state.tool_state.effective_permissions,
        active_remote_host=state.remote_state.active_remote_host,
        router=base_tool_router,
    )
    return AgentInput(
        role=agent_config.role,
        agent_id=agent_config.id,
        project_workspace_root=state.project_workspace_root,
        managed_venv_path=state.managed_venv_path,
        managed_non_python_dependency_root=state.managed_non_python_dependency_root,
        semantic_context=AgentSemanticContext(
            raw_user_message=raw_user_message,
            task_spec=task_spec_text,
            stage_contract=stage_contract,
            source_roles={
                'raw_user_message': 'authoritative original user intent',
                'task_spec': 'concise task-scoped fixed facts and bindings',
                'stage_contract': 'canonical contract for the current stage' if stage_contract else '',
            },
        ),
        workflow_view=workflow_view,
        critic_view=critic_view,
        tool_context=tool_context,
        operation=operation,
        memory_map=memory_map,
        runtime_preferences=state.runtime_preferences,
        run_metadata=state.run_metadata,
        environment_state=state.environment_state,
        remote_state=state.remote_state,
        artifact_state=state.artifact_state,
        options=dict(agent_config.options),
    )


def build_effective_llm_snapshot(llm_config: LLMConfig) -> dict[str, Any]:
    from ownpi.runtime.native_tools.hosted_web_search import (
        HostedWebSearchOptions,
        select_web_search_execution_mode,
    )

    web_search_mode = select_web_search_execution_mode(
        supports_hosted_web_search=(
            llm_config.supports_hosted_web_search
            and (
                llm_config.api_style != 'anthropic_messages'
                or llm_config.web_search_mode == 'live'
            )
        ),
        web_search_mode=llm_config.web_search_mode,
    )
    hosted_options = HostedWebSearchOptions(
        mode=llm_config.web_search_mode,
        context_size=llm_config.web_search_context_size,
        allowed_domains=tuple(llm_config.web_search_allowed_domains),
        user_location=tuple(llm_config.web_search_user_location),
        search_content_types=tuple(llm_config.web_search_content_types),
        max_uses=llm_config.web_search_max_uses,
    )
    return {
        'provider': llm_config.provider,
        'model': llm_config.model,
        'reasoning_effort': llm_config.reasoning_effort,
        'api_style': llm_config.api_style,
        'web_search_execution_mode': web_search_mode.value,
        'hosted_web_search_spec_digest': (
            (
                hosted_options.anthropic_digest
                if llm_config.api_style == 'anthropic_messages'
                else hosted_options.digest
            )
            if web_search_mode.value == 'hosted'
            else ''
        ),
    }


def apply_temporary_llm_overrides(
    llm_config: LLMConfig,
    *,
    temporary_model: Any,
    temporary_reasoning: Any,
) -> LLMConfig:
    model = str(temporary_model or '').strip()
    reasoning = str(temporary_reasoning or '').strip()
    if not model and not reasoning:
        return llm_config
    payload = llm_config.to_dict()
    if model:
        payload['model'] = model
    if reasoning:
        payload['reasoning_effort'] = reasoning
    return LLMConfig.from_dict(payload)


def _materialize_session_tool_surface_snapshot(
    router: ToolRouter,
) -> tuple[list[ToolDescriptorSummary], list[ToolDescriptorSummary]]:
    available_tools = [definition.to_summary() for definition in router.registry.definitions]
    visible_names = set(router.visible_names)
    visible_tools = [
        definition.to_summary()
        for definition in router.registry.definitions
        if definition.name in visible_names
    ]
    return available_tools, visible_tools


def _base_tool_router_for_state(
    state: RuntimeState,
    *,
    internal_memory_snapshot: Any | None = None,
) -> ToolRouter:
    return ToolRouter.from_context(
        ToolPlanContext.from_runtime_state(
            state,
            allow_human_question=_allow_human_question(state),
            environment=ToolEnvironmentSnapshot.from_runtime_state(state),
            internal_memory_snapshot=internal_memory_snapshot,
        )
    )


def _allow_human_question(state: RuntimeState) -> bool:
    return allow_human_question_from_runtime_state(state)


def _select_active_tool_abis(
    *,
    state: RuntimeState,
    stage: str,
    catalog_names: set[str],
) -> list[ToolABI]:
    active: dict[str, ToolABI] = {}
    router = ToolRouter.from_context(
        ToolPlanContext.from_runtime_state(
            state,
            allow_human_question=_allow_human_question(state),
            environment=ToolEnvironmentSnapshot.from_runtime_state(state),
        )
    )
    visible_names = set(router.visible_names)
    for definition in router.registry.definitions:
        if definition.name not in visible_names:
            continue
        if catalog_names and definition.name not in catalog_names:
            continue
        active[definition.name] = definition.tool_abi()
    search_entries = {
        (entry.canonical_name.casefold(), entry.digest): entry
        for entry in router.deferred_search_entries
    }
    for ref in verified_native_loaded_tool_refs(
        state.tool_state,
        stage=stage,
        attempt=state.workflow_state.current_stage_attempt,
    ):
        if not isinstance(ref, dict):
            continue
        canonical_name = str(ref.get('canonical_name', '') or '').strip()
        spec_digest = str(ref.get('spec_digest', '') or '').strip()
        entry = search_entries.get((canonical_name.casefold(), spec_digest))
        if entry is None or (catalog_names and canonical_name not in catalog_names):
            continue
        spec = entry.spec
        active[canonical_name] = ToolABI(
            tool_name=spec.name,
            input_schema=dict(spec.input_schema),
            description=spec.description,
        )
    return list(active.values())


def _build_base_tool_selection_summary(
    *,
    available_tools: list[ToolDescriptorSummary],
    visible_tools: list[ToolDescriptorSummary],
) -> str:
    return (
        f'registered={len(available_tools)} visible={len(visible_tools)} '
        f'deferred={max(len(available_tools) - len(visible_tools), 0)}'
    )
