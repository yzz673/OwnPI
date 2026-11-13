from __future__ import annotations

from typing import Any

from ownpi.runtime.contracts import ToolResult


_REMOTE_STATE_ESTABLISHING_TOOLS = frozenset({'remote_check', 'remote_run'})


def project_remote_workflow_result(
    *,
    remote_state: Any,
    tool_name: str,
    input_payload: dict[str, Any],
    tool_result: ToolResult,
) -> bool:
    """Project successful remote bootstrap facts into the canonical runtime state."""

    if tool_name not in _REMOTE_STATE_ESTABLISHING_TOOLS or not tool_result.ok:
        return False
    data = tool_result.data if isinstance(tool_result.data, dict) else {}
    metadata = tool_result.metadata if isinstance(tool_result.metadata, dict) else {}
    profile_payload = data.get('profile')
    profile = _nonempty_text(input_payload.get('profile'))
    if isinstance(profile_payload, dict):
        profile = _nonempty_text(profile_payload.get('name')) or profile
    else:
        profile = _nonempty_text(profile_payload) or profile
    if not profile:
        return False

    remote_state.active_remote_profile = profile
    host = _profile_value(profile_payload, 'host')
    if host:
        remote_state.active_remote_host = host
    remote_root = (
        _profile_value(profile_payload, 'remote_root')
        or _nonempty_text(data.get('remote_root'))
        or _nonempty_text(metadata.get('remote_root'))
    )
    if remote_root:
        remote_state.remote_workdir = remote_root

    remote_state.remote_status_summary = _result_summary(tool_result)
    return True


def _profile_value(profile_payload: Any, key: str) -> str:
    if not isinstance(profile_payload, dict):
        return ''
    return _nonempty_text(profile_payload.get(key))


def _nonempty_text(value: Any) -> str:
    return str(value or '').strip()


def _result_summary(tool_result: ToolResult) -> str:
    return _nonempty_text(tool_result.stdout) or _nonempty_text(tool_result.tool_name)


__all__ = ['project_remote_workflow_result']
