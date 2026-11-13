"""Main-loop business ownership around the shared live conversation."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any, Callable

from ownpi.runtime.context.manager import ContextRuntimeManager
from ownpi.runtime.limits.tool_output import resolve_tool_result_truncation_policy
from ownpi.runtime.native_tools.compaction.model_metadata import model_context_metadata_from_config
from ownpi.runtime.native_tools.core_loop_services import (
    permission_projected_provider_capabilities, record_provider_capability_fact,
)
from ownpi.runtime.native_tools.history_items import HistoryEntry, HistoryMessage
from ownpi.runtime.native_tools.hosted_web_search import WebSearchExecutionMode
from ownpi.runtime.native_tools.loaded_tools import NativeLoadedToolSurface, update_native_loaded_tool_state
from ownpi.runtime.native_tools.loop_engine.session import create_native_session
from ownpi.runtime.native_tools.loop_engine.continuation import NativeLoopContinuation
from ownpi.runtime.native_tools.loop_engine.provider_recovery import NativeProviderResponseDecision
from ownpi.runtime.native_tools.loop_engine.execution import NativeToolBatchState
from ownpi.runtime.rollout.agent_relationships import read_agent_relationships
from ownpi.runtime.rollout.resume import find_conversation
from ownpi.schemas.runtime import NativeConversationRuntimeConfig

if TYPE_CHECKING:
    from ownpi.runtime.native_tools.compaction.local import NativeCompactionInvoke
    from ownpi.runtime.native_tools.core_loop_models import (
        NativeToolLoopDomainPolicy, NativeToolLoopResult, NativeToolRequestMeasureCallable,
    )
    from ownpi.runtime.native_tools.executor import NativeToolExecutor
    from ownpi.runtime.native_tools.loop_engine.contracts import NativeExecutionRequest
    from ownpi.runtime.native_tools.provider import NativeToolProviderCapabilities
    from ownpi.runtime.native_tools.spec import NativeToolSpec
    from ownpi.runtime.rollout.agent_relationships import AgentRelationships
    from ownpi.runtime.rollout.resume import ResumedConversation
    from ownpi.runtime.tools.runtime import ToolRuntimeContext
    from ownpi.schemas.runtime import LLMConfig


class CoreLoopSession:
    def __init__(
        self, *, context: ToolRuntimeContext, tool_specs: tuple[NativeToolSpec, ...],
        executor: NativeToolExecutor, provider_capabilities: NativeToolProviderCapabilities,
        request_measure: NativeToolRequestMeasureCallable,
        compaction_invoke: NativeCompactionInvoke | None, llm_config: LLMConfig | None,
        canonical_ledger_messages: list[dict[str, Any]],
        initial_conversation_messages: tuple[dict[str, Any], ...],

        domain_policy: NativeToolLoopDomainPolicy, resume_conversation_id: str | None = None,
    ) -> None:
        from ownpi.runtime.native_tools.session_adapter import build_native_session_bindings

        self.context = replace(
            context, tool_result_truncation_policy=resolve_tool_result_truncation_policy(llm_config),
        )
        context = self.context
        self.executor = executor
        self.domain_policy = domain_policy
        self.provider_decision = NativeProviderResponseDecision()
        self.execution = NativeToolBatchState()
        self.on_tool_started: Callable[[NativeExecutionRequest], None] | None = None
        self.terminal_result: NativeToolLoopResult | None = None
        self.agent_relationships: AgentRelationships | None = None
        self._business_results_recovered = False
        self.provider_capabilities = permission_projected_provider_capabilities(context, provider_capabilities)
        record_provider_capability_fact(context, self.provider_capabilities)
        preferences = getattr(context.state_store.state.runtime_preferences, 'native_conversation', {})
        self.compaction_config = NativeConversationRuntimeConfig.from_dict(
            preferences if isinstance(preferences, dict) else None,
        ).conversation_compaction
        self.tool_surface = NativeLoadedToolSurface(
            base_specs=self._project_tool_specs(tool_specs),
            search_entries=context.tool_router.deferred_search_entries,
            provider_capabilities=self.provider_capabilities,
        )
        self._publish_loaded_tools()
        model = {key: getattr(llm_config, key, None) for key in (
            'provider', 'model', 'reasoning_effort', 'reasoning_options',
            'max_output_tokens', 'context_window_limit',
        )}
        metadata = context.state_store.state.run_metadata
        history = None
        try:
            replay, relationships = None, None
            if resume_conversation_id is not None:
                replay, relationships = self._select_replay(resume_conversation_id)
                replay.check_identity({
                    'run_id': str(metadata.run_id or ''), 'actor_kind': context.actor_kind,
                    'actor_id': context.actor_id, 'stage': context.conversation_stage,
                    'attempt': context.attempt,
                })
            self.shared = create_native_session(
                directory=context.run_dir / 'rollouts', policy=context.tool_result_truncation_policy,
                metadata={
                    'run_id': str(metadata.run_id or ''), 'session_id': str(metadata.session_id or ''),
                    'actor_kind': context.actor_kind, 'actor_id': context.actor_id,
                    'parent_id': context.rollout_parent_id, 'workspace': str(context.project_root),
                    'stage': context.conversation_stage, 'attempt': context.attempt,
                    'stage_loop_index': int(context.state_store.state.workflow_state.scratchpad.get('stage_loop_index', 1)),
                    'critic_round': context.state_store.state.feedback_state.critic_loop.round,
                    'native_tool_loop_index': context.continuation_index, 'model': model,
                }, replay=replay, canonical_ledger_messages=canonical_ledger_messages,
                context_runtime_manager=ContextRuntimeManager(state=context.state_store.state, llm_config=llm_config),
                request_measure=request_measure, bindings=build_native_session_bindings(self),
                provider_capabilities=self.provider_capabilities, compaction_invoke=compaction_invoke,
                model_metadata=model_context_metadata_from_config(
                    llm_config=llm_config, compaction_config=self.compaction_config,
                ), rollout_model=model,
            )
            history = self.shared.history
            self.continuation = NativeLoopContinuation(self.shared)
            self.context = replace(context, rollout_conversation_id=history.conversation_id, rollout_history=history)
            if replay is not None:
                self.tool_surface.replay(history.snapshot)
                owner = context.subagent_owner
                if owner is not None and relationships is not None:
                    owner.install_historical_agents(relationships)
                self.agent_relationships = relationships
                self._publish_loaded_tools()
            self._bind_subagent_parent()
            for index, message in enumerate(() if resume_conversation_id else initial_conversation_messages, start=1):
                role, content = str(message.get('role') or '').strip(), str(message.get('content') or '').strip()
                if role != 'user' or not content:
                    raise ValueError('initial native conversation messages must be non-empty user messages')
                self.shared.pending_input.append(HistoryEntry(
                    HistoryMessage(role, content), exchange_id=f'runtime-initial-conversation-{index}',
                ))
        except BaseException as error:
            if history is not None:
                try:
                    history.close()
                except BaseException as close_error:
                    error.add_note(f'Session construction close failed: {close_error!r}')
            raise

    def _project_tool_specs(self, tool_specs: tuple[NativeToolSpec, ...]) -> tuple[NativeToolSpec, ...]:
        return tuple(
            replace(spec, hosted_execution_allowed=(
                self.provider_capabilities.web_search_execution_mode == WebSearchExecutionMode.HOSTED
            )) if spec.name == 'web_search' else spec for spec in tool_specs
        )

    def refresh_tool_surface(self, tool_specs: tuple[NativeToolSpec, ...]) -> None:
        # Direct keeps one live conversation across critic phases and requests.
        self.tool_surface.base_specs = self._project_tool_specs(tool_specs)
        self.tool_surface.search_entries = self.context.tool_router.deferred_search_entries

    def _publish_loaded_tools(self) -> None:
        update_native_loaded_tool_state(
            self.context.state_store.state.tool_state, self.tool_surface.loaded_state,
            stage=self.context.conversation_stage, attempt=self.context.attempt,
        )

    def _select_replay(self, conversation_id: str) -> tuple[ResumedConversation, AgentRelationships | None]:
        context = self.context
        owner = context.subagent_owner
        reader = context.resume_read_session
        relationships = getattr(owner, 'historical_agent_relationships', None)
        if context.actor_kind in {'core', 'reviewer'}:
            if relationships is None or relationships.run_root != context.run_dir.resolve():
                relationships = (reader.relationships(context.run_dir) if reader is not None
                                 else read_agent_relationships(context.run_dir))
        if context.actor_kind == 'subagent' and relationships is not None:
            replay = relationships.read_child(
                child_id=context.actor_id, conversation_id=conversation_id,
                parent_conversation_id=context.rollout_parent_id,
                read_session=reader,
            )
        else:
            replay = find_conversation(context.run_dir, conversation_id=conversation_id, read_session=reader)
        return replay, relationships

    def update_for_continuation(
        self, *, context: ToolRuntimeContext, tool_specs: tuple[NativeToolSpec, ...],
    ) -> None:
        self.terminal_result = None
        self.execution.turn_outcomes = ()
        self.context = replace(
            context, tool_result_truncation_policy=self.shared.history.policy,
            rollout_conversation_id=self.context.rollout_conversation_id,
            rollout_parent_id=self.context.rollout_parent_id, rollout_history=self.shared.history,
        )
        self.tool_surface.executable_specs = tool_specs
        self._bind_subagent_parent()

    def _bind_subagent_parent(self) -> None:
        context = self.context
        factory = getattr(context.subagent_owner, 'execution_factory', None)
        if context.actor_kind in {'core', 'reviewer'} and factory is not None:
            factory.parent_context = replace(
                factory.parent_context, rollout_conversation_id=self.shared.history.conversation_id,
            )
            factory.parent_rollout = self.shared.history
