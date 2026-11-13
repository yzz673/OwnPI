from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from enum import Enum
from collections.abc import Iterable
from typing import Any, Mapping

from ownpi.runtime.memory.map_contracts import MemoryDomain


MEMORY_CONSUMPTION_RECEIPT_SCHEMA_VERSION = 1
MAX_MEMORY_LOGICAL_REF_LENGTH = 320
MAX_MEMORY_INVOCATION_ID_LENGTH = 160
MAX_MEMORY_FALLBACK_LENGTH = 120
_LOGICAL_REF_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:@+#=-]*$')


class MemoryConsumptionOperation(str, Enum):
    SEARCH = 'search'
    READ = 'read'


class MemoryConsumptionStatus(str, Enum):
    SUCCESS = 'success'
    EMPTY = 'empty'
    UNAVAILABLE = 'unavailable'
    PARTIAL = 'partial'
    ERROR = 'error'


@dataclass(frozen=True)
class MemoryConsumptionReceipt:
    domain: MemoryDomain
    invocation_id: str
    operation: MemoryConsumptionOperation
    status: MemoryConsumptionStatus
    logical_refs: tuple[str, ...]
    matched_count: int
    returned_count: int
    fallback: str = ''
    truncated: bool = False
    schema_version: int = MEMORY_CONSUMPTION_RECEIPT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != MEMORY_CONSUMPTION_RECEIPT_SCHEMA_VERSION
        ):
            raise ValueError('unsupported memory consumption receipt schema version')
        if not isinstance(self.domain, MemoryDomain):
            raise TypeError('memory consumption domain must use MemoryDomain')
        if not isinstance(self.operation, MemoryConsumptionOperation):
            raise TypeError('memory consumption operation must use MemoryConsumptionOperation')
        if not isinstance(self.status, MemoryConsumptionStatus):
            raise TypeError('memory consumption status must use MemoryConsumptionStatus')
        _bounded_scalar(
            self.invocation_id,
            field_name='invocation_id',
            max_length=MAX_MEMORY_INVOCATION_ID_LENGTH,
            required=True,
        )
        _bounded_scalar(
            self.fallback,
            field_name='fallback',
            max_length=MAX_MEMORY_FALLBACK_LENGTH,
            required=False,
        )
        if not isinstance(self.logical_refs, tuple):
            raise TypeError('memory consumption logical refs must use a tuple')
        seen: set[str] = set()
        for logical_ref in self.logical_refs:
            _bounded_scalar(
                logical_ref,
                field_name='logical_ref',
                max_length=MAX_MEMORY_LOGICAL_REF_LENGTH,
                required=True,
            )
            if _LOGICAL_REF_PATTERN.fullmatch(logical_ref) is None:
                raise ValueError('logical_ref must be an opaque canonical reference')
            if logical_ref in seen:
                raise ValueError('memory consumption logical refs must be unique')
            seen.add(logical_ref)
        for field_name in ('matched_count', 'returned_count'):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f'{field_name} must be a non-negative integer')
        if self.returned_count > self.matched_count:
            raise ValueError('returned_count cannot exceed matched_count')
        if self.returned_count != len(self.logical_refs):
            raise ValueError('returned_count must equal the number of logical refs')
        if self.status == MemoryConsumptionStatus.SUCCESS and self.returned_count == 0:
            raise ValueError('successful memory consumption must return at least one logical ref')
        if self.status in {
            MemoryConsumptionStatus.EMPTY,
            MemoryConsumptionStatus.UNAVAILABLE,
            MemoryConsumptionStatus.ERROR,
        } and self.returned_count:
            raise ValueError(f'{self.status.value} memory consumption cannot return logical refs')
        if isinstance(self.truncated, bool) is False:
            raise TypeError('truncated must be a boolean')

    def to_dict(self) -> dict[str, Any]:
        return {
            'schema_version': self.schema_version,
            'domain': self.domain.value,
            'invocation_id': self.invocation_id,
            'operation': self.operation.value,
            'status': self.status.value,
            'logical_refs': list(self.logical_refs),
            'matched_count': self.matched_count,
            'returned_count': self.returned_count,
            'fallback': self.fallback,
            'truncated': self.truncated,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> 'MemoryConsumptionReceipt':
        try:
            raw_refs = data.get('logical_refs', ())
            if not isinstance(raw_refs, list | tuple):
                raise TypeError('logical_refs must use a list or tuple')
            return cls(
                schema_version=data.get('schema_version', 0),
                domain=MemoryDomain(data.get('domain', '')),
                invocation_id=data.get('invocation_id', ''),
                operation=MemoryConsumptionOperation(data.get('operation', '')),
                status=MemoryConsumptionStatus(data.get('status', '')),
                logical_refs=tuple(raw_refs),
                matched_count=data.get('matched_count', -1),
                returned_count=data.get('returned_count', -1),
                fallback=data.get('fallback', ''),
                truncated=data.get('truncated', False),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError('invalid memory consumption receipt') from exc


def memory_consumption_context_effects(
    receipt: MemoryConsumptionReceipt,
) -> dict[str, Any]:
    return {'memory_consumption': receipt.to_dict()}


def memory_consumption_receipt_from_metadata(
    metadata: Mapping[str, Any] | None,
) -> MemoryConsumptionReceipt | None:
    if not isinstance(metadata, Mapping):
        return None
    effects = metadata.get('context_effects')
    if not isinstance(effects, Mapping):
        return None
    raw_receipt = effects.get('memory_consumption')
    if not isinstance(raw_receipt, Mapping):
        return None
    try:
        return MemoryConsumptionReceipt.from_dict(raw_receipt)
    except ValueError:
        return None


def bind_memory_consumption_receipt(
    receipt: MemoryConsumptionReceipt,
    *,
    request_id: str,
) -> MemoryConsumptionReceipt:
    return replace(receipt, invocation_id=str(request_id or '').strip())


def canonicalize_memory_consumption_effects(
    effects: Mapping[str, Any] | None,
    *,
    receipt: MemoryConsumptionReceipt | None,
) -> dict[str, Any]:
    canonical = dict(effects or {})
    if receipt is None:
        canonical.pop('memory_consumption', None)
    else:
        canonical['memory_consumption'] = receipt.to_dict()
    return canonical


def with_memory_consumption_receipt(
    metadata: Mapping[str, Any] | None,
    receipt: MemoryConsumptionReceipt,
) -> dict[str, Any]:
    revised = dict(metadata or {})
    existing_effects = revised.get('context_effects')
    context_effects = dict(existing_effects) if isinstance(existing_effects, Mapping) else {}
    context_effects.update(memory_consumption_context_effects(receipt))
    revised['context_effects'] = context_effects
    return revised


def collect_memory_consumption_receipts(
    tool_call_history: Iterable[Any],
    *,
    domain: MemoryDomain,
) -> tuple[MemoryConsumptionReceipt, ...]:
    receipts: list[MemoryConsumptionReceipt] = []
    seen_invocations: set[str] = set()
    for item in tool_call_history:
        effects = getattr(item, 'context_effects', None)
        if not isinstance(effects, Mapping) and isinstance(item, Mapping):
            effects = item.get('context_effects')
        if not isinstance(effects, Mapping):
            continue
        raw_receipt = effects.get('memory_consumption')
        if not isinstance(raw_receipt, Mapping):
            continue
        try:
            receipt = MemoryConsumptionReceipt.from_dict(raw_receipt)
        except ValueError:
            continue
        if receipt.domain != domain or receipt.invocation_id in seen_invocations:
            continue
        seen_invocations.add(receipt.invocation_id)
        receipts.append(receipt)
    return tuple(receipts)


def _bounded_scalar(
    value: object,
    *,
    field_name: str,
    max_length: int,
    required: bool,
) -> None:
    if not isinstance(value, str):
        raise TypeError(f'{field_name} must be a string')
    if required and not value.strip():
        raise ValueError(f'{field_name} must be non-empty')
    if len(value) > max_length:
        raise ValueError(f'{field_name} exceeds maximum length')
    if any(unicodedata.category(char).startswith('C') for char in value):
        raise ValueError(f'{field_name} cannot contain control characters')
    if any(unicodedata.category(char) in {'Zl', 'Zp'} for char in value):
        raise ValueError(f'{field_name} cannot contain line or paragraph separators')
