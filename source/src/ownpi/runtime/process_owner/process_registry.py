from __future__ import annotations

import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Any

from ownpi.runtime.process_owner.os_control import (
    close_fd,
    close_process_resources,
    signal_process_tree,
    terminate_process_tree,
)
from ownpi.runtime.process_owner.process_io import (
    EventSink,
    ProcessEntry,
    ProcessIOManager,
)
from ownpi.runtime.process_owner.protocol import ProcessOwnerProtocolError
from ownpi.runtime.process_owner.spawn_worker import ProcessSpawnWorker
from ownpi.runtime.limits.resources import (
    PROCESS_RELEASE_DEDUP_MAX_ENTRIES,
    PROCESS_RELEASE_DEDUP_TTL_SECONDS,
    PROCESS_REPLAY_MAX_BYTES,
    PROCESS_REPLAY_MAX_CHUNKS,
    PROCESS_WRITE_DEDUP_MAX_ENTRIES,
)


class ProcessRegistry:
    def __init__(
        self,
        *,
        event_sink: EventSink,
        max_chunks: int = PROCESS_REPLAY_MAX_CHUNKS,
        max_bytes: int = PROCESS_REPLAY_MAX_BYTES,
        max_write_ids: int = PROCESS_WRITE_DEDUP_MAX_ENTRIES,
    ) -> None:
        self._max_chunks = max(1, int(max_chunks))
        self._max_bytes = max(1024, int(max_bytes))
        self._max_write_ids = max(1, int(max_write_ids))
        self._lock = threading.RLock()
        self._state_condition = threading.Condition(self._lock)
        self._drain_lock = threading.Lock()
        self._entries: dict[str, ProcessEntry | None] = {}
        self._released: dict[str, tuple[int, float]] = {}
        self._process_io = ProcessIOManager(
            event_sink=event_sink,
            max_chunks=self._max_chunks,
            max_bytes=self._max_bytes,
        )
        self._spawn_worker = ProcessSpawnWorker()
        self._accepting_starts = True
        self._drain_result: dict[str, Any] | None = None

    def start(
        self,
        *,
        process_id: str,
        argv: list[str],
        cwd: str,
        env: dict[str, str],
        tty_mode: bool,
        pipe_stdin: bool,
        pty_columns: int | None = None,
        pty_rows: int | None = None,
        timeout_seconds: int = 0,
        stdout_path: str = "",
        stderr_path: str = "",
        sandbox_backend: str = "unknown",
    ) -> dict[str, Any]:
        with self._lock:
            self._cleanup_closed_locked()
            if not self._accepting_starts:
                raise ProcessOwnerProtocolError(
                    "session is draining and rejects new process starts",
                    start_outcome='not_created',
                )
            if process_id in self._entries or process_id in self._released:
                raise ProcessOwnerProtocolError(f"duplicate process_id: {process_id}")
            self._entries[process_id] = None
        process: subprocess.Popen[bytes] | None = None
        master_fd: int | None = None
        process_group_id: int | None = None
        try:
            try:
                _prepare_log_path(stdout_path)
                _prepare_log_path(stderr_path)
            except Exception as exc:
                raise ProcessOwnerProtocolError('process owner request failed', start_outcome='not_created') from exc
            process, master_fd, process_group_id = self._spawn_worker.spawn(
                argv,
                cwd=cwd,
                env={**os.environ, **env},
                tty_mode=tty_mode,
                pipe_stdin=pipe_stdin,
                pty_columns=pty_columns,
                pty_rows=pty_rows,
            )
            entry = ProcessEntry(
                process_id=process_id,
                process=process,
                stdin_handle=(process.stdin if not tty_mode else None),
                process_group_id=process_group_id,
                master_fd=master_fd,
                open_streams=1 if tty_mode else 2,
                timeout_seconds=max(0, int(timeout_seconds)),
                stdout_path=str(stdout_path or ""),
                stderr_path=str(stderr_path or ""),
                sandbox_backend=sandbox_backend,
            )
            with self._lock:
                if (
                    process_id not in self._entries
                    or self._entries[process_id] is not None
                    or not self._accepting_starts
                ):
                    terminate_process_tree(
                        process,
                        process_group_id=process_group_id,
                    )
                    raise ProcessOwnerProtocolError(
                        f"process reservation was cancelled: {process_id}"
                    )
                self._entries[process_id] = entry
            self._process_io.start_watchers(entry, tty_mode=tty_mode)
            return {"process_id": process_id}
        except Exception:
            with self._lock:
                if self._entries.get(process_id) is None:
                    self._entries.pop(process_id, None)
            if process is not None and process.poll() is None:
                terminate_process_tree(
                    process,
                    process_group_id=process_group_id,
                )
            if master_fd is not None:
                close_fd(master_fd)
            raise
        finally:
            with self._lock:
                self._state_condition.notify_all()

    def read(
        self,
        *,
        process_id: str,
        after_seq: int,
        max_bytes: int,
        wait_ms: int,
    ) -> dict[str, Any]:
        entry = self._require_running_entry(process_id)
        deadline = time.monotonic() + max(0, wait_ms) / 1000.0
        with entry.condition:
            while not entry.closed and not any(
                chunk[0] > after_seq for chunk in entry.chunks
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                entry.condition.wait(timeout=remaining)
            chunks: list[dict[str, Any]] = []
            retained = 0
            for chunk in entry.chunks:
                if chunk[0] <= after_seq:
                    continue
                encoded_size = len(chunk[2].encode("utf-8", errors="replace"))
                if chunks and retained + encoded_size > max_bytes:
                    break
                chunks.append({"seq": chunk[0], "stream": chunk[1], "chunk": chunk[2]})
                retained += encoded_size
            output_truncated = after_seq < entry.omitted_output_through_seq
            result = {
                "process_id": process_id,
                "process_group_id": entry.process_group_id,
                "chunks": chunks,
                "exit_code": entry.exit_code,
                "closed": entry.closed,
                "final_seq": entry.final_seq if entry.closed else None,
                "output_truncated": output_truncated,
                "omitted_output_through_seq": entry.omitted_output_through_seq,
                "timed_out": entry.timed_out,
                "interrupted": entry.termination_started,
                "sandbox_backend": entry.sandbox_backend,
                "denial_evidence": entry.denial_evidence.to_dict(),
                "stdout_ref": entry.stdout_path,
                "stderr_ref": entry.stderr_path,
                "log_error": entry.log_error,
            }
            if output_truncated:
                result["recovery_guidance"] = (
                    "Inspect the execution's durable stdout/stderr result refs; process replay "
                    "before omitted_output_through_seq is no longer available."
                )
            return result

    def write(self, *, process_id: str, chunk: str, write_id: str) -> dict[str, Any]:
        entry = self._require_running_entry(process_id)
        payload = chunk.encode("utf-8")
        with entry.condition:
            if write_id in entry.write_id_set:
                return {
                    "process_id": process_id,
                    "write_id": write_id,
                    "duplicate": True,
                }
            if entry.process.poll() is not None:
                raise ProcessOwnerProtocolError(
                    f"process is not writable: {process_id}"
                )
            if entry.master_fd is not None:
                os.write(entry.master_fd, payload)
            elif entry.stdin_handle is not None:
                entry.stdin_handle.write(payload)
                entry.stdin_handle.flush()
            else:
                raise ProcessOwnerProtocolError(
                    f"process stdin is not available: {process_id}"
                )
            entry.write_ids.append(write_id)
            entry.write_id_set.add(write_id)
            while len(entry.write_ids) > self._max_write_ids:
                entry.write_id_set.discard(entry.write_ids.popleft())
            return {"process_id": process_id, "write_id": write_id, "duplicate": False}

    def signal(self, *, process_id: str, signal_number: int) -> dict[str, Any]:
        entry = self._require_running_entry(process_id)
        signal_process_tree(
            entry.process,
            signal_number,
            process_group_id=entry.process_group_id,
        )
        return {"process_id": process_id, "signal": signal_number}

    def terminate(
        self, *, process_id: str, grace_seconds: float = 2.0
    ) -> dict[str, Any]:
        entry = self._require_running_entry(process_id)
        self._terminate_once(entry, grace_seconds=grace_seconds)
        return {"process_id": process_id, "terminated": True}

    def release(self, *, process_id: str, final_seq: int) -> dict[str, Any]:
        with self._lock:
            entry = self._entries.get(process_id)
            if entry is None:
                released = self._released.get(process_id)
                if released is not None:
                    if released[0] == final_seq:
                        return {
                            "process_id": process_id,
                            "released": True,
                            "final_seq": final_seq,
                            "duplicate": True,
                        }
                raise ProcessOwnerProtocolError(f"unknown process_id: {process_id}")
            with entry.condition:
                if not entry.closed or entry.final_seq != final_seq:
                    raise ProcessOwnerProtocolError(
                        "release requires the matching closed final_seq"
                    )
            self._entries.pop(process_id, None)
            self._released[process_id] = (final_seq, time.monotonic())
            self._cleanup_released_locked()
        close_process_resources(entry.process, entry.master_fd)
        entry.master_fd = None
        return {
            "process_id": process_id,
            "released": True,
            "final_seq": final_seq,
            "duplicate": False,
        }

    def maintenance(self) -> None:
        with self._lock:
            self._cleanup_closed_locked()

    def drain_and_shutdown(self, *, grace_seconds: float = 2.0) -> dict[str, Any]:
        with self._drain_lock:
            with self._lock:
                if self._drain_result is not None:
                    return dict(self._drain_result)
                self._accepting_starts = False
                while any(entry is None for entry in self._entries.values()):
                    self._state_condition.wait(timeout=0.05)
                entries = [
                    entry for entry in self._entries.values() if entry is not None
                ]
            for entry in entries:
                self._terminate_once(entry, grace_seconds=grace_seconds)
            deadline = time.monotonic() + max(0.1, grace_seconds + 1.0)
            for entry in entries:
                with entry.condition:
                    while not entry.closed and time.monotonic() < deadline:
                        entry.condition.wait(timeout=0.05)
            result = {
                "final_seq": max((entry.final_seq for entry in entries), default=0),
                "process_count": len(entries),
            }
            with self._lock:
                self._drain_result = result
            return dict(result)

    def shutdown(self) -> None:
        self.drain_and_shutdown()
        with self._lock:
            entries = [entry for entry in self._entries.values() if entry is not None]
            self._entries.clear()
        for entry in entries:
            close_process_resources(entry.process, entry.master_fd)
            entry.master_fd = None
        self._spawn_worker.close()

    def has_processes(self) -> bool:
        with self._lock:
            return bool(self._entries)

    def _require_running_entry(self, process_id: str) -> ProcessEntry:
        with self._lock:
            self._cleanup_closed_locked()
            entry = self._entries.get(process_id)
        if entry is None:
            raise ProcessOwnerProtocolError(f"unknown process_id: {process_id}")
        return entry

    def _terminate_once(self, entry: ProcessEntry, *, grace_seconds: float) -> None:
        with entry.condition:
            if entry.termination_started:
                return
            entry.termination_started = True
        try:
            terminate_process_tree(
                entry.process,
                process_group_id=entry.process_group_id,
                grace_seconds=grace_seconds,
            )
        except Exception:
            with entry.condition:
                entry.termination_started = False
            raise

    def _cleanup_released_locked(self) -> None:
        cutoff = time.monotonic() - PROCESS_RELEASE_DEDUP_TTL_SECONDS
        expired = [key for key, record in self._released.items() if record[1] <= cutoff]
        for process_id in expired:
            self._released.pop(process_id, None)
        while len(self._released) > PROCESS_RELEASE_DEDUP_MAX_ENTRIES:
            self._released.pop(next(iter(self._released)))

    def _cleanup_closed_locked(self) -> None:
        self._cleanup_released_locked()


def _prepare_log_path(value: str) -> None:
    if not str(value or "").strip():
        return
    path = Path(value).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
