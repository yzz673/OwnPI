"""Minimal contracts for the subagent skeleton.

Detailed schemas are intentionally deferred to the later subagent design work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from ownpi.schemas.runtime_human_clarification import HumanClarificationSession


@dataclass(frozen=True)
class SubagentTask:
    task_name: str
    message: str

    def __post_init__(self) -> None:
        if not self.task_name.strip() or not self.message.strip():
            raise ValueError("subagent task_name and message must be non-empty")


@dataclass(frozen=True)
class SubagentContext:
    task: SubagentTask
    workspace: Path | None = None
    parent_run_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SubagentResult:
    task_name: str
    status: str
    content: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    pending_interactions: tuple[HumanClarificationSession, ...] = ()
