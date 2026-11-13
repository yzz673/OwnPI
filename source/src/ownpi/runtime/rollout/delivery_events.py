"""Compatibility entry point for business result delivery.

Interaction answers and child completions are already durable facts, while the
actual tool result is part of A-class history. They do not need a second
delivery record in the business event stream.
"""

from __future__ import annotations

from typing import Any


def record_result_delivery(context: Any, history: Any, entry: Any, call: Any) -> None:
    return
