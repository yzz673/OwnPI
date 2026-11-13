from __future__ import annotations

import json
import logging
from copy import deepcopy
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

from ownpi.llm.streaming import CanonicalResponseEvent, CanonicalResponseEventType
from ownpi.llm.sse_failure import decode_native_json
from ownpi.runtime.native_tools.adapters import AnthropicNativeToolAdapter
from ownpi.runtime.native_tools.provider import ProviderOutputItemKind


class AnthropicMessagesStreamNormalizer:
    def __init__(self) -> None:
        self._response_id = ''
        self._started = False
        self._completed = False
        self._blocks: dict[int, dict[str, Any]] = {}
        self._item_ids: dict[int, str] = {}
        self._sequences: dict[int, int] = {}
        self._next_sequence = 0
        self._usage: dict[str, int] = {}
        self._raw_usage: dict[str, Any] = {}
        self._metadata: dict[str, Any] = {}
        self._extension_events: list[dict[str, Any]] = []
        self._stop_reason = ''

    @property
    def completed(self) -> bool:
        return self._completed

    def feed(
        self,
        event_name: str,
        payload: dict[str, Any],
        *,
        attempt: int,
        request_kind: str,
    ) -> Iterator[CanonicalResponseEvent]:
        try:
            yield from self._feed(event_name, payload, attempt=attempt, request_kind=request_kind)
        except Exception as error:
            from ownpi.runtime.native_tools.provider_json import FrozenProviderJson

            setattr(error, 'provider_output_source', FrozenProviderJson.from_value({'event': event_name, 'payload': payload}))
            raise

    def _feed(
        self,
        event_name: str,
        payload: dict[str, Any],
        *,
        attempt: int,
        request_kind: str,
    ) -> Iterator[CanonicalResponseEvent]:
        if self._completed:
            return
        if event_name == 'message_start':
            message = payload.get('message')
            message_payload = message if isinstance(message, dict) else {}
            self._response_id = str(message_payload.get('id') or '')
            self._metadata.update(deepcopy({
                key: value for key, value in message_payload.items()
                if key not in {'content', 'usage'}
            }))
            self._merge_usage(message_payload.get('usage'))
            yield from self._ensure_response_started(attempt, request_kind)
            return
        if event_name == 'content_block_start':
            yield from self._start_block(payload, attempt, request_kind)
            return
        if event_name == 'content_block_delta':
            yield from self._feed_delta(payload, attempt, request_kind)
            return
        if event_name == 'content_block_stop':
            yield from self._complete_block(payload, attempt, request_kind)
            return
        if event_name == 'message_delta':
            delta = payload.get('delta')
            if isinstance(delta, dict):
                self._metadata.update(deepcopy(delta))
                self._stop_reason = str(delta.get('stop_reason') or self._stop_reason)
            self._merge_usage(payload.get('usage'))
            return
        if event_name == 'message_stop':
            yield from self._complete_response(attempt, request_kind)
            return
        if event_name != 'ping':
            self._extension_events.append({'event': event_name, 'payload': deepcopy(payload)})

    def _ensure_response_started(
        self,
        attempt: int,
        request_kind: str,
    ) -> Iterator[CanonicalResponseEvent]:
        if self._started:
            return
        self._started = True
        yield self._event(
            CanonicalResponseEventType.RESPONSE_STARTED,
            attempt=attempt,
            request_kind=request_kind,
        )

    def _start_block(
        self,
        payload: dict[str, Any],
        attempt: int,
        request_kind: str,
    ) -> Iterator[CanonicalResponseEvent]:
        yield from self._ensure_response_started(attempt, request_kind)
        index = _content_index(payload)
        block = payload.get('content_block')
        if index is None or not isinstance(block, dict):
            logging.getLogger(__name__).debug('Skipping malformed Anthropic block start: %r', payload)
            return
        block_payload = deepcopy(block)
        item_type = _anthropic_item_type(str(block_payload.get('type') or ''))
        item_id = f'content-{index}'
        self._blocks[index] = block_payload
        self._item_ids[index] = item_id
        self._sequences[index] = self._next_sequence
        self._next_sequence += 1
        yield self._event(
            CanonicalResponseEventType.ITEM_STARTED,
            attempt=attempt,
            request_kind=request_kind,
            item_id=item_id,
            item_type=item_type,
            sequence_index=self._sequences[index],
        )

    def _feed_delta(
        self,
        payload: dict[str, Any],
        attempt: int,
        request_kind: str,
    ) -> Iterator[CanonicalResponseEvent]:
        index = _content_index(payload)
        block = self._blocks.get(index)
        if block is None:
            logging.getLogger(__name__).debug('Skipping orphan Anthropic delta: %r', payload)
            return
        delta = payload.get('delta')
        if not isinstance(delta, dict):
            logging.getLogger(__name__).debug('Skipping malformed Anthropic delta: %r', payload)
            return
        delta_payload = delta
        delta_type = str(delta_payload.get('type') or '')
        if delta_type == 'citations_delta':
            _append_anthropic_delta(block, delta_type, delta_payload)
            return
        if delta_type == 'signature_delta':
            if not isinstance(delta_payload.get('signature'), str):
                logging.getLogger(__name__).debug('Skipping malformed Anthropic signature: %r', payload)
                return
            _append_anthropic_delta(
                block,
                delta_type,
                str(delta_payload.get('signature') or ''),
            )
            return
        text, event_type = _anthropic_delta(delta_type, delta_payload)
        if event_type is None:
            self._extension_events.append({
                'event': 'content_block_delta', 'payload': deepcopy(payload),
            })
            return
        _append_anthropic_delta(block, delta_type, text)
        yield self._event(
            event_type,
            attempt=attempt,
            request_kind=request_kind,
            item_id=self._item_ids[index],
            item_type=_anthropic_item_type(str(block.get('type') or '')),
            sequence_index=self._sequences[index],
            delta=text,
        )

    def _complete_block(
        self,
        payload: dict[str, Any],
        attempt: int,
        request_kind: str,
    ) -> Iterator[CanonicalResponseEvent]:
        index = _content_index(payload)
        block = self._blocks.pop(index, None)
        if block is None:
            logging.getLogger(__name__).debug('Skipping orphan Anthropic block stop: %r', payload)
            return
        raw_input = block.pop('_input_json', None)
        original_block = deepcopy(block)
        if raw_input:
            try:
                block['input'] = decode_native_json(raw_input)
            except json.JSONDecodeError:
                # Invalid arguments are retained verbatim below, not rejected.
                pass
        items = AnthropicNativeToolAdapter().parse_response_items({'content': [block]})
        if not items:
            logging.getLogger(__name__).debug('Skipping undecodable Anthropic block: %r', block)
            return
        item = replace(items[0], sequence_index=self._sequences[index])
        # Empty fragments preserve the initial input; raw deltas remain in the event payload.
        if raw_input:
            item = replace(item, raw_arguments=raw_input)
        item_id = self._item_ids[index]
        yield self._event(
            CanonicalResponseEventType.ITEM_COMPLETED,
            attempt=attempt,
            request_kind=request_kind,
            item_id=item_id,
            item_type=item.kind.value,
            sequence_index=item.sequence_index,
            completed_item=item,
            payload={'provider_block': original_block, 'raw_arguments': raw_input},
        )

    def _complete_response(
        self,
        attempt: int,
        request_kind: str,
    ) -> Iterator[CanonicalResponseEvent]:
        yield from self._ensure_response_started(attempt, request_kind)
        if self._usage:
            yield self._event(
                CanonicalResponseEventType.USAGE_UPDATED,
                attempt=attempt,
                request_kind=request_kind,
                usage=self._usage,
            )
        self._completed = True
        yield self._event(
            CanonicalResponseEventType.RESPONSE_COMPLETED,
            attempt=attempt,
            request_kind=request_kind,
            usage=self._usage,
            stop_reason=self._stop_reason,
            provider_metadata={
                **self._metadata,
                'usage': deepcopy(self._raw_usage),
                'api_style': 'anthropic_messages',
                'stop_reason': self._stop_reason,
                **({'extension_events': deepcopy(self._extension_events)} if self._extension_events else {}),
            },
        )

    def _event(
        self,
        event_type: CanonicalResponseEventType,
        *,
        attempt: int,
        request_kind: str,
        **kwargs: Any,
    ) -> CanonicalResponseEvent:
        return CanonicalResponseEvent(
            event_type=event_type,
            request_kind=request_kind,
            response_mode='native_tools',
            response_id=self._response_id,
            attempt=attempt,
            provider_protocol='anthropic_messages',
            **kwargs,
        )

    def _merge_usage(self, value: Any) -> None:
        if isinstance(value, dict):
            self._raw_usage.update(deepcopy(value))
        self._usage.update(_anthropic_usage(value))
        if 'input_tokens' in self._usage and 'output_tokens' in self._usage:
            self._usage['total_tokens'] = (
                self._usage['input_tokens'] + self._usage['output_tokens']
            )


