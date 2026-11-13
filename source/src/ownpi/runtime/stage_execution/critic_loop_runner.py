from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ownpi.agents.base import AgentRuntimeInvocationContext
from ownpi.agents.context_runtime import prepare_agent_context
from ownpi.runtime.contracts import AgentInput, AgentOutput
from ownpi.runtime.interactions.pause import raise_pending_actor_interaction
from ownpi.runtime.limits.context import resolve_context_budget_limits
from ownpi.runtime.native_tools.core_agent_loop import (
    CoreAgentLoopAdapter,
    CoreAgentLoopContinuation,
)
from ownpi.runtime.native_tools.core_loop_models import (
    NativeToolLoopDomainPolicy,
    NativeToolLoopResult,
)
from ownpi.runtime.native_tools.executor import NativeToolExecutor
from ownpi.runtime.native_tools.hosted_web_search import WebSearchExecutionMode
from ownpi.runtime.native_tools.role_continuation import (
    RoleLoopReceiptSnapshot,
    critic_feedback_continuation_policy,
    evaluate_role_loop_continuation,
)
from ownpi.runtime.p_assembly import (
    PAssemblyBindings,
    PAssemblyRequest,
    PLoopKind,
    PSourceInputs,
    assemble_native_loop_p,
)
from ownpi.runtime.stage_execution.feedback_store import (
    feedback_path,
    load_feedback_record,
    verify_feedback_terminal_receipt,
)
from ownpi.runtime.stage_execution.feedback_tools import (
    SUBMIT_FEEDBACK_TOOL_NAME,
    FeedbackBinding,
    build_submit_feedback_definition,
)
from ownpi.runtime.stage_execution.critic_profile import StageCriticRoleProfile
from ownpi.runtime.stage_execution.critic_tool_surface import (
    build_critic_tool_surface,
)
from ownpi.runtime.tools.runtime import ToolRuntimeContext
from ownpi.schemas.feedback import FeedbackRecord
from ownpi.schemas.runtime import LLMConfig
from ownpi.schemas.feedback import DirectReviewTarget
from ownpi.schemas.workflow_plan import StageRuntimeProfile
from ownpi.skills.snapshot import build_skill_catalog_snapshot

if TYPE_CHECKING:
    from ownpi.runtime.tools.router import ToolRouter


def run_stage_critic_native_loop(
    *,
    invocation_context: AgentRuntimeInvocationContext,
    agent_input: AgentInput,
    agent_id: str,
    llm_client: Any,
    app_paths: Any,
    project_root: Path,
    profile: StageCriticRoleProfile,
) -> tuple[AgentOutput, FeedbackRecord]:
    return _run_feedback_critic_native_loop(
        invocation_context=invocation_context, agent_input=agent_input,
        agent_id=agent_id, llm_client=llm_client, app_paths=app_paths,
        project_root=project_root, profile=profile, direct=False,
    )


def run_direct_critic_native_loop(
    *, invocation_context: AgentRuntimeInvocationContext, agent_input: AgentInput,
    agent_id: str, llm_client: Any, app_paths: Any, project_root: Path,
    profile: StageCriticRoleProfile,
) -> tuple[AgentOutput, FeedbackRecord]:
    return _run_feedback_critic_native_loop(
        invocation_context=invocation_context, agent_input=agent_input,
        agent_id=agent_id, llm_client=llm_client, app_paths=app_paths,
        project_root=project_root, profile=profile, direct=True,
    )


