"""Read run facts without loading snapshots, executing tools, or resuming a run."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ownpi.runtime.rollout.business_events import BusinessEvent
from ownpi.runtime.rollout.reader import read_rollout


@dataclass(frozen=True)
class BusinessReadResult:
    run_id: str
    events: tuple[BusinessEvent, ...]
    parse_errors: int


def read_business_events(path: Path, *, run_id: str | None = None) -> BusinessReadResult:
    result = read_rollout(path)
    if result.scope != 'run_events':
        raise ValueError('Expected run_events scope')
    headers = [record.payload.to_object() for record in result.records if record.type == 'session_meta']
    if not headers:
        raise ValueError('Run events require a header')
    header = headers[0]
    if any(candidate != header for candidate in headers[1:]):
        raise ValueError('Run events contain conflicting headers')
    identity = header.get('run_id')
    if not isinstance(identity, str) or not identity.strip() or header.get('schema_version') != 1:
        raise ValueError('Invalid run events header')
    if run_id is not None and identity != run_id:
        raise ValueError('Run events belong to a different run')
    events: dict[str, BusinessEvent] = {}
    for record in result.records:
        if record.type == 'session_meta':
            continue
        if record.type != 'event_msg':
            raise ValueError('Run events cannot contain conversation items')
        event = BusinessEvent.from_payload(record.payload.to_object(), record.timestamp)
        if event.run_id != identity:
            raise ValueError('Event belongs to a different run')
        previous = events.get(event.event_id)
        if previous is not None:
            if previous.kind != event.kind or previous.data != event.data:
                raise ValueError(f'Conflicting business event: {event.event_id}')
        else:
            events[event.event_id] = event
    return BusinessReadResult(identity, tuple(events.values()), result.parse_errors)
