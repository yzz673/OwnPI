from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from ownpi.agents.base import RegisteredAgent
from ownpi.agents.context_runtime import (
    PreparedAgentContext,
    prepare_agent_context,
)
from ownpi.agents.registry import register_agent_role
from ownpi.agents.native_stream_bridge import stream_callback_operation
from ownpi.agents.streaming import AgentStreamEvent
from ownpi.app.paths import AppPaths
from ownpi.controller.stages import ORDINARY_STAGES, Stage
from ownpi.llm.base import LLMClient
from ownpi.llm.streaming import CanonicalResponseEvent, CanonicalResponseEventType
from ownpi.runtime.builders import build_agent_input
from ownpi.runtime.contracts import AgentInput, AgentOutput
from ownpi.runtime.events import now_utc_iso
from ownpi.runtime.limits.context import resolve_context_budget_limits
from ownpi.runtime.execution.wait_tool import (
    build_wait_for_execution_update_definition,
)
from ownpi.runtime.model_content import ModelContentItem
from ownpi.runtime.native_tools.executor import NativeToolExecutor
from ownpi.runtime.native_tools.core_agent_loop import (
    CoreAgentLoopAdapter,
    CoreAgentLoopContinuation,
)
from ownpi.runtime.native_tools.core_loop_models import (
    NativeToolLoopDomainPolicy,
    NativeToolLoopResult,
)
from ownpi.runtime.native_tools.core_loop_session import CoreLoopSession
from ownpi.runtime.native_tools.spec import NativeToolSpec
from ownpi.runtime.p_assembly import (
    PAssemblyBindings,
    PAssemblyRequest,
    PAssemblyResult,
    PLoopKind,
    PSourceInputs,
    assemble_native_loop_p,
)
from ownpi.runtime.stage_execution.feedback_control_tools import (
    COMPLETE_FEEDBACK_TOOL_NAME,
    NEXT_STAGE_TOOL_NAME,
    REQUEST_REVIEW_TOOL_NAME,
    build_complete_feedback_definition,
    build_next_stage_definition,
    build_request_review_definition,
)
from ownpi.runtime.stage_execution.feedback_handoff import mark_feedback_handoff_consumed
from ownpi.runtime.stage_execution.loop_policy import is_analysis_route_loop
from ownpi.runtime.stage_execution.feedback_store import feedback_path
from ownpi.runtime.stage_execution.feedback_tools import (
    FeedbackBinding,
    build_submit_feedback_definition,
)
from ownpi.runtime.state_models import RuntimeState
from ownpi.runtime.vision import (
    ImagePreparationError,
    load_local_image_for_prompt,
)
from ownpi.runtime.tools.router import ToolRouter
from ownpi.runtime.tools.runtime import ToolRuntimeContext
from ownpi.schemas.runtime import (
    AgentInstanceConfig,
    LLMConfig,
)
from ownpi.schemas.workflow_plan import StageRuntimeProfile
from ownpi.skills.snapshot import build_skill_catalog_snapshot


