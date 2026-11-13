"""Read-only subagent display facts; execution and recovery stay with Runtime."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ownpi.runtime.rollout.child_projection import project_children
from ownpi.runtime.rollout.source_reader import read_original_call


def agent_ref(run_id: str, conversation_id: str, metadata: dict[str, Any]) -> dict[str, str]:
    return {"run_id": run_id, "conversation_id": conversation_id,
            "actor_id": str(metadata.get("actor_id") or ""),
            "actor_kind": str(metadata.get("actor_kind") or ""),
            "parent_conversation_id": str(metadata.get("parent_id") or "")}


def project_activity(items: list[dict[str, Any]], reference: dict[str, Any]) -> dict[str, str]:
    """Last visible action from one verified conversation, never a wire-id match."""
    for item in reversed(items):
        owner = item.get("agent_ref") or {}
        if not all(reference.get(key) and owner.get(key) == reference[key]
                   for key in ("run_id", "conversation_id", "actor_id")):
            continue
        kind, name, action = item.get("kind"), item.get("tool_name", ""), item.get("action", "")
        if kind in {"reasoning", "model_activity", "heartbeat"} or name == "complete_subagent":
            continue
        payload = item.get("payload") or {}
        if payload.get("source_list") is True:
            continue
        label, detail = "", ""
        if kind == "command_execution":
            label = {"read": "Read", "search": "Search", "list": "List", "run": "Bash",
                     "resolve_citation": "Resolve", "fetch_paper": "Fetch"}.get(action, name or "Tool")
            detail = payload.get("display_target") or payload.get("target") or payload.get("requested_identifier") or payload.get("requested_title") or payload.get("command", "")
            if payload.get("query"):
                detail = f"{detail} · {payload['query']}" if detail else payload["query"]
        elif kind == "web_search":
            label, detail = "Web", payload.get("query") or payload.get("web_action", {}).get("url", "")
        elif kind == "file_change":
            label, detail = "Changed", ", ".join(str(value.get("path") or "") for value in payload.get("changes", []))
        elif kind == "mcp_tool_call":
            label, detail = "Tool", payload.get("tool") or name
        elif kind == "agent_message":
            if not str(payload.get("text") or "").strip():
                continue
            label, detail = "Message", payload.get("text", "")
        elif name == "run_subagents":
            label, detail = "Delegate", "Subagent tasks"
        if label:
            return {"item_id": item["item_id"], "kind": str(kind), "label": str(label),
                    "detail": " ".join(str(detail).split()), "status": str(item.get("status") or ""),
                    "timestamp": str(item.get("timestamp") or "")}
    return {}


def project_subagents(run_root: Path, events: tuple, reader: Any,
                      contexts: dict[str, dict[str, Any]], relationships: Any,
                      items: list[dict[str, Any]], warnings: list[str]) -> list[dict[str, Any]]:
    """Use backend projections per child so one bad source does not hide siblings."""
    batches = []
    for item in items:
        if item.get("tool_name") != "run_subagents":
            continue
        source = item.get("source")
        if not source:
            continue
        batch = {"item_id": item["item_id"], "source": source, "run_id": run_root.name,
                 "parent_ref": item.get("agent_ref"), "audit_context": item.get("audit_context"),
                 "stage": (item.get("audit_context") or {}).get("stage") or item.get("scope", {}).get("stage") or "", "tasks": [], "error": ""}
        try:
            call = read_original_call(run_root, source, run_id=str(item.get("scope", {}).get("run_id") or run_root.name),
                                      tool_name="run_subagents", read_session=reader)
            tasks = call.arguments.get("tasks", [])
            if not isinstance(tasks, list):
                raise ValueError("Subagent tasks are not a list")
        except (OSError, ValueError, KeyError, TypeError) as error:
            batch["error"] = str(error)
            warnings.append(f"Subagents {item['item_id']}: {error}")
            batches.append(batch)
            continue
        for index, task in enumerate(tasks):
            if not isinstance(task, dict):
                continue
            admissions = [event.data.to_object() for event in events
                          if event.kind == "child_admitted"
                          and event.data.to_object().get("source") == source
                          and event.data.to_object().get("task_index") == index]
            ids = {value.get("child_id") for value in admissions}
            child_id = next(iter(ids)) if len(ids) == 1 else ""
            row = {"task_index": index, "task_name": str(task.get("task_name") or "Unnamed task"),
                   "task_message": str(task.get("message") or ""), "child_id": child_id or "",
                   "agent_ref": None, "parent_ref": batch["parent_ref"], "run_id": run_root.name,
                   "stage": batch["stage"], "audit_context": batch["audit_context"],
                   "status": "created" if child_id else "pending", "terminal_status": "",
                   "completion_committed": False, "completion_content": "", "reclaimed": False,
                   "error": "", "source": source, "item_id": item["item_id"]}
            if len(ids) > 1:
                row["error"] = "Multiple children admitted for one task"
                row["status"] = "unconfirmed"
                warnings.append(f"Subagents {item['item_id']} task {index}: {row['error']}")
            if child_id:
                try:
                    projected = project_children(events, run_root=run_root, run_id=str(call.metadata["run_id"]),
                        source=source, selected_child_id=child_id, read_session=reader)
                    state = next(value for value in projected.states if value.child_id == child_id)
                    row.update(status=state.status, terminal_status=state.terminal_status,
                        completion_committed=state.completion_committed,
                        completion_content=state.completion_content if state.completion_committed else "",
                        reclaimed=state.status == "reclaimed", error=state.error or "")
                    matches = [relation for relation in relationships.relations
                               if relation.actor_id == child_id and relation.parent_id == call.metadata["id"]]
                    if len(matches) == 1 and not matches[0].issues:
                        relation = matches[0]
                        row["agent_ref"] = contexts[relation.conversation_id]["agent_ref"]
                        row["activity"] = project_activity(items, row["agent_ref"])
                    elif matches:
                        raise ValueError("Child conversation has conflicting parent identity")
                except (OSError, ValueError, KeyError, TypeError, StopIteration) as error:
                    row["error"] = str(error)
                    row["status"] = "unconfirmed"
                    warnings.append(f"Subagent {child_id}: {error}")
            batch["tasks"].append(row)
        batches.append(batch)
    return batches


def enrich_batches(batches: list[dict[str, Any]], pending: list[dict[str, Any]],
                   run_active: bool, run_status: str) -> list[dict[str, Any]]:
    """Transient request/owner overlay; never changes a recorded child outcome."""
    result = []
    for batch in batches:
        rows = []
        for task in batch["tasks"]:
            requests = [value for value in pending if value.get("actor_kind") == "subagent"
                        and value.get("actor_id") == task["child_id"]
                        and (not value.get("run_id") or value["run_id"] == task.get("run_id"))]
            row = {**task, "request_ids": [value["session_id"] for value in requests]}
            if requests:
                row["display_status"] = "Needs approval" if any(v["kind"] == "permission" for v in requests) else "Needs input"
            elif row["completion_committed"]:
                row["display_status"] = "Feedback submitted"
            elif row["terminal_status"] in {"failed", "cancelled"}:
                row["display_status"] = row["terminal_status"].capitalize()
            elif row["terminal_status"] == "completed":
                row["display_status"] = "Execution completed"
            elif not run_active and row["status"] in {"created", "running", "reclaimed"}:
                row["display_status"] = "State unconfirmed"
                row["last_recorded_status"] = row["status"]
            else:
                row["display_status"] = {"pending": "Pending", "created": "Queued", "running": "Running",
                    "waiting": "Waiting", "unconfirmed": "State unconfirmed"}.get(row["status"], row["status"].capitalize())
            row["run_status"] = run_status
            rows.append(row)
        result.append({**batch, "tasks": rows})
    return result
