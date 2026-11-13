from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ownpi.runtime.native_tools.provider import (
    FrozenProviderJson,
    ProviderOutputEnvelope,
    ProviderOutputItemKind,
)
from ownpi.runtime.native_tools.spec import NativeToolCall, NativeToolError
from ownpi.runtime.tools.input_validation import parse_function_arguments
from ownpi.runtime.native_tools.strict_arguments import canonical_function_arguments


@dataclass(frozen=True)
class NativeToolInvocation:
    """An execution request whose arguments have not been interpreted yet."""

    tool_name: str
    raw_arguments: str
    provider_call_id: str
    request_id: str
    sequence_index: int
    raw_provider_payload: FrozenProviderJson

    def to_dict(self) -> dict[str, Any]:
        return {
            'tool_name': self.tool_name,
            'raw_arguments': self.raw_arguments,
            'provider_call_id': self.provider_call_id,
            'request_id': self.request_id,
            'sequence_index': self.sequence_index,
            'raw_provider_payload': self.raw_provider_payload.to_value(),
        }


def build_tool_invocations(
    response: ProviderOutputEnvelope,
) -> tuple[NativeToolInvocation, ...]:
    """Select client calls without parsing or changing provider output facts."""
    return tuple(
        NativeToolInvocation(
            tool_name=item.tool_name,
            raw_arguments=item.raw_arguments,
            provider_call_id=item.call_id,
            request_id=item.request_id,
            sequence_index=item.sequence_index,
            raw_provider_payload=item.provider_payload,
        )
        for item in response.completed_items
        if item.kind is ProviderOutputItemKind.TOOL_CALL
    )


def parse_tool_invocation(
    invocation: NativeToolInvocation,
    *,
    parse_arguments: Callable[[str], dict[str, Any]] = parse_function_arguments,
    input_schema: dict[str, Any] | None = None,
    native_tool_strict: bool = False,
) -> NativeToolCall | NativeToolError:
    """Decode a function's input at dispatch, returning local model errors."""
    try:
        arguments = parse_arguments(invocation.raw_arguments)
        if input_schema is not None:
            arguments = canonical_function_arguments(
                arguments, input_schema,
                strict=(native_tool_strict and invocation.raw_provider_payload.to_object().get('type') != 'tool_search_call'),
            )
    except (ValueError, RecursionError) as error:
        return NativeToolError(
            tool_name=invocation.tool_name,
            request_id=invocation.request_id,
            provider_call_id=invocation.provider_call_id,
            failure_class='invalid_tool_arguments',
            error_message=f'failed to parse function arguments: {error}',
        )
    return NativeToolCall(
        tool_name=invocation.tool_name,
        input=arguments,
        provider_call_id=invocation.provider_call_id,
        request_id=invocation.request_id,
        raw_provider_payload=invocation.raw_provider_payload.to_object(),
    )