class OwnPICoreAgent(RegisteredAgent):
    role_name = "core"
    artifact_type = "native_tool_loop_result"

    def run(self, agent_input: AgentInput | RuntimeState) -> AgentOutput:
        if isinstance(agent_input, RuntimeState):
            agent_input = build_agent_input(agent_input, self._agent_config)
        if agent_input.operation in {"core_stage_round", "analysis_core_round", "direct_critic_core"}:
            return self._run_stage_core_round(agent_input)
        raise ValueError(f"Unsupported core operation: {agent_input.operation!r}")

    def stream(self, agent_input: AgentInput | RuntimeState):
        if isinstance(agent_input, RuntimeState):
            agent_input = build_agent_input(agent_input, self._agent_config)
        if agent_input.operation in {"core_stage_round", "analysis_core_round", "direct_critic_core"}:
            yield from self._stream_stage_core_round(agent_input)
            return
        yield AgentStreamEvent(
            event_type="agent_stream_fallback",
            operation=agent_input.operation,
            stage=getattr(agent_input.workflow_view.current_stage, "value", ""),
            payload={"reason": "operation_not_streamable"},
        )
        yield AgentStreamEvent(
            event_type="agent_completed",
            operation=agent_input.operation,
            stage=getattr(agent_input.workflow_view.current_stage, "value", ""),
            agent_output=self.run(agent_input),
        )

    def _run_stage_core_round(self, agent_input: AgentInput) -> AgentOutput:
        stage = agent_input.workflow_view.current_stage
        native_output = self._try_run_native_stage_core_round(agent_input)
        if native_output is not None:
            return native_output
        capability_family = (
            "analysis_stage" if stage == Stage.ANALYSIS_DECISION else "ordinary_stage"
        )
        raise RuntimeError(
            f"{capability_family}_native_capability_missing: native Core loop returned no result"
        )

    def _stream_stage_core_round(self, agent_input: AgentInput):
        stage = agent_input.workflow_view.current_stage
        native_output: AgentOutput | None = None
        for bridge_item in stream_callback_operation(
            lambda emit: self._try_run_native_stage_core_round(
                agent_input,
                stream_event_handler=emit,
            )
        ):
            if bridge_item.event is not None:
                yield self._native_agent_stream_event(
                    bridge_item.event,
                    operation=agent_input.operation,
                    stage=stage,
                )
            if bridge_item.terminal:
                native_output = bridge_item.result
        if native_output is not None:
            yield AgentStreamEvent(
                event_type="agent_completed",
                operation=agent_input.operation,
                stage=stage.value,
                agent_output=native_output,
            )
            return
        capability_family = (
            "analysis_stage" if stage == Stage.ANALYSIS_DECISION else "ordinary_stage"
        )
        raise RuntimeError(
            f"{capability_family}_native_capability_missing: native Core stream returned no result"
        )

    @staticmethod
    def _native_agent_stream_event(
        event: CanonicalResponseEvent,
        *,
        operation: str,
        stage: Stage,
    ) -> AgentStreamEvent:
        event_type = event.type_value
        direct_text_item = (
            stage == Stage.DIRECT_QA and event.item_type == "assistant_text"
        )
        direct_user_text = (
            direct_text_item
            and event_type == CanonicalResponseEventType.ASSISTANT_TEXT_DELTA.value
        )
        projected_type = {
            CanonicalResponseEventType.ASSISTANT_TEXT_DELTA.value: (
                "llm_text_delta" if direct_user_text else "llm_protocol_delta"
            ),
            CanonicalResponseEventType.ASSISTANT_REASONING_DELTA.value: "llm_reasoning_delta",
            CanonicalResponseEventType.TOOL_CALL_ARGUMENTS_DELTA.value: (
                "llm_tool_arguments_delta"
            ),
            CanonicalResponseEventType.ATTEMPT_DISCARDED.value: "llm_attempt_discarded",
        }.get(event_type, f"llm_{event_type}")
        presentation_class = (
            "user_text"
            if direct_text_item
            else (
                "reasoning"
                if event_type
                == CanonicalResponseEventType.ASSISTANT_REASONING_DELTA.value
                else (
                    "tool_progress"
                    if event.item_type
                    in {
                        "assistant_tool_call",
                        "provider_hosted_tool_call",
                    }
                    else "protocol"
                )
            )
        )
        completed_text = ""
        if (
            direct_text_item
            and event.completed_item is not None
            and event.completed_item.item_type.value == "assistant_text"
        ):
            completed_text = event.completed_item.text
        return AgentStreamEvent(
            event_type=projected_type,
            operation=operation,
            stage=stage.value,
            delta=event.delta,
            payload={
                **dict(event.payload),
                "canonical_event_type": event_type,
                "response_id": event.response_id,
                "item_id": event.item_id,
                "item_type": event.item_type,
                "sequence_index": event.sequence_index,
                "usage": dict(event.usage),
                "provider_attempt": event.attempt,
                "provider_protocol": event.provider_protocol,
                "presentation_class": presentation_class,
                "stop_reason": event.stop_reason,
                "provider_metadata": dict(event.provider_metadata),
                "completed_text": completed_text,
            },
        )

    def _try_run_native_stage_core_round(
        self,
        agent_input: AgentInput,
        *,
        stream_event_handler: Callable[[CanonicalResponseEvent], None] | None = None,
        prepare_only: bool = False,
    ) -> AgentOutput | None:
        direct_critic = agent_input.operation == 'direct_critic_core'
        runtime_context = self._native_tool_runtime_context(agent_input)
        loop_index = int(runtime_context.state_store.state.workflow_state.scratchpad.get(
            'stage_loop_index', 1,
        )) if runtime_context is not None else 1
        analysis_stage = not direct_critic and is_analysis_route_loop(
            agent_input.workflow_view.current_stage,
            agent_input.workflow_view.stage_runtime_profile,
            loop_index,
        )
        ordinary_stage = (
            agent_input.workflow_view.current_stage in ORDINARY_STAGES
            or (agent_input.workflow_view.current_stage == Stage.ANALYSIS_DECISION
                and not analysis_stage)
        )
        feedback_core = ordinary_stage or direct_critic
        native_required = feedback_core or analysis_stage
        capability_family = ('direct_critic' if direct_critic else
                             'ordinary_stage' if ordinary_stage else 'analysis_stage')
        if not bool(getattr(self.llm_client, "supports_native_tool_calling", False)):
            if native_required:
                raise RuntimeError(
                    f"{capability_family}_native_capability_missing: provider_native_tool_calling"
                )
            return None
        if runtime_context is None:
            if native_required:
                raise RuntimeError(
                    f"{capability_family}_native_capability_missing: ToolRuntimeContext"
                )
            return None
        skill_snapshot = build_skill_catalog_snapshot(
            app_paths=self.app_paths,
            project_root=self.project_root,
        )
        runtime_context = replace(
            runtime_context,
            skill_catalog_snapshot=skill_snapshot,
        )
        prepared_context = prepare_agent_context(
            skill_catalog_snapshot=skill_snapshot,
            agent_id=self.agent_id,
            role=self.role_name,
            selected_at_stage=agent_input.workflow_view.current_stage.value,
            memory_map=agent_input.memory_map,
        )
        stage_value = agent_input.workflow_view.current_stage.value
        attempt = agent_input.workflow_view.current_stage_attempt
        state = runtime_context.state_store.state
        feedback_state = state.feedback_state
        critic_loop = feedback_state.critic_loop
        post_feedback_core = bool(
            feedback_core
            and critic_loop.phase == "core_post_feedback"
            and critic_loop.source_stage == stage_value
            and critic_loop.attempt == attempt
            and critic_loop.feedback_record_id
        )
        scoped_definitions: tuple[Any, ...] = ()
        scoped_definitions = (
            *scoped_definitions,
            build_wait_for_execution_update_definition(),
        )
        if feedback_core:
            scoped_definitions = (
                *scoped_definitions,
                build_request_review_definition(direct=direct_critic),
            )
        if post_feedback_core:
            scoped_definitions = (
                *scoped_definitions,
                build_complete_feedback_definition(),
            )
        if analysis_stage:
            analysis_transition = feedback_state.analysis_transition
            analysis_plan = state.workflow_state.run_workflow_plan
            if analysis_plan is None:
                raise RuntimeError('Analysis requires an active workflow plan')
            if analysis_transition.attempt not in {0, attempt}:
                raise RuntimeError(
                    "Analysis feedback state belongs to a different attempt"
                )
            analysis_target = (
                f"analysis-workspace:{state.run_metadata.run_id}:{stage_value}:a{attempt}:"
                f"loop-{loop_index:03d}:"
                f"{Path(agent_input.project_workspace_root).expanduser().resolve()}"
            )
            analysis_binding = FeedbackBinding(
                author_role="analysis",
                source_stage=stage_value,
                attempt=attempt,
                round=1,
                stage_loop_index=loop_index,
                review_target=analysis_target,
                feedback_path=feedback_path(
                    run_dir=runtime_context.run_dir,
                    author_role="analysis",
                    source_stage=stage_value,
                    attempt=attempt,
                    round_index=1,
                    stage_loop_index=loop_index,
                ),
            )
            scoped_definitions = (
                *scoped_definitions,
                build_submit_feedback_definition(analysis_binding),
                build_next_stage_definition(workflow_plan=analysis_plan),
            )
        tool_router = _build_core_tool_router(
            runtime_context=runtime_context,
            scoped_definitions=scoped_definitions,
        )
        runtime_context = replace(
            runtime_context,
            tool_router=tool_router,
        )
        p = assemble_native_loop_p(
            request=PAssemblyRequest(
                loop_kind=PLoopKind.DIRECT_CRITIC_CORE if direct_critic else PLoopKind.STAGE_CORE,
                level=StageRuntimeProfile.TASK_LEVEL if direct_critic else agent_input.workflow_view.stage_runtime_profile,
                stage=None if direct_critic else agent_input.workflow_view.current_stage,
                stage_loop_index=int(runtime_context.state_store.state.workflow_state.scratchpad.get('stage_loop_index', 1)),
                bindings=_p_bindings(
                    agent_input=agent_input,
                    runtime_context=runtime_context,
                ),
            ),
            source_inputs=replace(PSourceInputs.from_runtime_state(
                runtime_context.state_store.state,
                skill_catalog_snapshot=skill_snapshot,
                tool_router=tool_router,
                memory_map=agent_input.memory_map,
                runtime_config=getattr(runtime_context.user_config, "runtime", runtime_context.user_config),
            ), **({'task_message': agent_input.semantic_context.raw_user_message,
                   'run_dir': runtime_context.run_dir}
                  if direct_critic else {})),
        )
        if not direct_critic:
            self._consume_feedback_handoff(
                agent_input=agent_input,
                runtime_context=runtime_context,
            )
        tool_specs = tool_router.initial_provider_specs
        if not tool_specs:
            if native_required:
                raise RuntimeError(
                    f"{capability_family}_native_capability_missing: non_empty_native_tool_specs"
                )
            return None
        invocation_context = self.runtime_invocation_context
        if invocation_context is None or invocation_context.tool_runtime is None:
            if native_required:
                raise RuntimeError(
                    f"{capability_family}_native_capability_missing: invocation_tool_runtime"
                )
            return None
        runtime_config = getattr(runtime_context.user_config, "runtime", runtime_context.user_config)
        subagent_enabled = bool(getattr(runtime_config, "subagent_enabled", False))
        if subagent_enabled:
            from ownpi.subagent.caller import prepare_caller

            runtime_context = prepare_caller(
                runtime_context, level=agent_input.workflow_view.stage_runtime_profile,
                memory_map=agent_input.memory_map, llm_client=self.llm_client,
                tool_runtime=invocation_context.tool_runtime,
                canonical_ledger_messages=_serialize_p_with_image_content(
                    p=p, agent_input=agent_input, project_root=self.project_root,
                    supports_image_input=bool(getattr(self.llm_client, "supports_image_input", False)),
                ),
            )
            invocation_context = replace(invocation_context, subagent_owner=runtime_context.subagent_owner)
            self.set_runtime_invocation_context(invocation_context)
            tool_router = _build_core_tool_router(runtime_context=runtime_context, scoped_definitions=scoped_definitions)
            runtime_context = replace(runtime_context, tool_router=tool_router)
            tool_specs = tool_router.initial_provider_specs
        analysis_receipt = feedback_state.analysis_transition.next_stage_receipt
        recovered_analysis_action = bool(
            analysis_stage
            and analysis_receipt is not None
            and analysis_receipt.attempt == attempt
        )
        recovered_core_intent = bool(
            feedback_core
            and critic_loop.core_intent_receipt is not None
            and critic_loop.core_intent_receipt.attempt == attempt
        )
        recovered_terminal_result = (
            NativeToolLoopResult(
                status="next_stage_submitted",
                turns=0,
                stop_reason="next_stage_submitted",
            )
            if recovered_analysis_action
            else NativeToolLoopResult(
                status="core_feedback_intent_submitted",
                turns=0,
                stop_reason="core_feedback_intent_submitted",
            )
            if recovered_core_intent
            else None
        )
        if recovered_terminal_result is not None:
            result = recovered_terminal_result
        else:
            continuation = invocation_context.native_loop_continuation
            if continuation is not None and not isinstance(
                continuation, CoreAgentLoopContinuation
            ):
                raise RuntimeError('invalid native loop continuation holder')
            if continuation is not None and continuation.adapter is not None:
                native_loop = continuation.adapter
                if not direct_critic:
                    continuation.append_pending_input(
                        _continuation_messages(runtime_context.state_store.state)
                    )
            else:
                native_loop = CoreAgentLoopAdapter.from_llm_client(
                    executor=NativeToolExecutor(
                        invocation_context.tool_runtime,
                        native_tool_strict=self.llm_client.native_tool_provider_capabilities.native_tool_strict,
                    ),
                    llm_client=self.llm_client,
                    canonical_ledger_messages=_serialize_p_with_image_content(
                        p=p,
                        agent_input=agent_input,
                        project_root=self.project_root,
                        supports_image_input=bool(
                            getattr(self.llm_client, "supports_image_input", False)
                        ),
                    ),
                    initial_conversation_messages=() if direct_critic else _continuation_messages(
                        runtime_context.state_store.state
                    ),
                    resume_conversation_id=invocation_context.resume_conversation_id,
                    request_kind=(
                        'native_direct_critic_core:direct_qa' if direct_critic else
                        f"native_core_stage_round:{agent_input.workflow_view.current_stage.value}"
                        if ordinary_stage
                        else f"native_analysis_core_round:{agent_input.workflow_view.current_stage.value}"
                    ),
                    stream_event_handler=stream_event_handler,
                    domain_policy=NativeToolLoopDomainPolicy(
                        terminal_tool_names=(
                            (NEXT_STAGE_TOOL_NAME,)
                            if analysis_stage
                            else (
                                REQUEST_REVIEW_TOOL_NAME,
                                COMPLETE_FEEDBACK_TOOL_NAME,
                            )
                            if feedback_core
                            else None
                        ),
                    ),
                )
                if continuation is not None:
                    continuation.attach(native_loop)
            try:
                native_loop.set_stream_event_handler(stream_event_handler)
                prepare_session = None
                if direct_critic:
                    from ownpi.runtime.rollout.direct_inputs import prepare_direct_inputs

                    def prepare_session(session: CoreLoopSession) -> None:
                        session.refresh_tool_surface(tool_specs)
                        prepare_direct_inputs(
                            session, current_request=agent_input.semantic_context.raw_user_message,
                        )
                        self._consume_feedback_handoff(
                            agent_input=agent_input, runtime_context=runtime_context,
                        )

                if prepare_only:
                    if not direct_critic or continuation is None:
                        raise ValueError('Input preparation requires a direct Core continuation')
                    session = native_loop.prepare(
                        context=runtime_context, tool_specs=tool_specs, session=native_loop.session,
                    )
                    continuation.session = session
                    prepare_session(session)
                    return None
                if continuation is not None:
                    result = continuation.run(
                        context=runtime_context,
                        tool_specs=tool_specs,
                        **({'prepare_session': prepare_session} if direct_critic else {}),
                    )
                else:
                    result = native_loop.run(
                        context=runtime_context,
                        tool_specs=tool_specs,
                        **({'prepare_session': prepare_session} if direct_critic else {}),
                    )
            except NotImplementedError:
                if native_required:
                    raise RuntimeError(
                        f"{capability_family}_native_capability_missing: provider_native_tool_adapter"
                    ) from None
                return None
        progress_check_mode = bool(
            runtime_context.state_store.state.workflow_state.scratchpad.get(
                "native_tool_progress_check_mode"
            )
        )
        if analysis_stage and result.status == "next_stage_submitted":
            return self._analysis_transition_output(
                agent_input=agent_input,
                prepared_context=prepared_context,
                tool_router=tool_router,
                result=result,
                runtime_context=runtime_context,
            )
        call_continuation_metadata = {
            "native_tool_loop": {
                "status": result.status,
                "turns": result.turns,
                "stop_reason": result.stop_reason,
            },
        }
        native_tool_loop_payload = _native_tool_loop_result_payload(
            stage=agent_input.workflow_view.current_stage,
            attempt=agent_input.workflow_view.current_stage_attempt,
            result=result,
        )
        if progress_check_mode:
            runtime_context.state_store.state.workflow_state.scratchpad[
                "native_tool_loop_pending_result"
            ] = dict(native_tool_loop_payload)
            return AgentOutput(
                artifact_type="native_tool_loop_result",
                payload=native_tool_loop_payload,
                summary=f"{agent_input.workflow_view.current_stage.value}:native_tool_loop_result",
                status="success",
                needs_review=False,
                next_action="core_round_complete",
                metadata={
                    "agent_id": self.agent_id,
                    "role": self.role_name,
                    "context_trace": prepared_context.trace.to_dict(),
                    "tool_trace": self._tool_trace(
                        agent_input=agent_input,
                        selected_at_stage=agent_input.workflow_view.current_stage.value,
                    ),
                    "native_tool_loop": _native_tool_loop_metadata(result),
                    "native_tool_specs": _native_tool_spec_metadata(
                        tool_specs=tool_specs,
                        tool_router=tool_router,
                    ),
                    "tool_router": tool_router.trace_metadata(),
                    "call_continuation_v2": dict(call_continuation_metadata),
                    "native_tool_progress_check_mode": True,
                },
            )
        runtime_context.state_store.state.workflow_state.scratchpad[
            "native_tool_loop_pending_result"
        ] = dict(native_tool_loop_payload)
        return AgentOutput(
            artifact_type="native_tool_loop_result",
            payload=native_tool_loop_payload,
            summary=f"{agent_input.workflow_view.current_stage.value}:native_tool_loop_result",
            status="success",
            needs_review=False,
            next_action="core_round_complete",
            metadata={
                "agent_id": self.agent_id,
                "role": self.role_name,
                "context_trace": prepared_context.trace.to_dict(),
                "tool_trace": self._tool_trace(
                    agent_input=agent_input,
                    selected_at_stage=agent_input.workflow_view.current_stage.value,
                ),
                "native_tool_loop": _native_tool_loop_metadata(result),
                "native_tool_specs": _native_tool_spec_metadata(
                    tool_specs=tool_specs,
                    tool_router=tool_router,
                ),
                "tool_router": tool_router.trace_metadata(),
                "call_continuation_v2": dict(call_continuation_metadata),
            },
        )

    def _analysis_transition_output(
        self,
        *,
        agent_input: AgentInput,
        prepared_context: PreparedAgentContext,
        tool_router: ToolRouter,
        result: NativeToolLoopResult,
        runtime_context: ToolRuntimeContext,
    ) -> AgentOutput:
        feedback_state = runtime_context.state_store.state.feedback_state
        transition = feedback_state.analysis_transition
        stage = agent_input.workflow_view.current_stage
        attempt = agent_input.workflow_view.current_stage_attempt
        receipt = transition.next_stage_receipt
        record_ref = feedback_state.record_ref(transition.feedback_record_id)
        if (
            transition.source_stage != stage.value
            or transition.attempt != attempt
            or receipt is None
            or receipt.attempt != attempt
            or record_ref is None
        ):
            raise RuntimeError(
                "Analysis terminal state is missing feedback or next-stage receipt"
            )
        payload = _native_tool_loop_result_payload(
            stage=stage,
            attempt=attempt,
            result=result,
        )
        return AgentOutput(
            artifact_type="native_tool_loop_result",
            payload=payload,
            summary=f"{stage.value}:native_tool_loop_result",
            status="success",
            needs_review=False,
            next_action="runtime_gate",
            metadata={
                "agent_id": self.agent_id,
                "role": self.role_name,
                "context_trace": prepared_context.trace.to_dict(),
                "tool_trace": self._tool_trace(
                    agent_input=agent_input,
                    selected_at_stage=stage.value,
                ),
                "native_tool_loop": _native_tool_loop_metadata(result),
                "native_tool_specs": _native_tool_spec_metadata(
                    tool_specs=tool_router.initial_provider_specs,
                    tool_router=tool_router,
                ),
                "tool_router": tool_router.trace_metadata(),
                "analysis_single_core": True,
            },
        )

    def _consume_feedback_handoff(
        self,
        *,
        agent_input: AgentInput,
        runtime_context: ToolRuntimeContext,
    ) -> None:
        state = runtime_context.state_store.state
        feedback_state = state.feedback_state
        stage = agent_input.workflow_view.current_stage.value
        attempt = agent_input.workflow_view.current_stage_attempt
        handoff = feedback_state.active_handoff
        if (
            handoff.record_id
            and handoff.consumer_stage == stage
            and handoff.consumer_attempt == attempt
            and handoff.status in {"pending", "consumed"}
        ):
            if handoff.status == "pending":
                from ownpi.runtime.rollout.stage_business_events import emit_feedback_delivered

                emit_feedback_delivered(
                    runtime_context.state_store.run_event_store,
                    record_id=handoff.record_id, target_stage=stage, target_attempt=attempt,
                )
                mark_feedback_handoff_consumed(
                    feedback_state,
                    record_id=handoff.record_id,
                    consumer_stage=stage,
                    consumer_attempt=attempt,
                    consumed_at=now_utc_iso(),
                )
                runtime_context.state_store.poll_runtime_control(
                    "feedback_handoff_consumed"
                )

    def _native_tool_runtime_context(
        self, agent_input: AgentInput
    ) -> ToolRuntimeContext | None:
        invocation_context = self.runtime_invocation_context
        if invocation_context is None or invocation_context.operation not in {
            "core_stage_round",
            "analysis_core_round",
            "direct_critic_core",
        }:
            return None
        if invocation_context.tool_runtime is None:
            return None
        state_store = invocation_context.state_store
        scratchpad = state_store.state.workflow_state.scratchpad
        base_router = agent_input.tool_context.router
        if base_router is None:
            raise RuntimeError("AgentInput is missing its authoritative ToolRouter")
        return ToolRuntimeContext(
            state_store=state_store,
            raw_user_message=agent_input.semantic_context.raw_user_message,
            task_spec_text=agent_input.semantic_context.task_spec,
            project_root=self.project_root,
            run_dir=invocation_context.run_dir,
            stage=agent_input.workflow_view.current_stage,
            attempt=agent_input.workflow_view.current_stage_attempt,
            tool_router=base_router,
            query_chain_id=str(
                state_store.state.workflow_state.current_query_chain_id or ""
            ),
            query_chain_depth=int(
                state_store.state.workflow_state.current_query_chain_depth or 0
            ),
            continuation_index=int(scratchpad.get("continuation_index", 0) or 0),
            requested_by="native_tool_loop",
            user_config=invocation_context.user_config,
            actor_kind="core",
            actor_id=self.agent_id,
            hook_engine=invocation_context.hook_engine,
            app_paths=invocation_context.app_paths or self.app_paths,
            execution_runtime_service=invocation_context.execution_runtime_service,
            process_owner_manager=invocation_context.process_owner_manager,
            cancel_checker=invocation_context.cancel_checker,
            subagent_owner=invocation_context.subagent_owner,
            resume_read_session=invocation_context.resume_read_session,
        )


    def _tool_trace(
        self, *, agent_input: AgentInput, selected_at_stage: str
    ) -> dict[str, object]:
        active_tool_names = [
            item.tool_name for item in agent_input.tool_context.active_tool_abis
        ]
        loaded_ref_names = [
            item.canonical_name
            for item in agent_input.tool_context.native_loaded_tool_refs
        ]
        loaded_tool_names = list(dict.fromkeys([*active_tool_names, *loaded_ref_names]))
        return {
            "agent_id": self.agent_id,
            "role": self.role_name,
            "selected_at_stage": selected_at_stage,
            "available_tool_count": len(agent_input.tool_context.available_tools),
            "loaded_tool_count": len(loaded_tool_names),
            "active_tool_abi_count": len(active_tool_names),
            "native_loaded_tool_ref_count": len(loaded_ref_names),
            "selection_summary": agent_input.tool_context.selection_summary or "",
            "available_tool_names": [
                item.name for item in agent_input.tool_context.available_tools
            ],
            "loaded_tool_names": loaded_tool_names,
            "active_tool_abi_names": active_tool_names,
            "native_loaded_tool_ref_names": loaded_ref_names,
        }


