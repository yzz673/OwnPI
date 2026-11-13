from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4

from ownpi.runtime.native_tools.provider_capabilities import (
    NativeToolProviderCapabilities as NativeToolProviderCapabilities,
    NativeToolSearchOutputV1 as NativeToolSearchOutputV1,
)
from ownpi.runtime.native_tools.provider_output_attempt import (
    ProviderOutputAttempt as ProviderOutputAttempt,
    decode_prior_attempts,
    iter_history_batches as iter_history_batches,
)
from ownpi.runtime.native_tools.provider_json import (
    FrozenProviderJson,
    immutable_members as _immutable_members,
    required_mapping as _required_mapping,
    required_sequence as _required_sequence,
    string_fields,
)



class ProviderOutputItemKind(str, Enum):
    MESSAGE = 'message'
    REASONING = 'reasoning'
    TOOL_CALL = 'tool_call'
    HOSTED_TOOL_CALL = 'hosted_tool_call'
    HOSTED_TOOL_RESULT = 'hosted_tool_result'
    PROVIDER_SPECIFIC = 'provider_specific'


@dataclass(frozen=True)
class ProviderOutputItem:
    kind: ProviderOutputItemKind
    sequence_index: int
    provider_type: str
    provider_payload: FrozenProviderJson
    provider_item_id: str = ''
    provider_status: str = ''
    text: str = ''
    content_blocks: tuple[FrozenProviderJson, ...] = ()
    tool_name: str = ''
    call_id: str = ''
    request_id: str = ''
    raw_arguments: str = ''
    provider_metadata: FrozenProviderJson = FrozenProviderJson()

    def __post_init__(self) -> None:
        string_fields(self, ('provider_type', 'provider_item_id', 'provider_status', 'text',
                             'tool_name', 'call_id', 'request_id', 'raw_arguments'))
        if not isinstance(self.kind, ProviderOutputItemKind):
            raise TypeError('provider output kind must be ProviderOutputItemKind')
        if type(self.sequence_index) is not int:
            raise TypeError('provider output sequence_index must be an integer')
        _immutable_members(self.content_blocks, FrozenProviderJson, 'content_blocks')
        for name in ('provider_payload', 'provider_metadata'):
            if not isinstance(getattr(self, name), FrozenProviderJson):
                raise TypeError(f'provider output {name} must be frozen JSON')
        self.provider_payload.to_object()
        self.provider_metadata.to_object()

    def to_dict(self) -> dict[str, Any]:
        return {
            'kind': self.kind.value,
            'sequence_index': self.sequence_index,
            'provider_type': self.provider_type,
            'provider_payload': self.provider_payload.to_value(),
            'provider_item_id': self.provider_item_id,
            'provider_status': self.provider_status,
            'text': self.text,
            'content_blocks': tuple(block.to_value() for block in self.content_blocks),
            'tool_name': self.tool_name,
            'call_id': self.call_id,
            'request_id': self.request_id,
            'raw_arguments': self.raw_arguments,
            'provider_metadata': self.provider_metadata.to_value(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> 'ProviderOutputItem':
        blocks = _required_sequence(data.get('content_blocks'), 'content_blocks')
        return cls(
            kind=ProviderOutputItemKind(str(data.get('kind') or '')),
            sequence_index=data['sequence_index'],
            provider_type=data['provider_type'],
            provider_payload=FrozenProviderJson.from_value(data.get('provider_payload')),
            provider_item_id=data['provider_item_id'],
            provider_status=data['provider_status'],
            text=data['text'],
            content_blocks=tuple(FrozenProviderJson.from_value(block) for block in blocks),
            tool_name=data['tool_name'],
            call_id=data['call_id'],
            request_id=data['request_id'],
            raw_arguments=data['raw_arguments'],
            provider_metadata=FrozenProviderJson.from_value(data.get('provider_metadata')),
        )


@dataclass(frozen=True)
class ProviderOutputCompletion:
    response_id: str = ''
    status: str = ''
    stop_reason: str = ''
    usage_items: tuple[tuple[str, int], ...] = ()
    provider_metadata: FrozenProviderJson = FrozenProviderJson()
    provider_protocol: str = ''

    def __post_init__(self) -> None:
        string_fields(self, ('response_id', 'status', 'stop_reason', 'provider_protocol'))
        _immutable_members(self.usage_items, tuple, 'usage_items')
        for pair in self.usage_items:
            if len(pair) != 2 or not isinstance(pair[0], str) or type(pair[1]) is not int:
                raise TypeError('provider output usage_items requires (str, int) pairs')
        if not isinstance(self.provider_metadata, FrozenProviderJson):
            raise TypeError('provider output completion metadata must be frozen JSON')
        self.provider_metadata.to_object()

    @classmethod
    def create(
        cls,
        *,
        response_id: str = '',
        status: str = '',
        stop_reason: str = '',
        usage: dict[str, int] | None = None,
        provider_metadata: dict[str, Any] | None = None,
        provider_protocol: str = '',
    ) -> 'ProviderOutputCompletion':
        return cls(
            response_id=response_id,
            status=status,
            stop_reason=stop_reason,
            usage_items=tuple(sorted((usage or {}).items())),
            provider_metadata=FrozenProviderJson.from_value(provider_metadata or {}),
            provider_protocol=provider_protocol,
        )

    @property
    def usage(self) -> dict[str, int]:
        return dict(self.usage_items)

    @property
    def exact_usage(self) -> Any:
        """Return the untouched provider usage; normalized counters stay separate."""
        return self.provider_metadata.to_object().get('usage')

    def to_dict(self) -> dict[str, Any]:
        return {
            'response_id': self.response_id,
            'status': self.status,
            'stop_reason': self.stop_reason,
            'usage': self.usage,
            'provider_metadata': self.provider_metadata.to_value(),
            'provider_protocol': self.provider_protocol,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> 'ProviderOutputCompletion':
        usage = _required_mapping(data.get('usage'), 'usage')
        return cls.create(
            response_id=data['response_id'],
            status=data['status'],
            stop_reason=data['stop_reason'],
            usage=dict(usage),
            provider_metadata=dict(_required_mapping(data.get('provider_metadata'), 'provider_metadata')),
            provider_protocol=data['provider_protocol'],
        )


class ProviderOutputSourceKind(str, Enum):
    BLOCKING = 'blocking'
    STREAMING = 'streaming'
    SYNTHETIC = 'synthetic'


@dataclass(frozen=True)
class ProviderOutputSource:
    kind: ProviderOutputSourceKind = ProviderOutputSourceKind.SYNTHETIC
    response_payload: FrozenProviderJson | None = None
    completed_item_payloads: tuple[FrozenProviderJson, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ProviderOutputSourceKind):
            raise TypeError('provider output source kind must be ProviderOutputSourceKind')
        _immutable_members(self.completed_item_payloads, FrozenProviderJson, 'completed_item_payloads')
        if self.response_payload is not None and not isinstance(self.response_payload, FrozenProviderJson):
            raise TypeError('provider output response_payload must be frozen JSON or null')

    def to_dict(self) -> dict[str, Any]:
        return {
            'kind': self.kind.value,
            'response_payload': (
                self.response_payload.to_value()
                if self.response_payload is not None
                else None
            ),
            'completed_item_payloads': tuple(
                payload.to_value() for payload in self.completed_item_payloads
            ),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> 'ProviderOutputSource':
        payloads = _required_sequence(data.get('completed_item_payloads'), 'completed_item_payloads')
        response_payload = data.get('response_payload')
        return cls(
            kind=ProviderOutputSourceKind(data['kind']),
            response_payload=(
                FrozenProviderJson.from_value(response_payload)
                if response_payload is not None
                else None
            ),
            completed_item_payloads=tuple(
                FrozenProviderJson.from_value(payload) for payload in payloads
            ),
        )


@dataclass(frozen=True)
class ProviderOutputLifecycleRecord:
    event_type: str
    attempt: int
    response_id: str = ''
    item_id: str = ''
    item_type: str = ''
    sequence_index: int = -1
    provider_protocol: str = ''
    payload: FrozenProviderJson = FrozenProviderJson()

    def __post_init__(self) -> None:
        string_fields(self, ('event_type', 'response_id', 'item_id', 'item_type', 'provider_protocol'))
        for name in ('attempt', 'sequence_index'):
            if type(getattr(self, name)) is not int:
                raise TypeError(f'provider output {name} must be an integer')
        if not isinstance(self.payload, FrozenProviderJson):
            raise TypeError('provider output lifecycle payload must be frozen JSON')

    def to_dict(self) -> dict[str, Any]:
        return {
            'event_type': self.event_type,
            'attempt': self.attempt,
            'response_id': self.response_id,
            'item_id': self.item_id,
            'item_type': self.item_type,
            'sequence_index': self.sequence_index,
            'provider_protocol': self.provider_protocol,
            'payload': self.payload.to_value(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> 'ProviderOutputLifecycleRecord':
        return cls(
            event_type=data['event_type'],
            attempt=data['attempt'],
            response_id=data['response_id'],
            item_id=data['item_id'],
            item_type=data['item_type'],
            sequence_index=data['sequence_index'],
            provider_protocol=data['provider_protocol'],
            payload=FrozenProviderJson.from_value(data.get('payload', {})),
        )


@dataclass(frozen=True)
class ProviderOutputEnvelope:
    completed_items: tuple[ProviderOutputItem, ...] = ()
    exchange_id: str = field(default_factory=lambda: f'nex-{uuid4().hex}')
    completion: ProviderOutputCompletion = field(default_factory=ProviderOutputCompletion)
    source: ProviderOutputSource = field(default_factory=ProviderOutputSource)
    lifecycle: tuple[ProviderOutputLifecycleRecord, ...] = ()
    prior_attempts: tuple[ProviderOutputAttempt, ...] = ()

    def __post_init__(self) -> None:
        _immutable_members(self.completed_items, ProviderOutputItem, 'completed_items')
        _immutable_members(self.lifecycle, ProviderOutputLifecycleRecord, 'lifecycle')
        _immutable_members(self.prior_attempts, ProviderOutputAttempt, 'prior_attempts')
        if not isinstance(self.completion, ProviderOutputCompletion):
            raise TypeError('provider output completion must be ProviderOutputCompletion')
        if not isinstance(self.source, ProviderOutputSource):
            raise TypeError('provider output source must be ProviderOutputSource')
        string_fields(self, ('exchange_id',))

    @property
    def assistant_text(self) -> str:
        return ''.join(
            item.text
            for item in self.completed_items
            if item.kind is ProviderOutputItemKind.MESSAGE
        )

    @property
    def usage(self) -> dict[str, int]:
        return self.completion.usage

    @property
    def provider_metadata(self) -> dict[str, Any]:
        metadata = self.completion.provider_metadata.to_object()
        if self.completion.response_id:
            metadata.setdefault('response_id', self.completion.response_id)
        if self.completion.stop_reason:
            metadata.setdefault('stop_reason', self.completion.stop_reason)
        if self.completion.status:
            metadata.setdefault('status', self.completion.status)
        return metadata

    def to_dict(self) -> dict[str, Any]:
        return {
            'completed_items': tuple(item.to_dict() for item in self.completed_items),
            'exchange_id': self.exchange_id,
            'completion': self.completion.to_dict(),
            'source': self.source.to_dict(),
            'lifecycle': tuple(record.to_dict() for record in self.lifecycle),
            'prior_attempts': tuple(attempt.to_dict() for attempt in self.prior_attempts),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> 'ProviderOutputEnvelope':
        items = _required_sequence(data.get('completed_items'), 'completed_items')
        lifecycle = _required_sequence(data.get('lifecycle'), 'lifecycle')
        return cls(
            completed_items=tuple(
                ProviderOutputItem.from_dict(_required_mapping(item, 'completed item'))
                for item in items
            ),
            exchange_id=data.get('exchange_id', ''),
            completion=ProviderOutputCompletion.from_dict(
                _required_mapping(data.get('completion'), 'completion')
            ),
            source=ProviderOutputSource.from_dict(
                _required_mapping(data.get('source'), 'source')
            ),
            lifecycle=tuple(
                ProviderOutputLifecycleRecord.from_dict(
                    _required_mapping(record, 'lifecycle record')
                )
                for record in lifecycle
            ),
            prior_attempts=decode_prior_attempts(data.get('prior_attempts', ())),
        )
