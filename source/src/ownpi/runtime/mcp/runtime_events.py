from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from typing import Any

from ownpi.runtime.events import now_utc_iso
from ownpi.runtime.limits.projection import (
    MAX_MCP_LIVE_REFRESH_EVENTS,
    MAX_MCP_SERVER_CAPABILITY_HISTORY,
)
from ownpi.runtime.lifecycle_contracts import (
    RuntimeArtifactLifecycleRecord,
    artifact_lifecycle_id,
    record_lifecycle_transition,
    stable_lifecycle_result_hash,
)
from ownpi.runtime.runtime_event_writer import append_event_payload

SUPPORTED_MCP_TRANSPORTS = {'http', 'sse', 'stdio', 'ws'}
DECLARED_MCP_TRANSPORTS = {'stdio', 'http', 'sse', 'ws', 'websocket'}
@dataclass(frozen=True)
class MCPAppStateRefreshEvent:
    event: str
    server_name: str
    capability: str
    refresh_generation: int
    refresh_required: bool
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            'event': self.event,
            'server_name': self.server_name,
            'capability': self.capability,
            'refresh_generation': self.refresh_generation,
            'refresh_required': bool(self.refresh_required),
            'payload': dict(self.payload),
        }


class MCPAppStateRefreshBus:
    def __init__(self, *, max_events: int = MAX_MCP_LIVE_REFRESH_EVENTS) -> None:
        self._max_events = max(1, int(max_events))
        self._lock = RLock()
        self._events: list[MCPAppStateRefreshEvent] = []
        self._subscribers: list[Callable[[MCPAppStateRefreshEvent], None]] = []
        self._callback_errors: list[dict[str, Any]] = []

    def subscribe(self, callback: Callable[[MCPAppStateRefreshEvent], None]) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                self._subscribers = [item for item in self._subscribers if item is not callback]

        return unsubscribe

    def emit(self, event: MCPAppStateRefreshEvent) -> None:
        with self._lock:
            self._events.append(event)
            del self._events[:-self._max_events]
            subscribers = list(self._subscribers)
        for callback in subscribers:
            try:
                callback(event)
            except Exception as exc:  # pragma: no cover - defensive extension boundary
                with self._lock:
                    self._callback_errors.append(
                        {
                            'event': event.event,
                            'server_name': event.server_name,
                            'capability': event.capability,
                            'error': str(exc),
                        }
                    )
                    del self._callback_errors[:-self._max_events]

    def drain(self) -> list[dict[str, Any]]:
        with self._lock:
            events = [item.to_dict() for item in self._events]
            self._events.clear()
        return events

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                'event_count': len(self._events),
                'events': [item.to_dict() for item in self._events],
                'subscriber_count': len(self._subscribers),
                'callback_errors': [dict(item) for item in self._callback_errors],
            }


default_mcp_app_state_refresh_bus = MCPAppStateRefreshBus()


def normalize_mcp_transport(value: Any) -> str:
    text = str(value or 'stdio').strip().lower()
    if text == 'websocket':
        return 'ws'
    return text or 'stdio'


def is_supported_mcp_transport(value: Any) -> bool:
    return normalize_mcp_transport(value) in SUPPORTED_MCP_TRANSPORTS


def unsupported_mcp_transport_reason(server: dict[str, Any]) -> str | None:
    transport = normalize_mcp_transport(server.get('transport', server.get('type', 'stdio')))
    if transport in SUPPORTED_MCP_TRANSPORTS:
        return None
    server_name = str(server.get('name', '') or '<unnamed>').strip()
    if transport in DECLARED_MCP_TRANSPORTS:
        return (
            f'MCP server {server_name} declares transport {transport}; this runtime currently supports '
            f'{", ".join(sorted(SUPPORTED_MCP_TRANSPORTS))}.'
        )
    return (
        f'MCP server {server_name} declares unknown transport {transport}; this runtime currently supports '
        f'{", ".join(sorted(SUPPORTED_MCP_TRANSPORTS))}.'
    )


