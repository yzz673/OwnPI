from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ownpi.runtime.native_tools.spec import NativeToolCall, NativeToolSpec
from ownpi.runtime.native_tools.tool_invocation import NativeToolInvocation
from ownpi.schemas.runtime_tooling import ToolCallBatchPolicy


@dataclass(frozen=True)
class NativeToolBatchGrammar:
    terminal_marker: str = ''
    allowed_before_terminal: frozenset[str] = frozenset()


@dataclass(frozen=True)
class NativeToolBatchAdmission:
    accepted: bool
    violation_code: str = ''
    correction_text: str = ''
    tool_names: tuple[str, ...] = ()


def admit_native_tool_batch(
    *,
    calls: Sequence[NativeToolCall | NativeToolInvocation],
    tool_specs: Sequence[NativeToolSpec],
    grammar: NativeToolBatchGrammar | None = None,
) -> NativeToolBatchAdmission:
    tool_names = tuple(call.tool_name for call in calls)
    specs = {spec.name: spec for spec in tool_specs}
    exclusive = tuple(
        call.tool_name
        for call in calls
        if (spec := specs.get(call.tool_name)) is not None
        and spec.batch_policy is ToolCallBatchPolicy.TURN_EXCLUSIVE
    )
    if exclusive and len(calls) != 1:
        names = ', '.join(dict.fromkeys(exclusive))
        return NativeToolBatchAdmission(
            accepted=False,
            violation_code='turn_exclusive_tool_mixed_batch',
            correction_text=(
                f'Tool-call batch rejected before execution. No tool call in the batch was '
                f'executed. {names} must be the only tool call in its provider turn. Retry '
                f'the required work in separate provider turns.'
            ),
            tool_names=tool_names,
        )
    if grammar is not None and grammar.terminal_marker:
        marker = grammar.terminal_marker
        marker_indices = tuple(
            index for index, call in enumerate(calls) if call.tool_name == marker
        )
        invalid_marker = (
            len(marker_indices) > 1
            or bool(marker_indices and marker_indices[0] != len(calls) - 1)
            or bool(
                marker_indices
                and any(
                    call.tool_name not in grammar.allowed_before_terminal
                    for call in calls[: marker_indices[0]]
                )
            )
        )
        if invalid_marker:
            allowed = ', '.join(sorted(grammar.allowed_before_terminal)) or '<none>'
            return NativeToolBatchAdmission(
                accepted=False,
                violation_code='invalid_ordered_terminal_batch',
                correction_text=(
                    'Tool-call batch rejected before execution. No tool call in the batch was '
                    f'executed. {marker} may appear at most once and must be the final call; '
                    f'only these calls may precede it: {allowed}. Retry with a valid order.'
                ),
                tool_names=tool_names,
            )
    return NativeToolBatchAdmission(accepted=True, tool_names=tool_names)


__all__ = [
    'NativeToolBatchAdmission',
    'NativeToolBatchGrammar',
    'admit_native_tool_batch',
]