def _build_ownpi_core_agent(
    agent_config: AgentInstanceConfig,
    llm_client: LLMClient,
    project_root: Path,
    app_paths: AppPaths,
) -> OwnPICoreAgent:
    return OwnPICoreAgent(
        agent_config=agent_config,
        llm_client=llm_client,
        project_root=project_root,
        app_paths=app_paths,
    )


register_agent_role("core", _build_ownpi_core_agent)


def _runtime_context_window_tokens(context: ToolRuntimeContext) -> int | None:
    user_config = context.user_config
    get_llm_config = getattr(user_config, 'get_llm_config', None)
    if callable(get_llm_config):
        llm_config = get_llm_config()
        if isinstance(llm_config, LLMConfig):
            limits = resolve_context_budget_limits(llm_config)
            return int(limits['context_window_limit'])
    snapshot = context.state_store.state.context_state.token_budget_snapshot
    value = int(getattr(snapshot, 'context_window_limit', 0) or 0)
    return value if value > 0 else None


def _p_bindings(
    *,
    agent_input: AgentInput,
    runtime_context: ToolRuntimeContext,
) -> PAssemblyBindings:
    app_home = getattr(runtime_context.app_paths, "app_home", None)
    return PAssemblyBindings.from_runtime_state(
        runtime_context.state_store.state,
        app_home=str(app_home) if app_home is not None else "",
        current_working_directory=str(runtime_context.project_root),
        context_window_tokens=_runtime_context_window_tokens(runtime_context),
    )


