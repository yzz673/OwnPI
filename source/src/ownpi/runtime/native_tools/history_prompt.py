"""Prompt-only normalization of immutable history, without replay or execution."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from typing import Any

from ownpi.runtime.model_content import ModelContentItem
from ownpi.runtime.native_tools.history_items import HistoryEntry, ToolOutputItem, ToolSearchOutputItem
from ownpi.runtime.native_tools.provider import ProviderOutputItem, ProviderOutputItemKind
from ownpi.runtime.native_tools.provider_json import FrozenProviderJson


def call_key(entry: HistoryEntry) -> tuple[str, str] | None:
    """Use the actual provider protocol, never infer it from a tool name."""
    item = entry.item
    if not isinstance(item, ProviderOutputItem):
        return None
    payload = item.provider_payload.to_object()
    if item.provider_type in {'function_call', 'function', 'tool_use'}:
        id_field = 'call_id' if item.provider_type == 'function_call' else 'id'
        call_id = payload.get(id_field, item.call_id or None)
        return ('function', call_id) if isinstance(call_id, str) else None
    if item.provider_type == 'tool_search_call' and payload.get('execution') != 'server':
        call_id = payload.get('call_id', item.call_id or None)
        return ('search', call_id) if isinstance(call_id, str) else None
    return None


def output_key(entry: HistoryEntry) -> tuple[str, str] | None:
    item = entry.item
    if isinstance(item, (ToolOutputItem, ToolSearchOutputItem)):
        return ('search' if item.kind == 'tool_search_output' else 'function', item.call_id)
    if isinstance(item, ProviderOutputItem):
        payload = item.provider_payload.to_object()
        call_id = payload.get('call_id', item.call_id or None)
        if item.provider_type in {'function_call_output', 'tool_search_output'} and isinstance(call_id, str):
            return ('search' if item.provider_type == 'tool_search_output' else 'function', call_id)
    return None


def _server_output(entry: HistoryEntry) -> bool:
    item = entry.item
    if isinstance(item, ToolSearchOutputItem):
        return item.kind == 'tool_search_output' and item.execution == 'server'
    return isinstance(item, ProviderOutputItem) and item.provider_type == 'tool_search_output' and (
        item.provider_payload.to_object().get('execution') == 'server'
    )


def for_prompt(
    entries: Iterable[HistoryEntry], input_modalities: Iterable[str],
) -> tuple[HistoryEntry, ...]:
    snapshot = tuple(entries)
    modalities = frozenset(input_modalities)
    calls = {key for entry in snapshot if (key := call_key(entry)) is not None}
    outputs = {key for entry in snapshot if (key := output_key(entry)) is not None}
    result = []
    for entry in snapshot:
        key = output_key(entry)
        if key is not None and key not in calls and not _server_output(entry):
            continue
        result.append(filter_modalities(entry, modalities))
        key = call_key(entry)
        if key is not None and key not in outputs:
            result.append(_missing_output(entry, key))
    return tuple(result)


def _missing_output(entry: HistoryEntry, key: tuple[str, str]) -> HistoryEntry:
    item = entry.item
    assert isinstance(item, ProviderOutputItem)
    output: ToolOutputItem | ToolSearchOutputItem
    if key[0] == 'search':
        output = ToolSearchOutputItem(
            item.tool_name, key[1], item.request_id, (), False, 'tool_search_call',
        )
    else:
        output = ToolOutputItem(item.tool_name, key[1], item.request_id, 'aborted', False)
    return HistoryEntry(output, entry.exchange_id, entry.output_sequence_index)


def filter_modalities(entry: HistoryEntry, modalities: frozenset[str]) -> HistoryEntry:
    if {'image', 'audio'} <= modalities:
        return entry
    item = entry.item
    if isinstance(item, ToolOutputItem) and isinstance(item.body, tuple):
        body = tuple(
            ModelContentItem.input_text(_omitted('image'))
            if block.type == 'input_image' and 'image' not in modalities else block
            for block in item.body
        )
        return replace(entry, item=replace(item, body=body)) if body != item.body else entry
    if not isinstance(item, ProviderOutputItem):
        return entry
    payload = item.provider_payload.to_object()
    blocks = item.content_blocks
    is_message = item.kind == ProviderOutputItemKind.MESSAGE or item.provider_type == 'message'
    if is_message or item.provider_type in {'function_call_output', 'custom_tool_call_output'}:
        field = 'content' if is_message else 'output'
        text_type = 'text' if is_message and payload.get('type') != 'message' else 'input_text'
        if field in payload:
            payload[field] = _filter_content(payload[field], modalities, text_type)
        blocks = tuple(FrozenProviderJson.from_value(_filter_block(block.to_object(), modalities, text_type))
                       for block in blocks)
    if item.provider_type == 'image_generation_call' and 'image' not in modalities and 'result' in payload:
        payload['result'] = ''
    frozen = FrozenProviderJson.from_value(payload)
    if frozen == item.provider_payload and blocks == item.content_blocks:
        return entry
    return replace(entry, item=replace(item, provider_payload=frozen, content_blocks=blocks))


def _filter_content(content: Any, modalities: frozenset[str], text_type: str) -> Any:
    if isinstance(content, list):
        return [_filter_block(block, modalities, text_type) if isinstance(block, dict) else block
                for block in content]
    return content


def _filter_block(block: dict[str, Any], modalities: frozenset[str], text_type: str) -> dict[str, Any]:
    kind = block.get('type')
    modality = {'input_image': 'image', 'image_url': 'image', 'image': 'image',
                'input_audio': 'audio', 'audio': 'audio'}.get(kind) if isinstance(kind, str) else None
    if modality is not None and modality not in modalities:
        return {'type': text_type, 'text': _omitted(modality)}
    if kind == 'tool_result' and 'content' in block:
        return {**block, 'content': _filter_content(block['content'], modalities, 'text')}
    return block


def _omitted(modality: str) -> str:
    return f'{modality} content omitted because you do not support {modality} input'
