"""Transient submit receipts, correlated to source-backed user item identities."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Submission:
    request_id: str
    run_id: str
    previous_ids: set[str]
    item_id: str = ""
    owned: bool = False
    owned_since: str = ""


@dataclass
class Submissions:
    entries: dict[str, Submission] = field(default_factory=dict)

    def begin(self, request_id: str, run_id: str, items: list[dict[str, Any]], *, owned: bool = False) -> None:
        # Bridge admits one submit at a time. Only user sources added to this
        # particular run can acknowledge it; equal text is never an identity.
        self.entries[request_id] = Submission(request_id, run_id, {
            item["item_id"] for item in items if item["kind"] == "user"
        }, owned=owned)

    def confirm_owner(self, request_id: str, started_at: str) -> None:
        if request_id in self.entries:
            self.entries[request_id].owned = True
            self.entries[request_id].owned_since = started_at

    def receipts(self, items: list[dict[str, Any]]) -> list[dict[str, str]]:
        receipts = []
        for entry in self.entries.values():
            if not entry.owned:
                continue
            if not entry.item_id:
                candidates = [item["item_id"] for item in items
                    if item["kind"] == "user"
                    and item["item_id"].startswith(entry.run_id + ":")
                    and item["item_id"] not in entry.previous_ids
                    and _during_owner(item, entry.owned_since)]
                # Ambiguous or unreadable evidence must retain the pending UI.
                if len(candidates) == 1:
                    entry.item_id = candidates[0]
            if entry.item_id:
                receipts.append({"request_id": entry.request_id,
                                 "run_id": entry.run_id, "item_id": entry.item_id})
        return receipts

    def reject(self, request_id: str) -> None:
        entry = self.entries.get(request_id)
        if entry and not entry.item_id:
            self.entries.pop(request_id)


def _during_owner(item: dict[str, Any], started_at: str) -> bool:
    if not started_at:
        return True  # New run created exclusively for this submit.
    try:
        return datetime.fromisoformat(item["timestamp"]) >= datetime.fromisoformat(started_at)
    except (KeyError, TypeError, ValueError):
        return False
