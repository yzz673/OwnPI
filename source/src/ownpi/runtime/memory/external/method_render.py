from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ownpi.runtime.limits.projection import (
    MAX_EXTERNAL_KNOWLEDGE_RESULT_BODY_CHARS,
)


def render_payload(payload: Mapping[str, Any]) -> tuple[str, bool]:
    body = json.dumps(
        _strip_locators(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(body) <= MAX_EXTERNAL_KNOWLEDGE_RESULT_BODY_CHARS:
        return body, False
    return body[: MAX_EXTERNAL_KNOWLEDGE_RESULT_BODY_CHARS - 3].rstrip() + "...", True


def _strip_locators(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _strip_locators(item)
            for key, item in value.items()
            if key not in {"path", "resource_path", "source_pdf_name"}
            and not key.endswith(("_path", "_paths", "_dir", "_dirs"))
        }
    if isinstance(value, list):
        return [_strip_locators(item) for item in value]
    return value
