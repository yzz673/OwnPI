"""Provider wire serializers over the normalized live-history request view."""

from __future__ import annotations

from typing import Any

from ownpi.runtime.native_tools.adapters.openai import OpenAINativeToolAdapter

from ownpi.runtime.native_tools.history_items import HistoryMessage, ToolOutputItem, ToolSearchOutputItem
from ownpi.runtime.native_tools.provider import ProviderOutputItem, ProviderOutputItemKind
from ownpi.runtime.native_tools.provider_content_projection import (
    history_prefix_messages,
    openai_responses_content,
    provider_message_content,
)
from ownpi.runtime.native_tools.provider_history_anthropic import (
    anthropic_history_messages as anthropic_history_messages,
    anthropic_system_text as anthropic_system_text,
)
from ownpi.runtime.native_tools.provider_history_chat import (
    openai_chat_history_messages as openai_chat_history_messages,
)
from ownpi.runtime.native_tools.provider_input import NativeHistoryProviderInput


def openai_responses_history_input(
    value: NativeHistoryProviderInput,
    *,
    prefix_messages: list[dict[str, Any]] | None = None,
    native_tool_strict: bool = False,
) -> list[dict[str, Any]]:
    projected = history_prefix_messages(value, prefix_messages)
    for entry in value.items:
        item = entry.item
        if isinstance(item, HistoryMessage):
            projected.append({'role': item.role, 'content': item.text})
        elif isinstance(item, ToolSearchOutputItem) and item.kind == 'tool_search_output':
            projected.append({
                'type': 'tool_search_output', 'call_id': item.call_id,
                'status': item.status, 'execution': item.execution,
                'tools': OpenAINativeToolAdapter().build_search_output_tools(
                    (tool.to_object() for tool in item.tools), strict=native_tool_strict,
                ),
            })
        elif isinstance(item, (ToolOutputItem, ToolSearchOutputItem)):
            projected.append({
                'type': 'function_call_output', 'call_id': item.call_id,
                'output': item.body if isinstance(item.body, str)
                else openai_responses_content(item.body),
            })
        else:
            payload = _responses_provider_item(item)
            if payload is not None:
                projected.append(payload)
    return projected


def _responses_provider_item(item: ProviderOutputItem) -> dict[str, Any] | None:
    payload = item.provider_payload.to_object()
    item_type = payload.get('type')
    # Replay known input items, not arbitrary output-only or internal fields.
    fields = {
        'message': ('id', 'role', 'content', 'phase'),
        'reasoning': ('id', 'summary', 'content', 'encrypted_content'),
        'function_call': ('id', 'call_id', 'name', 'namespace', 'arguments'),
        'function_call_output': ('id', 'call_id', 'name', 'namespace', 'output'),
        'tool_search_call': ('id', 'call_id', 'status', 'execution', 'arguments'),
        'tool_search_output': ('id', 'call_id', 'status', 'execution', 'tools'),
        'web_search_call': ('id', 'status', 'action'),
        'file_search_call': ('id', 'status', 'queries', 'results'),
        'code_interpreter_call': ('id', 'status', 'code', 'container_id', 'outputs'),
        'image_generation_call': ('id', 'status', 'revised_prompt', 'result'),
        'compaction': ('id', 'encrypted_content'),
    }
    if item_type in fields:
        result = {'type': item_type, **{
            key: payload[key] for key in fields[item_type] if key in payload
        }}
        if item_type == 'message' and result.get('phase') not in ('commentary', 'final_answer'):
            result.pop('phase', None)
        if item_type == 'message':
            result['content'] = provider_message_content(
                result.get('content'), source='openai_responses', target='openai_responses',
            )
            if not result['content']:
                return None
        # Preserve provider associations; only legacy, non-prefixed IDs leave the wire copy.
        item_id = result.get('id')
        prefix, _, suffix = item_id.partition('_') if isinstance(item_id, str) else ('', '', '')
        if not prefix or not suffix:
            result.pop('id', None)
        return result
    if item.kind is ProviderOutputItemKind.TOOL_CALL and item.provider_type in {'function', 'tool_use'}:
        return {
            'type': 'function_call', 'call_id': item.call_id,
            'name': item.tool_name, 'arguments': item.raw_arguments,
        }
    if item.kind is ProviderOutputItemKind.MESSAGE:
        content = provider_message_content(
            payload.get('content', item.text), source='openai_chat', target='openai_responses',
        )
        return {'role': 'assistant', 'content': content} if content else None
    return None
