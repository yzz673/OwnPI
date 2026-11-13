from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Any, Callable
from uuid import uuid4

from ownpi.runtime.process_owner.process_registry import ProcessRegistry
from ownpi.runtime.process_owner.protocol import ProcessOwnerProtocolError


NotificationSink = Callable[[str, dict[str, Any]], None]


@dataclass
class SessionEntry:
    session_id: str
    process_registry: ProcessRegistry
    connection_id: str
    notification_sink: NotificationSink
    detached_at: float | None = None


class SessionRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, SessionEntry] = {}
        self._closed = False

    def attach(
        self,
        *,
        connection_id: str,
        notification_sink: NotificationSink,
        resume_session_id: str = "",
    ) -> SessionEntry:
        with self._lock:
            if self._closed:
                raise ProcessOwnerProtocolError("session registry is shut down")
            if resume_session_id:
                entry = self._sessions.get(resume_session_id)
                if entry is None:
                    raise ProcessOwnerProtocolError(
                        "unknown or expired resume_session_id"
                    )
                if entry.detached_at is None:
                    raise ProcessOwnerProtocolError("session is already attached")
                entry.connection_id = connection_id
                entry.notification_sink = notification_sink
                entry.detached_at = None
                return entry
            else:
                session_id = f"owner-session-{uuid4().hex}"
                entry = SessionEntry(
                    session_id=session_id,
                    process_registry=ProcessRegistry(
                        event_sink=lambda method, params: self._notify(
                            session_id, method, params
                        ),
                    ),
                    connection_id=connection_id,
                    notification_sink=notification_sink,
                )
                self._sessions[session_id] = entry
                return entry

    def detach(self, *, session_id: str, connection_id: str, recoverable: bool) -> None:
        immediate: SessionEntry | None = None
        with self._lock:
            entry = self._sessions.get(session_id)
            if entry is None or entry.connection_id != connection_id:
                return
            if not recoverable:
                immediate = self._sessions.pop(session_id)
            else:
                entry.detached_at = time.monotonic()
                entry.notification_sink = _discard_notification
        if immediate is not None:
            immediate.process_registry.shutdown()

    def maintenance(self) -> int:
        with self._lock:
            registries = tuple(
                entry.process_registry for entry in self._sessions.values()
            )
        for registry in registries:
            registry.maintenance()
        with self._lock:
            empty = [entry for entry in self._sessions.values()
                     if entry.detached_at is not None and not entry.process_registry.has_processes()]
            for entry in empty:
                self._sessions.pop(entry.session_id)
        for entry in empty:
            entry.process_registry.shutdown()
        return 0

    def has_sessions(self) -> bool:
        with self._lock:
            return bool(self._sessions)

    def shutdown(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            entries = list(self._sessions.values())
            self._sessions.clear()
        for entry in entries:
            entry.process_registry.shutdown()

    def _notify(self, session_id: str, method: str, params: dict[str, Any]) -> None:
        with self._lock:
            entry = self._sessions.get(session_id)
            sink = (
                entry.notification_sink if entry is not None else _discard_notification
            )
        try:
            sink(method, params)
        except (OSError, ValueError):
            return


def _discard_notification(_method: str, _params: dict[str, Any]) -> None:
    return