def _run_feedback_critic_native_loop(
    *, invocation_context: AgentRuntimeInvocationContext, agent_input: AgentInput,
    agent_id: str, llm_client: Any, app_paths: Any, project_root: Path,
    profile: StageCriticRoleProfile, direct: bool,
) -> tuple[AgentOutput, FeedbackRecord]:
    state = invocation_context.state_store.state
    stage = _stage_value(invocation_context.stage)
    attempt = invocation_context.attempt
    workspace_root = Path(agent_input.project_workspace_root)
    if workspace_root != project_root.expanduser().resolve():
        raise RuntimeError("critic project workspace does not match RuntimeState")
    loop = state.feedback_state.critic_loop
    if loop.critic_role != profile.critic_kind or loop.phase != "critic_pending":
        raise RuntimeError(
            f"{profile.critic_kind} native loop requires a pending critic round"
        )

    review_target = loop.review_target
    if review_target is None:
        raise RuntimeError("critic round has no workspace review target")
    if direct != isinstance(review_target, DirectReviewTarget):
        raise RuntimeError('critic operation and review target type differ')
    if (
        review_target.stage != stage
        or review_target.attempt != attempt
        or review_target.run_id != state.run_metadata.run_id
        or review_target.loop_index != int(state.workflow_state.scratchpad.get('stage_loop_index', 1))
    ):
        raise RuntimeError("workspace review target belongs to a different loop")

    canonical_feedback_path = feedback_path(
        run_dir=invocation_context.run_dir,
        source_stage=stage,
        attempt=attempt,
        author_role=profile.critic_kind,
        round_index=loop.round,
        review_target=review_target.identity,
        stage_loop_index=int(state.workflow_state.scratchpad.get('stage_loop_index', 1)),
    )
    binding = FeedbackBinding(
        author_role=profile.critic_kind,
        source_stage=stage,
        attempt=attempt,
        round=loop.round,
        review_target=review_target.identity,
        feedback_path=canonical_feedback_path,
        stage_loop_index=int(state.workflow_state.scratchpad.get('stage_loop_index', 1)),
    )
    terminal_definition = build_submit_feedback_definition(binding)
    scoped_definitions = (terminal_definition,)
    runtime_config = getattr(invocation_context.user_config, "runtime", invocation_context.user_config)
    if bool(getattr(runtime_config, "subagent_enabled", False)):
        from ownpi.subagent.tools import tool_definitions_for_actor

        scoped_definitions += tool_definitions_for_actor(profile.actor_kind)
    provider_capabilities = getattr(llm_client, "native_tool_provider_capabilities", None)
    web_mode = getattr(
        provider_capabilities,
        "web_search_execution_mode",
        WebSearchExecutionMode.LOCAL_FALLBACK,
    )
    critic_surface = build_critic_tool_surface(
        state=state,
        profile=profile,
        agent_id=agent_id,
        scoped_definitions=scoped_definitions,
        base_memory_map=agent_input.memory_map,
        supports_image_input=bool(getattr(llm_client, "supports_image_input", False)),
        web_search_execution_mode=WebSearchExecutionMode(
            str(getattr(web_mode, "value", web_mode))
        ),
    )
    tool_router = critic_surface.tool_router
    if SUBMIT_FEEDBACK_TOOL_NAME not in tool_router.visible_names:
        raise RuntimeError("critic feedback terminal is not visible in the tool router")
    agent_input = replace(agent_input, memory_map=critic_surface.memory_map)
    context_window_tokens = _context_window_tokens(
        invocation_context=invocation_context,
        state=state,
    )

    skill_snapshot = build_skill_catalog_snapshot(
        app_paths=app_paths,
        project_root=workspace_root,
    )
    prepared_context = prepare_agent_context(
        skill_catalog_snapshot=skill_snapshot,
        agent_id=agent_id,
        role=profile.actor_kind,
        selected_at_stage=f"{stage}_{profile.critic_kind}_review",
        memory_map=agent_input.memory_map,
    )
    tool_context = _build_tool_context(
        invocation_context=invocation_context,
        agent_input=agent_input,
        actor_kind=profile.actor_kind,
        actor_id=agent_id,
        project_root=workspace_root,
        app_paths=app_paths,
        tool_router=tool_router,
        conversation_lane=f"{stage}__{profile.critic_kind}_round_{loop.round:03d}",
    )
    tool_context = replace(
        tool_context,
        skill_catalog_snapshot=skill_snapshot,
    )
    source_inputs = PSourceInputs.from_runtime_state(
        state, skill_catalog_snapshot=skill_snapshot, tool_router=tool_router,
        memory_map=critic_surface.memory_map, runtime_config=runtime_config,
    )
    from ownpi.runtime.rollout.direct_submission import read_review_request

    if direct:
        from ownpi.runtime.rollout.direct_submission import read_direct_submission
        from ownpi.runtime.rollout.direct_review_evidence import build_direct_review_evidence
        from ownpi.runtime.rollout.resume_read_session import ResumeReadSession

        with ResumeReadSession() as read_session:
            review_request = read_review_request(
                invocation_context.state_store, invocation_context.run_dir, read_session=read_session,
            )
            submission, identity = read_direct_submission(
                invocation_context.state_store, invocation_context.run_dir,
                read_session=read_session, review_request=review_request,
            )
            evidence = build_direct_review_evidence(
                invocation_context.state_store, invocation_context.run_dir,
                review_request=review_request, read_session=read_session,
                effective_permissions=tool_context.canonical_permissions,
                project_root=tool_context.project_root,
            )
        source_inputs = replace(
            source_inputs, task_message=agent_input.semantic_context.raw_user_message,
            run_dir=invocation_context.run_dir,
            core_submission=submission, core_submission_identity=identity,
            direct_request_context=evidence.request_context,
            direct_request_context_identity=evidence.request_context_identity,
            direct_review_evidence=evidence.review_evidence,
            direct_review_evidence_identity=evidence.review_evidence_identity,
        )
    else:
        review_request = read_review_request(invocation_context.state_store, invocation_context.run_dir)
        from ownpi.runtime.rollout.answer_publication import answer_text

        source_inputs = replace(
            source_inputs, submitted_answer=answer_text(review_request.arguments),
            submitted_answer_identity=(
                f"submitted-answer:{review_request.source['path']}:{review_request.source['item_id']}"
            ),
        )
    source_inputs = replace(
        source_inputs, review_context=review_request.arguments.get('review_context') or '',
        review_context_identity=(
            f"review-context:{review_request.source['path']}:{review_request.source['item_id']}"
        ),
    )
    p = assemble_native_loop_p(
        request=PAssemblyRequest(
            loop_kind=(
                PLoopKind.DIRECT_CRITIC_REVIEWER if direct else PLoopKind.REVIEWER
            ),
            level=StageRuntimeProfile.TASK_LEVEL if direct else agent_input.workflow_view.stage_runtime_profile,
            stage=None if direct else invocation_context.stage,
            stage_loop_index=int(state.workflow_state.scratchpad.get('stage_loop_index', 1)),
            bindings=PAssemblyBindings.from_runtime_state(
                state,
                app_home=str(app_paths.app_home),
                current_working_directory=str(workspace_root),
                context_window_tokens=context_window_tokens,
            ),
        ),
        source_inputs=source_inputs,
    )
    tool_runtime = invocation_context.tool_runtime
    if tool_runtime is None:
        raise RuntimeError(f"{profile.critic_kind} native loop requires a tool runtime")
    if bool(getattr(runtime_config, "subagent_enabled", False)):
        from ownpi.subagent.caller import prepare_caller

        tool_context = prepare_caller(
            tool_context, level=agent_input.workflow_view.stage_runtime_profile,
            memory_map=critic_surface.memory_map, llm_client=llm_client,
            tool_runtime=tool_runtime, canonical_ledger_messages=list(p.ledger_messages()),
        )
    continuation_policy = critic_feedback_continuation_policy()
    nudge = ""
    native_loop_continuation = CoreAgentLoopContinuation(
        identity=(str(state.run_metadata.run_id or ''), stage, str(attempt), agent_id)
    )
    while True:
        tool_context = replace(
            tool_context,
            continuation_index=int(
                state.workflow_state.scratchpad.get("continuation_index", 0) or 0
            ),
        )
        if native_loop_continuation.adapter is None:
            loop_adapter = CoreAgentLoopAdapter.from_llm_client(
                executor=NativeToolExecutor(
                    tool_runtime,
                    native_tool_strict=llm_client.native_tool_provider_capabilities.native_tool_strict,
                ),
                llm_client=llm_client,
                canonical_ledger_messages=list(p.ledger_messages()),
                initial_conversation_messages=(
                    ({"role": "user", "content": f"Runtime continuation:\n{nudge}"},)
                    if nudge
                    else ()
                ),
                request_kind=f"native_{profile.critic_kind}_review:{stage}",
                resume_conversation_id=invocation_context.resume_conversation_id,
                domain_policy=NativeToolLoopDomainPolicy(
                    terminal_tool_names=(SUBMIT_FEEDBACK_TOOL_NAME,),
                ),
            )
            native_loop_continuation.attach(loop_adapter)
        elif nudge:
            native_loop_continuation.append_pending_input(
                ({"role": "user", "content": f"Runtime continuation:\n{nudge}"},)
            )
        try:
            result = native_loop_continuation.run(
                context=tool_context,
                tool_specs=tool_router.initial_provider_specs,
            )
        except BaseException:
            native_loop_continuation.close()
            raise
        try:
            from ownpi.runtime.native_tools.loop_outcome import core_outcome
            from ownpi.runtime.interactions.pause import raise_loop_cancellation

            boundary = core_outcome(result)
            if boundary.kind == 'cancelled':
                raise_loop_cancellation(invocation_context.state_store,
                                        stage=invocation_context.stage, attempt=attempt,
                                        index=tool_context.continuation_index)
            if boundary.kind == 'failed':
                raise RuntimeError(f'{profile.critic_kind} native loop failed: '
                                   f'{result.failure_class}: {result.failure_diagnostics}')
            raise_pending_actor_interaction(
                state_store=invocation_context.state_store,
                stage=invocation_context.stage,
                attempt=attempt,
                continuation_index=tool_context.continuation_index,
                step_results=[],
            )
            loop = state.feedback_state.critic_loop
            decision = evaluate_role_loop_continuation(
                policy=continuation_policy,
                receipts=RoleLoopReceiptSnapshot(
                    feedback_submitted=bool(loop.feedback_record_id),
                ),
            )
        except BaseException:
            native_loop_continuation.close()
            raise
        if decision.complete:
            native_loop_continuation.close()
            break
        if result.status != "stopped_without_tool_calls":
            native_loop_continuation.close()
            raise RuntimeError(
                f"{profile.critic_kind} native loop stopped before feedback submission: "
                f"{result.status}"
            )
        nudge = decision.nudge
    record_ref = state.feedback_state.record_ref(loop.feedback_record_id)
    if record_ref is None:
        raise RuntimeError(f"{profile.critic_kind} feedback was not durably reduced")
    record, content = load_feedback_record(record_ref, run_dir=invocation_context.run_dir)
    if record.author_role != profile.critic_kind:
        raise RuntimeError(f"{profile.critic_kind} feedback identity is invalid")
    verify_feedback_terminal_receipt(
        record=record,
        content=content,
        run_dir=invocation_context.run_dir,
    )
    invocation_context.state_store.poll_runtime_control("critic_result_ready")
    output = AgentOutput(
        artifact_type="stage_feedback",
        payload=record,
        summary=f"{profile.critic_kind.title()} round {loop.round} completed",
        status="success",
        needs_review=False,
        next_action="core_post_feedback",
        metadata={
            "agent_id": agent_id,
            "role": profile.actor_kind,
            "author_role": profile.critic_kind,
            "review_target": review_target.to_dict(),
            "feedback_path": str(canonical_feedback_path),
            "native_tool_loop": _loop_metadata(result),
            "tool_router": tool_router.trace_metadata(),
            "context_trace": prepared_context.trace.to_dict(),
            "p_digest": p.digest,
        },
    )
    return output, record


