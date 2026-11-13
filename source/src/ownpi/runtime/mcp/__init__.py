"""MCP runtime integration package."""

from __future__ import annotations

from typing import Any

__all__ = [
    'MCPElicitationRequest',
    'MCPElicitationResponse',
    'MCPOAuthError',
    'MCPOAuthToken',
    'MCPAppStateRefreshEvent',
    'MCPAppStateRefreshBus',
    'RemoteMCPClientManager',
    'StdioMCPClientManager',
    'build_mcp_dynamic_tool_definitions_from_state',
    'mcp_resource_id',
    'mcp_resource_mention',
    'parse_mcp_resource_id',
    'parse_mcp_resource_mention',
]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(name)

    module_map = {
        'MCPElicitationRequest': 'ownpi.runtime.mcp.client_manager',
        'MCPElicitationResponse': 'ownpi.runtime.mcp.client_manager',
        'RemoteMCPClientManager': 'ownpi.runtime.mcp.client_manager',
        'StdioMCPClientManager': 'ownpi.runtime.mcp.client_manager',
        'build_mcp_dynamic_tool_definitions_from_state': 'ownpi.runtime.mcp.dynamic_tools',
        'MCPOAuthError': 'ownpi.runtime.mcp.oauth',
        'MCPOAuthToken': 'ownpi.runtime.mcp.oauth',
        'mcp_resource_id': 'ownpi.runtime.mcp.resources',
        'mcp_resource_mention': 'ownpi.runtime.mcp.resources',
        'parse_mcp_resource_id': 'ownpi.runtime.mcp.resources',
        'parse_mcp_resource_mention': 'ownpi.runtime.mcp.resources',
        'MCPAppStateRefreshEvent': 'ownpi.runtime.mcp.runtime_events',
        'MCPAppStateRefreshBus': 'ownpi.runtime.mcp.runtime_events',
    }

    import importlib

    module = importlib.import_module(module_map[name])
    return getattr(module, name)
