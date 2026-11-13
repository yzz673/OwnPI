from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, ContextManager, Literal, Protocol, Union

from ownpi.runtime.native_tools.provider import (
    ProviderOutputEnvelope,
)
from ownpi.runtime.native_tools.spec import (
    NativeToolCall,
    NativeToolError,
    NativeToolResult,
    NativeToolSpec,
)
from ownpi.runtime.native_tools.tool_invocation import NativeToolInvocation
from ownpi.runtime.native_tools.history_items import HistoryEntry
from ownpi.runtime.native_tools.provider_input import NativeHistoryProviderInput
from ownpi.runtime.native_tools.provider_request_measurement import NativeProviderRequestMeasurement
from ownpi.runtime.native_tools.provider import NativeToolProviderCapabilities


@dataclass(frozen=True)
class NativePreparedRequest:
    conversation_input: NativeHistoryProviderInput
    tool_specs: tuple[NativeToolSpec, ...]
    measurement: NativeProviderRequestMeasurement
    context_preparation_id: str
    immutable_prefix_digest: str
    provider_capabilities: NativeToolProviderCapabilities

    def __post_init__(self) -> None:
        if self.immutable_prefix_digest != self.conversation_input.immutable_prefix_digest:
            raise ValueError('native conversation immutable prefix digest mismatch')


LoopAction = Literal["continue", "complete", "fail"]
NativeExecutionRequest = NativeToolCall | NativeToolInvocation
if TYPE_CHECKING:
    from ownpi.runtime.native_tools.executor import NativeToolExecutionOutcome

NativeLoopToolValue = Union[NativeToolResult, 'NativeToolExecutionOutcome']
NativeLoopToolDelivery = tuple[NativeExecutionRequest, NativeLoopToolValue]


@dataclass(frozen=True)
class NativeLoopIdentity:
    session_id: str = ""
    task_id: str = ""
    run_id: str = ""
    actor_kind: str = ""
    actor_id: str = ""


@dataclass(frozen=True)
class NativeLoopPaths:
    project_root: Path
    run_dir: Path






@dataclass(frozen=True)
class NativeLoopServices:
    cancel_checker: Callable[[], bool] | None = None


@dataclass(frozen=True)
class NativeLoopInvocation:
    identity: NativeLoopIdentity
    paths: NativeLoopPaths
    services: NativeLoopServices = NativeLoopServices()


@dataclass(frozen=True)
class NativeLoopDirective:
    action: LoopAction
    reason: str = ""
    failure_class: str = ""
    failure_message: str = ""
    correction_text: str = ""
    correction_index: int | None = None

    @classmethod
    def continue_with(
        cls,
        *,
        reason: str = "",
        correction_text: str = "",
        correction_index: int | None = None,
    ) -> "NativeLoopDirective":
        return cls(
            action="continue",
            reason=reason,
            correction_text=correction_text,
            correction_index=correction_index,
        )

    @classmethod
    def complete(cls, *, reason: str = "") -> "NativeLoopDirective":
        return cls(action="complete", reason=reason)

    @classmethod
    def fail(cls, *, failure_class: str, message: str) -> "NativeLoopDirective":
        return cls(
            action="fail",
            reason=failure_class,
            failure_class=failure_class,
            failure_message=message,
        )


@dataclass(frozen=True)
class NativeLoopTurnPlan:
    calls: tuple[NativeExecutionRequest, ...] = ()
    directive: NativeLoopDirective | None = None


@dataclass(frozen=True)
class NativeLoopSnapshot:
    turn_index: int
    response: ProviderOutputEnvelope
    tool_call_count: int
    usage: dict[str, int]
    provider_request_ids: tuple[str, ...]


@dataclass(frozen=True)
class NativeLoopToolSurfaceSnapshot:
    provider_specs: tuple[NativeToolSpec, ...]
    executable_specs: tuple[NativeToolSpec, ...]


@dataclass(frozen=True)
class NativeLoopResult:
    status: Literal["completed", "failed"]
    turns: int
    tool_call_count: int
    usage: dict[str, int] = field(default_factory=dict)
    provider_request_ids: tuple[str, ...] = ()
    failure_class: str = ""
    failure_message: str = ""
    terminal_reason: str = ""


class NativeLoopPolicy(Protocol):
    def tool_choice(self, *, turn_index: int) -> str: ...

    def plan_turn(self, snapshot: NativeLoopSnapshot) -> NativeLoopTurnPlan: ...

    def after_results(
        self,
        snapshot: NativeLoopSnapshot,
        results: tuple[NativeLoopToolValue, ...],
    ) -> NativeLoopDirective: ...

    def on_provider_error(
        self,
        error: BaseException,
        *,
        turn_index: int,
        tool_call_count: int,
    ) -> NativeLoopDirective: ...


