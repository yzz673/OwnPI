from __future__ import annotations

from dataclasses import dataclass, field
from queue import Queue
import subprocess
import threading

from ownpi.runtime.process_owner.os_control import spawn_owned_process
from ownpi.runtime.process_owner.protocol import ProcessOwnerProtocolError


SpawnResult = tuple[subprocess.Popen[bytes], int | None, int | None]


@dataclass
class _SpawnRequest:
    argv: list[str]
    cwd: str
    env: dict[str, str]
    tty_mode: bool
    pipe_stdin: bool
    pty_columns: int | None
    pty_rows: int | None
    completed: threading.Event = field(default_factory=threading.Event)
    result: SpawnResult | None = None
    error: Exception | None = None


class ProcessSpawnWorker:
    """Spawns session processes from one stable parent thread."""

    def __init__(self) -> None:
        self._queue: Queue[_SpawnRequest | None] = Queue()
        self._lock = threading.Lock()
        self._closed = False
        self._thread = threading.Thread(
            target=self._run,
            name="ownpi-process-spawn",
            daemon=True,
        )
        self._thread.start()

    def spawn(
        self,
        argv: list[str],
        *,
        cwd: str,
        env: dict[str, str],
        tty_mode: bool,
        pipe_stdin: bool,
        pty_columns: int | None = None,
        pty_rows: int | None = None,
    ) -> SpawnResult:
        request = _SpawnRequest(
            argv=list(argv),
            cwd=cwd,
            env=dict(env),
            tty_mode=tty_mode,
            pipe_stdin=pipe_stdin,
            pty_columns=pty_columns,
            pty_rows=pty_rows,
        )
        with self._lock:
            if self._closed:
                raise ProcessOwnerProtocolError(
                    "process spawn worker is closed", start_outcome='not_created',
                )
            self._queue.put(request)
        request.completed.wait()
        if request.error is not None:
            raise request.error
        if request.result is None:
            raise RuntimeError("process spawn worker returned no result")
        return request.result

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._queue.put(None)
        self._thread.join(timeout=5.0)
        if self._thread.is_alive():
            raise RuntimeError("process spawn worker did not stop")

    def _run(self) -> None:
        while True:
            request = self._queue.get()
            if request is None:
                return
            try:
                request.result = spawn_owned_process(
                    request.argv,
                    cwd=request.cwd,
                    env=request.env,
                    tty_mode=request.tty_mode,
                    pipe_stdin=request.pipe_stdin,
                    pty_columns=request.pty_columns,
                    pty_rows=request.pty_rows,
                )
            except Exception as exc:
                request.error = exc
            finally:
                request.completed.set()