def iter_anthropic_sse_events(response: Any) -> Iterator[tuple[str, dict[str, Any]]]:
    current_event = ''
    data_chunks: list[str] = []
    while True:
        raw_line = response.readline()
        if not raw_line:
            if data_chunks:
                yield from _decode_sse_block(current_event, data_chunks)
            return
        line = raw_line if isinstance(raw_line, str) else raw_line.decode('utf-8', errors='replace')
        stripped = line.rstrip('\r\n')
        if not stripped:
            yield from _decode_sse_block(current_event, data_chunks)
            current_event = ''
            data_chunks = []
        elif stripped.startswith('event:'):
            current_event = stripped[len('event:') :].strip()
        elif stripped.startswith('data:'):
            data_chunks.append(stripped[len('data:') :].lstrip())


def _decode_sse_block(
    event_name: str,
    data_chunks: list[str],
) -> Iterator[tuple[str, dict[str, Any]]]:
    if not data_chunks:
        return
    try:
        parsed = json.loads('\n'.join(data_chunks))
    except json.JSONDecodeError:
        logging.getLogger(__name__).debug('Ignoring malformed Anthropic SSE data: %r', data_chunks)
        return
    if not isinstance(parsed, dict):
        logging.getLogger(__name__).debug('Ignoring non-object Anthropic SSE data: %r', parsed)
        return
    normalized_name = event_name or str(parsed.get('type') or '')
    if normalized_name:
        yield normalized_name, parsed


