"""Single-writer append queue; flush acknowledgement is not an fsync guarantee."""

from __future__ import annotations

from collections import deque
from concurrent.futures import Future, TimeoutError
from dataclasses import dataclass
import fcntl
import json
import logging
import os
from pathlib import Path
from queue import Full, Queue
from threading import Lock, Thread
from typing import BinaryIO, Callable, Iterable

from ownpi.runtime.rollout.codec import RolloutRecord, should_persist


def open_append(path: Path) -> BinaryIO:
    handle = path.open('a+b')
    try:
        handle.seek(0, 2)
        if handle.tell():
            handle.seek(-1, 2)
            if handle.read(1) != b'\n':
                handle.write(b'\n')
                handle.flush()
        return handle
    except BaseException:
        handle.close()
        raise


class WriterState:
    def __init__(self, path: Path, meta: RolloutRecord | None,
                 opener: Callable[[Path], BinaryIO] = open_append) -> None:
        self.path, self.meta, self.opener = path, meta, opener
        self.pending: deque[bytes] = deque()
        self.handle: BinaryIO | None = None
        self.materialized = meta is None

    def add(self, records: tuple[bytes, ...]) -> None:
        self.pending.extend(records)

    def write(self, *, force: bool = False) -> None:
        if not force and not self.materialized and not self.pending:
            return
        for attempt in range(2):
            try:
                self._write_once()
                return
            except OSError:
                self.close()
                if attempt:
                    raise

    def _write_once(self) -> None:
        if self.handle is None:
            self.handle = self.opener(self.path)
            self.materialized = True
        if self.meta is not None:
            self._line(_encode(self.meta))
            self.meta = None
        while self.pending:
            self._line(self.pending[0])
            self.pending.popleft()
        self.handle.flush()

    def _line(self, data: bytes) -> None:
        assert self.handle is not None
        view = memoryview(data)
        while view:
            written = self.handle.write(view)
            if written is None or written <= 0:
                raise OSError('rollout writer made no progress')
            view = view[written:]
        self.handle.flush()

    def close(self) -> None:
        handle, self.handle = self.handle, None
        if handle is not None:
            try:
                handle.close()
            except OSError:
                logging.getLogger(__name__).warning('Rollout handle close failed', exc_info=True)

    def sync(self) -> None:
        """Explicit business acknowledgement; ordinary history uses flush only."""
        self.write(force=True)
        assert self.handle is not None
        os.fsync(self.handle.fileno())
        directory_fd = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def _encode(record: RolloutRecord) -> bytes:
    return (json.dumps(record.to_dict(), ensure_ascii=False, allow_nan=False,
                       separators=(',', ':')) + '\n').encode('utf-8')


@dataclass(frozen=True)
class _Command:
    kind: str
    records: tuple[bytes, ...]
    acknowledgement: Future[None]


class RolloutRecorder:
    def __init__(self, path: Path, meta: RolloutRecord | None, *,
                 opener: Callable[[Path], BinaryIO] = open_append, queue_size: int = 256) -> None:
        if queue_size < 1:
            raise ValueError('rollout queue must be bounded and nonempty')
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_file = path.with_suffix('.lock').open('a+b')
        try:
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if meta is not None and path.exists():
                raise FileExistsError(path)
        except BaseException:
            self._lock_file.close()
            raise
        self._state = WriterState(path, meta, opener)
        self._queue: Queue[_Command] = Queue(maxsize=queue_size)
        self._submit_lock = Lock()
        self._failure: BaseException | None = None
        self._closed = False
        self._thread = Thread(target=self._run, name='rollout-writer', daemon=True)
        try:
            self._thread.start()
        except BaseException:
            self._lock_file.close()
            raise

    def record_items(self, records: Iterable[RolloutRecord]) -> tuple[RolloutRecord, ...]:
        """Return filtered records accepted by the queue, before any durability barrier."""
        accepted = tuple(record for record in records if should_persist(record))
        encoded = tuple(_encode(record) for record in accepted)
        if encoded:
            self._command('add', encoded, wait=False)
        return accepted

    def append(self, records: Iterable[RolloutRecord]) -> None:
        self.record_items(records)
        self.flush()

    def persist(self) -> None:
        self._command('persist')

    def flush(self) -> None:
        self._command('flush')

    def sync(self) -> None:
        self._command('sync')

    def shutdown(self) -> None:
        self._command('shutdown')
        self._thread.join()

    def _command(self, kind: str, records: tuple[bytes, ...] = (), *, wait: bool = True) -> None:
        with self._submit_lock:
            if self._closed:
                if kind == 'shutdown':
                    return
                raise OSError('rollout recorder is closed')
            command = _Command(kind, records, Future())
            while True:
                self._check_worker()
                try:
                    self._queue.put(command, timeout=0.05)
                    break
                except Full:
                    continue
            if not wait:
                return
            while True:
                try:
                    command.acknowledgement.result(timeout=0.05)
                    break
                except TimeoutError:
                    if command.acknowledgement.done():
                        command.acknowledgement.result()
                    self._check_worker()
            if kind == 'shutdown':
                self._closed = True

    def _check_worker(self) -> None:
        if not self._thread.is_alive():
            if self._failure is not None:
                raise self._failure
            raise OSError('rollout writer terminated') from self._failure

    def _run(self) -> None:
        command = None
        try:
            while True:
                command = self._queue.get()
                try:
                    if command.kind == 'add':
                        self._state.add(command.records)
                        if self._state.materialized:
                            try:
                                self._state.write()
                            except OSError:
                                logging.getLogger(__name__).warning('Rollout append buffered for retry', exc_info=True)
                    elif command.kind == 'sync':
                        self._state.sync()
                    else:
                        self._state.write(force=command.kind == 'persist')
                except OSError as error:
                    command.acknowledgement.set_exception(error)
                    # A completed Future can retain its caller through a traceback.
                    # Do not keep it alive while this worker waits for another command.
                    command = None
                    continue
                command.acknowledgement.set_result(None)
                if command.kind == 'shutdown':
                    break
                command = None
        except BaseException as error:
            self._failure = error
            if command is not None and not command.acknowledgement.done():
                command.acknowledgement.set_exception(error)
        finally:
            self._state.close()
            self._lock_file.close()
