from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import uuid
from typing import Any

from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.environment import (
    EnvironmentDescriptor,
    EnvironmentExecOutcome,
    EnvironmentExecRequest,
    EnvironmentExecutionReceipt,
    build_environment_provider,
)
from ownpi.runtime.environment.resolver import materialize_environment_artifact_root
from ownpi.runtime.environment.contracts import (
    build_permission_enforcement_projection,
    build_sandbox_provider_metadata,
)
from ownpi.runtime.permissions.models import EffectivePermissions
from ownpi.runtime.environment.preflight import (
    EnvironmentPreflightDecision,
    classify_environment_failure,
    preflight_environment_provider,
)
from ownpi.runtime.environment.providers import ResolvedEnvironmentSelection
from ownpi.runtime.limits.projection import DEFAULT_ENVIRONMENT_COMMAND_OUTPUT_CHARS
from ownpi.runtime.process_owner.bounded_execution import execute_bounded_process
from ownpi.runtime.process_owner.manager import ProcessOwnerManager
from ownpi.runtime.tasks.runtime_task_framework import (
    complete_runtime_task,
    fail_runtime_task,
    register_runtime_task,
    runtime_task_id_for_command,
)
from ownpi.tools.common import metadata_for_context

@dataclass(frozen=True)
class CommandExecutionResult:
    session_id: str
    backend_kind: str
    provider_kind: str
    status: str
    command_display: str
    cwd: str
    started_at: str
    heartbeat_at: str
    completed_at: str
    elapsed_ms: int
    exit_code: int | None
    heartbeat_count: int
    stdout_preview: str
    stderr_preview: str
    stdout_truncated: bool
    stderr_truncated: bool
    timeout_class: str | None = None
    stdout_ref: str | None = None
    stderr_ref: str | None = None
    pty_mode: str = 'pipe'
    pty_columns: int | None = None
    pty_rows: int | None = None
    environment_selection_reason: str = ''
    environment_receipt_id: str | None = None
    environment_descriptor: dict[str, Any] | None = None
    output_replay_truncated: bool = False
    omitted_output_through_seq: int = 0