def _continuation_messages(state: RuntimeState) -> tuple[dict[str, str], ...]:
    nudge = str(
        state.workflow_state.scratchpad.get("role_loop_continuation_nudge", "") or ""
    ).strip()
    if not nudge:
        return ()
    return ({"role": "user", "content": f"Runtime continuation:\n{nudge}"},)


def _serialize_p_with_image_content(
    *,
    p: PAssemblyResult,
    agent_input: AgentInput,
    project_root: Path,
    supports_image_input: bool,
) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = [dict(message) for message in p.ledger_messages()]
    image_sources = _task_image_input_sources(agent_input)
    if not image_sources:
        return messages
    task_image_index = next(
        (
            index
            for index, message in enumerate(p.messages)
            if message.section_id == "task_images"
        ),
        None,
    )
    if task_image_index is None:
        raise RuntimeError("image inputs require a task_images P section")
    content_items: list[ModelContentItem] = [
        ModelContentItem.input_text(str(messages[task_image_index]["content"]))
    ]
    for index, source in enumerate(image_sources, start=1):
        source_path = _resolve_image_source_path(
            source_path=str(getattr(source, "path", "") or ""),
            project_root=project_root,
        )
        label = f"local_image_{index}"
        if source_path is None:
            content_items.append(
                ModelContentItem.input_text(
                    f"\n<local_image:{label} path_unavailable>\n"
                    f"Image source `{getattr(source, 'path', '')}` cannot be resolved as a local path.\n"
                    "</local_image>\n"
                )
            )
            continue
        if not supports_image_input:
            content_items.append(
                ModelContentItem.input_text(
                    f'\n<local_image:{label} path="{source_path}">\n'
                    "Image source is present, but the active model/provider is not configured with image input support. "
                    "Use textual metadata tools or ask for a vision-capable model if visual content is required.\n"
                    "</local_image>\n"
                )
            )
            continue
        try:
            image = load_local_image_for_prompt(source_path, detail="high")
        except ImagePreparationError as exc:
            content_items.append(
                ModelContentItem.input_text(
                    f'\n<local_image:{label} path="{source_path}">\n'
                    f"Image content omitted because it could not be prepared: {exc}\n"
                    "</local_image>\n"
                )
            )
            continue
        content_items.extend(
            [
                ModelContentItem.input_text(
                    f'\n<local_image:{label} path="{source_path}" mime_type="{image.mime_type}" '
                    f'size="{image.width}x{image.height}">\n'
                ),
                ModelContentItem.input_image(
                    image_url=image.image_url,
                    detail=image.detail,
                    source_path=str(source_path),
                    mime_type=image.mime_type,
                ),
                ModelContentItem.input_text("\n</local_image>\n"),
            ]
        )
    messages[task_image_index] = {
        "role": messages[task_image_index]["role"],
        "content": [item.to_dict() for item in content_items],
    }
    return messages


