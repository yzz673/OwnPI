from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import CancelledError
from dataclasses import dataclass, field

from ownpi.controller.runtime_control_signal import RuntimeControlRequested
from ownpi.llm.provider_retry import RetryCancelled
from ownpi.runtime.native_tools.loop_engine.contracts import (
    NativeExecutionRequest,
    NativeLoopToolDelivery,
)
from ownpi.runtime.native_tools.spec import NativeToolCall, NativeToolError, NativeToolResult, NativeToolSpec
from ownpi.runtime.native_tools.tool_invocation import NativeToolInvocation, parse_tool_invocation
from ownpi.runtime.native_tools.tool_output import local_error_result


NativeToolHandler = Callable[[NativeToolCall], NativeToolResult]


@dataclass(frozen=True)
class CallableNativeToolExecutor:
    handlers: Mapping[str, NativeToolHandler]
    default_handler: NativeToolHandler | None = None
    tool_specs: tuple[NativeToolSpec, ...] = ()
    native_tool_strict: bool = False
    _retained_deliveries: list[NativeLoopToolDelivery] = field(
        default_factory=list, init=False, repr=False, compare=False,
    )

    def drain_retained_deliveries(self) -> tuple[NativeLoopToolDelivery, ...]:
        deliveries = tuple(self._retained_deliveries)
        self._retained_deliveries.clear()
        return deliveries

    def execute_many(
        self,
        calls: tuple[NativeExecutionRequest, ...],
    ) -> tuple[NativeLoopToolDelivery, ...]:
        self._retained_deliveries.clear()
        for call in calls:
            result = self._execute_one(call)
            self._retained_deliveries.append((call, result))
        return tuple(self._retained_deliveries)

    def _execute_one(self, call: NativeExecutionRequest) -> NativeToolResult:
        if isinstance(call, NativeToolInvocation):
            spec = next((spec for spec in self.tool_specs if spec.name == call.tool_name), None)
            parsed = parse_tool_invocation(
                call, input_schema=spec.input_schema if spec is not None else None,
                native_tool_strict=self.native_tool_strict,
            )
            if isinstance(parsed, NativeToolError):
                return local_error_result(parsed)
            call = parsed
        handler = self.handlers.get(call.tool_name)
        if handler is None:
            handler = self.default_handler
        if handler is None:
            return local_error_result(NativeToolError(
                tool_name=call.tool_name,
                request_id=call.request_id,
                provider_call_id=call.provider_call_id,
                error_message=f"unknown native tool: {call.tool_name}",
                failure_class="unknown_tool",
            ))
        try:
            return handler(call)
        except (RuntimeControlRequested, CancelledError, RetryCancelled):
            raise
        except Exception as error:
            return local_error_result(NativeToolError(
                tool_name=call.tool_name,
                request_id=call.request_id,
                provider_call_id=call.provider_call_id,
                error_message=str(error),
                failure_class="tool_runtime_error",
                metadata={
                    "exception_type": error.__class__.__name__,
                },
            ))


__all__ = ["CallableNativeToolExecutor", "NativeToolHandler"]