async def run_local_command(
    *,
    context: Any,
    tool_name: str,
    command: list[str] | None = None,
    shell_command: str | None = None,
    cwd: Path,
    timeout_seconds: int,
    env: dict[str, str] | None = None,
    max_output_chars: int = DEFAULT_ENVIRONMENT_COMMAND_OUTPUT_CHARS,
    use_pty: bool = False,
    pty_columns: int = 120,
    pty_rows: int = 40,
    metadata: dict[str, Any] | None = None,
    command_display_override: str | None = None,
) -> ToolResult:
    if bool(command) == bool(shell_command):
        raise ValueError('provide exactly one of command or shell_command')
    selection = _selection_from_context(context, use_pty=use_pty)
    command_display = str(command_display_override or '').strip() or _display_command(
        command,
        shell_command,
    )
    request = EnvironmentExecRequest(
        request_id=f'cmd-{uuid.uuid4().hex[:12]}',
        tool_name=tool_name,
        command=tuple(command or ()),
        shell_command=shell_command,
        cwd=str(cwd),
        env_overrides=dict(env or {}),
        timeout_seconds=timeout_seconds,
        execution_class=str(getattr(context, 'execution_class', 'interactive_pty' if use_pty else 'command_exec') or ('interactive_pty' if use_pty else 'command_exec')),
        pty_mode='pty' if use_pty else 'pipe',
        pty_columns=pty_columns if use_pty else None,
        pty_rows=pty_rows if use_pty else None,
        run_id=_run_id_from_context(context),
        metadata={
            **_execution_contract_metadata_from_context(context),
            **dict(metadata or {}),
            'command_display_override': command_display,
        },
    )
    _register_command_runtime_task(
        context=context,
        request=request,
        tool_name=tool_name,
        command_display=command_display,
        backend_kind='local_pty' if use_pty else 'local_exec',
        provider_kind=selection.provider_kind,
        environment_selection_reason=selection.selection_reason,
    )
    try:
        preflight = preflight_environment_provider(
            selection=selection,
            config_snapshot=getattr(context, 'environment_config_snapshot', None),
            request=request,
        )
        if not preflight.ok:
            return _provider_failure_result(
                context=context,
                tool_name=tool_name,
                request=request,
                selection=selection,
                error=preflight.unavailable_reason,
                backend_kind='local_pty' if use_pty else 'local_exec',
                preflight_decision=preflight,
            )
        materialize_environment_artifact_root(selection)
        provider = build_environment_provider(
            project_root=Path(getattr(context, 'project_root', cwd) or cwd),
            selection=selection,
            config_snapshot=getattr(context, 'environment_config_snapshot', None),
        )
        provider.prepare(descriptor=selection.descriptor, sandbox_policy=selection.sandbox_policy)
    except Exception as exc:
        preflight = locals().get('preflight')
        return _provider_failure_result(
            context=context,
            tool_name=tool_name,
            request=request,
            selection=selection,
            error=str(exc),
            backend_kind='local_pty' if use_pty else 'local_exec',
            preflight_decision=(preflight if isinstance(preflight, EnvironmentPreflightDecision) else None),
        )
    try:
        outcome = await _execute_request(
            context=context,
            provider=provider,
            request=request,
            selection=selection,
        )
    except FileNotFoundError as exc:
        return _provider_failure_result(
            context=context,
            tool_name=tool_name,
            request=request,
            selection=selection,
            error=str(exc),
            backend_kind='local_pty' if use_pty else 'local_exec',
            preflight_decision=(preflight if isinstance(preflight, EnvironmentPreflightDecision) else None),
            failure_stage='provider_exec',
            request_kind='exec',
        )
    except Exception as exc:
        return _provider_failure_result(
            context=context,
            tool_name=tool_name,
            request=request,
            selection=selection,
            error=str(exc),
            backend_kind='local_pty' if use_pty else 'local_exec',
            preflight_decision=(preflight if isinstance(preflight, EnvironmentPreflightDecision) else None),
            failure_stage='provider_exec',
            request_kind='exec',
        )
    receipt = outcome.receipt
    artifacts = []
    receipt = replace(
        receipt,
        command_display=command_display,
        artifact_refs=(),
        provider_metadata={
            **dict(receipt.provider_metadata),
            **_preflight_metadata(preflight if isinstance(preflight, EnvironmentPreflightDecision) else None),
        },
    )
    _record_environment_receipt(context=context, receipt=receipt)
    payload = _payload_from_receipt(
        receipt=receipt,
        session_id=request.request_id,
        backend_kind='local_pty' if use_pty else 'local_exec',
        stdout_ref=None,
        stderr_ref=None,
    )
    result = ToolResult(
        ok=receipt.status == 'completed',
        tool_name=tool_name,
        data=_payload_dict(payload),
        stdout=payload.stdout_preview or None,
        stderr=payload.stderr_preview or None,
        exit_code=payload.exit_code,
        error=_error_from_receipt(receipt=receipt, timeout_seconds=timeout_seconds),
        metadata=_command_metadata(
            context=context,
            session_id=payload.session_id,
            cwd=cwd,
            command_display=payload.command_display,
            status=payload.status,
            timeout_class=payload.timeout_class,
            backend_kind=payload.backend_kind,
            provider_kind=payload.provider_kind,
            environment_receipt=receipt,
        ),
        artifacts=artifacts,
        warnings=_warnings_from_payload(payload),
        duration=max(0.0, float(payload.elapsed_ms) / 1000.0),
    )
    result = _with_provider_recovery_hint(result, receipt=receipt)
    _finish_command_runtime_task(
        context=context,
        request=request,
        payload=payload,
        receipt=receipt,
        stdout_ref=None,
        stderr_ref=None,
    )
    return result


