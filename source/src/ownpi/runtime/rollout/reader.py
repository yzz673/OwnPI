"""Read canonical lines without reconstructing runtime or replaying tools."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from ownpi.runtime.rollout.codec import RolloutRecord, validate_recovery_record


@dataclass(frozen=True)
class RolloutReadResult:
    records: tuple[RolloutRecord, ...]
    conversation_id: str | None
    parse_errors: int
    scope: str = 'conversation'


def read_rollout(path: Path) -> RolloutReadResult:
    records = []
    identity = None
    errors = 0
    nonempty = False
    scope = 'conversation'
    with path.open('r', encoding='utf-8') as source:
        for line in source:
            if not line.strip():
                continue
            nonempty = True
            try:
                record = RolloutRecord.from_dict(json.loads(line))
                validate_recovery_record(record)
            except (ValueError, TypeError, KeyError, AttributeError):
                errors += 1
                continue
            if identity is None and record.type == 'session_meta':
                identity = record.payload.to_object().get('id')
                scope = record.payload.to_object().get('scope', 'conversation')
            records.append(record)
    if not nonempty:
        raise OSError('empty session file')
    return RolloutReadResult(tuple(records), identity, errors, scope)


def open_history(path: Path) -> RolloutReadResult:
    result = read_rollout(path)
    if result.scope != 'conversation':
        raise OSError('Run events are not conversation history')
    if not result.conversation_id:
        raise OSError('failed to parse conversation ID from rollout file')
    return result
