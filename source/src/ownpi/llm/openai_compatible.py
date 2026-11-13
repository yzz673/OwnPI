from __future__ import annotations

import json
import hashlib
import os
import random
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import copy_context
from dataclasses import replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

from ownpi.llm.base import LLMClient, current_cancel_checker
from ownpi.llm.cancellation import run_provider_io
from ownpi.llm.failure_diagnostics import diagnostic_message, sanitize_error_metadata
from ownpi.llm.openai_chat_stream import OpenAIChatStreamNormalizer
from ownpi.llm.openai_native_stream import OpenAIResponsesStreamNormalizer
from ownpi.llm.registry import register_llm_provider
from ownpi.llm.provider_request_context import (
    ProviderTurnSession,
    current_provider_request_context,
    provider_turn_session_for_request,
)
from ownpi.llm.provider_error_classification import classify_provider_http_error
from ownpi.llm.provider_retry import (
    ProviderRetryBudgetState,
    ProviderRetryKernel,
    ProviderRetryOutcome,
    ProviderRetryPolicy,
    RetryExhausted,
    RetryCancelled,
    RetryKind,
    RetryOwner,
    RetryableProviderOperation,
    record_provider_retry,
)
from ownpi.llm.responses_http_transport import (
    ResponsesHttpRequest,
    ResponsesHttpStatusError,
    ResponsesHttpTransport,
)
from ownpi.llm.sse_failure import (
    ProviderSSEFailure,
    SSEEventTracker,
    malformed_sse_failure,
    iter_native_sse_events,
    decode_native_json,
    native_responses_event_decodable,
    native_response_completion_decodable,
    native_responses_error,
    native_blocking_response_error,
    provider_http_failure,
    provider_transport_failure,
    protocol_sse_failure,
    retryable_malformed_response,
)
from ownpi.llm.streaming import (
    CanonicalResponseEvent,
    CanonicalResponseEventType,
    LLMStreamEvent,
)
from ownpi.llm.telemetry import emit_llm_trace, summarize_prompt
from ownpi.llm.transport import (
    build_transport_error_message,
    build_transport_trace_attempt,
    classify_transport_error,
    unwrap_transport_error,
)
from ownpi.runtime.model_content import ModelContentItem, content_items_from_dicts, redact_image_url_value
from ownpi.runtime.native_tools.provider_content_projection import provider_message_blocks
from ownpi.runtime.native_tools.adapters import OpenAINativeToolAdapter
from ownpi.runtime.native_tools.provider import (
    FrozenProviderJson,
    NativeToolProviderCapabilities,
    ProviderOutputCompletion,
    ProviderOutputEnvelope,
    ProviderOutputItem,
    ProviderOutputItemKind,
    ProviderOutputSource,
    ProviderOutputSourceKind,
)
from ownpi.runtime.native_tools.provider_input import NativeHistoryProviderInput
from ownpi.runtime.native_tools.provider_input_projection import (
    openai_chat_history_messages,
    openai_responses_history_input,
)
from ownpi.runtime.native_tools.streaming import (
    aggregate_native_response_events,
    blocking_native_response_events,
)
from ownpi.runtime.native_tools.hosted_web_search import (
    HostedWebSearchOptions,
    WebSearchExecutionMode,
    count_url_citations,
    hosted_web_search_trace_metadata,
    select_web_search_execution_mode,
)
from ownpi.runtime.native_tools.spec import NativeToolSpec
from ownpi.schemas.runtime import LLMConfig


_HTTP_ERROR_TYPES = (urllib.error.HTTPError, ResponsesHttpStatusError)
_SSE_TERMINAL_EVENTS = {
    'response.completed',
    'response.failed',
    'response.incomplete',
    'error',
}


def _normalize_schema_name(value: str) -> str:
    cleaned = re.sub(r'[^A-Za-z0-9_-]+', '_', str(value or '').strip())
    cleaned = cleaned.strip('_') or 'structured_response'
    return cleaned[:64]


def _expects_json_response(response_mode: str) -> bool:
    return response_mode in {'json', 'structured_json'}


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _sensitive_hash(value: str) -> str:
    normalized = str(value or "").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""


def _schema_allows_null(schema: dict[str, Any]) -> bool:
    schema_type = schema.get('type')
    if schema_type == 'null':
        return True
    if isinstance(schema_type, list) and 'null' in schema_type:
        return True
    for union_key in ('anyOf', 'oneOf'):
        union_items = schema.get(union_key)
        if isinstance(union_items, list):
            for item in union_items:
                if isinstance(item, dict) and _schema_allows_null(item):
                    return True
    return False


def _nullable_schema(schema: dict[str, Any]) -> dict[str, Any]:
    if _schema_allows_null(schema):
        return schema
    return {'anyOf': [schema, {'type': 'null'}]}


