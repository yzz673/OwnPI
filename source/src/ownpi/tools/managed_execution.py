from __future__ import annotations

from pathlib import Path
from typing import Any

from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.execution.job_contract import ExecutionArtifactLayout
from ownpi.runtime.execution.state import ExecutionHandle, ExecutionRecord
from ownpi.runtime.process_owner.manager import ProcessOwnerManager
from ownpi.runtime.process_owner.protocol import ProcessOwnerProtocolError
from ownpi.runtime.process_owner.remote_transport import RemoteTransportError
from ownpi.runtime.tools.definitions import ToolExecutionContext
from ownpi.tools.process_denial import process_denial_projection


_MONITOR_STREAM_PREVIEW_CHARS = 8_000


async def run_execution_monitor(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> ToolResult:
    record = _record_from_context(context, arguments)
    if record is None:
        return _failure('execution_monitor', 'execution_not_found', 'unknown execution_id')
    if record.terminal:
        return terminal_execution_result(record, tool_name='execution_monitor')
    return await _monitor_handle(
        context=context,
        tool_name='execution_monitor',
        handle=record.handle,
        wait_ms=_bounded_wait_ms(arguments.get('wait_ms')),
    )


async def run_execution_logs(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> ToolResult:
    record = _record_from_context(context, arguments)
    if record is None:
        return _failure('execution_logs', 'execution_not_found', 'unknown execution_id')
    tail_chars = _bounded_tail_chars(arguments.get('tail_chars'))
    stdout, stdout_error = _read_tail(record.handle.stdout_ref, tail_chars=tail_chars)
    stderr, stderr_error = _read_tail(record.handle.stderr_ref, tail_chars=tail_chars)
    log_errors = {
        key: value
        for key, value in {
            'stdout': stdout_error,
            'stderr': stderr_error,
        }.items()
        if value
    }
    return ToolResult(
        ok=not log_errors,
        tool_name='execution_logs',
        data={
            'execution_id': record.execution_id,
            'execution_handle': record.handle.to_dict(),
            'stdout': stdout,
            'stderr': stderr,
            'log_refs': _log_refs(record.handle),
            'log_errors': log_errors,
        },
        stdout=stdout or None,
        stderr=stderr or None,
        error=(
            '; '.join(f'{stream}: {error}' for stream, error in log_errors.items())
            if log_errors
            else None
        ),
        metadata={
            'backend': 'process_owner',
            'failure_class': 'execution_log_unavailable' if log_errors else '',
        },
    )


async def run_execution_kill(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> ToolResult:
    record = _record_from_context(context, arguments)
    if record is None:
        return _failure('execution_kill', 'execution_not_found', 'unknown execution_id')
    if record.terminal:
        return terminal_execution_result(record, tool_name='execution_kill')
    manager = _manager(context, tool_name='execution_kill')
    if isinstance(manager, ToolResult):
        return manager
    try:
        await manager.terminate_process_async(
            owner_id=record.handle.owner_id,
            process_id=record.handle.process_id,
        )
    except (KeyError, OSError, ProcessOwnerProtocolError, RemoteTransportError) as exc:
        return _failure('execution_kill', 'process_owner_lost', str(exc))
    return await _monitor_handle(
        context=context,
        tool_name='execution_kill',
        handle=record.handle,
        wait_ms=3000,
        killed=True,
    )


async def _monitor_handle(
    *,
    context: ToolExecutionContext,
    tool_name: str,
    handle: ExecutionHandle,
    wait_ms: int,
    killed: bool = False,
) -> ToolResult:
    manager = _manager(context, tool_name=tool_name)
    if isinstance(manager, ToolResult):
        return manager
    try:
        snapshot = await manager.read_process_async(
            owner_id=handle.owner_id,
            process_id=handle.process_id,
            after_seq=0,
            wait_ms=wait_ms,
            max_bytes=64 * 1024,
        )
    except (KeyError, OSError, ProcessOwnerProtocolError, RemoteTransportError) as exc:
        registry = getattr(getattr(context.state_store, 'state', None), 'execution_registry', None)
        if context.execution_runtime_service is not None:
            registry = context.execution_runtime_service.registry_snapshot()
        current = next((item for item in registry.records
                        if item.handle.owner_id == handle.owner_id
                        and item.handle.process_id == handle.process_id), None) if registry else None
        if current is not None and current.terminal:
            return terminal_execution_result(current, tool_name=tool_name)
        return ToolResult(
            ok=False,
            tool_name=tool_name,
            data={
                'execution_handle': handle.to_dict(),
                'monitor': {
                    'job_state': 'unknown',
                    'failure_class': 'process_owner_lost',
                },
            },
            error=str(exc),
            metadata={
                'backend': 'process_owner',
                'failure_class': 'process_owner_lost',
            },
        )
    closed = bool(snapshot.get('closed', False))
    timed_out = bool(snapshot.get('timed_out', False))
    exit_code = snapshot.get('exit_code')
    denial = process_denial_projection(
        snapshot,
        sandbox_backend=handle.sandbox_backend,
        effective_permissions=context.effective_permissions,
        interrupted=killed,
    )
    if not closed:
        job_state = 'running'
    elif killed or bool(snapshot.get('interrupted')):
        job_state = 'terminated'
    elif timed_out or denial:
        job_state = 'failed'
    elif int(exit_code or 0) == 0:
        job_state = 'completed'
    else:
        job_state = 'failed'
    recent_stdout, recent_stderr = _recent_stream_output(snapshot.get('chunks'))
    artifact_contract = _artifact_contract(handle)
    monitor = {
        'job_state': job_state,
        'exit_code': exit_code,
        'timed_out': timed_out,
        'output_truncated': bool(snapshot.get('output_truncated', False)),
        'recent_stdout': recent_stdout,
        'recent_stderr': recent_stderr,
        'omitted_output_through_seq': int(
            snapshot.get('omitted_output_through_seq', 0) or 0
        ),
        'log_refs': _log_refs(handle),
        'result_refs': _result_refs(handle) if closed else [],
        **denial,
    }
    if artifact_contract is not None:
        monitor.update(
            {
                'artifact_contract_bound': True,
                'artifacts_ready': artifact_contract['artifacts_ready'],
                'missing_required_artifacts': artifact_contract[
                    'missing_required_artifacts'
                ],
                'light_artifacts': artifact_contract['light_artifacts'],
                'artifact_contract': artifact_contract,
            }
        )
    control_succeeded = job_state in {'running', 'completed'} or (
        killed and job_state == 'terminated'
    )
    return ToolResult(
        ok=control_succeeded,
        tool_name=tool_name,
        data={
            'execution_handle': handle.to_dict(),
            'monitor': monitor,
            **denial,
        },
        stdout=(recent_stdout + recent_stderr) or None,
        error=(None if control_succeeded else f'execution {job_state}'),
        metadata={
            'backend': 'process_owner',
            'terminal_status': job_state,
            'owner_id': handle.owner_id,
            'process_id': handle.process_id,
            'timed_out': timed_out,
            **denial,
        },
    )


def _record_from_context(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> ExecutionRecord | None:
    execution_id = str(arguments.get('execution_id', '') or '').strip()
    state_store = context.state_store
    registry = getattr(getattr(state_store, 'state', None), 'execution_registry', None)
    service = context.execution_runtime_service
    if service is not None:
        registry = service.registry_snapshot()
    if registry is None or not execution_id:
        return None
    record = registry.get(execution_id)
    if record is not None and getattr(context, 'actor_kind', '') == 'subagent':
        from ownpi.runtime.rollout.resource_projection import project_resources

        events = getattr(getattr(state_store, 'run_event_store', None), 'events', ())
        resources = project_resources(events).child_resources(str(context.actor_id or ''))
        if not any(item['owner_id'] == record.handle.owner_id
                   and item['process_id'] == record.handle.process_id for item in resources):
            return None
    return record


def terminal_execution_result(record: ExecutionRecord, *, tool_name: str) -> ToolResult:
    return ToolResult(
        ok=record.status == 'terminal_success', tool_name=tool_name,
        data={'execution_id': record.execution_id,
              'execution_handle': record.handle.to_dict(),
              'monitor': {'job_state': 'completed' if record.status == 'terminal_success' else 'failed',
                          'issue': record.issue,
                          'log_refs': _log_refs(record.handle),
                          'result_refs': list(record.result_refs)}},
        error=None if record.status == 'terminal_success' else record.issue or 'execution failed',
        metadata={'backend': 'process_owner', 'recorded_terminal': True},
    )


def _manager(
    context: ToolExecutionContext,
    *,
    tool_name: str,
) -> ProcessOwnerManager | ToolResult:
    manager = context.process_owner_manager
    if isinstance(manager, ProcessOwnerManager):
        return manager
    return _failure(
        tool_name,
        'process_owner_unavailable',
        f'{tool_name} requires the run-scoped process owner manager',
    )


def _result_refs(handle: ExecutionHandle) -> list[str]:
    refs = [handle.stdout_ref, handle.stderr_ref]
    if handle.artifact_root:
        light = Path(handle.artifact_root) / 'light'
        if light.is_dir():
            refs.extend(str(path.resolve()) for path in sorted(light.rglob('*')) if path.is_file())
    return list(dict.fromkeys(ref for ref in refs if ref))


def _artifact_contract(handle: ExecutionHandle) -> dict[str, Any] | None:
    if not handle.artifact_root:
        return None
    layout = ExecutionArtifactLayout.from_artifact_root(handle.artifact_root)
    light_dir = Path(layout.light_artifact_dir)
    light_artifacts = (
        [str(path.resolve()) for path in sorted(light_dir.rglob('*')) if path.is_file()]
        if light_dir.is_dir()
        else []
    )
    names = {Path(path).name for path in light_artifacts}
    control_names = {'status.json', 'manifest.json', 'job_handle.json', 'exit_status.json'}
    missing = [
        name for name in ('status.json', 'manifest.json') if name not in names
    ]
    if not any(name not in control_names for name in names):
        missing.append('non_control_result_artifact')
    return {
        'artifact_root': layout.artifact_root,
        'artifacts_ready': not missing,
        'missing_required_artifacts': missing,
        'light_artifacts': light_artifacts,
    }


def _log_refs(handle: ExecutionHandle) -> dict[str, str]:
    return {
        key: value
        for key, value in {
            'stdout': handle.stdout_ref,
            'stderr': handle.stderr_ref,
        }.items()
        if value
    }


def _recent_stream_output(value: Any) -> tuple[str, str]:
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            chunk = str(item.get('chunk', '') or '')
            stream = str(item.get('stream', 'stdout') or 'stdout').lower()
            if stream == 'stderr':
                stderr_parts.append(chunk)
            else:
                stdout_parts.append(chunk)
    return (
        ''.join(stdout_parts)[-_MONITOR_STREAM_PREVIEW_CHARS:],
        ''.join(stderr_parts)[-_MONITOR_STREAM_PREVIEW_CHARS:],
    )


def _read_tail(value: str, *, tail_chars: int) -> tuple[str, str]:
    if not value:
        return '', 'log reference is missing'
    path = Path(value)
    try:
        content = path.read_text(encoding='utf-8', errors='replace')
    except OSError as exc:
        return '', str(exc)
    return content[-tail_chars:], ''


def _bounded_wait_ms(value: Any) -> int:
    try:
        return max(0, min(30_000, int(value or 0)))
    except (TypeError, ValueError):
        return 0


def _bounded_tail_chars(value: Any) -> int:
    try:
        return max(1, min(200_000, int(value or 20_000)))
    except (TypeError, ValueError):
        return 20_000


def _failure(tool_name: str, failure_class: str, error: str) -> ToolResult:
    return ToolResult(
        ok=False,
        tool_name=tool_name,
        data={'status': failure_class, 'failure_class': failure_class},
        error=error,
        metadata={'backend': 'process_owner', 'failure_class': failure_class},
    )


__all__ = [
    'run_execution_kill',
    'run_execution_logs',
    'run_execution_monitor',
]
