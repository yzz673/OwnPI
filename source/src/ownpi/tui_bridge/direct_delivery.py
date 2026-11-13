"""Observed Direct reply/file receipts; never infer task success or read live files."""
from pathlib import Path
import re
from typing import Any


def recorded_citation_count(change: dict[str, Any]) -> int:
    """Inspect complete recorded prose, not today's file or a partial update hunk.

    Code-bearing documents are left unclassified rather than treating example
    syntax as a broken reference. This is a UI observation, not citation review.
    """
    content = change.get("content")
    if change.get("body_complete") is not True or not isinstance(content, str):
        return 0
    if "`" in content or "~~~" in content or re.search(r"(?m)^(?: {4}|\t)", content):
        return 0
    return len(re.findall(r"\ue200cite(?:\ue202[\w-]+)+\ue201", content))


def direct_reply_observation(items: list[dict[str, Any]], start: str, end: str) -> dict[str, Any]:
    if not start:
        return {}
    current = sorted((item for item in items
        if start <= item.get("timestamp", "") <= end
        and item.get("scope", {}).get("actor_kind") == "core"
        and not item.get("audit_context")), key=lambda item: item["timestamp"])
    messages = [item["payload"] for item in current
        if item["kind"] == "agent_message" and item["payload"].get("text", "").strip()
        and not item["payload"].get("detail_only") and item.get("visibility") != "audit"
        and not item["payload"].get("source_list")]
    final = any(message.get("phase") in {"final", "final_answer"} for message in messages)
    files: dict[str, dict[str, Any]] = {}
    for item in current:
        if item["kind"] != "file_change" or item.get("status") != "completed":
            continue
        payload = item["payload"]
        for change in payload.get("changes", []):
            path = change.get("path")
            if not isinstance(path, str) or not path.strip():
                continue
            absolute = Path(path)
            if not absolute.is_absolute():
                cwd = Path(payload.get("cwd") or "")
                if not cwd.is_absolute():
                    continue
                absolute = cwd / absolute
            key = str(absolute)
            if change.get("kind") == "delete":
                files.pop(key, None)
            elif change.get("kind") in {"add", "update"}:
                destination = change.get("move_path")
                if isinstance(destination, str) and destination.strip():
                    files.pop(key, None)
                    moved = Path(destination)
                    if not moved.is_absolute():
                        cwd = Path(payload.get("cwd") or "")
                        if not cwd.is_absolute():
                            continue
                        moved = cwd / moved
                    key, path = str(moved), destination
                try:
                    label = str(Path(key).relative_to(Path(payload.get("cwd") or "")))
                except ValueError:
                    label = path
                files[key] = {"path": key, "display_path": label}
                citation_count = recorded_citation_count(change)
                if citation_count:
                    files[key]["internal_citation_count"] = citation_count
    explicit_commentary = all(message.get("phase") == "commentary"
        and message.get("phase_recorded", True) for message in messages)
    observation = "final" if final else "none" if not messages else "commentary_only" if explicit_commentary else "unclassified"
    return {"reply_observation": observation,
            "recorded_files": list(files.values())}
