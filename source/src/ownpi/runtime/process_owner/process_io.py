from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Any, Callable

from ownpi.runtime.process_owner.os_control import terminate_process_tree
from ownpi.runtime.sandbox.denial import DenialOutputEvidence


EventSink = Callable[[str, dict[str, Any]], None]


@dataclass
class ProcessEntry:
    process_id: str
    process: subprocess.Popen[bytes]
    stdin_handle: Any | None
    process_group_id: int | None = None
    master_fd: int | None = None
    chunks: deque[tuple[int, str, str]] = field(default_factory=deque)
    retained_bytes: int = 0
    next_seq: int = 1
    omitted_output_through_seq: int = 0
    exit_code: int | None = None
    closed: bool = False
    final_seq: int = 0
    closed_at: float | None = None
    open_streams: int = 0
    write_ids: deque[str] = field(default_factory=deque)
    write_id_set: set[str] = field(default_factory=set)
    termination_started: bool = False
    timeout_seconds: int = 0
    timed_out: bool = False
    stdout_path: str = ""
    stderr_path: str = ""
    log_error: str = ""
    sandbox_backend: str = "unknown"
    denial_evidence: DenialOutputEvidence = field(default_factory=DenialOutputEvidence)
    condition: threading.Condition = field(
        default_factory=threading.Condition, repr=False
    )


class ProcessIOManager:
    def __init__(
        self,
        *,
        event_sink: EventSink,
        max_chunks: int,
        max_bytes: int,
    ) -> None:
        self._event_sink = event_sink
        self._max_chunks = max_chunks
        self._max_bytes = max_bytes

    def start_watchers(self, entry: ProcessEntry, *, tty_mode: bool) -> None:
        if tty_mode:
            threading.Thread(
                target=self._read_fd,
                args=(entry, entry.master_fd, "pty"),
                daemon=True,
            ).start()
        else:
            threading.Thread(
                target=self._read_stream,
                args=(entry, entry.process.stdout, "stdout"),
                daemon=True,
            ).start()
            threading.Thread(
                target=self._read_stream,
                args=(entry, entry.process.stderr, "stderr"),
                daemon=True,
            ).start()
        threading.Thread(target=self._watch_exit, args=(entry,), daemon=True).start()
        if entry.timeout_seconds > 0:
            threading.Thread(
                target=self._watch_timeout,
                args=(entry,),
                daemon=True,
            ).start()

    def _read_stream(self, entry: ProcessEntry, stream: Any, stream_name: str) -> None:
        try:
            if stream is None:
                return
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    return
                self._append_output(entry, stream_name, chunk)
        finally:
            self._mark_stream_closed(entry)

    def _read_fd(self, entry: ProcessEntry, fd: int | None, stream_name: str) -> None:
        try:
            if fd is None:
                return
            while True:
                try:
                    chunk = os.read(fd, 4096)
                except OSError:
                    return
                if not chunk:
                    return
                self._append_output(entry, stream_name, chunk)
        finally:
            self._mark_stream_closed(entry)

    def _append_output(self, entry: ProcessEntry, stream: str, payload: bytes) -> None:
        self._persist_output(entry, stream=stream, payload=payload)
        text = payload.decode("utf-8", errors="replace")
        with entry.condition:
            entry.denial_evidence.observe(stream, text)
            chunk = (entry.next_seq, stream, text)
            entry.next_seq += 1
            entry.chunks.append(chunk)
            entry.retained_bytes += len(payload)
            while (
                len(entry.chunks) > self._max_chunks
                or entry.retained_bytes > self._max_bytes
            ):
                removed = entry.chunks.popleft()
                entry.omitted_output_through_seq = removed[0]
                entry.retained_bytes -= len(
                    removed[2].encode("utf-8", errors="replace")
                )
            entry.condition.notify_all()
        self._event_sink(
            "process/output_delta",
            {
                "process_id": entry.process_id,
                "seq": chunk[0],
                "stream": chunk[1],
                "chunk": chunk[2],
            },
        )

    @staticmethod
    def _persist_output(entry: ProcessEntry, *, stream: str, payload: bytes) -> None:
        path_text = entry.stderr_path if stream == "stderr" else entry.stdout_path
        if not path_text or not payload:
            return
        try:
            with Path(path_text).open("ab") as stream_file:
                stream_file.write(payload)
        except OSError as exc:
            with entry.condition:
                if not entry.log_error:
                    entry.log_error = str(exc)

    @staticmethod
    def _watch_timeout(entry: ProcessEntry) -> None:
        deadline = time.monotonic() + entry.timeout_seconds
        with entry.condition:
            while entry.exit_code is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    entry.timed_out = True
                    break
                entry.condition.wait(timeout=remaining)
            if entry.exit_code is not None:
                return
        terminate_process_tree(
            entry.process,
            process_group_id=entry.process_group_id,
        )

    def _watch_exit(self, entry: ProcessEntry) -> None:
        exit_code = entry.process.wait()
        with entry.condition:
            entry.exit_code = exit_code
            event_seq = entry.next_seq
            entry.next_seq += 1
            entry.condition.notify_all()
        self._event_sink(
            "process/exited",
            {"process_id": entry.process_id, "seq": event_seq, "exit_code": exit_code},
        )
        self._maybe_mark_closed(entry)

    def _mark_stream_closed(self, entry: ProcessEntry) -> None:
        with entry.condition:
            entry.open_streams = max(0, entry.open_streams - 1)
            entry.condition.notify_all()
        self._maybe_mark_closed(entry)

    def _maybe_mark_closed(self, entry: ProcessEntry) -> None:
        with entry.condition:
            if entry.closed or entry.exit_code is None or entry.open_streams > 0:
                return
            entry.closed = True
            entry.final_seq = entry.next_seq
            entry.next_seq += 1
            entry.closed_at = time.monotonic()
            entry.condition.notify_all()
            final_seq = entry.final_seq
        self._event_sink(
            "process/closed",
            {"process_id": entry.process_id, "seq": final_seq, "final_seq": final_seq},
        )
