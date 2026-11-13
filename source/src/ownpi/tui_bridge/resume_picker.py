"""Read-only run picker over the same Runtime service used by the CLI."""
from datetime import datetime, timezone
from pathlib import Path
import shlex
from typing import Any

from ownpi.tui_bridge.lifecycle import run_capabilities


def timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        # Do not guess a timezone for historical, ambiguous timestamps.
        return parsed if parsed.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def time_labels(value: Any, now: datetime) -> tuple[int, str, str]:
    parsed = timestamp(value)
    if parsed is None:
        return 0, "Unknown", "Unknown"
    seconds = int((now - parsed).total_seconds())
    if seconds < 0:
        relative = "Future"
    elif seconds < 60:
        relative = "Just now"
    elif seconds < 3600:
        relative = f"{seconds // 60}m ago"
    elif seconds < 86400:
        relative = f"{seconds // 3600}h ago"
    else:
        relative = f"{seconds // 86400}d ago"
    return int(parsed.timestamp()), relative, parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


def resume_picker(bridge: Any, *, now: datetime | None = None) -> dict[str, Any]:
    from ownpi.tui_bridge.panels import row, selection

    now = now or datetime.now(timezone.utc)
    service = bridge.service()
    sessions = {item["session_id"]: item for item in service.list()}
    rows = []
    for run in service.list_runs():
        session = sessions.get(run["session_id"], {})
        path = Path(run["run_dir"])
        capability = run_capabilities(path)
        try:
            status = service.status(path).payload
        except (OSError, ValueError, KeyError) as error:
            status = {"status": "unavailable"}
            capability = {"available": False, "reason": f"Cannot inspect run: {error}"}
        updated = time_labels(status.get("last_recorded_at") or run.get("updated_at"), now)
        created = time_labels(run.get("created_at"), now)
        project = str(session.get("project_root") or "")
        active = bool(status.get("owner_active"))
        ready = bool(capability["available"]) and not active
        label = str(status["status"])
        if active:
            action, hint = "inspect_run", "Running elsewhere; open history without starting a second owner."
        elif ready:
            action = "resume"
            hint = ("Continue conversation; no model call until your next message."
                    if capability.get("can_submit") else "Resume unfinished work through the CLI's Runtime recovery service.")
        else:
            action, hint = "run_actions", capability.get("reason") or "History only."
        entry = row(session.get("title") or run["run_id"], shlex.join([f"/{action}", run["run_id"]]),
                    navigation="push" if action == "run_actions" else "leave")
        entry.update({
            "id": run["run_id"],
            "selected_description": f"Updated {updated[2]}\nCreated {created[2]}\n{project}\n{run['run_id']}\n{hint}",
            "resume_entry": {
                "updated": updated[0], "created": created[0],
                "updated_label": updated[1], "created_label": created[1],
                "status": label, "ready": ready,
                "same_project": bool(project) and Path(project).expanduser().resolve() == bridge.project_root.resolve(),
                "project": project, "hint": hint,
                "details_action": row("Details", shlex.join(["/run_actions", run["run_id"]]))["action"],
            },
        })
        rows.append(entry)
    rows.sort(key=lambda r: (-r["resume_entry"]["updated"], r["id"]))
    return {**selection("Resume a previous session", rows, view_id="resume"), "resume_picker": True,
            "footer_hint": "↑↓ select · Enter continue · Ctrl+O details · Esc back"}