def _with_provider_recovery_hint(result: ToolResult, *, receipt: EnvironmentExecutionReceipt) -> ToolResult:
    hint = str(receipt.provider_metadata.get('dependency_recovery_hint', '') or '').strip()
    if not hint:
        return result
    data = dict(result.data or {})
    data['recovery_hint'] = hint
    metadata = dict(result.metadata)
    metadata['recoverable_execution_error'] = True
    metadata['recovery_hint'] = hint
    return replace(result, data=data, metadata=metadata, warnings=[*result.warnings, hint])


async def _execute_request(
    *,
    context: Any,
    provider: Any,
    request: EnvironmentExecRequest,
    selection: ResolvedEnvironmentSelection,
) -> EnvironmentExecOutcome:
    manager = getattr(context, 'process_owner_manager', None)
    if not isinstance(manager, ProcessOwnerManager):
        raise RuntimeError('bounded command execution requires ProcessOwnerManager')
    launch = provider.prepare_session_launch(request=request)
    capture = await execute_bounded_process(
        manager=manager,
        owner_id=_owner_id_for_context(context),
        argv=list(launch.argv),
        cwd=launch.cwd,
        env=launch.env,
        tty=launch.tty,
        pipe_stdin=launch.pipe_stdin,
        timeout_seconds=request.timeout_seconds,
    )
    stdout_preview, stdout_truncated = _truncate_output(
        capture.stdout_text,
        limit=DEFAULT_ENVIRONMENT_COMMAND_OUTPUT_CHARS,
    )
    stderr_preview, stderr_truncated = _truncate_output(
        capture.stderr_text,
        limit=DEFAULT_ENVIRONMENT_COMMAND_OUTPUT_CHARS,
    )
    receipt = EnvironmentExecutionReceipt(
        receipt_id=request.request_id,
        provider_kind=selection.provider_kind,
        request_kind='exec',
        tool_name=request.tool_name,
        status=capture.status,
        descriptor=selection.descriptor,
        sandbox_policy=selection.sandbox_policy,
        selection_reason=selection.selection_reason,
        environment_affinity=selection.environment_affinity,
        execution_class=request.execution_class,
        session_id=request.request_id,
        command_display=_request_command_display(request),
        started_at=capture.started_at,
        heartbeat_at=capture.heartbeat_at,
        completed_at=capture.completed_at,
        elapsed_ms=capture.elapsed_ms,
        exit_code=capture.exit_code,
        heartbeat_count=capture.heartbeat_count,
        stdout_preview=stdout_preview,
        stderr_preview=stderr_preview,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
        timeout_class=capture.timeout_class,
        pty_mode=request.pty_mode,
        pty_columns=request.pty_columns,
        pty_rows=request.pty_rows,
        network_mode='full' if selection.sandbox_policy.allow_network else 'none',
        writable_roots=selection.sandbox_policy.writable_host_roots,
        provider_metadata={
            **dict(
                provider.execution_metadata(
                    request=request,
                    stderr_text=capture.stderr_text,
                )
            ),
            "process_replay_output_truncated": capture.replay_output_truncated,
            "process_omitted_output_through_seq": capture.omitted_output_through_seq,
        },
    )
    return EnvironmentExecOutcome(
        receipt=receipt,
        stdout_text=capture.stdout_text,
        stderr_text=capture.stderr_text,
        combined_output=capture.stdout_text if request.pty_mode == 'pty' else '',
    )


def _owner_id_for_context(context: Any) -> str:
    run_id = str(getattr(context, 'run_id', '') or 'anonymous').strip().replace(' ', '-')
    return f'run-{run_id}'


