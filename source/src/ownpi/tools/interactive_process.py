from __future__ import annotations

import asyncio
import hashlib
import os
import shlex
import time
import uuid
from collections.abc import Callable
from concurrent.futures import CancelledError
from pathlib import Path
from typing import Any

from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.environment import (
    EnvironmentDescriptor,
    EnvironmentExecRequest,
    ResolvedEnvironmentSelection,
    build_environment_provider,
    preflight_environment_provider,
)
from ownpi.runtime.environment.contracts import (
    build_permission_enforcement_projection,
)
from ownpi.runtime.limits.projection import DEFAULT_EXEC_OUTPUT_TOKENS
from ownpi.runtime.limits.recovery import (
    DEFAULT_EXEC_POLL_YIELD_TIME_MS,
    DEFAULT_EXEC_WRITE_YIELD_TIME_MS,
    DEFAULT_EXEC_YIELD_TIME_MS,
    MAX_EXEC_YIELD_TIME_MS,
)
from ownpi.runtime.permissions.models import EffectivePermissions
from ownpi.runtime.process_owner.protocol import ProcessOwnerProtocolError
from ownpi.runtime.process_owner.remote_transport import RemoteTransportError
from ownpi.runtime.process_owner.manager import (
    ProcessOwnerManager,
    ProcessOwnerSessionView,
)
from ownpi.runtime.tools.definitions import ToolExecutionContext
from ownpi.tools.process_denial import process_denial_projection


# TODO: remove the internal execution-contract timeout once sandbox execution
# no longer requires a positive deadline. This is not model-visible; command
# lifetime is controlled by yield_time_ms, sessions, and execution monitoring.
_NO_SANDBOX_DEADLINE_SECONDS = 2_147_483_647


