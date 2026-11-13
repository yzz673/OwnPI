from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from ownpi.controller.stages import Stage
from ownpi.runtime.context.entry import RuntimeEntryContext
from ownpi.schemas.workflow_plan import StageRuntimeProfile


StagePrepStatus = Literal['started', 'completed', 'failed']


@dataclass(frozen=True)
class StagePrepRequest:
    run_dir: Path
    stage: Stage
    level: StageRuntimeProfile
    entry_context: RuntimeEntryContext = field(default_factory=RuntimeEntryContext)
    attempt: int | None = None
    continuation_index: int = 0
    approved_stage_plan_path: str = ''
    approved_stage_plan_sha256: str = ''
    stage_loop_index: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.stage, Stage):
            raise TypeError('stage must be a Stage')
        if not isinstance(self.level, StageRuntimeProfile):
            raise TypeError('level must be a StageRuntimeProfile')
        if self.attempt is not None and self.attempt < 1:
            raise ValueError('attempt must be at least 1')
        if self.continuation_index < 0:
            raise ValueError('continuation index must not be negative')
        if self.stage_loop_index < 1:
            raise ValueError('stage_loop_index must be positive')
        if bool(self.approved_stage_plan_path) != bool(self.approved_stage_plan_sha256):
            raise ValueError('approved stage plan path and hash must be provided together')

    @property
    def has_approved_stage_plan(self) -> bool:
        return bool(self.approved_stage_plan_path and self.approved_stage_plan_sha256)


@dataclass(frozen=True)
class StagePrepHandoff:
    prep_id: str
    loop_count: int = 1

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'StagePrepHandoff':
        _reject_unknown_fields(
            data,
            allowed={'prep_id', 'loop_count'},
            label='StagePrepHandoff',
        )
        return cls(
            prep_id=str(data.get('prep_id', '')),
            loop_count=int(data.get('loop_count', 1) or 1),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StagePrepRecord:
    prep_id: str
    status: StagePrepStatus
    stage: str
    level: str
    attempt: int
    query_chain_id: str
    query_chain_depth: int
    continuation_index: int
    entry_origin: str
    source_resume_id: str
    continuation_kind: str
    created_at: str
    updated_at: str
    failure_class: str = ''
    failure_message: str = ''

    def __post_init__(self) -> None:
        if self.attempt < 1:
            raise ValueError('stage prep attempt must be at least 1')
        if self.status == 'failed' and not self.failure_class:
            raise ValueError('failed stage prep requires a failure class')

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'StagePrepRecord':
        _reject_unknown_fields(
            data,
            allowed={
                'prep_id',
                'status',
                'stage',
                'level',
                'attempt',
                'query_chain_id',
                'query_chain_depth',
                'continuation_index',
                'entry_origin',
                'source_resume_id',
                'continuation_kind',
                'created_at',
                'updated_at',
                'failure_class',
                'failure_message',
            },
            label='StagePrepRecord',
        )
        return cls(
            prep_id=str(data.get('prep_id', '')),
            status=str(data.get('status', 'failed')),  # type: ignore[arg-type]
            stage=str(data.get('stage', '')),
            level=str(data.get('level', '')),
            attempt=int(data.get('attempt', 0) or 0),
            query_chain_id=str(data.get('query_chain_id', '')),
            query_chain_depth=int(data.get('query_chain_depth', 0) or 0),
            continuation_index=int(data.get('continuation_index', 0) or 0),
            entry_origin=str(data.get('entry_origin', 'live')),
            source_resume_id=str(data.get('source_resume_id', '')),
            continuation_kind=str(data.get('continuation_kind', '')),
            created_at=str(data.get('created_at', '')),
            updated_at=str(data.get('updated_at', '')),
            failure_class=str(data.get('failure_class', '')),
            failure_message=str(data.get('failure_message', '')),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return payload


@dataclass(frozen=True)
class StagePrepResult:
    status: Literal['completed', 'failed']
    record: StagePrepRecord
    handoff: StagePrepHandoff | None = None
    reused: bool = False

    def __post_init__(self) -> None:
        if self.status == 'completed' and self.handoff is None:
            raise ValueError('completed stage prep requires a handoff')
        if self.status == 'failed' and self.handoff is not None:
            raise ValueError('failed stage prep must not expose a handoff')


@dataclass
class StagePrepState:
    active_handoff: StagePrepHandoff | None = None
    history: list[StagePrepRecord] = field(default_factory=list)

    def active_record(self) -> StagePrepRecord | None:
        handoff = self.active_handoff
        if handoff is None:
            return None
        in_memory_record = next(
            (item for item in reversed(self.history) if item.prep_id == handoff.prep_id),
            None,
        )
        return in_memory_record

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> 'StagePrepState':
        payload = data or {}
        _reject_unknown_fields(
            payload,
            allowed={'active_handoff', 'history'},
            label='StagePrepState',
        )
        handoff_data = payload.get('active_handoff')
        return cls(
            active_handoff=(
                StagePrepHandoff.from_dict(handoff_data)
                if isinstance(handoff_data, dict)
                else None
            ),
            history=[
                StagePrepRecord.from_dict(item)
                for item in payload.get('history', [])
                if isinstance(item, dict)
            ],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            'active_handoff': (
                self.active_handoff.to_dict() if self.active_handoff is not None else None
            ),
            'history': [item.to_dict() for item in self.history[-64:]],
        }


def _reject_unknown_fields(
    data: dict[str, Any],
    *,
    allowed: set[str],
    label: str,
) -> None:
    unknown = sorted(str(key) for key in data if str(key) not in allowed)
    if unknown:
        raise ValueError(f'{label} contains unsupported fields: {", ".join(unknown)}')
