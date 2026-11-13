from __future__ import annotations

from dataclasses import dataclass, asdict
from collections.abc import Callable
from dataclasses import replace
import hashlib
import json
from typing import Any, Iterable, Mapping

from ownpi.runtime.native_tools.provider_input import NativeHistoryProviderInput
from ownpi.runtime.native_tools.spec import NativeToolSpec
from ownpi.runtime.native_tools.history_items import HistoryEntry, HistoryMessage, ToolOutputItem, ToolSearchOutputItem
from ownpi.runtime.native_tools.provider import ProviderOutputItem
from ownpi.runtime.native_tools.token_estimation import estimate_item_tokens, estimate_items_tokens


@dataclass(frozen=True)
class NativeProviderRequestMeasurement:
    """Accounting for the canonical request components consumed by provider adapters."""

    prefix_bytes: int
    conversation_bytes: int
    tool_schema_bytes: int
    tool_search_output_bytes: int
    envelope_bytes: int
    total_bytes: int
    estimated_tokens: int
    largest_item_bytes: int
    item_count: int
    prefix_tokens: int
    conversation_tokens: int
    tool_schema_tokens: int
    tool_search_output_tokens: int
    envelope_tokens: int
    estimate_history: Callable[[tuple[HistoryEntry, ...]], int] | None = None
    estimate_normalization: Callable[[tuple[HistoryEntry, ...]], int] | None = None
    request_static_properties_digest: str = ''
    provider_id: str = ''
    model_id: str = ''


def measure_native_provider_request(
    *,
    conversation_input: NativeHistoryProviderInput,
    tool_specs: Iterable[NativeToolSpec] = (),
    provider_id: str = '',
    model_id: str = '',
    request_static_properties: Mapping[str, Any] | None = None,
) -> NativeProviderRequestMeasurement:
    prefix_bytes = len(_json_bytes(conversation_input.immutable_prefix_messages))
    item_payloads = [_provider_item_payload(item) for item in conversation_input.items]
    item_bytes = [len(_json_bytes(item)) for item in item_payloads]
    conversation_bytes = len(_json_bytes(item_payloads))
    tool_payloads = [spec.provider_neutral_schema() for spec in tool_specs]
    tool_schema_bytes = len(_json_bytes(tool_payloads))
    tool_search_output_bytes = 0
    request_static_properties_digest = _sha256(
        _json_bytes(dict(request_static_properties or {}))
    )
    envelope = {
        'immutable_prefix_digest': conversation_input.immutable_prefix_digest,
        'tool_choice': 'auto',
    }
    envelope_bytes = len(_json_bytes(envelope))
    total_bytes = (
        prefix_bytes
        + conversation_bytes
        + tool_schema_bytes
        + tool_search_output_bytes
        + envelope_bytes
    )
    prefix_tokens = estimate_items_tokens(conversation_input.immutable_prefix_messages)
    conversation_tokens = sum(estimate_item_tokens(_model_item_payload(entry)) for entry in conversation_input.items)
    tool_schema_tokens = estimate_items_tokens(tool_payloads)
    return NativeProviderRequestMeasurement(
        prefix_bytes=prefix_bytes,
        conversation_bytes=conversation_bytes,
        tool_schema_bytes=tool_schema_bytes,
        tool_search_output_bytes=tool_search_output_bytes,
        envelope_bytes=envelope_bytes,
        total_bytes=total_bytes,
        estimated_tokens=prefix_tokens + conversation_tokens + tool_schema_tokens,
        largest_item_bytes=max(item_bytes, default=0),
        item_count=len(item_payloads),
        prefix_tokens=prefix_tokens,
        conversation_tokens=conversation_tokens,
        tool_schema_tokens=tool_schema_tokens,
        tool_search_output_tokens=0,
        envelope_tokens=0,
        request_static_properties_digest=request_static_properties_digest,
        provider_id=provider_id,
        model_id=model_id,
    )


