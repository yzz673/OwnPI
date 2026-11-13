from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ownpi.runtime.hooks.types import HookModelContextFragment


TaskSpecAuthoringStatus = Literal["completed", "failed", "skipped"]


@dataclass(frozen=True)
class TaskSpecAuthoringRequest:
    request_id: str
    raw_user_message: str
    run_dir: Path
    project_root: Path
    run_id: str
    session_id: str
    task_id: str
    provider: str = ""
    model: str = ""
    hook_prefix_fragments: tuple[HookModelContextFragment, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "raw_user_message": self.raw_user_message,
            "run_dir": str(self.run_dir),
            "project_root": str(self.project_root),
            "provider": self.provider,
            "model": self.model,
        }


@dataclass(frozen=True)
class TaskSpecArtifactRef:
    raw_message_path: str
    task_spec_path: str
    content_hash: str
    raw_message_hash: str
    revision: int = 1

    def __post_init__(self) -> None:
        if self.revision < 1:
            raise ValueError("TaskSpec artifact revision must be positive.")
        if not self.raw_message_path or not self.task_spec_path:
            raise ValueError("TaskSpec artifact paths must not be empty.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_message_path": self.raw_message_path,
            "task_spec_path": self.task_spec_path,
            "content_hash": self.content_hash,
            "raw_message_hash": self.raw_message_hash,
            "revision": self.revision,
        }

@dataclass(frozen=True)
class TaskSpecAuthoringOutcome:
    status: TaskSpecAuthoringStatus
    request_id: str
    artifact: TaskSpecArtifactRef | None = None
    turns: int = 0
    tool_call_count: int = 0
    inspected_path_count: int = 0
    provider_request_ids: tuple[str, ...] = ()
    failure_class: str = ""
    failure_message: str = ""

    def __post_init__(self) -> None:
        if self.turns < 0 or self.tool_call_count < 0 or self.inspected_path_count < 0:
            raise ValueError("TaskSpec authoring counters must not be negative.")
        if self.status == "completed" and self.artifact is None:
            raise ValueError("Completed TaskSpec authoring requires an artifact.")
        if self.status == "failed" and not self.failure_class:
            raise ValueError("Failed TaskSpec authoring requires a failure class.")
        if self.status == "skipped" and self.artifact is not None:
            raise ValueError("Skipped TaskSpec authoring must not have an artifact.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "request_id": self.request_id,
            "artifact": self.artifact.to_dict() if self.artifact is not None else None,
            "turns": self.turns,
            "tool_call_count": self.tool_call_count,
            "inspected_path_count": self.inspected_path_count,
            "provider_request_ids": list(self.provider_request_ids),
            "failure_class": self.failure_class,
            "failure_message": self.failure_message,
        }
