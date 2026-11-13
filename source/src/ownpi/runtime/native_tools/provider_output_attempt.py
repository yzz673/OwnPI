from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterator, Mapping

from ownpi.runtime.native_tools.provider_json import (
    immutable_members,
    required_mapping,
    required_sequence,
    string_fields,
)

if TYPE_CHECKING:
    from ownpi.runtime.native_tools.provider import ProviderOutputEnvelope, ProviderOutputItem
    from ownpi.runtime.native_tools.provider_output_failure import ProviderOutputFailureFacts


@dataclass(frozen=True)
class ProviderOutputAttempt:
    """Completed facts from a prior attempt, never an execution candidate."""

    exchange_id: str
    attempt: int
    completed_items: tuple[ProviderOutputItem, ...]

    def __post_init__(self) -> None:
        from ownpi.runtime.native_tools.provider import ProviderOutputItem

        string_fields(self, ('exchange_id',))
        if type(self.attempt) is not int:
            raise TypeError('provider output attempt must be an integer')
        immutable_members(self.completed_items, ProviderOutputItem, 'completed_items')

    def to_dict(self) -> dict[str, Any]:
        return {
            'exchange_id': self.exchange_id,
            'attempt': self.attempt,
            'completed_items': tuple(item.to_dict() for item in self.completed_items),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ProviderOutputAttempt:
        from ownpi.runtime.native_tools.provider import ProviderOutputItem

        return cls(
            exchange_id=data['exchange_id'],
            attempt=data['attempt'],
            completed_items=tuple(
                ProviderOutputItem.from_dict(required_mapping(item, 'completed item'))
                for item in required_sequence(data['completed_items'], 'completed_items')
            ),
        )


def decode_prior_attempts(value: Any) -> tuple[ProviderOutputAttempt, ...]:
    return tuple(
        ProviderOutputAttempt.from_dict(required_mapping(attempt, 'prior attempt'))
        for attempt in required_sequence(value, 'prior_attempts')
    )


def iter_history_batches(
    output: ProviderOutputEnvelope | ProviderOutputFailureFacts,
) -> Iterator[ProviderOutputAttempt | ProviderOutputEnvelope | ProviderOutputFailureFacts]:
    """Yield prior attempts then the current batch, including an empty current batch."""
    yield from output.prior_attempts
    yield output


__all__ = ['ProviderOutputAttempt', 'iter_history_batches']
