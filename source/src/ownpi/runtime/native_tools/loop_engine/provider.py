from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Generic, Iterator, TypeVar, cast, TYPE_CHECKING

from ownpi.llm.base import LLMClient
from ownpi.llm.errors import normalize_provider_context_overflow
from ownpi.llm.provider_request_context import (
    ProviderTurnSession,
    bind_provider_turn_session,
)
from ownpi.llm.streaming import CanonicalResponseEvent, CanonicalResponseEventType
from ownpi.runtime.native_tools.loop_engine.contracts import NativeLoopInvocation, NativePreparedRequest
from ownpi.runtime.native_tools.provider import ProviderOutputEnvelope
from ownpi.runtime.native_tools.provider_input import NativeHistoryProviderInput
from ownpi.runtime.native_tools.spec import NativeToolSpec
from ownpi.runtime.native_tools.streaming import (
    CanonicalNativeStreamAccumulator,
)


PreparedTurnT = TypeVar("PreparedTurnT")
ProviderTurnT = TypeVar("ProviderTurnT")

if TYPE_CHECKING:
    from pathlib import Path
    from ownpi.runtime.context.manager import ContextRuntimeManager
    from ownpi.runtime.native_tools.loop_engine.session import NativeLoopSession
    from ownpi.runtime.native_tools.loop_engine.session_bindings import NativeSessionBindings
    from ownpi.runtime.rollout.resume import ResumedConversation
    from ownpi.schemas.runtime import NativeConversationCompactionConfig


@dataclass(frozen=True)
class ProjectedNativeProviderTurnRunner(Generic[PreparedTurnT, ProviderTurnT]):
    provider_turn: Callable[[PreparedTurnT], ProviderTurnT]
    response_projector: Callable[[ProviderTurnT], ProviderOutputEnvelope]

    def invoke(self, *, prepared_turn: object, tool_choice: str) -> ProviderOutputEnvelope:
        del tool_choice
        turn = self.provider_turn(cast(PreparedTurnT, prepared_turn))
        return self.response_projector(turn)


@dataclass(frozen=True)
class NativeProviderSession:
    client: LLMClient
    request_kind: str
    stream_event_handler: Callable[[CanonicalResponseEvent], None] | None = None

    def run(
        self,
        *,
        prepared_turn: NativeHistoryProviderInput,
        tools: tuple[NativeToolSpec, ...],
        tool_choice: str,
        cancel_checker: Callable[[], bool] | None = None,
        streaming: bool,
    ) -> ProviderOutputEnvelope:
        call_kwargs = self._call_kwargs(
            prepared_turn=prepared_turn,
            tools=tools,
            tool_choice=tool_choice,
            include_auto_tool_choice=not streaming,
        )
        try:
            if not streaming:
                return self.client.complete_with_native_tools(**call_kwargs)
            return self._run_stream(
                call_kwargs=call_kwargs,
                cancel_checker=cancel_checker,
            )
        except BaseException as exc:
            normalized = normalize_provider_context_overflow(exc)
            if normalized is not exc:
                if hasattr(exc, 'provider_output_failure'):
                    setattr(normalized, 'provider_output_failure', getattr(exc, 'provider_output_failure'))
                if hasattr(exc, 'provider_output_source'):
                    setattr(normalized, 'provider_output_source', getattr(exc, 'provider_output_source'))
                raise normalized from exc
            raise

    def _call_kwargs(
        self,
        *,
        prepared_turn: NativeHistoryProviderInput,
        tools: tuple[NativeToolSpec, ...],
        tool_choice: str,
        include_auto_tool_choice: bool,
    ) -> dict[str, Any]:
        call_kwargs: dict[str, Any] = {
            "messages": [
                dict(message) for message in prepared_turn.immutable_prefix_messages
            ],
            "native_conversation_input": prepared_turn,
            "tools": list(tools),
            "request_kind": self.request_kind,
        }
        if include_auto_tool_choice or tool_choice != "auto":
            call_kwargs["tool_choice"] = tool_choice
        return call_kwargs

    def _run_stream(
        self,
        *,
        call_kwargs: dict[str, Any],
        cancel_checker: Callable[[], bool] | None,
    ) -> ProviderOutputEnvelope:
        accumulator = CanonicalNativeStreamAccumulator()
        set_cancel_checker = getattr(self.client, "set_cancel_checker", None)
        try:
            if callable(set_cancel_checker):
                set_cancel_checker(cancel_checker)
            events = self.client.stream_with_native_tools(**call_kwargs)
            for event in events:
                accumulator.observe(event)
                if self.stream_event_handler is not None:
                    self.stream_event_handler(event)
                if event.type_value == CanonicalResponseEventType.RESPONSE_COMPLETED.value:
                    break
            return accumulator.finalize()
        except BaseException as error:
            setattr(error, 'provider_output_failure', accumulator.failure_facts(error))
            raise
        finally:
            if callable(set_cancel_checker):
                set_cancel_checker(None)