def record_unsupported_mcp_transport(
    runtime_state: Any,
    *,
    server_name: str,
    transport: Any,
    reason: str | None = None,
    capability: str | None = None,
) -> dict[str, Any]:
    normalized_transport = normalize_mcp_transport(transport)
    summary = reason or (
        f'MCP server {server_name or "<unnamed>"} declares transport {normalized_transport}; '
        f'this runtime currently supports {", ".join(sorted(SUPPORTED_MCP_TRANSPORTS))}.'
    )
    payload = {
        'event': 'mcp_transport_unsupported',
        'server_name': server_name,
        'transport': normalized_transport,
        'status': 'unsupported',
        'supported_transports': sorted(SUPPORTED_MCP_TRANSPORTS),
        'declared_transports': sorted(DECLARED_MCP_TRANSPORTS),
        'unsupported_policy': 'record_status_and_skip',
        'summary': summary,
    }
    if capability:
        payload['capability'] = capability
    append_event_payload(getattr(runtime_state, 'events', None), payload)
    connection_status = getattr(runtime_state, 'connection_status', None)
    if isinstance(connection_status, dict) and server_name:
        connection_status[server_name] = {
            'status': 'unsupported',
            'transport': normalized_transport,
            'supported': False,
            'supported_transports': sorted(SUPPORTED_MCP_TRANSPORTS),
            'unsupported_policy': 'record_status_and_skip',
            'reason': summary,
        }
    if server_name:
        update_mcp_server_connection_status(
            runtime_state,
            server_name=server_name,
            status='unsupported',
            transport=normalized_transport,
            supported=False,
            capability=capability or '',
            reason=summary,
        )
    return payload


