from __future__ import annotations
from ownpi.runtime.tools.definitions import ToolExposure

from collections import Counter
import asyncio
from dataclasses import replace
import hashlib
import json
import re
from typing import Any

from ownpi.controller.stages import Stage
from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.mcp.client_manager import MCPClientError, build_mcp_manager
from ownpi.runtime.mcp.runtime_events import (
    apply_mcp_manager_events,
    is_supported_mcp_transport,
    normalize_mcp_transport,
    record_mcp_capability_error,
    record_mcp_capability_snapshot,
    record_unsupported_mcp_transport,
    unsupported_mcp_transport_reason,
)
from ownpi.runtime.tools.definitions import ToolDefinition, ToolExecutionContext
from ownpi.runtime.tools.effects import resolve_mcp_dynamic_effects
from ownpi.tools.mcp_tools import run_mcp_tool_invoke
from ownpi.tools.common import metadata_for_context


MCP_DYNAMIC_TOOL_PREFIX = 'mcp'
_MCP_DYNAMIC_TOOL_STAGES = (
    Stage.LITERATURE_GROUNDING.value,
    Stage.METHOD_DESIGN.value,
    Stage.IMPLEMENTATION_EXECUTION.value,
    Stage.ANALYSIS_DECISION.value,
)


def refresh_mcp_tool_cache_from_configured_servers(
    state: Any,
    *,
    run_id: str = '',
    force: bool = False,
) -> int:
    """Refresh stdio MCP tool cache from configured servers.

    This is intentionally best-effort. Prompt assembly should not fail just because
    one external server is unavailable.
    """

    runtime_state = getattr(state, 'mcp_runtime_state', None)
    if runtime_state is None:
        return 0
    configured = [
        dict(item)
        for item in getattr(runtime_state, 'configured_servers', [])
        if isinstance(item, dict)
    ]
    refreshed = 0
    effective_permissions = getattr(
        getattr(state, 'tool_state', None),
        'effective_permissions',
        None,
    )
    for server in configured:
        server_name = str(server.get('name', '') or '').strip()
        if not server_name:
            continue
        if not is_supported_mcp_transport(server.get('transport', 'stdio')):
            record_unsupported_mcp_transport(
                runtime_state,
                server_name=server_name,
                transport=server.get('transport', 'stdio'),
                reason=unsupported_mcp_transport_reason(server),
                capability='tools',
            )
            continue
        if _server_has_connection_config(server):
            try:
                manager = build_mcp_manager(
                    server,
                    run_id=run_id,
                    effective_permissions=effective_permissions,
                )
                _sync_mcp_runtime_state_from_manager(runtime_state, manager)
            except (MCPClientError, OSError, ValueError):
                manager = None
        stale = _server_capability_is_stale(runtime_state, server_name=server_name, capability='tools')
        if not force and not stale and server_name in runtime_state.tool_cache:
            continue
        if not _server_has_connection_config(server):
            continue
        try:
            manager = manager or build_mcp_manager(
                server,
                run_id=run_id,
                effective_permissions=effective_permissions,
            )
            result = asyncio.run(manager.list_tools())
            _sync_mcp_runtime_state_from_manager(runtime_state, manager)
        except (MCPClientError, OSError, ValueError) as exc:
            record_mcp_capability_error(
                runtime_state,
                server_name=server_name,
                capability='tools',
                error=str(exc),
                source='dynamic_tool_discovery',
            )
            _append_mcp_discovery_event(
                runtime_state,
                server_name=server_name,
                status='failed',
                summary=str(exc),
            )
            continue
        tools = [dict(item) for item in result.get('tools', []) if isinstance(item, dict)]
        runtime_state.tool_cache[server_name] = tools
        record_mcp_capability_snapshot(
            runtime_state,
            server_name=server_name,
            capability='tools',
            items=tools,
            source='dynamic_tool_discovery',
        )
        _clear_stale_capability(runtime_state, server_name=server_name, capability='tools')
        refreshed += 1
        _append_mcp_discovery_event(
            runtime_state,
            server_name=server_name,
            status='completed',
            summary=f'tools={len(tools)}',
        )
    return refreshed


