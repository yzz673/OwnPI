from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from ownpi.runtime.limits.projection import ENVIRONMENT_RECEIPT_PREVIEW_CHARS


_OMITTED_KEYS = frozenset(
    {
        "feedback_text",
        "raw_user_message",
        "research_state",
        "research_state_text",
        "task_spec",
        "task_spec_text",
        "tool_result_text",
        "conversation_history",
        "debug_trace",
        "history",
        "runtime_event_ledger",
        "runtime_projection_snapshot",
        "tool_call_history",
    }
)


def _receipt_value(value: Any, *, _ancestors: frozenset[int] = frozenset()) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _receipt_value(value.to_dict(), _ancestors=_ancestors)
    if is_dataclass(value):
        return _receipt_value(
            {item.name: getattr(value, item.name) for item in fields(value)},
            _ancestors=_ancestors,
        )
    if isinstance(value, Mapping):
        ancestors = _with_container(value, _ancestors)
        return {
            str(key): _receipt_value(item, _ancestors=ancestors)
            for key, item in value.items()
            if str(key) not in _OMITTED_KEYS
        }
    if isinstance(value, (list, tuple, set)):
        ancestors = _with_container(value, _ancestors)
        return [_receipt_value(item, _ancestors=ancestors) for item in value]
    return str(value)


def _with_container(value: Any, ancestors: frozenset[int]) -> frozenset[int]:
    identity = id(value)
    if identity in ancestors:
        raise ValueError("snapshot current state must not contain recursive containers")
    return ancestors | {identity}


def current_environment_receipt_view(
    payload: dict[str, Any],
) -> dict[str, Any]:
    view = {
        key: payload.get(key)
        for key in (
            "receipt_id",
            "provider_kind",
            "request_kind",
            "tool_name",
            "status",
            "selection_reason",
            "environment_affinity",
            "execution_class",
            "session_id",
            "started_at",
            "heartbeat_at",
            "completed_at",
            "elapsed_ms",
            "exit_code",
            "heartbeat_count",
            "stdout_truncated",
            "stderr_truncated",
            "timeout_class",
            "pty_mode",
            "pty_columns",
            "pty_rows",
            "network_mode",
            "environment_policy_hash",
            "authorization_evidence_only",
        )
    }
    for key in (
        "descriptor",
        "sandbox_policy",
        "writable_roots",
        "artifact_refs",
        "provider_metadata",
    ):
        view[key] = _receipt_value(payload.get(key))
    view["command_display"] = _bounded_text(payload.get("command_display"))
    view["stdout_preview"] = _bounded_text(payload.get("stdout_preview"))
    view["stderr_preview"] = _bounded_text(payload.get("stderr_preview"))
    return view


def _bounded_text(value: Any) -> str:
    return str(value or "")[:ENVIRONMENT_RECEIPT_PREVIEW_CHARS]


__all__ = [
    "current_environment_receipt_view",
]
