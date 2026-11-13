from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
import time

from ownpi.runtime.process_owner.client import complete_on_cancel
from ownpi.runtime.process_owner.manager import ProcessOwnerManager
from ownpi.runtime.process_owner.protocol import ProcessOwnerProtocolError


@dataclass(frozen=True)
class BoundedExecutionCapture:
    status: str
    exit_code: int | None
    started_at: str
    heartbeat_at: str
    completed_at: str
    elapsed_ms: int
    heartbeat_count: int
    stdout_text: str
    stderr_text: str
    timeout_class: str | None = None
    replay_output_truncated: bool = False
    omitted_output_through_seq: int = 0


class _BoundedTextCapture:
    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max(1, int(max_bytes))
        self._parts: deque[str] = deque()
        self._retained_bytes = 0

    def append(self, text: str) -> None:
        if not text:
            return
        encoded = text.encode("utf-8", errors="replace")
        if len(encoded) >= self._max_bytes:
            text = encoded[-self._max_bytes :].decode("utf-8", errors="replace")
            self._parts.clear()
            self._retained_bytes = 0
            encoded = text.encode("utf-8", errors="replace")
        self._parts.append(text)
        self._retained_bytes += len(encoded)
        while self._retained_bytes > self._max_bytes and self._parts:
            removed = self._parts.popleft()
            self._retained_bytes -= len(removed.encode("utf-8", errors="replace"))

    def text(self) -> str:
        return "".join(self._parts)


async def execute_bounded_process(
    *,
    manager: ProcessOwnerManager,
    owner_id: str,
    argv: list[str],
    cwd: str,
    env: dict[str, str],
    tty: bool,
    pipe_stdin: bool,
    timeout_seconds: int,
    cancel_checker: Callable[[], bool] | None = None,
    max_capture_bytes: int = 4 * 1024 * 1024,
) -> BoundedExecutionCapture:
    started_at = _now_iso()
    started_monotonic = time.monotonic()
    process_id = f'process-{uuid.uuid4().hex}'
    await manager.client_for_async(owner_id)
    if cancel_checker is not None and cancel_checker():
        raise asyncio.CancelledError('Private process cancelled before start')
    async def acquire() -> None:
        await manager.start_process_async(
            owner_id=owner_id, process_id=process_id, argv=argv, cwd=cwd,
            env=env, tty=tty, pipe_stdin=pipe_stdin,
        )
    try:
        await complete_on_cancel(acquire())
    except BaseException as error:
        try:
            await complete_on_cancel(_terminate_and_release(manager, owner_id, process_id))
        except Exception as cleanup_error:
            error.add_note(f'Private process acquisition cleanup failed: {cleanup_error}')
        raise
    cursor = 0
    stdout_capture = _BoundedTextCapture(max_capture_bytes)
    stderr_capture = _BoundedTextCapture(max_capture_bytes)
    heartbeat_count = 0
    timed_out = False
    response: dict[str, object] = {}
    replay_output_truncated = False
    omitted_output_through_seq = 0
    deadline = started_monotonic + max(1, int(timeout_seconds))

    try:
        while True:
            if cancel_checker is not None and cancel_checker():
                raise asyncio.CancelledError('Private process wait cancelled')
            remaining = deadline - time.monotonic()
            if remaining <= 0 and not timed_out:
                await manager.terminate_process_async(owner_id=owner_id, process_id=process_id)
                timed_out = True
            wait_ms = 1000 if timed_out else min(1000, max(0, int(remaining * 1000)))
            response = await manager.read_process_async(
                owner_id=owner_id,
                process_id=process_id,
                after_seq=cursor,
                wait_ms=wait_ms,
            )
            replay_output_truncated = replay_output_truncated or bool(
                response.get("output_truncated", False)
            )
            omitted_output_through_seq = max(
                omitted_output_through_seq,
                _protocol_int(
                    response.get("omitted_output_through_seq", 0),
                    field_name="omitted_output_through_seq",
                ),
            )
            replay_output_truncated = replay_output_truncated or (
                omitted_output_through_seq > 0
            )
            chunks = response.get("chunks", [])
            if isinstance(chunks, list):
                for item in chunks:
                    if not isinstance(item, dict):
                        continue
                    cursor = max(
                        cursor,
                        _protocol_int(item.get("seq", 0), field_name="chunk.seq"),
                    )
                    chunk = str(item.get("chunk", "") or "")
                    stream = str(item.get("stream", "") or "")
                    if stream == "stderr":
                        stderr_capture.append(chunk)
                    else:
                        stdout_capture.append(chunk)
                    heartbeat_count += 1
            if bool(response.get("closed", False)) and not chunks:
                break

        final_seq = _protocol_int(
            response.get("final_seq", 0),
            field_name="final_seq",
        )
        if final_seq > 0:
            try:
                await manager.release_process_async(
                    owner_id=owner_id,
                    process_id=process_id,
                    final_seq=final_seq,
                )
            except Exception:
                pass
    except BaseException as error:
        try:
            await complete_on_cancel(_terminate_and_release(manager, owner_id, process_id))
        except Exception as cleanup_error:
            error.add_note(f'Private process cleanup failed: {cleanup_error}')
        raise
    completed_at = _now_iso()
    exit_code = response.get("exit_code")
    normalized_exit_code = (
        _protocol_int(exit_code, field_name="exit_code")
        if exit_code is not None
        else None
    )
    return BoundedExecutionCapture(
        status=(
            "timeout"
            if timed_out
            else "completed"
            if normalized_exit_code == 0
            else "failed"
        ),
        exit_code=None if timed_out else normalized_exit_code,
        started_at=started_at,
        heartbeat_at=completed_at,
        completed_at=completed_at,
        elapsed_ms=max(0, int((time.monotonic() - started_monotonic) * 1000)),
        heartbeat_count=max(1, heartbeat_count),
        stdout_text=stdout_capture.text(),
        stderr_text=stderr_capture.text(),
        timeout_class="wall_clock" if timed_out else None,
        replay_output_truncated=replay_output_truncated,
        omitted_output_through_seq=omitted_output_through_seq,
    )


async def _terminate_and_release(manager: ProcessOwnerManager, owner_id: str, process_id: str) -> None:
    await manager.terminate_process_async(owner_id=owner_id, process_id=process_id)
    cursor = 0
    while True:
        response = await manager.read_process_async(
            owner_id=owner_id, process_id=process_id, after_seq=cursor, wait_ms=100,
        )
        for chunk in response.get('chunks', []):
            cursor = max(cursor, int(chunk.get('seq', 0)))
        if response.get('closed') and not response.get('chunks'):
            await manager.release_process_async(
                owner_id=owner_id, process_id=process_id,
                final_seq=int(response.get('final_seq', 0)),
            )
            return


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _protocol_int(value: object, *, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ProcessOwnerProtocolError(
            f"process owner response {field_name} must be an integer"
        )
    return value
