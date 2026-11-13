from __future__ import annotations

from typing import Any

from ownpi.runtime.events import now_utc_iso
from ownpi.runtime.execution.state import (
    PROCESS_OWNER_LOST_ISSUE,
    ExecutionHandle,
    ExecutionObservation,
    ExecutionOrigin,
)
from ownpi.runtime.native_tools.messages import NativeToolMessage


LOCAL_PROCESS_OBSERVATION_TOOLS = frozenset(
    {
        'exec_command',
        'write_stdin',
        'execution_monitor',
        'execution_kill',
    }
)


class LocalProcessExecutionAdapter:
    backend_name = 'local_process'

    def observation_from_native_message(
        self,
        message: NativeToolMessage,
        *,
        tool_name: str,
        data: dict[str, Any],
        metadata: dict[str, Any],
    ) -> ExecutionObservation | None:
        if tool_name not in LOCAL_PROCESS_OBSERVATION_TOOLS:
            return None
        return _local_process_observation(
            message=message,
            tool_name=tool_name,
            data=data,
            metadata=metadata,
        )

local_process_adapter = LocalProcessExecutionAdapter()


def _local_process_observation(
    *,
    message: NativeToolMessage,
    tool_name: str,
    data: dict[str, Any],
    metadata: dict[str, Any],
) -> ExecutionObservation | None:
    raw_handle = data.get('job_handle')
    handle_payload: dict[str, Any] = raw_handle if isinstance(raw_handle, dict) else {}
    raw_execution_handle = data.get('execution_handle')
    if not handle_payload and isinstance(raw_execution_handle, dict):
        handle_payload = raw_execution_handle
    if not handle_payload:
        return None
    handle = _handle_from_payload(tool_name=tool_name, data=data, handle_payload=handle_payload)
    if tool_name in {'exec_command', 'write_stdin'}:
        terminal_status = str(
            metadata.get('terminal_status', data.get('terminal_status', '')) or ''
        ).strip().lower()
        exit_code = data.get('exit_code')
        terminal = exit_code is not None or terminal_status in {'completed', 'failed', 'timeout', 'interrupted'}
        timed_out = bool(metadata.get('timed_out', False)) or terminal_status == 'timeout'
        failed = (
            timed_out or terminal_status == 'interrupted'
            or metadata.get('failure_class') == 'sandbox_denied'
        )
        status = (
            'terminal_failure'
            if terminal and (failed or int(exit_code or 0) != 0)
            else 'terminal_success'
            if terminal
            else 'running'
        )
        if tool_name == 'exec_command' and terminal:
            return None
        raw_log_refs = data.get('log_refs')
        log_refs: dict[str, Any] = (
            raw_log_refs if isinstance(raw_log_refs, dict) else {}
        )
        result_refs = _string_tuple(
            [
                handle.stdout_ref,
                handle.stderr_ref,
                log_refs.get('stdout'),
                log_refs.get('stderr'),
            ]
        )
        return _observation(
            message=message,
            tool_name=tool_name,
            status=status,
            execution_id=_local_execution_id(
                owner_id=handle.owner_id,
                process_id=handle.process_id,
                artifact_root=handle.artifact_root,
                job_id=handle.job_id,
            ),
            handle=handle,
            summary=_first_text(
                message.text,
                f'exec command {"finished" if terminal else "is running"}: {handle.process_id}',
            ),
            readback_refs=result_refs if terminal else (),
            recommended_next_actions=(
                ()
                if terminal
                else ('wait_or_poll_execution', 'inspect_execution_logs')
            ),
            metadata={
                'source': tool_name,
                'timed_out': timed_out,
                **_compact_metadata(metadata),
            },
        )
    raw_monitor = data.get('monitor')
    monitor: dict[str, Any] = raw_monitor if isinstance(raw_monitor, dict) else {}
    job_state = str(monitor.get('job_state', data.get('status', '')) or '').strip().lower()
    status = _status_from_local_job_state(job_state)
    terminal = status not in {'running', 'unknown'}
    raw_log_refs = monitor.get('log_refs')
    log_refs = raw_log_refs if isinstance(raw_log_refs, dict) else {}
    artifact_refs = _string_tuple(
        [
            *list(_string_tuple(monitor.get('light_artifacts'))),
            *list(_string_tuple(monitor.get('result_refs'))),
            log_refs.get('stdout'),
            log_refs.get('stderr'),
        ]
    )
    return _observation(
        message=message,
        tool_name=str(handle_payload.get('tool_name', tool_name) or tool_name),
        status=status,
        execution_id=_local_execution_id(
            owner_id=handle.owner_id,
            process_id=handle.process_id,
            artifact_root=handle.artifact_root,
            job_id=handle.job_id,
        ),
        handle=handle,
        summary=_first_text(message.text, f'local process monitor job_state={job_state or "unknown"}'),
        reconciliation_status='monitoring_terminal' if not terminal else 'terminal_reconciled',
        last_monitor_at=message.created_at or now_utc_iso(),
        terminal_monitor_ref=_first_text(str(message.request_id or ''), str(message.provider_call_id or '')),
        readback_refs=artifact_refs if terminal else (),
        artifact_refs=artifact_refs,
        recommended_next_actions=_string_tuple([monitor.get('recommended_next_action')]),
        metadata={
            'source': tool_name,
            'job_state': job_state,
            'failure_class': str(monitor.get('failure_class', '') or ''),
            'artifacts_ready': bool(monitor.get('artifacts_ready', False)),
            **_compact_metadata(metadata),
        },
    )


