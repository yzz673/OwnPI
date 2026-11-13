"""Explain the current backend's reusable approval identity; never grant access."""
from __future__ import annotations

import json
from typing import Any

from ownpi.runtime.permissions.session_identity import read_session_components, read_session_identity


def approval_reuse_scope(item: dict[str, Any]) -> str:
    request = item.get('approval_request')
    if not isinstance(request, dict):
        return ''
    overlay = request.get('requested_overlay')
    if not isinstance(overlay, dict):
        return ''
    tool = str(item.get('tool_name') or request.get('tool_name') or '')
    identity = read_session_identity(request)
    kind = identity['session_match_kind']
    prefix = request.get('command_prefix')
    prefix = prefix if isinstance(prefix, list) and all(isinstance(p, str) and p.strip() for p in prefix) else []
    entries = overlay.get('file_system_entries') or []
    typed = tool in {'exec_command', 'patch_apply', 'mcp_tool_invoke'} or tool.startswith('mcp__')
    if typed:
        if tool == 'exec_command' and kind == 'exec_command_exact' and not prefix:
            match = 'Same command and execution settings'
        elif tool == 'exec_command' and kind == 'exec_command_prefix' and prefix:
            match = 'Command prefix: ' + json.dumps(prefix, ensure_ascii=False)
        elif tool == 'patch_apply' and kind == 'patch_path_exact' and read_session_components(request):
            match = 'These exact file paths and rename destinations; patch content may change'
        elif (tool == 'mcp_tool_invoke' or tool.startswith('mcp__')) and kind == 'mcp_remote_tool':
            match = 'Same remote MCP server and tool; arguments may change'
        else:
            # Current backend accepts once but stores no reusable grant here.
            return ''
    else:
        if not request.get('normalized_input_hash') or kind:
            return ''
        if prefix:
            match = 'Command prefix: ' + json.dumps(prefix, ensure_ascii=False)
        elif entries and overlay.get('network_enabled') is not True:
            match = 'Requests within access scope'
        else:
            match = 'Exact input'
    scope = 'This subagent run' if item.get('actor_kind') == 'subagent' else 'This run'
    lines = [f'Scope: {scope}', f'Match: {match} · same tool/action']
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get('path'), dict):
            continue
        path = entry['path']
        target = str(path.get('value') or '')
        if path.get('subpath'):
            target += '/' + str(path['subpath'])
        suffix = ' · if present' if entry.get('skip_if_missing') is True else ''
        lines.append(f"Access: {entry.get('access', '')}: {target} ({path.get('kind', '')}){suffix}")
    if overlay.get('network_enabled') is True:
        lines.append('Access: Network enabled')
    if not entries and overlay.get('network_enabled') is not True:
        lines.append('Access: Current sandbox only')
    lines.append('Limits: Unchanged environment, policy and requested access')
    return '\n'.join(lines)
