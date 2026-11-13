from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from ownpi.runtime.events import now_utc_iso
from ownpi.runtime.execution.state import (
    PROCESS_OWNER_LOST_ISSUE,
    ExecutionHandle,
    ExecutionObservation,
    ExecutionOrigin,
)
from ownpi.runtime.native_tools.messages import NativeToolMessage


REMOTE_WORKFLOW_OBSERVATION_TOOLS = frozenset({'remote_run', 'remote_monitor', 'remote_pull'})


class RemoteWorkflowExecutionAdapter:
    backend_name = 'remote_workflow'

    def observation_from_native_message(
        self,
        message: NativeToolMessage,
        *,
        tool_name: str,
        data: dict[str, Any],
        metadata: dict[str, Any],
    ) -> ExecutionObservation | None:
        if tool_name not in REMOTE_WORKFLOW_OBSERVATION_TOOLS:
            return None
        return _remote_workflow_observation(
            message=message,
            tool_name=tool_name,
            data=data,
            metadata=metadata,
        )

remote_workflow_adapter = RemoteWorkflowExecutionAdapter()


def _remote_workflow_observation(
    *,
    message: NativeToolMessage,
    tool_name: str,
    data: dict[str, Any],
    metadata: dict[str, Any],
) -> ExecutionObservation | None:
    if tool_name == 'remote_run':
        handle_payload = data.get('job_handle') if isinstance(data.get('job_handle'), dict) else {}
        if not handle_payload:
            return None
        handle = _handle_from_remote_payload(
            tool_name=tool_name,
            data=data,
            handle_payload=handle_payload,
        )
        return _observation(
            message=message,
            tool_name=tool_name,
            status='running',
            execution_id=_execution_id_for_handle(handle),
            handle=handle,
            summary=_first_text(
                message.text,
                data.get('summary'),
                f'remote execution started: {handle.process_id or handle.job_id}',
            ),
            recommended_next_actions=('wait_or_poll_execution', 'inspect_or_reconcile_outputs'),
            metadata={'source': 'remote_run', **_compact_metadata(metadata)},
        )

    if tool_name == 'remote_monitor':
        monitor = data.get('monitor') if isinstance(data.get('monitor'), dict) else {}
        if not monitor:
            return None
        job_state = str(monitor.get('job_state', '') or '').strip().lower()
        status = _status_from_remote_job_state(job_state)
        handle = _handle_from_remote_payload(
            tool_name=tool_name,
            data=data,
            handle_payload={},
        )
        recommended = _string_tuple([monitor.get('recommended_next_action')])
        monitor_ref = _first_text(
            str(message.request_id or ''),
            str(message.provider_call_id or ''),
            str(message.tool_name or ''),
        )
        return _observation(
            message=message,
            tool_name=tool_name,
            status=status,
            execution_id=_execution_id_for_handle(handle),
            handle=handle,
            summary=_first_text(
                message.text,
                f"remote monitor job_state={job_state or 'unknown'}",
            ),
            reconciliation_status='monitoring_terminal' if status in {'running', 'unknown'} else 'terminal_reconciled',
            last_monitor_at=message.created_at or now_utc_iso(),
            terminal_monitor_ref=monitor_ref,
            artifact_refs=_string_tuple(monitor.get('light_artifacts')),
            recommended_next_actions=recommended,
            metadata={
                'source': 'remote_monitor',
                'job_state': job_state,
                'failure_class': str(data.get('failure_class', '') or ''),
                'artifacts_ready': bool(monitor.get('artifacts_ready', False)),
                'monitor_ref': monitor_ref,
                **_compact_metadata(metadata),
            },
        )

    if tool_name == 'remote_pull':
        local_destinations = data.get('local_destinations')
        if not local_destinations:
            return None
        handle = _handle_from_remote_payload(
            tool_name=tool_name,
            data=data,
            handle_payload={},
        )
        readback_refs = _artifact_refs_from_local_destinations(local_destinations)
        return _observation(
            message=message,
            tool_name=tool_name,
            status='terminal_success',
            execution_id=_execution_id_for_handle(handle),
            handle=handle,
            summary=_first_text(message.text, 'remote artifacts pulled for reconciliation'),
            reconciliation_status='terminal_reconciled',
            terminal_pull_ref=_first_text(str(message.request_id or ''), str(message.provider_call_id or '')),
            readback_refs=readback_refs,
            artifact_refs=readback_refs,
            recommended_next_actions=('synthesize_from_reconciled_artifacts',),
            metadata={'source': 'remote_pull', **_compact_metadata(metadata)},
        )
    return None


