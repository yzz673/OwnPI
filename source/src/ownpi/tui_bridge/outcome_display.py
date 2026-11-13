"""Read-only outcome evidence. Diagnostics never decide Runtime state or recovery."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

from ownpi.runtime.sessions import SessionStore, parse_session_run_dir
from ownpi.tui_bridge.facts import _failure_facts


def timestamp(value: object) -> datetime | None:
    try:
        result = datetime.fromisoformat(value) if isinstance(value, str) else None
        return result if result is not None and result.tzinfo is not None else None
    except ValueError:
        return None


def outcome_timing(
    run_dir: Path, initialized: str, finalized: str, *, use_run_created_at: bool = True
) -> dict[str, Any]:
    """Run outcomes use run creation; Direct request outcomes use their own start."""
    start, end = timestamp(initialized), timestamp(finalized)
    identity = parse_session_run_dir(run_dir)
    if identity is not None and use_run_created_at:
        try:
            store = SessionStore(run_dir.parents[4])
            task = store.load_task(identity.session_id, identity.task_id)
            run = next(value for value in task.runs if value.run_id == identity.run_id)
            recorded = timestamp(run.created_at)
            if recorded is not None and end is not None and recorded <= end:
                start = recorded
        except (OSError, ValueError, KeyError, TypeError, StopIteration):
            pass
    if start is None or end is None or end < start:
        return {}
    elapsed_ms = int((end - start).total_seconds() * 1000)
    seconds = elapsed_ms // 1000
    return {
        "elapsed_ms": elapsed_ms,
        "elapsed_display": f"{seconds // 60}m {seconds % 60}s" if seconds >= 60 else f"{seconds}s",
    }


def failure_diagnostics(run_dir: Path, finalized: str, after: str = "") -> dict[str, Any]:
    """Bind the last failed request to its terminal exception in one execution.

    Run directory, diagnostic-session PID, timestamps and structured exception
    type constrain the join. A successful last request, a later resume, child
    PID or ambiguous completion cannot supply this terminal failure's details.
    No error text is parsed to manufacture classification or recovery advice.
    """
    end, previous = timestamp(finalized), timestamp(after)
    if end is None:
        return {}
    try:
        lines = (run_dir / "diagnostics/runtime.jsonl").read_text().splitlines()
    except (OSError, UnicodeError):
        return {}
    records = []
    for line in lines:
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if not isinstance(value, dict):
            continue
        at = timestamp(value.get("timestamp"))
        if at is not None and at <= end and (previous is None or at > previous):
            records.append((at, value))
    starts = [(at, value) for at, value in records
              if value.get("event_type") == "diagnostic_session_started"]
    if not starts:
        return {}
    begun, session = max(starts, key=lambda pair: pair[0])
    pid = session.get("pid")
    if type(pid) is not int or pid <= 0:
        return {}
    scoped = [(at, value) for at, value in records if at >= begun and value.get("pid") == pid]
    requests = [(at, value) for at, value in scoped if value.get("event_type") == "request_completed"]
    if not requests:
        return {}
    completed, request = max(requests, key=lambda pair: pair[0])
    if sum(at == completed for at, _ in requests) != 1 or request.get("success") is not False:
        return {}
    exceptions = [value for at, value in scoped if at >= completed
                  and value.get("event_type") == "exception"
                  and value.get("component") == "native_tool_loop"
                  and value.get("reason_code") == "provider_failure"
                  and value.get("error_type") == request.get("error_type")
                  and isinstance(value.get("error_type"), str)]
    if not exceptions:
        return {}
    attempts = request.get("attempts", [])
    if not isinstance(attempts, list) or (attempts and not isinstance(attempts[-1], dict)):
        return {}
    # Some backend diagnostic envelopes flatten the failure metadata and omit
    # attempts (for example a pre-stream HTTP failure or redacted record).
    provider = attempts[-1] if attempts else request
    facts = _failure_facts({"failure_diagnostics": provider})
    # These booleans are not persisted here. Do not infer them from retryable.
    facts.append(("Resume safety", "Not recorded · open /resume to inspect"))
    selected = {key: request[key] for key in (
        "timestamp", "request_kind", "model", "provider", "attempt_count",
        "outer_sampling_retry_count", "elapsed_ms", "error_type",
    ) if key in request}
    selected["attempts"] = [{key: attempt[key] for key in (
        "attempt", "status", "failure_class", "retryable", "http_status",
        "error_code", "provider_error_code", "error_message", "response_id",
        "recovery_disposition", "elapsed_ms",
    ) if key in attempt} for attempt in attempts if isinstance(attempt, dict)]
    if not attempts:
        selected["failure"] = {key: request[key] for key in (
            "error_code", "error_message", "http_status", "failure_class", "retryable",
            "failure_origin", "recovery_disposition", "__redaction_truncated__",
        ) if key in request}
    result: dict[str, Any] = {
        "failure_facts": facts,
        "failure_detail": json.dumps({"source": "backend_diagnostics", "request": selected}, ensure_ascii=False),
        "failure_cause": "Provider failure recorded in backend diagnostics",
    }
    count, retries = request.get("attempt_count"), request.get("outer_sampling_retry_count")
    if type(count) is int and type(retries) is int and count > 0 and 0 <= retries < count:
        result["final_request_attempt_count"] = count
        result["final_request_retry_count"] = retries
    return result


def merge_outcome(recorded: dict[str, Any], reply: dict[str, Any]) -> dict[str, Any]:
    """Live replies supply explicit facts; empty reply fields cannot erase history."""
    result = {**recorded, **reply}
    for field in ("failure_cause", "failure_detail", "failure_summary", "failure_code",
                  "recovery_actions"):
        if not reply.get(field) and recorded.get(field):
            result[field] = recorded[field]
    for field in ("final_request_attempt_count", "final_request_retry_count"):
        if field not in reply and field in recorded:
            result[field] = recorded[field]
    if recorded.get("failure_detail") and reply.get("failure_detail"):
        result["failure_detail"] = json.dumps({
            "backend_reply": json.loads(reply["failure_detail"]),
            "backend_diagnostics": json.loads(recorded["failure_detail"]),
        }, ensure_ascii=False)
    if reply.get("failure_facts"):
        facts = dict(recorded.get("failure_facts") or [])
        # A partial live reply may add availability without repeating the error.
        # Keep the recorded error fields; explicit reply fields take precedence.
        if any(label == "Resume" for label, _ in reply["failure_facts"]):
            facts.pop("Resume safety", None)
        facts.update(reply["failure_facts"])
        result["failure_facts"] = list(facts.items())
    elif recorded.get("failure_facts"):
        result["failure_facts"] = recorded["failure_facts"]
    return result