def _truncate_output(text: str, *, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[: max(0, limit - 24)] + '\n...[truncated]...', True


def _execution_contract_metadata_from_context(context: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in ('execution_contract_id', 'execution_contract_hash', 'exec_policy_decision_id', 'exec_policy_hash'):
        value = str(getattr(context, key, '') or '').strip()
        if value:
            payload[key] = value
    effective_permissions = getattr(context, 'effective_permissions', None)
    if effective_permissions is not None and hasattr(effective_permissions, 'to_dict'):
        payload['effective_permissions'] = effective_permissions.to_dict()
    return payload


def _provider_failure_result(
    *,
    context: Any,
    tool_name: str,
    request: EnvironmentExecRequest,
    selection: ResolvedEnvironmentSelection,
    error: str,
    backend_kind: str,
    preflight_decision: EnvironmentPreflightDecision | None = None,
    failure_stage: str = 'provider_prepare',
    request_kind: str = 'prepare',
) -> ToolResult:
    now = _now_iso()
    normalized_failure_stage = str(failure_stage or 'provider_prepare')
    normalized_request_kind = str(request_kind or 'prepare')
    status = 'exec_failed' if normalized_failure_stage == 'provider_exec' else 'prepare_failed'
    provider_metadata = {
        **build_sandbox_provider_metadata(
            descriptor=selection.descriptor,
            sandbox_policy=selection.sandbox_policy,
            provider_kind=selection.provider_kind,
            blocked_policy_reasons=(classify_environment_failure(error),),
        ),
        'backend_kind': backend_kind,
        'failure_stage': normalized_failure_stage,
        'violation_class': (
            preflight_decision.violation_class
            if preflight_decision is not None and preflight_decision.violation_class
            else classify_environment_failure(error)
        ),
        **_preflight_metadata(preflight_decision),
    }
    receipt = EnvironmentExecutionReceipt(
        receipt_id=f'env-receipt-{uuid.uuid4().hex[:12]}',
        provider_kind=selection.provider_kind,
        request_kind=normalized_request_kind,
        tool_name=tool_name,
        status=status,
        descriptor=selection.descriptor,
        sandbox_policy=selection.sandbox_policy,
        selection_reason=selection.selection_reason,
        environment_affinity=selection.environment_affinity,
        execution_class=request.execution_class,
        session_id=request.request_id,
        command_display=_request_command_display(request),
        started_at=now,
        heartbeat_at=now,
        completed_at=now,
        elapsed_ms=0,
        exit_code=None,
        heartbeat_count=0,
        stdout_preview='',
        stderr_preview=error,
        stdout_truncated=False,
        stderr_truncated=False,
        timeout_class=None,
        pty_mode=request.pty_mode,
        pty_columns=request.pty_columns,
        pty_rows=request.pty_rows,
        network_mode='full' if selection.sandbox_policy.allow_network else 'none',
        writable_roots=selection.sandbox_policy.writable_host_roots,
        provider_metadata=provider_metadata,
    )
    _record_environment_receipt(context=context, receipt=receipt)
    payload = _payload_from_receipt(
        receipt=receipt,
        session_id=request.request_id,
        backend_kind=backend_kind,
        stdout_ref=None,
        stderr_ref=None,
    )
    _finish_command_runtime_task(
        context=context,
        request=request,
        payload=payload,
        receipt=receipt,
        stdout_ref=None,
        stderr_ref=None,
    )
    metadata = _command_metadata(
        context=context,
        session_id=payload.session_id,
        cwd=Path(payload.cwd),
        command_display=payload.command_display,
        status='failed',
        timeout_class=None,
        backend_kind=payload.backend_kind,
        provider_kind=payload.provider_kind,
        environment_receipt=receipt,
    )
    return ToolResult(
        ok=False,
        tool_name=tool_name,
        error=error,
        data=_payload_dict(payload),
        metadata=metadata,
        duration=0.0,
    )


def _request_command_display(request: EnvironmentExecRequest) -> str:
    override = str(request.metadata.get('command_display_override', '') or '').strip()
    return override or _display_command(list(request.command) or None, request.shell_command)


def _run_id_from_context(context: Any) -> str:
    direct = str(getattr(context, 'run_id', '') or '').strip()
    if direct:
        return direct
    state_store = getattr(context, 'state_store', None)
    state = getattr(state_store, 'state', None)
    run_metadata = getattr(state, 'run_metadata', None)
    return str(getattr(run_metadata, 'run_id', '') or '').strip()


def _record_environment_receipt(*, context: Any, receipt: EnvironmentExecutionReceipt) -> None:
    state_store = getattr(context, 'state_store', None)
    recorder = getattr(state_store, 'record_environment_receipt', None)
    if not callable(recorder):
        return
    stage = getattr(getattr(context, 'stage', None), 'value', getattr(context, 'stage', ''))
    recorder(
        receipt,
        stage=str(stage or ''),
        attempt=int(getattr(context, 'attempt', 0) or 0),
        query_chain_id=str(getattr(context, 'query_chain_id', '') or ''),
        continuation_index=int(getattr(context, 'continuation_index', 0) or 0),
    )


def _register_command_runtime_task(
    *,
    context: Any,
    request: EnvironmentExecRequest,
    tool_name: str,
    command_display: str,
    backend_kind: str,
    provider_kind: str,
    environment_selection_reason: str,
) -> None:
    state = getattr(getattr(context, 'state_store', None), 'state', None)
    if state is None:
        return
    stage = getattr(getattr(context, 'stage', None), 'value', getattr(context, 'stage', ''))
    register_runtime_task(
        state,
        task_id=runtime_task_id_for_command(request.request_id),
        task_type='command',
        status='running',
        description=command_display,
        provider_kind=provider_kind,
        run_id=request.run_id,
        stage=str(stage or ''),
        attempt=int(getattr(context, 'attempt', 0) or 0),
        session_id=request.request_id,
        metadata={
            'tool_name': tool_name,
            'backend_kind': backend_kind,
            'command_display': command_display,
            'cwd': request.cwd,
            'execution_class': request.execution_class,
            'pty_mode': request.pty_mode,
            'environment_selection_reason': environment_selection_reason,
        },
    )


def _finish_command_runtime_task(
    *,
    context: Any,
    request: EnvironmentExecRequest,
    payload: CommandExecutionResult,
    receipt: EnvironmentExecutionReceipt,
    stdout_ref: str | None,
    stderr_ref: str | None,
) -> None:
    state = getattr(getattr(context, 'state_store', None), 'state', None)
    if state is None:
        return
    runtime_task_id = runtime_task_id_for_command(request.request_id)
    status = _runtime_status_from_receipt(receipt.status)
    metadata = {
        'environment_receipt_id': receipt.receipt_id,
        'environment_status': receipt.status,
        'exit_code': payload.exit_code,
        'elapsed_ms': payload.elapsed_ms,
        'stdout_ref': stdout_ref,
        'stderr_ref': stderr_ref,
        'stdout_truncated': payload.stdout_truncated,
        'stderr_truncated': payload.stderr_truncated,
        'timeout_class': payload.timeout_class,
        'artifact_refs': list(receipt.artifact_refs),
    }
    if status == 'completed':
        complete_runtime_task(
            state,
            task_id=runtime_task_id,
            output_file=stdout_ref or stderr_ref,
            metadata=metadata,
        )
        return
    fail_runtime_task(
        state,
        task_id=runtime_task_id,
        status=status,
        output_file=stdout_ref or stderr_ref,
        metadata=metadata,
    )


def _runtime_status_from_receipt(status: str) -> str:
    normalized = str(status or '').strip()
    if normalized == 'completed':
        return 'completed'
    if normalized in {'timeout', 'timed_out'}:
        return 'timed_out'
    if normalized == 'cancelled':
        return 'cancelled'
    return 'failed'


def _preflight_metadata(preflight_decision: EnvironmentPreflightDecision | None) -> dict[str, Any]:
    if preflight_decision is None:
        return {'preflight_ok': True}
    return {
        'preflight_ok': bool(preflight_decision.ok),
        'preflight_unavailable_reason': preflight_decision.unavailable_reason,
        'preflight_user_action_hint': preflight_decision.user_action_hint,
        'preflight_violation_class': preflight_decision.violation_class,
        'preflight_advisory': bool(preflight_decision.advisory),
    }



def _payload_from_receipt(
    *,
    receipt: EnvironmentExecutionReceipt,
    session_id: str,
    backend_kind: str,
    stdout_ref: str | None,
    stderr_ref: str | None,
) -> CommandExecutionResult:
    return CommandExecutionResult(
        session_id=session_id or receipt.session_id or receipt.receipt_id,
        backend_kind=backend_kind,
        provider_kind=receipt.provider_kind,
        status=receipt.status,
        command_display=receipt.command_display,
        cwd=receipt.descriptor.workdir or receipt.descriptor.workspace_root,
        started_at=receipt.started_at or _now_iso(),
        heartbeat_at=receipt.heartbeat_at or receipt.started_at or _now_iso(),
        completed_at=receipt.completed_at or receipt.heartbeat_at or receipt.started_at or _now_iso(),
        elapsed_ms=receipt.elapsed_ms,
        exit_code=receipt.exit_code,
        heartbeat_count=max(0, int(receipt.heartbeat_count or 0)),
        stdout_preview=receipt.stdout_preview,
        stderr_preview=receipt.stderr_preview,
        stdout_truncated=receipt.stdout_truncated,
        stderr_truncated=receipt.stderr_truncated,
        timeout_class=receipt.timeout_class,
        stdout_ref=stdout_ref,
        stderr_ref=stderr_ref,
        pty_mode=receipt.pty_mode,
        pty_columns=receipt.pty_columns,
        pty_rows=receipt.pty_rows,
        environment_selection_reason=receipt.selection_reason,
        environment_receipt_id=receipt.receipt_id,
        environment_descriptor=receipt.descriptor.to_dict(),
        output_replay_truncated=bool(
            receipt.provider_metadata.get('process_replay_output_truncated', False)
        ),
        omitted_output_through_seq=max(
            0,
            int(receipt.provider_metadata.get('process_omitted_output_through_seq', 0) or 0),
        ),
    )



def _payload_dict(payload: CommandExecutionResult) -> dict[str, Any]:
    return {
        'session_id': payload.session_id,
        'backend_kind': payload.backend_kind,
        'provider_kind': payload.provider_kind,
        'status': payload.status,
        'command': payload.command_display,
        'cwd': payload.cwd,
        'started_at': payload.started_at,
        'heartbeat_at': payload.heartbeat_at,
        'completed_at': payload.completed_at,
        'elapsed_ms': payload.elapsed_ms,
        'exit_code': payload.exit_code,
        'heartbeat_count': payload.heartbeat_count,
        'stdout_preview': payload.stdout_preview,
        'stderr_preview': payload.stderr_preview,
        'stdout_truncated': payload.stdout_truncated,
        'stderr_truncated': payload.stderr_truncated,
        'timeout_class': payload.timeout_class,
        'stdout_ref': payload.stdout_ref,
        'stderr_ref': payload.stderr_ref,
        'pty_mode': payload.pty_mode,
        'pty_columns': payload.pty_columns,
        'pty_rows': payload.pty_rows,
        'environment_selection_reason': payload.environment_selection_reason,
        'environment_receipt_id': payload.environment_receipt_id,
        'environment_descriptor': payload.environment_descriptor,
        'output_replay_truncated': payload.output_replay_truncated,
        'omitted_output_through_seq': payload.omitted_output_through_seq,
        'partial_stdout_excerpt': payload.stdout_preview,
        'partial_stderr_excerpt': payload.stderr_preview,
    }



def _warnings_from_payload(payload: CommandExecutionResult) -> list[str]:
    warnings: list[str] = []
    if payload.stdout_truncated:
        warnings.append('stdout truncated for prompt budget')
    if payload.stderr_truncated:
        warnings.append('stderr truncated for prompt budget')
    if payload.output_replay_truncated:
        warnings.append(
            'process output replay was truncated; stdout/stderr artifacts are partial '
            f'after sequence {payload.omitted_output_through_seq}'
        )
    return warnings



def _error_from_receipt(*, receipt: EnvironmentExecutionReceipt, timeout_seconds: int) -> str | None:
    if receipt.status == 'completed':
        return None
    if receipt.status == 'timeout':
        return f'command timed out after {timeout_seconds}s'
    if receipt.status == 'failed' and receipt.exit_code is not None:
        return f'command exited with code {receipt.exit_code}'
    if receipt.status == 'cancelled':
        return 'command session was cancelled'
    return None



def _command_metadata(
    *,
    context: Any,
    session_id: str,
    cwd: Path,
    command_display: str,
    status: str,
    timeout_class: str | None,
    backend_kind: str,
    provider_kind: str,
    environment_receipt: EnvironmentExecutionReceipt | None,
) -> dict[str, Any]:
    payload = metadata_for_context(
        context,
        session_id=session_id,
        backend_kind=backend_kind,
        provider_kind=provider_kind,
        cwd=str(cwd),
        command=command_display,
        command_status=status,
        timeout_class=timeout_class,
    )
    if environment_receipt is not None:
        payload['environment_receipt_id'] = environment_receipt.receipt_id
        payload['environment_provider_kind'] = environment_receipt.provider_kind
        payload['environment_selection_reason'] = environment_receipt.selection_reason
    return payload



def _display_command(command: list[str] | None, shell_command: str | None) -> str:
    if shell_command is not None:
        return shell_command
    assert command is not None
    return subprocess.list2cmdline(command)



def _selection_from_context(context: Any, *, use_pty: bool) -> ResolvedEnvironmentSelection:
    descriptor = EnvironmentDescriptor.from_dict(
        getattr(context, 'environment_descriptor', None)
        if isinstance(getattr(context, 'environment_descriptor', None), dict)
        else {
            'provider_kind': getattr(context, 'environment_provider_kind', 'host_local') or 'host_local',
            'workspace_root': str(getattr(context, 'project_root', '')),
            'workdir': str(getattr(context, 'project_root', '')),
        }
    )
    effective_permissions = _effective_permissions_from_context(context)
    provider_kind = (
        getattr(context, 'environment_provider_kind', descriptor.provider_kind or 'host_local')
        or 'host_local'
    )
    policy = build_permission_enforcement_projection(
        effective_permissions=effective_permissions,
        provider_kind=provider_kind,
        requires_isolation=bool(descriptor.sandboxed),
        remote_required=(provider_kind == 'ssh_remote'),
        host_managed=descriptor.host_managed,
        reason=str(getattr(context, 'environment_selection_reason', '') or ''),
    )
    return ResolvedEnvironmentSelection(
        provider_kind=provider_kind,
        descriptor=descriptor,
        sandbox_policy=policy,
        selection_reason=str(getattr(context, 'environment_selection_reason', '') or ''),
        environment_affinity=str(getattr(context, 'environment_affinity', 'host_preferred') or 'host_preferred'),
        execution_class=str(getattr(context, 'execution_class', 'interactive_pty' if use_pty else 'command_exec') or ('interactive_pty' if use_pty else 'command_exec')),
        backend_kind='local_pty' if use_pty else 'local_exec',
    )


def _effective_permissions_from_context(context: Any) -> EffectivePermissions:
    value = getattr(context, 'effective_permissions', None)
    if isinstance(value, EffectivePermissions):
        return value
    if isinstance(value, dict):
        return EffectivePermissions.from_dict(value)
    raise RuntimeError('command execution requires canonical effective permissions')




def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
