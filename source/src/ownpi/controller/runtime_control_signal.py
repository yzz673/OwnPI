from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Literal
from uuid import uuid4

from ownpi.runtime.events import now_utc_iso
from ownpi.utils.locking import filesystem_write_lock
from ownpi.utils.io import ensure_dir

if TYPE_CHECKING:
    from ownpi.runtime.rollout.run_events import RunEventStore


RuntimeControlAction = Literal["pause", "interrupt"]
RuntimeControlSubmissionDisposition = Literal["accepted", "pending"]
RUNTIME_CONTROL_REQUEST_FILENAME = ".runtime-control.json"
RUNTIME_CONTROL_LOCK_FILENAME = ".runtime-control.lock"


@dataclass(frozen=True)
class RuntimeControlRequest:
    """A minimal external control intent consumed at a Runtime safe point."""

    request_id: str
    action: RuntimeControlAction
    requested_at: str
    reason: str = ""

    def __post_init__(self) -> None:
        if not str(self.request_id or "").strip():
            raise ValueError("runtime control request_id is required")
        if self.action not in {"pause", "interrupt"}:
            raise ValueError(f"unsupported Runtime control action: {self.action}")
        if not str(self.requested_at or "").strip():
            raise ValueError("runtime control requested_at is required")

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "RuntimeControlRequest":
        action = str(payload.get("action", "") or "").strip().lower()
        if action not in {"pause", "interrupt"}:
            raise ValueError(f"unsupported Runtime control action: {action!r}")
        return cls(
            request_id=str(payload.get("request_id", "") or "").strip(),
            action=action,  # type: ignore[arg-type]
            requested_at=str(payload.get("requested_at", "") or "").strip(),
            reason=str(payload.get("reason", "") or "").strip(),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "request_id": self.request_id,
            "action": self.action,
            "requested_at": self.requested_at,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RuntimeControlSubmission:
    """Atomic submission outcome for a Runtime control intent."""

    request: RuntimeControlRequest
    disposition: RuntimeControlSubmissionDisposition


class RuntimeControlRequested(RuntimeError):
    """Raised after a control intent has been durably consumed."""

    def __init__(self, request: RuntimeControlRequest) -> None:
        self.request = request
        super().__init__(
            f"Runtime {request.action} requested at safe point "
            f"({request.request_id})"
        )


def runtime_control_request_path(run_dir: Path) -> Path:
    return Path(run_dir).expanduser().resolve() / RUNTIME_CONTROL_REQUEST_FILENAME


def runtime_control_lock_path(run_dir: Path) -> Path:
    return Path(run_dir).expanduser().resolve() / RUNTIME_CONTROL_LOCK_FILENAME


@contextmanager
def runtime_control_lock(run_dir: Path) -> Iterator[None]:
    with filesystem_write_lock(runtime_control_lock_path(run_dir)):
        yield


def read_runtime_control_request(run_dir: Path) -> RuntimeControlRequest | None:
    path = runtime_control_request_path(run_dir)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Runtime control request must be a JSON object: {path}")
        return RuntimeControlRequest.from_dict(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        logging.getLogger(__name__).warning(
            "Ignoring malformed runtime control request %s: %s", path, error,
        )
        return None


def _control_request_consumed(run_dir: Path, request: RuntimeControlRequest) -> bool:
    from ownpi.runtime.rollout.business_reader import read_business_events
    from ownpi.runtime.rollout.run_events import run_events_path

    path = run_events_path(run_dir)
    if path.exists():
        events = read_business_events(path, run_id=run_dir.name).events
        return any(event.event_id == f'runtime-control-consumed:{request.request_id}'
                   for event in events)
    return False


def runtime_interrupt_requested(run_dir: Path) -> bool:
    request = read_runtime_control_request(run_dir)
    return (request is not None and request.action == "interrupt"
            and not _control_request_consumed(run_dir, request))


def submit_runtime_control(
    run_dir: Path,
    *,
    action: RuntimeControlAction,
    reason: str = "",
) -> RuntimeControlRequest:
    """Submit an idempotent control request without changing Runtime state."""

    return submit_runtime_control_with_disposition(
        run_dir,
        action=action,
        reason=reason,
    ).request


def submit_runtime_control_with_disposition(
    run_dir: Path,
    *,
    action: RuntimeControlAction,
    reason: str = "",
) -> RuntimeControlSubmission:
    """Submit a control intent and report whether it was new or pending."""

    if action not in {"pause", "interrupt"}:
        raise ValueError(f"unsupported Runtime control action: {action}")
    resolved_run_dir = Path(run_dir).expanduser().resolve()
    if not resolved_run_dir.is_dir():
        raise FileNotFoundError(f"Run directory not found: {resolved_run_dir}")
    import json
    import os

    with runtime_control_lock(resolved_run_dir):
        existing = read_runtime_control_request(resolved_run_dir)
        if existing is not None and not _control_request_consumed(resolved_run_dir, existing):
            if existing.action == "interrupt" or existing.action == action:
                return RuntimeControlSubmission(
                    request=existing,
                    disposition="pending",
                )
        request = RuntimeControlRequest(
            request_id=f"control-{uuid4().hex}",
            action=action,
            requested_at=now_utc_iso(),
            reason=str(reason or "").strip(),
        )
        path = runtime_control_request_path(resolved_run_dir)
        ensure_dir(path.parent)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(request.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
        return RuntimeControlSubmission(
            request=request,
            disposition="accepted",
        )


def clear_runtime_control_request(
    run_dir: Path,
    *,
    request_id: str,
) -> bool:
    """Clear only the request that was consumed, preserving a newer request."""

    path = runtime_control_request_path(run_dir)
    current = read_runtime_control_request(run_dir)
    if current is None or current.request_id != str(request_id or ""):
        return False
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logging.getLogger(__name__).warning(
            "Failed to clear consumed runtime control request %s", path, exc_info=True,
        )
        return False
    return True


def consume_runtime_control_request(run_dir: Path, request: RuntimeControlRequest,
                                    *, event_store: RunEventStore | None = None) -> bool:
    """Commit consumption before cleanup; callers hold the control lock."""
    if event_store is None:
        from ownpi.runtime.rollout.run_events import RunEventStore

        with RunEventStore(run_dir, run_dir.name) as events:
            return consume_runtime_control_request(run_dir, request, event_store=events)
    identity = f'runtime-control-consumed:{request.request_id}'
    consumed = any(event.event_id == identity for event in event_store.events)
    event_store.emit('runtime_control_consumed', identity, {
        'request_id': request.request_id, 'action': request.action,
        'requested_at': request.requested_at, 'reason': request.reason,
    })
    return not consumed


def clear_consumed_runtime_control_request(run_dir: Path, *, request_id: str) -> bool:
    """Clean up a consumed request without replacing its runtime result."""

    try:
        with runtime_control_lock(run_dir):
            return clear_runtime_control_request(run_dir, request_id=request_id)
    except OSError:
        logging.getLogger(__name__).warning(
            "Failed to lock cleanup of consumed runtime control request in %s",
            run_dir, exc_info=True,
        )
        return False


__all__ = [
    "RUNTIME_CONTROL_LOCK_FILENAME",
    "RUNTIME_CONTROL_REQUEST_FILENAME",
    "RuntimeControlAction",
    "RuntimeControlRequest",
    "RuntimeControlRequested",
    "RuntimeControlSubmission",
    "RuntimeControlSubmissionDisposition",
    "clear_runtime_control_request",
    "clear_consumed_runtime_control_request",
    "consume_runtime_control_request",
    "read_runtime_control_request",
    "runtime_interrupt_requested",
    "runtime_control_lock",
    "runtime_control_lock_path",
    "runtime_control_request_path",
    "submit_runtime_control",
    "submit_runtime_control_with_disposition",
]
