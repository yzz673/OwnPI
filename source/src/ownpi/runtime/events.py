from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class HistoryEvent:
    event_type: str
    phase: str
    status: str
    timestamp: str
    round_index: int | None = None
    agent_id: str | None = None
    role: str | None = None
    artifact_type: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    file_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