def _task_image_input_sources(agent_input: AgentInput) -> tuple[object, ...]:
    artifact_state = agent_input.artifact_state
    records = getattr(artifact_state, "recent_artifacts", ()) or ()
    return tuple(
        _ArtifactImageSource(path=path)
        for record in records
        if "user_input" in set(getattr(record, "tags", ()) or ())
        for path in tuple(getattr(record, "source_paths", ()) or ())
        if Path(path).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}
    )


@dataclass(frozen=True)
class _ArtifactImageSource:
    path: str
    user_provided: bool = True
    required: bool = True
    media_type: str = "image/*"


def _resolve_image_source_path(*, source_path: str, project_root: Path) -> Path | None:
    text = source_path.strip()
    if not text:
        return None
    path = Path(text)
    if not path.is_absolute():
        path = project_root / path
    return path


def _native_tool_loop_metadata(result: NativeToolLoopResult) -> dict[str, object]:
    return {
        "status": result.status,
        "turns": result.turns,
        "stop_reason": result.stop_reason,
        "failure_class": result.failure_class,
        "failure_diagnostics": dict(result.failure_diagnostics),
        "auto_resume_safe": result.auto_resume_safe,
        "recommended_recovery": result.recommended_recovery,
        "tool_outcome_count": len(result.tool_outcomes),
        "tool_outcomes": [
            {
                "tool_name": outcome.call.tool_name,
                "ok": outcome.ok,
                "status": (outcome.invocation.record.status if outcome.invocation is not None
                           else 'diagnostic' if outcome.diagnostic else 'failed'),
                "request_id": outcome.call.request_id,
            }
            for outcome in result.tool_outcomes
        ],
    }


