from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import json
from pathlib import Path
from uuid import uuid4

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback for development environments.
    fcntl = None  # type: ignore[assignment]

from ownpi.runtime.events import now_utc_iso
from ownpi.utils.io import write_text_atomic
from ownpi.runtime.sessions.models import (
    RuntimeSession,
    SessionRunRef,
    SessionStatus,
    SessionTask,
    SessionTaskStatus,
    SessionTaskType,
)


SESSION_METADATA_FILENAME = 'session.json'
TASK_METADATA_FILENAME = 'task.json'
class SessionStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().resolve()

    def create_session(
        self,
        *,
        session_id: str | None = None,
        title: str = '',
        project_root: str = '',
        parent_session_id: str | None = None,
        metadata: dict | None = None,
    ) -> RuntimeSession:
        resolved_session_id = _clean_id(session_id) or f'session-{uuid4().hex}'
        session_dir = self.session_dir(resolved_session_id)
        session_dir.mkdir(parents=True, exist_ok=False)
        timestamp = now_utc_iso()
        session = RuntimeSession(
            session_id=resolved_session_id,
            status=SessionStatus.ACTIVE,
            title=title,
            project_root=project_root,
            session_dir=str(session_dir),
            created_at=timestamp,
            updated_at=timestamp,
            parent_session_id=parent_session_id,
            metadata=metadata or {},
        )
        self.save_session(session)
        return session

    def load_session(self, session_id: str) -> RuntimeSession:
        path = self.session_metadata_path(session_id)
        payload = _read_json(path)
        session = RuntimeSession.from_dict(payload)
        tasks = tuple(self.list_tasks(session.session_id))
        return RuntimeSession.from_dict({**session.to_dict(), 'tasks': [task.to_dict() for task in tasks]})

    def get_or_create_session(
        self,
        *,
        session_id: str | None = None,
        title: str = '',
        project_root: str = '',
        parent_session_id: str | None = None,
        metadata: dict | None = None,
    ) -> RuntimeSession:
        resolved_session_id = _clean_id(session_id)
        if resolved_session_id and self.session_metadata_path(resolved_session_id).exists():
            return self.load_session(resolved_session_id)
        return self.create_session(
            session_id=resolved_session_id,
            title=title,
            project_root=project_root,
            parent_session_id=parent_session_id,
            metadata=metadata,
        )

    def save_session(self, session: RuntimeSession) -> None:
        session_dir = self.session_dir(session.session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        payload = session.to_dict()
        # Tasks are stored independently under tasks/<task_id>/task.json.
        payload['tasks'] = []
        _write_json(self.session_metadata_path(session.session_id), payload)

    def list_sessions(self) -> list[RuntimeSession]:
        if not self.root.exists():
            return []
        sessions: list[RuntimeSession] = []
        for path in sorted(self.root.iterdir()):
            if not path.is_dir():
                continue
            metadata_path = path / SESSION_METADATA_FILENAME
            if not metadata_path.exists():
                continue
            sessions.append(self.load_session(path.name))
        return sessions

    def create_task(
        self,
        session_id: str,
        *,
        task_id: str | None = None,
        task_type: SessionTaskType | str = SessionTaskType.WORKFLOW,
        description: str = '',
        metadata: dict | None = None,
    ) -> SessionTask:
        self.load_session(session_id)
        resolved_task_id = _clean_id(task_id) or f'task-{uuid4().hex}'
        task_dir = self.task_dir(session_id, resolved_task_id)
        task_dir.mkdir(parents=True, exist_ok=False)
        timestamp = now_utc_iso()
        task = SessionTask(
            task_id=resolved_task_id,
            task_type=task_type,
            status=SessionTaskStatus.PENDING,
            description=description,
            created_at=timestamp,
            updated_at=timestamp,
            metadata=metadata or {},
        )
        self.save_task(session_id, task)
        self._touch_session(session_id, active_task_id=resolved_task_id)
        return task

    def load_task(self, session_id: str, task_id: str) -> SessionTask:
        payload = _read_json(self.task_metadata_path(session_id, task_id))
        return SessionTask.from_dict(payload)

    def get_or_create_task(
        self,
        session_id: str,
        *,
        task_id: str | None = None,
        task_type: SessionTaskType | str = SessionTaskType.WORKFLOW,
        description: str = '',
        metadata: dict | None = None,
    ) -> SessionTask:
        resolved_task_id = _clean_id(task_id)
        if resolved_task_id and self.task_metadata_path(session_id, resolved_task_id).exists():
            return self.load_task(session_id, resolved_task_id)
        return self.create_task(
            session_id,
            task_id=resolved_task_id,
            task_type=task_type,
            description=description,
            metadata=metadata,
        )

    def save_task(self, session_id: str, task: SessionTask) -> None:
        task_dir = self.task_dir(session_id, task.task_id)
        task_dir.mkdir(parents=True, exist_ok=True)
        _write_json(self.task_metadata_path(session_id, task.task_id), task.to_dict())

    def update_task_status(
        self,
        session_id: str,
        task_id: str,
        *,
        status: SessionTaskStatus | str,
        active_run_id: str | None = None,
        metadata: dict | None = None,
        clear_owner: bool = False,
    ) -> SessionTask:
        task = self.load_task(session_id, task_id)
        timestamp = now_utc_iso()
        normalized_status = (
            status.value if isinstance(status, SessionTaskStatus) else str(status or '').strip()
        )
        completed_at = timestamp if normalized_status in {'completed', 'failed', 'cancelled'} else task.completed_at
        updated_metadata = dict(task.metadata)
        if metadata:
            updated_metadata.update(dict(metadata))
        updated_task = SessionTask.from_dict(
            {
                **task.to_dict(),
                'status': normalized_status,
                'updated_at': timestamp,
                'completed_at': completed_at,
                'active_run_id': active_run_id if active_run_id is not None else task.active_run_id,
                'owner': None if clear_owner else task.owner,
                'claimed_at': None if clear_owner else task.claimed_at,
                'metadata': updated_metadata,
            }
        )
        self.save_task(session_id, updated_task)
        self._touch_session(session_id, active_task_id=task_id)
        return updated_task

    def claim_pending_task(self, session_id: str, task_id: str, *, owner: str) -> SessionTask | None:
        normalized_owner = str(owner or '').strip()
        if not normalized_owner:
            raise ValueError('owner is required')
        with _exclusive_file_lock(self.task_claim_lock_path(session_id, task_id)):
            task = self.load_task(session_id, task_id)
            if task.status is not SessionTaskStatus.PENDING:
                return None
            timestamp = now_utc_iso()
            updated_task = SessionTask.from_dict(
                {
                    **task.to_dict(),
                    'status': SessionTaskStatus.RUNNING.value,
                    'updated_at': timestamp,
                    'owner': normalized_owner,
                    'claimed_at': timestamp,
                }
            )
            self.save_task(session_id, updated_task)
            self._touch_session(session_id, active_task_id=task_id)
            return updated_task

    def update_run_status(
        self,
        session_id: str,
        task_id: str,
        run_id: str,
        *,
        status: str,
        metadata: dict | None = None,
    ) -> SessionRunRef:
        task = self.load_task(session_id, task_id)
        timestamp = now_utc_iso()
        normalized_status = str(status or '').strip() or 'unknown'
        completed_at = timestamp if normalized_status in {'completed', 'failed', 'cancelled'} else None
        updated_metadata = dict(metadata or {})
        updated_runs: list[SessionRunRef] = []
        updated_run: SessionRunRef | None = None
        for run in task.runs:
            if run.run_id != run_id:
                updated_runs.append(run)
                continue
            run_metadata = dict(run.metadata)
            run_metadata.update(updated_metadata)
            updated_run = SessionRunRef.from_dict(
                {
                    **run.to_dict(),
                    'status': normalized_status,
                    'updated_at': timestamp,
                    'completed_at': completed_at if completed_at is not None else run.completed_at,
                    'metadata': run_metadata,
                }
            )
            updated_runs.append(updated_run)
        if updated_run is None:
            raise KeyError(f'run not found in task {task_id}: {run_id}')
        updated_task = SessionTask.from_dict(
            {
                **task.to_dict(),
                'updated_at': timestamp,
                'runs': [run.to_dict() for run in updated_runs],
            }
        )
        self.save_task(session_id, updated_task)
        self._touch_session(session_id, active_task_id=task_id)
        return updated_run

    def list_tasks(self, session_id: str) -> list[SessionTask]:
        tasks_dir = self.tasks_dir(session_id)
        if not tasks_dir.exists():
            return []
        tasks: list[SessionTask] = []
        for path in sorted(tasks_dir.iterdir()):
            if not path.is_dir():
                continue
            task_path = path / TASK_METADATA_FILENAME
            if task_path.exists():
                tasks.append(SessionTask.from_dict(_read_json(task_path)))
        return tasks

    def allocate_run(
        self,
        session_id: str,
        task_id: str,
        *,
        run_id: str | None = None,
        metadata: dict | None = None,
    ) -> SessionRunRef:
        task = self.load_task(session_id, task_id)
        resolved_run_id = _clean_id(run_id) or f'run-{uuid4().hex}'
        run_dir = self.run_dir(session_id, task_id, resolved_run_id)
        run_dir.mkdir(parents=True, exist_ok=False)
        timestamp = now_utc_iso()
        run_ref = SessionRunRef(
            run_id=resolved_run_id,
            run_dir=str(run_dir),
            status='created',
            created_at=timestamp,
            updated_at=timestamp,
            metadata=metadata or {},
        )
        updated_task = SessionTask.from_dict(
            {
                **task.to_dict(),
                'status': SessionTaskStatus.RUNNING.value,
                'updated_at': timestamp,
                'completed_at': None,
                'active_run_id': resolved_run_id,
                'runs': [*task.to_dict()['runs'], run_ref.to_dict()],
            }
        )
        self.save_task(session_id, updated_task)
        self._touch_session(session_id, active_task_id=task_id)
        return run_ref

    def session_dir(self, session_id: str) -> Path:
        return self.root / _require_clean_id(session_id, 'session_id')

    def session_metadata_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / SESSION_METADATA_FILENAME

    def tasks_dir(self, session_id: str) -> Path:
        return self.session_dir(session_id) / 'tasks'

    def task_dir(self, session_id: str, task_id: str) -> Path:
        return self.tasks_dir(session_id) / _require_clean_id(task_id, 'task_id')

    def task_metadata_path(self, session_id: str, task_id: str) -> Path:
        return self.task_dir(session_id, task_id) / TASK_METADATA_FILENAME

    def task_claim_lock_path(self, session_id: str, task_id: str) -> Path:
        return self.task_dir(session_id, task_id) / '.claim.lock'

    def runs_dir(self, session_id: str, task_id: str) -> Path:
        return self.task_dir(session_id, task_id) / 'runs'

    def run_dir(self, session_id: str, task_id: str, run_id: str) -> Path:
        return self.runs_dir(session_id, task_id) / _require_clean_id(run_id, 'run_id')

    def _touch_session(self, session_id: str, *, active_task_id: str | None = None) -> None:
        session = self.load_session(session_id)
        payload = session.to_dict()
        payload['updated_at'] = now_utc_iso()
        if active_task_id is not None:
            payload['active_task_id'] = active_task_id
        self.save_session(RuntimeSession.from_dict(payload))


def _read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(payload, dict):
        raise ValueError(f'Expected JSON object at {path}')
    return payload


def _write_json(path: Path, payload: dict) -> None:
    content = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + '\n'
    write_text_atomic(path, content)


@contextmanager
def _exclusive_file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+', encoding='utf-8') as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _clean_id(value: str | None) -> str:
    normalized = str(value or '').strip()
    if not normalized:
        return ''
    if '/' in normalized or '\\' in normalized or normalized in {'.', '..'}:
        raise ValueError(f'Invalid identifier: {normalized!r}')
    return normalized


def _require_clean_id(value: str, field_name: str) -> str:
    normalized = _clean_id(value)
    if not normalized:
        raise ValueError(f'{field_name} is required')
    return normalized
