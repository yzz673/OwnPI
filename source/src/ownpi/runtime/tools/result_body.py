from __future__ import annotations

import json
from typing import Any

from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.tools.projection_errors import ToolResultProjectionError
from ownpi.runtime.tools.result_serialization import serialization_failure_text


def canonical_ordinary_result_body(tool_result: ToolResult) -> str:
    return ordinary_body_with_diagnostic(tool_result)[0]


def ordinary_body_with_diagnostic(tool_result: ToolResult) -> tuple[str, str]:
    try:
        return _serialize_ordinary_result(tool_result), ""
    except ToolResultProjectionError:
        raise
    except (TypeError, ValueError, RecursionError) as error:
        return (
            serialization_failure_text(error, tool_name=tool_result.tool_name),
            f"serialization_failure:{type(error).__name__}",
        )


def _serialize_ordinary_result(tool_result: ToolResult) -> str:
    payload: dict[str, Any] = {"ok": bool(tool_result.ok)}
    if tool_result.data:
        payload["data"] = tool_result.data
    if tool_result.stdout:
        payload["stdout"] = tool_result.stdout
    if tool_result.stderr:
        payload["stderr"] = tool_result.stderr
    if tool_result.exit_code is not None:
        payload["exit_code"] = int(tool_result.exit_code)
    if tool_result.artifacts:
        payload["artifacts"] = [
            artifact.to_dict() for artifact in tool_result.artifacts
        ]
    warnings = [str(item) for item in tool_result.warnings if str(item).strip()]
    if warnings:
        payload["warnings"] = warnings
    if tool_result.error:
        payload["error"] = tool_result.error
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )
