from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.tasks.runtime_task_framework import (
    build_runtime_task_summary,
    kill_runtime_task,
    poll_runtime_task_output,
)
from ownpi.tools.common import metadata_for_context

if TYPE_CHECKING:
    from ownpi.runtime.tools.definitions import ToolExecutionContext


async def run_runtime_task_list(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
    state = _state(context)
    if state is None:
        return ToolResult(ok=False, tool_name='runtime_task_list', error='runtime state is not available')
    run_id = str(arguments.get('run_id', '') or '').strip() or getattr(context, 'run_id', '')
    summary = build_runtime_task_summary(
        state,
        run_id=run_id or None,
        max_tasks=max(1, min(int(arguments.get('max_tasks', 8) or 8), 32)),
    )
    return ToolResult(
        ok=True,
        tool_name='runtime_task_list',
        data={'summary': summary},
        stdout=f"runtime_tasks={summary.get('runtime_task_count', 0)} active={summary.get('active_runtime_task_count', 0)}",
        metadata=metadata_for_context(context, run_id=run_id or None),
    )


async def run_runtime_task_get(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
    state = _state(context)
    if state is None:
        return ToolResult(ok=False, tool_name='runtime_task_get', error='runtime state is not available')
    task_id = str(arguments.get('task_id', '') or '').strip()
    task = state.runtime_tasks.get(task_id)
    if task is None:
        return ToolResult(ok=False, tool_name='runtime_task_get', error=f'runtime task not found: {task_id}')
    return ToolResult(
        ok=True,
        tool_name='runtime_task_get',
        data={'task': task.to_dict()},
        stdout=f"{task.task_id} {task.task_type} {task.status}",
        metadata=metadata_for_context(context, runtime_task_id=task_id),
    )


async def run_runtime_task_poll(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
    state = _state(context)
    if state is None:
        return ToolResult(ok=False, tool_name='runtime_task_poll', error='runtime state is not available')
    task_id = str(arguments.get('task_id', '') or '').strip()
    offset = arguments.get('offset')
    try:
        timeout_arg = arguments.get('timeout_ms')
        poll_interval_arg = arguments.get('poll_interval_ms')
        payload = await poll_runtime_task_output(
            state,
            task_id=task_id,
            offset=(int(offset) if offset is not None else None),
            max_bytes=max(1, min(int(arguments.get('max_bytes', 8192) or 8192), 65536)),
            advance_offset=bool(arguments.get('advance_offset', True)),
            block=bool(arguments.get('block', False)),
            timeout_ms=max(0, min(int(timeout_arg if timeout_arg is not None else 30000), 300000)),
            poll_interval_ms=max(10, min(int(poll_interval_arg if poll_interval_arg is not None else 100), 5000)),
            cancel_checker=getattr(context, 'cancel_checker', None),
            progress_callback=getattr(context, 'progress_callback', None),
            prefer_clean_result=bool(arguments.get('prefer_clean_result', True)),
        )
    except KeyError as exc:
        return ToolResult(ok=False, tool_name='runtime_task_poll', error=str(exc))
    return ToolResult(
        ok=True,
        tool_name='runtime_task_poll',
        data=payload,
        stdout=str(payload.get('content', '')),
        metadata=metadata_for_context(context, runtime_task_id=task_id),
    )


async def run_runtime_task_kill(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
    state = _state(context)
    if state is None:
        return ToolResult(ok=False, tool_name='runtime_task_kill', error='runtime state is not available')
    task_id = str(arguments.get('task_id', '') or '').strip()
    task = kill_runtime_task(state, task_id=task_id, force=bool(arguments.get('force', False)))
    if task is None:
        return ToolResult(ok=False, tool_name='runtime_task_kill', error=f'runtime task not found: {task_id}')
    killed_count = int(task.metadata.get('killed_process_count', 0) or 0)
    return ToolResult(
        ok=True,
        tool_name='runtime_task_kill',
        data={'task': task.to_dict(), 'killed_process_count': killed_count},
        stdout=f'{task.task_id} killed_process_count={killed_count}',
        metadata=metadata_for_context(context, runtime_task_id=task_id),
    )


def _state(context: ToolExecutionContext) -> Any | None:
    return getattr(getattr(context, 'state_store', None), 'state', None)
