"""Secret-conscious matching identity for supported remote MCP tool calls."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping
from urllib.parse import urlsplit

MCP_MATCH_KIND = 'mcp_remote_tool'
MCP_MATCH_VERSION = 1


def build_mcp_match(
    *,
    tool_name: str,
    input_payload: Mapping[str, Any],
    effects: Mapping[str, Any],
) -> tuple[str, int, str] | None:
    if tool_name != 'mcp_tool_invoke' and not tool_name.startswith('mcp__'):
        return None
    if effects.get('process_required') is True:
        # stdio/header-helper argv may contain credentials or unstable command data.
        return None
    if effects.get('network_required') is not True or effects.get('remote_required') is not True:
        return None
    server_identity = effects.get('session_identity')
    if (
        not isinstance(server_identity, str) or len(server_identity) != 64
        or any(char not in '0123456789abcdef' for char in server_identity)
    ):
        return None
    endpoints = effects.get('network_endpoints')
    if not isinstance(endpoints, (list, tuple)) or len(endpoints) != 1:
        return None
    endpoint = str(endpoints[0] or '').strip()
    try:
        parsed = urlsplit(endpoint)
        parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme not in {'http', 'https'}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return None
    server_name = str(input_payload.get('server_name', '') or '').strip()
    invoked_tool = str(input_payload.get('tool_name', '') or '').strip()
    if tool_name == 'mcp_tool_invoke' and not invoked_tool:
        return None
    payload = {
        'schema': 'ownpi.mcp.session_match.v1',
        'tool_identity': tool_name,
        'server_name': server_name,
        'mcp_tool_name': invoked_tool,
        'endpoint': f'{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path or "/"}',
        'remote_target': str(effects.get('remote_target', '') or ''),
        'transport': 'remote_http',
        'server_identity': server_identity,
        'effects': dict(effects),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return MCP_MATCH_KIND, MCP_MATCH_VERSION, digest
