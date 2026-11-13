from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from ownpi.llm.streaming import CanonicalResponseEvent
from ownpi.runtime.native_tools.core_loop_models import (
    NativeToolLoopDomainPolicy,
    NativeToolLoopRequest,
    NativeToolLoopResult,
    NativeToolProviderTurn,
    NativeToolProviderTurnCallable,
    NativeToolRequestMeasureCallable,
    project_native_tool_provider_turn,
)
from ownpi.runtime.native_tools.executor import NativeToolExecutor
from ownpi.runtime.native_tools.compaction.local import NativeCompactionInvoke
from ownpi.runtime.native_tools.provider import (
    NativeToolProviderCapabilities,
    ProviderOutputEnvelope,
)
from ownpi.runtime.native_tools.provider_input import (
    NativeHistoryProviderInput,
)
from ownpi.runtime.native_tools.history_items import HistoryEntry, HistoryMessage
from ownpi.runtime.native_tools.provider_request_measurement import (
    NativeProviderRequestMeasurement,
    measure_native_provider_request,
)
from ownpi.runtime.native_tools.spec import NativeToolSpec
from ownpi.runtime.native_tools.loop_engine.provider import (
    StreamingNativeProviderTurnRunner,
    NativeProviderSession,
)
from ownpi.runtime.native_tools.session_adapter import bind_main_provider_request
from ownpi.runtime.native_tools.loop_engine.session import build_native_session_engine
from ownpi.runtime.tools.runtime import ToolRuntimeContext

if TYPE_CHECKING:
    from ownpi.runtime.native_tools.core_loop_session import CoreLoopSession
    from ownpi.llm.base import LLMClient
    from ownpi.schemas.runtime import LLMConfig


