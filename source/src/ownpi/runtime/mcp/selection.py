from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Any

from ownpi.runtime.mcp.resources import (
    mcp_resource_id,
    mcp_resource_metadata,
    normalize_mcp_resource_entry,
    resolve_mcp_resource_reference,
)


def stdio_argv(server: dict[str, Any]) -> tuple[str, ...]:
    command = server.get('command', [])
    if not isinstance(command, list):
        return ()
    return tuple(str(item) for item in command if str(item).strip())


def header_helper_argv(server: dict[str, Any]) -> tuple[str, ...]:
    helper = str(server.get('headers_helper', server.get('headersHelper', '')) or '').strip()
    return tuple(shlex.split(helper))


def configured_servers(context: Any) -> list[dict[str, Any]]:
    state = getattr(getattr(context, 'state_store', None), 'state', None)
    runtime = getattr(state, 'mcp_runtime_state', None)
    return [dict(item) for item in getattr(runtime, 'configured_servers', ()) if isinstance(item, dict)]


def select_servers(context: Any, name: str = '', *, all_servers: bool = False) -> list[dict[str, Any]]:
    servers = configured_servers(context)
    if name:
        return [item for item in servers if str(item.get('name', '') or '').strip() == name][:1]
    if all_servers:
        return sorted(
            (item for item in servers if str(item.get('name', '') or '').strip()),
            key=lambda item: str(item['name']),
        )
    return servers[:1]


def resource_cache(context: Any) -> dict[str, Any]:
    state = getattr(getattr(context, 'state_store', None), 'state', None)
    cache = getattr(getattr(state, 'mcp_runtime_state', None), 'resource_cache', {})
    return cache if isinstance(cache, dict) else {}


def cached_resource_list(context: Any, server_name: str) -> list[dict[str, Any]]:
    return [
        mcp_resource_metadata(normalize_mcp_resource_entry(
            str(value.get('server_name', server_name) or server_name),
            {'resource_id': key, **value},
        ))
        for key, value in resource_cache(context).items()
        if isinstance(value, dict) and (not server_name or str(value.get('server_name', '')) == server_name)
    ]


@dataclass(frozen=True)
class ResourceReadSelection:
    server_name: str
    uri: str
    resource_id: str
    payload: dict[str, Any] | None

    @property
    def needs_connection(self) -> bool:
        return self.payload is None or 'contents' not in self.payload


def select_resource_read(context: Any, arguments: dict[str, Any]) -> ResourceReadSelection:
    server, uri, identity, payload = resolve_mcp_resource_reference(
        str(arguments.get('resource_id', '') or '').strip(),
        server_name=str(arguments.get('server_name', '') or '').strip(),
        resource_cache=resource_cache(context),
    )
    if not server:
        selected = select_servers(context)
        if selected:
            server = str(selected[0].get('name', '') or '').strip()
            identity = mcp_resource_id(server, uri)
    return ResourceReadSelection(server, uri, identity, payload)