def _content_index(payload: dict[str, Any]) -> int | None:
    value = payload.get('index')
    if type(value) is not int or value < 0:
        return None
    return value


def _anthropic_item_type(block_type: str) -> str:
    return {
        'text': ProviderOutputItemKind.MESSAGE.value,
        'thinking': ProviderOutputItemKind.REASONING.value,
        'redacted_thinking': ProviderOutputItemKind.REASONING.value,
        'tool_use': ProviderOutputItemKind.TOOL_CALL.value,
        'server_tool_use': ProviderOutputItemKind.HOSTED_TOOL_CALL.value,
        'web_search_tool_result': ProviderOutputItemKind.HOSTED_TOOL_RESULT.value,
    }.get(block_type, ProviderOutputItemKind.PROVIDER_SPECIFIC.value)


def _anthropic_delta(
    delta_type: str,
    payload: dict[str, Any],
) -> tuple[str, CanonicalResponseEventType | None]:
    field = {'text_delta': 'text', 'thinking_delta': 'thinking', 'input_json_delta': 'partial_json'}.get(delta_type)
    if field is not None and not isinstance(payload.get(field), str):
        return '', None
    if delta_type == 'text_delta':
        return str(payload.get('text') or ''), CanonicalResponseEventType.ASSISTANT_TEXT_DELTA
    if delta_type == 'thinking_delta':
        return (
            str(payload.get('thinking') or ''),
            CanonicalResponseEventType.ASSISTANT_REASONING_DELTA,
        )
    if delta_type == 'input_json_delta':
        return (
            str(payload.get('partial_json') or ''),
            CanonicalResponseEventType.TOOL_CALL_ARGUMENTS_DELTA,
        )
    return '', None


def _append_anthropic_delta(
    block: dict[str, Any],
    delta_type: str,
    value: str | dict[str, Any],
) -> None:
    text = value if isinstance(value, str) else ''
    if delta_type == 'text_delta':
        if isinstance(block.get('text', ''), str):
            block['text'] = block.get('text', '') + text
    elif delta_type == 'thinking_delta':
        if isinstance(block.get('thinking', ''), str):
            block['thinking'] = block.get('thinking', '') + text
    elif delta_type == 'signature_delta':
        if isinstance(block.get('signature', ''), str):
            block['signature'] = block.get('signature', '') + text
    elif delta_type == 'input_json_delta':
        block['_input_json'] = f'{block.get("_input_json", "")}{text}'
    elif delta_type == 'citations_delta' and isinstance(value, dict):
        citation = value.get('citation')
        if isinstance(citation, dict) and isinstance(block.get('citations', []), list):
            block.setdefault('citations', []).append(dict(citation))


def _anthropic_usage(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    result = {
        str(key): int(item)
        for key, item in value.items()
        if isinstance(item, int) and not isinstance(item, bool)
    }
    if 'input_tokens' in result and 'output_tokens' in result:
        result['total_tokens'] = result['input_tokens'] + result['output_tokens']
    if 'cache_read_input_tokens' in result:
        result['cache_read_tokens'] = result.pop('cache_read_input_tokens')
    if 'cache_creation_input_tokens' in result:
        result['cache_write_tokens'] = result.pop('cache_creation_input_tokens')
    return result


__all__ = ['AnthropicMessagesStreamNormalizer', 'iter_anthropic_sse_events']
