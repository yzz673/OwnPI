from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field, replace
from pathlib import Path
import time
from typing import Any, Callable

from ownpi.runtime.events import now_utc_iso
from ownpi.runtime.limits.projection import MAX_RUNTIME_TASK_WAIT_PROGRESS_EVENTS
from ownpi.runtime.state_models import RuntimeState, RuntimeTaskNotification, RuntimeTaskState


ACTIVE_RUNTIME_TASK_STATUSES = frozenset({'pending', 'running', 'stalled', 'cancelling'})
TERMINAL_RUNTIME_TASK_STATUSES = frozenset(
    {'completed', 'failed', 'timed_out', 'killed', 'cancelled'}
)
RUNTIME_TASK_OUTPUT_READY_STATUSES = TERMINAL_RUNTIME_TASK_STATUSES | frozenset({'stalled'})
KNOWN_RUNTIME_TASK_STATUSES = tuple(
    sorted(ACTIVE_RUNTIME_TASK_STATUSES | TERMINAL_RUNTIME_TASK_STATUSES)
)
@dataclass
class RuntimeTaskWaitProgressRecorder:
    max_events: int = MAX_RUNTIME_TASK_WAIT_PROGRESS_EVENTS
    event_count: int = 0
    dropped_event_count: int = 0
    callback_error_count: int = 0
    wait_started_event: dict[str, Any] | None = None
    final_event: dict[str, Any] | None = None
    first_callback_error: dict[str, Any] | None = None
    last_callback_error: dict[str, Any] | None = None
    recent_events: deque[dict[str, Any]] = field(default_factory=deque)

    def __post_init__(self) -> None:
        if self.recent_events.maxlen != self.max_events:
            self.recent_events = deque(self.recent_events, maxlen=max(1, int(self.max_events or 1)))

    def record(
        self,
        *,
        event: str,
        task: RuntimeTaskState,
        start: float,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.event_count += 1
        payload = {
            'event': event,
            'task_id': task.task_id,
            'status': task.status,
            'elapsed_ms': int((time.monotonic() - start) * 1000),
        }
        if progress_callback is not None:
            try:
                progress_callback(dict(payload))
            except Exception as exc:  # noqa: BLE001 - progress hooks must not break task output polling.
                self._record_callback_error(exc, task=task, start=start)
        self._keep(payload)

    def _keep(self, payload: dict[str, Any]) -> None:
        event = str(payload.get('event', '') or '')
        if event == 'wait_started':
            self.wait_started_event = dict(payload)
            return
        if event in {
            'wait_ready',
            'wait_timed_out',
            'wait_cancelled',
            'task_missing_during_wait',
        }:
            self.final_event = dict(payload)
            return
        before = len(self.recent_events)
        self.recent_events.append(dict(payload))
        if len(self.recent_events) == before and self.recent_events.maxlen:
            self.dropped_event_count += 1

    def _record_callback_error(self, exc: Exception, *, task: RuntimeTaskState, start: float) -> None:
        self.callback_error_count += 1
        payload = {
            'event': 'progress_callback_error',
            'task_id': task.task_id,
            'status': task.status,
            'elapsed_ms': int((time.monotonic() - start) * 1000),
            'error_type': type(exc).__name__,
            'error': str(exc),
        }
        if self.first_callback_error is None:
            self.first_callback_error = dict(payload)
        self.last_callback_error = dict(payload)

    def visible_events(self) -> list[dict[str, Any]]:
        limit = max(1, int(self.max_events or 1))
        final_event = dict(self.final_event) if self.final_event is not None else None
        events: list[dict[str, Any]] = []
        final_slots = 1 if final_event is not None else 0
        if self.wait_started_event is not None and limit > final_slots:
            events.append(dict(self.wait_started_event))
        available = max(0, limit - len(events) - final_slots)
        callback_errors = self._callback_error_summaries()
        callback_slots = min(len(callback_errors), available)
        recent_slots = max(0, available - callback_slots)
        if recent_slots:
            events.extend(dict(item) for item in list(self.recent_events)[-recent_slots:])
        if callback_slots:
            events.extend(dict(item) for item in callback_errors[-callback_slots:])
        if final_event is not None:
            events.append(final_event)
        return events[-limit:]

    def _callback_error_summaries(self) -> list[dict[str, Any]]:
        if self.callback_error_count <= 0:
            return []
        if self.first_callback_error == self.last_callback_error:
            return [dict(self.first_callback_error or {})]
        return [dict(self.first_callback_error or {}), dict(self.last_callback_error or {})]

    def summary_payload(self) -> dict[str, Any]:
        visible = self.visible_events()
        return {
            'wait_progress_event_count': int(self.event_count),
            'wait_progress_events_truncated': bool(
                self.dropped_event_count > 0 or int(self.event_count) > len(visible)
            ),
            'wait_progress_events': visible,
            'wait_progress_dropped_event_count': int(self.dropped_event_count),
            'progress_callback_error_count': int(self.callback_error_count),
        }


def register_runtime_task(
    state: RuntimeState,
    *,
    task_id: str,
    task_type: str,
    description: str,
    status: str = 'running',
    output_file: str | None = None,
    provider_kind: str | None = None,
    run_id: str | None = None,
    stage: str | None = None,
    attempt: int | None = None,
    session_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    started_at: str | None = None,
) -> RuntimeTaskState:
    normalized_task_id = _normalize_task_id(task_id)
    normalized_status = _normalize_status(status)
    now = now_utc_iso()
    existing = state.runtime_tasks.get(normalized_task_id)
    merged_metadata = dict(existing.metadata) if existing is not None else {}
    if metadata:
        merged_metadata.update(dict(metadata))
    if existing is not None and existing.status in TERMINAL_RUNTIME_TASK_STATUSES and normalized_status in ACTIVE_RUNTIME_TASK_STATUSES:
        merged_metadata['ignored_status_regression'] = {
            'from_status': existing.status,
            'attempted_status': normalized_status,
            'at': now,
            'source': 'register_runtime_task',
        }
        normalized_status = existing.status
    task = RuntimeTaskState(
        task_id=normalized_task_id,
        task_type=str(task_type or 'unknown').strip() or 'unknown',
        status=normalized_status,
        description=str(description or ''),
        output_file=output_file if output_file is not None else (existing.output_file if existing else None),
        output_offset=existing.output_offset if existing is not None else 0,
        notified=existing.notified if existing is not None else False,
        provider_kind=provider_kind if provider_kind is not None else (existing.provider_kind if existing else None),
        run_id=run_id if run_id is not None else (existing.run_id if existing else None),
        stage=stage if stage is not None else (existing.stage if existing else None),
        attempt=attempt if attempt is not None else (existing.attempt if existing else None),
        session_id=session_id if session_id is not None else (existing.session_id if existing else None),
        started_at=started_at or (existing.started_at if existing else None) or now,
        updated_at=now,
        completed_at=(
            existing.completed_at
            if existing is not None and existing.completed_at is not None
            else (None if normalized_status in ACTIVE_RUNTIME_TASK_STATUSES else now)
        ),
        metadata=merged_metadata,
    )
    state.runtime_tasks[normalized_task_id] = task
    return task


def update_runtime_task(
    state: RuntimeState,
    *,
    task_id: str,
    status: str | None = None,
    description: str | None = None,
    output_file: str | None = None,
    output_offset: int | None = None,
    notified: bool | None = None,
    provider_kind: str | None = None,
    run_id: str | None = None,
    stage: str | None = None,
    attempt: int | None = None,
    session_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    completed_at: str | None = None,
) -> RuntimeTaskState | None:
    normalized_task_id = _normalize_task_id(task_id)
    existing = state.runtime_tasks.get(normalized_task_id)
    if existing is None:
        return None
    normalized_status = _normalize_status(status) if status is not None else existing.status
    merged_metadata = dict(existing.metadata)
    if metadata:
        merged_metadata.update(dict(metadata))
    now = now_utc_iso()
    if existing.status in TERMINAL_RUNTIME_TASK_STATUSES and normalized_status in ACTIVE_RUNTIME_TASK_STATUSES:
        merged_metadata['ignored_status_regression'] = {
            'from_status': existing.status,
            'attempted_status': normalized_status,
            'at': now,
            'source': 'update_runtime_task',
        }
        normalized_status = existing.status
    task = replace(
        existing,
        status=normalized_status,
        description=description if description is not None else existing.description,
        output_file=output_file if output_file is not None else existing.output_file,
        output_offset=int(output_offset) if output_offset is not None else existing.output_offset,
        notified=bool(notified) if notified is not None else existing.notified,
        provider_kind=provider_kind if provider_kind is not None else existing.provider_kind,
        run_id=run_id if run_id is not None else existing.run_id,
        stage=stage if stage is not None else existing.stage,
        attempt=attempt if attempt is not None else existing.attempt,
        session_id=session_id if session_id is not None else existing.session_id,
        updated_at=now,
        completed_at=(
            completed_at
            if completed_at is not None
            else (now if normalized_status in TERMINAL_RUNTIME_TASK_STATUSES and existing.completed_at is None else existing.completed_at)
        ),
        metadata=merged_metadata,
    )
    state.runtime_tasks[normalized_task_id] = task
    if notified is True:
        consumed_at = now_utc_iso()
        state.runtime_task_notifications = [
            replace(notification, consumed=True, consumed_at=notification.consumed_at or consumed_at)
            if notification.task_id == normalized_task_id and not notification.consumed
            else notification
            for notification in state.runtime_task_notifications
        ]
    if status is not None and existing.status != task.status and task.status in (TERMINAL_RUNTIME_TASK_STATUSES | {'stalled'}):
        enqueue_runtime_task_notification(
            state,
            task_id=task.task_id,
            status=task.status,
            target_actor=(
                str(merged_metadata.get('notification_target_actor'))
                if merged_metadata.get('notification_target_actor') is not None
                else None
            ),
            priority=str(merged_metadata.get('notification_priority') or ('next' if task.status == 'stalled' else 'later')),
        )
    return task


def complete_runtime_task(
    state: RuntimeState,
    *,
    task_id: str,
    output_file: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> RuntimeTaskState | None:
    return update_runtime_task(
        state,
        task_id=task_id,
        status='completed',
        output_file=output_file,
        metadata=metadata,
    )


def fail_runtime_task(
    state: RuntimeState,
    *,
    task_id: str,
    status: str = 'failed',
    output_file: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> RuntimeTaskState | None:
    return update_runtime_task(
        state,
        task_id=task_id,
        status=status,
        output_file=output_file,
        metadata=metadata,
    )


def kill_runtime_task(state: RuntimeState, *, task_id: str, force: bool = False) -> RuntimeTaskState | None:
    task = state.runtime_tasks.get(_normalize_task_id(task_id))
    if task is None:
        return None
    if task.status in TERMINAL_RUNTIME_TASK_STATUSES and not force:
        update_runtime_task(
            state,
            task_id=task.task_id,
            metadata={
                'ignored_kill_request': {
                    'status': task.status,
                    'force': False,
                    'at': now_utc_iso(),
                }
            },
        )
        return state.runtime_tasks.get(task.task_id)
    return fail_runtime_task(
        state,
        task_id=task.task_id,
        status='killed',
        metadata={'kill_authority': 'canonical_execution_runtime'},
    )


async def poll_runtime_task_output(
    state: RuntimeState,
    *,
    task_id: str,
    offset: int | None = None,
    max_bytes: int = 8192,
    advance_offset: bool = True,
    block: bool = False,
    timeout_ms: int = 30000,
    poll_interval_ms: int = 100,
    cancel_checker: Callable[[], bool] | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    prefer_clean_result: bool = True,
) -> dict[str, Any]:
    normalized_task_id = _normalize_task_id(task_id)
    task = state.runtime_tasks.get(normalized_task_id)
    if task is None:
        raise KeyError(f'runtime task not found: {task_id}')
    (
        task,
        timed_out,
        wait_elapsed_ms,
        task_missing_during_wait,
        cancelled,
        wait_progress_recorder,
    ) = await _wait_for_runtime_task_output_ready(
        state,
        task_id=normalized_task_id,
        block=block,
        timeout_ms=timeout_ms,
        poll_interval_ms=poll_interval_ms,
        cancel_checker=cancel_checker,
        progress_callback=progress_callback,
    )
    clean_projection = _runtime_task_clean_result_projection(task) if prefer_clean_result else None
    if clean_projection is not None:
        return {
            'task_id': task.task_id,
            'output_file': str(task.output_file) if task.output_file else None,
            'offset': int(task.output_offset or 0),
            'next_offset': int(task.output_offset or 0),
            'content': clean_projection['content'],
            'content_source': clean_projection['content_source'],
            'clean_projection': True,
            'raw_output_available': bool(task.output_file),
            'eof': True,
            'bytes_read': len(clean_projection['content'].encode('utf-8')),
            **_runtime_task_wait_payload(
                task,
                block=block,
                timed_out=timed_out,
                wait_elapsed_ms=wait_elapsed_ms,
                task_missing_during_wait=task_missing_during_wait,
                cancelled=cancelled,
                wait_progress_recorder=wait_progress_recorder,
            ),
        }
    if not task.output_file:
        return {
            'task_id': task.task_id,
            'output_file': None,
            'offset': int(task.output_offset or 0),
            'next_offset': int(task.output_offset or 0),
            'content': '',
            'eof': True,
            'bytes_read': 0,
            **_runtime_task_wait_payload(
                task,
                block=block,
                timed_out=timed_out,
                wait_elapsed_ms=wait_elapsed_ms,
                task_missing_during_wait=task_missing_during_wait,
                cancelled=cancelled,
                wait_progress_recorder=wait_progress_recorder,
            ),
        }
    output_path = Path(task.output_file).expanduser()
    allow_symlink = bool(task.metadata.get('allow_output_symlink', False))
    safety_error = _runtime_output_path_safety_error(output_path, allow_symlink=allow_symlink)
    if safety_error:
        return {
            'task_id': task.task_id,
            'output_file': str(output_path),
            'offset': int(task.output_offset or 0),
            'next_offset': int(task.output_offset or 0),
            'content': '',
            'eof': True,
            'bytes_read': 0,
            'error': safety_error,
            'safety_rejected': True,
            **_runtime_task_wait_payload(
                task,
                block=block,
                timed_out=timed_out,
                wait_elapsed_ms=wait_elapsed_ms,
                task_missing_during_wait=task_missing_during_wait,
                cancelled=cancelled,
                wait_progress_recorder=wait_progress_recorder,
            ),
        }
    if not output_path.exists() or not output_path.is_file():
        return {
            'task_id': task.task_id,
            'output_file': str(output_path),
            'offset': int(task.output_offset or 0),
            'next_offset': int(task.output_offset or 0),
            'content': '',
            'eof': True,
            'bytes_read': 0,
            'missing': True,
            **_runtime_task_wait_payload(
                task,
                block=block,
                timed_out=timed_out,
                wait_elapsed_ms=wait_elapsed_ms,
                task_missing_during_wait=task_missing_during_wait,
                cancelled=cancelled,
                wait_progress_recorder=wait_progress_recorder,
            ),
        }
    start = max(0, int(task.output_offset if offset is None else offset))
    limit = max(1, min(int(max_bytes or 8192), 65536))
    with output_path.open('rb') as file:
        file.seek(start)
        chunk = file.read(limit)
        next_offset = file.tell()
        file.seek(0, 2)
        end_offset = file.tell()
    content = chunk.decode('utf-8', errors='replace')
    stored_next_offset = max(int(task.output_offset or 0), int(next_offset))
    if advance_offset:
        update_runtime_task(state, task_id=task.task_id, output_offset=stored_next_offset)
    return {
        'task_id': task.task_id,
        'output_file': str(output_path),
        'offset': start,
        'next_offset': next_offset,
        'stored_next_offset': stored_next_offset,
        'advance_offset': bool(advance_offset),
        'content': content,
        'eof': next_offset >= end_offset,
        'bytes_read': len(chunk),
        'file_size': end_offset,
        **_runtime_task_wait_payload(
            task,
            block=block,
            timed_out=timed_out,
            wait_elapsed_ms=wait_elapsed_ms,
            task_missing_during_wait=task_missing_during_wait,
            cancelled=cancelled,
            wait_progress_recorder=wait_progress_recorder,
        ),
    }


def _runtime_task_clean_result_projection(task: RuntimeTaskState) -> dict[str, str] | None:
    if task.status not in TERMINAL_RUNTIME_TASK_STATUSES:
        return None
    metadata = task.metadata if isinstance(task.metadata, dict) else {}
    task_type = str(task.task_type or '').strip().casefold()
    agent_like = task_type in {'agent', 'local_agent'} or bool(
        metadata.get('prefer_clean_result')
    )
    if not agent_like:
        return None
    for key in (
        'clean_result',
        'final_answer',
        'clean_final_answer',
        'agent_final_answer',
        'result_projection',
    ):
        value = metadata.get(key)
        text = _coerce_clean_result_text(value)
        if text is not None:
            return {'content': text, 'content_source': key}
    nested = metadata.get('agent_final_result') or metadata.get('final_result')
    if isinstance(nested, dict):
        for key in ('clean_result', 'final_answer', 'answer', 'content', 'text'):
            text = _coerce_clean_result_text(nested.get(key))
            if text is not None:
                return {'content': text, 'content_source': f'agent_final_result.{key}'}
    return None


def _coerce_clean_result_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value
    elif isinstance(value, (int, float, bool)):
        text = str(value)
    else:
        return None
    return text if text.strip() else None


async def _wait_for_runtime_task_output_ready(
    state: RuntimeState,
    *,
    task_id: str,
    block: bool,
    timeout_ms: int,
    poll_interval_ms: int,
    cancel_checker: Callable[[], bool] | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[RuntimeTaskState, bool, int, bool, bool, RuntimeTaskWaitProgressRecorder]:
    start = time.monotonic()
    task = state.runtime_tasks[task_id]
    progress_recorder = RuntimeTaskWaitProgressRecorder()
    if not block or task.status in RUNTIME_TASK_OUTPUT_READY_STATUSES:
        return task, False, int((time.monotonic() - start) * 1000), False, False, progress_recorder
    timeout_seconds = max(0.0, min(float(timeout_ms or 0) / 1000.0, 300.0))
    poll_interval_seconds = max(0.01, min(float(poll_interval_ms or 100) / 1000.0, 5.0))
    deadline = start + timeout_seconds
    timed_out = False
    progress_recorder.record(
        event='wait_started',
        task=task,
        start=start,
        progress_callback=progress_callback,
    )
    while task.status not in RUNTIME_TASK_OUTPUT_READY_STATUSES:
        if _runtime_wait_cancelled(cancel_checker):
            progress_recorder.record(
                event='wait_cancelled',
                task=task,
                start=start,
                progress_callback=progress_callback,
            )
            return task, False, int((time.monotonic() - start) * 1000), False, True, progress_recorder
        now = time.monotonic()
        if now >= deadline:
            timed_out = True
            break
        progress_recorder.record(
            event='wait_poll',
            task=task,
            start=start,
            progress_callback=progress_callback,
        )
        await asyncio.sleep(min(poll_interval_seconds, max(0.0, deadline - now)))
        if _runtime_wait_cancelled(cancel_checker):
            progress_recorder.record(
                event='wait_cancelled',
                task=task,
                start=start,
                progress_callback=progress_callback,
            )
            return task, False, int((time.monotonic() - start) * 1000), False, True, progress_recorder
        next_task = state.runtime_tasks.get(task_id)
        if next_task is None:
            progress_recorder.record(
                event='task_missing_during_wait',
                task=task,
                start=start,
                progress_callback=progress_callback,
            )
            return task, True, int((time.monotonic() - start) * 1000), True, False, progress_recorder
        task = next_task
    progress_recorder.record(
        event='wait_timed_out' if timed_out else 'wait_ready',
        task=task,
        start=start,
        progress_callback=progress_callback,
    )
    return task, timed_out, int((time.monotonic() - start) * 1000), False, False, progress_recorder


def _runtime_wait_cancelled(cancel_checker: Callable[[], bool] | None) -> bool:
    if cancel_checker is None:
        return False
    try:
        return bool(cancel_checker())
    except Exception:
        return False


def _runtime_task_wait_payload(
    task: RuntimeTaskState,
    *,
    block: bool,
    timed_out: bool,
    wait_elapsed_ms: int,
    task_missing_during_wait: bool = False,
    cancelled: bool = False,
    wait_progress_recorder: RuntimeTaskWaitProgressRecorder | None = None,
) -> dict[str, Any]:
    wait_progress_payload = (
        wait_progress_recorder.summary_payload()
        if wait_progress_recorder is not None
        else RuntimeTaskWaitProgressRecorder().summary_payload()
    )
    return {
        'status': task.status,
        'ready': task.status in RUNTIME_TASK_OUTPUT_READY_STATUSES,
        'blocked': bool(block),
        'timed_out': bool(timed_out),
        'cancelled': bool(cancelled),
        'wait_elapsed_ms': int(wait_elapsed_ms),
        'wait_source': 'runtime_state',
        'task_missing_during_wait': bool(task_missing_during_wait),
        **wait_progress_payload,
    }


def evict_terminal_runtime_tasks(
    state: RuntimeState,
    *,
    keep_terminal: int = 64,
) -> dict[str, int]:
    pending_notification_task_ids = {
        notification.task_id
        for notification in getattr(state, 'runtime_task_notifications', [])
        if not notification.consumed and notification.task_id
    }
    running = {
        task_id: task
        for task_id, task in state.runtime_tasks.items()
        if task.status not in TERMINAL_RUNTIME_TASK_STATUSES
    }
    unnotified_terminal = {
        task_id: task
        for task_id, task in state.runtime_tasks.items()
        if task.status in TERMINAL_RUNTIME_TASK_STATUSES and (not task.notified or task_id in pending_notification_task_ids)
    }
    terminal = [
        (task_id, task)
        for task_id, task in state.runtime_tasks.items()
        if task.status in TERMINAL_RUNTIME_TASK_STATUSES and task.notified
    ]
    terminal.sort(key=lambda item: (item[1].updated_at or item[1].completed_at or item[1].started_at or '', item[0]))
    kept_terminal = dict(terminal[-max(0, int(keep_terminal)):])
    evicted = max(0, len(terminal) - len(kept_terminal))
    state.runtime_tasks = {**running, **unnotified_terminal, **kept_terminal}
    return {'evicted_terminal_count': evicted, 'retained_task_count': len(state.runtime_tasks)}


def enqueue_runtime_task_notification(
    state: RuntimeState,
    *,
    task_id: str,
    target_actor: str | None = None,
    priority: str = 'later',
    status: str | None = None,
) -> RuntimeTaskNotification | None:
    normalized_task_id = _normalize_task_id(task_id)
    task = state.runtime_tasks.get(normalized_task_id)
    if task is None:
        return None
    normalized_target = str(target_actor or '').strip() or None
    normalized_priority = str(priority or 'later').strip()
    if normalized_priority not in {'next', 'later'}:
        normalized_priority = 'later'
    notification_status = str(status or task.status or '').strip() or task.status
    for existing in state.runtime_task_notifications:
        if (
            not existing.consumed
            and existing.task_id == normalized_task_id
            and existing.target_actor == normalized_target
            and existing.status == notification_status
        ):
            return existing
    sequence = len(state.runtime_task_notifications) + 1
    notification = RuntimeTaskNotification(
        notification_id=f'runtime-task-notification:{normalized_task_id}:{sequence}',
        task_id=normalized_task_id,
        target_actor=normalized_target,
        priority=normalized_priority,
        status=notification_status,
        consumed=False,
        created_at=now_utc_iso(),
    )
    state.runtime_task_notifications.append(notification)
    return notification


def build_runtime_task_notification(
    task: RuntimeTaskState,
    notification: RuntimeTaskNotification | None = None,
) -> dict[str, Any]:
    return {
        'notification_id': notification.notification_id if notification is not None else '',
        'task_id': task.task_id,
        'task_type': task.task_type,
        'status': task.status,
        'notification_status': notification.status if notification is not None else task.status,
        'priority': notification.priority if notification is not None else 'later',
        'target_actor': notification.target_actor if notification is not None else None,
        'description': task.description,
        'output_file': task.output_file,
        'provider_kind': task.provider_kind,
        'run_id': task.run_id,
        'stage': task.stage,
        'attempt': task.attempt,
        'session_id': task.session_id,
        'completed_at': task.completed_at,
        'updated_at': task.updated_at,
        'summary': _runtime_task_notification_summary(task),
        'recommended_reader': 'read_file',
        'fallback_tool': 'runtime_task_poll',
        'recommended_action': 'prefer read_file on output_file; use runtime_task_poll only for offset-based incremental output',
    }


def collect_runtime_task_notifications(
    state: RuntimeState,
    *,
    target_actor: str | None = None,
    include_all_targets: bool = False,
    limit: int = 8,
) -> list[dict[str, Any]]:
    _ensure_runtime_task_notification_backfill(state)
    normalized_target = str(target_actor or '').strip() or None
    candidates = [
        notification
        for notification in state.runtime_task_notifications
        if not notification.consumed
        and _runtime_task_notification_matches_target(
            notification,
            target_actor=normalized_target,
            include_all_targets=include_all_targets,
        )
        and notification.task_id in state.runtime_tasks
    ]
    candidates.sort(
        key=lambda item: (
            0 if item.priority == 'next' else 1,
            item.created_at or '',
            item.notification_id,
        )
    )
    payloads: list[dict[str, Any]] = []
    for notification in candidates[: max(0, int(limit))]:
        task = state.runtime_tasks.get(notification.task_id)
        if task is None:
            continue
        payloads.append(build_runtime_task_notification(task, notification))
    return payloads


def _runtime_task_notification_matches_target(
    notification: RuntimeTaskNotification,
    *,
    target_actor: str | None,
    include_all_targets: bool,
) -> bool:
    if include_all_targets:
        return True
    if target_actor is None:
        return notification.target_actor is None
    return notification.target_actor is None or notification.target_actor == target_actor


def mark_runtime_task_notifications_consumed(state: RuntimeState, notification_ids: list[str]) -> None:
    consumed_at = now_utc_iso()
    consumed_task_ids: set[str] = set()
    for notification_id in notification_ids:
        normalized_id = str(notification_id or '').strip()
        if not normalized_id:
            continue
        matched = False
        updated_notifications: list[RuntimeTaskNotification] = []
        for notification in state.runtime_task_notifications:
            if notification.notification_id == normalized_id:
                matched = True
                consumed_task_ids.add(notification.task_id)
                updated_notifications.append(
                    replace(notification, consumed=True, consumed_at=consumed_at)
                )
            else:
                updated_notifications.append(notification)
        state.runtime_task_notifications = updated_notifications
        if not matched and normalized_id in state.runtime_tasks:
            consumed_task_ids.add(normalized_id)
    for task_id in consumed_task_ids:
        task = state.runtime_tasks.get(task_id)
        if task is None:
            continue
        if any(
            not notification.consumed and notification.task_id == task_id
            for notification in state.runtime_task_notifications
        ):
            continue
        metadata = dict(task.metadata)
        metadata['notification_consumed_at'] = consumed_at
        update_runtime_task(
            state,
            task_id=task_id,
            notified=True,
            metadata=metadata,
        )


def build_runtime_task_summary(
    state: RuntimeState,
    *,
    run_id: str | None = None,
    max_tasks: int = 8,
) -> dict[str, Any]:
    requested_run_id = str(run_id or '').strip() or None
    tasks = [
        task
        for task in state.runtime_tasks.values()
        if requested_run_id is None or not task.run_id or task.run_id == requested_run_id
    ]
    status_counts = {status: 0 for status in KNOWN_RUNTIME_TASK_STATUSES}
    type_counts: dict[str, int] = {}
    for task in tasks:
        status_counts[task.status] = status_counts.get(task.status, 0) + 1
        type_counts[task.task_type] = type_counts.get(task.task_type, 0) + 1
    active_tasks = [task for task in tasks if task.status in ACTIVE_RUNTIME_TASK_STATUSES]
    terminal_tasks = [task for task in tasks if task.status in TERMINAL_RUNTIME_TASK_STATUSES]
    return {
        'runtime_task_count': len(tasks),
        'active_runtime_task_count': len(active_tasks),
        'terminal_runtime_task_count': len(terminal_tasks),
        'status_counts': status_counts,
        'type_counts': type_counts,
        'active_runtime_task_ids': [task.task_id for task in active_tasks],
        'active_runtime_tasks': [_compact_runtime_task(task) for task in active_tasks[:max_tasks]],
        'recent_runtime_tasks': [_compact_runtime_task(task) for task in tasks[-max_tasks:]],
        'pending_notification_count': _pending_notification_count(state, tasks),
    }


def _ensure_runtime_task_notification_backfill(state: RuntimeState) -> None:
    for task in state.runtime_tasks.values():
        if task.notified or task.status not in (TERMINAL_RUNTIME_TASK_STATUSES | {'stalled'}):
            continue
        if any(
            not notification.consumed and notification.task_id == task.task_id
            for notification in state.runtime_task_notifications
        ):
            continue
        enqueue_runtime_task_notification(
            state,
            task_id=task.task_id,
            status=task.status,
            priority='next' if task.status == 'stalled' else 'later',
        )


def _pending_notification_count(state: RuntimeState, tasks: list[RuntimeTaskState]) -> int:
    task_ids = {task.task_id for task in tasks}
    queued = {
        notification.task_id
        for notification in getattr(state, 'runtime_task_notifications', [])
        if not notification.consumed and notification.task_id in task_ids
    }
    return len(queued)


def runtime_task_id_for_command(request_id: str) -> str:
    return f'command:{_normalize_task_id(request_id)}'


def runtime_task_id_for_mcp(server_name: str, operation: str) -> str:
    return f'mcp:{_normalize_task_id(server_name or "stdio")}:{_normalize_task_id(operation)}'


def runtime_task_id_for_lsp(session_id: str) -> str:
    return f'lsp:{_normalize_task_id(session_id)}'


def _compact_runtime_task(task: RuntimeTaskState) -> dict[str, Any]:
    return {
        'task_id': task.task_id,
        'task_type': task.task_type,
        'status': task.status,
        'description': task.description,
        'provider_kind': task.provider_kind,
        'run_id': task.run_id,
        'stage': task.stage,
        'attempt': task.attempt,
        'session_id': task.session_id,
        'output_file': task.output_file,
        'started_at': task.started_at,
        'updated_at': task.updated_at,
        'completed_at': task.completed_at,
        'notified': bool(task.notified),
    }


def _runtime_task_notification_summary(task: RuntimeTaskState) -> str:
    pieces = [f'{task.task_type} {task.task_id} is {task.status}']
    if task.output_file:
        pieces.append(f'output={task.output_file}')
    if task.metadata.get('exit_code') is not None:
        pieces.append(f'exit_code={task.metadata.get("exit_code")}')
    if task.metadata.get('stall_reason'):
        pieces.append(f'stall_reason={task.metadata.get("stall_reason")}')
    return '; '.join(pieces)


def _runtime_output_path_safety_error(path: Path, *, allow_symlink: bool) -> str:
    if path.is_symlink() and not allow_symlink:
        return 'runtime output path is a symlink'
    if path.exists() and path.is_dir():
        return 'runtime output path is a directory'
    return ''


def _normalize_task_id(task_id: str) -> str:
    normalized = str(task_id or '').strip()
    if not normalized:
        raise ValueError('runtime task id is required')
    return normalized


def _normalize_status(status: str | None) -> str:
    normalized = str(status or 'pending').strip() or 'pending'
    if normalized == 'timeout':
        normalized = 'timed_out'
    if normalized not in KNOWN_RUNTIME_TASK_STATUSES:
        raise ValueError(f'unknown runtime task status: {normalized}')
    return normalized
