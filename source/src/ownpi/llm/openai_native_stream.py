from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from dataclasses import replace
from typing import Any

from ownpi.llm.responses_item_types import (
    _integer_usage,
    _responses_delta_type,
    _responses_item_id,
)
from ownpi.llm.streaming import CanonicalResponseEvent, CanonicalResponseEventType
from ownpi.runtime.native_tools.provider import ProviderOutputItem


ResponsesItemParser = Callable[[dict[str, Any]], tuple[ProviderOutputItem, ...]]
UsageParser = Callable[[dict[str, Any]], dict[str, int]]


class OpenAIResponsesStreamNormalizer:
    def __init__(
        self,
        *,
        item_parser: ResponsesItemParser,
        usage_parser: UsageParser = lambda payload: _integer_usage(payload.get('usage')),
    ) -> None:
        self._item_parser = item_parser
        self._usage_parser = usage_parser
        self._response_id = ''
        self._started = False
        self._completed = False
        self._next_sequence = 0
        self._indices: dict[int, str] = {}
        self._active_id = ''

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
        if self._completed:
            return
        response = payload.get('response')
        if isinstance(response, dict) and isinstance(response.get('id'), str):
            self._response_id = response['id']
        if event_name == 'response.created':
            yield from self._ensure_response_started(attempt, request_kind)
        elif event_name in {'response.output_item.added', 'response.output_item.done'}:
            yield from self._normalize_item(event_name, payload, attempt, request_kind)
        elif event_name in {
            'response.output_text.delta',
            'response.reasoning_summary_text.delta',
            'response.reasoning_text.delta',
            'response.function_call_arguments.delta',
            'response.custom_tool_call_input.delta',
            'response.tool_call_arguments.delta',
        }:
            yield from self._normalize_delta(event_name, payload, attempt, request_kind)
        elif event_name == 'response.completed' and isinstance(response, dict):
            yield from self._complete_response(response, attempt, request_kind)

    def _ensure_response_started(
        self, attempt: int, request_kind: str,
    ) -> Iterator[CanonicalResponseEvent]:
        if not self._started:
            self._started = True
            yield self._event(
                CanonicalResponseEventType.RESPONSE_STARTED,
                attempt=attempt, request_kind=request_kind,
            )

    def _normalize_item(
        self, event_name: str, payload: dict[str, Any], attempt: int, request_kind: str,
    ) -> Iterator[CanonicalResponseEvent]:
        yield from self._ensure_response_started(attempt, request_kind)
        item = payload.get('item')
        if not isinstance(item, dict):
            logging.getLogger(__name__).debug('Skipping non-object Responses item: event=%s payload=%r', event_name, payload)
            return
        items = self._item_parser({'output': [item]})
        index = payload.get('output_index')
        indexed_id = self._indices.get(index, '') if type(index) is int else ''
        item_id = item.get('id') or payload.get('item_id') or indexed_id
        item_id = str(item_id or _responses_item_id(
            item, payload, fallback_index=self._next_sequence,
        ))
        if not items:
            logging.getLogger(__name__).debug('Skipping undecodable Responses item: event=%s payload=%r', event_name, payload)
            if type(index) is int:
                self._indices.pop(index, None)
            return
        if event_name == 'response.output_item.added':
            self._active_id = item_id
            if type(index) is int:
                self._indices[index] = item_id
            yield self._event(
                CanonicalResponseEventType.ITEM_STARTED,
                attempt=attempt, request_kind=request_kind,
                item_id=item_id, item_type=items[0].kind.value,
                sequence_index=self._next_sequence,
                payload={'provider_item': item},
            )
            return
        # Done events, including repeated identities, are independent authorities.
        for parsed in items:
            completed = replace(
                parsed, sequence_index=self._next_sequence,
                provider_item_id=parsed.provider_item_id or indexed_id,
            )
            self._next_sequence += 1
            yield self._event(
                CanonicalResponseEventType.ITEM_COMPLETED,
                attempt=attempt, request_kind=request_kind,
                item_id=item_id, item_type=completed.kind.value,
                sequence_index=completed.sequence_index, completed_item=completed,
                payload={'provider_item': item},
            )
        if type(index) is int:
            self._indices.pop(index, None)
        self._active_id = ''

    def _normalize_delta(
        self, event_name: str, payload: dict[str, Any], attempt: int, request_kind: str,
    ) -> Iterator[CanonicalResponseEvent]:
        delta = payload.get('delta')
        if not isinstance(delta, str):
            logging.getLogger(__name__).debug('Skipping malformed Responses delta: event=%s payload=%r', event_name, payload)
            return
        yield from self._ensure_response_started(attempt, request_kind)
        item_type, canonical_type = _responses_delta_type(event_name)
        index = payload.get('output_index')
        indexed_id = self._indices.get(index, '') if type(index) is int else ''
        item_id = payload.get('item_id') or payload.get('call_id') or indexed_id
        yield self._event(
            canonical_type, attempt=attempt, request_kind=request_kind,
            item_id=str(item_id or self._active_id), item_type=item_type,
            sequence_index=self._next_sequence, delta=delta,
        )

    def _complete_response(
        self, response: dict[str, Any], attempt: int, request_kind: str,
    ) -> Iterator[CanonicalResponseEvent]:
        yield from self._ensure_response_started(attempt, request_kind)
        usage = self._usage_parser(response)
        if usage:
            yield self._event(
                CanonicalResponseEventType.USAGE_UPDATED,
                attempt=attempt, request_kind=request_kind, usage=usage,
            )
        self._completed = True
        yield self._event(
            CanonicalResponseEventType.RESPONSE_COMPLETED,
            attempt=attempt, request_kind=request_kind, usage=usage,
            stop_reason=str(response.get('stop_reason') or ''),
            provider_metadata={key: value for key, value in response.items() if key != 'output'},
            payload={'response_payload': response},
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
            event_type=event_type, request_kind=request_kind,
            response_mode='native_tools', response_id=self._response_id,
            attempt=attempt, provider_protocol='openai_responses', **kwargs,
        )


__all__ = ['OpenAIResponsesStreamNormalizer']
