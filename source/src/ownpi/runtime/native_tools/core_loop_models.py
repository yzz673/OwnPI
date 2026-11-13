from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ownpi.runtime.native_tools.executor import NativeToolExecutionOutcome
from ownpi.runtime.native_tools.provider import (
    NativeToolProviderCapabilities,
    ProviderOutputEnvelope,
)
from ownpi.runtime.native_tools.provider_input import NativeHistoryProviderInput
from ownpi.runtime.native_tools.provider_request_measurement import (
    NativeProviderRequestMeasurement,
)
from ownpi.runtime.native_tools.spec import NativeToolSpec


@dataclass(frozen=True)
class NativeToolLoopRequest:
    stage: str
    attempt: int
    native_tool_loop_index: int
    tool_specs: tuple[NativeToolSpec, ...]
    conversation_input: NativeHistoryProviderInput
    measurement: NativeProviderRequestMeasurement
    context_preparation_id: str = ""
    immutable_prefix_digest: str = ""
    provider_capabilities: NativeToolProviderCapabilities = NativeToolProviderCapabilities()

    def __post_init__(self) -> None:
        if self.immutable_prefix_digest != self.conversation_input.immutable_prefix_digest:
            raise ValueError("native conversation immutable prefix digest mismatch")


@dataclass(frozen=True)
class NativeToolProviderTurn:
    response: ProviderOutputEnvelope

    @property
    def assistant_text(self) -> str:
        return self.response.assistant_text

    @property
    def exchange_id(self) -> str:
        return self.response.exchange_id


@dataclass(frozen=True)
class NativeToolLoopResult:
    status: str
    turns: int
    final_assistant_text: str = ""
    tool_outcomes: tuple[NativeToolExecutionOutcome, ...] = ()
    stop_reason: str = ""
    failure_class: str = ""
    failure_diagnostics: dict[str, Any] = field(default_factory=dict)
    auto_resume_safe: bool = False
    recommended_recovery: str = ""


@dataclass(frozen=True)
class NativeToolLoopDomainPolicy:
    terminal_tool_names: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if self.terminal_tool_names is not None:
            normalized = tuple(name.strip() for name in self.terminal_tool_names)
            if any(not name for name in normalized) or len(set(normalized)) != len(normalized):
                raise ValueError('terminal tool names must be unique non-empty strings')
            object.__setattr__(self, 'terminal_tool_names', normalized)


NativeToolProviderTurnCallable = Callable[[NativeToolLoopRequest], NativeToolProviderTurn]
NativeToolRequestMeasureCallable = Callable[
    [
        NativeHistoryProviderInput,
        tuple[NativeToolSpec, ...],
    ],
    NativeProviderRequestMeasurement,
]


def project_native_tool_provider_turn(
    turn: NativeToolProviderTurn,
) -> ProviderOutputEnvelope:
    return turn.response


__all__ = [
    "NativeToolLoopRequest",
    "NativeToolLoopResult",
    "NativeToolLoopDomainPolicy",
    "NativeToolProviderTurn",
    "NativeToolProviderTurnCallable",
    "NativeToolRequestMeasureCallable",
    "project_native_tool_provider_turn",
]