class NativeLoopConversation(Protocol):
    @property
    def provider_input(self) -> Any: ...

    def append_response(self, response: ProviderOutputEnvelope, *, turn_index: int) -> None: ...

    def flush_tool_context(self, *, calls: tuple[NativeExecutionRequest, ...]) -> None: ...

    def append_result(
        self,
        result: NativeLoopToolValue,
        *,
        call: NativeExecutionRequest,
        turn_index: int,
    ) -> HistoryEntry | None: ...

    def append_error(
        self, error: NativeToolError, *, turn_index: int,
        output_sequence_index: int | None = None,
        call: NativeExecutionRequest | None = None,
    ) -> None: ...

    def append_correction(
        self,
        text: str,
        *,
        turn_index: int,
        correction_index: int | None = None,
    ) -> None: ...

    def append_user_message(self, text: str, *, exchange_id: str = "") -> None: ...


class NativeLoopTurnPreparer(Protocol):
    def prepare(self, *, turn_index: int) -> Any: ...


class NativeLoopToolSurfaceProvider(Protocol):
    def refresh(self, *, turn_index: int) -> NativeLoopToolSurfaceSnapshot: ...


class NativeLoopExecutor(Protocol):
    def execute_many(
        self,
        calls: tuple[NativeExecutionRequest, ...],
    ) -> tuple[NativeLoopToolDelivery, ...]:
        """Reset this batch's deliveries, retaining collected occurrences even on exit."""
        ...

    def drain_retained_deliveries(self) -> tuple[NativeLoopToolDelivery, ...]:
        """Take collected deliveries once, in occurrence order, without changing raw records."""
        ...


class NativeLoopTurnRunner(Protocol):
    def invoke(self, *, prepared_turn: Any, tool_choice: str) -> ProviderOutputEnvelope: ...


class NativeLoopObservationSink(Protocol):
    def loop_started(self) -> None: ...

    def provider_started(self, *, turn_index: int) -> None: ...

    def provider_completed(
        self,
        *,
        turn_index: int,
        response: ProviderOutputEnvelope,
        provider_request_id: str,
    ) -> None: ...

    def tool_called(self, *, turn_index: int, call: NativeExecutionRequest) -> None: ...

    def tool_result(
        self,
        *,
        turn_index: int,
        call: NativeExecutionRequest,
        result: NativeLoopToolValue,
    ) -> None: ...

    def correction(self, *, turn_index: int, text: str, reason: str) -> None: ...

    def completed(self, result: NativeLoopResult) -> None: ...

    def failed(self, result: NativeLoopResult) -> None: ...

    @property
    def events(self) -> tuple[dict[str, Any], ...]: ...


class NativeLoopLifecycle(Protocol):
    def bind_invocation(self, invocation: NativeLoopInvocation) -> None: ...

    def before_turn(self, *, turn_index: int) -> None: ...

    def before_response(
        self,
        *,
        turn_index: int,
        response: ProviderOutputEnvelope,
    ) -> None: ...

    def after_provider(
        self,
        *,
        turn_index: int,
        response: ProviderOutputEnvelope,
    ) -> None: ...

    def on_provider_failure(
        self,
        *,
        turn_index: int,
        reason_code: str,
        failure_class: str,
    ) -> None: ...

    def after_plan(
        self,
        *,
        snapshot: NativeLoopSnapshot,
        plan: NativeLoopTurnPlan,
    ) -> None: ...

    def before_tools(
        self,
        *,
        turn_index: int,
        calls: tuple[NativeExecutionRequest, ...],
    ) -> None: ...

    def after_results(
        self,
        *,
        turn_index: int,
        calls: tuple[NativeExecutionRequest, ...],
        results: tuple[NativeLoopToolValue, ...],
    ) -> None: ...

    def on_terminal(self, result: NativeLoopResult) -> None: ...


class NativeLoopLifecycleBindings(Protocol):
    def before_turn(self) -> None: ...

    def after_provider(self) -> None: ...

    def before_tools(self) -> None: ...

    def after_results(self) -> None: ...


class NativeLoopRunScope(Protocol):
    def bind(self, invocation: NativeLoopInvocation) -> ContextManager[None]: ...


__all__ = [
    "NativeLoopIdentity",
    "NativeLoopInvocation",
    "NativeLoopPaths",
    "NativeLoopRunScope",
    "NativeLoopServices",
    "NativeLoopToolSurfaceProvider",
    "NativeLoopToolSurfaceSnapshot",
    "NativeLoopConversation",
    "NativeLoopDirective",
    "NativeLoopExecutor",
    "NativeLoopLifecycle",
    "NativeLoopLifecycleBindings",
    "NativeLoopObservationSink",
    "NativeLoopPolicy",
    "NativeLoopResult",
    "NativeLoopSnapshot",
    "NativeLoopTurnPreparer",
    "NativeLoopTurnPlan",
    "NativeLoopTurnRunner",
]