def _handle_from_payload(*, tool_name: str, data: dict[str, Any], handle_payload: dict[str, Any]) -> ExecutionHandle:
    raw_layout = handle_payload.get('artifact_layout')
    layout: dict[str, Any] = raw_layout if isinstance(raw_layout, dict) else {}
    if not layout:
        raw_layout = data.get('artifact_layout')
        layout = raw_layout if isinstance(raw_layout, dict) else {}
    artifact_root = str(handle_payload.get('artifact_root', layout.get('artifact_root', '')) or '')
    job_id = str(handle_payload.get('job_id', '') or '')
    return ExecutionHandle(
        backend=str(handle_payload.get('backend', 'local_process') or 'local_process'),
        tool_name=str(handle_payload.get('tool_name', tool_name) or tool_name),
        owner_id=str(handle_payload.get('owner_id', '') or ''),
        process_id=str(handle_payload.get('process_id', '') or ''),
        job_id=job_id,
        local_workdir=str(handle_payload.get('local_workdir') or handle_payload.get('cwd') or ''),
        command_display=str(handle_payload.get('command_display') or ''),
        artifact_root=artifact_root,
        stdout_ref=str(handle_payload.get('stdout_ref', '') or ''),
        stderr_ref=str(handle_payload.get('stderr_ref', '') or ''),
        public_session_id=max(
            0,
            int(handle_payload.get('public_session_id', 0) or 0),
        ),
        timeout_seconds=max(0, int(handle_payload.get('timeout_seconds', 0) or 0)),
        sandbox_backend=str(handle_payload.get('sandbox_backend') or 'unknown'),
    )


def _observation(
    *,
    message: NativeToolMessage,
    tool_name: str,
    execution_id: str,
    status: str,
    handle: ExecutionHandle,
    summary: str,
    reconciliation_status: str = '',
    last_monitor_at: str = '',
    terminal_monitor_ref: str = '',
    readback_refs: tuple[str, ...] = (),
    artifact_refs: tuple[str, ...] = (),
    recommended_next_actions: tuple[str, ...] = (),
    metadata: dict[str, Any] | None = None,
) -> ExecutionObservation:
    del reconciliation_status, last_monitor_at
    refs = tuple(dict.fromkeys((*readback_refs, *artifact_refs)))
    return ExecutionObservation(
        execution_id=execution_id,
        status=status,  # type: ignore[arg-type]
        handle=handle,
        origin=ExecutionOrigin(
            stage=message.stage,
            attempt=message.attempt,
            continuation_index=message.continuation_index,
            tool_name=tool_name,
        ),
        observed_at=message.created_at or now_utc_iso(),
        result_refs=refs,
        readback_status=('complete' if readback_refs else ('pending' if status.startswith('terminal_') else '')),
        issue=_issue_from_metadata(metadata or {}),
        evidence={
            'summary': summary,
            'terminal_monitor_ref': terminal_monitor_ref,
            'recommended_next_actions': list(recommended_next_actions),
            **dict(metadata or {}),
        },
    )


def _status_from_local_job_state(job_state: str) -> str:
    if job_state == 'running':
        return 'running'
    if job_state == 'completed':
        return 'terminal_success'
    if job_state in {
        'failed',
        'contract_violation',
        'reconciliation_failure',
        'terminated',
        'unsafe_to_kill',
    }:
        return 'terminal_failure'
    if job_state == 'already_stopped':
        return 'terminal_failure'
    return 'unknown'


def _local_execution_id(
    *,
    owner_id: str,
    process_id: str,
    artifact_root: str,
    job_id: str,
) -> str:
    if owner_id and process_id:
        return f'local:{owner_id}:{process_id}'
    del artifact_root, job_id
    return 'local_process:unknown'


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        str(item).strip()
        for item in value
        if item is not None and str(item).strip()
    )


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or '').strip()
        if text:
            return text
    return ''


def _compact_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in metadata.items()
        if key in {
            'backend', 'environment_receipt_id', 'execution_contract_id',
            'execution_contract_hash', 'sandbox_backend', 'failure_class',
            'failure_kind', 'denial_evidence', 'recommended_next_action',
        }
    }


def _issue_from_metadata(metadata: dict[str, Any]) -> str:
    if metadata.get('failure_class') == 'sandbox_denied':
        return 'sandbox_denied'
    if str(metadata.get('failure_class', '') or '') == PROCESS_OWNER_LOST_ISSUE:
        return PROCESS_OWNER_LOST_ISSUE
    return str(metadata.get('recovery_hint', '') or '')
