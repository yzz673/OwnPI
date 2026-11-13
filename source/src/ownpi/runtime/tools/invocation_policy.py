from __future__ import annotations


SIDE_CHANNEL_CONTROL_TOOL_NAMES = frozenset({'permission_request'})


def is_side_channel_control_tool_name(tool_name: str) -> bool:
    return str(tool_name or '').strip().casefold() in SIDE_CHANNEL_CONTROL_TOOL_NAMES


__all__ = ['SIDE_CHANNEL_CONTROL_TOOL_NAMES', 'is_side_channel_control_tool_name']