def build_mcp_dynamic_tool_definitions_from_state(state: Any) -> tuple[ToolDefinition, ...]:
    runtime_state = getattr(state, 'mcp_runtime_state', None)
    tool_cache = getattr(runtime_state, 'tool_cache', {}) if runtime_state is not None else {}
    if not isinstance(tool_cache, dict):
        return ()
    definitions: list[ToolDefinition] = []
    used_names: set[str] = set()
    candidates: list[tuple[str, str, dict[str, Any]]] = []
    for server_name, tools in sorted(tool_cache.items(), key=lambda item: str(item[0])):
        normalized_server_name = str(server_name or '').strip()
        if not normalized_server_name or not isinstance(tools, list):
            continue
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            raw_tool_name = str(tool.get('name', '') or '').strip()
            if not raw_tool_name:
                continue
            candidates.append((normalized_server_name, raw_tool_name, dict(tool)))
    raw_name_counts = Counter(raw_name.casefold() for _, raw_name, _ in candidates)
    server_descriptions = {
        str(server.get('name', '') or '').strip(): str(server.get('description', '') or '')
        for server in getattr(runtime_state, 'configured_servers', [])
        if isinstance(server, dict)
    }
    for normalized_server_name, raw_tool_name, tool in candidates:
        dynamic_name = _unique_dynamic_tool_name(
            mcp_dynamic_tool_name(normalized_server_name, raw_tool_name),
            used_names=used_names,
            collision_key=f'{normalized_server_name}:{raw_tool_name}',
        )
        used_names.add(dynamic_name)
        definitions.append(
            _build_dynamic_mcp_tool_definition(
                dynamic_tool_name=dynamic_name,
                server_name=normalized_server_name,
                mcp_tool_name=raw_tool_name,
                tool_payload=tool,
                include_raw_alias=raw_name_counts[raw_tool_name.casefold()] == 1,
                namespace_description=server_descriptions.get(normalized_server_name, ''),
            )
        )
    return tuple(definitions)


def mcp_dynamic_tools_fingerprint(state: Any) -> dict[str, Any]:
    runtime_state = getattr(state, 'mcp_runtime_state', None)
    tool_cache = getattr(runtime_state, 'tool_cache', {}) if runtime_state is not None else {}
    if not isinstance(tool_cache, dict):
        return {'servers': []}
    servers: list[dict[str, Any]] = []
    for server_name, tools in sorted(tool_cache.items(), key=lambda item: str(item[0])):
        if not isinstance(tools, list):
            continue
        server_tools = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            raw_tool_name = str(tool.get('name', '') or '').strip()
            if not raw_tool_name:
                continue
            server_tools.append(
                {
                    'name': raw_tool_name,
                    'schema_hash': _json_hash(_normalize_input_schema(tool)),
                }
            )
        servers.append({'server_name': str(server_name), 'tools': server_tools})
    return {'servers': servers}


def mcp_dynamic_tool_name(server_name: str, tool_name: str) -> str:
    return f'{MCP_DYNAMIC_TOOL_PREFIX}__{_identifier_part(server_name)}__{_identifier_part(tool_name)}'


def _unique_dynamic_tool_name(base_name: str, *, used_names: set[str], collision_key: str) -> str:
    if base_name not in used_names:
        return base_name
    stem = f'{base_name}__{_short_hash(collision_key)}'
    candidate = stem
    index = 2
    while candidate in used_names:
        candidate = f'{stem}_{index}'
        index += 1
    return candidate


