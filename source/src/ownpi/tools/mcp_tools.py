from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

from ownpi.runtime.mcp.client_manager import MCPClientError, build_mcp_manager
from ownpi.runtime.mcp.oauth import MCPOAuthError, perform_mcp_oauth_browser_flow
from ownpi.runtime.mcp.runtime_events import (
    apply_mcp_manager_events,
    is_supported_mcp_transport,
    record_mcp_capability_error,
    record_mcp_capability_snapshot,
    record_unsupported_mcp_transport,
    unsupported_mcp_transport_reason,
)
from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.hooks.context_delivery import persist_hook_delivery_batch
from ownpi.runtime.hooks.types import HookDeliveryBatch
from ownpi.runtime.mcp.resources import (
    mcp_resource_metadata,
    normalize_mcp_resource_entry,
    normalize_mcp_resource_template_entry,
)
from ownpi.runtime.mcp.selection import (
    cached_resource_list,
    select_resource_read,
    select_servers,
)
from ownpi.runtime.tasks.runtime_task_framework import (
    complete_runtime_task,
    fail_runtime_task,
    register_runtime_task,
    runtime_task_id_for_mcp,
)
from ownpi.tools.common import metadata_for_context

if TYPE_CHECKING:
    from ownpi.runtime.permissions.models import EffectivePermissions
    from ownpi.runtime.tools.definitions import ToolExecutionContext




