"""Small, serializable lifecycle records owned by the subagent subsystem."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


# Reclaimed describes the child worker, not the processes started by its model.
VALID_CHILD_STATES = frozenset({"created", "running", "waiting", "completed", "failed", "cancelled", "reclaimed", "reclamation_failed"})


@dataclass(frozen=True)
class ChildState:
    batch_id: str
    task_name: str
    child_id: str
    status: str = "created"
    completion_content: str = ""
    error: str | None = None
    task_message: str = ""
    completion_committed: bool = False
    terminal_status: str = ""
    invocation_id: str = ""

    def __post_init__(self) -> None:
        if not self.batch_id or not self.task_name or not self.child_id:
            raise ValueError("child state requires batch_id, task_name and child_id")
        if self.status not in VALID_CHILD_STATES:
            raise ValueError(f"invalid child state: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ChildState":
        for key in ("batch_id", "task_name", "child_id"):
            if not isinstance(payload.get(key), str) or not payload[key].strip():
                raise ValueError(f"child state requires a non-empty string {key}")
        for key in ("status", "completion_content", "task_message", "terminal_status", "invocation_id"):
            if key in payload and not isinstance(payload[key], str):
                raise ValueError(f"child state {key} must be a string")
        if "completion_committed" in payload and not isinstance(payload["completion_committed"], bool):
            raise ValueError("child state completion_committed must be a boolean")
        return cls(
            batch_id=str(payload.get("batch_id", "")),
            task_name=str(payload.get("task_name", "")),
            child_id=str(payload.get("child_id", "")),
            status=str(payload.get("status", "created")),
            completion_content=str(payload.get("completion_content", "")),
            error=(str(payload["error"]) if payload.get("error") is not None else None),
            task_message=str(payload.get("task_message", "")),
            completion_committed=bool(payload.get("completion_committed", False)),
            terminal_status=str(payload.get("terminal_status", "")),
            invocation_id=str(payload.get("invocation_id", "")),
        )
