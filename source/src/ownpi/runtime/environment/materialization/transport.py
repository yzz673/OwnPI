from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any, Callable

from ownpi.runtime.environment.command_backend import run_local_command
from ownpi.runtime.environment.materialization.file_io import MaterializationFileIO
from ownpi.runtime.permissions.models import EffectivePermissions
from ownpi.runtime.environment.materialization.contracts import (
    MaterializationFailure,
    TransportExecutor,
    TransportRequest,
    TransportResult,
)


@dataclass(frozen=True)
class TransportPolicy:
    overall_deadline_seconds: int = 600
    attempt_timeout_seconds: int = 120
    max_attempts: int = 3


class BoundedTransport:
    def __init__(
        self,
        *,
        executor: TransportExecutor,
        policy: TransportPolicy,
        effective_permissions: EffectivePermissions | None = None,
        clock: Callable[[], float] = time.monotonic,
        cancel_checker: Callable[[], bool] | None = None,
    ) -> None:
        self._executor = executor
        self._io = MaterializationFileIO(effective_permissions)
        self._policy = policy
        self._clock = clock
        self._cancel_checker = cancel_checker
        self._started_at: float | None = None

    async def execute(
        self,
        argv: list[str],
        *,
        cwd: Path,
        partial_path: Path | None = None,
        max_partial_bytes: int | None = None,
        env: dict[str, str] | None = None,
    ) -> TransportResult:
        if not argv or any('\x00' in item for item in argv):
            raise MaterializationFailure(
                'transport argv is invalid',
                owner='runtime_capability_missing',
                code='invalid_transport_argv',
            )
        if self._started_at is None:
            self._started_at = self._clock()
        started = self._started_at
        last = TransportResult(ok=False, stderr='transport was not attempted')
        for _attempt in range(self._policy.max_attempts):
            if partial_path is not None:
                self._io.write(partial_path)
                self._io.read(partial_path)
            if self._cancel_checker is not None and self._cancel_checker():
                if partial_path is not None:
                    self._io.unlink(partial_path)
                raise MaterializationFailure(
                    'resource materialization was cancelled',
                    owner='runtime_capability_missing',
                    code='materialization_cancelled',
                )
            remaining = self._policy.overall_deadline_seconds - (self._clock() - started)
            if remaining <= 0:
                break
            timeout = max(1, min(self._policy.attempt_timeout_seconds, int(remaining)))
            last = await self._executor(
                TransportRequest(
                    argv=tuple(argv),
                    cwd=cwd,
                    timeout_seconds=timeout,
                    partial_path=partial_path,
                    max_partial_bytes=max_partial_bytes,
                    env=dict(env or {}),
                )
            )
            if partial_path is not None:
                self._io.read(partial_path)
            if (
                partial_path is not None
                and max_partial_bytes is not None
                and partial_path.is_file()
                and partial_path.stat().st_size > max_partial_bytes
            ):
                self._io.unlink(partial_path)
                raise MaterializationFailure(
                    'resource transfer exceeded the configured byte limit',
                    owner='runtime_capability_missing',
                    code='transfer_byte_limit_exceeded',
                )
            if last.ok:
                return last
        detail = f'{last.stderr}\n{last.stdout}'.casefold()
        if any(marker in detail for marker in ('no such file', 'command not found', 'executable not found')):
            owner = 'runtime_capability_missing'
            code = 'transport_executable_missing'
        elif any(marker in detail for marker in ('policy denied', 'network denied', 'permission class denied')):
            owner = 'policy_owned'
            code = 'transport_canonical_permission_denied'
        else:
            owner = 'external_route'
            code = 'transport_deadline' if self._clock() - started >= self._policy.overall_deadline_seconds else 'transport_failed'
        message = (last.stderr or last.stdout or code)[:1000]
        raise MaterializationFailure(message, owner=owner, code=code)


def command_executor_for_context(context: Any, *, tool_name: str) -> TransportExecutor:
    async def execute(request: TransportRequest) -> TransportResult:
        result = await run_local_command(
            context=context,
            tool_name=tool_name,
            command=list(request.argv),
            cwd=request.cwd,
            timeout_seconds=request.timeout_seconds,
            env=request.env,
            max_output_chars=4000,
            metadata={
                'resource_materialization': True,
                'monitored_partial_path': str(request.partial_path) if request.partial_path is not None else '',
                'max_monitored_bytes': request.max_partial_bytes,
            },
            command_display_override=f'{tool_name} transport <redacted>',
        )
        data = result.data if isinstance(result.data, dict) else {}
        return TransportResult(
            ok=result.ok,
            exit_code=result.exit_code,
            stdout=str(result.stdout or ''),
            stderr=str(result.stderr or result.error or ''),
            timed_out=str(data.get('status', '')).casefold() == 'timeout',
        )

    return execute
