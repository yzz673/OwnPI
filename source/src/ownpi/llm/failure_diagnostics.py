from __future__ import annotations

import re
from typing import Any


_SECRET = re.compile(
    r"""(?i)(["']?(?:authorization|api[_-]?key|access[_-]?token|password|secret)["']?\s*[:=]\s*["']?(?:bearer\s+)?)([^\s,"';}]+)"""
)
_KEY = re.compile(r"\b(?:sk|sess|key)-[A-Za-z0-9_-]{12,}\b")


def diagnostic_message(value: Any, limit: int = 400) -> str:
    text = _SECRET.sub(r"\1[REDACTED]", str(value or ""))
    return _KEY.sub("[REDACTED]", text)[:limit]


def sanitize_error_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: diagnostic_message(value)
        if isinstance(value, str)
        and (
            "error" in key
            or key in {"transport_reason", "transport_exception_message", "message"}
        )
        else value
        for key, value in metadata.items()
    }
