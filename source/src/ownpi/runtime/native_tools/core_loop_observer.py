from __future__ import annotations

from ownpi.runtime.native_tools.loop_engine.contracts import NativeExecutionRequest, NativeLoopResult
from ownpi.runtime.native_tools.provider import ProviderOutputEnvelope
from ownpi.runtime.native_tools.spec import NativeToolResult


class CoreLoopObserver:
    """No-op observer satisfying the shared Native Tool Loop observation port."""

    @property
    def events(self) -> tuple[dict[str, object], ...]:
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
        result: NativeToolResult,
    ) -> None:
        del turn_index, call, result

    def correction(self, *, turn_index: int, text: str, reason: str) -> None:
        del turn_index, text, reason

    def completed(self, result: NativeLoopResult) -> None:
        del result

    def failed(self, result: NativeLoopResult) -> None:
        del result


__all__ = ["CoreLoopObserver"]