async def run_list_mcp_resource_templates(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> ToolResult:
    tool_name = 'list_mcp_resource_templates'
    requested_server = str(arguments.get('server', '') or '').strip()
    cursor = str(arguments.get('cursor', '') or '').strip()
    if cursor and not requested_server:
        return ToolResult(
            ok=False,
            tool_name=tool_name,
            error='cursor can only be used when a server is specified',
            metadata=metadata_for_context(context, capability='resource_templates'),
        )

    state = getattr(getattr(context, 'state_store', None), 'state', None)
    configured = getattr(getattr(state, 'mcp_runtime_state', None), 'configured_servers', [])
    if not configured:
        return _resolve_mcp_server(
            context=context,
            server_name=requested_server,
            caller_tool_name=tool_name,
        )

    selected_servers: list[dict[str, Any]] = []
    if requested_server:
        resolved = _resolve_mcp_server(
            context=context,
            server_name=requested_server,
            caller_tool_name=tool_name,
        )
        if isinstance(resolved, ToolResult):
            return resolved
        selected_servers.append(resolved)
    else:
        for item in select_servers(context, all_servers=True):
            server_name = str(item.get('name', '') or '').strip()
            if not server_name:
                continue
            resolved = _resolve_mcp_server(
                context=context,
                server_name=server_name,
                caller_tool_name=tool_name,
            )
            if isinstance(resolved, ToolResult):
                return resolved
            selected_servers.append(resolved)

    resource_templates: list[dict[str, Any]] = []
    warnings: list[str] = []
    connection_failed = False
    next_cursor: str | None = None
    for server in selected_servers:
        server_name = str(server.get('name', '') or '').strip()
        try:
            templates, server_next_cursor, invalid_count = await _fetch_mcp_resource_templates(
                context,
                server=server,
                cursor=cursor or None,
                paginate_all=not requested_server,
            )
        except MCPClientError as exc:
            _record_mcp_capability_error_from_context(
                context,
                server_name=server_name,
                capability='resource_templates',
                error=str(exc),
                source=tool_name,
            )
            _append_mcp_event(
                context,
                server_name=server_name,
                event='resource_templates_list_failed',
                status='failed',
                summary=str(exc),
            )
            if requested_server:
                return ToolResult(
                    ok=False,
                    tool_name=tool_name,
                    error=f'resources/templates/list failed: {exc}',
                    metadata=metadata_for_context(
                        context,
                        server=server_name,
                        capability='resource_templates',
                    ),
                )
            warnings.append(f'{server_name}: resources/templates/list failed: {exc}')
            connection_failed = True
            continue
        resource_templates.extend(templates)
        next_cursor = server_next_cursor if requested_server else None
        _record_mcp_capability_snapshot_from_context(
            context,
            server_name=server_name,
            capability='resource_templates',
            items=templates,
            source=tool_name,
        )
        if invalid_count:
            warnings.append(
                f'{server_name}: ignored {invalid_count} invalid resource template(s)'
            )
        _append_mcp_event(
            context,
            server_name=server_name,
            event='resource_templates_list_completed',
            status='completed',
            summary=f'resource_templates={len(templates)}',
        )

    data: dict[str, Any] = {'resourceTemplates': resource_templates}
    if requested_server:
        data['server'] = requested_server
    if next_cursor:
        data['nextCursor'] = next_cursor
    return ToolResult(
        ok=not connection_failed or bool(resource_templates),
        tool_name=tool_name,
        error='; '.join(warnings) if connection_failed and not resource_templates else None,
        data=data,
        stdout=json.dumps(data, ensure_ascii=False, sort_keys=True),
        warnings=warnings,
        metadata=metadata_for_context(
            context,
            server=requested_server or None,
            capability='resource_templates',
        ),
    )


async def run_mcp_resource_list(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
    state = getattr(getattr(context, 'state_store', None), 'state', None)
    configured = getattr(getattr(state, 'mcp_runtime_state', None), 'configured_servers', [])
    server_name = str(arguments.get('server_name', '') or '').strip()
    refresh = bool(arguments.get('refresh', False))
    if server_name and not any(str(item.get('name', '')) == server_name for item in configured if isinstance(item, dict)):
        return _resolve_mcp_server(
            context=context,
            server_name=server_name,
            caller_tool_name='mcp_resource_list',
        )
    cached = cached_resource_list(context, server_name)
    if not configured:
        return _resolve_mcp_server(
            context=context,
            server_name=server_name,
            caller_tool_name='mcp_resource_list',
        )
    server = _resolve_mcp_server(context=context, server_name=server_name, caller_tool_name='mcp_resource_list')
    if isinstance(server, ToolResult):
        return server
    matched = cached
    if refresh or not matched:
        if not isinstance(server, ToolResult):
            try:
                runtime_task_id = _register_mcp_runtime_task(
                    context,
                    server_name=str(server.get('name', '') or server_name or 'stdio'),
                    operation='resource_list',
                    description='mcp resource list',
                )
                manager = build_mcp_manager(
                    server,
                    run_id=_run_id_from_context(context),
                    elicitation_handler=getattr(context, 'mcp_elicitation_handler', None),
                    hook_engine=getattr(context, 'hook_engine', None),
                    effective_permissions=_canonical_permissions(context),
                )
                listed = await manager.list_resources()
                _sync_mcp_manager_state(context, manager)
                _complete_mcp_runtime_task(context, runtime_task_id=runtime_task_id, metadata={'resource_count': len(listed.get('resources', []) if isinstance(listed.get('resources'), list) else [])})
                listed_resources = [dict(item) for item in listed.get('resources', []) if isinstance(item, dict)]
                matched = []
                if listed_resources:
                    matched = []
                    for item in listed_resources:
                        normalized = normalize_mcp_resource_entry(str(server.get('name', '') or ''), item)
                        metadata = mcp_resource_metadata(normalized)
                        resource_id = str(normalized.get('resource_id', '') or '')
                        matched.append(metadata)
                        if resource_id:
                            getattr(state.mcp_runtime_state, 'resource_cache', {})[resource_id] = dict(metadata)
                    _append_mcp_event(
                        context,
                        server_name=str(server.get('name', '') or ''),
                        event='resources_list_completed',
                        status='completed',
                        summary=f'resources={len(matched)}',
                    )
                    _record_mcp_capability_snapshot_from_context(
                        context,
                        server_name=str(server.get('name', '') or ''),
                        capability='resources',
                        items=listed_resources,
                        source='mcp_resource_list',
                    )
            except MCPClientError as exc:
                _record_mcp_capability_error_from_context(
                    context,
                    server_name=str(server.get('name', '') or ''),
                    capability='resources',
                    error=str(exc),
                    source='mcp_resource_list',
                )
                _fail_mcp_runtime_task(context, runtime_task_id=locals().get('runtime_task_id'), error=str(exc))
                _append_mcp_event(
                    context,
                    server_name=str(server.get('name', '') or ''),
                    event='resources_list_failed',
                    status='failed',
                    summary=str(exc),
                )
                return ToolResult(
                    ok=False,
                    tool_name='mcp_resource_list',
                    error=str(exc),
                    metadata=metadata_for_context(context, server_name=server_name or None),
                )
    return ToolResult(
        ok=True,
        tool_name='mcp_resource_list',
        data={'server_name': server_name or None, 'configured_servers': configured, 'resources': matched, 'cache_hit': bool(cached), 'refreshed': refresh or not cached},
        stdout=f'configured_servers={[item.get("name") for item in configured if isinstance(item, dict)]}\nresources={matched[:20]}',
        metadata=metadata_for_context(context, server_name=server_name or None),
    )


async def run_mcp_resource_read(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
    state = getattr(getattr(context, 'state_store', None), 'state', None)
    resource_id = str(arguments.get('resource_id', '') or '').strip()
    server_name = str(arguments.get('server_name', '') or '').strip()
    resources = getattr(getattr(state, 'mcp_runtime_state', None), 'resource_cache', {})
    if not resource_id:
        return ToolResult(ok=False, tool_name='mcp_resource_read', error='resource_id is required')
    selection = select_resource_read(context, arguments)
    server_name = selection.server_name
    resource_uri = selection.uri
    canonical_resource_id = selection.resource_id
    payload = selection.payload
    if selection.needs_connection:
        server = _resolve_mcp_server(context=context, server_name=server_name, caller_tool_name='mcp_resource_read')
        if isinstance(server, ToolResult):
            return server
        if not isinstance(server, ToolResult):
            try:
                runtime_task_id = _register_mcp_runtime_task(
                    context,
                    server_name=str(server.get('name', '') or server_name or 'stdio'),
                    operation='resource_read',
                    description=f'mcp resource read {resource_id}',
                )
                manager = build_mcp_manager(
                    server,
                    run_id=_run_id_from_context(context),
                    elicitation_handler=getattr(context, 'mcp_elicitation_handler', None),
                    hook_engine=getattr(context, 'hook_engine', None),
                    effective_permissions=_canonical_permissions(context),
                )
                response = await manager.read_resource(resource_uri=resource_uri)
                _sync_mcp_manager_state(context, manager)
                _complete_mcp_runtime_task(context, runtime_task_id=runtime_task_id, metadata={'resource_id': canonical_resource_id})
                contents = response.get('contents')
                payload = normalize_mcp_resource_entry(
                    str(server.get('name', '') or ''),
                    {
                        'resource_id': canonical_resource_id,
                        'uri': resource_uri,
                        'contents': contents,
                    },
                )
                if isinstance(resources, dict):
                    resources[canonical_resource_id] = mcp_resource_metadata(payload)
                _append_mcp_event(
                    context,
                    server_name=str(server.get('name', '') or ''),
                    event='resource_read_completed',
                    status='completed',
                    summary=canonical_resource_id,
                )
            except MCPClientError as exc:
                _fail_mcp_runtime_task(context, runtime_task_id=locals().get('runtime_task_id'), error=str(exc))
                _append_mcp_event(
                    context,
                    server_name=str(server.get('name', '') or server_name),
                    event='resource_read_failed',
                    status='failed',
                    summary=f'{canonical_resource_id}: {exc}',
                )
                return ToolResult(ok=False, tool_name='mcp_resource_read', error=str(exc))
    if not isinstance(payload, dict):
        return ToolResult(ok=False, tool_name='mcp_resource_read', error=f'mcp resource not found: {resource_id}')
    return ToolResult(
        ok=True,
        tool_name='mcp_resource_read',
        data={'resource_id': canonical_resource_id, 'resource': dict(payload)},
        stdout=str(payload),
        metadata=metadata_for_context(context, resource_id=canonical_resource_id),
    )


async def run_mcp_tool_list(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
    server = _resolve_mcp_server(
        context=context,
        server_name=str(arguments.get('server_name', '') or '').strip(),
        caller_tool_name='mcp_tool_list',
    )
    if isinstance(server, ToolResult):
        return server
    server_name = str(server.get('name', '') or '')
    try:
        runtime_task_id = _register_mcp_runtime_task(
            context,
            server_name=server_name or 'stdio',
            operation='tool_list',
            description='mcp tool list',
        )
        manager = build_mcp_manager(
            server,
            run_id=_run_id_from_context(context),
            elicitation_handler=getattr(context, 'mcp_elicitation_handler', None),
            hook_engine=getattr(context, 'hook_engine', None),
            effective_permissions=_canonical_permissions(context),
            hook_delivery_handler=_hook_delivery_handler(context),
            hook_scope=_hook_delivery_scope(context),
        )
        result = await manager.list_tools()
        _sync_mcp_manager_state(context, manager)
        _complete_mcp_runtime_task(context, runtime_task_id=runtime_task_id, metadata={'tool_count': len(result.get('tools', []) if isinstance(result.get('tools'), list) else [])})
    except MCPClientError as exc:
        if 'manager' in locals():
            _sync_mcp_manager_state(context, manager)
        _fail_mcp_runtime_task(context, runtime_task_id=locals().get('runtime_task_id'), error=str(exc))
        _append_mcp_event(context, server_name=server_name, event='tools_list_failed', status='failed', summary=str(exc))
        return ToolResult(
            ok=False,
            tool_name='mcp_tool_list',
            error=str(exc),
            metadata=metadata_for_context(context, server_name=server_name),
        )
    tools = [dict(item) for item in result.get('tools', []) if isinstance(item, dict)]
    runtime_state = getattr(getattr(context, 'state_store', None), 'state', None)
    mcp_runtime_state = getattr(runtime_state, 'mcp_runtime_state', None)
    if mcp_runtime_state is not None:
        mcp_runtime_state.tool_cache[server_name] = [dict(item) for item in tools]
        record_mcp_capability_snapshot(
            mcp_runtime_state,
            server_name=server_name,
            capability='tools',
            items=tools,
            source='mcp_tool_list',
        )
    _append_mcp_event(context, server_name=server_name, event='tools_list_completed', status='completed', summary=f'tools={len(tools)}')
    return ToolResult(
        ok=True,
        tool_name='mcp_tool_list',
        data={'server_name': server_name, 'tools': tools},
        stdout=f"server_name={server_name}\ntools={[item.get('name') for item in tools]}",
        metadata=metadata_for_context(context, server_name=server_name),
    )


async def run_mcp_auth(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
    server = _resolve_mcp_server(
        context=context,
        server_name=str(arguments.get('server_name', '') or '').strip(),
        caller_tool_name='mcp_auth',
    )
    if isinstance(server, ToolResult):
        return server
    server_name = str(server.get('name', '') or '').strip()
    transport = str(server.get('transport', server.get('type', 'stdio')) or 'stdio').strip().lower()
    if transport not in {'http', 'sse', 'ws'}:
        return ToolResult(
            ok=False,
            tool_name='mcp_auth',
            error=f'mcp_auth is only available for remote MCP transports, got {transport}',
            metadata=metadata_for_context(context, server_name=server_name, transport=transport),
        )
    try:
        auth_result = await perform_mcp_oauth_browser_flow(server, server_name=server_name)
    except (MCPOAuthError, MCPClientError, OSError, ValueError) as exc:
        _append_mcp_event(context, server_name=server_name, event='oauth_failed', status='failed', summary=str(exc))
        return ToolResult(
            ok=False,
            tool_name='mcp_auth',
            error=str(exc),
            metadata=metadata_for_context(context, server_name=server_name, transport=transport, auth_status='failed'),
        )
    _append_mcp_event(context, server_name=server_name, event='oauth_completed', status='completed', summary='authenticated')
    return ToolResult(
        ok=True,
        tool_name='mcp_auth',
        data=auth_result,
        metadata=metadata_for_context(context, server_name=server_name, transport=transport, auth_status='authenticated'),
    )


async def run_mcp_tool_invoke(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
    server = _resolve_mcp_server(
        context=context,
        server_name=str(arguments.get('server_name', '') or '').strip(),
        caller_tool_name='mcp_tool_invoke',
    )
    if isinstance(server, ToolResult):
        return server
    tool_name = str(arguments.get('tool_name', '') or '').strip()
    tool_input = dict(arguments.get('input', {})) if isinstance(arguments.get('input'), dict) else {}
    if not tool_name:
        return ToolResult(ok=False, tool_name='mcp_tool_invoke', error='tool_name is required')
    server_name = str(server.get('name', '') or '')
    cached_schema = _resolve_cached_tool_schema(context=context, server_name=server_name, tool_name=tool_name)
    try:
        runtime_task_id = _register_mcp_runtime_task(
            context,
            server_name=server_name or 'stdio',
            operation='tool_invoke',
            description=f'mcp tool invoke {tool_name}',
        )
        manager = build_mcp_manager(
            server,
            run_id=_run_id_from_context(context),
            elicitation_handler=getattr(context, 'mcp_elicitation_handler', None),
            hook_engine=getattr(context, 'hook_engine', None),
            effective_permissions=_canonical_permissions(context),
        )
        result = await manager.invoke_tool(tool_name=tool_name, input=tool_input)
        _sync_mcp_manager_state(context, manager)
        _complete_mcp_runtime_task(context, runtime_task_id=runtime_task_id, metadata={'tool_name': tool_name})
    except MCPClientError as exc:
        _record_mcp_capability_error_from_context(
            context,
            server_name=server_name,
            capability='tools',
            error=str(exc),
            source='mcp_tool_list',
        )
        _fail_mcp_runtime_task(context, runtime_task_id=locals().get('runtime_task_id'), error=str(exc))
        _append_mcp_event(context, server_name=server_name, event='tool_invoke_failed', status='failed', summary=f'{tool_name}: {exc}')
        return ToolResult(
            ok=False,
            tool_name='mcp_tool_invoke',
            error=str(exc),
            metadata=metadata_for_context(context, server_name=server_name, tool_name=tool_name),
        )
    content = result.get('content')
    _append_mcp_event(context, server_name=server_name, event='tool_invoke_completed', status='completed', summary=f'{tool_name}')
    return ToolResult(
        ok=True,
        tool_name='mcp_tool_invoke',
        data={
            'server_name': server_name,
            'tool_name': tool_name,
            'input': tool_input,
            'content': content,
            'cached_tool_schema': cached_schema,
            'raw_result': result,
        },
        stdout=f'server_name={server_name}\ntool_name={tool_name}\ncontent={content}',
        metadata=metadata_for_context(context, server_name=server_name, tool_name=tool_name),
    )



def _run_id_from_context(context: ToolExecutionContext) -> str:
    direct = str(getattr(context, 'run_id', '') or '').strip()
    if direct:
        return direct
    state = getattr(getattr(context, 'state_store', None), 'state', None)
    run_metadata = getattr(state, 'run_metadata', None)
    return str(getattr(run_metadata, 'run_id', '') or '').strip()


def _hook_delivery_handler(context: ToolExecutionContext) -> Any:
    state = getattr(getattr(context, 'state_store', None), 'state', None)
    context_state = getattr(state, 'context_state', None)
    if context_state is None:
        return None

    def persist(batch: HookDeliveryBatch) -> None:
        persist_hook_delivery_batch(context_state, batch)

    return persist


def _hook_delivery_scope(context: ToolExecutionContext) -> dict[str, Any]:
    stage = getattr(
        getattr(context, 'stage', None),
        'value',
        getattr(context, 'stage', ''),
    )
    return {
        'run_id': _run_id_from_context(context),
        'stage': str(stage or ''),
        'attempt': context.attempt,
        'actor_kind': context.actor_kind,
        'tool_call_id': context.tool_call_id,
    }


def _canonical_permissions(context: ToolExecutionContext) -> EffectivePermissions | None:
    if context.effective_permissions is not None:
        return context.effective_permissions
    state = getattr(getattr(context, 'state_store', None), 'state', None)
    return getattr(getattr(state, 'tool_state', None), 'effective_permissions', None)


async def _fetch_mcp_resource_templates(
    context: ToolExecutionContext,
    *,
    server: dict[str, Any],
    cursor: str | None,
    paginate_all: bool,
) -> tuple[list[dict[str, Any]], str | None, int]:
    server_name = str(server.get('name', '') or '').strip()
    runtime_task_id = _register_mcp_runtime_task(
        context,
        server_name=server_name or 'stdio',
        operation='resource_templates_list',
        description='mcp resource templates list',
    )
    templates: list[dict[str, Any]] = []
    invalid_count = 0
    request_cursor = cursor
    seen_cursors: set[str] = set()
    manager = None
    try:
        manager = build_mcp_manager(
            server,
            run_id=_run_id_from_context(context),
            elicitation_handler=getattr(context, 'mcp_elicitation_handler', None),
            hook_engine=getattr(context, 'hook_engine', None),
            effective_permissions=_canonical_permissions(context),
        )
        while True:
            response = await manager.list_resource_templates(cursor=request_cursor)
            _sync_mcp_manager_state(context, manager)
            raw_templates = response.get('resourceTemplates', [])
            if not isinstance(raw_templates, list):
                raise MCPClientError(
                    'resources/templates/list response resourceTemplates must be an array'
                )
            for item in raw_templates:
                normalized = (
                    normalize_mcp_resource_template_entry(server_name, item)
                    if isinstance(item, dict)
                    else None
                )
                if normalized is None:
                    invalid_count += 1
                    continue
                templates.append(normalized)
            next_cursor = str(response.get('nextCursor', '') or '').strip() or None
            if not paginate_all or next_cursor is None:
                _complete_mcp_runtime_task(
                    context,
                    runtime_task_id=runtime_task_id,
                    metadata={'resource_template_count': len(templates)},
                )
                return templates, next_cursor, invalid_count
            if next_cursor in seen_cursors or next_cursor == request_cursor:
                raise MCPClientError(
                    'resources/templates/list returned duplicate cursor'
                )
            seen_cursors.add(next_cursor)
            request_cursor = next_cursor
    except MCPClientError as exc:
        if manager is not None:
            _sync_mcp_manager_state(context, manager)
        _fail_mcp_runtime_task(context, runtime_task_id=runtime_task_id, error=str(exc))
        raise


def _register_mcp_runtime_task(
    context: ToolExecutionContext,
    *,
    server_name: str,
    operation: str,
    description: str,
) -> str | None:
    state = getattr(getattr(context, 'state_store', None), 'state', None)
    if state is None:
        return None
    task_id = f'{runtime_task_id_for_mcp(server_name, operation)}:{uuid.uuid4().hex[:8]}'
    register_runtime_task(
        state,
        task_id=task_id,
        task_type='mcp',
        status='running',
        description=description,
        provider_kind='mcp_stdio',
        run_id=_run_id_from_context(context),
        stage=context.stage.value,
        attempt=context.attempt,
        session_id=f'mcp:{server_name}',
        metadata={
            'server_name': server_name,
            'operation': operation,
            'continuation_index': context.continuation_index,
        },
    )
    return task_id


def _complete_mcp_runtime_task(
    context: ToolExecutionContext,
    *,
    runtime_task_id: str | None,
    metadata: dict[str, Any] | None = None,
) -> None:
    state = getattr(getattr(context, 'state_store', None), 'state', None)
    if state is None or not runtime_task_id:
        return
    complete_runtime_task(state, task_id=runtime_task_id, metadata=metadata)


def _fail_mcp_runtime_task(
    context: ToolExecutionContext,
    *,
    runtime_task_id: str | None,
    error: str,
) -> None:
    state = getattr(getattr(context, 'state_store', None), 'state', None)
    if state is None or not runtime_task_id:
        return
    fail_runtime_task(state, task_id=runtime_task_id, metadata={'error': error})


def _resolve_mcp_server(
    context: ToolExecutionContext,
    server_name: str,
    *,
    caller_tool_name: str,
) -> dict[str, Any] | ToolResult:
    state = getattr(getattr(context, 'state_store', None), 'state', None)
    configured = select_servers(context, server_name)
    if not configured:
        return ToolResult(
            ok=False,
            tool_name=caller_tool_name,
            error=f'mcp server not configured: {server_name}' if server_name else 'no MCP servers configured',
            metadata=metadata_for_context(context, no_config=True),
        )
    first = configured[0]
    transport = first.get('transport', first.get('type', 'stdio'))
    if not is_supported_mcp_transport(transport):
        error = unsupported_mcp_transport_reason(first) or f'unsupported MCP transport: {transport}'
        runtime_state = getattr(state, 'mcp_runtime_state', None)
        if runtime_state is not None:
            record_unsupported_mcp_transport(
                runtime_state,
                server_name=str(first.get('name', '') or ''),
                transport=transport,
                reason=error,
                capability=_capability_for_tool(caller_tool_name),
            )
        return ToolResult(
            ok=False,
            tool_name=caller_tool_name,
            error=error,
            metadata=metadata_for_context(context, transport=transport),
        )
    return dict(first)


def _append_mcp_event(
    context: ToolExecutionContext,
    *,
    server_name: str,
    event: str,
    status: str,
    summary: str,
) -> None:
    state = getattr(getattr(context, 'state_store', None), 'state', None)
    runtime_state = getattr(state, 'mcp_runtime_state', None)
    if runtime_state is None:
        return
    payload = {
        'server_name': server_name,
        'event': event,
        'status': status,
        'summary': summary,
        'stage': context.stage.value,
        'attempt': context.attempt,
        'continuation_index': context.continuation_index,
    }
    runtime_state.events.append(payload)
    state_store = getattr(context, 'state_store', None)
    if state_store is not None and hasattr(state_store, 'record_mcp_mutation'):
        state_store.record_mcp_mutation(
            event_name=f'mcp_{event}',
            status=status,
            mutation_kind=_mcp_mutation_kind(event),
            stage=context.stage.value,
            attempt=context.attempt,
            continuation_index=context.continuation_index,
            payload=payload,
        )


def _mcp_mutation_kind(event: str) -> str:
    normalized = str(event or '').strip()
    if normalized in {
        'resources_list_completed',
        'resource_templates_list_completed',
        'tools_list_completed',
        'prompts_list_completed',
        'dynamic_tools_list_completed',
    }:
        return 'cache_refreshed'
    if normalized.endswith('_completed') or normalized.endswith('_failed'):
        return 'result_recorded'
    return 'state_delta'


def _sync_mcp_manager_state(context: ToolExecutionContext, manager: Any) -> None:
    state = getattr(getattr(context, 'state_store', None), 'state', None)
    runtime_state = getattr(state, 'mcp_runtime_state', None)
    if runtime_state is None:
        return
    apply_mcp_manager_events(
        runtime_state,
        manager,
        extra_event_fields={
            'state_store': getattr(context, 'state_store', None),
            'stage': context.stage.value,
            'attempt': context.attempt,
            'continuation_index': context.continuation_index,
        },
    )


def _record_mcp_capability_snapshot_from_context(
    context: ToolExecutionContext,
    *,
    server_name: str,
    capability: str,
    items: list[dict[str, Any]],
    source: str,
) -> None:
    state = getattr(getattr(context, 'state_store', None), 'state', None)
    runtime_state = getattr(state, 'mcp_runtime_state', None)
    if runtime_state is None:
        return
    record_mcp_capability_snapshot(
        runtime_state,
        server_name=server_name,
        capability=capability,
        items=items,
        source=source,
    )


def _record_mcp_capability_error_from_context(
    context: ToolExecutionContext,
    *,
    server_name: str,
    capability: str,
    error: str,
    source: str,
) -> None:
    state = getattr(getattr(context, 'state_store', None), 'state', None)
    runtime_state = getattr(state, 'mcp_runtime_state', None)
    if runtime_state is None:
        return
    record_mcp_capability_error(
        runtime_state,
        server_name=server_name,
        capability=capability,
        error=error,
        source=source,
    )


def _capability_for_tool(tool_name: str) -> str | None:
    if tool_name in {'mcp_tool_list', 'mcp_tool_invoke'}:
        return 'tools'
    if tool_name in {'mcp_resource_list', 'mcp_resource_read'}:
        return 'resources'
    if tool_name == 'list_mcp_resource_templates':
        return 'resource_templates'
    return None


def _resolve_cached_tool_schema(
    *,
    context: ToolExecutionContext,
    server_name: str,
    tool_name: str,
) -> dict[str, Any] | None:
    state = getattr(getattr(context, 'state_store', None), 'state', None)
    runtime_state = getattr(state, 'mcp_runtime_state', None)
    if runtime_state is None:
        return None
    for item in runtime_state.tool_cache.get(server_name, []):
        if isinstance(item, dict) and str(item.get('name', '') or '') == tool_name:
            return dict(item)
    return None
