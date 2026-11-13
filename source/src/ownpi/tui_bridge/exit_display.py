"""Display-only evidence for a runtime that exited without a terminal event."""

from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Any

from ownpi.controller.runtime_active_owner import read_active_runtime_owner
from ownpi.tui_bridge.facts import outcome_payload
from ownpi.tui_bridge.outcome_display import outcome_timing, timestamp


@lru_cache(maxsize=16)
def _read_records(
    path: Path, version: tuple[int, int, int]
) -> tuple[dict[str, Any], ...]:
    records = []
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and timestamp(row.get("timestamp")) is not None:
            records.append(row)
    return tuple(records)


def _records(path: Path) -> tuple[dict[str, Any], ...]:
    try:
        stat = path.stat()
        return _read_records(path, (stat.st_mtime_ns, stat.st_size, stat.st_ino))
    except (OSError, UnicodeError):
        return ()


def _terminal_exception(run_dir: Path) -> dict[str, Any]:
    records = _records(run_dir / "diagnostics/runtime.jsonl")
    starts = [
        row for row in records if row.get("event_type") == "diagnostic_session_started"
    ]
    if not starts:
        return {}
    session = max(starts, key=lambda row: timestamp(row["timestamp"]))
    begun, pid, operation = (
        timestamp(session["timestamp"]),
        session.get("pid"),
        session.get("operation"),
    )
    if (
        type(pid) is not int
        or pid <= 0
        or operation not in {
            "start", "resume", "answer", "answer_tool_permission",
            "admit", "tui_direct_submit",
        }
    ):
        return {}
    if sum(timestamp(row["timestamp"]) == begun for row in starts) != 1:
        return {}
    owner = read_active_runtime_owner(run_dir)
    if owner and owner.pid != pid:
        return {}
    scoped = sorted(
        (
            row
            for row in records
            if row.get("pid") == pid and timestamp(row["timestamp"]) >= begun
        ),
        key=lambda row: timestamp(row["timestamp"]),
    )
    if (
        not scoped
        or scoped[-1].get("event_type") != "diagnostic_session_finished"
        or scoped[-1].get("operation") != operation
    ):
        return {}
    ended = scoped[-1]["timestamp"]
    if owner and (timestamp(owner.started_at) or begun) > timestamp(ended):
        return {}
    # Require an uncaught exception from this very operation, not a recovered
    # provider request, a child failure, or a previous resume's exception.
    if len(scoped) < 2:
        return {}
    terminal = scoped[-2]
    if (
        terminal.get("event_type") != "exception"
        or terminal.get("operation") != operation
    ):
        return {}
    boundary = ""
    for row in _records(run_dir / "rollouts/run-events.jsonl"):
        event = row.get("payload")
        if isinstance(event, dict) and event.get("type") in {
            "direct_request_started",
            "run_initialized",
        }:
            if not boundary or timestamp(row["timestamp"]) > timestamp(boundary):
                boundary = row["timestamp"]
    if boundary and timestamp(boundary) > timestamp(terminal["timestamp"]):
        return {}
    cause = terminal
    # Preserve structured exception type even when request_completed was never
    # emitted (e.g. PoolTimeout before the first SSE event). Never parse the
    # wrapper's message/traceback into invented structured provider metadata.
    if len(scoped) >= 3:
        inner = scoped[-3]
        if (
            inner.get("event_type") == "exception"
            and inner.get("component") == "native_tool_loop"
            and inner.get("reason_code") == "provider_failure"
            and (not boundary or timestamp(inner["timestamp"]) >= timestamp(boundary))
        ):
            cause = inner
    error_type = cause.get("error_type")
    if not isinstance(error_type, str) or not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_.]{0,79}", error_type
    ):
        return {}
    meaning = {
        "PoolTimeout": "Timed out waiting for an available HTTP connection",
        "ConnectTimeout": "Timed out connecting to the model service",
        "ReadTimeout": "Timed out waiting for model response data",
        "ConnectError": "Could not establish a connection to the model service",
    }.get(error_type, "An uncaught runtime exception was recorded")
    summary = f"{error_type}: {meaning}"
    facts = [
        ("Exception", error_type),
        ("Cause", meaning),
        (
            "Component",
            "Native model loop" if cause is not terminal else f"Runtime {operation}",
        ),
        ("Evidence", "This execution's diagnostic log"),
    ]
    # Only allowlisted scalar metadata is copied. Raw exception messages and
    # tracebacks can contain prompts, headers or credentials and stay on disk.
    detail = {
        "source": "runtime_diagnostics",
        "pid": pid,
        "operation": operation,
        "session_started_at": session["timestamp"],
        "session_finished_at": ended,
        "exception_at": cause["timestamp"],
        "error_type": error_type,
        "terminal_error_type": terminal.get("error_type"),
    }
    return {
        "failure_summary": summary,
        "failure_cause": meaning,
        "failure_facts": facts,
        "failure_detail": json.dumps(detail, ensure_ascii=False),
        "failure_code": error_type,
        "timestamp": ended,
        "started_at": boundary or session["timestamp"],
    }


def unexpected_exit_item(run_dir: Path, state: dict[str, Any]) -> dict[str, Any] | None:
    """A transient UI observation, gated by the backend's inactive exit status."""
    if state.get("status") != "unexpected_exit" or state.get("owner_active"):
        return None
    evidence = _terminal_exception(run_dir)
    end, start = evidence.pop("timestamp", ""), evidence.pop("started_at", "")
    payload = {
        **outcome_payload("unexpected_exit"),
        **evidence,
        "task_id": run_dir.name,
        "outcome_source": "runtime_status_observation",
        "display_status": "Unexpected exit",
        "recovery_actions": [
            "Open /resume to inspect recovery; nothing has been restarted."
        ],
    }
    if not evidence:
        payload.update(
            failure_summary="Runtime exited; the cause is not recorded for this execution.",
            failure_facts=[("Cause", "Unknown · no matching terminal diagnostic")],
        )
    if start and end:
        payload.update(outcome_timing(run_dir, start, end, use_run_created_at=False))
    return {
        "schema_version": "ownpi.tui.item.v1",
        "visibility": "user",
        "kind": "task_outcome",
        "item_id": f"{run_dir.name}:observed-exit",
        "status": "failed",
        "timestamp": end,
        "payload": payload,
    }