@dataclass(frozen=True)
class NativeProviderTurnRunner:
    client: LLMClient
    request_kind: str

    def invoke(
        self,
        *,
        prepared_turn: NativePreparedRequest,
        tool_choice: str,
    ) -> ProviderOutputEnvelope:
        return NativeProviderSession(
            client=self.client,
            request_kind=self.request_kind,
        ).run(
            prepared_turn=prepared_turn.conversation_input,
            tools=prepared_turn.tool_specs,
            tool_choice=tool_choice,
            streaming=False,
        )


def create_client_session(
    *, client: LLMClient, directory: 'Path', metadata: dict[str, Any],
    messages: list[dict[str, Any]], bindings: 'NativeSessionBindings',
    context_manager: 'ContextRuntimeManager | None' = None,
    replay: 'ResumedConversation | None' = None,
    compaction_config: 'NativeConversationCompactionConfig | None' = None,
) -> 'NativeLoopSession':
    from ownpi.runtime.context.manager import ContextRuntimeManager
    from ownpi.runtime.limits.tool_output import resolve_tool_result_truncation_policy
    from ownpi.runtime.native_tools.compaction.model_metadata import model_context_metadata_from_config
    from ownpi.runtime.native_tools.loop_engine.session import create_native_session
    from ownpi.schemas.runtime import NativeConversationRuntimeConfig

    config = client.runtime_config
    manager = context_manager or ContextRuntimeManager(state=None, llm_config=config)
    compaction_config = compaction_config or NativeConversationRuntimeConfig().conversation_compaction
    model = {key: getattr(config, key, None) for key in (
        'provider', 'model', 'reasoning_effort', 'reasoning_options',
        'max_output_tokens', 'context_window_limit',
    )}

    def measure(value: NativeHistoryProviderInput, specs: tuple[NativeToolSpec, ...]):
        return client.measure_native_tool_request(
            messages=list(value.immutable_prefix_messages), native_conversation_input=value, tools=list(specs),
        )

    def compact(value: NativeHistoryProviderInput) -> ProviderOutputEnvelope:
        return NativeProviderSession(client, 'native_compaction').run(
            prepared_turn=value, tools=(), tool_choice='none', streaming=False,
        )

    return create_native_session(
        directory=directory, metadata={**metadata, 'model': model},
        canonical_ledger_messages=messages, policy=resolve_tool_result_truncation_policy(config),
        context_runtime_manager=manager, request_measure=measure, bindings=bindings,
        provider_capabilities=client.native_tool_provider_capabilities, compaction_invoke=compact,
        model_metadata=model_context_metadata_from_config(
            llm_config=config, compaction_config=compaction_config,
        ), rollout_model=model, replay=replay,
    )


@dataclass(frozen=True)
class StreamingNativeProviderTurnRunner:
    client: LLMClient
    request_kind: str
    stream_event_handler: Callable[[CanonicalResponseEvent], None] | None = None

    def invoke(
        self,
        *,
        prepared_turn: NativeHistoryProviderInput,
        tools: tuple[NativeToolSpec, ...],
        cancel_checker: Callable[[], bool] | None = None,
    ) -> ProviderOutputEnvelope:
        return NativeProviderSession(
            client=self.client,
            request_kind=self.request_kind,
            stream_event_handler=self.stream_event_handler,
        ).run(
            prepared_turn=prepared_turn,
            tools=tools,
            cancel_checker=cancel_checker,
            tool_choice="auto",
            streaming=True,
        )


@dataclass(frozen=True)
class ProviderTurnRunScope:
    cancel_checker_setter: Callable[[Callable[[], bool] | None], None] | None = None

    @contextmanager
    def bind(self, invocation: NativeLoopInvocation) -> Iterator[None]:
        if self.cancel_checker_setter is not None:
            self.cancel_checker_setter(invocation.services.cancel_checker)
        try:
            with bind_provider_turn_session(ProviderTurnSession()):
                yield
        finally:
            if self.cancel_checker_setter is not None:
                self.cancel_checker_setter(None)


def provider_request_id(
    response: ProviderOutputEnvelope,
    *,
    include_tool_calls: bool = False,
) -> str:
    for key in ("request_id", "response_id", "id"):
        value = response.provider_metadata.get(key)
        if value:
            return str(value)
    if include_tool_calls:
        for item in response.completed_items:
            value = item.request_id or item.call_id
            if value:
                return str(value)
    return ""


def merge_usage(target: dict[str, int], source: dict[str, int]) -> None:
    for key, value in source.items():
        if isinstance(value, int) and not isinstance(value, bool):
            target[key] = target.get(key, 0) + value


__all__ = [
    "NativeProviderSession",
    "NativeProviderTurnRunner",
    "ProjectedNativeProviderTurnRunner",
    "ProviderTurnRunScope",
    "StreamingNativeProviderTurnRunner",
    "merge_usage",
    "provider_request_id",
]
