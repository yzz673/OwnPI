from __future__ import annotations

from dataclasses import dataclass, replace
from contextlib import contextmanager
from typing import Any, Iterator

from ownpi.runtime.native_tools.provider import ProviderOutputItem, ProviderOutputLifecycleRecord
from ownpi.runtime.native_tools.provider_json import FrozenProviderJson, required_mapping, required_sequence
from ownpi.runtime.native_tools.provider_output_attempt import (
    ProviderOutputAttempt,
    decode_prior_attempts,
)


@contextmanager
def preserve_provider_parse_source(payload: dict[str, Any]) -> Iterator[None]:
    """Attach immutable source evidence without changing the parser exception type."""
    source = FrozenProviderJson.from_value(payload)
    try:
        yield
    except Exception as error:
        setattr(error, 'provider_output_source', source)
        raise


@dataclass(frozen=True)
class ProviderOutputFailureFacts:
    """Incomplete output evidence; never a successfully consumable response."""

    exchange_id: str
    completed_items: tuple[ProviderOutputItem, ...]
    lifecycle: tuple[ProviderOutputLifecycleRecord, ...]
    parser_source: FrozenProviderJson | None = None
    prior_attempts: tuple[ProviderOutputAttempt, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.exchange_id, str):
            raise TypeError('provider output failure exchange_id must be a string')
        if self.parser_source is not None and not isinstance(self.parser_source, FrozenProviderJson):
            raise TypeError('provider output failure parser_source must be frozen JSON or null')
        for name, member_type in (
            ('completed_items', ProviderOutputItem),
            ('lifecycle', ProviderOutputLifecycleRecord),
            ('prior_attempts', ProviderOutputAttempt),
        ):
            members = getattr(self, name)
            if not isinstance(members, tuple) or any(not isinstance(value, member_type) for value in members):
                raise TypeError(f'provider output failure {name} must be an immutable tuple')

    def to_dict(self) -> dict[str, Any]:
        return {
            'exchange_id': self.exchange_id,
            'completed_items': [item.to_dict() for item in self.completed_items],
            'lifecycle': [record.to_dict() for record in self.lifecycle],
            'parser_source': self.parser_source.to_value() if self.parser_source is not None else None,
            'prior_attempts': [attempt.to_dict() for attempt in self.prior_attempts],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> 'ProviderOutputFailureFacts':
        return cls(
            exchange_id=value['exchange_id'],
            completed_items=tuple(
                ProviderOutputItem.from_dict(required_mapping(item, 'completed item'))
                for item in required_sequence(value['completed_items'], 'completed_items')
            ),
            lifecycle=tuple(
                ProviderOutputLifecycleRecord.from_dict(required_mapping(record, 'lifecycle record'))
                for record in required_sequence(value['lifecycle'], 'lifecycle')
            ),
            parser_source=(FrozenProviderJson.from_value(value['parser_source'])
                           if value.get('parser_source') is not None else None),
            prior_attempts=decode_prior_attempts(value.get('prior_attempts', ())),
        )


def preserve_attached_failure_facts(
    observed: ProviderOutputFailureFacts, error: BaseException, *, attempt: int,
) -> ProviderOutputFailureFacts:
    """Retain facts delivered directly by an inner provider/bridge exception."""
    attached = getattr(error, 'provider_output_failure', None)
    if not isinstance(attached, ProviderOutputFailureFacts):
        return observed
    # Only collapse a repeated handoff of the same exchange, never provider IDs
    # or identical content from distinct attempts.
    attached_exchanges = {batch.exchange_id for batch in attached.prior_attempts}
    attached_exchanges.add(attached.exchange_id)
    preceding = tuple(
        batch for batch in observed.prior_attempts
        if batch.exchange_id not in attached_exchanges
    )
    if observed.completed_items and observed.exchange_id not in attached_exchanges:
        preceding += (ProviderOutputAttempt(
            observed.exchange_id, attempt, observed.completed_items,
        ),)
    if not preceding:
        return attached
    return replace(attached, prior_attempts=(*preceding, *attached.prior_attempts))
