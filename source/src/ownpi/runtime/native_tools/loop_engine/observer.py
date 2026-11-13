from __future__ import annotations

from typing import Any

from ownpi.runtime.native_tools.loop_engine.contracts import NativeExecutionRequest, NativeLoopResult, NativeLoopToolValue
from ownpi.runtime.native_tools.provider import ProviderOutputEnvelope, ProviderOutputItemKind
from ownpi.runtime.native_tools.spec import NativeToolResult


class NativeLoopObserver:
    def __init__(
        self,
        *,
        event_prefix: str,
        base_payload: dict[str, Any] | None = None,
        include_status: bool = False,
    ) -> None:
        self._event_prefix = event_prefix
        self._base_payload = dict(base_payload or {})
        self._include_status = include_status
        self._events: list[dict[str, Any]] = []

    @property
    def events(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._events)

    def add(self, event_type: str, **payload: Any) -> None:
        event = {"event_type": event_type, **self._base_payload, **payload}
        self._events.append(event)

    def loop_started(self) -> None:
        payload = {"turn_index": 0}
        if self._include_status:
            payload["status"] = "started"
        self.add(f"{self._event_prefix}_started", **payload)

    def provider_started(self, *, turn_index: int) -> None:
        payload: dict[str, Any] = {"turn_index": turn_index}
        if self._include_status:
            payload["status"] = "started"
        self.add(f"{self._event_prefix}_provider_turn_started", **payload)

    def provider_completed(
        self,
        *,
        turn_index: int,
        response: ProviderOutputEnvelope,
        provider_request_id: str,
    ) -> None:
        payload: dict[str, Any] = {
            "turn_index": turn_index,
            "provider_request_id": provider_request_id,
            "assistant_text_chars": len(response.assistant_text),
            "tool_call_count": sum(item.kind is ProviderOutputItemKind.TOOL_CALL for item in response.completed_items),
            "usage": dict(response.usage),
        }
        if self._include_status:
            payload["status"] = "completed"
        self.add(f"{self._event_prefix}_provider_turn_completed", **payload)

    def tool_called(self, *, turn_index: int, call: NativeExecutionRequest) -> None:
        payload: dict[str, Any] = {
            "turn_index": turn_index,
            "tool_name": call.tool_name,
            "provider_call_id": call.provider_call_id,
        }
        if self._include_status:
            payload["status"] = "started"
        self.add(f"{self._event_prefix}_tool_called", **payload)

    def tool_result(
        self,
        *,
        turn_index: int,
        call: NativeExecutionRequest,
        result: NativeLoopToolValue,
    ) -> None:
        if isinstance(result, NativeToolResult):
            status = result.status
            failure_class = str(result.metadata.get('failure_class') or '')
        elif result.native_error is not None:
            status, failure_class = 'failed', result.native_error.failure_class
        elif result.invocation is not None:
            status = result.invocation.record.status
            failure_class = str(result.invocation.record.context_effects.get('failure_class') or '')
        else:
            status, failure_class = 'diagnostic', ''
        self.add(
            f"{self._event_prefix}_tool_result",
            turn_index=turn_index,
            tool_name=call.tool_name,
            provider_call_id=call.provider_call_id,
            status=status,
            failure_class=failure_class,
        )

    def correction(self, *, turn_index: int, text: str, reason: str) -> None:
        del text
        payload: dict[str, Any] = {"turn_index": turn_index, "reason": reason}
        if self._include_status:
            payload["status"] = "continued"
        self.add(f"{self._event_prefix}_protocol_correction", **payload)

    def completed(self, result: NativeLoopResult) -> None:
        del result

    def failed(self, result: NativeLoopResult) -> None:
        payload: dict[str, Any] = {
            "failure_class": result.failure_class,
            "failure_message": result.failure_message[:500],
        }
        if self._include_status:
            payload["status"] = "failed"
        self.add(f"{self._event_prefix}_failed", **payload)


class NullNativeLoopObserver:
    @property
    def events(self) -> tuple[dict[str, Any], ...]:
        return ()

    def loop_started(self) -> None:
        return None

    def provider_started(self, *, turn_index: int) -> None:
        del turn_index

    def provider_completed(
        self,
        *,
        turn_index: int,
        response: ProviderOutputEnvelope,
        provider_request_id: str,
    ) -> None:
        del turn_index, response, provider_request_id

    def tool_called(self, *, turn_index: int, call: NativeExecutionRequest) -> None:
        del turn_index, call

    def tool_result(
        self,
        *,
        turn_index: int,
        call: NativeExecutionRequest,
        result: NativeLoopToolValue,
    ) -> None:
        del turn_index, call, result

    def correction(self, *, turn_index: int, text: str, reason: str) -> None:
        del turn_index, text, reason

    def completed(self, result: NativeLoopResult) -> None:
        del result

    def failed(self, result: NativeLoopResult) -> None:
        del result


__all__ = ["NativeLoopObserver", "NullNativeLoopObserver"]
