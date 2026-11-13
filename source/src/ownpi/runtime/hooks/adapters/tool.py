from __future__ import annotations

from pathlib import Path
from typing import Any

from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.hooks.engine import HookEngine
from ownpi.runtime.hooks.types import (
    PostToolOutcome,
    PostToolUseRequest,
    PreToolOutcome,
    PreToolUseRequest,
)


async def run_pre_tool_hooks(
    engine: HookEngine | None,
    *,
    context: Any,
    definition: Any,
    input_payload: dict[str, Any],
    tool_call_id: str,
) -> PreToolOutcome:
    if engine is None:
        return PreToolOutcome(event_name="PreToolUse")
    outcome = await engine.run_async(
        PreToolUseRequest(
            payload=_tool_payload(
                context,
                definition,
                input_payload,
                tool_call_id=tool_call_id,
            )
        ),
        effective_permissions=_permissions(context),
    )
    if not isinstance(outcome, PreToolOutcome):
        raise TypeError("PreToolUse adapter received an incompatible outcome")
    return outcome


async def run_post_tool_hooks(
    engine: HookEngine | None,
    *,
    context: Any,
    definition: Any,
    input_payload: dict[str, Any],
    result: ToolResult,
    tool_call_id: str,
) -> PostToolOutcome:
    if engine is None or not result.ok:
        return PostToolOutcome(event_name="PostToolUse")
    payload = _tool_payload(
        context,
        definition,
        input_payload,
        tool_call_id=tool_call_id,
    )
    payload["result"] = result.to_dict()
    outcome = await engine.run_async(
        PostToolUseRequest(payload=payload),
        effective_permissions=_permissions(context),
    )
    if not isinstance(outcome, PostToolOutcome):
        raise TypeError("PostToolUse adapter received an incompatible outcome")
    return outcome


def _tool_payload(
    context: Any,
    definition: Any,
    input_payload: dict[str, Any],
    *,
    tool_call_id: str,
) -> dict[str, Any]:
    tool_name = str(getattr(definition, "name", "") or "")
    project_root = getattr(context, "project_root", None)
    run_dir = getattr(context, "run_dir", None)
    cwd = str(project_root or run_dir or Path.cwd())
    aliases = _tool_aliases(tool_name)
    return {
        "tool_name": tool_name,
        "claude_tool_name": aliases[0] if aliases else tool_name,
        "tool_name_aliases": aliases,
        "tool_input": dict(input_payload),
        "input": dict(input_payload),
        "cwd": cwd,
        "stage": getattr(getattr(context, "stage", None), "value", str(getattr(context, "stage", ""))),
        "attempt": getattr(context, "attempt", None),
        "run_id": getattr(context, "run_id", ""),
        "actor_kind": getattr(context, "actor_kind", ""),
        "actor_id": getattr(context, "actor_id", None),
        "tool_call_id": tool_call_id,
    }


def _tool_aliases(tool_name: str) -> tuple[str, ...]:
    mapping = {
        "exec_command": "Bash",
        "file_reader": "Read",
        "web_fetch": "WebFetch",
        "web_search": "WebSearch",
    }
    alias = mapping.get(tool_name)
    return tuple(dict.fromkeys(item for item in (alias, tool_name) if item))


def _permissions(context: Any) -> Any:
    return getattr(context, "effective_permissions", None) or getattr(context, "canonical_permissions", None)
