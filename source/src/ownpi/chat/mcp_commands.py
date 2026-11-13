from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import re
import shlex
from pathlib import Path
from typing import Any

from ownpi.chat.command_registry import CommandDefinition, CommandRegistry, CommandResult
from ownpi.runtime.mcp.client_manager import MCPClientError, build_mcp_manager
from ownpi.runtime.permissions.models import EffectivePermissions
from ownpi.runtime.permissions.runtime_config import compile_runtime_permissions
from ownpi.runtime.mcp.runtime_events import (
    apply_mcp_manager_events,
    is_supported_mcp_transport,
    normalize_mcp_transport,
    record_mcp_capability_error,
    record_mcp_capability_snapshot,
    unsupported_mcp_transport_reason,
)
from ownpi.schemas.runtime import MCPServerConfig, UserConfig


MCP_COMMAND_SOURCE_PREFIX = 'mcp:'


@dataclass(frozen=True)
class MCPCommandRegistration:
    command_name: str
    server_name: str
    prompt_name: str
    registered: bool
    reason: str = ''


def sync_mcp_prompt_commands(
    registry: CommandRegistry,
    user_config: UserConfig,
    *,
    cache: dict[str, list[dict[str, Any]]] | None = None,
    runtime_state: Any | None = None,
    project_root: Path | None = None,
) -> tuple[MCPCommandRegistration, ...]:
    registry.unregister_by_source_prefix(MCP_COMMAND_SOURCE_PREFIX)
    prompt_cache = cache if cache is not None else {}
    canonical_permissions = compile_runtime_permissions(
        user_config.runtime.permissions,
        project_root=(
            project_root
            or Path(user_config.runtime.workspace_dir).expanduser().resolve()
        ),
    )
    registrations: list[MCPCommandRegistration] = []
    for server in user_config.runtime.mcp_servers:
        server_name = str(server.name or '').strip()
        if not server_name:
            continue
        if not is_supported_mcp_transport(server.transport):
            registrations.append(
                MCPCommandRegistration(
                    command_name=f'/mcp-{_identifier_part(server_name)}',
                    server_name=server_name,
                    prompt_name='',
                    registered=False,
                    reason=unsupported_mcp_transport_reason(server.to_dict()) or 'unsupported MCP transport',
                )
            )
            continue
        try:
            prompts = _load_prompt_list(
                server,
                cache=prompt_cache,
                runtime_state=runtime_state,
                effective_permissions=canonical_permissions,
            )
        except Exception as exc:
            if runtime_state is not None:
                record_mcp_capability_error(
                    runtime_state,
                    server_name=server_name,
                    capability='prompts',
                    error=str(exc),
                    source='mcp_prompt_command_sync',
                )
            registrations.append(
                MCPCommandRegistration(
                    command_name=f'/mcp-{_identifier_part(server_name)}',
                    server_name=server_name,
                    prompt_name='',
                    registered=False,
                    reason=str(exc),
                )
            )
            continue
        for prompt in prompts:
            prompt_name = str(prompt.get('name', '') or '').strip()
            if not prompt_name:
                continue
            command_name = _mcp_prompt_command_name(server_name, prompt_name)
            try:
                definition = create_mcp_prompt_command(
                    server,
                    dict(prompt),
                    command_name=command_name,
                    cache=prompt_cache,
                    effective_permissions=canonical_permissions,
                )
                registry.register(definition)
            except Exception as exc:
                registrations.append(
                    MCPCommandRegistration(
                        command_name=command_name,
                        server_name=server_name,
                        prompt_name=prompt_name,
                        registered=False,
                        reason=str(exc),
                    )
                )
                continue
            registrations.append(
                MCPCommandRegistration(
                    command_name=command_name,
                    server_name=server_name,
                    prompt_name=prompt_name,
                    registered=True,
                )
            )
    return tuple(registrations)


