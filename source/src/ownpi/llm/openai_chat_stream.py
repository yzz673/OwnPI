from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from copy import deepcopy
from dataclasses import replace
from typing import Any

from ownpi.llm.streaming import CanonicalResponseEvent, CanonicalResponseEventType
from ownpi.runtime.native_tools.adapters import OpenAINativeToolAdapter
from ownpi.runtime.native_tools.provider import (
    FrozenProviderJson,
    ProviderOutputItem,
    ProviderOutputItemKind,
)


UsageParser = Callable[[dict[str, Any]], dict[str, int]]


class OpenAIChatStreamNormalizer:
    def __init__(
        self,
        *,
        usage_parser: UsageParser = lambda payload: _integer_usage(payload.get('usage')),
    ) -> None:
        self._usage_parser = usage_parser
        self._response_id = ''
        self._started = False
        self._message: dict[str, Any] = {}
        self._metadata: dict[str, Any] = {}
        self._choice_metadata: dict[str, Any] = {}
        self._other_choices: list[dict[str, Any]] = []
        self._reasoning_sequences: dict[str, int] = {}
        self._extension_deltas: list[dict[str, Any]] = []
        self._tool_extension_deltas: list[dict[str, Any]] = []
        self._tool_calls: dict[int, dict[str, Any]] = {}
        self._tool_item_ids: dict[int, str] = {}
        self._tool_sequences: dict[int, int] = {}
        self._text_item_id = ''
        self._text_sequence = -1
        self._next_sequence = 0
        self._usage: dict[str, int] = {}
        self._finish_reason = ''

    @property
    def terminal_observed(self) -> bool:
        return bool(self._finish_reason)

    def feed(
        self,
        payload: dict[str, Any],
        *,
        attempt: int,
        request_kind: str,
    ) -> Iterator[CanonicalResponseEvent]:
        self._response_id = str(payload.get('id') or self._response_id)
        self._metadata.update(deepcopy({
            key: value for key, value in payload.items()
            if key != 'choices' and value is not None
        }))
        if not self._started:
            self._started = True
            yield self._event(
                CanonicalResponseEventType.RESPONSE_STARTED,
                attempt=attempt,
                request_kind=request_kind,
            )
        self._usage.update(self._usage_parser(payload))
        choices = payload.get('choices')
        if not isinstance(choices, list):
            logging.getLogger(__name__).debug('Skipping malformed Chat choices: %r', payload)
            return
        if not choices:
            return
        primary = []
        for choice in choices:
            if not isinstance(choice, dict):
                logging.getLogger(__name__).debug('Skipping malformed Chat choice: %r', choice)
                continue
            if choice.get('index', 0) == 0:
                primary.append(choice)
            else:
                self._other_choices.append(deepcopy(choice))
        if not primary:
            return
        for choice in primary:
            yield from self._feed_choice(choice, attempt, request_kind)

    def _feed_choice(
        self, choice: dict[str, Any], attempt: int, request_kind: str,
    ) -> Iterator[CanonicalResponseEvent]:
        self._choice_metadata.update(deepcopy({
            key: value for key, value in choice.items()
            if key != 'delta' and value is not None
        }))
        self._finish_reason = str(choice.get('finish_reason') or self._finish_reason)
        delta = choice.get('delta')
        if not isinstance(delta, dict):
            logging.getLogger(__name__).debug('Skipping non-object Chat delta: %r', choice)
            return
        extensions = {
            key: value for key, value in delta.items()
            if key not in {'content', 'tool_calls', 'role', 'reasoning', 'reasoning_content', 'refusal'}
        }
        if extensions:
            self._extension_deltas.append(deepcopy(extensions))
        for key, value in delta.items():
            if key in {'content', 'tool_calls'}:
                continue
            if key in {'reasoning', 'reasoning_content', 'refusal'} and isinstance(value, str):
                self._message[key] = str(self._message.get(key) or '') + value
            else:
                self._message[key] = deepcopy(value)
            if key in {'reasoning', 'reasoning_content'} and isinstance(value, str):
                if key not in self._reasoning_sequences:
                    self._reasoning_sequences[key] = self._next_sequence
                    self._next_sequence += 1
                    yield self._item_started(
                        f'chat-{key}', ProviderOutputItemKind.REASONING.value,
                        self._reasoning_sequences[key], attempt, request_kind,
                    )
                yield self._event(
                    CanonicalResponseEventType.ASSISTANT_REASONING_DELTA,
                    attempt=attempt, request_kind=request_kind,
                    item_id=f'chat-{key}', item_type=ProviderOutputItemKind.REASONING.value,
                    sequence_index=self._reasoning_sequences[key], delta=value,
                )
        content = delta.get('content')
        if isinstance(content, (str, list)):
            previous = self._message.get('content')
            if previous is None:
                self._message['content'] = deepcopy(content)
            elif isinstance(previous, str) and isinstance(content, str):
                self._message['content'] = previous + content
            else:
                before = [{'type': 'text', 'text': previous}] if isinstance(previous, str) else previous
                after = [{'type': 'text', 'text': content}] if isinstance(content, str) else content
                self._message['content'] = before + deepcopy(after)
        elif 'content' in delta:
            if content is None:
                self._message.setdefault('content', None)
            else:
                self._extension_deltas.append({'content': deepcopy(content)})
        if isinstance(content, str) and content:
            yield from self._feed_text(content, attempt, request_kind)
        elif content or self._message.get('refusal') or extensions:
            yield from self._ensure_message_started(attempt, request_kind)
        raw_calls = delta.get('tool_calls')
        if raw_calls is not None and not isinstance(raw_calls, list):
            self._extension_deltas.append({'tool_calls': deepcopy(raw_calls)})
        if isinstance(raw_calls, list):
            for raw_call in raw_calls:
                if not isinstance(raw_call, dict):
                    self._extension_deltas.append({'tool_calls': [deepcopy(raw_call)]})
                    continue
                yield from self._feed_tool_call(raw_call, attempt, request_kind)

    def finish(
        self,
        *,
        attempt: int,
        request_kind: str,
    ) -> Iterator[CanonicalResponseEvent]:
        if not self.terminal_observed:
            raise ValueError('OpenAI Chat stream ended without finish_reason')
        message = deepcopy(self._message)
        if self._tool_calls:
            message['tool_calls'] = [self._tool_calls[index] for index in sorted(self._tool_calls)]
        adapter = OpenAINativeToolAdapter()
        items = adapter.parse_response_items(self._message)
        for item in items:
            if item.kind is ProviderOutputItemKind.REASONING and item.provider_type not in self._reasoning_sequences:
                self._reasoning_sequences[item.provider_type] = self._next_sequence
                self._next_sequence += 1
                yield self._item_started(
                    f'chat-{item.provider_type}', item.kind.value,
                    self._reasoning_sequences[item.provider_type], attempt, request_kind,
                )
            if item.kind is ProviderOutputItemKind.MESSAGE and not self._text_item_id:
                self._text_item_id = 'chat-text-0'
                self._text_sequence = self._next_sequence
                self._next_sequence += 1
                yield self._item_started(
                    self._text_item_id, item.kind.value, self._text_sequence,
                    attempt, request_kind,
                )
        completed: list[tuple[str, ProviderOutputItem]] = []
        for item in items:
            item_id, sequence_index = self._completed_item_identity(item)
            item = replace(item, sequence_index=sequence_index)
            if item.kind is ProviderOutputItemKind.MESSAGE:
                item = replace(item, provider_payload=FrozenProviderJson.from_value(message))
            completed.append((item_id, item))
        # Parse each call independently: skipped malformed calls cannot shift identities.
        for index in sorted(self._tool_calls):
            call_items = adapter.parse_response_items({'tool_calls': [self._tool_calls[index]]})
            if not call_items:
                logging.getLogger(__name__).debug('Skipping undecodable Chat call: %r', self._tool_calls[index])
            for item in call_items:
                item = replace(item, sequence_index=self._tool_sequences[index])
                completed.append((self._tool_item_ids[index], item))
        for item_id, item in sorted(completed, key=lambda entry: entry[1].sequence_index):
            yield self._event(
                CanonicalResponseEventType.ITEM_COMPLETED,
                attempt=attempt,
                request_kind=request_kind,
                item_id=item_id,
                item_type=item.kind.value,
                sequence_index=item.sequence_index,
                completed_item=item,
            )
        if self._usage:
            yield self._event(
                CanonicalResponseEventType.USAGE_UPDATED,
                attempt=attempt,
                request_kind=request_kind,
                usage=self._usage,
            )
        yield self._event(
            CanonicalResponseEventType.RESPONSE_COMPLETED,
            attempt=attempt,
            request_kind=request_kind,
            usage=self._usage,
            stop_reason=self._finish_reason,
            provider_metadata={
                **self._metadata,
                'choice': self._choice_metadata,
                'other_choice_chunks': self._other_choices,
                'message': message,
                'extension_deltas': self._extension_deltas,
                'tool_extension_deltas': self._tool_extension_deltas,
                'finish_reason': self._finish_reason,
            },
        )

    def _feed_text(
        self,
        content: str,
        attempt: int,
        request_kind: str,
    ) -> Iterator[CanonicalResponseEvent]:
        yield from self._ensure_message_started(attempt, request_kind)
        yield self._event(
            CanonicalResponseEventType.ASSISTANT_TEXT_DELTA,
            attempt=attempt,
            request_kind=request_kind,
            item_id=self._text_item_id,
            item_type=ProviderOutputItemKind.MESSAGE.value,
            sequence_index=self._text_sequence,
            delta=content,
        )

    def _ensure_message_started(
        self, attempt: int, request_kind: str,
    ) -> Iterator[CanonicalResponseEvent]:
        if not self._text_item_id:
            self._text_item_id = 'chat-text-0'
            self._text_sequence = self._next_sequence
            self._next_sequence += 1
            yield self._item_started(
                self._text_item_id,
                ProviderOutputItemKind.MESSAGE.value,
                self._text_sequence,
                attempt,
                request_kind,
            )

    def _feed_tool_call(
        self,
        raw_call: dict[str, Any],
        attempt: int,
        request_kind: str,
    ) -> Iterator[CanonicalResponseEvent]:
        index = raw_call.get('index', 0)
        if type(index) is not int or index < 0:
            self._tool_extension_deltas.append(deepcopy(raw_call))
            return
        current = self._tool_calls.setdefault(
            index,
            {'type': raw_call.get('type', 'function')},
        )
        if 'id' in raw_call:
            current['id'] = deepcopy(raw_call['id'])
        current.update(deepcopy({
            key: value for key, value in raw_call.items()
            if key not in {'id', 'index', 'function'}
        }))
        function = raw_call.get('function')
        if (
            current['type'] != 'function'
            or any(key not in {'id', 'index', 'type', 'function'} for key in raw_call)
            or isinstance(function, dict) and any(key not in {'name', 'arguments'} for key in function)
        ):
            # Unknown delta semantics cannot be reconstructed from last-value merging.
            self._tool_extension_deltas.append(deepcopy(raw_call))
        function_delta = function if isinstance(function, dict) else {}
        arguments_delta = function_delta.get('arguments', '')
        if current['type'] == 'function' and isinstance(function, dict):
            current_function = current.get('function')
            if not isinstance(current_function, dict):
                current_function = {}
                current['function'] = current_function
            for field in ('name', 'arguments'):
                if field in function_delta:
                    previous = current_function.get(field, '')
                    value = function_delta[field]
                    current_function[field] = (
                        previous + value if isinstance(previous, str) and isinstance(value, str)
                        else deepcopy(value)
                    )
            current_function.update(deepcopy({
                key: value for key, value in function_delta.items()
                if key not in {'name', 'arguments'}
            }))
        elif function is not None:
            current['function'] = deepcopy(function)
        item_kind = (
            ProviderOutputItemKind.TOOL_CALL if current['type'] == 'function'
            else ProviderOutputItemKind.PROVIDER_SPECIFIC
        )
        item_id = self._tool_item_ids.get(index)
        if not item_id:
            item_id = f'chat-tool-{index}'
            self._tool_item_ids[index] = item_id
            self._tool_sequences[index] = self._next_sequence
            self._next_sequence += 1
            yield self._item_started(
                item_id,
                item_kind.value,
                self._tool_sequences[index],
                attempt,
                request_kind,
            )
        if isinstance(arguments_delta, str) and arguments_delta:
            yield self._event(
                CanonicalResponseEventType.TOOL_CALL_ARGUMENTS_DELTA,
                attempt=attempt,
                request_kind=request_kind,
                item_id=item_id,
                item_type=item_kind.value,
                sequence_index=self._tool_sequences[index],
                delta=arguments_delta,
            )

    def _completed_item_identity(self, item: ProviderOutputItem) -> tuple[str, int]:
        if item.kind is ProviderOutputItemKind.MESSAGE:
            return self._text_item_id, self._text_sequence
        if item.kind is ProviderOutputItemKind.REASONING:
            return f'chat-{item.provider_type}', self._reasoning_sequences[item.provider_type]
        raise AssertionError('message parser unexpectedly returned a tool item')

    def _item_started(
        self,
        item_id: str,
        item_type: str,
        sequence_index: int,
        attempt: int,
        request_kind: str,
    ) -> CanonicalResponseEvent:
        return self._event(
            CanonicalResponseEventType.ITEM_STARTED,
            attempt=attempt,
            request_kind=request_kind,
            item_id=item_id,
            item_type=item_type,
            sequence_index=sequence_index,
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
            provider_protocol='openai_chat',
            **kwargs,
        )


def _integer_usage(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): int(item)
        for key, item in value.items()
        if isinstance(item, int) and not isinstance(item, bool)
    }


__all__ = ['OpenAIChatStreamNormalizer']
