from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from ownpi.app.paths import AppPaths
from ownpi.runtime.mcp.dynamic_tools import refresh_mcp_tool_cache_from_configured_servers
from ownpi.runtime.state_models import MCPRuntimeState


PREFETCH_TASK_TYPES = ('mcp_capability_refresh',)
_PREFETCH_EXECUTOR = ThreadPoolExecutor(max_workers=3, thread_name_prefix='neuro-context-prefetch')


@dataclass
class ContextPrefetchTask:
    task_type: str
    status: str = 'pending'
    started_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    duration_ms: int = 0
    result_hash: str = ''
    error_summary: str = ''
    consumed_iteration: int | None = None
    result: dict[str, Any] | None = None
    future: Future | None = field(default=None, repr=False, compare=False)

    def summary(self) -> dict[str, Any]:
        return {
            'task_type': self.task_type,
            'status': self.status,
            'started_at': self.started_at,
            'completed_at': self.completed_at,
            'duration_ms': self.duration_ms,
            'result_hash': self.result_hash,
            'error_summary': self.error_summary,
            'consumed_iteration': self.consumed_iteration,
        }


@dataclass
class ContextPrefetchState:
    tasks: dict[str, ContextPrefetchTask] = field(default_factory=dict)
    generation: int = 0
    consumed_iteration: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)
    latest_consumed_tasks: list[dict[str, Any]] = field(default_factory=list)
    latest_pending_tasks: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> 'ContextPrefetchState':
        payload = data or {}
        state = cls(
            generation=max(0, int(payload.get('generation', 0) or 0)),
            consumed_iteration=max(0, int(payload.get('consumed_iteration', 0) or 0)),
            history=[dict(item) for item in payload.get('history', []) if isinstance(item, dict)][-16:],
            latest_consumed_tasks=[
                dict(item) for item in payload.get('latest_consumed_tasks', []) if isinstance(item, dict)
            ],
            latest_pending_tasks=[
                dict(item) for item in payload.get('latest_pending_tasks', []) if isinstance(item, dict)
            ],
        )
        for item in payload.get('tasks', []):
            if not isinstance(item, dict):
                continue
            task_type = str(item.get('task_type', '') or '').strip()
            if task_type not in PREFETCH_TASK_TYPES:
                continue
            state.tasks[task_type] = ContextPrefetchTask(
                task_type=task_type,
                status=str(item.get('status', 'pending') or 'pending'),
                started_at=float(item.get('started_at', 0.0) or 0.0),
                completed_at=(
                    float(item['completed_at'])
                    if item.get('completed_at') is not None
                    else None
                ),
                duration_ms=max(0, int(item.get('duration_ms', 0) or 0)),
                result_hash=str(item.get('result_hash', '') or ''),
                error_summary=str(item.get('error_summary', '') or ''),
                consumed_iteration=(
                    int(item['consumed_iteration'])
                    if item.get('consumed_iteration') is not None
                    else None
                ),
            )
        return state

    def to_dict(self) -> dict[str, Any]:
        return {
            'generation': int(self.generation or 0),
            'consumed_iteration': int(self.consumed_iteration or 0),
            'tasks': [task.summary() for task in self.tasks.values()],
            'history': [dict(item) for item in self.history[-16:] if isinstance(item, dict)],
            'latest_consumed_tasks': [
                dict(item) for item in self.latest_consumed_tasks if isinstance(item, dict)
            ],
            'latest_pending_tasks': [
                dict(item) for item in self.latest_pending_tasks if isinstance(item, dict)
            ],
        }


