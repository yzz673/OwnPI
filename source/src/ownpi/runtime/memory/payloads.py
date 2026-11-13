from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def serialized_payload_size_bytes(payload: Mapping[str, Any]) -> int:
    return len(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def drop_last_list_item(
    payload: dict[str, Any],
    paths: tuple[tuple[str, ...], ...],
) -> bool:
    for path in paths:
        current: Any = payload
        for segment in path[:-1]:
            current = current.get(segment) if isinstance(current, Mapping) else None
        if isinstance(current, dict):
            items = current.get(path[-1])
            if isinstance(items, list) and items:
                current[path[-1]] = items[:-1]
                return True
    return False


def truncate_utf8(value: str, max_bytes: int) -> tuple[str, bool]:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
        raise ValueError("max_bytes must be a non-negative integer")
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True
