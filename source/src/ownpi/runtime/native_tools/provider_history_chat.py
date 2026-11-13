"""Chat message grouping, limited to the current native call/result batch."""

from __future__ import annotations

from typing import Any

from ownpi.runtime.native_tools.history_items import HistoryMessage, ToolOutputItem, ToolSearchOutputItem
from ownpi.runtime.native_tools.provider import ProviderOutputItem, ProviderOutputItemKind
from ownpi.runtime.native_tools.provider_content_projection import (
    history_prefix_messages, openai_chat_content, provider_message_content,
)
from ownpi.runtime.native_tools.provider_input import NativeHistoryProviderInput


def openai_chat_history_messages(
    value: NativeHistoryProviderInput,
    *,
    prefix_messages: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    writer = _ChatMessages(history_prefix_messages(value, prefix_messages))
    for entry in value.items:
        item = entry.item
        if isinstance(item, HistoryMessage):
            writer.context({'role': item.role, 'content': item.text})
        elif isinstance(item, (ToolOutputItem, ToolSearchOutputItem)):
            if item.kind == 'function_call_output':
                writer.output(item)
        else:
            writer.provider_item(item)
    writer.flush_assistant()
    writer.messages.extend(writer.deferred)
    return writer.messages


class _ChatMessages:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.messages = messages
        self.assistant: dict[str, Any] | None = None
        self.pending: set[str] = set()
        self.deferred: list[dict[str, Any]] = []

    def flush_assistant(self) -> None:
        if self.assistant is not None:
            self.messages.append(self.assistant)
            self.assistant = None

    def context(self, message: dict[str, Any]) -> None:
        if self.pending:
            self.deferred.append(message)
        else:
            self.flush_assistant()
            self.messages.append(message)

    def provider_item(self, item: ProviderOutputItem) -> None:
        payload = item.provider_payload.to_object()
        if item.kind is ProviderOutputItemKind.TOOL_CALL:
            if item.provider_type not in {'function', 'function_call', 'tool_use'}:
                return
            message = self._assistant()
            message.setdefault('tool_calls', []).append({
                'id': item.call_id, 'type': 'function',
                'function': {'name': item.tool_name, 'arguments': item.raw_arguments},
            })
            self.pending.add(item.call_id)
        elif item.kind is ProviderOutputItemKind.REASONING:
            # Chat parser emits these fields separately from the full message.
            if item.provider_type in {'reasoning_content', 'reasoning'} and item.provider_type in payload:
                if self.assistant is not None and self.assistant.get('content') is not None and not self.pending:
                    self.flush_assistant()
                self._assistant()[item.provider_type] = payload[item.provider_type]
        elif item.kind is ProviderOutputItemKind.MESSAGE:
            raw_content = payload.get('content', item.text)
            content = provider_message_content(
                raw_content,
                source='openai_responses' if payload.get('type') == 'message' else 'openai_chat',
                target='openai_chat',
            )
            if not content and not any(payload.get(field) for field in ('name', 'refusal', 'audio')):
                return
            if raw_content is None:
                content = None
            if self.pending and self.assistant is None:
                self.deferred.append({'role': 'assistant', 'content': content})
                return
            if self.assistant is not None and self.assistant.get('content') is not None:
                if not self.pending:
                    self.flush_assistant()
                else:
                    content = _join_content(self.assistant['content'], content)
            message = self._assistant()
            message['content'] = content
            for field in ('name', 'refusal'):
                if field in payload:
                    message[field] = payload[field]
            if isinstance(payload.get('audio'), dict) and 'id' in payload['audio']:
                message['audio'] = {'id': payload['audio']['id']}

    def _assistant(self) -> dict[str, Any]:
        if self.assistant is None:
            self.assistant = {'role': 'assistant', 'content': None}
        return self.assistant

    def output(self, item: ToolOutputItem | ToolSearchOutputItem) -> None:
        self.flush_assistant()
        content: str | list[dict[str, Any]] = item.body if isinstance(item.body, str) else ''
        if not isinstance(item.body, str):
            blocks = openai_chat_content(item.body)
            content = [block for block in blocks if block['type'] == 'text'] or ''
            images = [block for block in blocks if block['type'] != 'text']
            if images:
                # Chat tool messages accept text only. Keep media next to this
                # result batch, labelled by the original call, not exchange.
                self.deferred.append({'role': 'user', 'content': [
                    {'type': 'text', 'text': f'[Tool output images: {item.tool_name}; call_id={item.call_id}]'},
                    *images,
                ]})
        self.messages.append({'role': 'tool', 'tool_call_id': item.call_id, 'content': content})
        self.pending.discard(item.call_id)
        if not self.pending:
            self.messages.extend(self.deferred)
            self.deferred.clear()


def _join_content(left: Any, right: Any) -> Any:
    if isinstance(left, str) and isinstance(right, str):
        return left + right
    left_blocks = left if isinstance(left, list) else [{'type': 'text', 'text': left}] if left else []
    right_blocks = right if isinstance(right, list) else [{'type': 'text', 'text': right}] if right else []
    return [*left_blocks, *right_blocks]
