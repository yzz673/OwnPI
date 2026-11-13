from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal


ExecutionStatus = Literal[
    'running',
    'terminal_success',
    'terminal_failure',
    'unknown',
    'blocked',
]

PROCESS_OWNER_LOST_ISSUE = 'process_owner_lost'


@dataclass(frozen=True)
class ExecutionHandle:
    backend: str
    tool_name: str
    owner_id: str = ''
    process_id: str = ''
    job_id: str = ''
    profile: str = ''
    local_workdir: str = ''
    remote_workdir: str = ''
    artifact_root: str = ''
    stdout_ref: str = ''
    stderr_ref: str = ''
    public_session_id: int = 0
    timeout_seconds: int = 0
    sandbox_backend: str = 'unknown'
    command_display: str = ''

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> 'ExecutionHandle':
        payload = data if isinstance(data, dict) else {}
        allowed = {
            'backend',
            'tool_name',
            'owner_id',
            'process_id',
            'job_id',
            'profile',
            'local_workdir',
            'remote_workdir',
            'artifact_root',
            'stdout_ref',
            'stderr_ref',
            'public_session_id',
            'timeout_seconds',
            'sandbox_backend',
            'command_display',
        }
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ValueError(f'execution handle contains unsupported fields: {unknown}')
        return cls(
            backend=str(payload.get('backend', '') or ''),
            tool_name=str(payload.get('tool_name', '') or ''),
            owner_id=str(payload.get('owner_id', '') or ''),
            process_id=str(payload.get('process_id', '') or ''),
            job_id=str(payload.get('job_id', '') or ''),
            profile=str(payload.get('profile', '') or ''),
            local_workdir=str(payload.get('local_workdir', '') or ''),
            remote_workdir=str(payload.get('remote_workdir', '') or ''),
            artifact_root=str(payload.get('artifact_root', '') or ''),
            stdout_ref=str(payload.get('stdout_ref', '') or ''),
            stderr_ref=str(payload.get('stderr_ref', '') or ''),
            public_session_id=max(0, int(payload.get('public_session_id', 0) or 0)),
            timeout_seconds=max(0, int(payload.get('timeout_seconds', 0) or 0)),
            sandbox_backend=str(payload.get('sandbox_backend') or 'unknown'),
            command_display=str(payload.get('command_display', '') or ''),
        )

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(asdict(self))


@dataclass(frozen=True)
class ExecutionOrigin:
    stage: str
    attempt: int
    continuation_index: int
    tool_name: str

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> 'ExecutionOrigin':
        payload = data if isinstance(data, dict) else {}
        return cls(
            stage=str(payload.get('stage', '') or ''),
            attempt=int(payload.get('attempt', 0) or 0),
            continuation_index=int(payload.get('continuation_index', 0) or 0),
            tool_name=str(payload.get('tool_name', '') or ''),
        )

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(asdict(self))


@dataclass(frozen=True)
class ExecutionObservation:
    execution_id: str
    status: ExecutionStatus
    handle: ExecutionHandle
    origin: ExecutionOrigin
    observed_at: str
    result_refs: tuple[str, ...] = ()
    readback_status: str = ''
    issue: str = ''
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def terminal(self) -> bool:
        return execution_status_is_terminal(self.status)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            'execution_id': self.execution_id,
            'status': self.status,
            'handle': self.handle.to_dict(),
            'origin': self.origin.to_dict(),
            'observed_at': self.observed_at,
            'result_refs': list(self.result_refs),
            'readback_status': self.readback_status,
            'issue': self.issue,
            'evidence': dict(self.evidence),
        }
        return _compact_dict(payload)


