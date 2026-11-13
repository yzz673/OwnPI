from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

from ownpi.runtime.hooks.types import HookEventName


_COMMON_FIELDS = frozenset({"systemMessage", "additionalContext", "hookSpecificOutput"})
_EVENT_FIELDS: dict[HookEventName, frozenset[str]] = {
    "SessionStart": _COMMON_FIELDS,
    "UserPromptSubmit": _COMMON_FIELDS | {"decision", "continue", "reason"},
    "PreToolUse": _COMMON_FIELDS | {"decision", "continue", "reason", "updatedInput"},
    "PostToolUse": _COMMON_FIELDS | {"decision", "reason", "updatedMCPToolOutput"},
    "PermissionRequest": _COMMON_FIELDS | {"decision", "reason"},
    "PreCompact": _COMMON_FIELDS | {"decision", "continue", "reason"},
    "PostCompact": _COMMON_FIELDS,
    "Stop": _COMMON_FIELDS | {"decision", "continue", "reason"},
    "SessionEnd": _COMMON_FIELDS,
    "Elicitation": _COMMON_FIELDS | {"action", "content", "reason"},
}


@dataclass(frozen=True)
class ParsedHookOutput:
    payload: dict[str, Any] = field(default_factory=dict)
    schema_error: str = ""


def parse_hook_output(stdout: str, stderr: str, *, event_name: HookEventName) -> ParsedHookOutput:
    for stream in (stdout, stderr):
        text = stream.strip()
        if not text:
            continue
        candidates = [text, *[line.strip() for line in text.splitlines() if line.strip()]]
        for candidate in reversed(candidates):
            if not (candidate.startswith("{") and candidate.endswith("}")):
                continue
            try:
                value = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                payload = {str(key): item for key, item in value.items()}
                error = _validate_payload(payload, event_name=event_name)
                return ParsedHookOutput(payload={} if error else payload, schema_error=error)
    if stdout.strip() and event_name in {"SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"}:
        return ParsedHookOutput(payload={"systemMessage": stdout.strip()})
    return ParsedHookOutput()


def normalized_event_output(payload: dict[str, Any], *, event_name: HookEventName) -> dict[str, Any]:
    result = {key: value for key, value in payload.items() if key != "hookSpecificOutput"}
    specific = payload.get("hookSpecificOutput")
    if not isinstance(specific, dict):
        return result
    declared_event = str(specific.get("hookEventName", "") or "").strip()
    if declared_event and declared_event != event_name:
        return result
    result.update({key: value for key, value in specific.items() if key != "hookEventName"})
    return result


def _validate_payload(payload: dict[str, Any], *, event_name: HookEventName) -> str:
    allowed = _EVENT_FIELDS[event_name]
    unknown = sorted(set(payload) - allowed)
    if unknown:
        return f"unsupported {event_name} Hook output field: {unknown[0]}"
    specific = payload.get("hookSpecificOutput")
    if specific is not None:
        if not isinstance(specific, dict):
            return "hookSpecificOutput must be an object"
        declared_event = specific.get("hookEventName")
        if declared_event is not None and not isinstance(declared_event, str):
            return "hookSpecificOutput.hookEventName must be a string"
        nested_unknown = sorted(set(specific) - (allowed | {"hookEventName"}))
        if nested_unknown:
            return f"unsupported {event_name} hookSpecificOutput field: {nested_unknown[0]}"
    normalized = normalized_event_output(payload, event_name=event_name)
    validators = {
        "systemMessage": lambda value: isinstance(value, str),
        "additionalContext": lambda value: isinstance(value, str)
        or (isinstance(value, list) and all(isinstance(item, str) for item in value)),
        "decision": lambda value: isinstance(value, str) or isinstance(value, dict),
        "continue": lambda value: isinstance(value, bool),
        "reason": lambda value: isinstance(value, str),
        "updatedInput": lambda value: isinstance(value, dict),
        "updatedMCPToolOutput": lambda value: isinstance(value, dict),
        "action": lambda value: isinstance(value, str),
        "content": lambda value: isinstance(value, dict),
    }
    for key, value in normalized.items():
        validator = validators.get(key)
        if validator is not None and not validator(value):
            return f"invalid {event_name} Hook output field {key}"
    return ""
