"""Writer-only confirmation of a business action's original model call."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def confirm_call_source(context: Any, tool_name: str) -> dict[str, str]:
    history = getattr(context, 'rollout_history', None)
    store = getattr(context.state_store, 'run_event_store', None)
    if history is None or store is None:
        raise OSError('Business persistence requires an active rollout writer')
    return history.confirmed_call_source(
        context.tool_call_id, run_dir=store.path.parent.parent, tool_name=tool_name,
    )


def validate_call_source(source: dict[str, str] | None) -> dict[str, str]:
    if not isinstance(source, dict) or set(source) != {'path', 'item_id'}:
        raise ValueError('Business source requires path and item_id')
    if any(not isinstance(value, str) or not value.strip() for value in source.values()):
        raise ValueError('Business source fields must be nonempty text')
    path = Path(source['path'])
    if path.is_absolute() or '..' in path.parts:
        raise ValueError('Business source must be relative to its run')
    return dict(source)