def with_serialized_request_bytes(
    measurement: NativeProviderRequestMeasurement,
    *,
    serialized_request: Any,
) -> NativeProviderRequestMeasurement:
    """Replace the neutral total with the exact adapter request serialization size."""

    total_bytes = len(_json_bytes(serialized_request))
    known_bytes = (
        measurement.prefix_bytes
        + measurement.conversation_bytes
        + measurement.tool_schema_bytes
        + measurement.tool_search_output_bytes
    )
    return replace(
        measurement,
        envelope_bytes=max(0, total_bytes - known_bytes),
        total_bytes=total_bytes,
    )


def with_model_visible_request_tokens(
    measurement: NativeProviderRequestMeasurement, *,
    conversation_input: NativeHistoryProviderInput,
    serialized_request: Mapping[str, Any],
    project: Callable[[NativeHistoryProviderInput], list[dict[str, Any]]],
    modalities: tuple[str, ...],
) -> NativeProviderRequestMeasurement:
    """Measure the actual input and retain a protocol projection for local deltas.

    The full wire projection is built by the adapter once. Prefix-only and
    delta projections use the same serializers, never a second request builder.
    Adjacent-message wrapper differences belong to conversation exactly once.
    """
    from ownpi.runtime.native_tools.history_prompt import _server_output, call_key, filter_modalities, for_prompt, output_key

    value = conversation_input.for_modalities(modalities)
    prefix_value = replace(value, items=())
    prefix_tokens = estimate_items_tokens(project(prefix_value))
    system = serialized_request.get('system')
    if system is not None:
        prefix_tokens += estimate_item_tokens(system)
    inputs = serialized_request.get('input', serialized_request.get('messages', []))
    input_tokens = estimate_items_tokens(inputs)
    if system is not None:
        input_tokens += estimate_item_tokens(system)
    tools = estimate_items_tokens(serialized_request.get('tools', []))
    calls = {call_key(entry) for entry in value.items}

    def estimate_history(entries: tuple[HistoryEntry, ...]) -> int:
        # Do not normalize a delta alone: its tool calls may be in the baseline.
        return estimate_items_tokens(project(NativeHistoryProviderInput(items=tuple(
            filter_modalities(entry, frozenset(modalities)) for entry in entries
            if output_key(entry) is None or output_key(entry) in calls or _server_output(entry)
        ))))

    def estimate_normalization(entries: tuple[HistoryEntry, ...]) -> int:
        # The request owner already normalized its input. Detect synthesized
        # outputs from the raw identity snapshot without serializing it again.
        output_keys = {output_key(entry) for entry in entries}
        synthetic = tuple(entry for entry in for_prompt(entries, modalities)
                          if isinstance(entry.item, (ToolOutputItem, ToolSearchOutputItem))
                          and output_key(entry) not in output_keys)
        return estimate_items_tokens(project(NativeHistoryProviderInput(items=synthetic)))
    return replace(
        measurement, prefix_tokens=prefix_tokens,
        conversation_tokens=input_tokens - prefix_tokens,
        tool_schema_tokens=tools, tool_search_output_tokens=0, envelope_tokens=0,
        estimated_tokens=input_tokens + tools, estimate_history=estimate_history,
        estimate_normalization=estimate_normalization,
    )


def _model_item_payload(entry: HistoryEntry) -> dict[str, Any]:
    payload = _provider_item_payload(entry)
    if isinstance(entry.item, ToolOutputItem) and not isinstance(entry.item.body, str):
        from ownpi.runtime.native_tools.provider_content_projection import openai_responses_content
        payload['output'] = openai_responses_content(entry.item.body)
    return payload


def _provider_item_payload(entry: HistoryEntry) -> dict[str, Any]:
    item = entry.item
    if isinstance(item, ProviderOutputItem):
        return item.provider_payload.to_object()
    if isinstance(item, HistoryMessage):
        return {'role': item.role, 'content': item.text}
    if isinstance(item, ToolSearchOutputItem):
        return {'type': item.kind, 'call_id': item.call_id, 'tools': [tool.to_value() for tool in item.tools], 'body': item.body}
    if isinstance(item, ToolOutputItem):
        return {'type': item.kind, 'call_id': item.call_id, 'output': item.body if isinstance(item.body, str) else [asdict(block) for block in item.body]}
    raise TypeError(f'Unsupported history item: {type(item).__name__}')


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
        default=str,
    ).encode('utf-8')


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