def _build_dynamic_mcp_tool_definition(
    *,
    dynamic_tool_name: str,
    server_name: str,
    mcp_tool_name: str,
    tool_payload: dict[str, Any],
    include_raw_alias: bool,
    namespace_description: str,
) -> ToolDefinition:
    description = str(tool_payload.get('description', '') or '').strip()
    if not description:
        description = f'Invoke MCP tool {mcp_tool_name} on server {server_name}.'
    input_schema = _normalize_input_schema(tool_payload)
    annotations = _normalize_mcp_tool_annotations(tool_payload)
    read_only = bool(annotations.get('readOnlyHint', False))
    destructive = bool(annotations.get('destructiveHint', False))
    open_world = bool(annotations.get('openWorldHint', False))
    always_load = bool(annotations.get('alwaysLoad', False))
    search_hint = str(
        annotations.get('searchHint')
        or f'Invoke MCP tool {mcp_tool_name} from configured server {server_name}.'
    ).strip()
    destructive_kinds = tuple(
        item
        for item, enabled in (
            ('mcp_destructive_hint', destructive),
            ('mcp_open_world_hint', open_world),
        )
        if enabled
    )
    permission_class = 'inspection' if read_only and not destructive and not open_world else 'mcp'
    side_effect_level = 'none' if read_only and not destructive and not open_world else 'external'
    execution_class = 'read_only' if read_only and not destructive and not open_world else 'external'

    async def _handler(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
        result = await run_mcp_tool_invoke(
            context,
            {
                'server_name': server_name,
                'tool_name': mcp_tool_name,
                'input': dict(arguments),
            },
        )
        metadata = dict(result.metadata)
        metadata.update(
            metadata_for_context(
                context,
                dynamic_tool_name=dynamic_tool_name,
                server_name=server_name,
                tool_name=mcp_tool_name,
                mcp_dynamic_tool=True,
            )
        )
        return replace(result, tool_name=dynamic_tool_name, metadata=metadata)

    return ToolDefinition(
        exposure=ToolExposure.DIRECT if always_load else ToolExposure.DEFERRED,
        name=dynamic_tool_name,
        namespace_description=namespace_description,
               kind='mcp_tool',
               description=description,
               detail=_mcp_dynamic_tool_detail(
                server_name=server_name,
                mcp_tool_name=mcp_tool_name,
                annotations=annotations,
            ),
               aliases=((mcp_tool_name,) if include_raw_alias else ()),
               search_hint=search_hint,
               search_keywords=_mcp_dynamic_tool_search_keywords(
                server_name=server_name,
                mcp_tool_name=mcp_tool_name,
                annotations=annotations,
            ),






               read_only=read_only,
               concurrency_safe=read_only and not destructive and not open_world,
               permission_class=permission_class,
               invocation_mode='agent_requested',
               execution_class=execution_class,
               side_effect_level=side_effect_level,
               requires_network=open_world,
               destructive_capable=destructive or open_world,
               destructive_kinds=destructive_kinds,
               resume_strategy='stateless',
               schema_cache_variant=f'{server_name}:{mcp_tool_name}',
        detail_builder=lambda: {
            'server_name': server_name,
            'mcp_tool_name': mcp_tool_name,
            'source': 'mcp_dynamic_tool_cache',
            'fallback_tool': 'mcp_tool_invoke',
            'annotations': dict(annotations),
            'result_highlights': ['server_name', 'tool_name', 'content'],
        },
        input_schema_builder=lambda: dict(input_schema),
        handler=_handler,
        effect_builder=lambda context, arguments: resolve_mcp_dynamic_effects(
            context, arguments, server_name=server_name, tool_name=mcp_tool_name
        ),
        activity_builder=lambda _arguments: f'mcp dynamic tool {server_name}:{mcp_tool_name}',
        classifier_input_builder=lambda arguments: {
            'server_name': server_name,
            'tool_name': mcp_tool_name,
            'input': dict(arguments),
        },
        observation_family='mcp',
        result_family='mcp',
        backend_kind='mcp',
        toolset_family='external',
        credential_mode='delegated',
    )


def _normalize_mcp_tool_annotations(tool_payload: dict[str, Any]) -> dict[str, Any]:
    raw = tool_payload.get('annotations')
    if not isinstance(raw, dict):
        raw = tool_payload.get('toolAnnotations')
    if not isinstance(raw, dict):
        raw = {}
    meta = tool_payload.get('_meta')
    if not isinstance(meta, dict):
        meta = tool_payload.get('meta')
    if not isinstance(meta, dict):
        meta = {}
    normalized: dict[str, Any] = {}
    for key in ('readOnlyHint', 'destructiveHint', 'openWorldHint', 'alwaysLoad'):
        if key in raw:
            normalized[key] = bool(raw.get(key))
    if 'anthropic/alwaysLoad' in meta:
        normalized['alwaysLoad'] = bool(meta.get('anthropic/alwaysLoad'))
    search_hint = raw.get('searchHint')
    if not isinstance(search_hint, str) or not search_hint.strip():
        search_hint = meta.get('anthropic/searchHint')
    if isinstance(search_hint, str) and search_hint.strip():
        normalized['searchHint'] = search_hint.strip()
    return normalized


def _mcp_dynamic_tool_detail(*, server_name: str, mcp_tool_name: str, annotations: dict[str, Any]) -> str:
    detail = f'Dynamic MCP tool compiled from {server_name}.{mcp_tool_name}.'
    flags = [
        key
        for key in ('readOnlyHint', 'destructiveHint', 'openWorldHint', 'alwaysLoad')
        if bool(annotations.get(key, False))
    ]
    if flags:
        detail = f'{detail} MCP annotations: {", ".join(flags)}.'
    return detail


def _mcp_dynamic_tool_search_keywords(
    *,
    server_name: str,
    mcp_tool_name: str,
    annotations: dict[str, Any],
) -> tuple[str, ...]:
    keywords = ['mcp', 'tool', 'dynamic', server_name, mcp_tool_name]
    if annotations.get('readOnlyHint'):
        keywords.append('read_only')
    if annotations.get('destructiveHint'):
        keywords.append('destructive')
    if annotations.get('openWorldHint'):
        keywords.append('open_world')
    if annotations.get('alwaysLoad'):
        keywords.append('always_load')
    return tuple(keywords)


def _normalize_input_schema(tool_payload: dict[str, Any]) -> dict[str, Any]:
    raw_schema = tool_payload.get('inputSchema', tool_payload.get('input_schema'))
    if not isinstance(raw_schema, dict):
        return {'type': 'object', 'properties': {}, 'additionalProperties': True}
    schema = dict(raw_schema)
    if schema.get('type') != 'object':
        schema['type'] = 'object'
    if not isinstance(schema.get('properties'), dict):
        schema['properties'] = {}
    return schema


def _identifier_part(value: str) -> str:
    normalized = re.sub(r'[^0-9A-Za-z_]+', '_', str(value or '').strip()).strip('_').lower()
    return normalized or f'unnamed_{_short_hash(value)}'


def _short_hash(value: str) -> str:
    return hashlib.sha256(str(value).encode('utf-8')).hexdigest()[:8]


def _json_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    ).hexdigest()[:16]


