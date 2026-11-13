from __future__ import annotations

from collections.abc import Callable
import time
from typing import Any
from uuid import uuid4

from ownpi.runtime.events import now_utc_iso
from ownpi.runtime.hooks.output_parser import parse_hook_output
from ownpi.runtime.hooks.registry import HookRegistration
from ownpi.runtime.hooks.types import HookRequest, HookRunReceipt


MCPHookExecutor = Callable[[str, str, dict[str, Any]], dict[str, Any] | str | None]


def run_mcp_handler(
    registration: HookRegistration,
    request: HookRequest,
    *,
    executor: MCPHookExecutor | None,
) -> HookRunReceipt:
    config = registration.config
    if config is None:
        raise ValueError("MCP Hook registration is missing config")
    started_at = now_utc_iso()
    started = time.monotonic()
    output: dict[str, Any] = {}
    schema_error = ""
    error = ""
    try:
        if executor is None:
            raise RuntimeError("mcp_tool Hook requires an MCP executor")
        arguments = _render_template(config.arguments, request=request)
        raw_output = executor(config.mcp_server, config.mcp_tool, arguments)
        if isinstance(raw_output, dict):
            output = dict(raw_output)
        elif isinstance(raw_output, str):
            parsed = parse_hook_output(raw_output, "", event_name=request.event_name)
            output = parsed.payload
            schema_error = parsed.schema_error
    except (KeyError, RuntimeError, TypeError, ValueError) as exc:
        error = str(exc)
    return HookRunReceipt(
        receipt_id=f"hook-receipt:{uuid4().hex}",
        event_id=request.event_id,
        event_name=request.event_name,
        handler_key=registration.handler_key,
        handler_name=registration.name,
        handler_type="mcp_tool",
        execution_mode="sync",
        source=registration.source,
        trust_status=registration.trust_status,
        started_at=started_at,
        finished_at=now_utc_iso(),
        duration_ms=int((time.monotonic() - started) * 1000),
        status="failed" if error or schema_error else "completed",
        error=error,
        output=output,
        schema_error=schema_error,
    )


def _render_template(value: Any, *, request: HookRequest) -> Any:
    if isinstance(value, dict):
        return {str(key): _render_template(item, request=request) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_template(item, request=request) for item in value]
    if not isinstance(value, str) or not (value.startswith("${") and value.endswith("}")):
        return value
    path = value[2:-1].strip()
    if not path:
        raise ValueError("empty MCP Hook template path")
    current: Any = {
        "event_name": request.event_name,
        "event_id": request.event_id,
        "payload": request.payload,
    }
    for segment in path.split("."):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
            continue
        if isinstance(current, list) and segment.isdigit() and int(segment) < len(current):
            current = current[int(segment)]
            continue
        raise KeyError(f"missing MCP Hook template path: {path}")
    return current
