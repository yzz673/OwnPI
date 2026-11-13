"""One run-scoped writer using the shared rollout recorder, never legacy stores."""

from __future__ import annotations

from pathlib import Path
import atexit
import stat
from threading import RLock
from typing import Any
import weakref

from ownpi.runtime.native_tools.provider_json import FrozenProviderJson
from ownpi.runtime.rollout.business_events import BusinessEvent
from ownpi.runtime.rollout.business_reader import read_business_events
from ownpi.runtime.rollout.codec import RolloutRecord
from ownpi.runtime.rollout.recorder import RolloutRecorder
from ownpi.runtime.rollout.resources import RecorderCloseOwner


def run_events_path(run_dir: Path) -> Path:
    return Path(run_dir).resolve() / 'rollouts' / 'run-events.jsonl'


def _validate_file(path: Path) -> None:
    try:
        status = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
        raise ValueError(f'Run event target must be a regular unlinked file: {path.name}')


class ClosedRunEventStoreError(OSError):
    """An event was submitted after its writer was closed."""


class RunEventStore:
    def __init__(self, run_dir: Path, run_id: str) -> None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError('Run event store requires a run ID')
        self.run_id = run_id
        self.path = run_events_path(run_dir)
        if self.path.parent.is_symlink():
            raise ValueError('Rollout directory must not be a symbolic link')
        _validate_file(self.path)
        _validate_file(self.path.with_suffix('.lock'))
        self._lock = RLock()
        self._events: dict[str, BusinessEvent] = {}
        self._pending: dict[str, BusinessEvent] = {}
        self._queued: set[str] = set()
        self._close_retry_registered = False
        self.pending_hook_contexts: dict[str, FrozenProviderJson] = {}
        self.parse_errors = 0
        meta = None if self.path.exists() else RolloutRecord.create('session_meta', {
            'id': f'run:{run_id}', 'scope': 'run_events', 'run_id': run_id, 'schema_version': 1,
        })
        self.recorder = RolloutRecorder(self.path, meta)
        self._owner = RecorderCloseOwner(self.recorder, durable=True)
        self._finalizer = weakref.finalize(self, self._owner.finalize)
        try:
            if meta is None:
                result = read_business_events(self.path, run_id=run_id)
                self._events = {event.event_id: event for event in result.events}
                self.parse_errors = result.parse_errors
            self.recorder.sync()
        except BaseException:
            self._owner.close()
            raise

    @property
    def closed(self) -> bool:
        return self._owner.closed

    @property
    def events(self) -> tuple[BusinessEvent, ...]:
        with self._lock:
            return tuple(self._events.values())

    def emit(self, kind: str, event_id: str, data: dict[str, Any]) -> BusinessEvent:
        with self._lock:
            if self.closed:
                raise ClosedRunEventStoreError('Run event store is closed')
            value = FrozenProviderJson.from_value(data)
            existing = self._events.get(event_id) or self._pending.get(event_id)
            if existing is not None:
                if existing.kind != kind or existing.data != value:
                    raise ValueError(f'Conflicting business event: {event_id}')
                self.flush()
                return existing
            record = RolloutRecord.create('event_msg', {})
            event = BusinessEvent(kind, event_id, self.run_id, value, record.timestamp)
            self._pending[event_id] = event
            self.flush()
            return event

    def flush(self) -> None:
        with self._lock:
            for identity, event in self._pending.items():
                if identity not in self._queued:
                    encoded = RolloutRecord('event_msg', FrozenProviderJson.from_value(event.payload()),
                                            timestamp=event.timestamp)
                    self.recorder.record_items((encoded,))
                    self._queued.add(identity)
            self.recorder.sync()
            self._events.update(self._pending)
            self._pending.clear()
            self._queued.clear()

    def close(self) -> bool:
        with self._lock:
            if self.closed:
                return True
            try:
                from ownpi.runtime.rollout.hook_events import flush_hook_registrations

                flush_hook_registrations(self)
                self.flush()
            except OSError:
                # Retain the store too: pending events may not have reached the recorder.
                if not self._close_retry_registered:
                    atexit.register(self.close)
                    self._close_retry_registered = True
                return False
            if not self._owner.close():
                return False
            self._events.update(self._pending)
            self._pending.clear()
            self._finalizer.detach()
            if self._close_retry_registered:
                atexit.unregister(self.close)
                self._close_retry_registered = False
            return True

    def __enter__(self) -> RunEventStore:
        return self

    def __exit__(self, *args: object) -> None:
        if not self.close() and args[0] is None:
            raise OSError('Run event persistence remains unconfirmed')