def _coerce_command(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _server_has_connection_config(server: dict[str, Any]) -> bool:
    transport = normalize_mcp_transport(server.get('transport', server.get('type', 'stdio')))
    if transport == 'stdio':
        return bool(_coerce_command(server.get('command')))
    if transport in {'http', 'sse', 'ws'}:
        return bool(str(server.get('url', '') or '').strip())
    return False


def _append_mcp_discovery_event(
    runtime_state: Any,
    *,
    server_name: str,
    status: str,
    summary: str,
) -> None:
    events = getattr(runtime_state, 'events', None)
    if isinstance(events, list):
        events.append(
            {
                'server_name': server_name,
                'event': 'dynamic_tools_list_completed' if status == 'completed' else 'dynamic_tools_list_failed',
                'status': status,
                'summary': summary,
            }
        )


def _sync_mcp_runtime_state_from_manager(runtime_state: Any, manager: Any) -> None:
    apply_mcp_manager_events(runtime_state, manager)


def _server_capability_is_stale(runtime_state: Any, *, server_name: str, capability: str) -> bool:
    stale = getattr(runtime_state, 'stale_capabilities', {})
    if not isinstance(stale, dict):
        return False
    return capability in list(stale.get(server_name, []))


def _clear_stale_capability(runtime_state: Any, *, server_name: str, capability: str) -> None:
    stale = getattr(runtime_state, 'stale_capabilities', {})
    if not isinstance(stale, dict):
        return
    current = [item for item in list(stale.get(server_name, [])) if str(item) != capability]
    if current:
        stale[server_name] = current
    elif server_name in stale:
        del stale[server_name]
