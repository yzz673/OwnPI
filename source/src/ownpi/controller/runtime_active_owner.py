from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import logging
import os
from pathlib import Path
import signal
import stat
import threading
import time
from typing import Iterator, Literal
from uuid import uuid4

from ownpi.controller.runtime_control_signal import runtime_interrupt_requested
from ownpi.llm.cancellation import interrupt_provider_io
from ownpi.runtime.events import now_utc_iso
from ownpi.utils.io import write_json


ACTIVE_RUNTIME_OWNER_FILENAME = ".runtime-owner.json"
RUNTIME_INTERRUPT_COOPERATIVE_GRACE_SECONDS = 2.0
RUNTIME_INTERRUPT_TERMINATE_GRACE_SECONDS = 1.0
RuntimeOwnerMode = Literal["fresh", "resume"]


@dataclass(frozen=True)
class ActiveRuntimeOwner:
    schema_version: int
    run_id: str
    pid: int
    process_group_id: int
    generation: str
    mode: RuntimeOwnerMode
    started_at: str

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "ActiveRuntimeOwner":
        mode = str(payload.get("mode", "") or "").strip()
        if mode not in {"fresh", "resume"}:
            raise ValueError(f"unsupported Runtime owner mode: {mode!r}")
        owner = cls(
            schema_version=int(payload.get("schema_version", 0) or 0),
            run_id=str(payload.get("run_id", "") or "").strip(),
            pid=int(payload.get("pid", 0) or 0),
            process_group_id=int(payload.get("process_group_id", 0) or 0),
            generation=str(payload.get("generation", "") or "").strip(),
            mode=mode,  # type: ignore[arg-type]
            started_at=str(payload.get("started_at", "") or "").strip(),
        )
        owner.validate()
        return owner

    def validate(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported Runtime owner schema version")
        if not self.run_id or not self.generation or not self.started_at:
            raise ValueError("Runtime owner identity is incomplete")
        if self.pid <= 0 or self.process_group_id <= 0:
            raise ValueError("Runtime owner process identity is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "pid": self.pid,
            "process_group_id": self.process_group_id,
            "generation": self.generation,
            "mode": self.mode,
            "started_at": self.started_at,
        }


@dataclass(frozen=True)
class RuntimeOwnerNotification:
    notified: bool
    owner: ActiveRuntimeOwner | None = None
    reason: str = ""
    owner_exited: bool = False
    escalated: bool = False
    escalation_signal: int | None = None


@dataclass
class ActiveRuntimeLease:
    owner: ActiveRuntimeOwner
    run_dir: Path
    cancellation_event: threading.Event

    def cancel_requested(self) -> bool:
        return self.cancellation_event.is_set() or runtime_interrupt_requested(
            self.run_dir
        )


def active_runtime_owner_path(run_dir: Path) -> Path:
    return Path(run_dir).expanduser().resolve() / ACTIVE_RUNTIME_OWNER_FILENAME


def read_active_runtime_owner(run_dir: Path) -> ActiveRuntimeOwner | None:
    path = active_runtime_owner_path(run_dir)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Runtime owner metadata is invalid: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Runtime owner metadata must be a JSON object")
    return ActiveRuntimeOwner.from_dict(payload)


@contextmanager
def bind_active_runtime_owner(
    run_dir: Path,
    *,
    run_id: str,
    mode: RuntimeOwnerMode,
) -> Iterator[ActiveRuntimeLease]:
    resolved = Path(run_dir).expanduser().resolve()
    owner = ActiveRuntimeOwner(
        schema_version=1,
        run_id=str(run_id or "").strip(),
        pid=os.getpid(),
        process_group_id=os.getpgrp(),
        generation=f"owner-{uuid4().hex}",
        mode=mode,
        started_at=now_utc_iso(),
    )
    owner.validate()
    cancellation_event = threading.Event()
    previous_handler: object | None = None
    signal_number = getattr(signal, "SIGUSR1", None)
    can_install_handler = (
        signal_number is not None
        and threading.current_thread() is threading.main_thread()
    )
    if can_install_handler:
        previous_handler = signal.getsignal(signal_number)

        def handle_wakeup(_signum: int, _frame: object) -> None:
            cancellation_event.set()
            interrupt_provider_io()

        signal.signal(signal_number, handle_wakeup)
    write_json(active_runtime_owner_path(resolved), owner.to_dict())
    try:
        yield ActiveRuntimeLease(
            owner=owner,
            run_dir=resolved,
            cancellation_event=cancellation_event,
        )
    finally:
        if can_install_handler and previous_handler is not None:
            signal.signal(signal_number, previous_handler)
        try:
            current = read_active_runtime_owner(resolved)
            if current is not None and current.generation == owner.generation:
                active_runtime_owner_path(resolved).unlink(missing_ok=True)
        except (OSError, ValueError) as error:
            logging.getLogger(__name__).warning(
                "Failed to release active runtime owner %s: %s", resolved, error,
            )


def notify_active_runtime_owner(
    run_dir: Path,
    *,
    cooperative_grace_seconds: float = RUNTIME_INTERRUPT_COOPERATIVE_GRACE_SECONDS,
    terminate_grace_seconds: float = RUNTIME_INTERRUPT_TERMINATE_GRACE_SECONDS,
) -> RuntimeOwnerNotification:
    resolved = Path(run_dir).expanduser().resolve()
    if not runtime_owner_is_active(resolved):
        return RuntimeOwnerNotification(notified=False, reason="runtime_inactive")
    try:
        owner = read_active_runtime_owner(resolved)
    except ValueError as exc:
        return RuntimeOwnerNotification(notified=False, reason=str(exc))
    if owner is None:
        return RuntimeOwnerNotification(notified=False, reason="active_owner_missing")
    signal_number = getattr(signal, "SIGUSR1", None)
    if signal_number is None:
        return RuntimeOwnerNotification(
            notified=False,
            owner=owner,
            reason="interrupt_signal_unavailable",
        )
    try:
        os.kill(owner.pid, signal_number)
    except ProcessLookupError:
        return RuntimeOwnerNotification(
            notified=False,
            owner=owner,
            reason="active_owner_process_missing",
        )
    except PermissionError:
        return RuntimeOwnerNotification(
            notified=False,
            owner=owner,
            reason="active_owner_signal_denied",
        )
    if owner.pid == os.getpid():
        return RuntimeOwnerNotification(notified=True, owner=owner)
    if _wait_for_owner_exit(
        resolved,
        owner=owner,
        timeout_seconds=cooperative_grace_seconds,
    ):
        return RuntimeOwnerNotification(
            notified=True,
            owner=owner,
            owner_exited=True,
        )
    if not _owner_identity_is_current(resolved, owner):
        return RuntimeOwnerNotification(
            notified=True,
            owner=owner,
            reason="active_owner_changed_before_escalation",
        )
    escalation_signal = signal.SIGTERM
    try:
        _signal_verified_owner(owner, escalation_signal)
    except ProcessLookupError:
        _clear_owner_if_released(resolved, owner)
        return RuntimeOwnerNotification(
            notified=True,
            owner=owner,
            owner_exited=True,
        )
    owner_exited = _wait_for_owner_exit(
        resolved,
        owner=owner,
        timeout_seconds=terminate_grace_seconds,
    )
    if not owner_exited and _owner_identity_is_current(resolved, owner):
        escalation_signal = signal.SIGKILL
        try:
            _signal_verified_owner(owner, escalation_signal)
        except ProcessLookupError:
            pass
        owner_exited = _wait_for_owner_exit(
            resolved,
            owner=owner,
            timeout_seconds=terminate_grace_seconds,
        )
    if owner_exited:
        _wait_for_runtime_owner_release(
            resolved,
            timeout_seconds=terminate_grace_seconds,
        )
        _clear_owner_if_released(resolved, owner)
    return RuntimeOwnerNotification(
        notified=True,
        owner=owner,
        owner_exited=owner_exited,
        escalated=True,
        escalation_signal=int(escalation_signal),
    )


def _wait_for_owner_exit(
    run_dir: Path,
    *,
    owner: ActiveRuntimeOwner,
    timeout_seconds: float,
) -> bool:
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while time.monotonic() < deadline:
        if not _owner_identity_is_current(run_dir, owner):
            return True
        time.sleep(0.05)
    return not _owner_identity_is_current(run_dir, owner)


def _owner_identity_is_current(
    run_dir: Path,
    owner: ActiveRuntimeOwner,
) -> bool:
    if not runtime_owner_is_active(run_dir):
        return False
    try:
        current = read_active_runtime_owner(run_dir)
        process_group_id = os.getpgid(owner.pid)
    except (OSError, ValueError):
        return False
    return (
        current is not None
        and current.generation == owner.generation
        and current.pid == owner.pid
        and current.process_group_id == owner.process_group_id
        and process_group_id == owner.process_group_id
    )


def _signal_verified_owner(owner: ActiveRuntimeOwner, signal_number: int) -> None:
    if owner.process_group_id == owner.pid:
        os.killpg(owner.process_group_id, signal_number)
        return
    os.kill(owner.pid, signal_number)


def _wait_for_runtime_owner_release(
    run_dir: Path,
    *,
    timeout_seconds: float,
) -> bool:
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while time.monotonic() < deadline:
        if not runtime_owner_is_active(run_dir):
            return True
        time.sleep(0.05)
    return not runtime_owner_is_active(run_dir)


def _clear_owner_if_released(run_dir: Path, owner: ActiveRuntimeOwner) -> None:
    if runtime_owner_is_active(run_dir):
        return
    try:
        current = read_active_runtime_owner(run_dir)
    except ValueError:
        return
    if current is not None and current.generation == owner.generation:
        try:
            active_runtime_owner_path(run_dir).unlink(missing_ok=True)
        except OSError as error:
            logging.getLogger(__name__).warning(
                "Failed to clear released runtime owner %s: %s", run_dir, error,
            )


def runtime_owner_is_active(run_dir: Path) -> bool:
    resolved = Path(run_dir).expanduser().resolve()
    try:
        descriptor = os.open(resolved / '.runtime.lock', os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return False
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError(f'filesystem lock is not a regular file: {resolved / ".runtime.lock"}')
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        else:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            return False
    finally:
        os.close(descriptor)


__all__ = [
    "ACTIVE_RUNTIME_OWNER_FILENAME",
    "RUNTIME_INTERRUPT_COOPERATIVE_GRACE_SECONDS",
    "RUNTIME_INTERRUPT_TERMINATE_GRACE_SECONDS",
    "ActiveRuntimeLease",
    "ActiveRuntimeOwner",
    "RuntimeOwnerNotification",
    "active_runtime_owner_path",
    "bind_active_runtime_owner",
    "notify_active_runtime_owner",
    "read_active_runtime_owner",
    "runtime_owner_is_active",
]