def create_mcp_prompt_command(
    server: MCPServerConfig,
    prompt: dict[str, Any],
    *,
    command_name: str | None = None,
    cache: dict[str, list[dict[str, Any]]] | None = None,
    effective_permissions: EffectivePermissions,
) -> CommandDefinition:
    server_name = str(server.name or '').strip()
    prompt_name = str(prompt.get('name', '') or '').strip()
    if not server_name:
        raise ValueError('MCP prompt command requires a server name')
    if not prompt_name:
        raise ValueError('MCP prompt command requires a prompt name')
    name = command_name or _mcp_prompt_command_name(server_name, prompt_name)
    description = str(prompt.get('description', '') or '').strip()
    if not description:
        description = f'Invoke MCP prompt {prompt_name} from {server_name}.'

    def _execute(_context, message: str) -> CommandResult:
        arguments = _parse_command_arguments(message)
        try:
            manager = build_mcp_manager(
                server.to_dict(),
                elicitation_handler=getattr(_context, 'mcp_elicitation_handler', None),
                hook_engine=getattr(_context, 'hook_engine', None),
                effective_permissions=_command_effective_permissions(
                    _context,
                    fallback=effective_permissions,
                ),
            )
            state_store = getattr(_context, 'state_store', None)
            workflow_state = getattr(state_store, 'state', None)
            result = asyncio.run(manager.get_prompt(
                prompt_name=prompt_name,
                arguments=arguments,
            ))
            _invalidate_prompt_cache_from_manager_events(
                manager,
                server_name=server_name,
                cache=cache,
                context_state=getattr(_context, 'state', None),
                runtime_state=_runtime_state_from_command_context(_context, workflow_state),
            )
        except (MCPClientError, OSError, ValueError) as exc:
            return CommandResult(
                handled=True,
                message=f'MCP prompt command failed: {exc}',
                metadata={
                    'source': 'mcp_prompt_command',
                    'server_name': server_name,
                    'prompt_name': prompt_name,
                    'ok': False,
                },
            )
        return CommandResult(
            handled=True,
            prompt=_render_mcp_prompt(
                server_name=server_name,
                prompt_name=prompt_name,
                arguments=arguments,
                prompt_result=result,
            ),
            metadata={
                'source': 'mcp_prompt_command',
                'server_name': server_name,
                'prompt_name': prompt_name,
                'arguments': dict(arguments),
                'ok': True,
            },
        )

    return CommandDefinition(
        name=name,
        description=description,
        source=f'{MCP_COMMAND_SOURCE_PREFIX}{server_name}:{prompt_name}',
        kind='mcp',
        context_resources=(f'mcp:{server_name}',),
        metadata={
            'server_name': server_name,
            'prompt_name': prompt_name,
            'prompt': dict(prompt),
        },
        execute=_execute,
    )


def _load_prompt_list(
    server: MCPServerConfig,
    *,
    cache: dict[str, list[dict[str, Any]]],
    runtime_state: Any | None = None,
    effective_permissions: EffectivePermissions,
) -> list[dict[str, Any]]:
    server_name = str(server.name or '').strip()
    manager = (
        build_mcp_manager(
            server.to_dict(),
            effective_permissions=effective_permissions,
        )
        if _server_has_connection_config(server)
        else None
    )
    if manager is not None:
        _invalidate_prompt_cache_from_manager_events(
            manager,
            server_name=server_name,
            cache=cache,
            runtime_state=runtime_state,
        )
    if server_name in cache:
        return [dict(item) for item in cache[server_name] if isinstance(item, dict)]
    if not _server_has_connection_config(server):
        cache[server_name] = []
        if runtime_state is not None:
            record_mcp_capability_snapshot(
                runtime_state,
                server_name=server_name,
                capability='prompts',
                items=[],
                source='mcp_prompt_command_sync',
            )
        return []
    manager = manager or build_mcp_manager(
        server.to_dict(),
        effective_permissions=effective_permissions,
    )
    result = asyncio.run(manager.list_prompts())
    _invalidate_prompt_cache_from_manager_events(
        manager,
        server_name=server_name,
        cache=cache,
        runtime_state=runtime_state,
    )
    prompts = [dict(item) for item in result.get('prompts', []) if isinstance(item, dict)]
    cache[server_name] = prompts
    if runtime_state is not None:
        record_mcp_capability_snapshot(
            runtime_state,
            server_name=server_name,
            capability='prompts',
            items=prompts,
            source='mcp_prompt_command_sync',
        )
    return [dict(item) for item in prompts]


