from __future__ import annotations

from dataclasses import dataclass

from ownpi.runtime.stage_execution.feedback_control_tools import (
    COMPLETE_FEEDBACK_TOOL_NAME,
    NEXT_STAGE_TOOL_NAME,
    REQUEST_REVIEW_TOOL_NAME,
)
from ownpi.runtime.stage_execution.feedback_tools import SUBMIT_FEEDBACK_TOOL_NAME


@dataclass(frozen=True)
class NativeTerminalTool:
    name: str
    safe_point: str
    result_status: str


_TERMINAL_TOOLS = (
    NativeTerminalTool(
        name=SUBMIT_FEEDBACK_TOOL_NAME,
        safe_point='feedback_result',
        result_status='feedback_submitted',
    ),
    NativeTerminalTool(
        name=REQUEST_REVIEW_TOOL_NAME,
        safe_point='core_feedback_intent_result',
        result_status='core_feedback_intent_submitted',
    ),
    NativeTerminalTool(
        name=COMPLETE_FEEDBACK_TOOL_NAME,
        safe_point='core_feedback_intent_result',
        result_status='core_feedback_intent_submitted',
    ),
    NativeTerminalTool(
        name=NEXT_STAGE_TOOL_NAME,
        safe_point='next_stage_result',
        result_status='next_stage_submitted',
    ),
)


def native_terminal_tools() -> tuple[NativeTerminalTool, ...]:
    return _TERMINAL_TOOLS


def terminal_tools_for_names(names: tuple[str, ...] | None) -> tuple[NativeTerminalTool, ...]:
    if names is None:
        return _TERMINAL_TOOLS
    requested = frozenset(names)
    selected = tuple(item for item in _TERMINAL_TOOLS if item.name in requested)
    missing = requested - {item.name for item in selected}
    if missing:
        raise ValueError(f'unknown terminal tools: {", ".join(sorted(missing))}')
    return selected
