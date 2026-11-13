from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any



class SessionStatus(StrEnum):
    ACTIVE = 'active'
    IDLE = 'idle'
    COMPLETED = 'completed'
    FAILED = 'failed'
    CANCELLED = 'cancelled'
    ARCHIVED = 'archived'


class SessionTaskStatus(StrEnum):
    IDLE = 'idle'
    PENDING = 'pending'
    RUNNING = 'running'
    BLOCKED = 'blocked'
    COMPLETED = 'completed'
    FAILED = 'failed'
    CANCELLED = 'cancelled'


class SessionTaskType(StrEnum):
    WORKFLOW = 'workflow'
    LOCAL_SHELL = 'local_shell'
    LOCAL_AGENT = 'local_agent'
    REMOTE_AGENT = 'remote_agent'
    REMOTE_WORKFLOW = 'remote_workflow'
    MONITOR = 'monitor'
    OTHER = 'other'


@dataclass(frozen=True)
class SessionRunRef:
    run_id: str
    run_dir: str
    status: str = 'created'
    created_at: str = ''
    updated_at: str = ''
    completed_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, 'run_id', _require_identifier(self.run_id, 'run_id'))
        object.__setattr__(self, 'run_dir', str(self.run_dir or '').strip())
        object.__setattr__(self, 'status', str(self.status or 'created').strip() or 'created')
        object.__setattr__(self, 'created_at', str(self.created_at or ''))
        object.__setattr__(self, 'updated_at', str(self.updated_at or ''))
        object.__setattr__(self, 'metadata', dict(self.metadata or {}))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'SessionRunRef':
        return cls(
            run_id=str(data.get('run_id', '') or ''),
            run_dir=str(data.get('run_dir', '') or ''),
            status=str(data.get('status', 'created') or 'created'),
            created_at=str(data.get('created_at', '') or ''),
            updated_at=str(data.get('updated_at', '') or ''),
            completed_at=(str(data['completed_at']) if data.get('completed_at') is not None else None),
            metadata=dict(data.get('metadata', {})) if isinstance(data.get('metadata'), dict) else {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            'run_id': self.run_id,
            'run_dir': self.run_dir,
            'status': self.status,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'completed_at': self.completed_at,
            'metadata': dict(self.metadata),
        }


@dataclass(frozen=True)
class SessionTask:
    task_id: str
    task_type: SessionTaskType | str = SessionTaskType.WORKFLOW
    status: SessionTaskStatus | str = SessionTaskStatus.PENDING
    description: str = ''
    created_at: str = ''
    updated_at: str = ''
    completed_at: str | None = None
    active_run_id: str | None = None
    owner: str | None = None
    claimed_at: str | None = None
    runs: tuple[SessionRunRef, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, 'task_id', _require_identifier(self.task_id, 'task_id'))
        object.__setattr__(self, 'task_type', _normalize_enum(self.task_type, SessionTaskType, SessionTaskType.OTHER))
        object.__setattr__(self, 'status', _normalize_enum(self.status, SessionTaskStatus, SessionTaskStatus.PENDING))
        object.__setattr__(self, 'description', str(self.description or ''))
        object.__setattr__(self, 'created_at', str(self.created_at or ''))
        object.__setattr__(self, 'updated_at', str(self.updated_at or ''))
        object.__setattr__(
            self,
            'runs',
            tuple(item if isinstance(item, SessionRunRef) else SessionRunRef.from_dict(item) for item in self.runs),
        )
        object.__setattr__(self, 'metadata', dict(self.metadata or {}))
        if self.active_run_id is not None:
            object.__setattr__(self, 'active_run_id', str(self.active_run_id or '').strip() or None)
        if self.owner is not None:
            object.__setattr__(self, 'owner', str(self.owner or '').strip() or None)
        if self.claimed_at is not None:
            object.__setattr__(self, 'claimed_at', str(self.claimed_at or '').strip() or None)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'SessionTask':
        return cls(
            task_id=str(data.get('task_id', '') or ''),
            task_type=str(data.get('task_type', SessionTaskType.WORKFLOW.value) or SessionTaskType.WORKFLOW.value),
            status=str(data.get('status', SessionTaskStatus.PENDING.value) or SessionTaskStatus.PENDING.value),
            description=str(data.get('description', '') or ''),
            created_at=str(data.get('created_at', '') or ''),
            updated_at=str(data.get('updated_at', '') or ''),
            completed_at=(str(data['completed_at']) if data.get('completed_at') is not None else None),
            active_run_id=(str(data['active_run_id']) if data.get('active_run_id') is not None else None),
            owner=(str(data['owner']) if data.get('owner') is not None else None),
            claimed_at=(str(data['claimed_at']) if data.get('claimed_at') is not None else None),
            runs=tuple(SessionRunRef.from_dict(item) for item in data.get('runs', []) if isinstance(item, dict)),
            metadata=dict(data.get('metadata', {})) if isinstance(data.get('metadata'), dict) else {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            'task_id': self.task_id,
            'task_type': self.task_type.value,
            'status': self.status.value,
            'description': self.description,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'completed_at': self.completed_at,
            'active_run_id': self.active_run_id,
            'owner': self.owner,
            'claimed_at': self.claimed_at,
            'runs': [item.to_dict() for item in self.runs],
            'metadata': dict(self.metadata),
        }


@dataclass(frozen=True)
class RuntimeSession:
    session_id: str
    status: SessionStatus | str = SessionStatus.ACTIVE
    title: str = ''
    project_root: str = ''
    session_dir: str = ''
    created_at: str = ''
    updated_at: str = ''
    completed_at: str | None = None
    active_task_id: str | None = None
    parent_session_id: str | None = None
    tasks: tuple[SessionTask, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, 'session_id', _require_identifier(self.session_id, 'session_id'))
        object.__setattr__(self, 'status', _normalize_enum(self.status, SessionStatus, SessionStatus.ACTIVE))
        object.__setattr__(self, 'title', str(self.title or ''))
        object.__setattr__(self, 'project_root', str(self.project_root or ''))
        object.__setattr__(self, 'session_dir', str(self.session_dir or ''))
        object.__setattr__(self, 'created_at', str(self.created_at or ''))
        object.__setattr__(self, 'updated_at', str(self.updated_at or ''))
        object.__setattr__(
            self,
            'tasks',
            tuple(item if isinstance(item, SessionTask) else SessionTask.from_dict(item) for item in self.tasks),
        )
        object.__setattr__(self, 'metadata', dict(self.metadata or {}))
        if self.active_task_id is not None:
            object.__setattr__(self, 'active_task_id', str(self.active_task_id or '').strip() or None)
        if self.parent_session_id is not None:
            object.__setattr__(self, 'parent_session_id', str(self.parent_session_id or '').strip() or None)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'RuntimeSession':
        return cls(
            session_id=str(data.get('session_id', '') or ''),
            status=str(data.get('status', SessionStatus.ACTIVE.value) or SessionStatus.ACTIVE.value),
            title=str(data.get('title', '') or ''),
            project_root=str(data.get('project_root', '') or ''),
            session_dir=str(data.get('session_dir', '') or ''),
            created_at=str(data.get('created_at', '') or ''),
            updated_at=str(data.get('updated_at', '') or ''),
            completed_at=(str(data['completed_at']) if data.get('completed_at') is not None else None),
            active_task_id=(str(data['active_task_id']) if data.get('active_task_id') is not None else None),
            parent_session_id=(str(data['parent_session_id']) if data.get('parent_session_id') is not None else None),
            tasks=tuple(SessionTask.from_dict(item) for item in data.get('tasks', []) if isinstance(item, dict)),
            metadata=dict(data.get('metadata', {})) if isinstance(data.get('metadata'), dict) else {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            'session_id': self.session_id,
            'status': self.status.value,
            'title': self.title,
            'project_root': self.project_root,
            'session_dir': self.session_dir,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'completed_at': self.completed_at,
            'active_task_id': self.active_task_id,
            'parent_session_id': self.parent_session_id,
            'tasks': [item.to_dict() for item in self.tasks],
            'metadata': dict(self.metadata),
        }


def _require_identifier(value: str, field_name: str) -> str:
    normalized = str(value or '').strip()
    if not normalized:
        raise ValueError(f'{field_name} is required')
    return normalized


def _normalize_enum(value: Any, enum_type: type[StrEnum], default: StrEnum) -> StrEnum:
    if isinstance(value, enum_type):
        return value
    normalized = str(value or '').strip()
    if not normalized:
        return default
    try:
        return enum_type(normalized)
    except ValueError:
        return default