async def run_exec_command(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> ToolResult:
    command_text = str(arguments.get('cmd', '') or '')
    program = str(arguments.get('program', '') or '').strip()
    command_args = (
        [str(item) for item in arguments.get('args', [])]
        if isinstance(arguments.get('args', []), list)
        else []
    )
    selection = _selection_from_context(context)
    cwd = _resolve_workdir(
        context,
        str(arguments.get('workdir') or arguments.get('cwd') or ''),
    )
    shell = str(arguments.get('shell', '') or '').strip() or _default_shell()
    login = bool(arguments.get('login', True))
    command = (
        tuple([program, *command_args])
        if program
        else (shell, '-lc' if login else '-c', command_text)
    )
    tty_requested = (
        bool(arguments.get('tty'))
        if 'tty' in arguments
        else bool(arguments.get('use_pty', False))
    )
    process_id = f'process-{uuid.uuid4().hex}'
    stdout_path, stderr_path = _execution_log_paths(context, process_id=process_id)
    env_overrides = _string_mapping(arguments.get('env_overrides'))
    request = EnvironmentExecRequest(
        request_id=f'exec-command-{uuid.uuid4().hex[:16]}',
        tool_name='exec_command',
        command=command,
        cwd=str(cwd),
        execution_class='command_exec',
        pty_mode='pty' if tty_requested else 'pipe',
        pty_columns=_optional_positive_int(arguments.get('pty_columns')),
        pty_rows=_optional_positive_int(arguments.get('pty_rows')),
        timeout_seconds=_NO_SANDBOX_DEADLINE_SECONDS,
        env_overrides=env_overrides,
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        run_id=context.run_id,
        metadata={
            'actor_id': str(context.actor_id or ''),
            'effective_permissions': _effective_permissions_payload(context),
            'interactive_stdin': True,
            'sandbox_permissions': str(arguments.get('sandbox_permissions', 'use_default') or 'use_default'),
            'justification': str(arguments.get('justification', '') or ''),
            'prefix_rule': list(arguments.get('prefix_rule', []))
            if isinstance(arguments.get('prefix_rule'), list)
            else [],
        },
    )
    preflight = preflight_environment_provider(
        selection=selection,
        config_snapshot=context.environment_config_snapshot,
        request=request,
    )
    if not preflight.ok:
        return _failure('exec_command', preflight.violation_class or 'provider_unavailable', preflight.unavailable_reason)
    provider = build_environment_provider(
        project_root=context.project_root,
        selection=selection,
        config_snapshot=context.environment_config_snapshot,
    )
    provider.prepare(
        descriptor=selection.descriptor,
        sandbox_policy=selection.sandbox_policy,
    )
    manager = context.process_owner_manager
    if not isinstance(manager, ProcessOwnerManager):
        return _failure(
            'exec_command',
            'process_owner_unavailable',
            'exec_command requires the run-scoped process owner manager',
        )
    launch = provider.prepare_session_launch(request=request)
    owner_id = _owner_id_for_context(context)
    from ownpi.runtime.execution.result_sink import publish_acquired_execution_on_failure

    view = await manager.start_session_async(
        acquisition_failure_callback=lambda view: publish_acquired_execution_on_failure(context, view),
        owner_id=owner_id,
        run_id=context.run_id,
        actor_id=str(
            context.process_owner_actor_id or context.actor_id or ''
        ),
        argv=list(launch.argv),
        cwd=launch.cwd,
        env=launch.env,
        tty=launch.tty,
        pipe_stdin=launch.pipe_stdin,
        pty_columns=launch.pty_columns,
        pty_rows=launch.pty_rows,
        process_id=process_id,
        sandbox_backend=launch.sandbox_backend,
        timeout_seconds=_NO_SANDBOX_DEADLINE_SECONDS,
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        command_display=(
            command_text
            if not program
            else shlex.join([program, *command_args])
        ),
    )
    try:
        return await _wait_and_project_owner(
            context=context,
            tool_name='exec_command',
            manager=manager,
            view=view,
            effective_permissions=context.effective_permissions,
            yield_time_ms=_bounded_yield_time(
                arguments.get('yield_time_ms'),
                DEFAULT_EXEC_YIELD_TIME_MS,
            ),
            max_output_tokens=_bounded_output_tokens(arguments.get('max_output_tokens')),
            wait_for_closed=not launch.tty,
            cancel_checker=(
                context.cancel_checker if callable(context.cancel_checker) else None
            ),
        )
    except (Exception, asyncio.CancelledError):
        publish_acquired_execution_on_failure(context, view)
        raise


async def run_write_stdin(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> ToolResult:
    public_session_id = arguments.get('session_id')
    execution_id = str(arguments.get('execution_id') or '')
    if (public_session_id is None) == (not execution_id):
        return _failure('write_stdin', 'invalid_arguments', 'Provide exactly one of session_id or execution_id')
    chars = str(arguments.get('chars', '') or '')
    manager = context.process_owner_manager
    if not isinstance(manager, ProcessOwnerManager):
        return _failure(
            'write_stdin',
            'process_owner_unavailable',
            'write_stdin requires the run-scoped process owner manager',
        )
    try:
        if execution_id:
            from ownpi.tools.managed_execution import _record_from_context, terminal_execution_result

            record = _record_from_context(context, arguments)
            if record is None:
                return _failure('write_stdin', 'execution_not_found', 'unknown execution_id')
            if record.terminal:
                if chars:
                    return _failure('write_stdin', 'stdin_closed', 'execution has already ended')
                return terminal_execution_result(record, tool_name='write_stdin')
            view = manager.execution_view(record.handle, run_id=context.run_id,
                                          actor_id=str(context.process_owner_actor_id or context.actor_id or ''))
        else:
            view = manager.session_for(
                int(public_session_id), run_id=context.run_id,
                actor_id=str(context.process_owner_actor_id or context.actor_id or ''),
            )
        if chars:
            await manager.write_session_async(
                view,
                chunk=chars,
                write_id=f'write-{uuid.uuid4().hex}',
            )
        default_yield = (
            DEFAULT_EXEC_WRITE_YIELD_TIME_MS
            if chars
            else DEFAULT_EXEC_POLL_YIELD_TIME_MS
        )
        return await _wait_and_project_owner(
            context=context,
            tool_name='write_stdin',
            manager=manager,
            view=view,
            effective_permissions=context.effective_permissions,
            yield_time_ms=_bounded_yield_time(
                arguments.get('yield_time_ms'),
                default_yield,
            ),
            max_output_tokens=_bounded_output_tokens(
                arguments.get('max_output_tokens')
            ),
            wait_for_closed=True,
            cancel_checker=(
                context.cancel_checker if callable(context.cancel_checker) else None
            ),
        )
    except PermissionError as exc:
        return _failure('write_stdin', 'session_permission_denied', str(exc))
    except KeyError:
        return _failure('write_stdin', 'session_lost', f'unknown environment session: {public_session_id}')
    except (BrokenPipeError, OSError, ProcessOwnerProtocolError, RemoteTransportError) as exc:
        if execution_id and not chars:
            record = _record_from_context(context, arguments)
            if record is not None and record.terminal:
                return terminal_execution_result(record, tool_name='write_stdin')
        return _failure('write_stdin', 'stdin_closed', str(exc))


async def _wait_and_project_owner(
    *,
    tool_name: str,
    manager: ProcessOwnerManager,
    view: ProcessOwnerSessionView,
    yield_time_ms: int,
    max_output_tokens: int,
    wait_for_closed: bool = False,
    cancel_checker: Callable[[], bool] | None = None,
    effective_permissions: EffectivePermissions | dict[str, Any] | None = None,
    context: ToolExecutionContext | None = None,
) -> ToolResult:
    started = time.monotonic()
    deadline = started + max(0, int(yield_time_ms)) / 1000.0
    remaining_bytes = max(1, max_output_tokens * 4)
    output_parts: list[str] = []
    response: dict[str, Any] = {}
    wait_cancelled = False
    while True:
        wait_cancelled = bool(cancel_checker is not None and cancel_checker())
        remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
        try:
            response = await manager.read_session_async(
                view,
                wait_ms=0 if wait_cancelled else min(remaining_ms, 100)
                if cancel_checker is not None else remaining_ms,
                max_bytes=remaining_bytes,
                cancel_checker=cancel_checker,
            )
        except CancelledError:
            wait_cancelled = True
            break
        chunks = response.get('chunks', [])
        if isinstance(chunks, list):
            for item in chunks:
                if not isinstance(item, dict):
                    continue
                chunk = str(item.get('chunk', '') or '')
                output_parts.append(chunk)
                remaining_bytes = max(
                    0,
                    remaining_bytes - len(chunk.encode('utf-8')),
                )
        if bool(response.get('closed', False)):
            wait_cancelled = False
            break
        if wait_cancelled or (cancel_checker is not None and cancel_checker()):
            wait_cancelled = True
            break
        if not wait_for_closed or remaining_bytes <= 0 or time.monotonic() >= deadline:
            break
    output = ''.join(output_parts)
    terminal = bool(response.get('closed', False))
    timed_out = bool(response.get('timed_out', False))
    exit_code = int(response.get('exit_code', 0) or 0)
    interrupted = bool(response.get('interrupted', False))
    denial = process_denial_projection(
        response,
        sandbox_backend=view.sandbox_backend,
        effective_permissions=effective_permissions,
        interrupted=interrupted,
    )
    sandbox_denied = bool(denial)
    final_seq = int(response.get('final_seq', 0) or 0)
    if terminal and final_seq > 0:
        await _release_observed_execution(
            context=context, manager=manager, view=view, tool_name=tool_name,
            response=response, final_seq=final_seq,
            failed=timed_out or interrupted or sandbox_denied or exit_code != 0,
        )
    data: dict[str, Any] = {
        'output': output,
        'wall_time_seconds': max(0.0, time.monotonic() - started),
        'original_token_count': max(0, (len(output) + 3) // 4),
        'execution_handle': _execution_handle(
            view,
        ),
        'log_refs': {
            key: value
            for key, value in {
                'stdout': view.stdout_ref,
                'stderr': view.stderr_ref,
            }.items()
            if value
        },
        'output_truncated': bool(response.get('output_truncated', False)),
        'omitted_output_through_seq': int(
            response.get('omitted_output_through_seq', 0) or 0
        ),
    }
    if output:
        data['chunk_id'] = hashlib.sha256(output.encode('utf-8')).hexdigest()[:12]
    data.update(denial)
    if terminal:
        data['exit_code'] = response.get('exit_code')
        if timed_out:
            data['timeout_class'] = 'wall_clock'
    else:
        data['session_id'] = view.public_session_id
        data['execution_id'] = _execution_id(view)
    return ToolResult(
        ok=(
            not interrupted
            and not wait_cancelled
            and not sandbox_denied
            and (
                not terminal
                or (not timed_out and int(response.get('exit_code', 0) or 0) == 0)
            )
        ),
        tool_name=tool_name,
        data=data,
        stdout=output or None,
        error=(
            'execution wait cancelled by Runtime control request'
            if wait_cancelled
            else 'process interrupted by Runtime control request'
            if interrupted
            else 'process timed out'
            if terminal and timed_out
            else 'sandbox denied'
            if sandbox_denied
            else 'process failed'
            if terminal and exit_code != 0
            else None
        ),
        metadata={
            'backend': 'process_owner',
            'owner_id': view.owner_id,
            'process_id': view.process_id,
            'terminal_status': (
                'running'
                if not terminal
                else
                'interrupted'
                if interrupted
                else 'timeout'
                if timed_out
                else 'completed'
            ),
            'stdout_ref': view.stdout_ref,
            'stderr_ref': view.stderr_ref,
            'timed_out': timed_out,
            'interrupted': interrupted,
            'wait_cancelled': wait_cancelled,
            **denial,
        },
    )


async def _release_observed_execution(
    *, context: ToolExecutionContext | None, manager: ProcessOwnerManager,
    view: ProcessOwnerSessionView, tool_name: str, response: dict[str, Any],
    final_seq: int, failed: bool,
) -> None:
    from ownpi.runtime.events import now_utc_iso
    from ownpi.runtime.execution.state import ExecutionHandle, ExecutionObservation, ExecutionOrigin

    service = getattr(context, 'execution_runtime_service', None)
    execution_id = _execution_id(view)
    # Initial bounded commands have never entered long-execution management.
    if service is None or (tool_name == 'exec_command'
                          and service.registry_snapshot().get(execution_id) is None):
        await manager.release_session_async(view, final_seq)
        return
    binding = manager.bindings.get(view.owner_id)
    if binding is not None:
        service.record_owner_binding(binding)
    observation = ExecutionObservation(
        execution_id=execution_id, status='terminal_failure' if failed else 'terminal_success',
        handle=ExecutionHandle.from_dict(_execution_handle(view)),
        origin=ExecutionOrigin(
            stage=str(getattr(context.stage, 'value', context.stage)),
            attempt=context.attempt, continuation_index=context.continuation_index,
            tool_name=tool_name,
        ),
        observed_at=now_utc_iso(),
        result_refs=tuple(ref for ref in (view.stdout_ref, view.stderr_ref) if ref),
        evidence={**{key: response[key] for key in (
            'closed', 'exit_code', 'final_seq', 'timed_out', 'interrupted',
        ) if key in response}, 'request_id': str(context.tool_call_id or '')},
    )
    if not service.confirm_release_evidence(observation, final_seq=final_seq):
        return
    try:
        await manager.release_session_async(view, final_seq)
    except BaseException:
        # The manager finishes owned cleanup before propagating cancellation.
        if view.released:
            service.record_external_release(execution_id, final_seq=final_seq)
        else:
            service.cancel_external_release(execution_id)
        raise
    service.record_external_release(execution_id, final_seq=final_seq)


def _execution_handle(
    view: ProcessOwnerSessionView,
) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            'backend': 'local_process',
            'tool_name': 'exec_command',
            'owner_id': view.owner_id,
            'process_id': view.process_id,
            'public_session_id': view.public_session_id,
            'local_workdir': view.cwd,
            'stdout_ref': view.stdout_ref,
            'stderr_ref': view.stderr_ref,
            'timeout_seconds': view.timeout_seconds,
            'sandbox_backend': view.sandbox_backend,
            'command_display': view.command_display,
        }.items()
        if value not in {'', 0}
    }


def _execution_id(view: ProcessOwnerSessionView) -> str:
    return f'local:{view.owner_id}:{view.process_id}'


def _selection_from_context(context: ToolExecutionContext) -> ResolvedEnvironmentSelection:
    descriptor = EnvironmentDescriptor.from_dict(
        context.environment_descriptor if isinstance(context.environment_descriptor, dict) else {}
    )
    effective_permissions = _effective_permissions(context)
    provider_kind = str(
        context.environment_provider_kind or descriptor.provider_kind or 'host_local'
    )
    sandbox_policy = build_permission_enforcement_projection(
        effective_permissions=effective_permissions,
        provider_kind=provider_kind,
        requires_isolation=bool(descriptor.sandboxed),
        remote_required=(provider_kind == 'ssh_remote'),
        host_managed=descriptor.host_managed,
        reason=str(context.environment_selection_reason or ''),
    )
    return ResolvedEnvironmentSelection(
        provider_kind=provider_kind,
        descriptor=descriptor,
        sandbox_policy=sandbox_policy,
        selection_reason=str(context.environment_selection_reason or ''),
        environment_affinity=str(context.environment_affinity or 'host_required'),
        execution_class=str(context.execution_class or 'command_exec'),
        backend_kind='local_exec',
    )


def _owner_id_for_context(context: ToolExecutionContext) -> str:
    run_id = str(context.run_id or 'anonymous').strip().replace(' ', '-')
    return f'run-{run_id}'


def _resolve_workdir(context: ToolExecutionContext, raw_workdir: str) -> Path:
    path = Path(raw_workdir).expanduser() if raw_workdir else context.project_root
    if not path.is_absolute():
        path = context.project_root / path
    return path.resolve()


def _execution_log_paths(
    context: ToolExecutionContext,
    *,
    process_id: str,
) -> tuple[Path, Path]:
    root = Path(context.run_dir or context.project_root / '.ownpi-runtime')
    log_dir = root.expanduser().resolve() / 'execution-logs' / process_id
    return log_dir / 'stdout.log', log_dir / 'stderr.log'


def _string_mapping(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): str(item)
        for key, item in value.items()
        if str(key).strip()
    }


