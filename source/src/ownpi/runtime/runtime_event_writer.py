from __future__ import annotations

from typing import Any

def append_event_payload(event_list: Any, payload: dict[str, Any]) -> None:
    if isinstance(event_list, list):
        event_list.append(dict(payload))