def start_context_prefetch(
    state: Any,
    app_paths: AppPaths | None,
    project_root: Path | None,
) -> ContextPrefetchState:
    prefetch_state = _ensure_prefetch_state(state)
    prefetch_state.generation += 1
    for task_type in PREFETCH_TASK_TYPES:
        current = prefetch_state.tasks.get(task_type)
        if current is not None and current.status == 'pending' and current.future is not None and not current.future.done():
            continue
        task = ContextPrefetchTask(task_type=task_type)
        task.future = _PREFETCH_EXECUTOR.submit(
            _run_prefetch_task,
            task_type,
            _build_prefetch_snapshot(state),
            app_paths,
            project_root,
        )
        prefetch_state.tasks[task_type] = task
    _store_prefetch_state(state, prefetch_state)
    return prefetch_state


def consume_context_prefetch(
    state: Any,
    max_wait_ms: int = 0,
    *,
    state_store: Any | None = None,
) -> dict[str, list[dict[str, Any]]]:
    prefetch_state = _ensure_prefetch_state(state)
    deadline = time.monotonic() + max(0, max_wait_ms) / 1000.0
    consumed: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for task in prefetch_state.tasks.values():
        future = task.future
        if task.status == 'pending' and future is not None:
            if max_wait_ms > 0:
                timeout = max(0.0, deadline - time.monotonic())
                try:
                    future.result(timeout=timeout)
                except TimeoutError:
                    pass
                except Exception:
                    pass
            if future.done():
                _settle_task(task)
            else:
                pending.append(task.summary())
                continue
        if task.status in {'completed', 'failed'} and task.consumed_iteration is None:
            prefetch_state.consumed_iteration += 1
            task.consumed_iteration = prefetch_state.consumed_iteration
            _apply_prefetch_result(state, task, state_store=state_store)
            consumed.append(task.summary())
            prefetch_state.history.append(task.summary())
    prefetch_state.history = prefetch_state.history[-16:]
    prefetch_state.latest_consumed_tasks = consumed
    prefetch_state.latest_pending_tasks = pending
    _store_prefetch_state(state, prefetch_state)
    return {'consumed': consumed, 'pending': pending}


def compact_context_prefetch_state(value: dict[str, Any] | ContextPrefetchState | None) -> dict[str, Any]:
    state = value if isinstance(value, ContextPrefetchState) else ContextPrefetchState.from_dict(value)
    return state.to_dict()


def _run_prefetch_task(
    task_type: str,
    state_snapshot: Any,
    app_paths: AppPaths | None,
    project_root: Path | None,
) -> dict[str, Any]:
    del project_root
    if task_type == 'mcp_capability_refresh':
        refreshed = refresh_mcp_tool_cache_from_configured_servers(
            state_snapshot,
            run_id=str(getattr(getattr(state_snapshot, 'run_metadata', None), 'run_id', '') or ''),
        )
        runtime_state = getattr(state_snapshot, 'mcp_runtime_state', None)
        return {
            'refreshed_server_count': int(refreshed or 0),
            'tool_cache': {
                str(key): [dict(item) for item in value if isinstance(item, dict)]
                for key, value in getattr(runtime_state, 'tool_cache', {}).items()
                if isinstance(value, list)
            },
            'connection_status': {
                str(key): dict(value)
                for key, value in getattr(runtime_state, 'connection_status', {}).items()
                if isinstance(value, dict)
            },
            'server_capabilities': {
                str(key): dict(value)
                for key, value in getattr(runtime_state, 'server_capabilities', {}).items()
                if isinstance(value, dict)
            },
            'tool_cache_server_count': len(getattr(runtime_state, 'tool_cache', {}) or {}),
            'stale_capabilities': dict(getattr(runtime_state, 'stale_capabilities', {}) or {}),
        }
    raise ValueError(f'unknown context prefetch task: {task_type}')


def _settle_task(task: ContextPrefetchTask) -> None:
    future = task.future
    task.completed_at = time.time()
    task.duration_ms = max(0, int((task.completed_at - task.started_at) * 1000))
    try:
        result = future.result() if future is not None else {}
    except Exception as exc:
        task.status = 'failed'
        task.error_summary = f'{type(exc).__name__}: {str(exc)[:240]}'
        task.result = None
        task.result_hash = _hash_payload({'error': task.error_summary})
        return
    task.status = 'completed'
    task.result = dict(result) if isinstance(result, dict) else {'value': result}
    task.result_hash = _hash_payload(task.result)