def _native_tool_loop_result_payload(
    *,
    stage: Stage,
    attempt: int,
    result: NativeToolLoopResult,
) -> dict[str, object]:
    return {
        "stage": stage.value,
        "attempt": attempt,
        "status": result.status,
        "turns": result.turns,
        "final_assistant_text": result.final_assistant_text,
        "stop_reason": result.stop_reason,
        "tool_outcomes": [
            {
                "tool_name": outcome.call.tool_name,
                "ok": outcome.ok,
                "status": (outcome.invocation.record.status if outcome.invocation is not None
                           else 'diagnostic' if outcome.diagnostic else 'failed'),
                "request_id": outcome.call.request_id,
            }
            for outcome in result.tool_outcomes
        ],
    }


def _native_tool_spec_metadata(
    *,
    tool_specs: tuple[NativeToolSpec, ...],
    tool_router: ToolRouter | None = None,
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for spec in tool_specs:
        definition = tool_router.resolve(spec.name) if tool_router is not None else None
        records.append(
            {
                "tool_name": spec.name,
                "source_tool_name": spec.source_tool_name,
                "exposure": definition.exposure.value
                if definition is not None
                else "hosted",
                "permission_class": spec.permission_class,
                "execution_class": spec.execution_class,
                "read_only": bool(spec.read_only),
                "concurrency_safe": bool(spec.concurrency_safe),
            }
        )
    return records


def _build_core_tool_router(
    *,
    runtime_context: ToolRuntimeContext,
    scoped_definitions: tuple[Any, ...],
) -> ToolRouter:
    existing_names = set(runtime_context.tool_router.registered_names)
    scoped_definitions = tuple(
        definition
        for definition in scoped_definitions
        if definition.name not in existing_names
    )
    if runtime_context.subagent_owner is not None and runtime_context.subagent_owner.enabled:
        from ownpi.subagent.tools import tool_definitions_for_actor

        scoped_definitions = (
            *scoped_definitions,
            *(
                definition
                for definition in tool_definitions_for_actor("core")
                if definition.name not in existing_names
            ),
        )
    return runtime_context.tool_router.with_scoped_definitions(
        scoped_definitions,
        tool_search_enabled=runtime_context.tool_router.tool_search_enabled,
    )