def apply_mcp_manager_events(
    runtime_state: Any,
    manager: Any,
    *,
    extra_event_fields: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    drain = getattr(manager, 'drain_events', None)
    events = drain() if callable(drain) else []
    server_name = str(getattr(manager, 'server_name', '') or '').strip()
    applied: list[dict[str, Any]] = []
    saw_needs_auth = False
    for event in events:
        if not isinstance(event, dict):
            continue
        payload = dict(event)
        if extra_event_fields:
            payload.update(dict(extra_event_fields))
        state_store = payload.pop('state_store', None)
        applied.append(payload)
        _append_runtime_event(runtime_state, payload)
        if state_store is not None and hasattr(state_store, 'record_mcp_mutation'):
            state_store.record_mcp_mutation(
                event_name=_mcp_runtime_event_name(payload),
                status=str(payload.get('status', 'recorded') or 'recorded'),
                mutation_kind=_mcp_runtime_mutation_kind(payload),
                stage=str(payload.get('stage', '') or ''),
                attempt=int(payload.get('attempt', 0) or 0),
                continuation_index=int(payload.get('continuation_index', 0) or 0),
                payload=payload,
            )
        event_server = str(payload.get('server_name', '') or server_name).strip()
        if str(payload.get('status', '') or '') == 'needs-auth' and event_server:
            saw_needs_auth = True
            update_mcp_server_connection_status(
                runtime_state,
                server_name=event_server,
                state_store=state_store,
                status='needs-auth',
                transport=str(payload.get('transport', getattr(manager, 'transport', 'stdio')) or 'stdio'),
                supported=True,
                persistent=bool(getattr(manager, 'persistent', False)),
                reason=str(payload.get('error', payload.get('reason', '')) or ''),
                refresh_generation=int(getattr(runtime_state, 'refresh_generation', 0) or 0),
            )
        stale_capability = str(payload.get('stale_capability', '') or '').strip()
        if stale_capability and event_server:
            _mark_stale_capability(runtime_state, server_name=event_server, capability=stale_capability)
            _invalidate_capability_cache(runtime_state, server_name=event_server, capability=stale_capability)
            _mark_server_capability_stale(runtime_state, server_name=event_server, capability=stale_capability)
            _record_live_refresh_event(
                runtime_state,
                server_name=event_server,
                capability=stale_capability,
                source_event=payload,
            )
    if server_name and not saw_needs_auth:
        persistent = bool(getattr(manager, 'persistent', False))
        owner_run_id = str(getattr(manager, 'run_id', '') or '').strip()
        connection_scope = 'run' if persistent and owner_run_id else ('app' if persistent else 'request')
        update_mcp_server_connection_status(
            runtime_state,
            server_name=server_name,
            state_store=(dict(extra_event_fields).get('state_store') if isinstance(extra_event_fields, dict) else None),
            status='connected',
            transport=str(getattr(manager, 'transport', 'stdio') or 'stdio'),
            supported=True,
            persistent=persistent,
            connection_scope=connection_scope,
            shared_connection=bool(persistent and not owner_run_id),
            owner_run_id=owner_run_id,
            refresh_generation=int(getattr(runtime_state, 'refresh_generation', 0) or 0),
        )
    return applied


def update_mcp_server_connection_status(
    runtime_state: Any,
    *,
    state_store: Any | None = None,
    server_name: str,
    status: str,
    transport: str = 'stdio',
    supported: bool = True,
    persistent: bool | None = None,
    connection_scope: str | None = None,
    shared_connection: bool | None = None,
    owner_run_id: str = '',
    capability: str = '',
    reason: str = '',
    error: str = '',
    refresh_generation: int | None = None,
) -> None:
    server = _server_capability_entry(runtime_state, server_name=server_name)
    if not server:
        return
    payload = {
        'status': status,
        'transport': normalize_mcp_transport(transport),
        'supported': bool(supported),
        'reason': reason,
        'error': error,
        'capability': capability,
        'refresh_generation': (
            int(refresh_generation)
            if refresh_generation is not None
            else int(getattr(runtime_state, 'refresh_generation', 0) or 0)
        ),
    }
    if persistent is not None:
        payload['persistent'] = bool(persistent)
    if connection_scope is not None:
        payload['connection_scope'] = str(connection_scope)
    if shared_connection is not None:
        payload['shared_connection'] = bool(shared_connection)
    if owner_run_id:
        payload['owner_run_id'] = str(owner_run_id)
    if not supported:
        payload['supported_transports'] = sorted(SUPPORTED_MCP_TRANSPORTS)
        payload['declared_transports'] = sorted(DECLARED_MCP_TRANSPORTS)
        payload['unsupported_policy'] = 'record_status_and_skip'
    clean_payload = {key: value for key, value in payload.items() if value not in ('', None)}
    server['transport'] = clean_payload.get('transport', 'stdio')
    server['connection'] = clean_payload
    connection_status = getattr(runtime_state, 'connection_status', None)
    if isinstance(connection_status, dict):
        connection_status[server_name] = dict(clean_payload)
    _record_mcp_server_lifecycle(
        runtime_state,
        state_store=state_store,
        server_name=server_name,
        status=_mcp_lifecycle_status(clean_payload),
        generation=int(clean_payload.get('refresh_generation', 0) or 0),
        metadata=clean_payload,
    )


def record_mcp_capability_snapshot(
    runtime_state: Any,
    *,
    server_name: str,
    capability: str,
    items: list[dict[str, Any]],
    status: str = 'available',
    source: str = '',
) -> None:
    normalized_capability = str(capability or '').strip()
    if normalized_capability not in {
        'tools',
        'prompts',
        'resources',
        'resource_templates',
    }:
        return
    server = _server_capability_entry(runtime_state, server_name=server_name)
    if not server:
        return
    effective_status = status
    refresh_required = False
    stale = getattr(runtime_state, 'stale_capabilities', None)
    if isinstance(stale, dict) and normalized_capability in list(stale.get(server_name, [])):
        effective_status = 'stale'
        refresh_required = True
    names = [_capability_item_name(item, capability=normalized_capability) for item in items]
    names = [item for item in names if item]
    snapshot = {
        'status': effective_status,
        'count': len(items),
        'names': names,
        'source': source,
        'refresh_generation': int(getattr(runtime_state, 'refresh_generation', 0) or 0),
        'generation': _next_capability_generation(
            runtime_state,
            server_name=server_name,
            capability=normalized_capability,
        ),
        'result_hash': stable_lifecycle_result_hash({'items': items, 'names': names}),
    }
    if refresh_required:
        snapshot['refresh_required'] = True
    server['capabilities'][normalized_capability] = snapshot
    _record_mcp_capability_refresh_result(
        runtime_state,
        server_name=server_name,
        capability=normalized_capability,
        status=effective_status,
        generation=int(snapshot['generation']),
        result_hash=str(snapshot['result_hash']),
        source=source,
        metadata={'count': len(items), 'names': names},
    )
    server.setdefault('capability_history', []).append(
        {
            'capability': normalized_capability,
            'status': effective_status,
            'count': len(items),
            'source': source,
            'refresh_generation': int(getattr(runtime_state, 'refresh_generation', 0) or 0),
        }
    )
    del server['capability_history'][:-MAX_MCP_SERVER_CAPABILITY_HISTORY]


def record_mcp_capability_error(
    runtime_state: Any,
    *,
    server_name: str,
    capability: str,
    error: str,
    source: str = '',
) -> None:
    normalized_capability = str(capability or '').strip()
    if normalized_capability not in {
        'tools',
        'prompts',
        'resources',
        'resource_templates',
    }:
        return
    server = _server_capability_entry(runtime_state, server_name=server_name)
    if not server:
        return
    server['capabilities'][normalized_capability] = {
        'status': 'failed',
        'count': 0,
        'names': [],
        'source': source,
        'error': error,
        'refresh_generation': int(getattr(runtime_state, 'refresh_generation', 0) or 0),
    }
    _record_mcp_capability_refresh_result(
        runtime_state,
        server_name=server_name,
        capability=normalized_capability,
        status='failed',
        generation=int(getattr(runtime_state, 'refresh_generation', 0) or 0),
        result_hash=stable_lifecycle_result_hash({'error': error}),
        source=source,
        error_summary=error,
    )
    connection_status = getattr(runtime_state, 'connection_status', None)
    current_connection = (
        connection_status.get(server_name, {})
        if isinstance(connection_status, dict)
        else {}
    )
    if str(current_connection.get('status', '') or '') != 'needs-auth':
        update_mcp_server_connection_status(
            runtime_state,
            server_name=server_name,
            status='error',
            transport=str(server.get('transport', 'stdio') or 'stdio'),
            supported=True,
            capability=normalized_capability,
            error=error,
        )


def _append_runtime_event(runtime_state: Any, payload: dict[str, Any]) -> None:
    append_event_payload(getattr(runtime_state, 'events', None), payload)


def _mcp_runtime_event_name(payload: dict[str, Any]) -> str:
    event = str(payload.get('event', '') or payload.get('method', '') or 'event').strip()
    normalized = event.replace('/', '_').replace('-', '_').strip('_') or 'event'
    return normalized if normalized.startswith('mcp_') else f'mcp_{normalized}'


def _mcp_runtime_mutation_kind(payload: dict[str, Any]) -> str:
    event = str(payload.get('event', '') or '').strip()
    status = str(payload.get('status', '') or '').strip()
    if str(payload.get('stale_capability', '') or '').strip():
        return 'cache_refreshed'
    if event == 'mcp_capability_list_changed' or 'list_changed' in event:
        return 'cache_refreshed'
    if status in {'needs-auth', 'unsupported'}:
        return 'state_delta'
    if status in {'failed', 'error'}:
        return 'result_recorded'
    if 'refresh' in event:
        return 'cache_refreshed'
    return 'state_delta'


def _mark_stale_capability(runtime_state: Any, *, server_name: str, capability: str) -> None:
    stale = getattr(runtime_state, 'stale_capabilities', None)
    if not isinstance(stale, dict):
        return
    current = list(stale.get(server_name, []))
    if capability not in current:
        current.append(capability)
    stale[server_name] = current


def _invalidate_capability_cache(runtime_state: Any, *, server_name: str, capability: str) -> None:
    if capability == 'tools':
        getattr(runtime_state, 'tool_cache', {}).pop(server_name, None)
        return
    if capability == 'prompts':
        getattr(runtime_state, 'prompt_cache', {}).pop(server_name, None)
        return
    if capability != 'resources':
        return
    cache = getattr(runtime_state, 'resource_cache', {})
    if isinstance(cache, dict):
        for key, value in list(cache.items()):
            if isinstance(value, dict) and str(value.get('server_name', '') or '') == server_name:
                del cache[key]


def _record_live_refresh_event(
    runtime_state: Any,
    *,
    server_name: str,
    capability: str,
    source_event: dict[str, Any],
) -> None:
    generation = int(getattr(runtime_state, 'refresh_generation', 0) or 0) + 1
    setattr(runtime_state, 'refresh_generation', generation)
    payload = {
        'event': 'mcp_capability_list_changed',
        'server_name': server_name,
        'capability': capability,
        'status': 'refresh_required',
        'refresh_required': True,
        'refresh_generation': generation,
        'source_event': dict(source_event),
    }
    live_events = getattr(runtime_state, 'live_refresh_events', None)
    if isinstance(live_events, list):
        live_events.append(dict(payload))
        del live_events[:-MAX_MCP_LIVE_REFRESH_EVENTS]
    _append_runtime_event(runtime_state, payload)
    _record_mcp_capability_refresh_request(
        runtime_state,
        server_name=server_name,
        capability=capability,
        generation=generation,
        source_event=source_event,
    )
    default_mcp_app_state_refresh_bus.emit(
        MCPAppStateRefreshEvent(
            event='mcp_capability_list_changed',
            server_name=server_name,
            capability=capability,
            refresh_generation=generation,
            refresh_required=True,
            payload=dict(payload),
        )
    )


def _server_capability_entry(runtime_state: Any, *, server_name: str) -> dict[str, Any]:
    normalized_server = str(server_name or '').strip()
    if not normalized_server:
        return {}
    registry = getattr(runtime_state, 'server_capabilities', None)
    if not isinstance(registry, dict):
        return {}
    server = registry.get(normalized_server)
    if not isinstance(server, dict):
        server = {
            'server_name': normalized_server,
            'transport': 'stdio',
            'connection': {},
            'capabilities': {},
            'capability_history': [],
        }
        registry[normalized_server] = server
    server.setdefault('server_name', normalized_server)
    server.setdefault('connection', {})
    server.setdefault('capabilities', {})
    server.setdefault('capability_history', [])
    return server


def _mark_server_capability_stale(runtime_state: Any, *, server_name: str, capability: str) -> None:
    server = _server_capability_entry(runtime_state, server_name=server_name)
    if not server:
        return
    capabilities = server.setdefault('capabilities', {})
    current = capabilities.get(capability)
    if not isinstance(current, dict):
        current = {'count': 0, 'names': []}
    current = dict(current)
    current['status'] = 'stale'
    current['refresh_required'] = True
    current['refresh_generation'] = int(getattr(runtime_state, 'refresh_generation', 0) or 0)
    capabilities[capability] = current
    _record_mcp_server_lifecycle(
        runtime_state,
        server_name=server_name,
        status='stale',
        generation=int(getattr(runtime_state, 'refresh_generation', 0) or 0),
        metadata={'capability': capability, 'refresh_required': True},
    )


def _capability_item_name(item: dict[str, Any], *, capability: str) -> str:
    if capability == 'resources':
        return str(item.get('uri') or item.get('resource_id') or item.get('name') or '').strip()
    return str(item.get('name') or '').strip()


def _mcp_lifecycle_status(payload: dict[str, Any]) -> str:
    status = str(payload.get('status', '') or '').strip().casefold()
    if status in {'connected', 'ready', 'available'}:
        return 'ready'
    if status in {'connecting', 'starting'}:
        return 'connecting'
    if status in {'unsupported', 'disabled'}:
        return 'disabled'
    if status in {'error', 'failed', 'needs-auth'}:
        return 'failed'
    return status or 'configured'


def _record_mcp_server_lifecycle(
    runtime_state: Any,
    *,
    state_store: Any | None = None,
    server_name: str,
    status: str,
    generation: int = 0,
    metadata: dict[str, Any] | None = None,
) -> None:
    lifecycle = getattr(runtime_state, 'server_lifecycle', None)
    if not isinstance(lifecycle, dict):
        return
    record = RuntimeArtifactLifecycleRecord(
        artifact_id=artifact_lifecycle_id('mcp', server_name),
        artifact_kind='mcp',
        name=server_name,
        status=status,
        generation=max(0, int(generation or 0)),
        source='mcp_runtime',
        result_hash=stable_lifecycle_result_hash(metadata or {}),
        updated_at=now_utc_iso(),
        metadata=dict(metadata or {}),
    )
    lifecycle[server_name] = record.to_dict()
    if state_store is not None:
        record_lifecycle_transition(state_store, record, {'mcp_runtime_artifact': record.to_dict()})


def _capability_key(*, server_name: str, capability: str) -> str:
    return f'{server_name}:{capability}'


def _next_capability_generation(runtime_state: Any, *, server_name: str, capability: str) -> int:
    generations = getattr(runtime_state, 'capability_generations', None)
    key = _capability_key(server_name=server_name, capability=capability)
    if not isinstance(generations, dict):
        return int(getattr(runtime_state, 'refresh_generation', 0) or 0)
    generation = int(generations.get(key, 0) or 0) + 1
    generations[key] = generation
    return generation


def _record_mcp_capability_refresh_request(
    runtime_state: Any,
    *,
    server_name: str,
    capability: str,
    generation: int,
    source_event: dict[str, Any],
) -> None:
    requests = getattr(runtime_state, 'capability_refresh_requests', None)
    if not isinstance(requests, list):
        return
    payload = {
        'server_name': server_name,
        'capability': capability,
        'status': 'refreshing',
        'generation': int(generation or 0),
        'source_event': dict(source_event),
        'created_at': now_utc_iso(),
    }
    requests.append(payload)
    del requests[:-64]


def _record_mcp_capability_refresh_result(
    runtime_state: Any,
    *,
    server_name: str,
    capability: str,
    status: str,
    generation: int,
    result_hash: str,
    source: str,
    metadata: dict[str, Any] | None = None,
    error_summary: str = '',
) -> None:
    results = getattr(runtime_state, 'capability_refresh_results', None)
    if not isinstance(results, list):
        return
    payload = {
        'server_name': server_name,
        'capability': capability,
        'status': status,
        'generation': int(generation or 0),
        'result_hash': result_hash,
        'source': source,
        'error_summary': error_summary,
        'metadata': dict(metadata or {}),
        'completed_at': now_utc_iso(),
    }
    results.append(payload)
    del results[:-64]
