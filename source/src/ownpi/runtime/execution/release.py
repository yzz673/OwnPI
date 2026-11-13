from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Literal, Protocol

from ownpi.runtime.execution.state import (
    PROCESS_OWNER_LOST_ISSUE,
    ExecutionRecord,
)
from ownpi.runtime.execution.runtime_work import record_needs_runtime_work


class ProcessReleasePort(Protocol):
    def read_process(
        self,
        *,
        owner_id: str,
        process_id: str,
        after_seq: int = 0,
        wait_ms: int = 0,
        max_bytes: int = 65536,
    ) -> dict[str, Any]: ...

    def release_process(
        self,
        *,
        owner_id: str,
        process_id: str,
        final_seq: int,
    ) -> dict[str, Any]: ...


@dataclass
class _PendingRelease:
    execution_id: str
    owner_id: str
    process_id: str
    final_seq: int | None = None
    in_flight: bool = False
    released: bool = False
    outcome: str = 'released'


@dataclass(frozen=True)
class ReleaseAttempt:
    status: Literal["absent", "busy", "retry", "complete"]
    error: str = ""

    @property
    def pending(self) -> bool:
        return self.status in {"busy", "retry"}


class ExecutionReleaseCoordinator:
    """Transient post-commit acknowledgment for closed owner processes."""

    def __init__(
        self, port: ProcessReleasePort | None,
    ) -> None:
        self._port = port
        self._pending: dict[str, _PendingRelease] = {}
        self._completed: set[str] = set()
        self._lock = RLock()

    def register(self, record: ExecutionRecord) -> bool:
        if self._port is None or not _release_eligible(record):
            return False
        with self._lock:
            if record.execution_id in self._completed:
                return False
            existing = self._pending.get(record.execution_id)
            if existing is None:
                self._pending[record.execution_id] = _PendingRelease(
                    execution_id=record.execution_id,
                    owner_id=record.handle.owner_id,
                    process_id=record.handle.process_id,
                )
            elif (
                existing.owner_id != record.handle.owner_id
                or existing.process_id != record.handle.process_id
            ):
                raise RuntimeError(
                    f"execution owner reference changed after terminal commit: {record.execution_id}"
                )
            return True

    def rebuild(self, records: tuple[ExecutionRecord, ...]) -> tuple[str, ...]:
        for record in records:
            self.register(record)
        return self.pending_execution_ids()

    def pending_execution_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._pending)

    def complete_external(self, execution_id: str) -> None:
        self._finish_complete(execution_id)

    def reserve_external(
        self, execution_id: str, *, owner_id: str, process_id: str, final_seq: int,
    ) -> bool:
        with self._lock:
            pending = self._pending.get(execution_id)
            if execution_id in self._completed or (pending is not None and pending.in_flight):
                return False
            if pending is None:
                pending = _PendingRelease(execution_id, owner_id, process_id)
                self._pending[execution_id] = pending
            pending.final_seq = final_seq
            pending.in_flight = True
            return True

    def cancel_external(self, execution_id: str) -> None:
        self._finish_retry(execution_id)

    def needs_release(self, execution_id: str) -> bool:
        with self._lock:
            return execution_id in self._pending

    def attempt(self, execution_id: str) -> ReleaseAttempt:
        port = self._port
        if port is None:
            return ReleaseAttempt("absent")
        with self._lock:
            pending = self._pending.get(execution_id)
            if pending is None:
                return ReleaseAttempt("absent")
            if pending.in_flight:
                return ReleaseAttempt("busy")
            pending.in_flight = True
            owner_id = pending.owner_id
            process_id = pending.process_id
            final_seq = pending.final_seq
            released = pending.released
        if released:
            return self._finish_complete(execution_id)
        try:
            if final_seq is None:
                response = port.read_process(
                    owner_id=owner_id,
                    process_id=process_id,
                    after_seq=0,
                    wait_ms=0,
                    max_bytes=1,
                )
                if not bool(response.get("closed", False)):
                    return self._finish_retry(execution_id)
                final_seq = _final_seq(response.get("final_seq"))
                if final_seq is None:
                    return self._finish_retry(
                        execution_id,
                        error="closed process did not report final_seq",
                    )
                with self._lock:
                    current = self._pending.get(execution_id)
                    if current is None:
                        return ReleaseAttempt("absent")
                    current.final_seq = final_seq
            response = port.release_process(
                owner_id=owner_id,
                process_id=process_id,
                final_seq=final_seq,
            )
            if response.get('released') is not True:
                return self._finish_retry(execution_id, error='owner did not confirm release')
        except Exception as exc:
            if any(
                marker in str(exc)
                for marker in (
                    "unknown process_id",
                    "local process owner is not resumable",
                )
            ):
                pending.outcome = 'owner_unavailable'
                pending.released = True
                return self._finish_complete(execution_id)
            return self._finish_retry(execution_id, error=str(exc))
        pending.released = True
        return self._finish_complete(pending.execution_id)

    def _finish_retry(self, execution_id: str, *, error: str = "") -> ReleaseAttempt:
        with self._lock:
            current = self._pending.get(execution_id)
            if current is not None:
                current.in_flight = False
        return ReleaseAttempt("retry", error=error)

    def _finish_complete(self, execution_id: str) -> ReleaseAttempt:
        with self._lock:
            self._pending.pop(execution_id, None)
            self._completed.add(execution_id)
        return ReleaseAttempt("complete")


def attempt_release_and_reconcile(
    *,
    coordinator: ExecutionReleaseCoordinator,
    execution_id: str,
    lock: RLock,
    state_store: Any,
    scheduler: Any,
) -> ReleaseAttempt:
    attempt = coordinator.attempt(execution_id)
    if attempt.status in {"absent", "busy"}:
        return attempt
    with lock:
        record = state_store.state.execution_registry.get(execution_id)
        if attempt.pending:
            scheduler.schedule(execution_id, immediate=False)
        elif record is None or not record_needs_runtime_work(record):
            scheduler.unschedule(execution_id)
    return attempt


def _release_eligible(record: ExecutionRecord) -> bool:
    return bool(
        record.terminal
        and record.readback_status == "complete"
        and record.issue != PROCESS_OWNER_LOST_ISSUE
        and record.handle.owner_id
        and record.handle.process_id
    )


def _final_seq(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


__all__ = [
    "ExecutionReleaseCoordinator",
    "ProcessReleasePort",
    "ReleaseAttempt",
    "attempt_release_and_reconcile",
]
