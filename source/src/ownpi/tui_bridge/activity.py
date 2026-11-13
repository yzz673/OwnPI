"""Presentation lifecycle from bound reviewer turns and canonical stage events."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ReviewTurns:
    latest: dict[str, dict[str, Any]] = field(default_factory=dict)

    def observe(
        self, raw: dict[str, Any], context: dict[str, Any], source: dict[str, Any]
    ) -> None:
        # Children inherit audit display ownership, but cannot end their parent review.
        if context.get("scope", {}).get("actor_kind") != "reviewer":
            return
        audit_id = context.get("audit_context", {}).get("audit_id")
        payload = raw.get("payload", {})
        kind = payload.get("type")
        if (
            not audit_id
            or raw.get("type") != "event_msg"
            or kind not in {"turn_started", "turn_aborted"}
        ):
            return
        timestamp = raw["timestamp"]
        previous = self.latest.get(audit_id)
        if previous and previous["timestamp"] > timestamp:
            return
        if (
            kind == "turn_aborted"
            and previous
            and previous["status"] == "running"
            and previous["source"].get("turn_id")
            and payload.get("turn_id")
            and previous["source"]["turn_id"] != payload["turn_id"]
        ):
            return  # A late abort from an older turn cannot end its resumed successor.
        status = (
            "running"
            if kind == "turn_started"
            else ("failed" if payload.get("status") == "failed" else "interrupted")
        )
        self.latest[audit_id] = {
            "status": status,
            "timestamp": timestamp,
            "source": {**source, "turn_id": payload.get("turn_id"), "event_type": kind},
        }

    def terminal(self, audit_id: str) -> dict[str, Any] | None:
        current = self.latest.get(audit_id)
        return current if current and current["status"] != "running" else None


def current_activity(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Consume typed control items, never assistant text or late child messages."""
    for item in reversed(items):
        payload = item["payload"]
        kind = item["kind"]
        if kind == "stage":
            role, phase, stage, round_index = (
                "core",
                payload.get("event", ""),
                payload.get("stage", ""),
                0,
            )
        elif kind == "review_cycle":
            role, phase, stage, round_index = (
                "reviewer",
                payload.get("phase", ""),
                payload.get("stage", ""),
                payload.get("round", 0),
            )
        elif kind == "review" and payload.get("phase") == "applying_feedback":
            role, phase, stage, round_index = (
                "core",
                "applying_feedback",
                payload.get("stage", ""),
                payload.get("round", 0),
            )
        else:
            continue
        return {
            "role": role,
            "phase": phase,
            "stage": stage,
            "attempt": payload.get("attempt", 0),
            "round": round_index,
            "observed_at": item["timestamp"],
            "source_item_id": item["item_id"],
        }
    return {}
