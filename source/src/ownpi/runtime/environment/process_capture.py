from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Any

from ownpi.runtime.environment.subprocess_env import build_subprocess_env


_PRIVATE_PIPE_DRAIN_TIMEOUT_SECONDS = 2.0


async def capture_private_process(
    command: list[str] | tuple[str, ...], *, cwd: str | Path,
    env: dict[str, str], input_text: str | None, timeout_seconds: float | None,
) -> subprocess.CompletedProcess[str]:
    """Own a short-lived subprocess through cancellation, termination and reap."""
    spawn = asyncio.create_task(asyncio.create_subprocess_exec(
        *command, cwd=cwd, env=env,
        stdin=asyncio.subprocess.PIPE if input_text is not None else None,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        start_new_session=os.name != 'nt',
    ))
    process: asyncio.subprocess.Process | None = None
    communication: asyncio.Task[tuple[bytes, bytes]] | None = None
    try:
        # Acquisition must deliver its handle even if cancellation wins spawn.
        process = await asyncio.shield(spawn)
        communication = asyncio.create_task(process.communicate(
            input_text.encode('utf-8') if input_text is not None else None,
        ))
        try:
            stdout, stderr = await asyncio.wait_for(
                asyncio.shield(communication), timeout=timeout_seconds,
            )
        except TimeoutError:
            stdout, stderr = await _finish_private_process(process, communication)
            raise subprocess.TimeoutExpired(command, timeout_seconds, stdout, stderr) from None
        return subprocess.CompletedProcess(
            command, process.returncode, stdout.decode('utf-8', errors='replace'),
            stderr.decode('utf-8', errors='replace'),
        )
    except BaseException:
        async def cleanup() -> None:
            nonlocal process, communication
            if process is None:
                process = await spawn
            if communication is None:
                communication = asyncio.create_task(process.communicate())
            await _finish_private_process(process, communication)

        cleanup_task = asyncio.create_task(cleanup())
        while not cleanup_task.done():
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        # Retrieve cleanup errors without replacing the original exception.
        if not cleanup_task.cancelled() and cleanup_task.exception() is not None:
            import logging

            logging.getLogger(__name__).warning(
                'Private process cleanup failed: %s', cleanup_task.exception(),
            )
        raise


async def _terminate_private_process(process: asyncio.subprocess.Process) -> None:
    def signal_group(number: int) -> None:
        try:
            if os.name != 'nt':
                os.killpg(process.pid, number)
            elif process.returncode is None:
                process.terminate() if number == signal.SIGTERM else process.kill()
        except ProcessLookupError:
            pass

    signal_group(signal.SIGTERM)
    try:
        await asyncio.wait_for(process.wait(), timeout=1)
    except TimeoutError:
        pass
    # The leader may exit on TERM while descendants still own output pipes.
    signal_group(signal.SIGKILL)


