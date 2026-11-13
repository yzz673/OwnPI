from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal, Mapping

from ownpi.stage_ids import canonical_stage_id


TRAJECTORY_SCHEMA_VERSION = 3


@dataclass(frozen=True)
class CoreTrajectoryIdentity:
    run_id: str
    stage: str
    attempt: int
    core_round: int
    actor_kind: Literal['core'] = 'core'
    operation: Literal['native_tool_loop'] = 'native_tool_loop'
    stage_loop_index: int = 1

    def __post_init__(self) -> None:
        if not self.run_id.strip() or not self.stage.strip():
            raise ValueError('run_id and stage must be non-empty')
        if self.attempt < 1 or self.core_round < 1:
            raise ValueError('attempt and core_round must be at least 1')
        if self.stage_loop_index < 1:
            raise ValueError('stage_loop_index must be positive')

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> 'CoreTrajectoryIdentity':
        return cls(
            run_id=str(data.get('run_id', '') or ''),
            stage=canonical_stage_id(data.get('stage', '') or ''),
            attempt=int(data.get('attempt', 0) or 0),
            core_round=int(data.get('core_round', 0) or 0),
            actor_kind=str(data.get('actor_kind', 'core') or 'core'),  # type: ignore[arg-type]
            operation=str(data.get('operation', 'native_tool_loop') or 'native_tool_loop'),  # type: ignore[arg-type]
            stage_loop_index=int(data.get('stage_loop_index', 1) or 1),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RecordSequenceSpan:
    start_sequence: int
    end_sequence: int
    end_hash: str
    item_count: int

    def __post_init__(self) -> None:
        if self.start_sequence < 0 or self.end_sequence < self.start_sequence:
            raise ValueError('record span values must be ordered and non-negative')
        if len(self.end_hash) != 64:
            raise ValueError('record span end_hash must be a sha256 digest')
        if self.item_count < 2:
            raise ValueError('record span must include round boundaries')

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> 'RecordSequenceSpan':
        return cls(
            start_sequence=int(data.get('start_sequence', -1)),
            end_sequence=int(data.get('end_sequence', -1)),
            end_hash=str(data.get('end_hash', '') or ''),
            item_count=int(data.get('item_count', 0) or 0),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CoreTrajectoryRef:
    schema_version: int
    identity: CoreTrajectoryIdentity
    source_span: RecordSequenceSpan
    trajectory_path: str
    content_sha256: str
    content_bytes: int
    source_item_count: int
    projected_entry_count: int
    suppressed_item_count: int
    status: Literal['sealed']
    created_at: str
    sealed_at: str

    def __post_init__(self) -> None:
        if self.schema_version != TRAJECTORY_SCHEMA_VERSION:
            raise ValueError('unsupported core trajectory schema version')
        if not self.trajectory_path.strip() or len(self.content_sha256) != 64:
            raise ValueError('trajectory path and sha256 are required')
        if self.content_bytes < 0 or self.source_item_count < 1 or self.projected_entry_count < 1:
            raise ValueError('sealed trajectory accounting is invalid')
        if self.source_item_count > self.source_span.item_count - 2:
            raise ValueError('trajectory source items exceed record span items')
        if self.suppressed_item_count < 0 or self.suppressed_item_count > self.source_item_count:
            raise ValueError('trajectory suppressed item accounting is invalid')
        if self.projected_entry_count + self.suppressed_item_count != self.source_item_count:
            raise ValueError('trajectory ref accounting must cover every source item')
        if not self.created_at or not self.sealed_at:
            raise ValueError('trajectory timestamps are required')

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> 'CoreTrajectoryRef':
        identity = data.get('identity')
        source_span = data.get('source_span')
        if not isinstance(identity, Mapping) or not isinstance(source_span, Mapping):
            raise ValueError('trajectory ref identity and source span must be mappings')
        return cls(
            schema_version=int(data.get('schema_version', 0) or 0),
            identity=CoreTrajectoryIdentity.from_dict(identity),
            source_span=RecordSequenceSpan.from_dict(source_span),
            trajectory_path=str(data.get('trajectory_path', '') or ''),
            content_sha256=str(data.get('content_sha256', '') or ''),
            content_bytes=int(data.get('content_bytes', -1)),
            source_item_count=int(data.get('source_item_count', 0) or 0),
            projected_entry_count=int(data.get('projected_entry_count', 0) or 0),
            suppressed_item_count=int(data.get('suppressed_item_count', 0) or 0),
            status=str(data.get('status', '') or ''),  # type: ignore[arg-type]
            created_at=str(data.get('created_at', '') or ''),
            sealed_at=str(data.get('sealed_at', '') or ''),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            'schema_version': self.schema_version,
            'identity': self.identity.to_dict(),
            'source_span': self.source_span.to_dict(),
            'trajectory_path': self.trajectory_path,
            'content_sha256': self.content_sha256,
            'content_bytes': self.content_bytes,
            'source_item_count': self.source_item_count,
            'projected_entry_count': self.projected_entry_count,
            'suppressed_item_count': self.suppressed_item_count,
            'status': self.status,
            'created_at': self.created_at,
            'sealed_at': self.sealed_at,
        }