def _to_openai_strict_schema(schema: Any) -> Any:
    """Convert structured-answer schemas; native tools use strict_arguments."""

    if isinstance(schema, list):
        return [_to_openai_strict_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema

    converted: dict[str, Any] = {}
    for key, value in schema.items():
        if key in {'properties', 'items', 'anyOf', 'oneOf', 'allOf', 'not'}:
            continue
        converted[key] = _to_openai_strict_schema(value)

    properties = schema.get('properties')
    original_required = schema.get('required')
    required_keys = {
        str(item)
        for item in original_required
        if isinstance(item, str)
    } if isinstance(original_required, list) else set()
    if isinstance(properties, dict):
        converted_properties: dict[str, Any] = {}
        for prop_name, prop_schema in properties.items():
            if not isinstance(prop_name, str):
                continue
            strict_prop_schema = _to_openai_strict_schema(prop_schema)
            if isinstance(strict_prop_schema, dict) and prop_name not in required_keys:
                strict_prop_schema = _nullable_schema(strict_prop_schema)
            converted_properties[prop_name] = strict_prop_schema
        converted['properties'] = converted_properties
        converted['required'] = list(converted_properties.keys())
        converted.setdefault('additionalProperties', False)
    elif converted.get('type') == 'object':
        converted.setdefault('properties', {})
        converted.setdefault('required', [])
        converted['additionalProperties'] = False

    items = schema.get('items')
    if 'items' in schema:
        converted['items'] = _to_openai_strict_schema(items)
    for union_key in ('anyOf', 'oneOf', 'allOf'):
        union_items = schema.get(union_key)
        if isinstance(union_items, list):
            converted[union_key] = [
                _to_openai_strict_schema(item)
                for item in union_items
            ]
    if 'not' in schema:
        converted['not'] = _to_openai_strict_schema(schema.get('not'))
    return converted


def _to_openai_response_format_schema(schema: dict[str, Any]) -> dict[str, Any]:
    flattened = _flatten_root_anyof_schema(schema)
    converted = _to_openai_strict_schema(flattened)
    if not isinstance(converted, dict):
        return {'type': 'object', 'properties': {}, 'additionalProperties': False, 'required': []}
    return converted


def _flatten_root_anyof_schema(schema: dict[str, Any]) -> dict[str, Any]:
    any_of = schema.get('anyOf')
    if not isinstance(any_of, list):
        return schema
    merged_properties: dict[str, Any] = {}
    for branch in any_of:
        if not isinstance(branch, dict):
            continue
        branch_properties = branch.get('properties')
        if not isinstance(branch_properties, dict):
            continue
        for prop_name, prop_schema in branch_properties.items():
            if not isinstance(prop_name, str):
                continue
            merged_properties[prop_name] = _merge_schema_alternatives(
                merged_properties.get(prop_name),
                prop_schema,
            )
    return {
        'type': 'object',
        'properties': merged_properties,
        'required': [],
        'additionalProperties': False,
    }


def _merge_schema_alternatives(existing: Any, incoming: Any) -> Any:
    if existing is None:
        return incoming
    if existing == incoming:
        return existing
    values = _string_const_or_enum_values(existing) + _string_const_or_enum_values(incoming)
    if values:
        deduped = list(dict.fromkeys(values))
        return {'type': 'string', 'enum': deduped}
    alternatives: list[Any] = []
    for candidate in (existing, incoming):
        if isinstance(candidate, dict) and isinstance(candidate.get('anyOf'), list):
            alternatives.extend(candidate['anyOf'])
        else:
            alternatives.append(candidate)
    deduped_alternatives: list[Any] = []
    for candidate in alternatives:
        if candidate not in deduped_alternatives:
            deduped_alternatives.append(candidate)
    return {'anyOf': deduped_alternatives}


def _string_const_or_enum_values(schema: Any) -> list[str]:
    if not isinstance(schema, dict):
        return []
    const_value = schema.get('const')
    if isinstance(const_value, str):
        return [const_value]
    enum_values = schema.get('enum')
    if isinstance(enum_values, list) and all(isinstance(item, str) for item in enum_values):
        return list(enum_values)
    return []


def _drop_native_null_optionals(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _drop_native_null_optionals(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [_drop_native_null_optionals(item) for item in value]
    return value


def _drop_structured_null_optionals(value: Any, schema: Any) -> Any:
    if not isinstance(schema, dict):
        return _drop_native_null_optionals(value)
    schema = _schema_branch_for_value(schema, value)
    if isinstance(value, dict):
        properties = schema.get('properties', {})
        if not isinstance(properties, dict):
            properties = {}
        required = schema.get('required', [])
        required_keys = {
            str(item)
            for item in required
            if isinstance(item, str)
        } if isinstance(required, list) else set()
        return {
            key: _drop_structured_null_optionals(
                item,
                properties.get(key, {}),
            )
            for key, item in value.items()
            if item is not None or key in required_keys or key not in properties
        }
    if isinstance(value, list):
        item_schema = schema.get('items', {})
        return [
            _drop_structured_null_optionals(item, item_schema)
            for item in value
        ]
    return value


def _schema_branch_for_value(schema: dict[str, Any], value: Any) -> dict[str, Any]:
    expected_type = _json_schema_type_for_value(value)
    schema_type = schema.get('type')
    if schema_type == expected_type or (
        expected_type == 'object' and isinstance(schema.get('properties'), dict)
    ):
        return schema
    for union_key in ('anyOf', 'oneOf'):
        branches = schema.get(union_key)
        if not isinstance(branches, list):
            continue
        for branch in branches:
            if isinstance(branch, dict) and branch.get('type') == expected_type:
                return branch
    return schema


def _json_schema_type_for_value(value: Any) -> str:
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'boolean'
    if isinstance(value, dict):
        return 'object'
    if isinstance(value, list):
        return 'array'
    if isinstance(value, str):
        return 'string'
    if isinstance(value, int):
        return 'integer'
    if isinstance(value, float):
        return 'number'
    return ''


def _loads_structured_json_text(text: str) -> Any:
    return json.loads(_strip_json_markdown_fence(text))


def _strip_json_markdown_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith('```'):
        return stripped
    lines = stripped.splitlines()
    if len(lines) < 2:
        return stripped
    opener = lines[0].strip()
    language = opener[3:].strip().lower()
    if language not in {'', 'json'}:
        return stripped
    if lines[-1].strip() != '```':
        return stripped
    return '\n'.join(lines[1:-1]).strip()


def _extract_openai_responses_message_text(output: Any) -> str | None:
    if not isinstance(output, list):
        return None
    for item in output:
        if not isinstance(item, dict):
            continue
        if str(item.get('type', '') or '').strip() != 'message':
            continue
        content = item.get('content')
        if isinstance(content, str):
            if content.strip():
                return content
        if isinstance(content, list):
            text_chunks: list[str] = []
            for content_item in content:
                if not isinstance(content_item, dict):
                    continue
                if 'text' in content_item and content_item['text'] is not None:
                    text_chunks.append(str(content_item['text']))
            text = ''.join(text_chunks)
            if text.strip():
                return text
    return None


def _is_responses_keepalive(event_name: str, payload: dict[str, Any]) -> bool:
    # Some gateways send an empty placeholder delta before response.created.
    return (
        event_name == 'response.output_text.delta'
        and payload.get('item_id') == 'SSE-Keep-Alive'
        and payload.get('delta') == ''
    )


def _parse_sse_events(raw_body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in raw_body.split("\n\n"):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue

        event_name: str | None = None
        data_chunks: list[str] = []
        for line in lines:
            if line.startswith("event:"):
                event_name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_chunks.append(line[len("data:") :].strip())

        if not data_chunks:
            continue

        data_str = "\n".join(data_chunks)
        if data_str == "[DONE]":
            continue

        try:
            payload = json.loads(data_str)
        except json.JSONDecodeError as exc:
            raise malformed_sse_failure(data=data_str, event_count=len(events)) from exc
        if isinstance(payload, dict):
            resolved_event_name = event_name or _sse_event_name_from_payload(payload)
            if resolved_event_name and not _is_responses_keepalive(resolved_event_name, payload):
                events.append((resolved_event_name, payload))
    return events


def _sse_event_name_from_payload(payload: dict[str, Any]) -> str | None:
    event_name = payload.get('type') or payload.get('event')
    if isinstance(event_name, str) and event_name.strip():
        return event_name.strip()
    choices = payload.get('choices')
    if isinstance(choices, list) and choices:
        return 'chat.completion.chunk'
    if choices == [] and isinstance(payload.get('usage'), dict):
        return 'chat.completion.chunk'
    return None


def _extract_chat_completion_stream_delta(payload: dict[str, Any]) -> str:
    choices = payload.get('choices')
    if not isinstance(choices, list):
        return ''
    chunks: list[str] = []
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        delta = choice.get('delta')
        if not isinstance(delta, dict):
            continue
        if isinstance(delta.get('tool_calls'), list) and delta.get('tool_calls'):
            raise protocol_sse_failure(
                'native_tool_call_in_structured_mode',
                error_message=(
                    'Provider returned native tool-call payload under structured JSON mode; '
                    'canonical tool protocol requires schema-valid JSON only.'
                ),
            )
        content = delta.get('content')
        if isinstance(content, str) and content:
            chunks.append(content)
    return ''.join(chunks)


def _contains_provider_tool_call_payload(parsed: dict[str, Any], *, api_style: str) -> bool:
    if api_style == 'openai_chat':
        try:
            message = parsed.get('choices', [{}])[0].get('message', {})
        except (IndexError, AttributeError, TypeError):
            return False
        tool_calls = message.get('tool_calls')
        return isinstance(tool_calls, list) and bool(tool_calls)
    if api_style == 'openai_responses':
        output = parsed.get('output')
        if not isinstance(output, list):
            return False
        return any(
            isinstance(item, dict) and str(item.get('type') or '') == 'function_call'
            for item in output
        )
    return False


def _extract_provider_usage(parsed: dict[str, Any], *, api_style: str) -> dict[str, int]:
    usage = parsed.get('usage') if isinstance(parsed, dict) else None
    if not isinstance(usage, dict):
        return {}
    prompt_tokens = usage.get('prompt_tokens')
    completion_tokens = usage.get('completion_tokens')
    total_tokens = usage.get('total_tokens')
    if api_style == 'openai_responses':
        prompt_tokens = usage.get('input_tokens', prompt_tokens)
        completion_tokens = usage.get('output_tokens', completion_tokens)
    details = usage.get('prompt_tokens_details')
    input_details = usage.get('input_tokens_details')
    cache_read = None
    cache_write = None
    if isinstance(details, dict):
        cache_read = details.get('cached_tokens')
    if isinstance(input_details, dict):
        cache_read = input_details.get('cached_tokens', cache_read)
        cache_write = input_details.get('cache_write_tokens', input_details.get('cache_creation_tokens'))
    normalized: dict[str, int] = {}
    for key, value in {
        'provider_prompt_tokens': prompt_tokens,
        'provider_completion_tokens': completion_tokens,
        'provider_total_tokens': total_tokens,
        'provider_cache_read_tokens': cache_read,
        'provider_cache_write_tokens': cache_write,
    }.items():
        if type(value) is int and value >= 0:
            normalized[key] = value
    return normalized


def _extract_sse_provider_usage(raw_body: str, *, api_style: str) -> dict[str, int]:
    for event_name, payload in reversed(_parse_sse_events(raw_body)):
        if event_name != 'response.completed':
            usage = _extract_provider_usage(payload, api_style=api_style)
            if usage:
                return usage
            continue
        response_payload = payload.get('response')
        if not isinstance(response_payload, dict):
            continue
        usage = _extract_provider_usage(response_payload, api_style=api_style)
        if usage:
            return usage
    return {}


def _sse_contains_provider_tool_call_payload(raw_body: str) -> bool:
    for event_name, payload in _parse_sse_events(raw_body):
        if event_name == 'response.output_item.done':
            item = payload.get('item')
            if isinstance(item, dict) and str(item.get('type') or '') == 'function_call':
                return True
            continue
        if event_name != 'response.completed':
            continue
        response_payload = payload.get('response')
        if not isinstance(response_payload, dict):
            continue
        output = response_payload.get('output')
        if not isinstance(output, list):
            continue
        if any(
            isinstance(item, dict) and str(item.get('type') or '') == 'function_call'
            for item in output
        ):
            return True
    return False


def _messages_trace_text(messages: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for message in messages[-6:]:
        role = str(message.get('role', '') or '')
        content = message.get('content')
        if isinstance(content, str):
            text = content
        else:
            text = json.dumps(_redacted_content_for_trace(content), ensure_ascii=False, sort_keys=True)
        chunks.append(f'{role}: {text[:1000]}')
    return '\n'.join(chunks)


def _redacted_content_for_trace(content: Any) -> Any:
    if isinstance(content, list):
        redacted: list[Any] = []
        for item in content:
            if isinstance(item, dict) and item.get('type') == 'input_image':
                updated = dict(item)
                updated['image_url'] = redact_image_url_value(str(updated.get('image_url', '') or ''))
                redacted.append(updated)
            else:
                redacted.append(item)
        return redacted
    return content


def _messages_input_image_count(messages: list[dict[str, Any]]) -> int:
    count = 0
    for message in messages:
        content = message.get('content')
        if isinstance(content, list):
            count += sum(1 for item in content if isinstance(item, dict) and item.get('type') == 'input_image')
    return count


def _openai_finish_reason(parsed: dict[str, Any]) -> str:
    choices = parsed.get('choices')
    if not isinstance(choices, list) or not choices:
        return ''
    first = next((choice for choice in choices if isinstance(choice, dict) and choice.get('index', 0) == 0), {})
    if not isinstance(first, dict):
        return ''
    return str(first.get('finish_reason', '') or '')


def _openai_chat_primary_message(parsed: dict[str, Any]) -> dict[str, Any]:
    choices = parsed.get('choices')
    if not isinstance(choices, list):
        raise retryable_malformed_response(error_message='OpenAI Chat choices must be an array')
    primary = [choice for choice in choices if isinstance(choice, dict) and choice.get('index', 0) == 0]
    if len(primary) != 1 or not isinstance(primary[0].get('message'), dict):
        raise retryable_malformed_response(error_message='OpenAI Chat response requires one primary message')
    return primary[0]['message']


def _openai_responses_native_tools(
    specs: list[NativeToolSpec],
    *,
    native_tool_search_output: bool,
    strict: bool = False,
    hosted_web_search_spec: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return OpenAINativeToolAdapter().build_responses_provider_tools(
        specs,
        strict=strict,
        native_tool_search_output=native_tool_search_output,
        hosted_web_search_spec=hosted_web_search_spec,
    )


def _supports_native_tool_search_output(
    *,
    api_style: str,
    provider: str,
    model: str,
    explicit: bool | None,
) -> bool:
    if api_style != 'openai_responses':
        return False
    if explicit is not None:
        return explicit
    if provider.casefold() != 'openai':
        return False
    match = re.match(r'^gpt-5\.(\d+)(?:-|$)', model.strip().casefold())
    return bool(match and int(match.group(1)) >= 4)


def _openai_responses_message_item(message: dict[str, Any]) -> dict[str, Any]:
    role = str(message.get('role', '') or '').strip()
    if role not in {'user', 'assistant', 'system', 'developer'}:
        role = 'user'
    content = message.get('content', '')
    return {'role': role, 'content': _openai_responses_content(content)}


def _openai_chat_message_item(message: dict[str, Any]) -> dict[str, Any]:
    role = str(message.get('role', '') or '').strip()
    if role not in {'user', 'assistant', 'system', 'developer', 'tool'}:
        role = 'user'
    content = message.get('content', '')
    return {'role': role, 'content': _openai_chat_content(content)}


def _openai_responses_content(content: Any) -> str | list[dict[str, Any]]:
    items = _coerce_model_content_items(content)
    if not items:
        return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, sort_keys=True)
    blocks: list[dict[str, Any]] = []
    for item in items:
        if item.type == 'input_image':
            block = {
                'type': 'input_image',
                'image_url': item.image_url,
            }
            if item.detail:
                block['detail'] = item.detail
            blocks.append(block)
        else:
            blocks.append({'type': 'input_text', 'text': item.text})
    return blocks


def _openai_chat_content(content: Any) -> str | list[dict[str, Any]]:
    items = _coerce_model_content_items(content)
    if not items:
        return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, sort_keys=True)
    blocks: list[dict[str, Any]] = []
    for item in items:
        if item.type == 'input_image':
            blocks.append({'type': 'image_url', 'image_url': {'url': item.image_url}})
        else:
            blocks.append({'type': 'text', 'text': item.text})
    return blocks


def _coerce_model_content_items(content: Any) -> tuple[ModelContentItem, ...]:
    if isinstance(content, list):
        return content_items_from_dicts(content)
    return ()


def _parse_openai_responses_response_items(
    parsed: dict[str, Any],
) -> tuple[ProviderOutputItem, ...]:
    from ownpi.runtime.native_tools.provider_output_failure import preserve_provider_parse_source

    with preserve_provider_parse_source(parsed):
        return _parse_openai_responses_output_items(parsed)


def _parse_openai_responses_output_items(
    parsed: dict[str, Any],
) -> tuple[ProviderOutputItem, ...]:
    output = parsed.get('output', [])
    if not isinstance(output, list):
        raise ValueError('OpenAI Responses output must be an array')
    items: list[ProviderOutputItem] = []
    for output_item in output:
        if not isinstance(output_item, dict):
            continue
        if not isinstance(output_item.get('type'), str):
            continue
        item_type = str(output_item.get('type', '') or '')
        if item_type in {'message', 'reasoning', 'function_call', 'tool_search_call', 'web_search_call'}:
            if output_item.get('id') is not None and not isinstance(output_item['id'], str):
                continue
        provider_item_id = str(output_item.get('id', '') or '')
        provider_status = str(output_item.get('status') or '')
        payload = FrozenProviderJson.from_value(output_item)
        metadata = FrozenProviderJson.from_value(
            {'citation_count': count_url_citations(output_item)}
        )
        if item_type == 'message':
            content = output_item.get('content')
            if not isinstance(output_item.get('role'), str) or not isinstance(content, list):
                continue
            blocks = provider_message_blocks(content, source='openai_responses')
            text = ''.join(
                block['text']
                for block in blocks
                if block['type'] in {'input_text', 'output_text'}
            )
            items.append(
                ProviderOutputItem(
                    kind=ProviderOutputItemKind.MESSAGE,
                    sequence_index=len(items),
                    provider_type=item_type,
                    provider_payload=payload,
                    provider_item_id=provider_item_id,
                    provider_status=provider_status,
                    text=text,
                    content_blocks=tuple(
                        FrozenProviderJson.from_value(block)
                        for block in blocks
                        if isinstance(block, dict)
                    ),
                    provider_metadata=metadata,
                )
            )
            continue
        if item_type == 'reasoning':
            if output_item.get('encrypted_content') is not None and not isinstance(output_item['encrypted_content'], str):
                continue
            summary = output_item.get('summary')
            if not isinstance(summary, list) or any(not isinstance(block, dict) for block in summary):
                continue
            blocks = summary
            if any(block.get('type') != 'summary_text' or not isinstance(block.get('text'), str) for block in blocks):
                continue
            reasoning_content = output_item.get('content')
            if reasoning_content is not None and (
                not isinstance(reasoning_content, list) or any(
                    not isinstance(block, dict) or not isinstance(block.get('type'), str)
                    or block['type'] not in {'reasoning_text', 'text'}
                    or not isinstance(block.get('text'), str) for block in reasoning_content
                )
            ):
                continue
            summary_text = '\n'.join(
                str(block.get('text') or '')
                for block in blocks
                if isinstance(block, dict) and block.get('text') is not None
            )
            items.append(
                ProviderOutputItem(
                    kind=ProviderOutputItemKind.REASONING,
                    sequence_index=len(items),
                    provider_type=item_type,
                    provider_payload=payload,
                    provider_item_id=provider_item_id,
                    provider_status=provider_status,
                    text=summary_text,
                    content_blocks=tuple(
                        FrozenProviderJson.from_value(block)
                        for block in blocks
                        if isinstance(block, dict)
                    ),
                    provider_metadata=metadata,
                )
            )
            continue
        if item_type == 'web_search_call':
            if output_item.get('status') is not None and not isinstance(output_item['status'], str):
                continue
            action = output_item.get('action')
            if action is not None:
                if not isinstance(action, dict) or not isinstance(action.get('type'), str):
                    continue
                action_fields = {'search': ('query',), 'open_page': ('url',), 'find_in_page': ('url', 'pattern')}
                if any(action.get(key) is not None and not isinstance(action[key], str)
                       for key in action_fields.get(action['type'], ())):
                    continue
                queries = action.get('queries')
                if action['type'] == 'search' and queries is not None and (
                    not isinstance(queries, list) or any(not isinstance(query, str) for query in queries)
                ):
                    continue
            items.append(
                ProviderOutputItem(
                    kind=ProviderOutputItemKind.HOSTED_TOOL_CALL,
                    sequence_index=len(items),
                    provider_type=item_type,
                    provider_payload=payload,
                    provider_item_id=provider_item_id,
                    provider_status=provider_status,
                    provider_metadata=metadata,
                )
            )
            continue
        if item_type not in {'function_call', 'tool_search_call'}:
            items.append(
                ProviderOutputItem(
                    kind=ProviderOutputItemKind.PROVIDER_SPECIFIC,
                    sequence_index=len(items),
                    provider_type=item_type or 'unknown',
                    provider_payload=payload,
                    provider_item_id=provider_item_id,
                    provider_status=provider_status,
                    provider_metadata=metadata,
                )
            )
            continue
        tool_name = 'tool_search' if item_type == 'tool_search_call' else output_item.get('name')
        if item_type == 'function_call':
            if output_item.get('namespace') is not None and not isinstance(output_item['namespace'], str):
                continue
        else:
            if not isinstance(output_item.get('execution'), str):
                continue
            if output_item.get('status') is not None and not isinstance(output_item['status'], str):
                continue
        if not isinstance(tool_name, str):
            continue
        call_id = output_item.get('call_id')
        if item_type == 'function_call' and not isinstance(call_id, str):
            continue
        if item_type == 'tool_search_call' and call_id is not None and not isinstance(call_id, str):
            continue
        raw_arguments_value = output_item.get('arguments')
        if item_type == 'function_call' and not isinstance(raw_arguments_value, str):
            continue
        raw_arguments = (
            raw_arguments_value
            if isinstance(raw_arguments_value, str)
            else json.dumps(
                raw_arguments_value,
                ensure_ascii=False,
                separators=(',', ':'),
                sort_keys=True,
            )
        )
        items.append(
            ProviderOutputItem(
                kind=(ProviderOutputItemKind.TOOL_CALL
                      if item_type == 'function_call' or (call_id is not None and output_item.get('execution') == 'client')
                      else ProviderOutputItemKind.PROVIDER_SPECIFIC),
                sequence_index=len(items),
                provider_type=item_type,
                provider_payload=payload,
                provider_item_id=provider_item_id,
                provider_status=provider_status,
                tool_name=tool_name,
                call_id=call_id if call_id is not None else '',
                request_id=call_id if call_id is not None else '',
                raw_arguments=raw_arguments,
                provider_metadata=metadata,
            )
        )
    return tuple(items)


def _provider_output_envelope(
    *,
    parsed: dict[str, Any],
    completed_items: tuple[ProviderOutputItem, ...],
    usage: dict[str, int],
    provider_metadata: dict[str, Any],
    provider_protocol: str,
    stop_reason: str = '',
) -> ProviderOutputEnvelope:
    response_id = str(parsed.get('id') or provider_metadata.get('response_id') or '')
    status = str(parsed.get('status') or provider_metadata.get('status') or '')
    return ProviderOutputEnvelope(
        completed_items=completed_items,
        completion=ProviderOutputCompletion.create(
            response_id=response_id,
            status=status,
            stop_reason=stop_reason,
            usage=usage,
            provider_metadata={
                **{key: value for key, value in parsed.items() if key not in {'output', 'choices'}},
                **provider_metadata,
            },
            provider_protocol=provider_protocol,
        ),
        source=ProviderOutputSource(
            kind=ProviderOutputSourceKind.BLOCKING,
            response_payload=FrozenProviderJson.from_value(parsed),
            completed_item_payloads=tuple(item.provider_payload for item in completed_items),
        ),
    )


def _provider_error_is_bad_response_body(exc: BaseException) -> bool:
    text = str(exc).casefold()
    return 'bad_response_body' in text or ('invalid character' in text and 'beginning of value' in text)


def _hosted_tool_execution_trace_metadata(
    parsed: dict[str, Any],
) -> dict[str, Any]:
    output = parsed.get('output')
    if not isinstance(output, list):
        return {'hosted_tool_execution_observed': False}
    hosted_items = [
        item
        for item in output
        if isinstance(item, dict)
        and str(item.get('type') or '') == 'web_search_call'
    ][:16]
    return {
        'hosted_tool_execution_observed': bool(hosted_items),
        'hosted_tool_item_count': len(hosted_items),
        'hosted_tool_item_ids': tuple(
            str(item.get('id') or '')[:160] for item in hosted_items
        ),
        'hosted_tool_item_types': tuple(
            str(item.get('type') or '')[:80] for item in hosted_items
        ),
        'hosted_tool_statuses': tuple(
            str(item.get('status') or '')[:80] for item in hosted_items
        ),
    }


class _RetriableJSONPayloadError(RuntimeError):
    pass


class _RequestProgressEmitter:
    def __init__(
        self,
        *,
        emit_callback,
        interval_seconds: float,
    ) -> None:
        self._emit_callback = emit_callback
        self._interval_seconds = max(1.0, float(interval_seconds))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        context = copy_context()
        self._thread = threading.Thread(
            target=context.run,
            args=(self._run,),
            name='ownpi-llm-heartbeat',
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=0.2)

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            self._emit_callback()


class OpenAICompatibleClient(LLMClient):
    _web_search_execution_mode = WebSearchExecutionMode.DISABLED
    _hosted_web_search_options = HostedWebSearchOptions(mode='disabled')

    def __init__(self, config: LLMConfig) -> None:
        forbidden_model_options = {
            option
            for option in config.model_options
            if option in {'native_tool_result_continuation', 'previous_response_id'}
        }
        if forbidden_model_options:
            forbidden = ', '.join(sorted(forbidden_model_options))
            raise ValueError(
                'OpenAI native tool transport has been removed; unsupported model_options: '
                f'{forbidden}'
            )
        self._config = config
        self._provider = config.provider
        self._model = config.model
        self._input_modalities = tuple(config.input_modalities)
        self._reasoning_effort = config.reasoning_effort
        self._api_key = os.getenv(config.api_key_env or "")
        self._base_url = self._resolve_base_url(config)
        self._api_style = config.api_style
        self._native_tool_search_output_override = config.native_tool_search_output
        self._native_tool_strict = config.native_tool_strict
        self._supports_parallel_tool_calls = bool(config.supports_parallel_tool_calls)
        self._web_search_execution_mode = select_web_search_execution_mode(
            supports_hosted_web_search=config.supports_hosted_web_search,
            web_search_mode=config.web_search_mode,
        )
        self._hosted_web_search_options = HostedWebSearchOptions(
            mode=config.web_search_mode,
            context_size=config.web_search_context_size,
            allowed_domains=tuple(config.web_search_allowed_domains),
            user_location=tuple(config.web_search_user_location),
            search_content_types=tuple(config.web_search_content_types),
        )
        self._endpoint_path = config.endpoint_path
        self._request_timeout_seconds = max(1, int(config.request_timeout_seconds))
        self._sse_idle_timeout_seconds = max(
            1,
            min(int(config.sse_idle_timeout_seconds), int(config.request_timeout_seconds)),
        )
        self._stream_responses = bool(config.stream_responses)
        self._retry_policy = ProviderRetryPolicy(
            request_max_retries=config.effective_request_max_retries,
            stream_max_retries=config.effective_stream_max_retries,
            base_delay_seconds=config.retry_backoff_seconds,
            config_source=config.retry_config_source,
        )
        self._retry_sleep: Callable[[float], None] = time.sleep
        self._retry_jitter: Callable[[], float] = lambda: random.uniform(0.9, 1.1)
        self._retry_kernel = ProviderRetryKernel(
            self._retry_policy,
            sleeper=lambda seconds: self._retry_sleep(seconds),
            jitter=lambda: self._retry_jitter(),
            is_cancelled=self._is_cancelled,
        )
        if not self._api_key:
            raise ValueError(
                f"Environment variable {config.api_key_env!r} is not set; cannot use provider {config.provider!r}."
            )
        self._responses_transport = (
            ResponsesHttpTransport()
            if self._api_style == "openai_responses"
            else None
        )

    def close(self) -> None:
        if self._responses_transport is not None:
            self._responses_transport.close()

    def set_cancel_checker(self, checker: Callable[[], bool] | None) -> None:
        super().set_cancel_checker(checker)

    def _is_cancelled(self) -> bool:
        checker = current_cancel_checker(self)
        return bool(checker is not None and checker())

    def _raise_if_cancelled(self, boundary: str) -> None:
        if self._is_cancelled():
            raise RetryCancelled(f'provider request cancelled at {boundary}')

    def _build_http_request(
        self,
        *,
        url: str,
        data: bytes,
        accept: str,
    ) -> urllib.request.Request | ResponsesHttpRequest:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
            "User-Agent": "ownpi/0.1",
            "Accept": accept,
        }
        if self._api_style != "openai_responses":
            return urllib.request.Request(
                url=url,
                data=data,
                headers=headers,
                method="POST",
            )
        turn_session = provider_turn_session_for_request()
        context = current_provider_request_context()
        if context is not None:
            thread_id = context.run_id or context.task_id or context.session_id
            headers.update(
                {
                    "session-id": context.session_id,
                    "thread-id": thread_id,
                    "x-client-request-id": thread_id,
                }
            )
        return ResponsesHttpRequest(
            url=url,
            data=data,
            headers=headers,
            turn_session=turn_session,
        )

    @contextmanager
    def _open_http_request(
        self,
        request: urllib.request.Request | ResponsesHttpRequest,
    ) -> Iterator[Any]:
        self._raise_if_cancelled('http_handshake_before_open')
        if isinstance(request, urllib.request.Request):
            with run_provider_io(
                urllib.request.urlopen,
                request,
                timeout=self._request_timeout_seconds,
            ) as response:
                self._raise_if_cancelled('http_handshake_after_open')
                yield response
            return
        if self._responses_transport is None:
            raise RuntimeError("OpenAI Responses request requires a Responses HTTP transport")
        turn_session = request.turn_session or provider_turn_session_for_request()
        replayed = turn_session.turn_state is not None
        headers = dict(request.headers)
        if turn_session.turn_state is not None:
            headers["x-codex-turn-state"] = turn_session.turn_state
        wire_request = replace(request, headers=headers, turn_session=turn_session)
        with self._responses_transport.open(
            wire_request,
            timeout=self._request_timeout_seconds,
            read_timeout=(
                self._sse_idle_timeout_seconds
                if "text/event-stream" in str(headers.get("Accept") or "")
                else self._request_timeout_seconds
            ),
        ) as response:
            self._raise_if_cancelled('http_handshake_after_open')
            turn_session.capture_turn_state(response.headers.get("x-codex-turn-state"))
            response.transport_metadata = self._responses_transport_metadata(
                request=wire_request,
                turn_session=turn_session,
                replayed=replayed,
            )
            yield response

    def _responses_transport_metadata(
        self,
        *,
        request: ResponsesHttpRequest,
        turn_session: ProviderTurnSession,
        replayed: bool,
    ) -> dict[str, Any]:
        headers = request.headers
        turn_state = turn_session.turn_state
        return {
            "provider_turn_id_hash": _sensitive_hash(turn_session.turn_scope_id),
            "http_client_instance_hash": _sensitive_hash(
                str(self._responses_transport.client_instance_id)
                if self._responses_transport is not None
                else ""
            ),
            "turn_state_present": turn_state is not None,
            "turn_state_hash": _sensitive_hash(turn_state or ""),
            "turn_state_replayed": bool(replayed),
            "session_header_hash": _sensitive_hash(str(headers.get("session-id") or "")),
            "thread_header_hash": _sensitive_hash(str(headers.get("thread-id") or "")),
            "client_request_header_hash": _sensitive_hash(
                str(headers.get("x-client-request-id") or "")
            ),
        }

    @staticmethod
    def _response_transport_metadata(response: Any) -> dict[str, Any]:
        metadata = getattr(response, "transport_metadata", {})
        return dict(metadata) if isinstance(metadata, dict) else {}

    def _resolve_base_url(self, config: LLMConfig) -> str:
        if config.base_url:
            return config.base_url
        if config.base_url_env:
            value = os.getenv(config.base_url_env or "")
            if value:
                return value
        if config.provider.lower() == "openai":
            return "https://api.openai.com/v1"
        raise ValueError(
            "No base URL configured. Provide either `base_url`, `base_url_env`, "
            "or use provider='openai' to fall back to the official default."
        )

    def _resolve_endpoint(self) -> str:
        if self._endpoint_path:
            if self._endpoint_path.startswith("http://") or self._endpoint_path.startswith("https://"):
                return self._endpoint_path
            return self._base_url.rstrip("/") + "/" + self._endpoint_path.lstrip("/")

        default_paths = {
            "openai_chat": "/chat/completions",
            "openai_responses": "/responses",
        }
        if self._api_style not in default_paths:
            raise ValueError(f"Unsupported api_style: {self._api_style!r}")
        return self._base_url.rstrip("/") + default_paths[self._api_style]

    def _finalize_responses_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._api_style != "openai_responses":
            return payload
        payload['store'] = False
        include = payload.setdefault('include', [])
        if 'reasoning.encrypted_content' not in include:
            include.append('reasoning.encrypted_content')
        context = current_provider_request_context()
        if context is not None:
            payload["prompt_cache_key"] = context.session_id
        return payload

    def _build_payload(
        self,
        prompt: str,
        *,
        response_mode: str,
        schema: dict[str, Any] | None = None,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        structured_schema = _to_openai_response_format_schema(schema) if isinstance(schema, dict) and schema else None
        structured_name = _normalize_schema_name(schema_name or 'structured_response')
        system_content = (
            "You must return a single JSON object that matches the requested schema exactly."
            if _expects_json_response(response_mode)
            else "Answer clearly in natural language."
        )
        if self._api_style == "openai_chat":
            payload = {
                "model": self._model,
                "temperature": 0.1,
                "messages": [
                    {'role': 'system', 'content': system_content},
                    {'role': 'user', 'content': prompt},
                ],
            }
            if self._reasoning_effort:
                payload["reasoning_effort"] = self._reasoning_effort
            if self._stream_responses:
                payload["stream"] = True
                payload["stream_options"] = {"include_usage": True}
            if response_mode == "json":
                payload["response_format"] = {"type": "json_object"}
            elif response_mode == "structured_json":
                if structured_schema is None:
                    raise ValueError("structured_json response mode requires a JSON schema.")
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": structured_name,
                        "strict": True,
                        "schema": structured_schema,
                    },
                }
            return payload
        if self._api_style == "openai_responses":
            payload = {
                "model": self._model,
                "input": prompt,
            }
            if self._stream_responses:
                payload["stream"] = True
            if self._reasoning_effort:
                payload["reasoning"] = {"effort": self._reasoning_effort}
            if response_mode == "json":
                payload["text"] = {"format": {"type": "json_object"}}
            elif response_mode == "structured_json":
                if structured_schema is None:
                    raise ValueError("structured_json response mode requires a JSON schema.")
                payload["text"] = {
                    "format": {
                        "type": "json_schema",
                        "name": structured_name,
                        "strict": True,
                        "schema": structured_schema,
                    }
                }
            return self._finalize_responses_payload(payload)
        raise ValueError(f"Unsupported api_style: {self._api_style!r}")

    def _build_payload_from_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        response_mode: str,
        schema: dict[str, Any] | None = None,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        structured_schema = _to_openai_response_format_schema(schema) if isinstance(schema, dict) and schema else None
        structured_name = _normalize_schema_name(schema_name or 'structured_response')
        system_content = (
            "You must return a single JSON object that matches the requested schema exactly."
            if _expects_json_response(response_mode)
            else "Answer clearly in natural language."
        )
        if self._api_style == "openai_chat":
            projected_messages = [{'role': 'system', 'content': system_content}]
            projected_messages.extend(_openai_chat_message_item(message) for message in messages)
            payload = {
                "model": self._model,
                "temperature": 0.1,
                "messages": projected_messages,
            }
            if self._reasoning_effort:
                payload["reasoning_effort"] = self._reasoning_effort
            if self._stream_responses:
                payload["stream"] = True
                payload["stream_options"] = {"include_usage": True}
            if response_mode == "json":
                payload["response_format"] = {"type": "json_object"}
            elif response_mode == "structured_json":
                if structured_schema is None:
                    raise ValueError("structured_json response mode requires a JSON schema.")
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": structured_name,
                        "strict": True,
                        "schema": structured_schema,
                    },
                }
            return payload
        if self._api_style == "openai_responses":
            payload = {
                "model": self._model,
                "input": [_openai_responses_message_item(message) for message in messages],
            }
            if self._stream_responses:
                payload["stream"] = True
            if self._reasoning_effort:
                payload["reasoning"] = {"effort": self._reasoning_effort}
            if response_mode == "json":
                payload["text"] = {"format": {"type": "json_object"}}
            elif response_mode == "structured_json":
                if structured_schema is None:
                    raise ValueError("structured_json response mode requires a JSON schema.")
                payload["text"] = {
                    "format": {
                        "type": "json_schema",
                        "name": structured_name,
                        "strict": True,
                        "schema": structured_schema,
                    }
                }
            return self._finalize_responses_payload(payload)
        raise ValueError(f"Unsupported api_style: {self._api_style!r}")

    def _extract_response_json(
        self,
        parsed: dict[str, Any],
        raw_body: str,
        schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            if _contains_provider_tool_call_payload(parsed, api_style=self._api_style):
                raise _RetriableJSONPayloadError(
                    'Provider returned native tool-call payload under structured JSON mode; '
                    'canonical tool protocol requires schema-valid JSON only.'
                )
            if self._api_style == "openai_chat":
                content = parsed["choices"][0]["message"]["content"]
                return _drop_structured_null_optionals(
                    _loads_structured_json_text(content),
                    schema,
                )
            if self._api_style == "openai_responses":
                if "output_text" in parsed:
                    return _drop_structured_null_optionals(
                        _loads_structured_json_text(parsed["output_text"]),
                        schema,
                    )
                output_text = _extract_openai_responses_message_text(parsed.get("output"))
                if output_text is None:
                    raise KeyError("output")
                return _drop_structured_null_optionals(
                    _loads_structured_json_text(output_text),
                    schema,
                )
        except json.JSONDecodeError as exc:
            raise _RetriableJSONPayloadError(
                f"Could not parse JSON content from {self._api_style} response: {raw_body}"
            ) from exc
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected {self._api_style} response: {raw_body}") from exc

        raise RuntimeError(f"Unsupported api_style during response extraction: {self._api_style!r}")

    def _extract_response_text(self, parsed: dict[str, Any], raw_body: str) -> str:
        try:
            if self._api_style == "openai_chat":
                content = parsed["choices"][0]["message"]["content"]
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    return "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
            if self._api_style == "openai_responses":
                if "output_text" in parsed:
                    return str(parsed["output_text"])
                output_text = _extract_openai_responses_message_text(parsed.get("output"))
                if output_text is None:
                    raise KeyError("output")
                return output_text
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected {self._api_style} response: {raw_body}") from exc

        raise RuntimeError(f"Unsupported api_style during text extraction: {self._api_style!r}")

    def _extract_sse_text(self, raw_body: str) -> str:
        output_text_done: str | None = None
        output_text_deltas: list[str] = []
        chat_completion_deltas: list[str] = []
        completed_payload: dict[str, Any] | None = None
        tracker = SSEEventTracker()

        for event_name, payload in _parse_sse_events(raw_body):
            tracker.observe(event_name, payload)
            if event_name == "response.output_text.done":
                output_text_done = payload.get("text")
            elif event_name == "response.output_text.delta":
                delta = payload.get("delta")
                if isinstance(delta, str):
                    output_text_deltas.append(delta)
            elif event_name == "chat.completion.chunk":
                delta = _extract_chat_completion_stream_delta(payload)
                if delta:
                    chat_completion_deltas.append(delta)
            elif event_name == "response.completed":
                completed_payload = payload
            elif event_name == 'response.incomplete':
                raise tracker.incomplete_failure(payload)
            elif event_name == "error" and isinstance(payload.get("error"), dict):
                raise tracker.explicit_error_failure(payload['error'])
            elif event_name == "response.failed":
                response_error = payload.get("response", {}).get("error")
                if isinstance(response_error, dict):
                    raise tracker.explicit_error_failure(response_error)
                raise tracker.failure(
                    failure_kind='response_failed_without_error',
                    failure_class='transient_provider_failure',
                    retryable=True,
                    semantic_disposition='retryable_transport',
                )
            elif isinstance(payload.get("error"), dict):
                raise tracker.explicit_error_failure(payload['error'])

        if output_text_done:
            if self._api_style == 'openai_responses':
                tracker.finalize(has_output=True)
            return output_text_done

        if output_text_deltas:
            if self._api_style == 'openai_responses':
                tracker.finalize(has_output=True)
            return "".join(output_text_deltas)

        if chat_completion_deltas:
            return "".join(chat_completion_deltas)

        if completed_payload:
            response = completed_payload.get("response")
            if isinstance(response, dict):
                output = _extract_openai_responses_message_text(response.get("output"))
                if output:
                    return output
        tracker.finalize(has_output=False)
        raise protocol_sse_failure('terminal_without_output')

    @staticmethod
    def _json_object_from_mislabeled_event_stream(raw_body: str) -> dict[str, Any] | None:
        stripped = raw_body.lstrip()
        if not stripped.startswith('{'):
            return None
        try:
            parsed = json.loads(raw_body)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return parsed
        return None

    def _extract_sse_json(
        self,
        raw_body: str,
        schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if _sse_contains_provider_tool_call_payload(raw_body):
            raise protocol_sse_failure(
                'native_tool_call_in_structured_mode',
                error_message=(
                    'Provider returned native tool-call payload under structured JSON mode; '
                    'canonical tool protocol requires schema-valid JSON only.'
                ),
            )
        text = self._extract_sse_text(raw_body)
        try:
            return _drop_structured_null_optionals(json.loads(text), schema)
        except json.JSONDecodeError as exc:
            raise protocol_sse_failure(
                'malformed_structured_output',
                error_message=str(exc),
            ) from exc

    def _total_attempts(self, retry_kind: RetryKind) -> int:
        return self._retry_policy.total_attempts(retry_kind)

    @staticmethod
    def _http_retry_after_seconds(exc: BaseException) -> float | None:
        headers = getattr(exc, 'headers', None)
        if headers is None:
            return None
        raw_value = headers.get('Retry-After') or headers.get('retry-after')
        if raw_value is None:
            return None
        value = str(raw_value).strip()
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
            except (TypeError, ValueError, OverflowError):
                return None
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(
                0.0,
                (retry_at - datetime.now(timezone.utc)).total_seconds(),
            )

    @staticmethod
    def _retry_trace_metadata(attempt: dict[str, Any]) -> dict[str, Any]:
        keys = (
            'retry_kind',
            'retry_delay_seconds',
            'retry_delay_source',
            'retry_jitter_factor',
        )
        return {key: attempt[key] for key in keys if key in attempt}

    def _timeout_hint(self) -> str:
        return (
            "You can increase `request_timeout_seconds`, `sse_idle_timeout_seconds`, "
            "`request_max_retries`, or `stream_max_retries` in ~/.ownpi/config.yaml "
            "if your model/provider is slow."
        )

    def _is_retriable_transport_error(self, exc: BaseException) -> bool:
        details = self._classify_transport_error(exc)
        return bool(details.get('retriable'))

    def _transport_error_message(self, *, attempts: int, exc: BaseException) -> str:
        return build_transport_error_message(
            attempts=attempts,
            exc=exc,
            timeout_hint=self._timeout_hint(),
        )

    def _http_error_message(self, *, attempts: int, status_code: int, detail: str) -> str:
        return (
            f"LLM request failed with a transient HTTP error after {attempts} attempt(s): "
            f"HTTP {status_code}: {detail}. {self._timeout_hint()}"
        )

    @staticmethod
    def _sse_block_is_terminal(lines: list[str]) -> bool:
        event_name: str | None = None
        for line in lines:
            stripped = line.strip()
            if stripped == 'data: [DONE]':
                return True
            if stripped.startswith('event:'):
                event_name = stripped[len('event:') :].strip()
        return event_name in _SSE_TERMINAL_EVENTS

    @staticmethod
    def _response_socket_candidates(response: Any) -> list[Any]:
        candidates: list[Any] = []
        seen: set[int] = set()
        queue: list[tuple[Any, int]] = [(response, 0)]

        def _push(candidate: Any, depth: int) -> None:
            if candidate is None:
                return
            identity = id(candidate)
            if identity in seen:
                return
            seen.add(identity)
            candidates.append(candidate)
            if depth >= 4:
                return
            queue.append((candidate, depth))

        while queue:
            current, depth = queue.pop(0)
            if current is None or depth >= 4:
                continue
            for attr in ('socket', '_sock', 'fp', 'raw'):
                child = getattr(current, attr, None)
                if child is None:
                    continue
                identity = id(child)
                if identity in seen:
                    continue
                seen.add(identity)
                candidates.append(child)
                queue.append((child, depth + 1))
        return candidates

    def _apply_sse_idle_timeout(self, response: Any) -> bool:
        timeout_seconds = float(self._sse_idle_timeout_seconds)
        for candidate in self._response_socket_candidates(response):
            setter = getattr(candidate, 'settimeout', None)
            if not callable(setter):
                continue
            try:
                setter(timeout_seconds)
                return True
            except Exception:
                continue
        return False

    def _read_sse_body(self, response: Any) -> str:
        if not hasattr(response, 'readline'):
            self._raise_if_cancelled('sse_read_before_body')
            return run_provider_io(response.read).decode('utf-8', errors='replace')
        self._apply_sse_idle_timeout(response)
        raw_lines: list[str] = []
        current_block: list[str] = []
        while True:
            self._raise_if_cancelled('sse_read_before_line')
            raw_line = run_provider_io(response.readline)
            self._raise_if_cancelled('sse_read_after_line')
            if not raw_line:
                if current_block and self._sse_block_is_terminal(current_block):
                    break
                break
            if isinstance(raw_line, str):
                line = raw_line
            else:
                line = raw_line.decode('utf-8', errors='replace')
            normalized = line.rstrip('\r\n')
            raw_lines.append(normalized)
            if normalized.strip():
                current_block.append(normalized)
                if normalized.strip() == 'data: [DONE]':
                    break
                continue
            if self._sse_block_is_terminal(current_block):
                break
            current_block = []
        return '\n'.join(raw_lines)

    def _iter_sse_events(self, response: Any) -> Iterator[tuple[str, dict[str, Any]]]:
        if not hasattr(response, 'readline'):
            self._raise_if_cancelled('sse_read_before_body')
            body = run_provider_io(response.read).decode('utf-8', errors='replace')
            self._raise_if_cancelled('sse_read_after_body')
            yield from _parse_sse_events(body)
            return
        self._apply_sse_idle_timeout(response)
        current_event: str | None = None
        data_chunks: list[str] = []
        while True:
            self._raise_if_cancelled('sse_read_before_line')
            raw_line = run_provider_io(response.readline)
            self._raise_if_cancelled('sse_read_after_line')
            if not raw_line:
                if data_chunks:
                    if '\n'.join(data_chunks) == '[DONE]':
                        break
                    payload = self._parse_sse_payload(data_chunks)
                    if payload is not None:
                        event_name = current_event or _sse_event_name_from_payload(payload)
                        if event_name and not _is_responses_keepalive(event_name, payload):
                            yield event_name, payload
                break
            line = raw_line if isinstance(raw_line, str) else raw_line.decode('utf-8', errors='replace')
            normalized = line.rstrip('\r\n')
            stripped = normalized.strip()
            if not stripped:
                if data_chunks:
                    if '\n'.join(data_chunks) == '[DONE]':
                        break
                    payload = self._parse_sse_payload(data_chunks)
                    if payload is not None:
                        event_name = current_event or _sse_event_name_from_payload(payload)
                        if event_name and not _is_responses_keepalive(event_name, payload):
                            yield event_name, payload
                    if current_event in _SSE_TERMINAL_EVENTS:
                        break
                current_event = None
                data_chunks = []
                continue
            if stripped.startswith('event:'):
                current_event = stripped[len('event:') :].strip()
            elif stripped.startswith('data:'):
                data_value = stripped[len('data:') :].strip()
                if data_value == '[DONE]':
                    break
                data_chunks.append(data_value)

    @staticmethod
    def _parse_sse_payload(data_chunks: list[str]) -> dict[str, Any] | None:
        data_str = '\n'.join(data_chunks)
        if data_str == '[DONE]':
            return None
        try:
            payload = json.loads(data_str)
        except json.JSONDecodeError as exc:
            raise malformed_sse_failure(data=data_str, event_count=0) from exc
        if isinstance(payload, dict):
            return payload
        return None

    def _read_response_body(self, response: Any, *, content_type: str) -> str:
        if 'text/event-stream' in content_type.casefold():
            return self._read_sse_body(response)
        self._raise_if_cancelled('response_read_before_body')
        body = run_provider_io(response.read).decode('utf-8', errors='replace')
        self._raise_if_cancelled('response_read_after_body')
        return body

    @staticmethod
    def _unwrap_transport_error(exc: BaseException) -> BaseException:
        return unwrap_transport_error(exc)

    def _classify_transport_error(self, exc: BaseException) -> dict[str, Any]:
        return classify_transport_error(exc).to_dict()

    def _post_raw_payload(
        self,
        *,
        payload: dict[str, Any],
        request_kind: str,
        response_mode: str,
        prompt_for_trace: str,
        extra_trace_metadata: dict[str, Any] | None = None,
        response_transport: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        outer_retry_count = {'value': 0}
        trace_attempts: list[dict[str, Any]] = []

        def operation(attempt: int) -> dict[str, Any]:
            metadata = {
                **dict(extra_trace_metadata or {}),
                'logical_sampling_attempt': attempt,
                'retry_owner': 'outer_sampling' if attempt > 1 else 'none',
                'outer_sampling_retry_count': outer_retry_count['value'],
                'same_turn_sampling_retry_count': outer_retry_count['value'],
                'same_turn_sampling_recovery_count': outer_retry_count['value'] > 0,
                'retry_recovery_count': int(outer_retry_count['value'] > 0),
            }
            try:
                return self._post_raw_payload_attempt(
                    payload=payload,
                    request_kind=request_kind,
                    response_mode=response_mode,
                    prompt_for_trace=prompt_for_trace,
                    extra_trace_metadata=metadata,
                    trace_attempts=trace_attempts,
                    sampling_attempt=attempt,
                    response_transport=response_transport,
                )
            except ProviderSSEFailure as exc:
                if (
                    not exc.retryable
                    or exc.diagnostics.retry_owner != 'outer_sampling'
                ):
                    raise
                raise RetryableProviderOperation(
                    owner=exc.diagnostics.retry_owner or 'outer_sampling',
                    kind=exc.diagnostics.retry_kind or 'request',
                    cause=exc,
                    retry_after_seconds=exc.retry_after_seconds,
                    trace_metadata=exc.to_trace_metadata(),
                ) from exc


        def on_retry(
            decision: Any,
            _failure: RetryableProviderOperation,
        ) -> None:
            if decision.owner == 'outer_sampling':
                outer_retry_count['value'] += 1

        try:
            result, _state = self._retry_kernel.run_nested(
                operation,
                kind='request',
                on_retry=on_retry,
            )
            return result
        except RetryExhausted as exc:
            failure: BaseException = exc.last_failure.cause
            if isinstance(failure, RetryExhausted):
                failure = failure.last_failure.cause
            failure_metadata = (
                failure.to_trace_metadata()
                if isinstance(failure, ProviderSSEFailure)
                else dict(exc.last_failure.trace_metadata)
            )
            self._emit_trace(
                url=self._resolve_endpoint(),
                prompt=prompt_for_trace,
                response_mode=response_mode,
                request_kind=request_kind,
                success=False,
                attempts=trace_attempts,
                total_attempts=self._total_attempts('request'),
                elapsed_ms=0.0,
                error_type=type(failure).__name__,
                error_message=str(failure)[:400],
                extra_metadata={
                    'transport_mode': 'json',
                    'outer_sampling_exhausted': True,
                    **failure_metadata,
                    **exc.outcome.to_trace_metadata(),
                    **dict(extra_trace_metadata or {}),
                },
            )
            if isinstance(failure, ProviderSSEFailure):
                raise failure
            raise failure

    def _post_raw_payload_attempt(
        self,
        *,
        payload: dict[str, Any],
        request_kind: str,
        response_mode: str,
        prompt_for_trace: str,
        extra_trace_metadata: dict[str, Any] | None = None,
        trace_attempts: list[dict[str, Any]] | None = None,
        sampling_attempt: int = 1,
        response_transport: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = self._resolve_endpoint()
        payload_json = json.dumps(payload)
        payload_bytes = payload_json.encode('utf-8')
        # The shared kernel owns retry attempts; this adapter method is one
        # wire attempt only.
        total_attempts = 1
        started_at = time.perf_counter()
        trace_attempts = trace_attempts if trace_attempts is not None else []
        last_error: Exception | None = None
        for attempt in range(1, 2):
            attempt_started_at = time.perf_counter()
            try:
                request = self._build_http_request(
                    url=url,
                    data=payload_bytes,
                    accept='application/json',
                )
                with self._open_http_request(request) as response:
                    body = run_provider_io(response.read).decode('utf-8', errors='replace')
                    if response_transport is not None:
                        response_transport['actual_transport'] = 'json'
                        response_transport['content_type'] = str(response.headers.get('Content-Type', '') or '')
                parsed = decode_native_json(body) if response_mode == 'native_tools' else json.loads(body)
                if not isinstance(parsed, dict):
                    raise _RetriableJSONPayloadError('Provider response was not a JSON object.')
                if response_mode == 'native_tools':
                    failure = native_blocking_response_error(parsed, responses=self._api_style == 'openai_responses')
                    if failure is not None:
                        raise failure
                trace_attempts.append(
                    {
                        'attempt': attempt,
                        'sampling_attempt': sampling_attempt,
                        'status': 'success',
                        'elapsed_ms': round((time.perf_counter() - attempt_started_at) * 1000, 2),
                        'content_type': response.headers.get('Content-Type', ''),
                        **self._response_transport_metadata(response),
                    }
                )
                self._emit_trace(
                    url=url,
                    prompt=prompt_for_trace,
                    response_mode=response_mode,
                    request_kind=request_kind,
                    success=True,
                    attempts=trace_attempts,
                    total_attempts=total_attempts,
                    elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
                    usage=_extract_provider_usage(parsed, api_style=self._api_style),
                    extra_metadata={
                        'transport_mode': 'json',
                        'request_payload_char_count': len(payload_json),
                        **(
                            _hosted_tool_execution_trace_metadata(parsed)
                            if response_mode == 'native_tools'
                            else {}
                        ),
                        **dict(extra_trace_metadata or {}),
                    },
                )
                return parsed
            except _HTTP_ERROR_TYPES as exc:
                detail = exc.read().decode('utf-8', errors='replace')
                last_error = exc
                classification = classify_provider_http_error(exc.code, detail)
                trace_attempts.append(
                    {
                        'attempt': attempt,
                        'status': 'http_error',
                        'elapsed_ms': round((time.perf_counter() - attempt_started_at) * 1000, 2),
                        'error_type': type(exc).__name__,
                        **classification.to_trace_metadata(),
                    }
                )
                failure = provider_http_failure(
                    classification=classification,
                    detail=detail,
                )
                if classification.retryable and classification.retry_kind is not None:
                    raise RetryableProviderOperation(
                        owner=classification.retry_owner or 'inner_transport',
                        kind=classification.retry_kind,
                        cause=failure,
                        trace_metadata=failure.to_trace_metadata(),
                        retry_after_seconds=(
                            self._http_retry_after_seconds(exc)
                            or classification.retry_after_seconds
                        ),
                    ) from exc
                self._emit_trace(
                    url=url,
                    prompt=prompt_for_trace,
                    response_mode=response_mode,
                    request_kind=request_kind,
                    success=False,
                    attempts=trace_attempts,
                    total_attempts=total_attempts,
                    error_type=type(exc).__name__,
                    error_message=f'HTTP {exc.code}: {detail[:400]}',
                    elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
                    extra_metadata={
                        'transport_mode': 'json',
                        'request_payload_char_count': len(payload_json),
                        **dict(extra_trace_metadata or {}),
                    },
                )
                raise failure from exc
            except ProviderSSEFailure as failure:
                if not failure.retryable:
                    raise
                raise RetryableProviderOperation(
                    owner='outer_sampling', kind='stream', cause=failure,
                    trace_metadata=failure.to_trace_metadata(),
                    retry_after_seconds=failure.retry_after_seconds,
                ) from failure
            except (json.JSONDecodeError, _RetriableJSONPayloadError) as exc:
                if response_mode == 'native_tools':
                    failure = retryable_malformed_response(error_message=str(exc))
                    raise RetryableProviderOperation(
                        owner='outer_sampling', kind='stream', cause=failure,
                        trace_metadata=failure.to_trace_metadata(),
                    ) from exc
                failure = protocol_sse_failure('malformed_response', error_message=str(exc))
                trace_attempts.append({
                    'attempt': attempt, 'status': 'protocol_error',
                    'error_type': type(exc).__name__, **failure.to_trace_metadata(),
                })
                self._emit_trace(
                    url=url,
                    prompt=prompt_for_trace,
                    response_mode=response_mode,
                    request_kind=request_kind,
                    success=False,
                    attempts=trace_attempts,
                    total_attempts=total_attempts,
                    elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
                    error_type=type(exc).__name__,
                    error_message=str(failure),
                    extra_metadata={**dict(extra_trace_metadata or {}), **failure.to_trace_metadata()},
                )
                raise failure from exc
            except Exception as exc:
                last_error = exc
                transport_details = self._classify_transport_error(exc)
                trace_attempts.append(
                    build_transport_trace_attempt(
                        attempt=attempt,
                        elapsed_ms=(time.perf_counter() - attempt_started_at) * 1000,
                        exc=exc,
                    )
                )
                failure = provider_transport_failure(
                    exception=exc,
                    phase=str(transport_details.get('phase') or 'unknown'),
                    kind=str(transport_details.get('kind') or type(exc).__name__),
                    detail=str(transport_details.get('reason_message') or exc),
                    retryable=bool(transport_details.get('retriable')),
                )
                if bool(transport_details.get('retriable')):
                    raise RetryableProviderOperation(
                        owner='inner_transport',
                        kind='request',
                        cause=failure,
                        trace_metadata=failure.to_trace_metadata(),
                    ) from exc
                self._emit_trace(
                    url=url,
                    prompt=prompt_for_trace,
                    response_mode=response_mode,
                    request_kind=request_kind,
                    success=False,
                    attempts=trace_attempts,
                    total_attempts=total_attempts,
                    error_type=type(exc).__name__,
                    error_message=str(transport_details.get('reason_message') or exc)[:400],
                    elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
                    extra_metadata={
                        'transport_mode': 'json',
                        'request_payload_char_count': len(payload_json),
                        **dict(extra_trace_metadata or {}),
                    },
                )
                raise failure from exc
        raise RuntimeError(f'LLM native tool request failed unexpectedly: {last_error}')

    def _post_native_tool_stream_payload(
        self,
        *,
        payload: dict[str, Any],
        request_kind: str,
        prompt_for_trace: str,
        extra_trace_metadata: dict[str, Any] | None = None,
        transport_mode: str = 'sse',
        response_transport: dict[str, str] | None = None,
        completed_output: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        url = self._resolve_endpoint()
        stream_payload = dict(payload)
        stream_payload['stream'] = True
        payload_json = json.dumps(stream_payload)
        payload_bytes = payload_json.encode('utf-8')
        total_attempts = self._total_attempts('stream')
        started_at = time.perf_counter()
        trace_attempts: list[dict[str, Any]] = []
        def operation(sampling_attempt: int) -> dict[str, Any]:
            return self._post_native_tool_stream_attempt(
                url=url,
                payload_bytes=payload_bytes,
                payload_json=payload_json,
                request_kind=request_kind,
                prompt_for_trace=prompt_for_trace,
                sampling_attempt=sampling_attempt,
                trace_attempts=trace_attempts,
                response_transport=response_transport,
                completed_output=completed_output,
            )

        def on_retry(decision: Any, _failure: RetryableProviderOperation) -> None:
            if trace_attempts:
                trace_attempts[-1].update(decision.to_trace_metadata())

        try:
            parsed, _state = self._retry_kernel.run_nested(
                operation,
                kind='stream',
                on_retry=on_retry,
            )
        except RetryExhausted as exc:
            failure: BaseException = exc.last_failure.cause
            if isinstance(failure, RetryExhausted):
                failure = failure.last_failure.cause
            failure_metadata = (
                failure.to_trace_metadata()
                if isinstance(failure, ProviderSSEFailure)
                else dict(exc.last_failure.trace_metadata)
            )
            self._emit_trace(
                url=url,
                prompt=prompt_for_trace,
                response_mode='native_tools',
                request_kind=request_kind,
                success=False,
                attempts=trace_attempts,
                total_attempts=total_attempts,
                error_type=type(failure).__name__,
                error_message=str(failure)[:400],
                elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
                extra_metadata={
                    'transport_mode': transport_mode,
                    'request_payload_char_count': len(payload_json),
                    **failure_metadata,
                    **exc.outcome.to_trace_metadata(),
                    **dict(extra_trace_metadata or {}),
                },
            )
            if isinstance(failure, ProviderSSEFailure):
                raise failure
            raise failure
        except ProviderSSEFailure as failure:
            self._emit_trace(
                url=url,
                prompt=prompt_for_trace,
                response_mode='native_tools',
                request_kind=request_kind,
                success=False,
                attempts=trace_attempts,
                total_attempts=total_attempts,
                error_type=type(failure).__name__,
                error_message=str(failure)[:400],
                elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
                extra_metadata={
                    'transport_mode': transport_mode,
                    'request_payload_char_count': len(payload_json),
                    **failure.to_trace_metadata(),
                    **dict(extra_trace_metadata or {}),
                },
            )
            raise
        except RetryableProviderOperation as exc:
            failure = exc.cause
            failure_metadata = (
                failure.to_trace_metadata()
                if isinstance(failure, ProviderSSEFailure)
                else dict(exc.trace_metadata)
            )
            self._emit_trace(
                url=url,
                prompt=prompt_for_trace,
                response_mode='native_tools',
                request_kind=request_kind,
                success=False,
                attempts=trace_attempts,
                total_attempts=total_attempts,
                error_type=type(failure).__name__,
                error_message=str(failure)[:400],
                elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
                extra_metadata={
                    'transport_mode': transport_mode,
                    'request_payload_char_count': len(payload_json),
                    **failure_metadata,
                    **dict(extra_trace_metadata or {}),
                },
            )
            if isinstance(failure, ProviderSSEFailure):
                raise failure
            raise failure

        self._emit_trace(
            url=url,
            prompt=prompt_for_trace,
            response_mode='native_tools',
            request_kind=request_kind,
            success=True,
            attempts=trace_attempts,
            total_attempts=total_attempts,
            elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
            usage=_extract_provider_usage(parsed, api_style=self._api_style),
            extra_metadata={
                'transport_mode': transport_mode,
                'request_payload_char_count': len(payload_json),
                **_hosted_tool_execution_trace_metadata(parsed),
                **dict(extra_trace_metadata or {}),
            },
        )
        return parsed

    def _post_native_tool_stream_attempt(
        self,
        *,
        url: str,
        payload_bytes: bytes,
        payload_json: str,
        request_kind: str,
        prompt_for_trace: str,
        sampling_attempt: int,
        trace_attempts: list[dict[str, Any]],
        response_transport: dict[str, str] | None,
        completed_output: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        attempt = len(trace_attempts) + 1
        attempt_started_at = time.perf_counter()
        accepted_stream = False
        try:
            request = self._build_http_request(
                url=url,
                data=payload_bytes,
                accept='application/json, text/event-stream',
            )
            with self._open_http_request(request) as response:
                content_type = response.headers.get('Content-Type', '')
                accepted_stream = 'text/event-stream' in content_type.casefold()
                if response_transport is not None:
                    response_transport['actual_transport'] = 'sse' if accepted_stream else 'json'
                    response_transport['content_type'] = content_type
                if accepted_stream:
                    parsed = self._parse_native_tool_sse_response(
                        response, completed_output=completed_output,
                    )
                else:
                    body = self._read_response_body(response, content_type=content_type)
                    parsed = decode_native_json(body)
                    if not isinstance(parsed, dict):
                        raise _RetriableJSONPayloadError('Provider response was not a JSON object.')
                    failure = native_blocking_response_error(parsed, responses=True)
                    if failure is not None:
                        raise failure
                    if completed_output is not None:
                        output = parsed.get('output')
                        completed_output[:] = output if isinstance(output, list) else []
                trace_attempts.append({
                    'attempt': attempt,
                    'sampling_attempt': sampling_attempt,
                    'status': 'success',
                    'elapsed_ms': round((time.perf_counter() - attempt_started_at) * 1000, 2),
                    'content_type': content_type,
                    'stream_accepted': accepted_stream,
                    **self._response_transport_metadata(response),
                })
                return parsed
        except _HTTP_ERROR_TYPES as exc:
            detail = exc.read().decode('utf-8', errors='replace')
            classification = classify_provider_http_error(exc.code, detail)
            failure = provider_http_failure(classification=classification, detail=detail)
            trace_attempts.append({
                'attempt': attempt,
                'sampling_attempt': sampling_attempt,
                'status': 'http_error',
                'elapsed_ms': round((time.perf_counter() - attempt_started_at) * 1000, 2),
                'error_type': type(exc).__name__,
                'stream_accepted': accepted_stream,
                **classification.to_trace_metadata(),
            })
            if not classification.retryable or classification.retry_kind is None:
                raise failure from exc
            raise RetryableProviderOperation(
                owner=classification.retry_owner or 'inner_transport',
                kind=classification.retry_kind,
                cause=failure,
                trace_metadata=failure.to_trace_metadata(),
                retry_after_seconds=(
                    self._http_retry_after_seconds(exc)
                    or classification.retry_after_seconds
                ),
            ) from exc
        except (json.JSONDecodeError, _RetriableJSONPayloadError, ProviderSSEFailure) as exc:
            if not isinstance(exc, ProviderSSEFailure):
                exc = retryable_malformed_response(error_message=str(exc))
            metadata = exc.to_trace_metadata() if isinstance(exc, ProviderSSEFailure) else {}
            trace_attempts.append({
                'attempt': attempt,
                'sampling_attempt': sampling_attempt,
                'status': 'stream_terminal_error' if isinstance(exc, ProviderSSEFailure) and not exc.retryable else 'stream_retryable_error',
                'elapsed_ms': round((time.perf_counter() - attempt_started_at) * 1000, 2),
                'error_type': type(exc).__name__,
                'stream_accepted': accepted_stream,
                **metadata,
            })
            retryable = not isinstance(exc, ProviderSSEFailure) or exc.retryable
            if not retryable:
                raise exc
            failure = exc if isinstance(exc, ProviderSSEFailure) else RuntimeError(str(exc))
            owner: RetryOwner = (
                'outer_sampling'
                if accepted_stream or (
                    isinstance(exc, ProviderSSEFailure)
                    and exc.diagnostics.retry_owner == 'outer_sampling'
                )
                else 'inner_transport'
            )
            raise RetryableProviderOperation(
                owner=owner,
                kind='stream' if accepted_stream else 'request',
                cause=failure,
                trace_metadata=(
                    exc.to_trace_metadata()
                    if isinstance(exc, ProviderSSEFailure)
                    else {}
                ),
                retry_after_seconds=(
                    exc.retry_after_seconds
                    if isinstance(exc, ProviderSSEFailure)
                    else None
                ),
            ) from exc
        except Exception as exc:
            transport = self._classify_transport_error(exc)
            if not isinstance(exc, (urllib.error.URLError, OSError)) and not transport.get('retriable'):
                raise
            trace_attempts.append({
                **build_transport_trace_attempt(
                    attempt=attempt,
                    elapsed_ms=(time.perf_counter() - attempt_started_at) * 1000,
                    exc=exc,
                ),
                'sampling_attempt': sampling_attempt,
                'stream_accepted': accepted_stream,
            })
            failure = provider_transport_failure(
                exception=exc,
                phase=str(transport.get('phase') or 'unknown'),
                kind=str(transport.get('kind') or type(exc).__name__),
                detail=str(transport.get('reason_message') or exc),
                retryable=bool(transport.get('retriable')),
            )
            if not bool(transport.get('retriable')):
                raise failure from exc
            raise RetryableProviderOperation(
                owner='inner_transport',
                kind='stream' if accepted_stream else 'request',
                cause=failure,
                trace_metadata=failure.to_trace_metadata(),
            ) from exc

    def _parse_native_tool_sse_response(
        self, response: Any, *, completed_output: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        output_items: list[dict[str, Any]] = []
        response_error: ProviderSSEFailure | None = None
        tracker = SSEEventTracker()
        self._apply_sse_idle_timeout(response)
        event_iterator = iter(iter_native_sse_events(
            response, check_cancelled=self._raise_if_cancelled,
        ))
        while True:
            try:
                event_name, event_payload = next(event_iterator)
            except StopIteration:
                break
            except ProviderSSEFailure:
                raise
            except (urllib.error.URLError, OSError) as exc:
                raise tracker.failure(
                    failure_kind='stream_transport_error',
                    failure_class='transient_provider_failure',
                    retryable=True,
                    semantic_disposition='retryable_transport',
                    transport_exception=exc,
                ) from exc
            if not native_responses_event_decodable(event_payload):
                continue
            tracker.observe(event_name, event_payload)
            error = native_responses_error(event_name, event_payload, tracker=tracker)
            if error is not None:
                response_error = error
                continue
            if event_name == 'response.output_item.done':
                item = event_payload.get('item')
                if isinstance(item, dict):
                    output_items.append(dict(item))
            elif event_name == 'response.completed':
                response_payload = event_payload.get('response')
                if native_response_completion_decodable(response_payload):
                    if completed_output is not None:
                        completed_output[:] = output_items
                    return response_payload
        raise response_error or tracker.failure(
            failure_kind='premature_eof',
            failure_class='transient_provider_failure',
            retryable=True,
            semantic_disposition='retryable_transport',
        )

    def _post_request(
        self,
        prompt: str,
        *,
        response_mode: str,
        request_kind: str,
        schema: dict[str, Any] | None = None,
        schema_name: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        response_text_handler: Callable[[str], None] | None = None,
    ) -> dict[str, Any] | str:
        url = self._resolve_endpoint()
        parsed_url = urlparse(url)
        prompt_for_trace = _messages_trace_text(messages) if messages is not None else prompt
        payload = (
            self._build_payload_from_messages(
                messages,
                response_mode=response_mode,
                schema=schema,
                schema_name=schema_name,
            )
            if messages is not None
            else self._build_payload(
                prompt,
                response_mode=response_mode,
                schema=schema,
                schema_name=schema_name,
            )
        )
        encoded_payload = json.dumps(payload).encode("utf-8")

        total_attempts = self._total_attempts(
            'stream' if self._stream_responses else 'request'
        )
        last_error: Exception | None = None
        started_at = time.perf_counter()
        trace_attempts: list[dict[str, Any]] = []
        current_attempt = {'value': 0}
        outer_state = ProviderRetryBudgetState()
        inner_state = ProviderRetryBudgetState()
        sampling_attempt = 1
        terminal_retry_outcome: ProviderRetryOutcome | None = None

        def _retry_outcome_from_trace(
            *,
            failure: RetryableProviderOperation,
            requested_owner: RetryOwner,
        ) -> ProviderRetryOutcome:
            owner_chain: tuple[RetryOwner, ...] = (
                ('outer_sampling', 'inner_transport')
                if requested_owner == 'inner_transport'
                else (requested_owner,)
            )
            terminal_owner: RetryOwner = owner_chain[0]
            inner_request_retries = sum(
                1
                for item in trace_attempts
                if item.get('retry_owner') == 'inner_transport'
                and item.get('retry_kind') == 'request'
                and 'retry_delay_seconds' in item
            )
            inner_stream_retries = sum(
                1
                for item in trace_attempts
                if item.get('retry_owner') == 'inner_transport'
                and item.get('retry_kind') == 'stream'
                and 'retry_delay_seconds' in item
            )
            outer_sampling_retries = sum(
                1
                for item in trace_attempts
                if item.get('retry_owner') == 'outer_sampling'
                and 'retry_delay_seconds' in item
            )
            return ProviderRetryOutcome(
                outcome_kind='retry_exhausted',
                terminal_owner=terminal_owner,
                owner_chain=owner_chain,
                root_cause_kind=failure.kind,
                root_cause_type=type(failure.cause).__name__,
                root_cause_failure_kind=str(
                    failure.trace_metadata.get('failure_kind') or ''
                ),
                inner_request_retries=inner_request_retries,
                inner_stream_retries=inner_stream_retries,
                outer_sampling_retries=outer_sampling_retries,
                payload_sha256=str(
                    failure.trace_metadata.get('payload_sha256')
                    or failure.trace_metadata.get('http_error_body_sha256')
                    or ''
                )[:128],
            )

        def schedule_retry(
            *,
            owner: RetryOwner,
            kind: RetryKind,
            trace_attempt: dict[str, Any],
            failure: RetryableProviderOperation | None = None,
            retry_after_seconds: float | None = None,
        ) -> tuple[Any, RetryOwner] | None:
            nonlocal inner_state, outer_state, sampling_attempt, terminal_retry_outcome
            requested_owner = owner
            decision = self._retry_kernel.next_decision(
                state=inner_state if owner == 'inner_transport' else outer_state,
                owner=owner,
                kind=kind,
                retry_after_seconds=retry_after_seconds,
            )
            if decision is None and owner == 'inner_transport':
                decision = self._retry_kernel.next_decision(
                    state=outer_state,
                    owner='outer_sampling',
                    kind=kind,
                    retry_after_seconds=retry_after_seconds,
                )
                owner = 'outer_sampling'
            if decision is None:
                if failure is not None:
                    terminal_retry_outcome = _retry_outcome_from_trace(
                        failure=failure,
                        requested_owner=requested_owner,
                    )
                return None
            trace_attempt.update(decision.to_trace_metadata())
            record_provider_retry(decision, failure, trace_metadata=trace_attempt)
            self._retry_kernel.wait(decision)
            if owner == 'inner_transport':
                inner_state = decision.state
            else:
                outer_state = decision.state
                sampling_attempt += 1
                inner_state = ProviderRetryBudgetState()
            return decision, owner

        progress_lock = threading.RLock()
        progress_active = True
        heartbeat_count = 0

        def _stop_progress() -> None:
            nonlocal progress_active
            with progress_lock:
                progress_active = False
            heartbeat.stop()

        def _emit_terminal_trace(**kwargs: Any) -> None:
            _stop_progress()
            kwargs['extra_metadata'] = {
                **(kwargs.get('extra_metadata') or {}),
                'heartbeat_count': heartbeat_count,
            }
            self._emit_trace(**kwargs)

        def _emit_progress_event(event_type: str) -> None:
            nonlocal heartbeat_count
            with progress_lock:
                if not progress_active:
                    return
                if event_type == 'request_heartbeat':
                    heartbeat_count += 1
                _publish_progress_event(event_type)

        def _publish_progress_event(event_type: str) -> None:
            emit_llm_trace(
                {
                    'event_type': event_type,
                    'heartbeat_count': heartbeat_count,
                    'provider': self._provider,
                    'model': self._model,
                    'reasoning_effort': self._reasoning_effort,
                    'api_style': self._api_style,
                    'request_kind': request_kind,
                    'response_mode': response_mode,
                    'endpoint_host': parsed_url.netloc,
                    'endpoint_path': parsed_url.path,
                    'timeout_seconds': self._request_timeout_seconds,
                    'sse_idle_timeout_seconds': self._sse_idle_timeout_seconds,
                    'request_max_retries': self._retry_policy.request_max_retries,
                    'stream_max_retries': self._retry_policy.stream_max_retries,
                    'retry_config_source': self._retry_policy.config_source,
                    'total_attempts_configured': total_attempts,
                    'attempt_count': current_attempt['value'],
                    'retried': current_attempt['value'] > 1,
                    'success': False if event_type != 'request_completed' else None,
                    'elapsed_ms': round((time.perf_counter() - started_at) * 1000, 2),
                    'prompt_char_count': len(prompt_for_trace),
                    'prompt_line_count': len(prompt_for_trace.splitlines()),
                    'prompt_head': summarize_prompt(prompt_for_trace),
                    'input_image_block_count': _messages_input_image_count(messages or []),
                }
            )

        heartbeat = _RequestProgressEmitter(
            emit_callback=lambda: _emit_progress_event('request_heartbeat'),
            interval_seconds=min(15.0, max(5.0, self._sse_idle_timeout_seconds)),
        )
        _emit_progress_event('request_started')
        heartbeat.start()
        try:
            attempt = 0
            while True:
                attempt += 1
                attempt_started_at = time.perf_counter()
                current_attempt['value'] = attempt
                accepted_stream = False
                try:
                    request = self._build_http_request(
                        url=url,
                        data=encoded_payload,
                        accept=(
                            "text/event-stream"
                            if self._stream_responses
                            else "application/json"
                        ),
                    )
                    with self._open_http_request(request) as response:
                        content_type = response.headers.get("Content-Type", "")
                        accepted_stream = (
                            'text/event-stream' in content_type.casefold()
                        )
                        if 'text/event-stream' in content_type.casefold():
                            self._apply_sse_idle_timeout(response)
                        body = self._read_response_body(response, content_type=content_type)
                except _HTTP_ERROR_TYPES as exc:
                    detail = exc.read().decode("utf-8", errors="replace")
                    last_error = exc
                    classification = classify_provider_http_error(exc.code, detail)
                    trace_attempts.append(
                        {
                            'attempt': attempt,
                            'sampling_attempt': sampling_attempt,
                            'status': 'http_error',
                            'elapsed_ms': round((time.perf_counter() - attempt_started_at) * 1000, 2),
                            'error_type': type(exc).__name__,
                            **classification.to_trace_metadata(),
                        }
                    )
                    failure = provider_http_failure(
                        classification=classification,
                        detail=detail,
                    )
                    if classification.retryable and classification.retry_kind is not None:
                        retry_operation = RetryableProviderOperation(
                            owner=classification.retry_owner or 'inner_transport',
                            kind=classification.retry_kind,
                            cause=failure,
                            trace_metadata=failure.to_trace_metadata(),
                            retry_after_seconds=(
                                self._http_retry_after_seconds(exc)
                                or classification.retry_after_seconds
                            ),
                        )
                        next_retry = schedule_retry(
                            owner=retry_operation.owner,
                            kind=retry_operation.kind,
                            trace_attempt=trace_attempts[-1],
                            failure=retry_operation,
                            retry_after_seconds=retry_operation.retry_after_seconds,
                        )
                        if next_retry is not None:
                            continue
                    _emit_terminal_trace(
                        url=url,
                        prompt=prompt_for_trace,
                        response_mode=response_mode,
                        request_kind=request_kind,
                        success=False,
                        attempts=trace_attempts,
                        total_attempts=total_attempts,
                        error_type=type(exc).__name__,
                        error_message=f'HTTP {exc.code}: {detail[:400]}',
                        elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
                        extra_metadata=(
                            terminal_retry_outcome.to_trace_metadata()
                            if terminal_retry_outcome is not None
                            else None
                        ),
                    )
                    raise failure from exc
                except Exception as exc:
                    transport_details = self._classify_transport_error(exc)
                    if not bool(transport_details.get('retriable')):
                        trace_attempts.append(
                            build_transport_trace_attempt(
                                attempt=attempt,
                                elapsed_ms=(time.perf_counter() - attempt_started_at) * 1000,
                                exc=exc,
                            )
                        )
                        trace_attempts[-1]['stream_accepted'] = accepted_stream
                        _emit_terminal_trace(
                            url=url,
                            prompt=prompt_for_trace,
                            response_mode=response_mode,
                            request_kind=request_kind,
                            success=False,
                            attempts=trace_attempts,
                            total_attempts=total_attempts,
                            error_type=type(exc).__name__,
                            error_message=str(transport_details.get('reason_message') or exc)[:400],
                            elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
                        )
                        raise
                    last_error = exc
                    trace_attempts.append(
                        build_transport_trace_attempt(
                            attempt=attempt,
                            elapsed_ms=(time.perf_counter() - attempt_started_at) * 1000,
                            exc=exc,
                        )
                    )
                    trace_attempts[-1]['stream_accepted'] = accepted_stream
                    trace_attempts[-1]['sampling_attempt'] = sampling_attempt
                    retry_failure = provider_transport_failure(
                        exception=exc,
                        phase=str(transport_details.get('phase') or 'unknown'),
                        kind=str(transport_details.get('kind') or type(exc).__name__),
                        detail=str(transport_details.get('reason_message') or exc),
                        retryable=True,
                    )
                    retry_operation = RetryableProviderOperation(
                        owner=(
                            'outer_sampling'
                            if accepted_stream
                            else 'inner_transport'
                        ),
                        kind='stream' if accepted_stream else 'request',
                        cause=retry_failure,
                        trace_metadata=retry_failure.to_trace_metadata(),
                    )
                    next_retry = schedule_retry(
                        owner=retry_operation.owner,
                        kind=retry_operation.kind,
                        trace_attempt=trace_attempts[-1],
                        failure=retry_operation,
                    )
                    if next_retry is not None:
                        continue
                    _emit_terminal_trace(
                        url=url,
                        prompt=prompt_for_trace,
                        response_mode=response_mode,
                        request_kind=request_kind,
                        success=False,
                        attempts=trace_attempts,
                        total_attempts=total_attempts,
                        error_type=type(exc).__name__,
                        error_message=str(transport_details.get('reason_message') or exc)[:400],
                        elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
                        extra_metadata=(
                            terminal_retry_outcome.to_trace_metadata()
                            if terminal_retry_outcome is not None
                            else None
                        ),
                    )
                    raise RuntimeError(
                        self._transport_error_message(attempts=attempt, exc=exc)
                    ) from exc

                if "text/event-stream" in content_type.casefold():
                    mislabeled_json = self._json_object_from_mislabeled_event_stream(body)
                    try:
                        if mislabeled_json is not None:
                            if _expects_json_response(response_mode):
                                result = self._extract_response_json(mislabeled_json, body, schema)
                            else:
                                result = self._extract_response_text(mislabeled_json, body)
                            usage = _extract_provider_usage(mislabeled_json, api_style=self._api_style)
                        elif _expects_json_response(response_mode):
                            result = self._extract_sse_json(body, schema)
                            usage = _extract_sse_provider_usage(body, api_style=self._api_style)
                        else:
                            result = self._extract_sse_text(body)
                            usage = _extract_sse_provider_usage(body, api_style=self._api_style)
                        if response_text_handler is not None:
                            if not isinstance(result, str):
                                raise RuntimeError('Expected text response from LLM provider.')
                            result = self._handle_text_response(
                                result,
                                response_text_handler=response_text_handler,
                            )
                        trace_attempts.append(
                            {
                                'attempt': attempt,
                                'sampling_attempt': sampling_attempt,
                                'status': 'success',
                                'elapsed_ms': round((time.perf_counter() - attempt_started_at) * 1000, 2),
                                'content_type': content_type,
                                **self._response_transport_metadata(response),
                            }
                        )
                        _emit_terminal_trace(
                            url=url,
                            prompt=prompt_for_trace,
                            response_mode=response_mode,
                            request_kind=request_kind,
                            success=True,
                            attempts=trace_attempts,
                            total_attempts=total_attempts,
                            elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
                            usage=usage,
                        )
                        return result
                    except ProviderSSEFailure as exc:
                        last_error = exc
                        sse_metadata = exc.to_trace_metadata()
                        trace_attempts.append(
                            {
                                'attempt': attempt,
                                'sampling_attempt': sampling_attempt,
                                'status': (
                                    'sse_retryable_error' if exc.retryable else 'sse_terminal_error'
                                ),
                                'elapsed_ms': round((time.perf_counter() - attempt_started_at) * 1000, 2),
                                'error_type': type(exc).__name__,
                                'stream_accepted': True,
                                **sse_metadata,
                            }
                        )
                        if exc.retryable:
                            retry_operation = RetryableProviderOperation(
                                owner='outer_sampling',
                                kind='stream',
                                cause=exc,
                                trace_metadata=exc.to_trace_metadata(),
                                retry_after_seconds=exc.retry_after_seconds,
                            )
                            next_retry = schedule_retry(
                                owner=retry_operation.owner,
                                kind=retry_operation.kind,
                                trace_attempt=trace_attempts[-1],
                                failure=retry_operation,
                                retry_after_seconds=retry_operation.retry_after_seconds,
                            )
                            if next_retry is not None:
                                continue
                        _emit_terminal_trace(
                            url=url,
                            prompt=prompt_for_trace,
                            response_mode=response_mode,
                            request_kind=request_kind,
                            success=False,
                            attempts=trace_attempts,
                            total_attempts=total_attempts,
                            error_type=type(exc).__name__,
                            error_message=str(exc)[:400],
                            elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
                            extra_metadata={
                                **sse_metadata,
                                **(
                                    terminal_retry_outcome.to_trace_metadata()
                                    if terminal_retry_outcome is not None
                                    else {}
                                ),
                            },
                        )
                        raise

                try:
                    parsed = json.loads(body)
                    if _expects_json_response(response_mode):
                        result = self._extract_response_json(parsed, body, schema)
                    else:
                        result = self._extract_response_text(parsed, body)
                    usage = _extract_provider_usage(parsed, api_style=self._api_style)
                    if response_text_handler is not None:
                        if not isinstance(result, str):
                            raise RuntimeError('Expected text response from LLM provider.')
                        result = self._handle_text_response(
                            result,
                            response_text_handler=response_text_handler,
                        )
                    trace_attempts.append(
                        {
                            'attempt': attempt,
                            'sampling_attempt': sampling_attempt,
                            'status': 'success',
                            'elapsed_ms': round((time.perf_counter() - attempt_started_at) * 1000, 2),
                            'content_type': content_type,
                            **self._response_transport_metadata(response),
                        }
                    )
                    _emit_terminal_trace(
                        url=url,
                        prompt=prompt_for_trace,
                        response_mode=response_mode,
                        request_kind=request_kind,
                        success=True,
                        attempts=trace_attempts,
                        total_attempts=total_attempts,
                        elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
                        usage=usage,
                    )
                    return result
                except json.JSONDecodeError as exc:
                    last_error = exc
                    failure = retryable_malformed_response(error_message=str(exc))
                    trace_attempts.append(
                        {
                            'attempt': attempt,
                            'sampling_attempt': sampling_attempt,
                            'status': 'protocol_error',
                            **failure.to_trace_metadata(),
                            'elapsed_ms': round((time.perf_counter() - attempt_started_at) * 1000, 2),
                            'error_type': type(exc).__name__,
                        }
                    )
                    next_retry = schedule_retry(
                        owner='inner_transport',
                        kind='request',
                        trace_attempt=trace_attempts[-1],
                    )
                    if next_retry is not None:
                        continue
                    _emit_terminal_trace(
                        url=url,
                        prompt=prompt_for_trace,
                        response_mode=response_mode,
                        request_kind=request_kind,
                        success=False,
                        attempts=trace_attempts,
                        total_attempts=total_attempts,
                        error_type=type(exc).__name__,
                        error_message=str(exc)[:400],
                        elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
                    )
                    raise failure from exc
                except _RetriableJSONPayloadError as exc:
                    last_error = exc
                    failure = retryable_malformed_response(error_message=str(exc))
                    trace_attempts.append(
                        {
                            'attempt': attempt,
                            'sampling_attempt': sampling_attempt,
                            'status': 'protocol_error',
                            **failure.to_trace_metadata(),
                            'elapsed_ms': round((time.perf_counter() - attempt_started_at) * 1000, 2),
                            'error_type': type(exc).__name__,
                        }
                    )
                    next_retry = schedule_retry(
                        owner='inner_transport',
                        kind='request',
                        trace_attempt=trace_attempts[-1],
                    )
                    if next_retry is not None:
                        continue
                    _emit_terminal_trace(
                        url=url,
                        prompt=prompt_for_trace,
                        response_mode=response_mode,
                        request_kind=request_kind,
                        success=False,
                        attempts=trace_attempts,
                        total_attempts=total_attempts,
                        error_type=type(exc).__name__,
                        error_message=str(exc)[:400],
                        elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
                    )
                    raise failure from exc

            raise RuntimeError(f"LLM request failed unexpectedly: {last_error}")
        finally:
            _stop_progress()

    def generate_text(self, prompt: str) -> str:
        result = self._post_request(prompt, response_mode="text", request_kind="generate_text")
        if not isinstance(result, str):
            raise RuntimeError("Expected text response from LLM provider.")
        return result

    def generate_text_response(self, prompt: str, *, request_kind: str) -> str:
        result = self._post_request(prompt, response_mode="text", request_kind=request_kind)
        if not isinstance(result, str):
            raise RuntimeError("Expected text response from LLM provider.")
        return result

    def generate_text_response_with_handler(
        self,
        prompt: str,
        *,
        request_kind: str,
        response_text_handler: Callable[[str], None],
    ) -> str:
        result = self._post_request(
            prompt,
            response_mode='text',
            request_kind=request_kind,
            response_text_handler=response_text_handler,
        )
        if not isinstance(result, str):
            raise RuntimeError('Expected text response from LLM provider.')
        return result

    def generate_json(self, prompt: str) -> dict[str, Any]:
        result = self._post_request(prompt, response_mode="json", request_kind="generate_json")
        if not isinstance(result, dict):
            raise RuntimeError("Expected JSON response from LLM provider.")
        return result

    def generate_structured_json(
        self,
        prompt: str,
        *,
        schema: dict[str, Any],
        request_kind: str,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        result = self._post_request(
            prompt,
            response_mode="structured_json",
            request_kind=request_kind,
            schema=schema,
            schema_name=schema_name,
        )
        if not isinstance(result, dict):
            raise RuntimeError("Expected structured JSON response from LLM provider.")
        return result

    def generate_structured_json_from_messages(
        self,
        *,
        messages: list[dict[str, Any]],
        prompt_for_fallback: str,
        schema: dict[str, Any],
        request_kind: str,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        result = self._post_request(
            prompt_for_fallback,
            response_mode="structured_json",
            request_kind=request_kind,
            schema=schema,
            schema_name=schema_name,
            messages=messages,
        )
        if not isinstance(result, dict):
            raise RuntimeError("Expected structured JSON response from LLM provider.")
        return result

    def complete_with_native_tools(
        self,
        *,
        messages: list[dict[str, Any]],
        native_conversation_input: NativeHistoryProviderInput,
        tools: list[NativeToolSpec],
        request_kind: str = 'native_tool_turn',
        tool_choice: str = 'auto',
    ) -> ProviderOutputEnvelope:
        return aggregate_native_response_events(
            self.stream_with_native_tools(
                messages=messages,
                native_conversation_input=native_conversation_input,
                tools=tools,
                request_kind=request_kind,
                tool_choice=tool_choice,
            )
        )

    def stream_with_native_tools(
        self,
        *,
        messages: list[dict[str, Any]],
        native_conversation_input: NativeHistoryProviderInput,
        tools: list[NativeToolSpec],
        request_kind: str = 'native_tool_turn',
        tool_choice: str = 'auto',
    ) -> Iterator[CanonicalResponseEvent]:
        if self._api_style in {'openai_chat', 'openai_responses'} and self._stream_responses:
            yield from self._stream_openai_native_tools(
                messages=messages,
                native_conversation_input=native_conversation_input,
                tools=tools,
                request_kind=request_kind,
                tool_choice=tool_choice,
            )
            return
        response = self._complete_with_native_tools_blocking(
            messages=messages,
            native_conversation_input=native_conversation_input,
            tools=tools,
            request_kind=request_kind,
            tool_choice=tool_choice,
        )
        yield from blocking_native_response_events(
            response,
            request_kind=request_kind,
            provider_protocol=f'{self._api_style}_blocking',
        )

    def _complete_with_native_tools_blocking(
        self,
        *,
        messages: list[dict[str, Any]],
        native_conversation_input: NativeHistoryProviderInput,
        tools: list[NativeToolSpec],
        request_kind: str = 'native_tool_turn',
        tool_choice: str = 'auto',
    ) -> ProviderOutputEnvelope:
        if self._api_style not in {'openai_chat', 'openai_responses'}:
            raise NotImplementedError(
                'OpenAI-compatible native tool calling currently supports api_style=openai_chat '
                'and api_style=openai_responses only.'
            )
        request_web_search_mode = self._request_web_search_execution_mode(tools)
        if tool_choice not in {'auto', 'required', 'none'}:
            raise ValueError('native tool_choice must be auto, required or none')
        payload = self._native_tool_payload(
            messages=messages,
            tools=tools,
            native_conversation_input=native_conversation_input,
        )
        payload['tool_choice'] = tool_choice
        if tool_choice == 'none' and not tools:
            payload.pop('tools', None)
        if tool_choice == 'required':
            payload['parallel_tool_calls'] = False
        extra_trace_metadata = {
            'input_image_block_count': _messages_input_image_count(messages),
            'native_tool_count': len(tools),
            'native_tool_names': [tool.name for tool in tools],
            'supports_parallel_tool_calls': self._supports_parallel_tool_calls,
            'tool_choice': tool_choice,
            **hosted_web_search_trace_metadata(
                execution_mode=request_web_search_mode,
                options=self._hosted_web_search_options,
            ),
        }
        prompt_for_trace = _messages_trace_text(messages)
        response_transport: dict[str, str] = {}
        completed_output: list[dict[str, Any]] | None = None
        if self._api_style == 'openai_responses' and self._stream_responses:
            completed_output = []
            parsed = self._post_native_tool_stream_payload(
                payload=payload,
                request_kind=request_kind,
                prompt_for_trace=prompt_for_trace,
                extra_trace_metadata=extra_trace_metadata,
                transport_mode='sse',
                response_transport=response_transport,
                completed_output=completed_output,
            )
        else:
            try:
                parsed = self._post_raw_payload(
                    payload=payload,
                    request_kind=request_kind,
                    response_mode='native_tools',
                    prompt_for_trace=prompt_for_trace,
                    extra_trace_metadata=extra_trace_metadata,
                    response_transport=response_transport,
                )
            except RuntimeError as exc:
                if (
                    self._api_style != 'openai_responses'
                    or not _provider_error_is_bad_response_body(exc)
                ):
                    raise
                completed_output = []
                parsed = self._post_native_tool_stream_payload(
                    payload=payload,
                    request_kind=request_kind,
                    prompt_for_trace=prompt_for_trace,
                    extra_trace_metadata={**extra_trace_metadata, 'fallback_from_transport_mode': 'json'},
                    transport_mode='sse_fallback',
                    response_transport=response_transport,
                    completed_output=completed_output,
                )
        if self._api_style == 'openai_responses':
            completed_items = _parse_openai_responses_response_items(
                parsed if completed_output is None else {'output': completed_output}
            )
            return _provider_output_envelope(
                parsed=parsed,
                completed_items=completed_items,
                usage=_extract_provider_usage(parsed, api_style=self._api_style),
                provider_metadata={
                    'status': str(parsed.get('status', '') or ''),
                    'api_style': self._api_style,
                    'response_id': str(parsed.get('id', '') or ''),
                    'response_transport': (
                        response_transport.get('actual_transport')
                        or ('json' if not self._stream_responses else 'unknown')
                    ),
                    'response_content_type': response_transport.get('content_type', ''),
                    **hosted_web_search_trace_metadata(
                        execution_mode=request_web_search_mode,
                        options=self._hosted_web_search_options,
                    ),
                },
                provider_protocol=self._api_style,
            )
        message = _openai_chat_primary_message(parsed)
        adapter = OpenAINativeToolAdapter()
        completed_items = adapter.parse_response_items(message)
        finish_reason = _openai_finish_reason(parsed)
        return _provider_output_envelope(
            parsed=parsed,
            completed_items=completed_items,
            usage=_extract_provider_usage(parsed, api_style=self._api_style),
            provider_metadata={
                'finish_reason': _openai_finish_reason(parsed),
                'api_style': self._api_style,
                'response_transport': response_transport.get('actual_transport', ''),
                'response_content_type': response_transport.get('content_type', ''),
            },
            provider_protocol=self._api_style,
            stop_reason=finish_reason,
        )

    def _stream_openai_native_tools(
        self,
        *,
        messages: list[dict[str, Any]],
        native_conversation_input: NativeHistoryProviderInput,
        tools: list[NativeToolSpec],
        request_kind: str,
        tool_choice: str,
    ) -> Iterator[CanonicalResponseEvent]:
        if tool_choice not in {'auto', 'required', 'none'}:
            raise ValueError('native tool_choice must be auto, required or none')
        is_responses = self._api_style == 'openai_responses'
        request_web_search_mode = (
            self._request_web_search_execution_mode(tools)
            if is_responses
            else WebSearchExecutionMode.DISABLED
        )
        payload = self._native_tool_payload(
            messages=messages,
            tools=tools,
            native_conversation_input=native_conversation_input,
        )
        payload['tool_choice'] = tool_choice
        if tool_choice == 'none' and not tools:
            payload.pop('tools', None)
        payload['stream'] = True
        if not is_responses:
            payload['stream_options'] = {'include_usage': True}
        if tool_choice == 'required':
            payload['parallel_tool_calls'] = False
        payload_json = json.dumps(payload)
        payload_bytes = payload_json.encode('utf-8')
        url = self._resolve_endpoint()
        prompt_for_trace = _messages_trace_text(messages)
        trace_metadata = {
            'transport_mode': 'sse',
            'request_payload_char_count': len(payload_json),
            'input_image_block_count': _messages_input_image_count(messages),
            'native_tool_count': len(tools),
            'native_tool_names': [tool.name for tool in tools],
            'supports_parallel_tool_calls': self._supports_parallel_tool_calls,
            'tool_choice': tool_choice,
            **hosted_web_search_trace_metadata(
                execution_mode=request_web_search_mode,
                options=self._hosted_web_search_options,
            ),
        }
        total_attempts = self._total_attempts('stream')
        started_at = time.perf_counter()
        trace_attempts: list[dict[str, Any]] = []
        outer_state = ProviderRetryBudgetState()
        inner_state = ProviderRetryBudgetState()
        sampling_attempt = 1

        def schedule_retry(
            *,
            owner: RetryOwner,
            kind: RetryKind,
            trace_attempt: dict[str, Any],
            retry_after_seconds: float | None = None,
        ) -> tuple[Any, RetryOwner] | None:
            nonlocal inner_state, outer_state, sampling_attempt
            decision = self._retry_kernel.next_decision(
                state=inner_state if owner == 'inner_transport' else outer_state,
                owner=owner,
                kind=kind,
                retry_after_seconds=retry_after_seconds,
            )
            if decision is None and owner == 'inner_transport':
                decision = self._retry_kernel.next_decision(
                    state=outer_state,
                    owner='outer_sampling',
                    kind=kind,
                    retry_after_seconds=retry_after_seconds,
                )
                owner = 'outer_sampling'
            if decision is None:
                return None
            trace_attempt.update(decision.to_trace_metadata())
            record_provider_retry(decision, trace_metadata=trace_attempt)
            self._retry_kernel.wait(decision)
            if owner == 'inner_transport':
                inner_state = decision.state
            else:
                outer_state = decision.state
                sampling_attempt += 1
                inner_state = ProviderRetryBudgetState()
            return decision, owner
        attempt = 0
        while True:
            attempt += 1
            attempt_started_at = time.perf_counter()
            accepted_stream = False
            emitted_count = 0
            emitted_delta_chars = 0
            tracker: SSEEventTracker | None = None
            completion_event: CanonicalResponseEvent | None = None
            try:
                request = self._build_http_request(
                    url=url,
                    data=payload_bytes,
                    accept='application/json, text/event-stream',
                )
                with self._open_http_request(request) as response:
                    content_type = response.headers.get('Content-Type', '')
                    if 'text/event-stream' not in content_type.casefold():
                        body = self._read_response_body(response, content_type=content_type)
                        parsed = decode_native_json(body)
                        if not isinstance(parsed, dict):
                            raise _RetriableJSONPayloadError(
                                'Provider response was not a JSON object.'
                            )
                        failure = native_blocking_response_error(parsed, responses=is_responses)
                        if failure is not None:
                            raise failure
                        completed_items = (
                                _parse_openai_responses_response_items(parsed)
                                if is_responses
                                else self._openai_chat_response_items(parsed)
                            )
                        finish_reason = '' if is_responses else _openai_finish_reason(parsed)
                        completed = _provider_output_envelope(
                            parsed=parsed,
                            completed_items=completed_items,
                            usage=_extract_provider_usage(parsed, api_style=self._api_style),
                            provider_metadata={
                                'status': str(parsed.get('status') or ''),
                                'api_style': self._api_style,
                                'response_id': str(parsed.get('id') or ''),
                                'finish_reason': (
                                    '' if is_responses else _openai_finish_reason(parsed)
                                ),
                                'response_transport': 'json',
                                'response_content_type': content_type,
                                **hosted_web_search_trace_metadata(
                                    execution_mode=request_web_search_mode,
                                    options=self._hosted_web_search_options,
                                ),
                            },
                            provider_protocol=f'{self._api_style}_json',
                            stop_reason=finish_reason,
                        )
                        for event in blocking_native_response_events(
                            completed,
                            request_kind=request_kind,
                            provider_protocol=f'{self._api_style}_json',
                            attempt=attempt,
                        ):
                            if event.type_value == CanonicalResponseEventType.RESPONSE_COMPLETED.value:
                                completion_event = event
                            else:
                                yield event
                        usage = completed.usage
                    else:
                        accepted_stream = True
                        tracker = SSEEventTracker()
                        normalizer = (
                            OpenAIResponsesStreamNormalizer(
                                item_parser=_parse_openai_responses_response_items,
                                usage_parser=lambda value: _extract_provider_usage(
                                    value,
                                    api_style='openai_responses',
                                ),
                            )
                            if is_responses
                            else OpenAIChatStreamNormalizer(
                                usage_parser=lambda value: _extract_provider_usage(
                                    value,
                                    api_style='openai_chat',
                                )
                            )
                        )
                        usage: dict[str, int] = {}
                        response_error: ProviderSSEFailure | None = None
                        self._apply_sse_idle_timeout(response)
                        for event_name, event_payload in iter_native_sse_events(
                            response, check_cancelled=self._raise_if_cancelled,
                            done_marker=not is_responses,
                        ):
                            if is_responses and not native_responses_event_decodable(event_payload):
                                continue
                            tracker.observe(event_name, event_payload)
                            if is_responses:
                                error = native_responses_error(
                                    event_name, event_payload, tracker=tracker,
                                )
                                if error is not None:
                                    response_error = error
                                    continue
                                if event_name == 'response.completed' and event_payload.get('response') is None:
                                    continue
                                normalized_events = normalizer.feed(
                                    event_name,
                                    event_payload,
                                    attempt=attempt,
                                    request_kind=request_kind,
                                )
                            else:
                                error = event_payload.get('error')
                                if isinstance(error, dict):
                                    response_error = tracker.explicit_error_failure(error)
                                    continue
                                normalized_events = normalizer.feed(
                                    event_payload,
                                    attempt=attempt,
                                    request_kind=request_kind,
                                )
                            for event in normalized_events:
                                if event.type_value == CanonicalResponseEventType.USAGE_UPDATED.value:
                                    usage.update(event.usage)
                                if event.type_value == CanonicalResponseEventType.RESPONSE_COMPLETED.value:
                                    event = replace(
                                        event,
                                        provider_metadata={
                                            **dict(event.provider_metadata),
                                            'api_style': self._api_style,
                                            'response_transport': 'sse',
                                            'response_content_type': content_type,
                                            **hosted_web_search_trace_metadata(
                                                execution_mode=request_web_search_mode,
                                                options=self._hosted_web_search_options,
                                            ),
                                        },
                                    )
                                emitted_count += 1
                                emitted_delta_chars += len(event.delta)
                                if event.type_value == CanonicalResponseEventType.RESPONSE_COMPLETED.value:
                                    completion_event = event
                                else:
                                    yield event
                            if is_responses and normalizer.completed:
                                break
                        if is_responses:
                            if not normalizer.completed:
                                raise response_error or tracker.failure(
                                    failure_kind='premature_eof',
                                    failure_class='transient_provider_failure',
                                    retryable=True,
                                    semantic_disposition='retryable_transport',
                                )
                        else:
                            if not normalizer.terminal_observed:
                                raise response_error or tracker.failure(
                                    failure_kind='premature_eof',
                                    failure_class='transient_provider_failure',
                                    retryable=True,
                                    semantic_disposition='retryable_transport',
                                )
                            for event in normalizer.finish(
                                attempt=attempt,
                                request_kind=request_kind,
                            ):
                                if event.type_value == CanonicalResponseEventType.USAGE_UPDATED.value:
                                    usage.update(event.usage)
                                if event.type_value == CanonicalResponseEventType.RESPONSE_COMPLETED.value:
                                    event = replace(
                                        event,
                                        provider_metadata={
                                            **dict(event.provider_metadata),
                                            'api_style': self._api_style,
                                            'response_transport': 'sse',
                                            'response_content_type': content_type,
                                        },
                                    )
                                emitted_count += 1
                                emitted_delta_chars += len(event.delta)
                                if event.type_value == CanonicalResponseEventType.RESPONSE_COMPLETED.value:
                                    completion_event = event
                                else:
                                    yield event
                trace_attempts.append(
                    {
                        'attempt': attempt,
                        'sampling_attempt': sampling_attempt,
                        'status': 'success',
                        'elapsed_ms': round(
                            (time.perf_counter() - attempt_started_at) * 1000,
                            2,
                        ),
                        'content_type': content_type,
                        **self._response_transport_metadata(response),
                    }
                )
                self._emit_trace(
                    url=url,
                    prompt=prompt_for_trace,
                    response_mode='native_tools',
                    request_kind=request_kind,
                    success=True,
                    attempts=trace_attempts,
                    total_attempts=total_attempts,
                    elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
                    usage=usage,
                    extra_metadata=trace_metadata,
                )
                if completion_event is not None:
                    yield completion_event
                return
            except _HTTP_ERROR_TYPES as exc:
                detail = exc.read().decode('utf-8', errors='replace')
                classification = classify_provider_http_error(exc.code, detail)
                trace_attempts.append(
                    {
                        'attempt': attempt,
                        'sampling_attempt': sampling_attempt,
                        'status': 'http_error',
                        'elapsed_ms': round(
                            (time.perf_counter() - attempt_started_at) * 1000,
                            2,
                        ),
                        'error_type': type(exc).__name__,
                        **classification.to_trace_metadata(),
                    }
                )
                if classification.retryable and classification.retry_kind is not None:
                    retry_failure = provider_http_failure(
                        classification=classification,
                        detail=detail,
                    )
                    retry_operation = RetryableProviderOperation(
                        owner=classification.retry_owner or 'inner_transport',
                        kind=classification.retry_kind,
                        cause=retry_failure,
                        trace_metadata=retry_failure.to_trace_metadata(),
                        retry_after_seconds=(
                            self._http_retry_after_seconds(exc)
                            or classification.retry_after_seconds
                        ),
                    )
                    next_retry = schedule_retry(
                        owner=retry_operation.owner,
                        kind=retry_operation.kind,
                        trace_attempt=trace_attempts[-1],
                        retry_after_seconds=retry_operation.retry_after_seconds,
                    )
                    if next_retry is not None:
                        continue
                self._emit_native_stream_failure_trace(
                    url=url,
                    prompt=prompt_for_trace,
                    request_kind=request_kind,
                    attempts=trace_attempts,
                    total_attempts=total_attempts,
                    started_at=started_at,
                    exc=exc,
                    extra_metadata=trace_metadata,
                )
                raise provider_http_failure(
                    classification=classification,
                    detail=detail,
                ) from exc
            except (json.JSONDecodeError, _RetriableJSONPayloadError, ProviderSSEFailure) as exc:
                if not isinstance(exc, ProviderSSEFailure):
                    exc = retryable_malformed_response(error_message=str(exc))
                trace_attempts.append(
                    {
                        'attempt': attempt,
                        'sampling_attempt': sampling_attempt,
                        'status': 'stream_retryable_error',
                        'elapsed_ms': round(
                            (time.perf_counter() - attempt_started_at) * 1000,
                            2,
                        ),
                        'error_type': type(exc).__name__,
                        'stream_accepted': accepted_stream,
                        **(exc.to_trace_metadata() if isinstance(exc, ProviderSSEFailure) else {}),
                    }
                )
                retryable = not isinstance(exc, ProviderSSEFailure) or exc.retryable
                retry_kind: RetryKind = 'stream' if accepted_stream else 'request'
                if retryable:
                    next_retry = schedule_retry(
                        owner='outer_sampling' if accepted_stream else 'inner_transport',
                        kind=retry_kind,
                        trace_attempt=trace_attempts[-1],
                        retry_after_seconds=(
                            exc.retry_after_seconds
                            if isinstance(exc, ProviderSSEFailure)
                            else None
                        ),
                    )
                    if next_retry is not None:
                        if emitted_count:
                            yield self._attempt_discarded_event(
                                request_kind=request_kind,
                                attempt=attempt,
                                event_count=emitted_count,
                                delta_char_count=emitted_delta_chars,
                                provider_protocol=self._api_style,
                            )
                        continue
                self._emit_native_stream_failure_trace(
                    url=url,
                    prompt=prompt_for_trace,
                    request_kind=request_kind,
                    attempts=trace_attempts,
                    total_attempts=total_attempts,
                    started_at=started_at,
                    exc=exc,
                    extra_metadata=trace_metadata,
                )
                if isinstance(exc, ProviderSSEFailure):
                    raise exc
                raise RuntimeError(
                    f'LLM native tool streaming request failed after {attempt} attempt(s): '
                    f'{exc}. {self._timeout_hint()}'
                ) from exc
            except Exception as exc:
                if not isinstance(exc, (urllib.error.URLError, OSError)) and not classify_transport_error(exc).retriable:
                    raise
                if accepted_stream and tracker is not None:
                    stream_failure = tracker.failure(
                        failure_kind='stream_transport_error',
                        failure_class='transient_provider_failure',
                        retryable=True,
                        semantic_disposition='retryable_transport',
                        transport_exception=exc,
                    )
                    trace_attempts.append(
                        {
                            'attempt': attempt,
                            'sampling_attempt': sampling_attempt,
                            'status': 'stream_retryable_error',
                            'elapsed_ms': round(
                                (time.perf_counter() - attempt_started_at) * 1000,
                                2,
                            ),
                            'error_type': type(exc).__name__,
                            'stream_accepted': True,
                            **stream_failure.to_trace_metadata(),
                        }
                    )
                    next_retry = schedule_retry(
                        owner='outer_sampling',
                        kind='stream',
                        trace_attempt=trace_attempts[-1],
                    ) if stream_failure.retryable else None
                    if next_retry is not None:
                        if emitted_count:
                            yield self._attempt_discarded_event(
                                request_kind=request_kind,
                                attempt=attempt,
                                event_count=emitted_count,
                                delta_char_count=emitted_delta_chars,
                                provider_protocol=self._api_style,
                            )
                        continue
                    self._emit_native_stream_failure_trace(
                        url=url,
                        prompt=prompt_for_trace,
                        request_kind=request_kind,
                        attempts=trace_attempts,
                        total_attempts=total_attempts,
                        started_at=started_at,
                        exc=stream_failure,
                        extra_metadata=trace_metadata,
                    )
                    raise stream_failure from exc
                details = self._classify_transport_error(exc)
                trace_attempts.append(
                    build_transport_trace_attempt(
                        attempt=attempt,
                        elapsed_ms=(time.perf_counter() - attempt_started_at) * 1000,
                        exc=exc,
                    )
                )
                trace_attempts[-1]['stream_accepted'] = accepted_stream
                trace_attempts[-1]['sampling_attempt'] = sampling_attempt
                if bool(details.get('retriable')):
                    next_retry = schedule_retry(
                        owner=(
                            'outer_sampling'
                            if accepted_stream
                            else 'inner_transport'
                        ),
                        kind='stream' if accepted_stream else 'request',
                        trace_attempt=trace_attempts[-1],
                    )
                    if next_retry is not None:
                        if emitted_count:
                            yield self._attempt_discarded_event(
                                request_kind=request_kind,
                                attempt=attempt,
                                event_count=emitted_count,
                                delta_char_count=emitted_delta_chars,
                                provider_protocol=self._api_style,
                            )
                        continue
                self._emit_native_stream_failure_trace(
                    url=url,
                    prompt=prompt_for_trace,
                    request_kind=request_kind,
                    attempts=trace_attempts,
                    total_attempts=total_attempts,
                    started_at=started_at,
                    exc=exc,
                    extra_metadata=trace_metadata,
                )
                raise provider_transport_failure(
                    exception=exc,
                    phase=str(details.get('phase') or 'unknown'),
                    kind=str(details.get('kind') or type(exc).__name__),
                    detail=str(details.get('reason_message') or exc),
                    retryable=bool(details.get('retriable')),
                ) from exc

    @staticmethod
    def _attempt_discarded_event(
        *,
        request_kind: str,
        attempt: int,
        event_count: int,
        delta_char_count: int,
        provider_protocol: str = 'openai_responses',
    ) -> CanonicalResponseEvent:
        return CanonicalResponseEvent(
            event_type=CanonicalResponseEventType.ATTEMPT_DISCARDED,
            request_kind=request_kind,
            response_mode='native_tools',
            attempt=attempt,
            provider_protocol=provider_protocol,
            payload={
                'discarded_event_count': event_count,
                'discarded_delta_char_count': delta_char_count,
            },
        )

    def _emit_native_stream_failure_trace(
        self,
        *,
        url: str,
        prompt: str,
        request_kind: str,
        attempts: list[dict[str, Any]],
        total_attempts: int,
        started_at: float,
        exc: BaseException,
        extra_metadata: dict[str, Any],
    ) -> None:
        self._emit_trace(
            url=url,
            prompt=prompt,
            response_mode='native_tools',
            request_kind=request_kind,
            success=False,
            attempts=attempts,
            total_attempts=total_attempts,
            elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
            error_type=type(exc).__name__,
            error_message=str(exc)[:400],
            extra_metadata=extra_metadata,
        )

    @staticmethod
    def _openai_chat_response_items(
        parsed: dict[str, Any],
    ) -> tuple[ProviderOutputItem, ...]:
        message = _openai_chat_primary_message(parsed)
        return OpenAINativeToolAdapter().parse_response_items(message)

    def measure_native_tool_request(
        self,
        *,
        messages: list[dict[str, Any]],
        native_conversation_input: NativeHistoryProviderInput,
        tools: list[NativeToolSpec],
    ) -> Any:
        from ownpi.runtime.native_tools.provider_request_measurement import (
            measure_native_provider_request,
            with_model_visible_request_tokens,
            with_serialized_request_bytes,
        )

        request_web_search_mode = self._request_web_search_execution_mode(tools)
        payload = self._native_tool_payload(
            messages=messages,
            tools=tools,
            native_conversation_input=native_conversation_input,
        )
        measurement = measure_native_provider_request(
            conversation_input=native_conversation_input,
            tool_specs=tools,
            provider_id=self._provider,
            model_id=self._model,
            request_static_properties={
                'api_style': self._api_style,
                'native_tool_strict': self._native_tool_strict,
                'reasoning_effort': self._reasoning_effort or '',
                'parallel_tool_calls': self._supports_parallel_tool_calls,
                'stream_responses': self._stream_responses,
                'tool_choice': 'auto',
                **({key: payload[key] for key in ('store', 'include')}
                   if self._api_style == 'openai_responses' else {}),
                **hosted_web_search_trace_metadata(
                    execution_mode=request_web_search_mode,
                    options=self._hosted_web_search_options,
                ),
            },
        )
        def project(value: NativeHistoryProviderInput) -> list[dict[str, Any]]:
            if self._api_style == 'openai_chat':
                return openai_chat_history_messages(value, prefix_messages=[
                    _openai_chat_message_item(item) for item in value.immutable_prefix_messages
                ])
            return openai_responses_history_input(
                value, native_tool_strict=self._native_tool_strict,
                prefix_messages=[_openai_responses_message_item(item) for item in value.immutable_prefix_messages],
            )
        measurement = with_model_visible_request_tokens(
            measurement, conversation_input=native_conversation_input,
            serialized_request=payload, project=project, modalities=self._input_modalities,
        )
        return with_serialized_request_bytes(measurement, serialized_request=payload)

    @property
    def supports_native_tool_calling(self) -> bool:
        return self._api_style in {'openai_chat', 'openai_responses'}

    @property
    def supports_image_input(self) -> bool:
        return 'image' in self._input_modalities

    @property
    def native_tool_provider_capabilities(self) -> NativeToolProviderCapabilities:
        return NativeToolProviderCapabilities(
            native_tool_strict=self._native_tool_strict,
            native_tool_search_output=_supports_native_tool_search_output(
                api_style=self._api_style,
                provider=self._provider,
                model=self._model,
                explicit=self._native_tool_search_output_override,
            ),
            supports_parallel_tool_calls=self._supports_parallel_tool_calls,
            web_search_execution_mode=self._web_search_execution_mode,
            hosted_web_search_spec_digest=(
                self._hosted_web_search_options.digest
                if self._web_search_execution_mode == WebSearchExecutionMode.HOSTED
                else ''
            ),
        )

    def _native_tool_payload(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[NativeToolSpec],
        native_conversation_input: NativeHistoryProviderInput,
    ) -> dict[str, Any]:
        adapter = OpenAINativeToolAdapter()
        native_conversation_input = native_conversation_input.for_modalities(self._input_modalities)
        if self._api_style == 'openai_chat':
            payload = {
                'model': self._model,
                'temperature': 0.1,
                'messages': openai_chat_history_messages(
                    native_conversation_input,
                    prefix_messages=[
                        _openai_chat_message_item(item)
                        for item in native_conversation_input.immutable_prefix_messages
                    ],
                ),
                'tools': adapter.build_provider_tools(tools, strict=self._native_tool_strict),
                'tool_choice': 'auto',
            }
            if self._reasoning_effort:
                payload['reasoning_effort'] = self._reasoning_effort
            if self._supports_parallel_tool_calls:
                payload['parallel_tool_calls'] = True
            return payload
        if self._api_style == 'openai_responses':
            supports_search_output = (
                self.native_tool_provider_capabilities.native_tool_search_output
            )
            payload = {
                'model': self._model,
                'input': openai_responses_history_input(
                    native_conversation_input,
                    native_tool_strict=self._native_tool_strict,
                    prefix_messages=[
                        _openai_responses_message_item(item)
                        for item in native_conversation_input.immutable_prefix_messages
                    ],
                ),
                'tools': _openai_responses_native_tools(
                    tools,
                    strict=self._native_tool_strict,
                    native_tool_search_output=supports_search_output,
                    hosted_web_search_spec=(
                        self._hosted_web_search_options.build_spec()
                        if self._request_web_search_execution_mode(tools)
                        == WebSearchExecutionMode.HOSTED
                        else None
                    ),
                ),
                'tool_choice': 'auto',
            }
            if self._reasoning_effort:
                payload['reasoning'] = {'effort': self._reasoning_effort}
            if self._supports_parallel_tool_calls:
                payload['parallel_tool_calls'] = True
            return self._finalize_responses_payload(payload)
        raise ValueError(f'Unsupported api_style for native tools: {self._api_style!r}')

    def _request_web_search_execution_mode(
        self,
        tools: list[NativeToolSpec],
    ) -> WebSearchExecutionMode:
        if not any(spec.name == 'web_search' for spec in tools):
            return WebSearchExecutionMode.DISABLED
        if not all(
            spec.hosted_execution_allowed
            for spec in tools
            if spec.name == 'web_search'
        ):
            return WebSearchExecutionMode.LOCAL_FALLBACK
        return self._web_search_execution_mode

    def stream_structured_json(
        self,
        prompt: str,
        *,
        schema: dict[str, Any],
        request_kind: str,
        schema_name: str | None = None,
    ) -> Iterator[LLMStreamEvent]:
        if not self._stream_responses:
            yield LLMStreamEvent(
                event_type='request_started',
                request_kind=request_kind,
                response_mode='structured_json',
                payload={
                    'provider': self._provider,
                    'model': self._model,
                },
            )
            payload = self.generate_structured_json(
                prompt,
                schema=schema,
                request_kind=request_kind,
                schema_name=schema_name,
            )
            yield LLMStreamEvent(
                event_type='completed',
                request_kind=request_kind,
                response_mode='structured_json',
                payload={'json_payload': payload},
            )
            return
        yield from self._stream_request(
            prompt,
            response_mode='structured_json',
            request_kind=request_kind,
            schema=schema,
            schema_name=schema_name,
        )

    def _stream_request(
        self,
        prompt: str,
        *,
        response_mode: str,
        request_kind: str,
        schema: dict[str, Any] | None = None,
        schema_name: str | None = None,
    ) -> Iterator[LLMStreamEvent]:
        url = self._resolve_endpoint()
        payload = self._build_payload(
            prompt,
            response_mode=response_mode,
            schema=schema,
            schema_name=schema_name,
        )
        payload["stream"] = True
        if self._api_style == "openai_chat":
            stream_options = payload.get("stream_options")
            if not isinstance(stream_options, dict):
                stream_options = {}
            stream_options["include_usage"] = True
            payload["stream_options"] = stream_options
        parsed_url = urlparse(url)
        payload_bytes = json.dumps(payload).encode("utf-8")
        total_attempts = self._total_attempts('stream')
        started_at = time.perf_counter()
        trace_attempts: list[dict[str, Any]] = []
        outer_state = ProviderRetryBudgetState()
        inner_state = ProviderRetryBudgetState()
        sampling_attempt = 1

        def schedule_retry(
            *,
            owner: RetryOwner,
            kind: RetryKind,
            trace_attempt: dict[str, Any],
            retry_after_seconds: float | None = None,
        ) -> tuple[Any, RetryOwner] | None:
            nonlocal inner_state, outer_state, sampling_attempt
            decision = self._retry_kernel.next_decision(
                state=inner_state if owner == 'inner_transport' else outer_state,
                owner=owner,
                kind=kind,
                retry_after_seconds=retry_after_seconds,
            )
            if decision is None and owner == 'inner_transport':
                decision = self._retry_kernel.next_decision(
                    state=outer_state,
                    owner='outer_sampling',
                    kind=kind,
                    retry_after_seconds=retry_after_seconds,
                )
                owner = 'outer_sampling'
            if decision is None:
                return None
            trace_attempt.update(decision.to_trace_metadata())
            record_provider_retry(decision, trace_metadata=trace_attempt)
            self._retry_kernel.wait(decision)
            if owner == 'inner_transport':
                inner_state = decision.state
            else:
                outer_state = decision.state
                sampling_attempt += 1
                inner_state = ProviderRetryBudgetState()
            return decision, owner
        yield LLMStreamEvent(
            event_type='request_started',
            request_kind=request_kind,
            response_mode=response_mode,
            payload={
                'provider': self._provider,
                'model': self._model,
                'endpoint_host': parsed_url.netloc,
                'endpoint_path': parsed_url.path,
            },
        )
        attempt = 0
        while True:
            attempt += 1
            attempt_started_at = time.perf_counter()
            text_chunks: list[str] = []
            streamed_delta_count = 0
            streamed_delta_char_count = 0
            usage: dict[str, int] = {}
            tracker = SSEEventTracker()
            accepted_stream = False
            try:
                request = self._build_http_request(
                    url=url,
                    data=payload_bytes,
                    accept="application/json, text/event-stream",
                )
                with self._open_http_request(request) as response:
                    content_type = response.headers.get("Content-Type", "")
                    if 'text/event-stream' not in content_type.casefold():
                        body = self._read_response_body(response, content_type=content_type)
                        parsed = json.loads(body)
                        if _expects_json_response(response_mode):
                            json_payload = self._extract_response_json(parsed, body, schema)
                        else:
                            text_payload = self._extract_response_text(parsed, body)
                            json_payload = {'text': text_payload}
                        usage = _extract_provider_usage(parsed, api_style=self._api_style)
                        trace_attempts.append(
                            {
                                'attempt': attempt,
                                'sampling_attempt': sampling_attempt,
                                'status': 'success',
                                'elapsed_ms': round((time.perf_counter() - attempt_started_at) * 1000, 2),
                                'content_type': content_type,
                                **self._response_transport_metadata(response),
                            }
                        )
                        self._emit_trace(
                            url=url,
                            prompt=prompt,
                            response_mode=response_mode,
                            request_kind=request_kind,
                            success=True,
                            attempts=trace_attempts,
                            total_attempts=total_attempts,
                            elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
                            usage=usage,
                        )
                        yield LLMStreamEvent(
                            event_type='completed',
                            request_kind=request_kind,
                            response_mode=response_mode,
                            payload={'json_payload': json_payload},
                            usage=usage,
                            attempt=attempt,
                        )
                        return
                    accepted_stream = True
                    for event_name, event_payload in self._iter_sse_events(response):
                        tracker.observe(event_name, event_payload)
                        if event_name == 'response.output_item.done':
                            item = event_payload.get('item')
                            if isinstance(item, dict) and str(item.get('type') or '') == 'function_call':
                                raise protocol_sse_failure(
                                    'native_tool_call_in_structured_mode',
                                    error_message=(
                                        'Provider returned native tool-call payload under structured JSON mode; '
                                        'canonical tool protocol requires schema-valid JSON only.'
                                    ),
                                )
                        elif event_name == 'response.output_text.delta':
                            delta = event_payload.get('delta')
                            if isinstance(delta, str) and delta:
                                text_chunks.append(delta)
                                streamed_delta_count += 1
                                streamed_delta_char_count += len(delta)
                                yield LLMStreamEvent(
                                    event_type='json_delta' if _expects_json_response(response_mode) else 'text_delta',
                                    request_kind=request_kind,
                                    response_mode=response_mode,
                                    delta=delta,
                                    attempt=attempt,
                                )
                        elif event_name == 'chat.completion.chunk':
                            delta = _extract_chat_completion_stream_delta(event_payload)
                            if delta:
                                text_chunks.append(delta)
                                streamed_delta_count += 1
                                streamed_delta_char_count += len(delta)
                                yield LLMStreamEvent(
                                    event_type='json_delta' if _expects_json_response(response_mode) else 'text_delta',
                                    request_kind=request_kind,
                                    response_mode=response_mode,
                                    delta=delta,
                                    attempt=attempt,
                                )
                            chunk_usage = _extract_provider_usage(event_payload, api_style=self._api_style)
                            if chunk_usage:
                                usage = chunk_usage
                        elif event_name == 'response.output_text.done':
                            done_text = event_payload.get('text')
                            if isinstance(done_text, str) and done_text:
                                text_chunks = [done_text]
                        elif event_name == 'response.completed':
                            response_payload = event_payload.get('response')
                            if isinstance(response_payload, dict):
                                usage = _extract_provider_usage(response_payload, api_style=self._api_style)
                                output_text = _extract_openai_responses_message_text(response_payload.get('output'))
                                if output_text and not text_chunks:
                                    text_chunks = [output_text]
                        elif event_name == 'response.incomplete':
                            raise tracker.incomplete_failure(event_payload)
                        elif event_name == 'error':
                            error_payload = event_payload.get('error')
                            if isinstance(error_payload, dict):
                                raise tracker.explicit_error_failure(error_payload)
                        elif event_name == 'response.failed':
                            response_error = event_payload.get('response', {}).get('error')
                            if isinstance(response_error, dict):
                                raise tracker.explicit_error_failure(response_error)
                            raise tracker.failure(
                                failure_kind='response_failed_without_error',
                                failure_class='transient_provider_failure',
                                retryable=True,
                                semantic_disposition='retryable_transport',
                            )
                raw_text = ''.join(text_chunks).strip()
                if self._api_style == 'openai_responses':
                    tracker.finalize(has_output=bool(raw_text))
                elif not raw_text:
                    raise protocol_sse_failure('terminal_without_output')
                if _expects_json_response(response_mode):
                    try:
                        json_payload = _drop_structured_null_optionals(
                            json.loads(raw_text),
                            schema,
                        )
                    except json.JSONDecodeError as exc:
                        raise protocol_sse_failure(
                            'malformed_structured_output',
                            error_message=str(exc),
                        ) from exc
                else:
                    json_payload = {'text': raw_text}
                trace_attempts.append(
                    {
                        'attempt': attempt,
                        'sampling_attempt': sampling_attempt,
                        'status': 'success',
                        'elapsed_ms': round((time.perf_counter() - attempt_started_at) * 1000, 2),
                        'content_type': 'text/event-stream',
                        **self._response_transport_metadata(response),
                    }
                )
                self._emit_trace(
                    url=url,
                    prompt=prompt,
                    response_mode=response_mode,
                    request_kind=request_kind,
                    success=True,
                    attempts=trace_attempts,
                    total_attempts=total_attempts,
                    elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
                    usage=usage,
                )
                yield LLMStreamEvent(
                    event_type='completed',
                    request_kind=request_kind,
                    response_mode=response_mode,
                    payload={'json_payload': json_payload},
                    usage=usage,
                    attempt=attempt,
                )
                return
            except _HTTP_ERROR_TYPES as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                classification = classify_provider_http_error(exc.code, detail)
                trace_attempts.append(
                    {
                        'attempt': attempt,
                        'sampling_attempt': sampling_attempt,
                        'status': 'http_error',
                        'elapsed_ms': round((time.perf_counter() - attempt_started_at) * 1000, 2),
                        'error_type': type(exc).__name__,
                        **classification.to_trace_metadata(),
                    }
                )
                next_retry = None
                if classification.retryable and classification.retry_kind is not None:
                    next_retry = schedule_retry(
                        owner=classification.retry_owner or 'inner_transport',
                        kind=classification.retry_kind,
                        trace_attempt=trace_attempts[-1],
                        retry_after_seconds=(
                            self._http_retry_after_seconds(exc)
                            or classification.retry_after_seconds
                        ),
                    )
                if next_retry is not None:
                    _decision, _owner = next_retry
                    retry_metadata = self._retry_trace_metadata(trace_attempts[-1])
                    yield LLMStreamEvent(
                        event_type='attempt_discarded',
                        request_kind=request_kind,
                        response_mode=response_mode,
                        payload={
                            'error_type': type(exc).__name__,
                            'http_status': exc.code,
                            'message': detail[:400],
                            'discarded_delta_count': streamed_delta_count,
                            'discarded_delta_char_count': streamed_delta_char_count,
                            **retry_metadata,
                        },
                        attempt=attempt,
                    )
                    continue
                self._emit_trace(
                    url=url,
                    prompt=prompt,
                    response_mode=response_mode,
                    request_kind=request_kind,
                    success=False,
                    attempts=trace_attempts,
                    total_attempts=total_attempts,
                    error_type=type(exc).__name__,
                    error_message=f'HTTP {exc.code}: {detail[:400]}',
                    elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
                )
                raise provider_http_failure(
                    classification=classification,
                    detail=detail,
                ) from exc
            except (json.JSONDecodeError, ProviderSSEFailure) as exc:
                if not isinstance(exc, ProviderSSEFailure):
                    exc = retryable_malformed_response(error_message=str(exc))
                sse_metadata = exc.to_trace_metadata() if isinstance(exc, ProviderSSEFailure) else {}
                trace_attempts.append(
                    {
                        'attempt': attempt,
                        'status': (
                            'stream_retryable_error'
                            if not isinstance(exc, ProviderSSEFailure) or exc.retryable
                            else 'stream_terminal_error'
                        ),
                        'elapsed_ms': round((time.perf_counter() - attempt_started_at) * 1000, 2),
                        'error_type': type(exc).__name__,
                        'stream_accepted': accepted_stream,
                        **sse_metadata,
                    }
                )
                next_retry = None
                if not isinstance(exc, ProviderSSEFailure) or exc.retryable:
                    retry_operation = RetryableProviderOperation(
                        owner=(
                            'outer_sampling'
                            if accepted_stream
                            else 'inner_transport'
                        ),
                        kind='stream' if accepted_stream else 'request',
                        cause=exc,
                        trace_metadata=(
                            exc.to_trace_metadata()
                            if isinstance(exc, ProviderSSEFailure)
                            else {}
                        ),
                        retry_after_seconds=(
                            exc.retry_after_seconds
                            if isinstance(exc, ProviderSSEFailure)
                            else None
                        ),
                    )
                    next_retry = schedule_retry(
                        owner=retry_operation.owner,
                        kind=retry_operation.kind,
                        trace_attempt=trace_attempts[-1],
                        retry_after_seconds=retry_operation.retry_after_seconds,
                    )
                if next_retry is not None:
                    retry_metadata = self._retry_trace_metadata(trace_attempts[-1])
                    yield LLMStreamEvent(
                        event_type='attempt_discarded',
                        request_kind=request_kind,
                        response_mode=response_mode,
                        payload={
                            'error_type': type(exc).__name__,
                            'message': str(exc)[:400],
                            'discarded_delta_count': streamed_delta_count,
                            'discarded_delta_char_count': streamed_delta_char_count,
                            **sse_metadata,
                            **retry_metadata,
                        },
                        attempt=attempt,
                    )
                    continue
                self._emit_trace(
                    url=url,
                    prompt=prompt,
                    response_mode=response_mode,
                    request_kind=request_kind,
                    success=False,
                    attempts=trace_attempts,
                    total_attempts=total_attempts,
                    error_type=type(exc).__name__,
                    error_message=str(exc)[:400],
                    elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
                    extra_metadata=sse_metadata,
                )
                if isinstance(exc, ProviderSSEFailure):
                    raise
                raise RuntimeError(
                    f"LLM streaming request failed after {attempt} attempt(s): {exc}. {self._timeout_hint()}"
                ) from exc
            except Exception as exc:
                transport_details = self._classify_transport_error(exc)
                trace_attempts.append(
                    build_transport_trace_attempt(
                        attempt=attempt,
                        elapsed_ms=(time.perf_counter() - attempt_started_at) * 1000,
                        exc=exc,
                    )
                )
                trace_attempts[-1]['stream_accepted'] = accepted_stream
                next_retry = None
                if bool(transport_details.get('retriable')):
                    retry_failure = provider_transport_failure(
                        exception=exc,
                        phase=str(transport_details.get('phase') or 'unknown'),
                        kind=str(transport_details.get('kind') or type(exc).__name__),
                        detail=str(transport_details.get('reason_message') or exc),
                        retryable=True,
                    )
                    retry_operation = RetryableProviderOperation(
                        owner=(
                            'outer_sampling'
                            if accepted_stream
                            else 'inner_transport'
                        ),
                        kind='stream' if accepted_stream else 'request',
                        cause=retry_failure,
                        trace_metadata=retry_failure.to_trace_metadata(),
                    )
                    next_retry = schedule_retry(
                        owner=retry_operation.owner,
                        kind=retry_operation.kind,
                        trace_attempt=trace_attempts[-1],
                    )
                if next_retry is not None:
                    retry_metadata = self._retry_trace_metadata(trace_attempts[-1])
                    yield LLMStreamEvent(
                        event_type='attempt_discarded',
                        request_kind=request_kind,
                        response_mode=response_mode,
                        payload={
                            'error_type': type(exc).__name__,
                            'message': str(exc)[:400],
                            'discarded_delta_count': streamed_delta_count,
                            'discarded_delta_char_count': streamed_delta_char_count,
                            **retry_metadata,
                        },
                        attempt=attempt,
                    )
                    continue
                self._emit_trace(
                    url=url,
                    prompt=prompt,
                    response_mode=response_mode,
                    request_kind=request_kind,
                    success=False,
                    attempts=trace_attempts,
                    total_attempts=total_attempts,
                    error_type=type(exc).__name__,
                    error_message=str(transport_details.get('reason_message') or exc)[:400],
                    elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
                )
                raise


    def _emit_trace(
        self,
        *,
        url: str,
        prompt: str,
        response_mode: str,
        request_kind: str,
        success: bool,
        attempts: list[dict[str, Any]],
        total_attempts: int,
        elapsed_ms: float,
        error_type: str | None = None,
        error_message: str | None = None,
        usage: dict[str, int] | None = None,
        extra_metadata: dict[str, Any] | None = None,
    ) -> None:
        parsed_url = urlparse(url)
        usage_payload = dict(usage or {})
        attempts = [sanitize_error_metadata(item) for item in attempts]
        error_message = diagnostic_message(error_message) if error_message is not None else None
        extra_metadata = sanitize_error_metadata(extra_metadata or {})
        retry_attempt_count = sum(
            1 for attempt in attempts if 'retry_delay_seconds' in attempt
        )
        inner_request_retry_count = sum(
            1
            for attempt in attempts
            if 'retry_delay_seconds' in attempt
            and attempt.get('retry_owner') == 'inner_transport'
            and attempt.get('retry_kind') == 'request'
        )
        inner_stream_retry_count = sum(
            1
            for attempt in attempts
            if 'retry_delay_seconds' in attempt
            and attempt.get('retry_owner') == 'inner_transport'
            and attempt.get('retry_kind') == 'stream'
        )
        outer_sampling_retry_count = sum(
            1
            for attempt in attempts
            if 'retry_delay_seconds' in attempt
            and attempt.get('retry_owner') == 'outer_sampling'
        )
        stream_attempt_count = sum(
            1
            for attempt in attempts
            if str(attempt.get('content_type') or '').casefold().startswith(
                'text/event-stream'
            )
            or bool(attempt.get('stream_accepted'))
            or str(attempt.get('status') or '').startswith(('sse_', 'stream_'))
        )
        final_status = str(attempts[-1].get('status') or '') if attempts else ''
        final_retryable = bool(attempts[-1].get('retryable')) if attempts else False
        retry_exhausted = (
            not success
            and (
                final_retryable
                or final_status == 'json_retryable_error'
            )
        )
        deterministic_stop = not success and not retry_exhausted
        emit_llm_trace(
            {
                'event_type': 'request_completed',
                'provider': self._provider,
                'model': self._model,
                'reasoning_effort': self._reasoning_effort,
                'api_style': self._api_style,
                'request_kind': request_kind,
                'response_mode': response_mode,
                'endpoint_host': parsed_url.netloc,
                'endpoint_path': parsed_url.path,
                'timeout_seconds': self._request_timeout_seconds,
                'sse_idle_timeout_seconds': self._sse_idle_timeout_seconds,
                'request_max_retries': self._retry_policy.request_max_retries,
                'stream_max_retries': self._retry_policy.stream_max_retries,
                'retry_config_source': self._retry_policy.config_source,
                'total_attempts_configured': total_attempts,
                'attempt_count': len(attempts),
                'request_attempt_count': len(attempts),
                'stream_attempt_count': stream_attempt_count,
                'retried': retry_attempt_count > 0,
                'retry_recovery_count': int(success and retry_attempt_count > 0),
                'request_retry_count': inner_request_retry_count,
                'inner_request_retry_count': inner_request_retry_count,
                'inner_stream_retry_count': inner_stream_retry_count,
                'outer_sampling_retry_count': outer_sampling_retry_count,
                'same_turn_sampling_retry_count': outer_sampling_retry_count,
                'same_turn_sampling_recovery_count': int(
                    success and outer_sampling_retry_count > 0
                ),
                'retry_exhaustion_count': int(retry_exhausted),
                'deterministic_stop_count': int(deterministic_stop),
                'success': success,
                'elapsed_ms': elapsed_ms,
                'prompt_char_count': len(prompt),
                'prompt_line_count': len(prompt.splitlines()),
                'prompt_head': summarize_prompt(prompt),
                **dict(extra_metadata or {}),
                'attempts': attempts,
                'usage': usage_payload,
                **usage_payload,
                'error_type': error_type,
                'error_message': error_message,
            }
        )


def _build_openai_compatible_client(config: LLMConfig) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(config=config)


register_llm_provider("openai", _build_openai_compatible_client)
register_llm_provider("openai_compatible", _build_openai_compatible_client)
