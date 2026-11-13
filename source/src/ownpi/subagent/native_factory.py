"""Construction of a subagent native loop from an assembled context bundle."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable
from ownpi.llm.base import current_cancel_checker

from ownpi.runtime.native_tools.loop_engine.session_lifecycle import NativeSessionLifecycle
from ownpi.runtime.native_tools.core_loop_session import CoreLoopSession
from ownpi.runtime.native_tools.core_loop_models import NativeToolLoopDomainPolicy, NativeToolProviderTurn
from ownpi.runtime.native_tools.executor import NativeToolExecutor
from ownpi.runtime.native_tools.loop_engine import NativeLoopIdentity, NativeLoopInvocation, NativeLoopResult, NativeLoopPaths, NativeLoopServices, ProjectedNativeProviderTurnRunner
from ownpi.runtime.native_tools.core_loop_models import project_native_tool_provider_turn
from ownpi.runtime.native_tools.loop_engine.provider import StreamingNativeProviderTurnRunner
from ownpi.runtime.native_tools.core_loop_observer import CoreLoopObserver
from ownpi.runtime.native_tools.core_loop_execution import build_core_loop_executor
from ownpi.runtime.native_tools.core_loop_bindings import CoreLoopBindings
from ownpi.runtime.native_tools.session_adapter import bind_main_provider_request
from ownpi.runtime.native_tools.loop_engine.session import build_native_session_engine
from ownpi.runtime.native_tools.provider_input import NativeHistoryProviderInput
from ownpi.runtime.native_tools.provider import ProviderOutputEnvelope
from ownpi.runtime.native_tools.spec import NativeToolSpec

from .context import SubagentContextBundle
from .policy import SubagentLoopPolicy
from ownpi.runtime.rollout.session import RecordingTurnRunner
from .native_scope import SubagentProviderRunScope


@dataclass(frozen=True)
class _TerminalSubagentLoop:
    result: NativeLoopResult

    def run(self, _invocation: Any = None) -> NativeLoopResult:
        return self.result


@dataclass
class SubagentNativeLoopFactory:
    """Build the shared engine with subagent-specific policy and scope."""

    provider_turn: Callable[[Any], Any]
    executor: NativeToolExecutor
    canonical_ledger_messages: list[dict[str, Any]]
    tool_specs: tuple[Any, ...]
    provider_capabilities: Any
    request_measure: Callable[..., Any]
    llm_config: Any = None
    llm_client: Any = None

    @classmethod
    def from_llm_client(
        cls,
        *,
        llm_client: Any,
        executor: NativeToolExecutor,
        canonical_ledger_messages: list[dict[str, Any]],
        tool_specs: tuple[Any, ...],
        request_kind: str = "native_subagent_tool_turn",
    ) -> "SubagentNativeLoopFactory":
        """Reuse the standard streaming client and request measurement wiring."""
        runner = StreamingNativeProviderTurnRunner(
            client=llm_client, request_kind=request_kind
        )

        def provider_turn(request: Any) -> Any:
            response = runner.invoke(
                prepared_turn=request.conversation_input,
                tools=request.tool_specs,
                cancel_checker=current_cancel_checker(llm_client),
            )
            return NativeToolProviderTurn(response=response)

        def request_measure(conversation_input: Any, specs: Any) -> Any:
            return llm_client.measure_native_tool_request(
                messages=list(canonical_ledger_messages),
                native_conversation_input=conversation_input,
                tools=list(specs),
            )

        return cls(
            provider_turn=provider_turn,
            executor=executor,
            canonical_ledger_messages=canonical_ledger_messages,
            tool_specs=tool_specs,
            provider_capabilities=llm_client.native_tool_provider_capabilities,
            request_measure=request_measure,
            llm_config=llm_client.runtime_config,
            llm_client=llm_client,
        )

    def build(self, bundle: SubagentContextBundle, _tool_config: Any = None) -> tuple[Any, Any]:
        context = bundle.runtime_context
        child_tool_specs = context.tool_router.initial_provider_specs
        child_ledger_messages = list(bundle.p.ledger_messages())
        request_measure = self.request_measure
        compaction_invoke = None
        if self.llm_client is not None:
            def compact_child(request: NativeHistoryProviderInput) -> ProviderOutputEnvelope:
                from ownpi.runtime.native_tools.loop_engine.provider import NativeProviderSession

                return NativeProviderSession(self.llm_client, 'native_compaction').run(
                    prepared_turn=request, tools=(), tool_choice='none', streaming=True,
                    cancel_checker=context.cancel_checker,
                )

            compaction_invoke = compact_child

            def measure_child_request(conversation_input: Any, specs: Any) -> Any:
                return self.llm_client.measure_native_tool_request(
                    messages=child_ledger_messages,
                    native_conversation_input=conversation_input,
                    tools=list(specs),
                )

            request_measure = measure_child_request
        session = bundle.live.session
        if session is not None:
            session.update_for_continuation(
                context=replace(session.context, continuation_index=context.continuation_index,
                                cancel_checker=context.cancel_checker), tool_specs=child_tool_specs,
            )
            if bundle.continuation_message:
                from ownpi.runtime.native_tools.history_items import HistoryEntry, HistoryMessage

                session.continuation.append_input((HistoryEntry(HistoryMessage('user', bundle.continuation_message)),))
        else:
            session = self._new_session(bundle, context, child_tool_specs, child_ledger_messages,
                                        request_measure, compaction_invoke)
            bundle.live.session = session
        try:
            return self._assemble_engine(session, session.context)
        except BaseException as error:
            try:
                session.continuation.close()
            except BaseException as close_error:
                error.add_note(f'Child assembly close failed: {close_error!r}')
            raise

    def _new_session(self, bundle: SubagentContextBundle, context: Any, child_tool_specs: Any,
                     child_ledger_messages: Any, request_measure: Any, compaction_invoke: Any) -> CoreLoopSession:
        return CoreLoopSession(
            context=context,
            tool_specs=child_tool_specs, executor=self.executor,
            provider_capabilities=self.provider_capabilities,
            request_measure=request_measure,
            compaction_invoke=compaction_invoke,
            llm_config=self.llm_config,
            canonical_ledger_messages=child_ledger_messages,
            initial_conversation_messages=(
                ({'role': 'user', 'content': bundle.continuation_message},)
                if bundle.continuation_message else ()
            ),
            domain_policy=NativeToolLoopDomainPolicy(),
            resume_conversation_id=bundle.live.resume_conversation_id,
        )
    def _assemble_engine(self, session: CoreLoopSession, context: Any) -> tuple[Any, Any]:
        loop_executor = build_core_loop_executor(session, context)
        lifecycle = NativeSessionLifecycle(session.shared, CoreLoopBindings(session, context))
        from ownpi.runtime.native_tools.core_loop_recovery import (
            deliver_resumed_results,
        )

        deliver_resumed_results(session=session, context=context)
        if session.terminal_result is not None:
            lifecycle.after_recovery()
        if session.terminal_result is not None:
            terminal = session.terminal_result
            session.continuation.execute = _TerminalSubagentLoop(NativeLoopResult(
                status="failed" if terminal.status == "user_cancelled" else "completed",
                turns=terminal.turns, tool_call_count=len(terminal.tool_outcomes),
                terminal_reason=terminal.stop_reason,
            )).run
            return session.continuation, None
        engine = build_native_session_engine(
            session=session.shared,
            tool_surface=session.tool_surface,
            turn_runner=RecordingTurnRunner(ProjectedNativeProviderTurnRunner(
                provider_turn=bind_main_provider_request(session, self.provider_turn),
                response_projector=project_native_tool_provider_turn,
            ), session.shared.history),
            executor=loop_executor,
            policy=SubagentLoopPolicy(
                tool_specs=session.tool_surface.executable_specs,
                completion_tool_name="complete_subagent",
            ),
            observer=CoreLoopObserver(), lifecycle_bindings=CoreLoopBindings(session, context),
            batch_admission_specs=tuple(
                NativeToolSpec.from_tool_definition(definition)
                for definition in context.tool_router.registry.definitions
            ),
            run_scope=SubagentProviderRunScope(
                cancel_checker_setter=getattr(self.llm_client, "set_cancel_checker", None),
            ),
        )
        metadata = context.state_store.state.run_metadata
        invocation = NativeLoopInvocation(
            identity=NativeLoopIdentity(
                session_id=str(metadata.session_id or ""), task_id=str(metadata.task_id or ""),
                run_id=str(metadata.run_id or ""), actor_kind="subagent", actor_id=str(context.actor_id or ""),
            ),
            paths=NativeLoopPaths(project_root=context.project_root, run_dir=context.run_dir),
            services=NativeLoopServices(cancel_checker=context.cancel_checker if callable(context.cancel_checker) else None),
        )
        session.continuation.execute = engine.run
        return session.continuation, invocation