def _handle_from_remote_payload(
    *,
    tool_name: str,
    data: dict[str, Any],
    handle_payload: dict[str, Any],
) -> ExecutionHandle:
    layout = handle_payload.get('artifact_layout') if isinstance(handle_payload.get('artifact_layout'), dict) else {}
    if not layout:
        layout = data.get('artifact_layout') if isinstance(data.get('artifact_layout'), dict) else {}
    artifact_root = str(layout.get('artifact_root', '') or '')
    if not artifact_root and tool_name == 'remote_pull':
        artifact_root = _artifact_root_from_remote_pull_data(data)
    job_id = str(handle_payload.get('job_id', '') or '')
    return ExecutionHandle(
        backend=str(handle_payload.get('backend', 'remote_workflow') or 'remote_workflow'),
        tool_name=tool_name,
        owner_id=str(handle_payload.get('owner_id', data.get('owner_id', '')) or ''),
        process_id=str(handle_payload.get('process_id', data.get('process_id', '')) or ''),
        job_id=job_id,
        profile=str(data.get('profile', '') or ''),
        remote_workdir=str(handle_payload.get('remote_workdir', handle_payload.get('workdir', data.get('remote_root', ''))) or ''),
        artifact_root=artifact_root,
        command_display=str(data.get('command_display', '') or ''),
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
    next_wakeup_at: str = '',
    last_monitor_at: str = '',
    terminal_monitor_ref: str = '',
    terminal_pull_ref: str = '',
    readback_refs: tuple[str, ...] = (),
    latest_output_ref: str = '',
    artifact_refs: tuple[str, ...] = (),
    recommended_next_actions: tuple[str, ...] = (),
    metadata: dict[str, Any] | None = None,
) -> ExecutionObservation:
    del reconciliation_status, next_wakeup_at, last_monitor_at, latest_output_ref
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
            'terminal_pull_ref': terminal_pull_ref,
            'recommended_next_actions': list(recommended_next_actions),
            **dict(metadata or {}),
        },
    )


def _execution_id_for_handle(handle: ExecutionHandle) -> str:
    if handle.owner_id and handle.process_id:
        return f'remote_workflow:{handle.owner_id}:{handle.process_id}'
    return 'remote_workflow:unknown'


def _issue_from_metadata(metadata: dict[str, Any]) -> str:
    if str(metadata.get('failure_class', '') or '') == PROCESS_OWNER_LOST_ISSUE:
        return PROCESS_OWNER_LOST_ISSUE
    return str(metadata.get('recovery_hint', '') or '')


def _status_from_remote_job_state(job_state: str) -> str:
    if job_state == 'running':
        return 'running'
    if job_state == 'completed':
        return 'terminal_success'
    if job_state in {'failed', 'stalled', 'contract_violation'}:
        return 'terminal_failure'
    return 'unknown'


def _artifact_root_from_remote_pull_data(data: dict[str, Any]) -> str:
    paths = _string_tuple(data.get('paths'))
    for raw_path in paths:
        artifact_root = _artifact_root_from_remote_artifact_path(raw_path)
        if artifact_root:
            return artifact_root
    return ''


def _artifact_root_from_remote_artifact_path(raw_path: str) -> str:
    text = str(raw_path or '').strip().rstrip('/')
    if not text:
        return ''
    path = PurePosixPath(text)
    if path.name in {'light', 'logs'}:
        return str(path.parent)
    if path.parent.name in {'light', 'logs'}:
        return str(path.parent.parent)
    return str(path)


def _artifact_refs_from_local_destinations(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    refs: list[str] = []
    for item in value:
        path = ''
        if isinstance(item, str) and item.strip():
            path = item.strip()
        elif isinstance(item, dict):
            path = str(item.get('local_path', item.get('path', '')) or '').strip()
        if path:
            refs.extend(_expand_local_artifact_ref(path))
    return tuple(dict.fromkeys(refs))


def _expand_local_artifact_ref(path: str) -> tuple[str, ...]:
    refs = [path]
    local_path = Path(path).expanduser()
    if not local_path.is_dir():
        return tuple(refs)
    for child in sorted(local_path.rglob('*')):
        if child.is_file():
            refs.append(str(child))
    return tuple(refs)


def _string_tuple(value: Any) -> tuple[str, ...]:
    candidates = value if isinstance(value, (list, tuple)) else (value,)
    return tuple(str(item).strip() for item in candidates if str(item or '').strip())


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or '').strip()
        if text:
            return text
    return ''


def _compact_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    allowed = {}
    for key in ('recommended_next_action', 'failure_class', 'recovery_hint'):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            allowed[key] = value.strip()
    return allowed
