from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any, Literal

from ownpi.runtime.events import now_utc_iso


LifecycleStatus = Literal[
    'discovered',
    'configured',
    'connecting',
    'ready',
    'stale',
    'refreshing',
    'failed',
    'disabled',
    'consumed',
]


@dataclass(frozen=True)
class RuntimeArtifactLifecycleRecord:
    artifact_id: str
    artifact_kind: str
    name: str
    status: str
    generation: int = 0
    source: str = ''
    dependency_refs: tuple[str, ...] = ()
    trust_level: str = ''
    injection_status: str = ''
    runtime_effect_status: str = ''
    result_hash: str = ''
    error_summary: str = ''
    updated_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> 'RuntimeArtifactLifecycleRecord':
        payload = data or {}
        return cls(
            artifact_id=str(payload.get('artifact_id', '') or ''),
            artifact_kind=str(payload.get('artifact_kind', '') or ''),
            name=str(payload.get('name', '') or ''),
            status=str(payload.get('status', '') or ''),
            generation=max(0, int(payload.get('generation', 0) or 0)),
            source=str(payload.get('source', '') or ''),
            dependency_refs=tuple(
                str(item) for item in payload.get('dependency_refs', []) if str(item).strip()
            )
            if not isinstance(payload.get('dependency_refs'), str)
            else (str(payload.get('dependency_refs')).strip(),),
            trust_level=str(payload.get('trust_level', '') or ''),
            injection_status=str(payload.get('injection_status', '') or ''),
            runtime_effect_status=str(payload.get('runtime_effect_status', '') or ''),
            result_hash=str(payload.get('result_hash', '') or ''),
            error_summary=str(payload.get('error_summary', '') or ''),
            updated_at=(str(payload['updated_at']) if payload.get('updated_at') is not None else None),
            metadata=dict(payload.get('metadata', {})) if isinstance(payload.get('metadata'), dict) else {},
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload['dependency_refs'] = list(self.dependency_refs)
        payload['metadata'] = dict(self.metadata)
        return payload


def artifact_lifecycle_id(kind: str, name: str) -> str:
    normalized_kind = str(kind or 'artifact').strip().casefold() or 'artifact'
    normalized_name = str(name or 'unknown').strip() or 'unknown'
    digest = hashlib.sha256(
        json.dumps(
            {'kind': normalized_kind, 'name': normalized_name.casefold()},
            sort_keys=True,
            separators=(',', ':'),
        ).encode('utf-8')
    ).hexdigest()[:12]
    return f'{normalized_kind}:{digest}'


def stable_lifecycle_result_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()[:16]


def record_lifecycle_transition(
    state_store: Any,
    record: RuntimeArtifactLifecycleRecord | dict[str, Any],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    lifecycle = record if isinstance(record, RuntimeArtifactLifecycleRecord) else RuntimeArtifactLifecycleRecord.from_dict(record)
    record_payload = lifecycle.to_dict()
    event_payload = {
        'lifecycle_record': record_payload,
        **(dict(payload or {})),
    }
    recorder_name = f'record_{lifecycle.artifact_kind}_mutation'
    recorder = getattr(state_store, recorder_name, None)
    if not callable(recorder):
        recorder = getattr(state_store, 'record_runtime_mutation', None)
    if callable(recorder):
        recorder(
            event_name=f'{lifecycle.artifact_kind}_runtime_artifact_updated',
            status=lifecycle.status,
            mutation_kind='state_delta',
            tool_name=lifecycle.name,
            payload=event_payload,
            created_at=lifecycle.updated_at or now_utc_iso(),
        )
    return event_payload


def compact_lifecycle_records(records: Any, limit: int = 64) -> list[dict[str, Any]]:
    if isinstance(records, dict):
        values = list(records.values())
    elif isinstance(records, (list, tuple)):
        values = list(records)
    else:
        values = []
    compacted: list[dict[str, Any]] = []
    for item in values:
        if isinstance(item, RuntimeArtifactLifecycleRecord):
            compacted.append(item.to_dict())
        elif isinstance(item, dict):
            compacted.append(RuntimeArtifactLifecycleRecord.from_dict(item).to_dict())
    return compacted[-max(1, int(limit or 64)):]
