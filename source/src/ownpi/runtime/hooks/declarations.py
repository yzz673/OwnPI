from __future__ import annotations

from ownpi.runtime.hooks.types import HookEventName


PUBLIC_HOOK_EVENTS: tuple[HookEventName, ...] = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PermissionRequest",
    "PreCompact",
    "PostCompact",
    "Stop",
    "SessionEnd",
    "Elicitation",
)

CONTROL_EVENTS: frozenset[HookEventName] = frozenset(
    {"UserPromptSubmit", "PreToolUse", "PermissionRequest", "PreCompact", "Stop", "Elicitation"}
)


def validate_event_name(value: str) -> HookEventName:
    if value not in PUBLIC_HOOK_EVENTS:
        raise ValueError(f"unsupported public Hook event: {value}")
    return value  # type: ignore[return-value]