class CoreAgentLoopAdapter:
    """Provider-neutral native tool loop skeleton."""

    def __init__(
        self,
        *,
        executor: NativeToolExecutor,
        provider_turn: NativeToolProviderTurnCallable,
        canonical_ledger_messages: list[dict[str, Any]],
        initial_conversation_messages: tuple[dict[str, Any], ...] = (),
        compaction_invoke: NativeCompactionInvoke | None = None,
        llm_config: 'LLMConfig | None' = None,
        provider_capabilities: NativeToolProviderCapabilities | None = None,
        request_measure: NativeToolRequestMeasureCallable | None = None,
        cancel_checker_setter: Callable[[Callable[[], bool] | None], None] | None = None,
        domain_policy: NativeToolLoopDomainPolicy | None = None,
        resume_conversation_id: str | None = None,
    ) -> None:
        self._executor = executor
        self._provider_turn = provider_turn
        self._compaction_invoke = compaction_invoke
        self._llm_config = llm_config
        if not canonical_ledger_messages:
            raise ValueError("native tool loop requires a canonical ledger prefix")
        self._canonical_ledger_messages = [
            dict(message) for message in canonical_ledger_messages
        ]
        self._initial_conversation_messages = tuple(
            dict(message) for message in initial_conversation_messages
        )
        self._provider_capabilities = provider_capabilities or NativeToolProviderCapabilities()
        self._request_measure = request_measure or (
            lambda conversation_input, specs: measure_native_provider_request(
                conversation_input=conversation_input,
                tool_specs=specs,
            )
        )
        self._cancel_checker_setter = cancel_checker_setter
        self._domain_policy = domain_policy or NativeToolLoopDomainPolicy()
        self._session: CoreLoopSession | None = None
        self._resume_conversation_id = resume_conversation_id
        self._stream_event_handler_setter: Callable[
            [Callable[[CanonicalResponseEvent], None] | None], None
        ] | None = None

    @property
    def session(self) -> 'CoreLoopSession | None':
        return self._session

    def close(self) -> bool:
        session = self._session
        if session is None:
            return True
        return session.continuation.close()

    def set_stream_event_handler(
        self,
        handler: Callable[[CanonicalResponseEvent], None] | None,
    ) -> None:
        if self._stream_event_handler_setter is not None:
            self._stream_event_handler_setter(handler)

    @classmethod
    def from_llm_client(
        cls,
        *,
        executor: NativeToolExecutor,
        llm_client: 'LLMClient',
        canonical_ledger_messages: list[dict[str, Any]],
        initial_conversation_messages: tuple[dict[str, Any], ...] = (),
        request_kind: str = 'native_tool_turn',
        stream_event_handler: Callable[[CanonicalResponseEvent], None] | None = None,
        domain_policy: NativeToolLoopDomainPolicy | None = None,
        resume_conversation_id: str | None = None,
    ) -> 'CoreAgentLoopAdapter':
        cancel_state: dict[str, Callable[[], bool] | None] = {'checker': None}
        stream_state: dict[str, Callable[[CanonicalResponseEvent], None] | None] = {
            'handler': stream_event_handler,
        }
        provider_runner = StreamingNativeProviderTurnRunner(
            client=llm_client,
            request_kind=request_kind,
            stream_event_handler=lambda event: (
                stream_state['handler'](event)
                if stream_state['handler'] is not None
                else None
            ),
        )

        def provider_turn(request: NativeToolLoopRequest) -> NativeToolProviderTurn:
            response = provider_runner.invoke(
                prepared_turn=request.conversation_input,
                tools=request.tool_specs,
                cancel_checker=cancel_state['checker'],
            )
            return NativeToolProviderTurn(
                response=response,
            )

        def compaction_invoke(request: NativeHistoryProviderInput) -> ProviderOutputEnvelope:
            return NativeProviderSession(llm_client, 'native_compaction').run(
                prepared_turn=request, tools=(), tool_choice='none', streaming=True,
                cancel_checker=cancel_state['checker'],
            )

        ledger_messages = [dict(message) for message in canonical_ledger_messages]

        def request_measure(
            conversation_input: NativeHistoryProviderInput,
            specs: tuple[NativeToolSpec, ...],
        ) -> NativeProviderRequestMeasurement:
            return llm_client.measure_native_tool_request(
                messages=list(ledger_messages),
                native_conversation_input=conversation_input,
                tools=list(specs),
            )

        adapter = cls(
            executor=executor,
            provider_turn=provider_turn,
            compaction_invoke=compaction_invoke,
            llm_config=llm_client.runtime_config,
            canonical_ledger_messages=ledger_messages,
            initial_conversation_messages=initial_conversation_messages,
            provider_capabilities=llm_client.native_tool_provider_capabilities,
            request_measure=request_measure,
            cancel_checker_setter=lambda checker: cancel_state.__setitem__('checker', checker),
            domain_policy=domain_policy,
            resume_conversation_id=resume_conversation_id,
        )
        adapter._stream_event_handler_setter = lambda handler: stream_state.__setitem__(
            'handler', handler
        )
        return adapter

    def run(
        self,
        *,
        context: ToolRuntimeContext,
        tool_specs: tuple[NativeToolSpec, ...],
        session: 'CoreLoopSession | None' = None,
        close_session: bool = True,
        prepare_session: Callable[['CoreLoopSession'], None] | None = None,
    ) -> NativeToolLoopResult:
        return self._run_bound(
            context=context,
            tool_specs=tool_specs,
            session=session,
            close_session=close_session,
            prepare_session=prepare_session,
        )

    def prepare(
        self, *, context: ToolRuntimeContext, tool_specs: tuple[NativeToolSpec, ...],
        session: 'CoreLoopSession | None' = None,
    ) -> 'CoreLoopSession':
        """Bind a real conversation without entering a provider turn."""
        from ownpi.runtime.native_tools.core_loop_session import CoreLoopSession

        if session is None:
            session = CoreLoopSession(
                context=context, tool_specs=tool_specs, executor=self._executor,
                provider_capabilities=self._provider_capabilities,
                request_measure=self._request_measure, compaction_invoke=self._compaction_invoke,
                llm_config=self._llm_config, canonical_ledger_messages=self._canonical_ledger_messages,
                initial_conversation_messages=self._initial_conversation_messages,
                domain_policy=self._domain_policy, resume_conversation_id=self._resume_conversation_id,
            )
        else:
            session.update_for_continuation(context=context, tool_specs=tool_specs)
        self._session = session
        return session

    def _run_bound(
        self,
        *,
        context: ToolRuntimeContext,
        tool_specs: tuple[NativeToolSpec, ...],
        session: 'CoreLoopSession | None',
        close_session: bool,
        prepare_session: Callable[['CoreLoopSession'], None] | None = None,
    ) -> NativeToolLoopResult:
        session = self.prepare(context=context, tool_specs=tool_specs, session=session)
        def outcome(result: NativeToolLoopResult) -> dict[str, str]:
            return dict(status='interrupted' if result.status in {
                    'user_cancelled', 'provider_or_protocol_failure', 'tool_result_projection_failure',
                    'local_runtime_failure',
                    'awaiting_user_interaction',
                } else 'completed',
                reason=result.failure_class or result.stop_reason or result.status,
            )
        try:
            return session.continuation.run_segment(
                lambda: self._run_session(session, session.context), outcome,
                **({'prepare_input': lambda: prepare_session(session)} if prepare_session else {}),
            )
        finally:
            if close_session:
                session.continuation.close()
    def _run_session(
        self, session: CoreLoopSession, context: ToolRuntimeContext,
    ) -> NativeToolLoopResult:
        from ownpi.runtime.native_tools.core_loop_execution import build_core_loop_executor
        from ownpi.runtime.native_tools.loop_engine.session_lifecycle import NativeSessionLifecycle
        from ownpi.runtime.native_tools.core_loop_bindings import CoreLoopBindings
        from ownpi.runtime.native_tools.core_loop_observer import CoreLoopObserver
        from ownpi.runtime.native_tools.core_loop_policy import CoreLoopPolicy
        from ownpi.runtime.native_tools.loop_engine import NativeLoopIdentity, NativeLoopInvocation, NativeLoopPaths, NativeLoopServices, ProjectedNativeProviderTurnRunner, ProviderTurnRunScope
        from ownpi.runtime.rollout.session import RecordingTurnRunner

        lifecycle = NativeSessionLifecycle(session.shared, CoreLoopBindings(session, context))
        from ownpi.runtime.native_tools.core_loop_recovery import (
            deliver_resumed_results,
        )

        deliver_resumed_results(session=session, context=context)
        if session.terminal_result is not None:
            lifecycle.after_recovery()
        if session.terminal_result is not None:
            return session.terminal_result
        run_metadata = context.state_store.state.run_metadata
        invocation = NativeLoopInvocation(
            identity=NativeLoopIdentity(
                session_id=str(run_metadata.session_id or ""),
                task_id=str(run_metadata.task_id or ""),
                run_id=str(run_metadata.run_id or ""),
                actor_kind=context.actor_kind,
                actor_id=str(context.actor_id or ""),
            ),
            paths=NativeLoopPaths(
                project_root=context.project_root,
                run_dir=context.run_dir,
            ),
            services=NativeLoopServices(
                cancel_checker=(
                    context.cancel_checker if callable(context.cancel_checker) else None
                )
            ),
        )
        kernel_result = build_native_session_engine(
            session=session.shared,
            tool_surface=session.tool_surface,
            turn_runner=RecordingTurnRunner(ProjectedNativeProviderTurnRunner(
                provider_turn=bind_main_provider_request(session, self._provider_turn),
                response_projector=project_native_tool_provider_turn,
            ), session.shared.history),
            executor=build_core_loop_executor(session, context),
            policy=CoreLoopPolicy(
                session=session,
                context=context,
            ),
            observer=CoreLoopObserver(),
            lifecycle_bindings=CoreLoopBindings(session, context),
            batch_admission_specs=tuple(
                NativeToolSpec.from_tool_definition(definition)
                for definition in context.tool_router.registry.definitions
            ),
            run_scope=ProviderTurnRunScope(
                cancel_checker_setter=self._cancel_checker_setter,
            ),
        ).run(invocation)
        if session.terminal_result is not None:
            return session.terminal_result
        return NativeToolLoopResult(
            status=(
                "tool_result_projection_failure"
                if kernel_result.failure_class == "tool_result_projection_failure"
                else "local_runtime_failure"
                if kernel_result.failure_class == "local_runtime_failure"
                else "provider_or_protocol_failure"
            ),
            turns=kernel_result.turns,
            tool_outcomes=tuple(session.execution.all_outcomes),
            failure_class=kernel_result.failure_class or "core_loop_protocol_failure",
            failure_diagnostics={"message": kernel_result.failure_message},
            # The current attempt failed, but the loop failure alone does not
            # establish that the durable run boundary is unusable.
            auto_resume_safe=False,
            recommended_recovery="resume_from_latest_valid_checkpoint",
        )


