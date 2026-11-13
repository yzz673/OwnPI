"""Anthropic native blocks and strictly local tool-result adjacency."""

from __future__ import annotations

import json
from typing import Any

from ownpi.runtime.model_content import ModelContentItem
from ownpi.runtime.native_tools.history_items import HistoryMessage, ToolOutputItem, ToolSearchOutputItem
from ownpi.runtime.native_tools.provider import ProviderOutputItem, ProviderOutputItemKind
from ownpi.runtime.native_tools.provider_content_projection import (
    anthropic_content, history_prefix_messages, provider_message_content,
)
from ownpi.runtime.native_tools.provider_input import NativeHistoryProviderInput


def anthropic_history_messages(
    value: NativeHistoryProviderInput,
    *,
    prefix_messages: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    writer = _AnthropicMessages()
    for message in history_prefix_messages(value, prefix_messages):
        role = message.get('role', 'user')
        if role not in {'system', 'developer'}:
            writer.append(role, _blocks(message.get('content', '')))
    for entry in value.items:
        item = entry.item
        if isinstance(item, HistoryMessage):
            writer.context(item)
        elif isinstance(item, (ToolOutputItem, ToolSearchOutputItem)):
            if item.kind == 'function_call_output':
                writer.output(item)
        else:
            writer.provider_item(item)
    writer.flush_context()
    return writer.messages


def anthropic_system_text(value: NativeHistoryProviderInput) -> str:
    return '\n\n'.join(
        str(item.get('content') or '')
        for item in value.immutable_prefix_messages
        if item.get('role') in {'system', 'developer'}
        and str(item.get('content') or '').strip()
    )


class _AnthropicMessages:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.pending: set[str] = set()
        self.deferred: list[tuple[str, list[dict[str, Any]]]] = []

    def append(self, role: str, blocks: list[dict[str, Any]]) -> None:
        if not blocks:
            return
        if self.messages and self.messages[-1]['role'] == role:
            self.messages[-1]['content'].extend(blocks)
        else:
            self.messages.append({'role': role, 'content': blocks})

    def context(self, item: HistoryMessage) -> None:
        role, text = item.role, item.text
        if role in {'system', 'developer'}:
            metadata = item.metadata.to_object()
            source = {key: metadata[key] for key in ('event_name', 'source', 'scope') if key in metadata}
            marker = f'[{role.capitalize()} context'
            if source:
                marker += ': ' + json.dumps(source, ensure_ascii=False, sort_keys=True)
            text = marker + ']\n' + text
            role = 'user'
        blocks = [{'type': 'text', 'text': text}]
        if self.pending:
            self.deferred.append((role, blocks))
        else:
            self.append(role, blocks)

    def flush_context(self) -> None:
        for role, blocks in self.deferred:
            self.append(role, blocks)
        self.deferred.clear()

    def provider_item(self, item: ProviderOutputItem) -> None:
        payload = item.provider_payload.to_object()
        block_type = payload.get('type')
        if item.kind is ProviderOutputItemKind.TOOL_CALL:
            if block_type == 'tool_use':
                block = payload
            elif item.provider_type in {'function', 'function_call'}:
                # Invalid JSON has no object-valued wire equivalent. Never
                # replace the raw arguments with a made-up error argument.
                block = {'type': 'tool_use', 'id': item.call_id, 'name': item.tool_name,
                         'input': json.loads(item.raw_arguments)}
            else:
                return
            self.append('assistant', [block])
            self.pending.add(item.call_id)
        elif block_type in {'text', 'thinking', 'redacted_thinking', 'server_tool_use', 'web_search_tool_result'}:
            self.assistant_blocks([payload])
        elif item.kind is ProviderOutputItemKind.MESSAGE:
            content = payload.get('content', item.text)
            projected = provider_message_content(
                content, source='openai_responses' if block_type == 'message' else 'openai_chat',
                target='anthropic',
            )
            self.assistant_blocks(_blocks(projected))

    def assistant_blocks(self, blocks: list[dict[str, Any]]) -> None:
        if self.pending and self.messages and self.messages[-1]['role'] == 'user':
            self.deferred.append(('assistant', blocks))
        else:
            self.append('assistant', blocks)

    def output(self, item: ToolOutputItem | ToolSearchOutputItem) -> None:
        content = item.body if isinstance(item.body, str) else anthropic_content(item.body)
        result: dict[str, Any] = {'type': 'tool_result', 'tool_use_id': item.call_id, 'content': content}
        if item.success is not None:
            result['is_error'] = not item.success
        self.append('user', [result])
        self.pending.discard(item.call_id)
        if not self.pending:
            self.flush_context()


def _blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{'type': 'text', 'text': content}] if content else []
    if not isinstance(content, list):
        return []
    result: list[dict[str, Any]] = []
    for block in content:
        block_type = block.get('type')
        if block_type in {'input_text', 'output_text'}:
            result.append({'type': 'text', 'text': block['text']})
        elif block_type in {'input_image', 'image_url'}:
            url = block['image_url']
            result.extend(anthropic_content((ModelContentItem.input_image(
                image_url=url['url'] if isinstance(url, dict) else url,
            ),)))
        else:
            result.append(block)
    return result
