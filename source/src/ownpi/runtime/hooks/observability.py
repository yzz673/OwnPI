from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import Lock

from ownpi.runtime.events import now_utc_iso
from ownpi.runtime.hooks.types import HookEventName, HookRunReceipt


DEFAULT_HOOK_LIFECYCLE_BUFFER = 256


@dataclass(frozen=True)
class HookLifecycleEvent:
    run_id: str
    event_name: HookEventName
    handler_key: str
    status: str
    created_at: str
    receipt_id: str = ""
    error: str = ""


class HookObservability:
    def __init__(self, max_events: int = DEFAULT_HOOK_LIFECYCLE_BUFFER) -> None:
        self._events: deque[HookLifecycleEvent] = deque(maxlen=max(1, max_events))
        self._lock = Lock()

    def started(self, *, run_id: str, event_name: HookEventName, handler_key: str) -> None:
        self._append(
            HookLifecycleEvent(
                run_id=run_id,
                event_name=event_name,
                handler_key=handler_key,
                status="started",
                created_at=now_utc_iso(),
            )
        )

    def completed(self, *, run_id: str, receipt: HookRunReceipt) -> None:
        self._append(
            HookLifecycleEvent(
                run_id=run_id,
                event_name=receipt.event_name,
                handler_key=receipt.handler_key,
                status=receipt.status,
                created_at=now_utc_iso(),
                receipt_id=receipt.receipt_id,
                error=receipt.error or receipt.schema_error,
            )
        )

    def snapshot(self) -> tuple[HookLifecycleEvent, ...]:
        with self._lock:
            return tuple(self._events)

    def _append(self, event: HookLifecycleEvent) -> None:
        with self._lock:
            self._events.append(event)