def _server_has_connection_config(server: MCPServerConfig) -> bool:
    transport = normalize_mcp_transport(server.transport)
    if transport == 'stdio':
        return bool(server.command)
    if transport in {'http', 'sse', 'ws'}:
        return bool(str(server.url or '').strip())
    return False


def _invalidate_prompt_cache_from_manager_events(
    manager: Any,
    *,
    server_name: str,
    cache: dict[str, list[dict[str, Any]]] | None,
    context_state: dict[str, Any] | None = None,
    runtime_state: Any | None = None,
) -> None:
    events = (
        apply_mcp_manager_events(runtime_state, manager)
        if runtime_state is not None
        else (getattr(manager, 'drain_events', lambda: [])())
    )
    stale_prompts = any(
        isinstance(event, dict)
        and str(event.get('stale_capability', '') or '') == 'prompts'
        and str(event.get('server_name', '') or server_name) == server_name
        for event in events
    )
    if not stale_prompts:
        return
    if cache is not None:
        cache.pop(server_name, None)
    if isinstance(context_state, dict):
        stale_servers = context_state.setdefault('mcp_prompt_stale_servers', [])
        if isinstance(stale_servers, list) and server_name not in stale_servers:
            stale_servers.append(server_name)


def _runtime_state_from_command_context(context: Any, workflow_state: Any) -> Any | None:
    runtime_state = getattr(workflow_state, 'mcp_runtime_state', None)
    if runtime_state is not None:
        return runtime_state
    context_state = getattr(context, 'state', None)
    if isinstance(context_state, dict):
        candidate = context_state.get('mcp_runtime_state')
        if candidate is not None:
            return candidate
    return None


def _command_effective_permissions(
    context: Any,
    *,
    fallback: EffectivePermissions,
) -> EffectivePermissions:
    state_store = getattr(context, 'state_store', None)
    state = getattr(state_store, 'state', None)
    current = getattr(getattr(state, 'tool_state', None), 'effective_permissions', None)
    if isinstance(current, EffectivePermissions):
        return current
    user_config = getattr(context, 'user_config', None)
    project_root = getattr(context, 'project_root', None)
    if isinstance(user_config, UserConfig) and project_root is not None:
        return compile_runtime_permissions(
            user_config.runtime.permissions,
            project_root=Path(project_root).expanduser().resolve(),
        )
    return fallback


def _render_mcp_prompt(
    *,
    server_name: str,
    prompt_name: str,
    arguments: dict[str, Any],
    prompt_result: dict[str, Any],
) -> str:
    return '\n'.join(
        [
            '<mcp_prompt_invocation>',
            f'server: {server_name}',
            f'prompt: {prompt_name}',
            f'arguments: {json.dumps(arguments, ensure_ascii=False, sort_keys=True)}',
            '</mcp_prompt_invocation>',
            '',
            '<mcp_prompt_result>',
            json.dumps(prompt_result, ensure_ascii=False, indent=2, sort_keys=True),
            '</mcp_prompt_result>',
        ]
    )


def _parse_command_arguments(message: str) -> dict[str, Any]:
    raw = _command_arguments(message)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {'input': raw}
    if isinstance(parsed, dict):
        return dict(parsed)
    return {'input': parsed}


def _command_arguments(message: str) -> str:
    try:
        tokens = shlex.split(message)
    except ValueError:
        tokens = str(message or '').split()
    if len(tokens) <= 1:
        return ''
    return str(message or '')[len(tokens[0]) :].strip()


def _mcp_prompt_command_name(server_name: str, prompt_name: str) -> str:
    return f'/mcp-{_identifier_part(server_name)}-{_identifier_part(prompt_name)}'


def _identifier_part(value: str) -> str:
    normalized = re.sub(r'[^0-9A-Za-z_]+', '-', str(value or '').strip()).strip('-').lower()
    if normalized:
        return normalized
    return f'unnamed-{hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:8]}'
