from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from ownpi.runtime.native_tools.history_items import HistoryEntry, HistoryMessage
from ownpi.runtime.native_tools.provider_json import FrozenProviderJson
from ownpi.runtime.native_tools.provider import ProviderOutputEnvelope
from ownpi.runtime.native_tools.provider_input import (
    NativeHistoryProviderInput,
)
from ownpi.runtime.native_tools.spec import NativeToolCall, NativeToolError, NativeToolSpec
from ownpi.runtime.native_tools.tool_invocation import build_tool_invocations, parse_tool_invocation


@dataclass(frozen=True)
class AtomicToolCallResult:
    tool_call: NativeToolCall
    usage: dict[str, int]
    provider_metadata: dict[str, Any]
    assistant_text_present: bool


class AtomicToolCallProtocolError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def build_atomic_provider_input(
    *,
    immutable_prefix_messages: tuple[dict[str, Any], ...],
    conversation_messages: tuple[dict[str, Any], ...] = (),
) -> NativeHistoryProviderInput:
    return NativeHistoryProviderInput(
        immutable_prefix=tuple(FrozenProviderJson.from_value(item) for item in immutable_prefix_messages),
        items=tuple(_atomic_conversation_item(message) for message in conversation_messages),
    )


def _atomic_conversation_item(
    message: dict[str, Any],
) -> HistoryEntry:
    role = str(message.get("role") or "").strip()
    text = str(message.get("content") or "").strip()
    if role != "user" or not text:
        raise ValueError("atomic conversation items must be non-empty user messages")
    return HistoryEntry(HistoryMessage(role, text))


def validate_atomic_tool_response(
    response: ProviderOutputEnvelope,
    *,
    expected_tool: NativeToolSpec,
    native_tool_strict: bool = False,
) -> AtomicToolCallResult:
    if response.assistant_text.strip():
        raise AtomicToolCallProtocolError(
            'unexpected_assistant_text',
            'Atomic required-tool response included assistant text alongside the control call.',
        )
    calls = build_tool_invocations(response)
    if not calls:
        raise AtomicToolCallProtocolError(
            'zero_tool_calls',
            f'Provider did not call required tool {expected_tool.name!r}.',
        )
    if len(calls) != 1:
        raise AtomicToolCallProtocolError(
            'multiple_tool_calls',
            f'Provider returned {len(calls)} tool calls; exactly one is required.',
        )
    call = calls[0]
    if call.tool_name != expected_tool.name:
        raise AtomicToolCallProtocolError(
            'unknown_tool',
            f'Provider called {call.tool_name!r}; expected {expected_tool.name!r}.',
        )
    call = parse_tool_invocation(
        call, input_schema=expected_tool.input_schema, native_tool_strict=native_tool_strict,
    )
    if isinstance(call, NativeToolError):
        raise AtomicToolCallProtocolError('malformed_arguments', call.error_message)
    try:
        Draft202012Validator(expected_tool.input_schema).validate(call.input)
    except ValidationError as exc:
        raise AtomicToolCallProtocolError(
            'malformed_arguments',
            f'Required tool arguments failed schema validation: {exc.message}',
        ) from exc
    return AtomicToolCallResult(
        tool_call=call,
        usage=dict(response.usage),
        provider_metadata=dict(response.provider_metadata),
        assistant_text_present=bool(response.assistant_text.strip()),
    )
