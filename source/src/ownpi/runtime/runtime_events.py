from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _read_continuation_index(data: dict[str, Any]) -> int:
    if data.get('continuation_index') is not None:
        return int(data.get('continuation_index', 0) or 0)
    return 0


def _read_query_chain_id(data: dict[str, Any]) -> str:
    if data.get('query_chain_id') is not None:
        return str(data.get('query_chain_id', '') or '').strip()
    return ''


def _read_query_chain_depth(data: dict[str, Any]) -> int:
    if data.get('query_chain_depth') is not None:
        return int(data.get('query_chain_depth', 0) or 0)
    return 0


@dataclass(frozen=True)
class RuntimeEventRecord:
    event_id: str
    event_name: str
    category: str
    status: str
    stage: str = ''
    attempt: int = 0
    turn_id: str = ''
    sequence: int = 0
    state_domain: str = ''
    mutation_kind: str = ''
    mutation_refs: tuple[dict[str, Any], ...] = ()
    caused_by_event_id: str | None = None
    before_hash: str = ''
    after_hash: str = ''
    replay_policy: str = 'replay_as_observation'
    inspect_summary: str = ''
    query_chain_id: str = ''
    query_chain_depth: int = 0
    continuation_index: int = 0
    tool_name: str | None = None
    request_id: str | None = None
    session_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'RuntimeEventRecord':
        payload = data.get('payload')
        return cls(
            event_id=str(data.get('event_id', '')),
            event_name=str(data.get('event_name', '')),
            category=str(data.get('category', 'runtime')),
            status=str(data.get('status', 'unknown')),
            stage=str(data.get('stage', '') or ''),
            attempt=int(data.get('attempt', 0) or 0),
            turn_id=str(data.get('turn_id', '') or ''),
            sequence=int(data.get('sequence', 0) or 0),
            state_domain=str(data.get('state_domain', '') or ''),
            mutation_kind=str(data.get('mutation_kind', '') or ''),
            mutation_refs=tuple(
                dict(item) for item in data.get('mutation_refs', []) if isinstance(item, dict)
            ),
            caused_by_event_id=(
                str(data['caused_by_event_id'])
                if data.get('caused_by_event_id') is not None
                else None
            ),
            before_hash=str(data.get('before_hash', '') or ''),
            after_hash=str(data.get('after_hash', '') or ''),
            replay_policy=str(data.get('replay_policy', 'replay_as_observation') or 'replay_as_observation'),
            inspect_summary=str(data.get('inspect_summary', '') or ''),
            query_chain_id=_read_query_chain_id(data),
            query_chain_depth=_read_query_chain_depth(data),
            continuation_index=_read_continuation_index(data),
            tool_name=(str(data['tool_name']) if data.get('tool_name') is not None else None),
            request_id=(str(data['request_id']) if data.get('request_id') is not None else None),
            session_id=(str(data['session_id']) if data.get('session_id') is not None else None),
            payload=dict(payload) if isinstance(payload, dict) else {},
            created_at=(str(data['created_at']) if data.get('created_at') is not None else None),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload['mutation_refs'] = [dict(item) for item in self.mutation_refs if isinstance(item, dict)]
        return payload
