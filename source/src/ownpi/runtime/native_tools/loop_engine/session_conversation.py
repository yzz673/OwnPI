from __future__ import annotations

from ownpi.runtime.native_tools.loop_engine.session import NativeLoopSession
from ownpi.runtime.native_tools.history_items import HistoryEntry
from ownpi.runtime.native_tools.loop_engine.contracts import NativeExecutionRequest, NativeLoopToolValue
from ownpi.runtime.native_tools.provider import ProviderOutputEnvelope
from ownpi.runtime.native_tools.spec import NativeToolError
from ownpi.runtime.native_tools.tool_invocation import NativeToolInvocation
from ownpi.runtime.native_tools.tool_output import error_output


class NativeSessionConversation:
    def __init__(self, session: NativeLoopSession) -> None:
        self._session = session

    @property
    def history_snapshot(self) -> tuple[HistoryEntry, ...]:
        return self._session.history.snapshot

    @property
    def provider_input(self) -> object:
        request = self._session.current_request
        if request is None:
            raise RuntimeError('Core loop turn was not prepared')
        return request.conversation_input

    def append_response(self, response: ProviderOutputEnvelope, *, turn_index: int) -> None:
        session = self._session
        history = session.history
        assert history is not None
        history.record_provider(response)
        history.record_usage(response)
        session.current_response = response
        session.bindings.provider_recorded(response)
        request = session.current_request
        if request is None:
            return
        history.mark_request_consumed(request.conversation_input.items)
        session.bindings.confirm_visible_file_pages(history.consumed_outputs)
        session.history_usage.completed(
            response, history.snapshot, history.generation, request.measurement,
        )
        session.context_runtime_manager.commit_provider_usage(
            request.context_preparation_id, response.usage,
        )
        session.current_request = None
        session.bindings.consume_compact_hook_deliveries(
            history, request.conversation_input.items,
        )

    def append_result(
        self, result: NativeLoopToolValue, *, call: NativeToolInvocation,
        turn_index: int, exchange_id: str = '',
    ) -> HistoryEntry | None:
        session = self._session
        entry = session.bindings.record_tool_delivery(
            session.history, result, call=call,
            exchange_id=exchange_id or self._exchange_id(),
        )
        return entry

    def _exchange_id(self) -> str:
        response = self._session.current_response
        return response.exchange_id if response is not None else ''

    def flush_tool_context(self, *, calls: tuple[NativeExecutionRequest, ...]) -> None:
        self._session.bindings.record_tool_context(
            self._session.history, exchange_id=self._exchange_id(), calls=calls,
        )

    def append_correction(
        self, text: str, *, turn_index: int, correction_index: int | None = None,
    ) -> None:
        self._session.history.record_message('user', text, exchange_id=self._exchange_id())

    def append_user_message(self, text: str, *, exchange_id: str = '') -> None:
        self._session.history.record_message('user', text, exchange_id=exchange_id)

    def append_error(
        self, error: NativeToolError, *, turn_index: int, exchange_id: str = '',
        output_sequence_index: int | None = None,
        call: NativeExecutionRequest | None = None,
    ) -> None:
        self._session.history.record_items((HistoryEntry(
            error_output(error, call=call), exchange_id or self._exchange_id(), output_sequence_index,
        ),))


__all__ = ['NativeSessionConversation']