def _apply_prefetch_result(state: Any, task: ContextPrefetchTask, *, state_store: Any | None = None) -> None:
    del state_store
    result = task.result if isinstance(task.result, dict) else {}
    if task.task_type == 'mcp_capability_refresh' and task.status == 'completed':
        runtime_state = getattr(state, 'mcp_runtime_state', None)
        if runtime_state is not None:
            if isinstance(result.get('tool_cache'), dict):
                runtime_state.tool_cache.update(
                    {
                        str(key): [dict(item) for item in value if isinstance(item, dict)]
                        for key, value in result['tool_cache'].items()
                        if isinstance(value, list)
                    }
                )
            if isinstance(result.get('connection_status'), dict):
                runtime_state.connection_status.update(
                    {
                        str(key): dict(value)
                        for key, value in result['connection_status'].items()
                        if isinstance(value, dict)
                    }
                )
            if isinstance(result.get('server_capabilities'), dict):
                runtime_state.server_capabilities.update(
                    {
                        str(key): dict(value)
                        for key, value in result['server_capabilities'].items()
                        if isinstance(value, dict)
                    }
                )
            if isinstance(result.get('stale_capabilities'), dict):
                runtime_state.stale_capabilities = {
                    str(key): [str(item) for item in value if str(item).strip()]
                    for key, value in result['stale_capabilities'].items()
                    if isinstance(value, list)
                }
            runtime_state.events.append(
                {
                    'event': 'context_prefetch_consumed',
                    'task_type': task.task_type,
                    'result_hash': task.result_hash,
                    'refreshed_server_count': int(result.get('refreshed_server_count', 0) or 0),
                }
            )


def _ensure_prefetch_state(state: Any) -> ContextPrefetchState:
    context_state = getattr(state, 'context_state', state)
    raw = getattr(context_state, 'context_prefetch_state', None)
    if isinstance(raw, ContextPrefetchState):
        return raw
    return ContextPrefetchState.from_dict(raw if isinstance(raw, dict) else {})


def _store_prefetch_state(state: Any, prefetch_state: ContextPrefetchState) -> None:
    context_state = getattr(state, 'context_state', state)
    if hasattr(context_state, 'context_prefetch_state'):
        context_state.context_prefetch_state = prefetch_state


def _build_prefetch_snapshot(state: Any) -> Any:
    runtime_state = getattr(state, 'mcp_runtime_state', None)
    snapshot_mcp = MCPRuntimeState()
    if runtime_state is not None:
        snapshot_mcp.configured_servers = [
            dict(item) for item in getattr(runtime_state, 'configured_servers', []) if isinstance(item, dict)
        ]
        snapshot_mcp.tool_cache = {
            str(key): [dict(item) for item in value if isinstance(item, dict)]
            for key, value in getattr(runtime_state, 'tool_cache', {}).items()
            if isinstance(value, list)
        }
        snapshot_mcp.connection_status = {
            str(key): dict(value)
            for key, value in getattr(runtime_state, 'connection_status', {}).items()
            if isinstance(value, dict)
        }
        snapshot_mcp.server_capabilities = {
            str(key): dict(value)
            for key, value in getattr(runtime_state, 'server_capabilities', {}).items()
            if isinstance(value, dict)
        }
        snapshot_mcp.stale_capabilities = {
            str(key): [str(item) for item in value if str(item).strip()]
            for key, value in getattr(runtime_state, 'stale_capabilities', {}).items()
            if isinstance(value, list)
        }
    return type(
        'ContextPrefetchSnapshot',
        (),
        {
            'mcp_runtime_state': snapshot_mcp,
            'run_metadata': getattr(state, 'run_metadata', None),
        },
    )()


def _hash_payload(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()[:16]
