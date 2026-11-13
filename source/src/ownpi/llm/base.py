from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any
from weakref import ReferenceType, ref

from ownpi.llm.streaming import CanonicalResponseEvent, LLMStreamEvent

if TYPE_CHECKING:
    from ownpi.schemas.runtime import LLMConfig
    from ownpi.llm.atomic_tools import AtomicToolCallResult
    from ownpi.runtime.native_tools.provider_input import NativeHistoryProviderInput
    from ownpi.runtime.native_tools.provider import (
        NativeToolProviderCapabilities,
        ProviderOutputEnvelope,
    )
    from ownpi.runtime.native_tools.spec import NativeToolSpec


_CancelChecker = Callable[[], bool]
_cancel_checker_context: ContextVar[
    tuple[tuple[ReferenceType[object], _CancelChecker], ...]
] = ContextVar("ownpi_llm_cancel_checker", default=())


def current_cancel_checker(client: object | None = None) -> _CancelChecker | None:
    """Return the current cancellation probe for a client and context."""
    for owner, checker in reversed(_cancel_checker_context.get()):
        bound_client = owner()
        if bound_client is not None and (client is None or bound_client is client):
            return checker
    return None


class LLMClient(ABC):
    @property
    def runtime_config(self) -> LLMConfig | None:
        return getattr(self, "_config", None)

    def set_cancel_checker(self, checker: Callable[[], bool] | None) -> None:
        """Bind the current run's cooperative cancellation probe, if any."""
        bindings = [(owner, probe) for owner, probe in _cancel_checker_context.get()
                    if owner() is not None]
        if checker is None:
            for index in range(len(bindings) - 1, -1, -1):
                if bindings[index][0]() is self:
                    del bindings[index]
                    break
        else:
            bindings.append((ref(self), checker))
        _cancel_checker_context.set(tuple(bindings))

    @abstractmethod
    def generate_text(self, prompt: str) -> str:
        raise NotImplementedError

    def generate_text_response(self, prompt: str, *, request_kind: str) -> str:
        del request_kind
        return self.generate_text(prompt)

    def generate_text_response_with_handler(
        self,
        prompt: str,
        *,
        request_kind: str,
        response_text_handler: Callable[[str], None],
    ) -> str:
        return self._handle_text_response(
            self.generate_text_response(prompt, request_kind=request_kind),
            response_text_handler=response_text_handler,
        )

    @staticmethod
    def _handle_text_response(
        response_text: str,
        *,
        response_text_handler: Callable[[str], None],
    ) -> str:
        if not isinstance(response_text, str) or not response_text.strip():
            raise RuntimeError('Text LLM provider completed without a non-empty response.')
        response_text_handler(response_text)
        return response_text

    @abstractmethod
    def generate_json(self, prompt: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def generate_structured_json(
        self,
        prompt: str,
        *,
        schema: dict[str, Any],
        request_kind: str,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def generate_structured_json_from_messages(
        self,
        *,
        messages: list[dict[str, Any]],
        prompt_for_fallback: str,
        schema: dict[str, Any],
        request_kind: str,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        del messages
        return self.generate_structured_json(
            prompt_for_fallback,
            schema=schema,
            request_kind=request_kind,
            schema_name=schema_name,
        )

    def stream_structured_json(
        self,
        prompt: str,
        *,
        schema: dict[str, Any],
        request_kind: str,
        schema_name: str | None = None,
    ) -> Iterator[LLMStreamEvent]:
        yield LLMStreamEvent(
            event_type='request_started',
            request_kind=request_kind,
            response_mode='structured_json',
        )
        payload = self.generate_structured_json(
            prompt,
            schema=schema,
            request_kind=request_kind,
            schema_name=schema_name,
        )
        yield LLMStreamEvent(
            event_type='completed',
            request_kind=request_kind,
            response_mode='structured_json',
            payload={'json_payload': payload},
        )

    def stream_structured_json_from_messages(
        self,
        *,
        messages: list[dict[str, Any]],
        prompt_for_fallback: str,
        schema: dict[str, Any],
        request_kind: str,
        schema_name: str | None = None,
    ) -> Iterator[LLMStreamEvent]:
        del messages
        yield from self.stream_structured_json(
            prompt_for_fallback,
            schema=schema,
            request_kind=request_kind,
            schema_name=schema_name,
        )

    def complete_with_native_tools(
        self,
        *,
        messages: list[dict[str, Any]],
        native_conversation_input: 'NativeHistoryProviderInput',
        tools: list['NativeToolSpec'],
        request_kind: str = 'native_tool_turn',
        tool_choice: str = 'auto',
    ) -> 'ProviderOutputEnvelope':
        del (
            messages,
            native_conversation_input,
            tools,
            request_kind,
            tool_choice,
        )
        raise NotImplementedError(f'{self.__class__.__name__} does not support provider-native tool calling.')

    def stream_with_native_tools(
        self,
        *,
        messages: list[dict[str, Any]],
        native_conversation_input: 'NativeHistoryProviderInput',
        tools: list['NativeToolSpec'],
        request_kind: str = 'native_tool_turn',
        tool_choice: str = 'auto',
    ) -> Iterator[CanonicalResponseEvent]:
        from ownpi.runtime.native_tools.streaming import blocking_native_response_events

        call_kwargs: dict[str, Any] = {
            'messages': messages,
            'native_conversation_input': native_conversation_input,
            'tools': tools,
            'request_kind': request_kind,
        }
        if tool_choice != 'auto':
            call_kwargs['tool_choice'] = tool_choice
        response = self.complete_with_native_tools(**call_kwargs)
        yield from blocking_native_response_events(
            response,
            request_kind=request_kind,
            provider_protocol='blocking_adapter',
        )

    def complete_required_tool_call(
        self,
        *,
        immutable_prefix_messages: tuple[dict[str, Any], ...],
        conversation_messages: tuple[dict[str, Any], ...] = (),
        tool: 'NativeToolSpec',
        request_kind: str,
    ) -> 'AtomicToolCallResult':
        from ownpi.llm.atomic_tools import (
            build_atomic_provider_input,
            validate_atomic_tool_response,
        )
        from ownpi.runtime.native_tools.loop_engine.provider import (
            NativeProviderSession,
        )

        provider_input = build_atomic_provider_input(
            immutable_prefix_messages=immutable_prefix_messages,
            conversation_messages=conversation_messages,
        )
        from ownpi.runtime.context.manager import ContextRuntimeManager

        context_manager = ContextRuntimeManager(llm_config=self.runtime_config)
        prepared_context = context_manager.prepare_provider_context(
            build_input=lambda: provider_input,
            tool_specs=(tool,),
            measure=lambda input_value, specs: self.measure_native_tool_request(
                messages=list(input_value.immutable_prefix_messages),
                native_conversation_input=input_value,
                tools=list(specs),
            ),
        )
        try:
            response = NativeProviderSession(
                client=self,
                request_kind=request_kind,
            ).run(
                prepared_turn=prepared_context.provider_input,
                tools=(tool,), tool_choice='required', streaming=False,
            )
        except BaseException:
            context_manager.abort_provider_context(
                prepared_context.context_preparation_id,
            )
            raise
        context_manager.commit_provider_usage(
            prepared_context.context_preparation_id,
            response.usage,
        )
        return validate_atomic_tool_response(
            response, expected_tool=tool,
            native_tool_strict=self.native_tool_provider_capabilities.native_tool_strict,
        )

    def measure_native_tool_request(
        self,
        *,
        messages: list[dict[str, Any]],
        native_conversation_input: 'NativeHistoryProviderInput',
        tools: list['NativeToolSpec'],
    ) -> Any:
        del messages
        from ownpi.runtime.native_tools.provider_request_measurement import (
            measure_native_provider_request,
        )

        return measure_native_provider_request(
            conversation_input=native_conversation_input,
            tool_specs=tools,
        )

    @property
    def supports_native_tool_calling(self) -> bool:
        return False

    @property
    def supports_image_input(self) -> bool:
        return False

    @property
    def native_tool_provider_capabilities(self) -> 'NativeToolProviderCapabilities':
        from ownpi.runtime.native_tools.provider import NativeToolProviderCapabilities

        return NativeToolProviderCapabilities()
