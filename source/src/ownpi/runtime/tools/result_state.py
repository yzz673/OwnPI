from __future__ import annotations

from typing import Any

from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.limits.projection import (
    MAX_CURRENT_TOOL_RESULT_ARTIFACTS,
    MAX_CURRENT_TOOL_RESULT_SUMMARY_CHARS,
    MAX_CURRENT_TOOL_RESULT_WARNINGS,
)


_MECHANICAL_METADATA_KEYS = frozenset(
    {
        "answer_artifact_path",
        "attempt",
        "available_to_continuation_index",
        "consumed_at",
        "consumed_by_continuation_index",
        "consumption_status",
        "continuation_index",
        "environment_provider_kind",
        "environment_receipt_id",
        "environment_selection_reason",
        "interaction_request_id",
        "kind",
        "observation_id",
        "origin_kind",
        "query_chain_depth",
        "query_chain_id",
        "raw_result_ref",
        "request_id",
        "result_id",
        "same_boundary",
        "session_id",
        "stage",
        "transcript_storage",
    }
)
_MECHANICAL_DATA_KEYS = frozenset(
    {
        "consumed_at",
        "consumed_by_continuation_index",
        "consumption_status",
        "interaction_request_id",
        "kind",
        "origin_kind",
        "request_id",
        "result_id",
        "session_id",
    }
)


def current_tool_result_projection(
    tool_result: ToolResult,
    *,
    summary: str,
) -> ToolResult:
    metadata = {
        key: _bounded_scalar(value)
        for key, value in mechanical_result_metadata(tool_result.metadata).items()
    }
    omitted_warning_count = max(
        0,
        len(tool_result.warnings) - MAX_CURRENT_TOOL_RESULT_WARNINGS,
    )
    omitted_artifact_count = max(
        0,
        len(tool_result.artifacts) - MAX_CURRENT_TOOL_RESULT_ARTIFACTS,
    )
    metadata["omitted_warning_count"] = omitted_warning_count
    metadata["omitted_artifact_count"] = omitted_artifact_count
    metadata["projection_truncated"] = bool(
        omitted_warning_count or omitted_artifact_count
    )
    data = {
        key: _bounded_scalar(value)
        for key, value in (tool_result.data or {}).items()
        if key in _MECHANICAL_DATA_KEYS and _is_scalar(value)
    }
    return ToolResult(
        ok=tool_result.ok,
        tool_name=tool_result.tool_name,
        data=data or None,
        stdout=_bounded_text(summary),
        stderr=_bounded_text(tool_result.stderr),
        exit_code=tool_result.exit_code,
        artifacts=list(tool_result.artifacts[:MAX_CURRENT_TOOL_RESULT_ARTIFACTS]),
        metadata=metadata,
        warnings=[
            _bounded_text(item) or ""
            for item in tool_result.warnings[:MAX_CURRENT_TOOL_RESULT_WARNINGS]
            if str(item).strip()
        ],
        error=_bounded_text(tool_result.error),
        duration=tool_result.duration,
    )


def _bounded_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)[:MAX_CURRENT_TOOL_RESULT_SUMMARY_CHARS]


def mechanical_result_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Select identity/scope fields without copying arbitrary result bodies."""
    selected = {
        key: value
        for key, value in metadata.items()
        if key in _MECHANICAL_METADATA_KEYS and _is_scalar(value)
    }
    reference = metadata.get("environment_receipt_ref")
    if isinstance(reference, dict):
        selected["environment_receipt_ref"] = dict(reference)
    return selected


def _bounded_scalar(value: Any) -> Any:
    return (
        value
        if not isinstance(value, str)
        else value[:MAX_CURRENT_TOOL_RESULT_SUMMARY_CHARS]
    )


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (bool, int, float, str))


__all__ = [
    "current_tool_result_projection",
]
