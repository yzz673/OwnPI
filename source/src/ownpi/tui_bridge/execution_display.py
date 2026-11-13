"""Read-only command display, bound to backend execution identities and facts."""
from __future__ import annotations

from typing import Any
import shlex

from ownpi.runtime.execution.state import ExecutionRecord

PROCESS_TOOLS = frozenset({"exec_command", "write_stdin", "execution_monitor", "execution_kill"})
DISPLAY_STATES = {"running": "running", "terminal_success": "completed",
                  "terminal_failure": "failed", "unknown": "unknown", "blocked": "blocked"}


def is_rg_no_matches(command: str, code: object, output: str, truncated: bool) -> bool:
    """Recognize only a standalone rg invocation; keep its canonical exit code."""
    if type(code) is not int or code != 1 or output.strip() or truncated:
        return False
    if any(char in command for char in ("\n", "\r", "`", "$")):
        return False
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        words = list(lexer)
    except ValueError:
        return False
    return bool(words and words[0] == "rg" and not any(
        word and all(char in "();<>|&" for char in word) for word in words[1:]))


def command_execution_state(
    name: str,
    arguments: dict[str, Any],
    data: dict[str, Any],
    payload: dict[str, Any],
    state: str,
    records: tuple[ExecutionRecord, ...],
) -> str:
    """A successful RPC is not proof that its external process has exited.

    Do not query processes or logs. Public session ids and provider call ids are
    deliberately not join keys: both can be reused by other execution owners.
    """
    if name not in PROCESS_TOOLS:
        return state
    for key in ("execution_id", "execution_handle", "session_id"):
        if key in data:
            payload[key] = data[key]
    if name == "write_stdin" and "session_id" not in payload and "session_id" in arguments:
        payload["session_id"] = arguments["session_id"]
    if state in {"pending", "unverified"}:
        return state

    code = payload.get("exit_code")
    terminal = data.get("terminal_status")
    terminal = terminal if isinstance(terminal, str) else ""
    if type(code) is int or terminal in {"completed", "failed", "timeout", "interrupted", "cancelled"}:
        failed = state == "failed" or (type(code) is int and code != 0) or terminal in {
            "failed", "timeout", "interrupted", "cancelled"}
        payload["execution_status"] = "terminal_failure" if failed else "terminal_success"
        return "failed" if failed else "completed"

    # wall_time_seconds in an acknowledgement measures the RPC wait, not the
    # process lifetime (including when a later canonical event confirms exit).
    payload.pop("duration_ms", None)
    raw_handle = data.get("execution_handle")
    handle = raw_handle if isinstance(raw_handle, dict) else {}
    execution_id = data.get("execution_id")
    if isinstance(execution_id, str) and execution_id:
        matches = [record for record in records if record.execution_id == execution_id]
    elif handle.get("backend") and handle.get("owner_id") and handle.get("process_id"):
        matches = [record for record in records if all(
            handle[key] == getattr(record.handle, key) for key in ("backend", "owner_id", "process_id"))]
    else:
        matches = []

    status = terminal if terminal in DISPLAY_STATES else "unknown"
    if len(matches) == 1:
        record = matches[0]
        # A supplied execution id cannot override a contradictory handle.
        if any(handle.get(key) and handle[key] != getattr(record.handle, key)
               for key in ("backend", "owner_id", "process_id")):
            status = "unknown"
            payload["execution_issue"] = "Execution identity mismatch; exit unconfirmed"
        else:
            status = record.status
            payload["execution_id"] = record.execution_id
            payload["execution_handle"] = record.handle.to_dict()
            payload["readback_status"] = record.readback_status
            if record.issue:
                payload["execution_issue"] = record.issue
    elif len(matches) > 1:
        status = "unknown"
        payload["execution_issue"] = "Execution identity is ambiguous; exit unconfirmed"

    payload["execution_status"] = status
    if payload.get("execution_issue"):
        issue = payload["execution_issue"]
        payload["metadata"]["Execution"] = (
            "Process owner unavailable; exit unconfirmed" if issue == "process_owner_lost" else issue)
    # An unsuccessful poll is still an unsuccessful tool request. It does not
    # establish that the external process failed, stopped or exited.
    return "failed" if state == "failed" else DISPLAY_STATES[status]
