"""Lossless history envelopes with provider payloads separate from metadata."""

from __future__ import annotations

from dataclasses import MISSING, asdict, dataclass, fields
from datetime import datetime, timezone
from typing import Any
import math

from ownpi.runtime.model_content import ModelContentItem
from ownpi.runtime.native_tools.history_items import (
    HistoryEntry, HistoryMessage, ToolOutputItem, ToolSearchOutputItem,
)
from ownpi.runtime.native_tools.provider import ProviderOutputItem, ProviderOutputItemKind
from ownpi.runtime.native_tools.provider_json import FrozenProviderJson
from ownpi.runtime.rollout.business_events import BUSINESS_EVENT_TYPES
from ownpi.runtime.tools.result_projection_utils import FilePageProjection


@dataclass(frozen=True)
class RolloutRecord:
    type: str
    payload: FrozenProviderJson
    metadata: FrozenProviderJson = FrozenProviderJson()
    timestamp: str = ''

    @classmethod
    def create(cls, kind: str, payload: dict[str, Any], metadata: dict[str, Any] | None = None) -> RolloutRecord:
        return cls(kind, FrozenProviderJson.from_value(payload),
                   FrozenProviderJson.from_value(metadata or {}),
                   datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        value = {'timestamp': self.timestamp, 'type': self.type, 'payload': self.payload.to_object()}
        if self.metadata.to_object():
            value['metadata'] = self.metadata.to_object()
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RolloutRecord:
        validate_record_envelope(value)
        return cls(value['type'], FrozenProviderJson.from_value(value['payload']),
                   FrozenProviderJson.from_value(value.get('metadata', {})), value['timestamp'])


def validate_record_envelope(value: dict[str, Any]) -> None:
    if not isinstance(value, dict) or not isinstance(value.get('type'), str):
        raise ValueError('Rollout line requires an object and string type')
    if value['type'] not in RECORD_TYPES:
        raise ValueError('Unknown rollout record type')
    if not isinstance(value.get('payload'), dict) or not isinstance(value.get('metadata', {}), dict):
        raise ValueError('Rollout payload and metadata must be objects')
    if not isinstance(value.get('timestamp'), str):
        raise ValueError('Rollout timestamp must be a string')
    if value['type'] == 'session_meta':
        identity = value['payload'].get('id')
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError('Session metadata requires a nonempty conversation ID')


RECORD_TYPES = frozenset({'session_meta', 'response_item', 'compacted', 'turn_context',
                          'world_state', 'event_msg', 'inter_agent_communication'})


def validate_recovery_record(record: RolloutRecord) -> None:
    """Validate consumed fields at the line boundary, not the whole lifecycle."""
    validate_raw_record(record.to_dict())


def _json_finite(value: Any) -> None:
    if isinstance(value, dict):
        for member in value.values():
            _json_finite(member)
    elif isinstance(value, list):
        for member in value:
            _json_finite(member)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError('provider JSON cannot contain nonfinite numbers')


def _constructor_fields(cls: type, value: dict[str, Any]) -> None:
    if not isinstance(value, dict):
        raise TypeError('Constructor fields must be an object')
    allowed = {field.name for field in fields(cls) if field.init}
    required = {field.name for field in fields(cls)
                if field.init and field.default is MISSING and field.default_factory is MISSING}
    if value.keys() - allowed or required - value.keys():
        raise TypeError(f'Invalid {cls.__name__} fields')


def validate_entry_fields(payload: dict[str, Any], meta: dict[str, Any]) -> None:
    """Check decoder inputs without freezing or constructing large history items."""
    variant = meta['variant']
    for key in ('exchange_id', 'output_sequence_index', 'preserve_output', 'page', 'projection_source'):
        meta[key]
    if meta['page'] is not None:
        _constructor_fields(FilePageProjection, meta['page'])
    if variant == 'provider':
        values = {**_provider_defaults(payload), **meta['adapter']}
        ProviderOutputItemKind(meta['kind'])
        if type(meta['sequence_index']) is not int:
            raise TypeError('provider output sequence_index must be an integer')
        for key in ('provider_item_id', 'provider_status', 'text', 'tool_name',
                    'call_id', 'request_id', 'raw_arguments'):
            if not isinstance(values[key], str):
                raise TypeError(f'provider output {key} must be a string')
        if not isinstance(meta['provider_type'], str):
            raise TypeError('provider output provider_type must be a string')
        if not isinstance(values['content_blocks'], (list, tuple)):
            raise TypeError('provider output content_blocks must be an array')
        if not isinstance(values['provider_metadata'], dict):
            raise TypeError('provider output provider_metadata must be an object')
    elif variant == 'message':
        for key in ('role', 'text', 'message_metadata'):
            payload[key]
    elif variant == 'tool':
        for key in ('tool_name', 'call_id', 'request_id', 'success'):
            payload[key]
        if not isinstance(payload['output'], str):
            for member in payload['output']:
                _constructor_fields(ModelContentItem, member)
    elif variant == 'search':
        values = {key: value for key, value in payload.items() if key not in {'type', 'tools'}}
        _constructor_fields(ToolSearchOutputItem, {**values, 'tools': payload['tools']})
        iter(payload['tools'])
    else:
        raise ValueError(f'Unknown history variant: {variant}')


def validate_raw_record(value: dict[str, Any]) -> None:
    validate_record_envelope(value)
    payload, kind = value['payload'], value['type']
    _json_finite(payload)
    _json_finite(value.get('metadata', {}))
    if kind == 'response_item':
        validate_entry_fields(payload, value.get('metadata', {}))
    elif kind == 'compacted':
        for entry, meta in zip(payload['replacement_history'],
                               payload['replacement_history_metadata'], strict=True):
            validate_record_envelope({'type': 'response_item', 'payload': entry,
                                      'metadata': meta, 'timestamp': ''})
            validate_entry_fields(entry, meta)
    elif kind == 'inter_agent_communication':
        if any(not isinstance(payload.get(key), str) for key in ('source', 'target', 'text', 'kind')):
            raise ValueError('Invalid communication fields')
    elif kind == 'turn_context':
        for key in ('model', 'effective_permissions'):
            if payload.get(key) is not None and not isinstance(payload[key], dict):
                raise ValueError(f'Invalid context {key}')
        if 'tools' in payload and not isinstance(payload['tools'], list):
            raise ValueError('Invalid context tools')
        if 'history_generation' in payload and (type(payload['history_generation']) is not int
                                                or payload['history_generation'] < 0):
            raise ValueError('Invalid context generation')
    elif kind == 'event_msg':
        kind = payload.get('type')
        if not isinstance(kind, str):
            raise ValueError('Invalid event type')
        if kind == 'protected_output_consumed':
            ids = payload.get('item_ids')
            if not isinstance(ids, list) or any(not isinstance(value, str) for value in ids):
                raise ValueError('Invalid consumed item IDs')
        elif kind == 'token_count':
            for key in ('usage', 'exact_usage'):
                if payload.get(key) is not None and not isinstance(payload[key], dict):
                    raise ValueError(f'Invalid {key}')
            for counter in (payload.get('usage') or {}).values():
                if type(counter) is not int or counter < 0:
                    raise ValueError('Invalid usage counter')
            for key in ('exchange_id', 'purpose', 'provider_protocol'):
                if key in payload and not isinstance(payload[key], str):
                    raise ValueError(f'Invalid usage {key}')
    if value['type'] in {'turn_context', 'event_msg'}:
        if 'turn_id' in payload and not isinstance(payload['turn_id'], str):
            raise ValueError('Invalid turn ID')


PERSISTED_PROVIDER_SPECIFIC_TYPES = frozenset({
    'message', 'agent_message', 'reasoning', 'function_call', 'function_call_output',
    'custom_tool_call', 'custom_tool_call_output', 'local_shell_call', 'tool_search_call',
    'tool_search_output', 'web_search_call', 'image_generation_call', 'compaction', 'context_compaction',
})
PERSISTED_RESPONSE_KINDS = frozenset({
    ProviderOutputItemKind.MESSAGE.value, ProviderOutputItemKind.REASONING.value,
    ProviderOutputItemKind.TOOL_CALL.value, ProviderOutputItemKind.HOSTED_TOOL_CALL.value,
    ProviderOutputItemKind.HOSTED_TOOL_RESULT.value,
})
EXCLUDED_RESPONSE_TYPES = frozenset({'additional_tools', 'compaction_trigger', 'unknown', 'other', ''})
PERSISTED_EVENTS = frozenset({'turn_started', 'turn_complete', 'turn_aborted', 'token_count',
                              'protected_output_consumed', 'thread_spawned'}) | BUSINESS_EVENT_TYPES


def should_persist(record: RolloutRecord) -> bool:
    if record.type == 'response_item':
        metadata = record.metadata.to_object()
        if metadata.get('variant') != 'provider':
            return True
        provider_type = metadata.get('provider_type')
        if provider_type in EXCLUDED_RESPONSE_TYPES:
            return False
        kind = metadata.get('kind')
        # Adapter semantics span protocols; opaque facts still require recognition.
        return kind in PERSISTED_RESPONSE_KINDS or (
            kind == ProviderOutputItemKind.PROVIDER_SPECIFIC.value
            and provider_type in PERSISTED_PROVIDER_SPECIFIC_TYPES
        )
    if record.type == 'event_msg':
        return record.payload.to_object().get('type') in PERSISTED_EVENTS
    return record.type in RECORD_TYPES


def _provider_defaults(payload: dict[str, Any]) -> dict[str, Any]:
    content = payload.get('content')
    blocks = content if isinstance(content, list) and all(isinstance(v, dict) for v in content) else []
    text = content if isinstance(content, str) else ''.join(str(v.get('text') or '') for v in blocks)
    return {
        'provider_item_id': payload.get('id', ''), 'provider_status': payload.get('status', ''),
        'text': text, 'content_blocks': blocks, 'tool_name': payload.get('name', ''),
        'call_id': payload.get('call_id', ''), 'request_id': '',
        'raw_arguments': payload.get('arguments', ''), 'provider_metadata': {},
    }


def encode_entry(entry: HistoryEntry, *, item_id: str = '') -> RolloutRecord:
    item = entry.item
    metadata: dict[str, Any] = {
        'item_id': item_id, 'exchange_id': entry.exchange_id, 'turn_id': entry.turn_id,
        'output_sequence_index': entry.output_sequence_index, 'preserve_output': entry.preserve_output,
        'page': asdict(entry.page) if entry.page is not None else None,
        'projection_source': entry.projection_source.to_object() if entry.projection_source is not None else None,
    }
    if isinstance(item, ProviderOutputItem):
        payload = item.provider_payload.to_object()
        fields = item.to_dict()
        defaults = _provider_defaults(payload)
        # Store only adapter facts not deterministically available in the raw payload.
        overrides = {key: value for key, value in fields.items() if key in defaults
                     and (list(value) if key == 'content_blocks' else value) != defaults[key]}
        metadata.update(variant='provider', provider_type=item.provider_type,
                        kind=item.kind.value, sequence_index=item.sequence_index, adapter=overrides)
    elif isinstance(item, HistoryMessage):
        payload = {'type': 'message', 'role': item.role, 'text': item.text,
                   'message_metadata': item.metadata.to_object()}
        metadata['variant'] = 'message'
    elif isinstance(item, ToolOutputItem):
        payload = {'type': item.kind, 'tool_name': item.tool_name, 'call_id': item.call_id,
                   'request_id': item.request_id, 'success': item.success,
                   'output': item.body if isinstance(item.body, str) else [asdict(v) for v in item.body]}
        metadata['variant'] = 'tool'
    elif isinstance(item, ToolSearchOutputItem):
        payload = {key: getattr(item, key) for key in (
            'tool_name', 'call_id', 'request_id', 'success', 'call_type', 'receipt_ref',
            'body', 'status', 'execution',
        )}
        payload.update(type=item.kind, tools=[tool.to_object() for tool in item.tools])
        metadata['variant'] = 'search'
    else:
        raise TypeError(f'Unsupported history item: {type(item).__name__}')
    return RolloutRecord.create('response_item', payload, metadata)


def decode_entry(record: RolloutRecord) -> HistoryEntry:
    payload, meta = record.payload.to_object(), record.metadata.to_object()
    validate_entry_fields(payload, meta)
    variant = meta['variant']
    item: ProviderOutputItem | HistoryMessage | ToolOutputItem | ToolSearchOutputItem
    if variant == 'provider':
        fields = {**_provider_defaults(payload), **meta['adapter']}
        item = ProviderOutputItem.from_dict({
            **fields, 'provider_payload': payload, 'kind': ProviderOutputItemKind(meta['kind']).value,
            'provider_type': meta['provider_type'], 'sequence_index': meta['sequence_index'],
        })
    elif variant == 'message':
        item = HistoryMessage(payload['role'], payload['text'], FrozenProviderJson.from_value(payload['message_metadata']))
    elif variant == 'tool':
        body = payload['output']
        item = ToolOutputItem(payload['tool_name'], payload['call_id'], payload['request_id'],
                              body if isinstance(body, str) else tuple(ModelContentItem(**v) for v in body),
                              payload['success'])
    elif variant == 'search':
        fields = {key: value for key, value in payload.items() if key not in {'type', 'tools'}}
        item = ToolSearchOutputItem(**fields, tools=tuple(FrozenProviderJson.from_value(v) for v in payload['tools']))
    else:
        raise ValueError(f'Unknown history variant: {variant}')
    return HistoryEntry(item, meta['exchange_id'], meta['output_sequence_index'], meta['preserve_output'],
                        FilePageProjection(**meta['page']) if meta['page'] is not None else None,
                        turn_id=meta.get('turn_id', ''),
                        projection_source=(FrozenProviderJson.from_value(meta['projection_source'])
                                           if meta['projection_source'] is not None else None))


def compacted_record(entries: tuple[RolloutRecord, ...]) -> RolloutRecord:
    return RolloutRecord.create('compacted', {
        'replacement_history': [entry.payload.to_object() for entry in entries],
        'replacement_history_metadata': [entry.metadata.to_object() for entry in entries],
    })


def decode_replacement(record: RolloutRecord) -> tuple[HistoryEntry, ...]:
    payload = record.payload.to_object()
    return tuple(decode_entry(RolloutRecord.create('response_item', value, meta))
                 for value, meta in zip(payload['replacement_history'],
                                        payload['replacement_history_metadata'], strict=True))
