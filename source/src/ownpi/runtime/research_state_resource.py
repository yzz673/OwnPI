from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import os
from pathlib import Path
import stat
from typing import Any

from ownpi.runtime.state_models import RuntimeState
from ownpi.schemas.workflow_plan import ExecutionTier, StageRuntimeProfile
from ownpi.utils.io import write_text_atomic


RESEARCH_STATE_FILENAME = "research_state.md"
RESEARCH_STATE_SCAFFOLD = """# Research State

This is the canonical record of durable research progress.
Core reads and writes it. Update it when a conclusion, decision, evidence,
failed direction, lesson, or next priority matters to later work. Do not copy tool logs,
trajectories, or routine stage activity.

## Current Direction

## Established Findings and Evidence

## Decisions and Rationale

## Methods and Experiments

## Invalidated Directions and Lessons

## Open Questions and Next Priorities

## Important Artifact Locators
"""


@dataclass(frozen=True)
class ResearchStateFileSnapshot:
    path: Path
    content: str
    sha256: str
    size_bytes: int
    mtime_ns: int


def research_state_path(run_dir: Path) -> Path:
    return Path(run_dir).expanduser().resolve() / RESEARCH_STATE_FILENAME


def runtime_uses_research_state(state: RuntimeState) -> bool:
    workflow_state = state.workflow_state
    return (
        workflow_state.execution_tier == ExecutionTier.FULL
        and workflow_state.stage_runtime_profile == StageRuntimeProfile.RESEARCH_LEVEL
    )


def runtime_state_view_uses_research_state(runtime_state_view: dict[str, Any]) -> bool:
    return (
        str(runtime_state_view.get("execution_tier") or "") == ExecutionTier.FULL.value
        and str(runtime_state_view.get("stage_runtime_profile") or "")
        == StageRuntimeProfile.RESEARCH_LEVEL.value
    )


def initialize_research_state(
    *,
    run_dir: Path,
    state: RuntimeState,
) -> ResearchStateFileSnapshot | None:
    if not runtime_uses_research_state(state):
        return None

    path = research_state_path(run_dir)
    if os.path.lexists(path):
        expected_sha256 = state.research_state_sha256
        if expected_sha256 is None:
            raise ValueError(
                "Research State already exists but RuntimeState digest metadata is missing"
            )
        return inspect_research_state_file(
            run_dir=run_dir,
            expected_sha256=expected_sha256,
        )

    if state.research_state_sha256 is not None:
        raise FileNotFoundError(
            "RuntimeState declares Research State metadata but the canonical file is missing"
        )

    write_text_atomic(path, RESEARCH_STATE_SCAFFOLD)
    snapshot = inspect_research_state_file(run_dir=run_dir)
    state.research_state_sha256 = snapshot.sha256
    return snapshot


def inspect_research_state_file(
    *,
    run_dir: Path,
    expected_sha256: str | None = None,
) -> ResearchStateFileSnapshot:
    path = research_state_path(run_dir)
    try:
        file_stat = path.lstat()
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"Canonical Research State file is missing: {path}"
        ) from exc

    if stat.S_ISLNK(file_stat.st_mode):
        raise ValueError("Canonical Research State path must not be a symbolic link")
    if not stat.S_ISREG(file_stat.st_mode):
        raise ValueError("Canonical Research State path must be a regular file")
    if file_stat.st_nlink != 1:
        raise ValueError(
            "Canonical Research State file must have exactly one hard link"
        )

    raw_content = path.read_bytes()
    if not raw_content:
        raise ValueError("Canonical Research State file must not be empty")
    try:
        content = raw_content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            "Canonical Research State file must contain valid UTF-8"
        ) from exc
    if not content.strip():
        raise ValueError(
            "Canonical Research State file must contain non-whitespace text"
        )

    actual_sha256 = sha256(raw_content).hexdigest()
    if expected_sha256 is not None and actual_sha256 != expected_sha256:
        raise ValueError(
            "Canonical Research State digest does not match RuntimeState metadata"
        )
    return ResearchStateFileSnapshot(
        path=path,
        content=content,
        sha256=actual_sha256,
        size_bytes=len(raw_content),
        mtime_ns=file_stat.st_mtime_ns,
    )


def build_research_state_inspection_projection(
    *,
    run_dir: Path,
    runtime_state_view: dict[str, Any] | None,
) -> dict[str, Any]:
    root = Path(run_dir).expanduser().resolve()
    path = research_state_path(root)
    configured = bool(
        isinstance(runtime_state_view, dict)
        and runtime_state_view_uses_research_state(runtime_state_view)
    )
    expected_sha256 = (
        runtime_state_view.get("research_state_sha256")
        if isinstance(runtime_state_view, dict)
        else None
    )
    projection: dict[str, Any] = {
        "configured": configured,
        "available": False,
        "relative_path": str(path.relative_to(root)),
        "expected_sha256": (
            expected_sha256 if isinstance(expected_sha256, str) else None
        ),
        "actual_sha256": None,
        "integrity_status": "not_applicable",
        "mtime": None,
        "mtime_ns": None,
        "bytes": None,
    }
    try:
        file_stat = path.lstat()
    except FileNotFoundError:
        if configured:
            projection["integrity_status"] = "missing"
        return projection
    except OSError:
        projection["integrity_status"] = "unreadable"
        return projection

    projection.update(
        {
            "available": True,
            "mtime": datetime.fromtimestamp(
                file_stat.st_mtime,
                tz=timezone.utc,
            ).isoformat(),
            "mtime_ns": file_stat.st_mtime_ns,
            "bytes": file_stat.st_size,
        }
    )
    if stat.S_ISLNK(file_stat.st_mode):
        projection["integrity_status"] = "invalid_symlink"
        return projection
    if not stat.S_ISREG(file_stat.st_mode):
        projection["integrity_status"] = "invalid_file_type"
        return projection
    if file_stat.st_nlink != 1:
        projection["integrity_status"] = "invalid_hard_link"
        return projection
    try:
        raw_content = path.read_bytes()
    except OSError:
        projection["integrity_status"] = "unreadable"
        return projection
    projection["bytes"] = len(raw_content)
    projection["actual_sha256"] = sha256(raw_content).hexdigest()
    if not raw_content:
        projection["integrity_status"] = "empty"
        return projection
    try:
        content = raw_content.decode("utf-8")
    except UnicodeDecodeError:
        projection["integrity_status"] = "invalid_utf8"
        return projection
    if not content.strip():
        projection["integrity_status"] = "empty"
        return projection
    if not configured:
        projection["integrity_status"] = "unexpected_file"
        return projection
    if not isinstance(expected_sha256, str):
        projection["integrity_status"] = "metadata_missing"
        return projection
    projection["integrity_status"] = (
        "ok" if projection["actual_sha256"] == expected_sha256 else "digest_mismatch"
    )
    return projection


__all__ = [
    "RESEARCH_STATE_FILENAME",
    "RESEARCH_STATE_SCAFFOLD",
    "ResearchStateFileSnapshot",
    "build_research_state_inspection_projection",
    "initialize_research_state",
    "inspect_research_state_file",
    "research_state_path",
    "runtime_uses_research_state",
    "runtime_state_view_uses_research_state",
]
