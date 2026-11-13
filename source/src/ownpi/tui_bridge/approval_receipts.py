"""Place backend-authored decisions beside verified original calls; no UI ledger."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ownpi.runtime.rollout.source_reader import read_original_call
from ownpi.tui_bridge.facts import approval_view


def bind_approval_receipts(
    items: list[dict[str, Any]], sessions: list[dict[str, Any]], run_root: Path, *,
    read_session: Any = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Wire IDs alone are insufficient: also bind actor and original exchange."""
    anchored: dict[str, list[dict[str, Any]]] = {}
    warnings: list[str] = []
    seen: set[str] = set()
    for session in sessions:
        if not isinstance(session, dict) or session.get("decision") not in {"approve", "deny", "deny_command"}:
            continue
        identity = session.get("session_id")
        request_id, tool = session.get("request_id"), session.get("tool_name")
        if not isinstance(identity, str) or not identity or identity in seen:
            continue
        seen.add(identity)
        candidates = []
        for item in items:
            if not request_id or not tool or item.get("tool_name") != tool or not item.get("source"):
                continue
            try:
                call = read_original_call(run_root, item["source"], run_id=run_root.name,
                                          tool_name=tool, read_session=read_session)
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if request_id not in {call.call.call_id, call.call.request_id}:
                continue
            meta = call.metadata
            if meta.get("actor_kind") != session.get("actor_kind", "core"):
                continue
            if session.get("actor_id") and session["actor_id"] != meta.get("actor_id"):
                continue
            if session.get("conversation_exchange_id") and session["conversation_exchange_id"] != call.entry.exchange_id:
                continue
            if session.get("output_sequence_index") is not None and session["output_sequence_index"] != call.entry.output_sequence_index:
                continue
            if session.get("stage") and meta.get("stage") and session["stage"] != meta["stage"]:
                continue
            if session.get("attempt") and meta.get("attempt") and session["attempt"] != meta["attempt"]:
                continue
            candidates.append((item, call))
        if len(candidates) != 1:
            warnings.append(f"Approval {identity}: original call binding is unavailable or ambiguous")
            continue
        item, call = candidates[0]
        try:
            arguments = call.arguments
        except (ValueError, TypeError):
            warnings.append(f"Approval {identity}: original call arguments are unreadable")
            continue
        view = approval_view({**session, "request_payload": {"input": arguments}})
        target = next((arguments[key] for key in
                       ("identifier", "doi", "url", "cmd", "command", "file_path", "path")
                       if isinstance(arguments.get(key), str) and arguments[key]), "")
        if not target and view["file_changes"]:
            target = ", ".join(change["path"] for change in view["file_changes"])
        target = target or tool
        actor = (item.get("scope") or {}).get("actor_label") or meta_label(call.metadata)
        scope = session.get("decision_scope") or ""
        qualifiers = []
        if actor:
            qualifiers.append(actor)
        if scope == "once":
            qualifiers.append("Once")
        elif scope == "session":
            qualifiers.append("This subagent run" if session.get("actor_kind") == "subagent" else "This run")
        else:
            qualifiers.append("Scope not recorded")
        receipt = {
            **{key: item[key] for key in ("scope", "audit_context", "agent_ref") if key in item},
            "schema_version": "ownpi.tui.item.v1", "visibility": "user",
            "item_id": f"{run_root.name}:approval:{identity}",
            "source": dict(item["source"]), "source_kind": "backend_reply",
            "kind": "permission_activity", "tool_name": tool, "status": "completed",
            "timestamp": session.get("answered_at") or "",
            "payload": {
                "interaction_id": identity,
                "lifecycle": "approved" if session["decision"] == "approve" else "denied",
                "decision": session["decision"], "decision_scope": scope,
                "display_group_id": identity,
                "subject": {"kind": "tool", "primary": target, "qualifiers": qualifiers},
            },
        }
        anchored.setdefault(item["item_id"], []).append(receipt)
    result = []
    for item in items:
        result.extend(anchored.get(item["item_id"], []))
        result.append(item)
    return result, warnings


def meta_label(metadata: dict[str, Any]) -> str:
    if metadata.get("actor_kind") == "subagent":
        return str(metadata.get("actor_label") or metadata.get("actor_id") or "Subagent")
    return str(metadata.get("actor_kind") or "").capitalize()
