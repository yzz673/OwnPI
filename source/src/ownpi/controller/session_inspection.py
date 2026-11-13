from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ownpi.controller.runtime_active_owner import read_active_runtime_owner, runtime_owner_is_active
from ownpi.runtime.rollout.business_reader import read_business_events
from ownpi.runtime.rollout.run_events import run_events_path
from ownpi.runtime.sessions import RuntimeSession, SessionStore, parse_session_run_dir


class SessionInspectionService:
    def __init__(self, *, store: SessionStore) -> None:
        self.store = store

    def list_sessions(self) -> list[dict[str, Any]]:
        return [_session_summary(session) for session in self.store.list_sessions()]

    def run_status(self, run_dir: Path) -> dict[str, Any]:
        identity = parse_session_run_dir(run_dir)
        if identity is None:
            raise ValueError('run directory is not session-managed')
        task = self.store.load_task(identity.session_id, identity.task_id)
        indexed = next((run for run in task.runs if run.run_id == identity.run_id), None)
        if indexed is None:
            raise FileNotFoundError(f'Runtime run not found: {identity.run_id}')
        events_path = run_events_path(run_dir)
        result = read_business_events(events_path, run_id=identity.run_id) if events_path.exists() else None
        events = result.events if result is not None else ()
        direct = any(event.kind == 'direct_request_started' for event in events)
        boundaries = {'run_finalized', 'runtime_control_consumed',
                      'direct_request_started', 'direct_request_completed', 'direct_request_failed'}
        if direct:
            boundaries.update({'interaction_requested', 'interaction_answer_accepted', 'interaction_closed'})
        boundary = next((event for event in reversed(events)
                         if event.kind in boundaries), None)
        stage = next((event.data.to_object() for event in reversed(events)
                      if event.kind == 'stage_attempt_started'), {})
        recorded = indexed.status
        if boundary is not None:
            data = boundary.data.to_object()
            if boundary.kind == 'direct_request_completed':
                recorded = 'idle'
            elif boundary.kind in {'direct_request_started', 'interaction_closed'}:
                recorded = 'in_progress'
            elif boundary.kind == 'direct_request_failed':
                recorded = 'failed'
            elif boundary.kind in {'interaction_requested', 'interaction_answer_accepted'}:
                recorded = 'paused_for_human'
            else:
                recorded = (str(data['status']) if boundary.kind == 'run_finalized'
                            else 'stopped' if data['action'] == 'interrupt' else 'paused_for_human')
        owner = read_active_runtime_owner(run_dir)
        active = runtime_owner_is_active(run_dir)
        # A previous stopped/failed result is not the result of a newer resume.
        unfinished_owner = owner is not None and (
            boundary is None or datetime.fromisoformat(owner.started_at)
            > datetime.fromisoformat(boundary.timestamp)
        )
        status = recorded
        if recorded == 'idle':
            status = 'idle'
        elif active:
            status = 'in_progress'
        elif direct and recorded == 'in_progress':
            status = 'unexpected_exit'
        elif unfinished_owner or (boundary is None and recorded not in {
            'completed', 'failed', 'stopped', 'paused_for_human', 'cancelled',
        } and (events or recorded in {'running', 'in_progress'})):
            status = 'unexpected_exit'
        modified = []
        for path in (run_dir / 'rollouts').rglob('*.jsonl'):
            try:
                modified.append(path.stat().st_mtime)
            except FileNotFoundError:
                continue
        last_recorded = datetime.fromtimestamp(max(modified), timezone.utc).isoformat() if modified else None
        return {
            'session_id': identity.session_id,
            'task_id': identity.task_id,
            'run_id': identity.run_id,
            'run_dir': str(run_dir),
            'status': status,
            'recorded_status': recorded,
            'current_stage': stage.get('stage'),
            'stage_attempt': stage.get('attempt'),
            'owner_active': active,
            'owner_pid': owner.pid if active and owner is not None else None,
            'last_recorded_at': last_recorded,
            'message': ('Runtime is no longer active; use ownpi run resume to continue.'
                        if status == 'unexpected_exit' else ''),
            'warnings': ([f'Skipped {result.parse_errors} unreadable rollout record(s).']
                         if result is not None and result.parse_errors else []),
        }


def _session_summary(session: RuntimeSession) -> dict[str, Any]:
    return {
        'session_id': session.session_id,
        'status': session.status.value,
        'title': session.title,
        'project_root': session.project_root,
        'session_dir': session.session_dir,
        'created_at': session.created_at,
        'updated_at': session.updated_at,
        'active_task_id': session.active_task_id,
        'parent_session_id': session.parent_session_id,
        'task_count': len(session.tasks),
        'metadata': dict(session.metadata),
    }
