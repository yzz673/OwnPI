from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from ownpi.runtime.native_tools.loop_engine.contracts import (
    NativeLoopInvocation,
    NativeLoopResult,
)
from ownpi.runtime.native_tools.spec import NativeToolSpec


PlanSegmentStatus = Literal[
    "user_cancelled",
    "plan_completed",
    "human_input_pending",
    "completed_without_plan",
    "failed",
]
PlanRuntimeStatus = Literal[
    "user_cancelled",
    "running",
    "human_input_requested",
    "approval_requested",
    "approved",
    "continue_planning",
    "failed",
]
PlanDecision = Literal["approved", "revise", "continue_planning"]


@dataclass(frozen=True)
class PlanModeLoopRequest:
    canonical_ledger_messages: tuple[dict[str, Any], ...]
    tool_specs: tuple[NativeToolSpec, ...]
    invocation: NativeLoopInvocation | None = None
    request_kind: str = "native_plan_mode"
    plan_start_tag: str = "<proposed_plan>"
    plan_end_tag: str = "</proposed_plan>"
    human_boundary_tool_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.canonical_ledger_messages:
            raise ValueError("Plan Mode requires at least one provider message")
        if not self.plan_start_tag or not self.plan_end_tag:
            raise ValueError("Plan Mode plan tags must not be empty")
        object.__setattr__(self, "tool_specs", tuple(self.tool_specs))
        object.__setattr__(
            self,
            "canonical_ledger_messages",
            tuple(dict(message) for message in self.canonical_ledger_messages),
        )
        object.__setattr__(self, "human_boundary_tool_names", tuple(self.human_boundary_tool_names))


@dataclass(frozen=True)
class PlanModeSegmentResult:
    status: PlanSegmentStatus
    plan_text: str = ""
    visible_text: str = ""
    pending_boundary: dict[str, Any] | None = None
    native_result: NativeLoopResult | None = None
    failure_class: str = ""
    failure_message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlanModeRuntimeResult:
    status: PlanRuntimeStatus
    plan_text: str = ""
    visible_text: str = ""
    pending_boundary: dict[str, Any] | None = None
    continuation_ref: str = ""
    failure_class: str = ""
    failure_message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "PlanDecision",
    "PlanModeLoopRequest",
    "PlanModeRuntimeResult",
    "PlanModeSegmentResult",
    "PlanRuntimeStatus",
    "PlanSegmentStatus",
]