async def _finish_private_process(
    process: asyncio.subprocess.Process,
    communication: asyncio.Task[tuple[bytes, bytes]],
) -> tuple[bytes, bytes]:
    await _terminate_private_process(process)
    try:
        try:
            return await asyncio.wait_for(
                asyncio.shield(communication), timeout=_PRIVATE_PIPE_DRAIN_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            # Escaped sessions can keep pipes open after the owned group exits.
            # Closing transports feeds EOF without discarding already-read output.
            _close_private_process_pipes(process)
            return await asyncio.shield(communication)
    finally:
        _close_private_process_pipes(process)
        # A wait begun before exit can depend on pipe closure, not just SIGKILL.
        await process.wait()


def _close_private_process_pipes(process: asyncio.subprocess.Process) -> None:
    # Process exposes no public read-pipe close API. Close the actual transports,
    # not only communicate's outer task, so its reader tasks can finish and join.
    for fd in (0, 1, 2):
        pipe = process._transport.get_pipe_transport(fd)
        if pipe is not None:
            if fd == 0:
                if not pipe.is_closing() or pipe.get_write_buffer_size():
                    pipe.abort()  # close() could wait forever to flush blocked stdin.
            else:
                pipe.close()


@dataclass
class PipeCaptureResult:
    status: str
    exit_code: int | None
    started_at: str
    heartbeat_at: str
    completed_at: str
    elapsed_ms: int
    heartbeat_count: int
    stdout_text: str
    stderr_text: str
    stdout_preview: str
    stderr_preview: str
    stdout_truncated: bool
    stderr_truncated: bool
    timeout_class: str | None = None


def execute_subprocess_capture(
    *,
    command: list[str],
    cwd: str,
    env: dict[str, str] | None,
    timeout_seconds: int,
    max_output_chars: int,
    input_text: str | None = None,
    monitored_path: str | None = None,
    max_monitored_bytes: int | None = None,
) -> PipeCaptureResult:
    started_at = now_utc_iso()
    started_monotonic = time.monotonic()
    process: subprocess.Popen[str] | None = None
    try:
        process = popen_with_process_group(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE if input_text is not None else None,
            text=True,
            env=(
                build_subprocess_env(base_env=env)
                if env is not None
                else build_subprocess_env()
            ),
        )
        limit_exceeded = False
        if monitored_path and max_monitored_bytes is not None:
            stdout_text, stderr_text, limit_exceeded = _communicate_with_file_limit(
                process,
                input_text=input_text,
                timeout_seconds=timeout_seconds,
                monitored_path=Path(monitored_path),
                max_bytes=max(1, int(max_monitored_bytes)),
            )
        else:
            stdout_text, stderr_text = process.communicate(
                input=input_text,
                timeout=timeout_seconds,
            )
    except FileNotFoundError:
        raise
    except subprocess.TimeoutExpired as exc:
        if process is not None:
            kill_process(process)
            try:
                stdout_after, stderr_after = process.communicate(timeout=1)
            except Exception:
                stdout_after, stderr_after = '', ''
        else:
            stdout_after, stderr_after = '', ''
        completed_at = now_utc_iso()
        stdout_text = _coerce_process_output(stdout_after) or _coerce_process_output(
            exc.stdout
        )
        stderr_text = _coerce_process_output(stderr_after) or _coerce_process_output(
            exc.stderr
        )
        stdout_preview, stdout_truncated = truncate_output(
            stdout_text,
            limit=max_output_chars,
        )
        stderr_preview, stderr_truncated = truncate_output(
            stderr_text,
            limit=max_output_chars,
        )
        return PipeCaptureResult(
            status='timeout',
            exit_code=None,
            started_at=started_at,
            heartbeat_at=completed_at,
            completed_at=completed_at,
            elapsed_ms=max(
                0,
                int((time.monotonic() - started_monotonic) * 1000),
            ),
            heartbeat_count=1,
            stdout_text=stdout_text,
            stderr_text=stderr_text,
            stdout_preview=stdout_preview,
            stderr_preview=stderr_preview,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            timeout_class='wall_clock',
        )
    completed_at = now_utc_iso()
    returncode = process.returncode if process is not None else None
    stdout_text = stdout_text or ''
    stderr_text = stderr_text or ''
    if limit_exceeded:
        stderr_text = (
            f'{stderr_text}\nresource transfer exceeded the configured byte limit'
        ).strip()
    stdout_preview, stdout_truncated = truncate_output(
        stdout_text,
        limit=max_output_chars,
    )
    stderr_preview, stderr_truncated = truncate_output(
        stderr_text,
        limit=max_output_chars,
    )
    return PipeCaptureResult(
        status='completed' if returncode == 0 and not limit_exceeded else 'failed',
        exit_code=returncode,
        started_at=started_at,
        heartbeat_at=completed_at,
        completed_at=completed_at,
        elapsed_ms=max(0, int((time.monotonic() - started_monotonic) * 1000)),
        heartbeat_count=1,
        stdout_text=stdout_text,
        stderr_text=stderr_text,
        stdout_preview=stdout_preview,
        stderr_preview=stderr_preview,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
    )


def _communicate_with_file_limit(
    process: subprocess.Popen[str],
    *,
    input_text: str | None,
    timeout_seconds: int,
    monitored_path: Path,
    max_bytes: int,
) -> tuple[str, str, bool]:
    deadline = time.monotonic() + timeout_seconds
    pending_input = input_text
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(process.args, timeout_seconds)
        try:
            stdout_text, stderr_text = process.communicate(
                input=pending_input,
                timeout=min(0.02, remaining),
            )
            return stdout_text or '', stderr_text or '', False
        except subprocess.TimeoutExpired:
            pending_input = None
            try:
                current_size = monitored_path.stat().st_size
            except OSError:
                current_size = 0
            if current_size <= max_bytes:
                continue
            kill_process(process)
            stdout_text, stderr_text = process.communicate()
            return stdout_text or '', stderr_text or '', True


def kill_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    if os.name != 'nt':
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError:
            process.terminate()
    else:
        process.terminate()
    try:
        process.wait(timeout=1)
        return
    except subprocess.TimeoutExpired:
        pass
    if os.name != 'nt':
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except ProcessLookupError:
            return
        except OSError:
            process.kill()
    else:
        process.kill()
    process.wait(timeout=1)


def popen_with_process_group(*args: Any, **kwargs: Any) -> subprocess.Popen[Any]:
    if os.name != 'nt':
        kwargs.setdefault('start_new_session', True)
    return subprocess.Popen(*args, **kwargs)


def truncate_output(text: str, *, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[: max(0, limit - 24)] + '\n...[truncated]...', True


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def shell_path() -> str:
    for candidate in (os.environ.get('SHELL'), '/bin/bash', '/bin/zsh'):
        if candidate and Path(candidate).exists():
            return str(candidate)
    return '/bin/sh'


def normalize_python_command(command: str) -> str:
    if _which('python') is not None:
        return command
    if command.startswith('python '):
        python3 = _which('python3')
        if python3:
            return command.replace('python ', f'{python3} ', 1)
    return command


def _coerce_process_output(value: object) -> str:
    if value is None:
        return ''
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    return str(value)


def _which(name: str) -> str | None:
    for directory in os.environ.get('PATH', '').split(os.pathsep):
        candidate = Path(directory) / name
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None