class CoreAgentLoopContinuation:
    """Own one live native session across continuation segments."""

    def __init__(self, *, identity: tuple[str, ...]) -> None:
        self.identity = identity
        self.adapter: CoreAgentLoopAdapter | None = None
        self.session: 'CoreLoopSession | None' = None

    def attach(self, adapter: CoreAgentLoopAdapter) -> None:
        if self.session is not None and self.session.continuation.closed:
            raise RuntimeError('cannot attach to a closed continuation')
        if self.adapter is not None and self.adapter is not adapter:
            raise RuntimeError('continuation already owns a native adapter')
        self.adapter = adapter

    def run(
        self,
        *,
        context: ToolRuntimeContext,
        tool_specs: tuple[NativeToolSpec, ...],
        prepare_session: Callable[['CoreLoopSession'], None] | None = None,
    ) -> NativeToolLoopResult:
        if self.adapter is None:
            raise RuntimeError('continuation has no native adapter')
        try:
            return self.adapter.run(
                context=context,
                tool_specs=tool_specs,
                session=self.session,
                close_session=False,
                prepare_session=prepare_session,
            )
        finally:
            self.session = self.adapter.session

    def append_pending_input(self, messages: tuple[dict[str, str], ...]) -> None:
        if self.session is None:
            raise RuntimeError('cannot append input before session initialization')
        for message in messages:
            self.session.continuation.append_input((
                HistoryEntry(
                    HistoryMessage('user', str(message.get('content') or '')),
                    exchange_id=(
                        f'runtime-continuation-'
                        f'{len(self.session.shared.pending_input) + 1}'
                    ),
                ),
            ))

    def close(self) -> bool:
        return self.adapter.close() if self.adapter is not None else True