@dataclass(frozen=True)
class ExecutionRecord:
    execution_id: str
    handle: ExecutionHandle
    origin: ExecutionOrigin
    started_at: str
    updated_at: str
    status: ExecutionStatus
    launch_order: int
    summary: str = ''
    result_refs: tuple[str, ...] = ()
    readback_status: str = ''
    issue: str = ''

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> 'ExecutionRecord':
        payload = data if isinstance(data, dict) else {}
        handle = ExecutionHandle.from_dict(
            payload.get('handle') if isinstance(payload.get('handle'), dict) else None
        )
        origin = ExecutionOrigin.from_dict(
            payload.get('origin') if isinstance(payload.get('origin'), dict) else None
        )
        return cls(
            execution_id=str(payload.get('execution_id', '') or ''),
            handle=handle,
            origin=origin,
            started_at=str(payload.get('started_at', '') or ''),
            updated_at=str(payload.get('updated_at', '') or ''),
            status=normalize_execution_status(payload.get('status')),
            launch_order=max(0, int(payload.get('launch_order', 0) or 0)),
            summary=str(payload.get('summary', '') or ''),
            result_refs=_string_tuple(payload.get('result_refs')),
            readback_status=str(payload.get('readback_status', '') or ''),
            issue=str(payload.get('issue', '') or ''),
        )

    @property
    def terminal(self) -> bool:
        return execution_status_is_terminal(self.status)

    @property
    def is_active(self) -> bool:
        return self.status in {'running', 'unknown'} and not self.terminal

    def to_dict(self) -> dict[str, Any]:
        payload = {
            'execution_id': self.execution_id,
            'handle': self.handle.to_dict(),
            'origin': self.origin.to_dict(),
            'started_at': self.started_at,
            'updated_at': self.updated_at,
            'status': self.status,
            'launch_order': self.launch_order,
            'summary': self.summary,
            'result_refs': list(self.result_refs),
            'readback_status': self.readback_status,
            'issue': self.issue,
        }
        return _compact_dict(payload)


def record_from_observation(
    observation: ExecutionObservation,
    *,
    launch_order: int,
) -> ExecutionRecord:
    return ExecutionRecord(
        execution_id=observation.execution_id,
        handle=observation.handle,
        origin=observation.origin,
        started_at=observation.observed_at,
        updated_at=observation.observed_at,
        status=observation.status,
        launch_order=launch_order,
        summary=_execution_summary(observation.origin, observation.handle),
        result_refs=observation.result_refs,
        readback_status=observation.readback_status,
        issue=observation.issue,
    )


def merge_execution_record(
    previous: ExecutionRecord,
    observation: ExecutionObservation,
) -> tuple[ExecutionRecord, bool]:
    if observation.observed_at and previous.updated_at and observation.observed_at < previous.updated_at:
        return previous, False
    if previous.terminal and not observation.terminal:
        return previous, False
    status = previous.status if previous.terminal else observation.status
    handle = _merge_handle(previous.handle, observation.handle)
    result_refs = tuple(dict.fromkeys((*previous.result_refs, *observation.result_refs)))
    readback_status = observation.readback_status or previous.readback_status
    issue = observation.issue
    material = (
        status != previous.status
        or handle != previous.handle
        or result_refs != previous.result_refs
        or readback_status != previous.readback_status
        or issue != previous.issue
    )
    if not material:
        return previous, False
    return (
        replace(
            previous,
            handle=handle,
            updated_at=observation.observed_at or previous.updated_at,
            status=status,
            result_refs=result_refs,
            readback_status=readback_status,
            issue=issue,
        ),
        True,
    )


def _execution_summary(origin: ExecutionOrigin, handle: ExecutionHandle) -> str:
    command = handle.command_display.strip() or handle.tool_name.strip() or 'managed execution'
    stage = origin.stage.strip() or 'unknown stage'
    return f'Execution from {stage} (attempt {origin.attempt}): {command}'[:240]


def execution_status_is_terminal(status: ExecutionStatus) -> bool:
    return status in {'terminal_success', 'terminal_failure', 'blocked'}


def normalize_execution_status(value: Any) -> ExecutionStatus:
    normalized = str(value or '').strip().lower()
    if normalized in {'running', 'terminal_success', 'terminal_failure', 'unknown', 'blocked'}:
        return normalized  # type: ignore[return-value]
    if normalized in {'completed', 'success', 'succeeded'}:
        return 'terminal_success'
    if normalized in {'failed', 'error', 'stalled', 'contract_violation', 'terminated'}:
        return 'terminal_failure'
    return 'unknown'


def handles_refer_to_same_execution(left: ExecutionHandle, right: ExecutionHandle) -> bool:
    if left.backend and right.backend and left.backend != right.backend:
        return False
    return bool(
        left.owner_id
        and left.process_id
        and right.owner_id
        and right.process_id
        and (left.owner_id, left.process_id) == (right.owner_id, right.process_id)
    )


def _merge_handle(previous: ExecutionHandle, latest: ExecutionHandle) -> ExecutionHandle:
    values = {
        name: getattr(latest, name) or getattr(previous, name)
        for name in ExecutionHandle.__dataclass_fields__
    }
    if previous.sandbox_backend != 'unknown':
        values['sandbox_backend'] = previous.sandbox_backend
    return ExecutionHandle(**values)


def _string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _compact_dict(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if value not in ('', None, {}, [], ())
    }
