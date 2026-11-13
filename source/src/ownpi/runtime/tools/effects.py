from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from typing import Any
from urllib.parse import urlsplit

from ownpi.runtime.mcp.runtime_events import normalize_mcp_transport
from ownpi.runtime.mcp.selection import (
    cached_resource_list,
    configured_servers,
    header_helper_argv,
    select_resource_read,
    select_servers,
    stdio_argv,
)
from ownpi.runtime.permissions.action_compiler import OperationEffects
from ownpi.runtime.permissions.models import FileSystemAccessMode
from ownpi.runtime.permissions.orchestrator import FileSystemAccessRequest
from ownpi.runtime.permissions.policy import lexical_absolute_path


def resolve_network_effects(
    _context: Any,
    arguments: dict[str, Any],
) -> OperationEffects:
    endpoints = tuple(
        str(arguments.get(key, '') or '').strip()
        for key in ('url', 'endpoint')
        if str(arguments.get(key, '') or '').strip()
    )
    return OperationEffects.declared(
        network_required=True,
        network_endpoints=endpoints,
        source='declared_network_operation',
    )


def resolve_remote_effects(
    context: Any,
    arguments: dict[str, Any],
    *,
    operation: str = 'remote',
) -> OperationEffects:
    from ownpi.tools.backends.remote_workflow.effects import resolve_remote_operation_effects

    return resolve_remote_operation_effects(context, arguments, operation=operation)


def resolve_image_effects(
    context: Any,
    arguments: dict[str, Any],
) -> OperationEffects:
    path = lexical_absolute_path(arguments.get('path') or '.', cwd=context.project_root)
    return OperationEffects.declared(
        file_system_accesses=(FileSystemAccessRequest(path, FileSystemAccessMode.READ),),
        source='declared_image_read',
    )


def resolve_mcp_effects(
    context: Any,
    arguments: dict[str, Any],
    *,
    server_name: str = '',
    all_servers: bool = False,
) -> OperationEffects:
    configured_name = str(
        server_name
        or arguments.get('server_name')
        or arguments.get('server')
        or ''
    ).strip()
    if not server_name and 'resource_id' in arguments:
        selection = select_resource_read(context, arguments)
        if not selection.needs_connection:
            return OperationEffects.declared(source='declared_mcp_resource_cache')
        configured_name = selection.server_name
    selected = select_servers(context, configured_name, all_servers=all_servers)
    effects = [_mcp_server_effects(server) for server in selected]
    session_identity = ''
    if len(selected) == 1:
        selected_name = str(selected[0].get('name', '') or '').strip()
        names = [str(server.get('name', '') or '').strip() for server in configured_servers(context)]
        if names.count(selected_name) == 1:
            session_identity = _stable_remote_mcp_session_identity(selected[0])
    return OperationEffects.declared(
        network_required=any(effect.network_required for effect in effects),
        network_endpoints=tuple(endpoint for effect in effects for endpoint in effect.network_endpoints),
        process_required=any(effect.process_required for effect in effects),
        process_argv=effects[0].process_argv if len(effects) == 1 else (),
        remote_required=any(effect.remote_required for effect in effects),
        remote_target=effects[0].remote_target if len(effects) == 1 else '',
        session_identity=session_identity,
        source='declared_mcp_selected_transports',
    )


def _stable_remote_mcp_session_identity(server: dict[str, Any]) -> str:
    """Remember only fully understood, credential-free remote configurations."""
    from ownpi.runtime.mcp.oauth import load_mcp_oauth_token

    allowed = {
        'name', 'transport', 'type', 'url', 'timeout_seconds', 'headers',
        'headers_helper', 'headersHelper', 'oauth', 'auth', 'env', 'command', 'cwd',
    }
    name = str(server.get('name', '') or '').strip()
    transport = normalize_mcp_transport(server.get('transport', server.get('type', 'stdio')))
    endpoint = str(server.get('url', '') or '').strip()
    timeout = server.get('timeout_seconds', 20)
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError:
        return ''
    if (
        set(server) - allowed or not name
        or type(timeout) is not int or timeout < 1
        or transport not in {'http', 'sse'}
        or parsed.scheme not in {'http', 'https'} or not parsed.hostname
        or parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment
        or any(server.get(key) for key in (
            'headers', 'headers_helper', 'headersHelper', 'oauth', 'auth', 'env',
            'command', 'cwd',
        ))
    ):
        return ''
    try:
        if load_mcp_oauth_token(name) is not None:
            return ''
    except (OSError, ValueError, TypeError, OverflowError):
        return ''
    payload = {
        'schema': 'ownpi.mcp.server_identity.v1', 'name': name,
        'transport': transport, 'endpoint': endpoint, 'port': port,
        'timeout_seconds': timeout,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str).encode()
    ).hexdigest()


def resolve_mcp_template_effects(context: Any, arguments: dict[str, Any]) -> OperationEffects:
    if arguments.get('cursor') and not arguments.get('server'):
        return OperationEffects.declared(source='declared_mcp_invalid_template_cursor')
    return resolve_mcp_effects(context, arguments, all_servers=True)


def resolve_mcp_resource_list_effects(context: Any, arguments: dict[str, Any]) -> OperationEffects:
    name = str(arguments.get('server_name', '') or '').strip()
    if not arguments.get('refresh') and cached_resource_list(context, name):
        return OperationEffects.declared(source='declared_mcp_resource_cache')
    return resolve_mcp_effects(context, arguments)


def _mcp_server_effects(server: dict[str, Any]) -> OperationEffects:
    transport = normalize_mcp_transport(server.get('transport', server.get('type', 'stdio')))
    if transport in {'http', 'sse', 'ws'}:
        endpoint = str(server.get('url', '') or '').strip()
        try:
            helper_argv = header_helper_argv(server)
        except ValueError:
            helper_argv = ()
        return OperationEffects.declared(
            network_required=True,
            network_endpoints=(endpoint,) if endpoint else (),
            remote_required=True,
            remote_target=endpoint,
            process_required=bool(helper_argv),
            process_argv=helper_argv,
            source='declared_mcp_remote_operation',
        )
    argv = stdio_argv(server)
    return OperationEffects.declared(
        process_required=transport == 'stdio' and bool(argv),
        process_argv=argv,
        source='declared_mcp_stdio_operation',
    )


def resolve_mcp_dynamic_effects(
    context: Any,
    arguments: dict[str, Any],
    *,
    server_name: str,
    tool_name: str = '',
) -> OperationEffects:
    effects = resolve_mcp_effects(context, {}, server_name=server_name)
    identity = ''
    if effects.session_identity and tool_name:
        identity = hashlib.sha256(json.dumps(
            [effects.session_identity, tool_name], separators=(',', ':'),
        ).encode()).hexdigest()
    return replace(effects, session_identity=identity)