def _build_tool_context(
    *,
    invocation_context: AgentRuntimeInvocationContext,
    agent_input: AgentInput,
    actor_kind: str,
    actor_id: str,
    project_root: Path,
    app_paths: Any,
    tool_router: ToolRouter,
    conversation_lane: str,
) -> ToolRuntimeContext:
    state_store = invocation_context.state_store
    scratchpad = state_store.state.workflow_state.scratchpad
    return ToolRuntimeContext(
        state_store=state_store,
        raw_user_message=agent_input.semantic_context.raw_user_message,
        task_spec_text=agent_input.semantic_context.task_spec,
        project_root=project_root,
        run_dir=invocation_context.run_dir,
        stage=invocation_context.stage,
        attempt=invocation_context.attempt,
        query_chain_id=str(
            state_store.state.workflow_state.current_query_chain_id or ""
        ),
        query_chain_depth=int(
            state_store.state.workflow_state.current_query_chain_depth or 0
        ),
        continuation_index=int(scratchpad.get("continuation_index", 0) or 0),
        requested_by="native_tool_loop",
        user_config=invocation_context.user_config,
        actor_kind=actor_kind,
        actor_id=actor_id,
        conversation_lane=conversation_lane,
        hook_engine=invocation_context.hook_engine,
        app_paths=invocation_context.app_paths or app_paths,
        execution_runtime_service=invocation_context.execution_runtime_service,
        process_owner_manager=invocation_context.process_owner_manager,
        cancel_checker=invocation_context.cancel_checker,
        subagent_owner=invocation_context.subagent_owner,
        resume_read_session=invocation_context.resume_read_session,
        tool_router=tool_router,
    )


def _stage_value(stage: Any) -> str:
    return str(getattr(stage, "value", stage) or "").strip()


def _context_window_tokens(
    *,
    invocation_context: AgentRuntimeInvocationContext,
    state: Any,
) -> int | None:
    get_llm_config = getattr(invocation_context.user_config, 'get_llm_config', None)
    if callable(get_llm_config):
        llm_config = get_llm_config()
        if isinstance(llm_config, LLMConfig):
            limits = resolve_context_budget_limits(llm_config)
            return int(limits['context_window_limit'])
    snapshot = state.context_state.token_budget_snapshot
    value = int(getattr(snapshot, 'context_window_limit', 0) or 0)
    return value if value > 0 else None




def _loop_metadata(result: NativeToolLoopResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "turns": result.turns,
        "stop_reason": result.stop_reason,
        "tool_outcome_count": len(result.tool_outcomes),
        "failure_class": result.failure_class,
    }