def _optional_positive_int(value: Any) -> int | None:
    if value in (None, ''):
        return None
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return None


def _bounded_yield_time(value: Any, default: int) -> int:
    try:
        return max(
            DEFAULT_EXEC_WRITE_YIELD_TIME_MS,
            min(MAX_EXEC_YIELD_TIME_MS, int(value if value is not None else default)),
        )
    except (TypeError, ValueError):
        return default


def _bounded_output_tokens(value: Any) -> int:
    try:
        return max(1, int(value if value is not None else DEFAULT_EXEC_OUTPUT_TOKENS))
    except (TypeError, ValueError):
        return DEFAULT_EXEC_OUTPUT_TOKENS


def _default_shell() -> str:
    candidate = str(os.environ.get('SHELL', '') or '')
    return candidate if candidate and Path(candidate).exists() else '/bin/sh'


def _effective_permissions_payload(context: ToolExecutionContext) -> dict[str, Any]:
    payload = _effective_permissions(context).to_dict()
    if not isinstance(payload, dict):
        raise RuntimeError('exec_command canonical effective permissions are malformed')
    return payload


def _effective_permissions(context: ToolExecutionContext) -> EffectivePermissions:
    value = context.effective_permissions
    if isinstance(value, EffectivePermissions):
        return value
    if isinstance(value, dict):
        return EffectivePermissions.from_dict(value)
    raise RuntimeError('exec_command requires canonical effective permissions')


def _failure(tool_name: str, failure_class: str, error: str) -> ToolResult:
    return ToolResult(
        ok=False,
        tool_name=tool_name,
        data={'status': failure_class, 'failure_class': failure_class},
        error=error,
        metadata={'backend': 'environment_session'},
    )
