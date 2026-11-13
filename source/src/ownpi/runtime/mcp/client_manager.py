from __future__ import annotations

import atexit
import asyncio
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import selectors
import subprocess
import time
from threading import Condition, Lock, RLock, Thread
from typing import Any, Callable, Literal, cast
from urllib.parse import urljoin

import httpx
import websockets

from ownpi.runtime.environment.process_capture import kill_process, popen_with_process_group
from ownpi.runtime.environment.owned_process_cleanup import reap_owned_process
from ownpi.runtime.mcp.async_wait import claim_lock
from ownpi.runtime.hooks.types import (
    ElicitationOutcome,
    ElicitationRequest,
    HookDeliveryBatch,
)
from ownpi.runtime.mcp.runtime_events import normalize_mcp_transport
from ownpi.runtime.mcp.selection import header_helper_argv, stdio_argv
from ownpi.runtime.mcp.oauth import mcp_authorization_header
from ownpi.runtime.environment.subprocess_env import build_subprocess_env
from ownpi.runtime.permissions.action_compiler import OperationEffects, compile_canonical_action
from ownpi.runtime.permissions.models import EffectivePermissions
from ownpi.runtime.permissions.orchestrator import PermissionOrchestrator
from ownpi.runtime.sandbox import SandboxCommandRequest, SandboxManager


class MCPClientError(RuntimeError):
    pass


MCPElicitationAction = Literal['accept', 'decline', 'cancel']


@dataclass(frozen=True)
class MCPElicitationRequest:
    server_name: str
    active_method: str
    request_id: Any
    method: str = 'elicitation/create'
    params: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            'server_name': self.server_name,
            'active_method': self.active_method,
            'request_id': self.request_id,
            'method': self.method,
            'params': dict(self.params or {}),
        }


@dataclass(frozen=True)
class MCPElicitationResponse:
    action: MCPElicitationAction = 'cancel'
    content: dict[str, Any] | None = None
    reason: str = ''

    def to_result(self) -> dict[str, Any]:
        result: dict[str, Any] = {'action': self.action}
        if self.action == 'accept' and isinstance(self.content, dict):
            result['content'] = dict(self.content)
        return result


MCPElicitationHandler = Callable[
    [MCPElicitationRequest],
    MCPElicitationResponse | dict[str, Any] | str | None,
]
HookDeliveryHandler = Callable[[HookDeliveryBatch], None]


class StdioMCPClientManager:
    def __init__(
        self,
        *,
        command: list[str],
        cwd: str | None,
        env: dict[str, str],
        timeout_seconds: int,
        run_id: str = '',
        server_name: str = '',
        elicitation_handler: MCPElicitationHandler | None = None,
        hook_engine: Any | None = None,
        persistent: bool = True,
        effective_permissions: EffectivePermissions | None = None,
        hook_delivery_handler: HookDeliveryHandler | None = None,
        hook_scope: dict[str, Any] | None = None,
    ) -> None:
        self.command = command
        self.cwd = cwd
        self.env = env
        self.timeout_seconds = timeout_seconds
        self.run_id = str(run_id or '')
        self.server_name = str(server_name or '')
        self.elicitation_handler = elicitation_handler
        self.hook_engine = hook_engine
        self.persistent = persistent
        self.effective_permissions = effective_permissions
        self.hook_delivery_handler = hook_delivery_handler
        self.hook_scope = dict(hook_scope or {})
        self.events: list[dict[str, Any]] = []
        self.transport = 'stdio'

    async def list_tools(self) -> dict[str, Any]:
        if self.persistent:
            return await self._persistent_request('tools/list', {})
        async with self._session() as session:
            await session.initialize()
            return await session.request('tools/list', {})

    async def list_resources(self) -> dict[str, Any]:
        if self.persistent:
            return await self._persistent_request('resources/list', {})
        async with self._session() as session:
            await session.initialize()
            return await session.request('resources/list', {})

    async def list_resource_templates(self, *, cursor: str | None = None) -> dict[str, Any]:
        params = {'cursor': cursor} if cursor else {}
        if self.persistent:
            return await self._persistent_request('resources/templates/list', params)
        async with self._session() as session:
            await session.initialize()
            return await session.request('resources/templates/list', params)

    async def list_prompts(self) -> dict[str, Any]:
        if self.persistent:
            return await self._persistent_request('prompts/list', {})
        async with self._session() as session:
            await session.initialize()
            return await session.request('prompts/list', {})

    async def get_prompt(self, *, prompt_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.persistent:
            return await self._persistent_request(
                'prompts/get',
                {'name': prompt_name, 'arguments': dict(arguments or {})},
            )
        async with self._session() as session:
            await session.initialize()
            return await session.request(
                'prompts/get',
                {'name': prompt_name, 'arguments': dict(arguments or {})},
            )

    async def read_resource(self, *, resource_uri: str) -> dict[str, Any]:
        if self.persistent:
            return await self._persistent_request('resources/read', {'uri': resource_uri})
        async with self._session() as session:
            await session.initialize()
            return await session.request('resources/read', {'uri': resource_uri})

    async def invoke_tool(self, *, tool_name: str, input: dict[str, Any]) -> dict[str, Any]:
        if self.persistent:
            return await self._persistent_request('tools/call', {'name': tool_name, 'arguments': input})
        async with self._session() as session:
            await session.initialize()
            return await session.request('tools/call', {'name': tool_name, 'arguments': input})

    def drain_events(self) -> list[dict[str, Any]]:
        events = [dict(item) for item in self.events]
        self.events.clear()
        if self.persistent:
            events.extend(_PERSISTENT_MCP_CONNECTIONS.drain_events(_persistent_connection_key(self)))
        return events

    def close(self) -> None:
        if self.persistent:
            _PERSISTENT_MCP_CONNECTIONS.close(_persistent_connection_key(self))

    async def _persistent_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        key = _persistent_connection_key(self)
        return await _PERSISTENT_MCP_CONNECTIONS.request(self, key=key, method=method, params=params)

    def _session(self) -> '_StdioMCPSession':
        process_env = build_subprocess_env(extra_env=self.env)
        command = _sandboxed_stdio_command(self, process_env=process_env)
        process = popen_with_process_group(
            command,
            cwd=self.cwd,
            env=process_env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return _StdioMCPSession(
            process=process,
            timeout_seconds=self.timeout_seconds,
            server_name=self.server_name,
            elicitation_handler=self.elicitation_handler,
            hook_engine=self.hook_engine,
            effective_permissions=self.effective_permissions,
            hook_delivery_handler=self.hook_delivery_handler,
            hook_scope=self.hook_scope,
            notification_handler=self._handle_notification,
            write_lock=RLock(),
        )

    def _handle_notification(self, payload: dict[str, Any]) -> None:
        method = str(payload.get('method', '') or '').strip()
        if not method:
            return
        event = {
            'server_name': self.server_name,
            'event': method,
            'status': 'notification',
            'created_at': time.time(),
        }
        capability = _stale_capability_from_notification(method)
        if capability:
            event['stale_capability'] = capability
        self.events.append(event)


class RemoteMCPClientManager:
    def __init__(
        self,
        *,
        url: str,
        headers: dict[str, str],
        timeout_seconds: int,
        transport: Literal['http', 'sse', 'ws'] = 'http',
        run_id: str = '',
        server_name: str = '',
        elicitation_handler: MCPElicitationHandler | None = None,
        hook_engine: Any | None = None,
        effective_permissions: EffectivePermissions | None = None,
        server_config: dict[str, Any] | None = None,
        hook_delivery_handler: HookDeliveryHandler | None = None,
        hook_scope: dict[str, Any] | None = None,
    ) -> None:
        self.url = str(url or '').strip()
        self.headers = dict(headers)
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self.run_id = str(run_id or '')
        self.server_name = str(server_name or '')
        self.elicitation_handler = elicitation_handler
        self.hook_engine = hook_engine
        self.effective_permissions = effective_permissions
        self.hook_delivery_handler = hook_delivery_handler
        self.hook_scope = dict(hook_scope or {})
        self.persistent = False
        self.events: list[dict[str, Any]] = []
        self._request_counter = 0
        self._initialized = False
        self._session_id = ''
        self._sse_endpoint = ''
        self._sse_task: asyncio.Task[None] | None = None
        self._sse_closed = False
        self._lock = RLock()
        self._ws_task: asyncio.Task[None] | None = None
        self._ws_connection: Any | None = None
        self._ws_responses: dict[Any, dict[str, Any]] = {}
        self._ws_condition = Condition(self._lock)
        self._ws_error: Exception | None = None
        self._server_config = dict(server_config or {})
        self._headers_ready = False
        if not self.url:
            raise MCPClientError(f'configured {self.transport} MCP server is missing url')

    async def list_tools(self) -> dict[str, Any]:
        return await self._request('tools/list', {})

    async def list_resources(self) -> dict[str, Any]:
        return await self._request('resources/list', {})

    async def list_resource_templates(self, *, cursor: str | None = None) -> dict[str, Any]:
        params = {'cursor': cursor} if cursor else {}
        return await self._request('resources/templates/list', params)

    async def list_prompts(self) -> dict[str, Any]:
        return await self._request('prompts/list', {})

    async def get_prompt(self, *, prompt_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request('prompts/get', {'name': prompt_name, 'arguments': dict(arguments or {})})

    async def read_resource(self, *, resource_uri: str) -> dict[str, Any]:
        return await self._request('resources/read', {'uri': resource_uri})

    async def invoke_tool(self, *, tool_name: str, input: dict[str, Any]) -> dict[str, Any]:
        return await self._request('tools/call', {'name': tool_name, 'arguments': dict(input)})

    def drain_events(self) -> list[dict[str, Any]]:
        events = [dict(item) for item in self.events]
        self.events.clear()
        return events

    async def close(self) -> None:
        self._sse_closed = True
        tasks = [task for task in (self._sse_task, self._ws_task)
                 if task is not None and not task.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._ws_connection is not None:
            await self._ws_connection.close()
            self._ws_connection = None
        self._sse_task = self._ws_task = None
        self._initialized = False
        self._session_id = ''
        self._sse_endpoint = ''
        self._ws_responses.clear()
        self._ws_error = None

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._sse_closed = False
        try:
            return await self._request_inner(method, params)
        except asyncio.CancelledError:
            cleanup = asyncio.create_task(self.close())
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not cleanup.cancelled() and cleanup.exception() is not None:
                import logging

                logging.getLogger(__name__).warning(
                    'Remote MCP cancellation cleanup failed: %s', cleanup.exception(),
                )
            raise

    async def _request_inner(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        _authorize_mcp_network_operation(
            effective_permissions=self.effective_permissions,
            endpoint=self.url,
            action=method,
            server_name=self.server_name,
        )
        if method != 'initialize' and not self._initialized:
            await self._request_inner(
                'initialize',
                {
                    'protocolVersion': '2025-03-26',
                    'capabilities': {},
                    'clientInfo': {'name': 'ownpi', 'version': '0.1'},
                },
            )
            self._initialized = True
            await self._notify_initialized()
        self._request_counter += 1
        request_id = self._request_counter
        payload = {'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': dict(params)}
        if self.transport == 'ws':
            return await self._ws_request(payload, request_id=request_id, method=method)
        return await self._http_like_request(payload, request_id=request_id, method=method)

    async def _http_like_request(self, payload: dict[str, Any], *, request_id: int, method: str) -> dict[str, Any]:
        headers = {
            'Accept': 'application/json, text/event-stream',
            'Content-Type': 'application/json',
            'User-Agent': f'ownpi/0.1 mcp-{self.transport}',
            **await self._headers(),
        }
        url = self._request_url()
        try:
            async with httpx.AsyncClient(trust_env=False) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
            if response.status_code == 401:
                self._record_needs_auth('remote MCP returned 401')
                raise MCPClientError(f'{self.transport} MCP server needs authentication')
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise MCPClientError(f'{self.transport.upper()} MCP request failed for {method}: {exc}') from exc
        response_payload = await self._response_payload(response, request_id=request_id, method=method)
        normalized = self._extract_jsonrpc_result(response_payload, request_id=request_id, method=method)
        if method == 'initialize':
            self._capture_remote_session(response, normalized)
        return normalized

    async def _response_payload(self, response: httpx.Response, *, request_id: int, method: str) -> Any:
        content_type = response.headers.get('content-type', '')
        if 'text/event-stream' in content_type:
            payloads = _parse_sse_json_messages(response.text)
            for payload in payloads:
                if isinstance(payload, dict) and await self._handle_remote_notification_or_request(payload, active_method=method):
                    continue
                if isinstance(payload, dict) and payload.get('id') == request_id:
                    return payload
            raise MCPClientError(f'{self.transport.upper()} MCP SSE response for {method} did not include request id {request_id}')
        try:
            return response.json()
        except ValueError as exc:
            raise MCPClientError(f'{self.transport.upper()} MCP response was not JSON for {method}: {exc}') from exc

    def _capture_remote_session(self, response: httpx.Response, result: dict[str, Any]) -> None:
        session_id = response.headers.get('mcp-session-id') or response.headers.get('Mcp-Session-Id') or ''
        if session_id:
            self._session_id = session_id
        endpoint = ''
        if isinstance(result.get('_meta'), dict):
            endpoint = str(result['_meta'].get('sse_endpoint', result['_meta'].get('sseEndpoint', '')) or '')
        if endpoint:
            self._sse_endpoint = urljoin(self.url, endpoint)
            self._start_sse_reader()

    async def _notify_initialized(self) -> None:
        if self.transport == 'ws':
            await self._ws_notify('notifications/initialized', {})
            return
        try:
            await self._http_like_request(
                {'jsonrpc': '2.0', 'method': 'notifications/initialized', 'params': {}},
                request_id=-1,
                method='notifications/initialized',
            )
        except MCPClientError:
            return

    async def _headers(self) -> dict[str, str]:
        if not self._headers_ready:
            self.headers.update(await _remote_mcp_headers(
                self._server_config, effective_permissions=self.effective_permissions,
            ))
            self._headers_ready = True
        headers = dict(self.headers)
        auth_header = await mcp_authorization_header(self._server_config, server_name=self.server_name)
        if auth_header and 'Authorization' not in headers:
            headers['Authorization'] = auth_header
        if self._session_id and 'Mcp-Session-Id' not in headers:
            headers['Mcp-Session-Id'] = self._session_id
        return headers

    def _request_url(self) -> str:
        return self.url

    def _start_sse_reader(self) -> None:
        if self.transport != 'http' or not self._sse_endpoint or self._sse_task is not None:
            return
        self._sse_task = asyncio.create_task(self._sse_reader_loop())

    async def _sse_reader_loop(self) -> None:
        _authorize_mcp_network_operation(
            effective_permissions=self.effective_permissions,
            endpoint=self._sse_endpoint,
            action='sse_stream',
            server_name=self.server_name,
        )
        while not self._sse_closed:
            try:
                async with httpx.AsyncClient(trust_env=False) as client, client.stream('GET', self._sse_endpoint, headers=await self._headers(), timeout=None) as response:
                    if response.status_code == 401:
                        self._record_needs_auth('remote MCP SSE stream returned 401')
                        return
                    response.raise_for_status()
                    buffer: list[str] = []
                    async for line in response.aiter_lines():
                        if self._sse_closed:
                            return
                        if line == '':
                            await self._handle_sse_event_lines(buffer)
                            buffer = []
                            continue
                        buffer.append(line)
            except httpx.HTTPError as exc:
                self._append_event('connection_error', status='failed', error=str(exc))
                await asyncio.sleep(0.2)
                return

    async def _handle_sse_event_lines(self, lines: list[str]) -> None:
        data_parts = [line[5:].strip() for line in lines if line.startswith('data:')]
        if not data_parts:
            return
        try:
            payload = json.loads('\n'.join(data_parts))
        except ValueError:
            return
        if isinstance(payload, dict):
            await self._handle_remote_notification_or_request(payload, active_method='sse')

    async def _ws_request(self, payload: dict[str, Any], *, request_id: int, method: str) -> dict[str, Any]:
        await self._ensure_ws_connection()
        assert self._ws_connection is not None
        await asyncio.wait_for(self._ws_connection.send(json.dumps(payload)), self.timeout_seconds)
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            if self._ws_error is not None:
                raise MCPClientError(f'WebSocket MCP reader failed: {self._ws_error}')
            response = self._ws_responses.pop(request_id, None)
            if response is not None:
                if response.get('error') is not None:
                    raise MCPClientError(str(response['error']))
                result = response.get('result')
                return dict(result) if isinstance(result, dict) else {'result': result}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MCPClientError(f'WebSocket MCP response timed out for {method}')
            await asyncio.sleep(min(0.01, remaining))

    async def _ws_notify(self, method: str, params: dict[str, Any]) -> None:
        _authorize_mcp_network_operation(
            effective_permissions=self.effective_permissions,
            endpoint=self.url,
            action=method,
            server_name=self.server_name,
        )
        await self._ensure_ws_connection()
        assert self._ws_connection is not None
        payload = {'jsonrpc': '2.0', 'method': method, 'params': params}
        await asyncio.wait_for(self._ws_connection.send(json.dumps(payload)), self.timeout_seconds)

    async def _ensure_ws_connection(self) -> None:
        if self.transport != 'ws':
            return
        if self._ws_connection is not None and self._ws_error is None:
            return
        _authorize_mcp_network_operation(
            effective_permissions=self.effective_permissions,
            endpoint=self.url,
            action='websocket_connect',
            server_name=self.server_name,
        )
        self._ws_connection = await websockets.connect(
            self.url, additional_headers=await self._headers() or None,
            open_timeout=self.timeout_seconds,
        )
        self._ws_task = asyncio.create_task(self._ws_connect_and_read())

    async def _ws_connect_and_read(self) -> None:
        connection = self._ws_connection
        try:
            async for message in connection:
                try:
                    payload = json.loads(message)
                except ValueError:
                    continue
                if not isinstance(payload, dict):
                    continue
                if await self._handle_remote_notification_or_request(payload, active_method='websocket'):
                    continue
                response_id = payload.get('id')
                with self._ws_condition:
                    self._ws_responses[response_id] = dict(payload)
                    self._ws_condition.notify_all()
        except Exception as exc:
            with self._ws_condition:
                self._ws_error = exc
                self._ws_condition.notify_all()
        finally:
            # asyncio.run shuts down pending readers when its caller loop exits.
            # The reader must release its socket even without an explicit close.
            try:
                await connection.close()
            finally:
                if self._ws_connection is connection:
                    self._ws_connection = None
                    self._ws_task = None
                    self._initialized = False

    async def _handle_remote_notification_or_request(self, payload: dict[str, Any], *, active_method: str) -> bool:
        if 'id' not in payload:
            method = str(payload.get('method') or '')
            if not method:
                return False
            self._append_notification(method)
            return True
        method = str(payload.get('method') or '')
        if method != 'elicitation/create':
            return False
        request_id = payload.get('id')
        request = MCPElicitationRequest(
            server_name=self.server_name,
            active_method=active_method,
            request_id=request_id,
            method=method,
            params=dict(payload.get('params')) if isinstance(payload.get('params'), dict) else {},
        )
        request_payload = request.to_payload()
        request_payload['supported'] = self.elicitation_handler is not None
        response = await self._resolve_elicitation_response(request)
        result = response.to_result()
        response_payload = {'jsonrpc': '2.0', 'id': request_id, 'result': result}
        if self.transport == 'ws' and self._ws_connection is not None:
            await self._ws_connection.send(json.dumps(response_payload))
        elif self.transport in {'http', 'sse'}:
            await self._send_http_jsonrpc_message(response_payload, source='elicitation_response')
        return True

    async def _send_http_jsonrpc_message(self, payload: dict[str, Any], *, source: str) -> None:
        _authorize_mcp_network_operation(
            effective_permissions=self.effective_permissions,
            endpoint=self._request_url(),
            action=source,
            server_name=self.server_name,
        )
        headers = {
            'Accept': 'application/json, text/event-stream',
            'Content-Type': 'application/json',
            'User-Agent': f'ownpi/0.1 mcp-{self.transport}',
            **await self._headers(),
        }
        try:
            async with httpx.AsyncClient(trust_env=False) as client:
                response = await client.post(
                    self._request_url(),
                    json=payload,
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
            if response.status_code == 401:
                self._record_needs_auth(f'remote MCP returned 401 while sending {source}')
                return
            response.raise_for_status()
        except httpx.HTTPError as exc:
            self._append_event(f'{source}_failed', status='failed', error=str(exc))

    async def _resolve_elicitation_response(self, request: MCPElicitationRequest) -> MCPElicitationResponse:
        hook_response = await self._resolve_elicitation_hook_response(request)
        if hook_response is not None:
            return hook_response
        if self.elicitation_handler is None:
            return MCPElicitationResponse(action='cancel', reason='mcp_elicitation_not_interactive')
        try:
            raw_response = self.elicitation_handler(request)
        except Exception as exc:
            return MCPElicitationResponse(action='cancel', reason=f'mcp_elicitation_handler_error: {exc}')
        return _normalize_elicitation_response(raw_response)

    async def _resolve_elicitation_hook_response(self, request: MCPElicitationRequest) -> MCPElicitationResponse | None:
        engine = self.hook_engine
        if engine is None or not getattr(engine, 'enabled', False):
            return None
        outcome = await engine.run_async(
            ElicitationRequest(
                payload={
                    **request.to_payload(),
                    **self.hook_scope,
                    'supported': self.elicitation_handler is not None,
                }
            ),
            effective_permissions=self.effective_permissions,
        )
        if self.hook_delivery_handler is not None:
            self.hook_delivery_handler(outcome.delivery)
        return _elicitation_response_from_hook_outcome(outcome)

    def _record_needs_auth(self, reason: str) -> None:
        self._append_event('needs_auth', status='needs-auth', error=reason)

    def _append_notification(self, method: str) -> None:
        event = {
            'server_name': self.server_name,
            'event': method,
            'status': 'notification',
            'created_at': time.time(),
        }
        capability = _stale_capability_from_notification(method)
        if capability:
            event['stale_capability'] = capability
        self.events.append(event)

    def _append_event(self, event: str, *, status: str, error: str = '') -> None:
        payload = {
            'server_name': self.server_name,
            'event': event,
            'status': status,
            'transport': self.transport,
            'created_at': time.time(),
        }
        if error:
            payload['error'] = error
        self.events.append(payload)

    def _extract_jsonrpc_result(self, payload: Any, *, request_id: int, method: str) -> dict[str, Any]:
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict) and item.get('id') == request_id:
                    return self._extract_jsonrpc_result(item, request_id=request_id, method=method)
            if request_id == -1:
                return {}
            raise MCPClientError(f'{self.transport.upper()} MCP response for {method} did not include request id {request_id}')
        if not isinstance(payload, dict):
            raise MCPClientError(f'{self.transport.upper()} MCP response for {method} was not a JSON-RPC object')
        if payload.get('error') is not None:
            raise MCPClientError(str(payload['error']))
        if request_id == -1:
            return {}
        result = payload.get('result')
        return dict(result) if isinstance(result, dict) else {'result': result}


HttpMCPClientManager = RemoteMCPClientManager
SSEMCPClientManager = RemoteMCPClientManager
WebSocketMCPClientManager = RemoteMCPClientManager


class _StdioMCPSession:
    def __init__(
        self,
        *,
        process: subprocess.Popen[bytes],
        timeout_seconds: int,
        server_name: str = '',
        elicitation_handler: MCPElicitationHandler | None = None,
        hook_engine: Any | None = None,
        effective_permissions: EffectivePermissions | None = None,
        notification_handler: Callable[[dict[str, Any]], None] | None = None,
        write_lock: RLock | None = None,
        hook_delivery_handler: HookDeliveryHandler | None = None,
        hook_scope: dict[str, Any] | None = None,
    ) -> None:
        self.process = process
        self.timeout_seconds = timeout_seconds
        self.server_name = str(server_name or '')
        self.elicitation_handler = elicitation_handler
        self.hook_engine = hook_engine
        self.effective_permissions = effective_permissions
        self.notification_handler = notification_handler
        if self.process.stdin is not None:
            os.set_blocking(self.process.stdin.fileno(), False)
        self.write_lock = write_lock or RLock()
        self.hook_delivery_handler = hook_delivery_handler
        self.hook_scope = dict(hook_scope or {})
        self._request_counter = 0
        self._initialized = False

    async def __aenter__(self) -> '_StdioMCPSession':
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def initialize(self) -> None:
        if self._initialized:
            return
        await self.request(
            'initialize',
            {
                'protocolVersion': '2025-03-26',
                'capabilities': {},
                'clientInfo': {'name': 'ownpi', 'version': '0.1'},
            },
        )
        await self.notify('notifications/initialized', {})
        self._initialized = True

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._request_counter += 1
        request_id = self._request_counter
        await self._write_message({'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params})
        while True:
            payload = await self._read_message_async()
            if self._handle_server_notification(payload):
                continue
            if await self._handle_server_request(payload, active_method=method):
                continue
            if payload.get('id') != request_id:
                continue
            if payload.get('error') is not None:
                raise MCPClientError(str(payload['error']))
            result = payload.get('result')
            return dict(result) if isinstance(result, dict) else {'result': result}

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        await self._write_message({'jsonrpc': '2.0', 'method': method, 'params': params})

    def close(self) -> None:
        if self.process.poll() is None:
            kill_process(self.process)
        self._close_pipes()

    async def aclose(self) -> None:
        try:
            await reap_owned_process(self.process)
        finally:
            self._close_pipes()

    def _close_pipes(self) -> None:
        for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
            if pipe is not None:
                pipe.close()

    async def _handle_server_request(self, payload: dict[str, Any], *, active_method: str) -> bool:
        method = str(payload.get('method') or '')
        if not method or 'id' not in payload:
            return False
        if method != 'elicitation/create':
            return False
        request_id = payload.get('id')
        params = payload.get('params')
        request = MCPElicitationRequest(
            server_name=self.server_name,
            active_method=active_method,
            request_id=request_id,
            method=method,
            params=dict(params) if isinstance(params, dict) else {},
        )
        request_payload = request.to_payload()
        request_payload['supported'] = self.elicitation_handler is not None
        response = await self._resolve_elicitation_response(request)
        result = response.to_result()
        await self._write_message({'jsonrpc': '2.0', 'id': request_id, 'result': result})
        return True

    def _handle_server_notification(self, payload: dict[str, Any]) -> bool:
        if 'id' in payload:
            return False
        method = str(payload.get('method') or '')
        if not method:
            return False
        if self.notification_handler is not None:
            self.notification_handler(dict(payload))
        return True

    async def _resolve_elicitation_response(self, request: MCPElicitationRequest) -> MCPElicitationResponse:
        hook_response = await self._resolve_elicitation_hook_response(request)
        if hook_response is not None:
            return hook_response
        if self.elicitation_handler is None:
            return MCPElicitationResponse(action='cancel', reason='mcp_elicitation_not_interactive')
        try:
            raw_response = self.elicitation_handler(request)
        except Exception as exc:
            return MCPElicitationResponse(action='cancel', reason=f'mcp_elicitation_handler_error: {exc}')
        return _normalize_elicitation_response(raw_response)

    async def _resolve_elicitation_hook_response(self, request: MCPElicitationRequest) -> MCPElicitationResponse | None:
        engine = self.hook_engine
        if engine is None or not getattr(engine, 'enabled', False):
            return None
        outcome = await engine.run_async(
            ElicitationRequest(
                payload={
                    **request.to_payload(),
                    **self.hook_scope,
                    'supported': self.elicitation_handler is not None,
                }
            ),
            effective_permissions=self.effective_permissions,
        )
        if self.hook_delivery_handler is not None:
            self.hook_delivery_handler(outcome.delivery)
        return _elicitation_response_from_hook_outcome(outcome)

    async def _write_message(self, payload: dict[str, Any]) -> None:
        if self.process.stdin is None:
            raise MCPClientError('stdin is unavailable')
        body = json.dumps(payload, separators=(',', ':')).encode('utf-8') + b'\n'
        deadline = time.monotonic() + self.timeout_seconds
        remaining = memoryview(body)
        while remaining:
            if time.monotonic() >= deadline:
                raise MCPClientError('MCP stdin write timed out')
            try:
                written = os.write(self.process.stdin.fileno(), remaining)
            except BlockingIOError:
                await asyncio.sleep(0.01)
                continue
            if not written:
                raise MCPClientError('MCP stdin closed')
            remaining = remaining[written:]
            if remaining:
                await asyncio.sleep(0)

    async def _read_message_async(self) -> dict[str, Any]:
        if self.process.stdout is None:
            raise MCPClientError('stdout is unavailable')
        fd = self.process.stdout.fileno()
        os.set_blocking(fd, False)
        body = bytearray()
        deadline = time.monotonic() + self.timeout_seconds
        while not body.endswith(b'\n'):
            if time.monotonic() >= deadline:
                raise MCPClientError('MCP response timed out while reading JSON line')
            try:
                chunk = os.read(fd, 1)
            except BlockingIOError:
                await asyncio.sleep(0.01)
                continue
            if not chunk:
                raise MCPClientError('MCP server closed stdout early')
            body.extend(chunk)
        try:
            payload = json.loads(body)
        except ValueError as error:
            raise MCPClientError(f'invalid newline-delimited MCP response: {error}') from error
        if not isinstance(payload, dict):
            raise MCPClientError('newline-delimited MCP response must be a JSON object')
        return payload

    def _read_message(self) -> dict[str, Any]:
        if self.process.stdout is None:
            raise MCPClientError('stdout is unavailable')
        body = self._read_until_delimiter(self.process.stdout, b'\n').strip()
        if not body:
            raise MCPClientError('empty newline-delimited MCP response')
        try:
            payload = json.loads(body.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MCPClientError(f'invalid newline-delimited MCP response: {exc}') from exc
        if not isinstance(payload, dict):
            raise MCPClientError('newline-delimited MCP response must be a JSON object')
        return payload

    def _read_until_delimiter(self, stream, delimiter: bytes) -> bytes:
        buffer = bytearray()
        deadline = time.monotonic() + self.timeout_seconds
        selector = selectors.DefaultSelector()
        selector.register(stream, selectors.EVENT_READ)
        fd = stream.fileno()
        try:
            while delimiter not in buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MCPClientError('MCP response timed out while reading JSON line')
                events = selector.select(timeout=remaining)
                if not events:
                    raise MCPClientError('MCP response timed out while reading JSON line')
                chunk = os.read(fd, 1)
                if not chunk:
                    stderr = self._drain_stderr()
                    raise MCPClientError(f'MCP server closed stdout early: {stderr}'.strip())
                buffer.extend(chunk)
        finally:
            selector.close()
        return bytes(buffer)

    def _read_exact(self, stream, size: int) -> bytes:
        buffer = bytearray()
        deadline = time.monotonic() + self.timeout_seconds
        selector = selectors.DefaultSelector()
        selector.register(stream, selectors.EVENT_READ)
        fd = stream.fileno()
        try:
            while len(buffer) < size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MCPClientError('MCP response timed out while reading body')
                events = selector.select(timeout=remaining)
                if not events:
                    raise MCPClientError('MCP response timed out while reading body')
                chunk = os.read(fd, size - len(buffer))
                if not chunk:
                    stderr = self._drain_stderr()
                    raise MCPClientError(f'MCP server closed stdout early: {stderr}'.strip())
                buffer.extend(chunk)
        finally:
            selector.close()
        return bytes(buffer)

    def _drain_stderr(self) -> str:
        if self.process.stderr is None:
            return ''
        try:
            os.set_blocking(self.process.stderr.fileno(), False)
            data = self.process.stderr.read() or b''
        except Exception:
            return ''
        return data.decode('utf-8', errors='ignore')


class PersistentStdioMCPConnection:
    def __init__(self, manager: StdioMCPClientManager) -> None:
        self.manager = manager
        self.session: _StdioMCPSession | None = None
        self.events: list[dict[str, Any]] = []
        self.status: dict[str, Any] = {'status': 'idle'}
        self._lock = RLock()
        self._condition = Condition(self._lock)
        self._responses: dict[Any, dict[str, Any]] = {}
        self._reader_thread: Thread | None = None
        self._reader_error: Exception | None = None
        self._closed = False
        self._active_method = ''
        self._request_lock = Lock()
        self._server_requests: list[tuple[dict[str, Any], str]] = []

    def _connection_metadata(self) -> dict[str, Any]:
        run_id = str(self.manager.run_id or '').strip()
        if run_id:
            return {
                'connection_scope': 'run',
                'shared_connection': False,
                'owner_run_id': run_id,
            }
        return {
            'connection_scope': 'app',
            'shared_connection': True,
        }

    def _status_payload(self, **values: Any) -> dict[str, Any]:
        payload = dict(values)
        payload.setdefault('server_name', self.manager.server_name)
        payload.update(self._connection_metadata())
        return payload

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        async with claim_lock(self._request_lock):
            try:
                return await self._request_once(method, params)
            except asyncio.CancelledError:
                await self._aclose(status='cancelled')
                raise
            except Exception as first_exc:
                await self._aclose(status='reconnecting', error=str(first_exc))
                try:
                    result = await self._request_once(method, params)
                except asyncio.CancelledError:
                    await self._aclose(status='cancelled')
                    raise
                except Exception as second_exc:
                    await self._aclose(status='failed', error=str(second_exc))
                    raise
                self.status = self._status_payload(status='connected', reconnected=True)
                return result

    def refresh_manager(self, manager: StdioMCPClientManager) -> None:
        with self._lock:
            self.manager = manager
            if self.session is not None:
                self.session.elicitation_handler = manager.elicitation_handler
                self.session.hook_engine = manager.hook_engine
                self.session.effective_permissions = manager.effective_permissions
                self.session.hook_delivery_handler = manager.hook_delivery_handler
                self.session.hook_scope = dict(manager.hook_scope)

    def drain_events(self) -> list[dict[str, Any]]:
        with self._lock:
            events = [dict(item) for item in self.events]
            self.events.clear()
            return events

    def close(self) -> None:
        with self._condition:
            self._closed = True
            session = self.session
            reader = self._reader_thread
            self._condition.notify_all()
        # Reap and join outside the condition needed by the exiting reader.
        if session is not None and session.process.poll() is None:
            kill_process(session.process)
        if reader is not None:
            reader.join()
        if session is not None:
            session.close()
        with self._condition:
            self.session = None
            self._reader_thread = None
            self.status = self._status_payload(status='closed')

    async def _request_once(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        session = await self._ensure_session()
        result = await self._request_with_reader(session, method, params)
        self.status = self._status_payload(status='connected', last_method=method)
        return result

    async def _ensure_session(self) -> _StdioMCPSession:
        if self.session is not None and self.session.process.poll() is None:
            return self.session
        process_env = build_subprocess_env(extra_env=self.manager.env)
        command = _sandboxed_stdio_command(self.manager, process_env=process_env)
        process = popen_with_process_group(
            command,
            cwd=self.manager.cwd,
            env=process_env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._responses.clear()
        self._server_requests.clear()
        self._reader_error = None
        self._closed = False
        self.session = _StdioMCPSession(
            process=process,
            timeout_seconds=self.manager.timeout_seconds,
            server_name=self.manager.server_name,
            elicitation_handler=self.manager.elicitation_handler,
            hook_engine=self.manager.hook_engine,
            effective_permissions=self.manager.effective_permissions,
            hook_delivery_handler=self.manager.hook_delivery_handler,
            hook_scope=self.manager.hook_scope,
            notification_handler=self._handle_notification,
            write_lock=self._lock,
        )
        self._start_reader_locked(self.session)
        await self._request_with_reader(
            self.session,
            'initialize',
            {
                'protocolVersion': '2025-03-26',
                'capabilities': {},
                'clientInfo': {'name': 'ownpi', 'version': '0.1'},
            },
        )
        await self.session.notify('notifications/initialized', {})
        self.session._initialized = True
        self.status = self._status_payload(status='connected', initialized=True)
        return self.session

    def _start_reader_locked(self, session: _StdioMCPSession) -> None:
        self._reader_thread = Thread(
            target=self._reader_loop,
            args=(session,),
            name=f'mcp-reader:{self.manager.server_name or "stdio"}',
            daemon=True,
        )
        self._reader_thread.start()

    def _reader_loop(self, session: _StdioMCPSession) -> None:
        while True:
            with self._condition:
                if self._closed:
                    return
            try:
                payload = session._read_message()
            except Exception as exc:
                with self._condition:
                    if not self._closed:
                        self._reader_error = exc
                        self.status = self._status_payload(status='reader_failed', error=str(exc))
                    self._condition.notify_all()
                return
            if session._handle_server_notification(payload):
                continue
            if 'method' in payload and 'id' in payload:
                with self._condition:
                    active_method = self._active_method
                with self._condition:
                    self._server_requests.append((payload, active_method))
                continue
            response_id = payload.get('id')
            with self._condition:
                self._responses[response_id] = dict(payload)
                self._condition.notify_all()

    async def _request_with_reader(self, session: _StdioMCPSession, method: str, params: dict[str, Any]) -> dict[str, Any]:
        session._request_counter += 1
        request_id = session._request_counter
        self._active_method = method
        await session._write_message({'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params})
        deadline = time.monotonic() + session.timeout_seconds
        try:
            while True:
                if self._reader_error is not None:
                    raise MCPClientError(f'MCP reader failed: {self._reader_error}')
                payload = self._responses.pop(request_id, None)
                if payload is not None:
                    if payload.get('error') is not None:
                        raise MCPClientError(str(payload['error']))
                    result = payload.get('result')
                    return dict(result) if isinstance(result, dict) else {'result': result}
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MCPClientError(f'MCP response timed out for {method}')
                with self._condition:
                    requests, self._server_requests = self._server_requests, []
                for payload, active_method in requests:
                    await session._handle_server_request(payload, active_method=active_method)
                await asyncio.sleep(min(0.01, remaining))
        finally:
            self._active_method = ''

    def _handle_notification(self, payload: dict[str, Any]) -> None:
        method = str(payload.get('method', '') or '').strip()
        event = {
            'server_name': self.manager.server_name,
            'event': method,
            'status': 'notification',
            'created_at': time.time(),
        }
        capability = _stale_capability_from_notification(method)
        if capability:
            event['stale_capability'] = capability
        with self._condition:
            self.events.append(event)
            self._condition.notify_all()

    async def _aclose(self, *, status: str, error: str = '') -> None:
        with self._condition:
            self._closed = True
            session = self.session
            reader = self._reader_thread
            self._condition.notify_all()

        async def cleanup() -> None:
            if session is not None:
                await reap_owned_process(session.process)
            while reader is not None and reader.is_alive():
                await asyncio.sleep(0.01)
            if reader is not None:
                reader.join()
            # No live reader may hold a buffered pipe lock when it is closed.
            if session is not None:
                session._close_pipes()
            self.session = None
            self._reader_thread = None
            self.status = self._status_payload(status=status, error=error)

        cleanup_task = asyncio.create_task(cleanup())
        cancelled = False
        while not cleanup_task.done():
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                cancelled = True
        cleanup_task.result()
        if cancelled:
            raise asyncio.CancelledError


class PersistentMCPConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, PersistentStdioMCPConnection] = {}
        self._lock = RLock()

    async def request(
        self,
        manager: StdioMCPClientManager,
        *,
        key: str,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        connection = self._connection(manager, key=key)
        return await connection.request(method, params)

    def drain_events(self, key: str) -> list[dict[str, Any]]:
        with self._lock:
            connection = self._connections.get(key)
        if connection is None:
            return []
        return connection.drain_events()

    def close(self, key: str) -> None:
        with self._lock:
            connection = self._connections.pop(key, None)
        if connection is not None:
            connection.close()

    def status(self, key: str) -> dict[str, Any]:
        with self._lock:
            connection = self._connections.get(key)
        status = dict(connection.status) if connection is not None else {'status': 'not_started'}
        return status

    def close_all(self) -> None:
        with self._lock:
            connections = list(self._connections.values())
            self._connections.clear()
        for connection in connections:
            connection.close()

    def _connection(self, manager: StdioMCPClientManager, *, key: str) -> PersistentStdioMCPConnection:
        with self._lock:
            connection = self._connections.get(key)
            if connection is None:
                connection = PersistentStdioMCPConnection(manager)
                self._connections[key] = connection
            else:
                connection.refresh_manager(manager)
            return connection


_PERSISTENT_MCP_CONNECTIONS = PersistentMCPConnectionManager()


def close_persistent_mcp_connections() -> None:
    _PERSISTENT_MCP_CONNECTIONS.close_all()


atexit.register(close_persistent_mcp_connections)


def build_stdio_manager(
    server_config: dict[str, Any],
    *,
    run_id: str = '',
    elicitation_handler: MCPElicitationHandler | None = None,
    hook_engine: Any | None = None,
    effective_permissions: EffectivePermissions | None = None,
    hook_delivery_handler: HookDeliveryHandler | None = None,
    hook_scope: dict[str, Any] | None = None,
) -> StdioMCPClientManager:
    command = list(stdio_argv(server_config))
    if not command:
        raise MCPClientError('configured MCP server is missing command')
    cwd = str(server_config.get('cwd') or '').strip() or None
    if cwd is not None:
        cwd = str(Path(cwd).expanduser())
    env = {str(key): str(value) for key, value in dict(server_config.get('env', {})).items()}
    timeout_seconds = max(1, int(server_config.get('timeout_seconds', 20) or 20))
    return StdioMCPClientManager(
        command=command,
        cwd=cwd,
        env=env,
        timeout_seconds=timeout_seconds,
        run_id=run_id or str(server_config.get('run_id', '') or ''),
        server_name=str(server_config.get('name', '') or ''),
        elicitation_handler=elicitation_handler,
        hook_engine=hook_engine,
        persistent=bool(server_config.get('persistent', True)),
        effective_permissions=effective_permissions,
        hook_delivery_handler=hook_delivery_handler,
        hook_scope=hook_scope,
    )


def build_mcp_manager(
    server_config: dict[str, Any],
    *,
    run_id: str = '',
    elicitation_handler: MCPElicitationHandler | None = None,
    hook_engine: Any | None = None,
    effective_permissions: EffectivePermissions | None = None,
    hook_delivery_handler: HookDeliveryHandler | None = None,
    hook_scope: dict[str, Any] | None = None,
) -> StdioMCPClientManager | RemoteMCPClientManager:
    transport = normalize_mcp_transport(server_config.get('transport', server_config.get('type', 'stdio')))
    if transport == 'stdio':
        return build_stdio_manager(
            server_config,
            run_id=run_id,
            elicitation_handler=elicitation_handler,
            hook_engine=hook_engine,
            effective_permissions=effective_permissions,
            hook_delivery_handler=hook_delivery_handler,
            hook_scope=hook_scope,
        )
    if transport == 'http':
        return _build_remote_manager(
            server_config,
            transport='http',
            run_id=run_id,
            elicitation_handler=elicitation_handler,
            hook_engine=hook_engine,
            effective_permissions=effective_permissions,
            hook_delivery_handler=hook_delivery_handler,
            hook_scope=hook_scope,
        )
    if transport == 'sse':
        return _build_remote_manager(
            server_config,
            transport='sse',
            run_id=run_id,
            elicitation_handler=elicitation_handler,
            hook_engine=hook_engine,
            effective_permissions=effective_permissions,
            hook_delivery_handler=hook_delivery_handler,
            hook_scope=hook_scope,
        )
    if transport == 'ws':
        return _build_remote_manager(
            server_config,
            transport='ws',
            run_id=run_id,
            elicitation_handler=elicitation_handler,
            hook_engine=hook_engine,
            effective_permissions=effective_permissions,
            hook_delivery_handler=hook_delivery_handler,
            hook_scope=hook_scope,
        )
    raise MCPClientError(f'unsupported MCP transport: {transport}')


def _build_remote_manager(
    server_config: dict[str, Any],
    *,
    transport: Literal['http', 'sse', 'ws'],
    run_id: str = '',
    elicitation_handler: MCPElicitationHandler | None = None,
    hook_engine: Any | None = None,
    effective_permissions: EffectivePermissions | None = None,
    hook_delivery_handler: HookDeliveryHandler | None = None,
    hook_scope: dict[str, Any] | None = None,
) -> RemoteMCPClientManager:
    headers = {str(key): str(value) for key, value in dict(server_config.get('headers', {})).items()}
    return RemoteMCPClientManager(
        url=str(server_config.get('url') or '').strip(),
        headers=headers,
        timeout_seconds=max(1, int(server_config.get('timeout_seconds', 20) or 20)),
        transport=transport,
        run_id=run_id or str(server_config.get('run_id', '') or ''),
        server_name=str(server_config.get('name', '') or ''),
        elicitation_handler=elicitation_handler,
        hook_engine=hook_engine,
        effective_permissions=effective_permissions,
        server_config=server_config,
        hook_delivery_handler=hook_delivery_handler,
        hook_scope=hook_scope,
    )


def _authorize_mcp_network_operation(
    *,
    effective_permissions: EffectivePermissions | None,
    endpoint: str,
    action: str,
    server_name: str,
) -> None:
    if effective_permissions is None:
        raise MCPClientError('remote MCP execution requires canonical effective permissions')
    canonical = compile_canonical_action(
        request_id=f'mcp-network:{server_name or "remote"}:{action}',
        tool_name='mcp_remote_transport',
        permission_class='network',
        input_payload={'url': endpoint, 'action': action},
        environment={'server_name': server_name, 'transport': 'mcp_remote'},
        destructive=False,
        declared_effects=OperationEffects.declared(
            network_required=True,
            network_endpoints=(endpoint,),
            remote_required=True,
            remote_target=endpoint,
            source='configured_mcp_remote',
        ),
    )
    _require_canonical_mcp_allow(
        canonical.operation,
        effective_permissions=effective_permissions,
        operation_label='remote MCP network operation',
    )


def _authorize_mcp_process_operation(
    *,
    effective_permissions: EffectivePermissions | None,
    command: tuple[str, ...],
    cwd: Path,
    action: str,
    server_name: str,
) -> tuple[EffectivePermissions, str, str]:
    if effective_permissions is None:
        raise MCPClientError('MCP process execution requires canonical effective permissions')
    canonical = compile_canonical_action(
        request_id=f'mcp-process:{server_name or "stdio"}:{action}',
        tool_name='mcp_configured_process',
        permission_class='execution',
        input_payload={'argv': list(command), 'cwd': str(cwd)},
        environment={'server_name': server_name, 'transport': 'mcp_stdio'},
        destructive=False,
        declared_effects=OperationEffects.declared(
            process_required=True,
            process_argv=command,
            source='configured_mcp_process',
        ),
    )
    permissions = _require_canonical_mcp_allow(
        canonical.operation,
        effective_permissions=effective_permissions,
        operation_label='MCP configured process',
    )
    return permissions, canonical.action_hash, canonical.effects.effect_hash


def _require_canonical_mcp_allow(
    operation: Any,
    *,
    effective_permissions: EffectivePermissions,
    operation_label: str,
) -> EffectivePermissions:
    orchestrator = PermissionOrchestrator()
    evaluation = orchestrator.evaluate(
        operation,
        effective_permissions=effective_permissions,
    )
    if not evaluation.allowed:
        reason = (
            evaluation.approval_request.reason
            if evaluation.approval_request is not None
            else evaluation.denial_reason
        )
        raise MCPClientError(f'{operation_label} requires canonical approval: {reason}')
    revalidated = orchestrator.revalidate_for_execution(
        evaluation,
        effective_permissions=evaluation.effective_permissions,
    )
    if not revalidated.allowed:
        raise MCPClientError(
            f'{operation_label} permission revalidation failed: {revalidated.denial_reason}'
        )
    return revalidated.effective_permissions


async def _remote_mcp_headers(
    server_config: dict[str, Any],
    *,
    effective_permissions: EffectivePermissions | None,
) -> dict[str, str]:
    headers = {str(key): str(value) for key, value in dict(server_config.get('headers', {})).items()}
    helper = str(server_config.get('headers_helper', server_config.get('headersHelper', '')) or '').strip()
    if helper:
        try:
            command = header_helper_argv(server_config)
            permissions, _action_hash, _effect_hash = _authorize_mcp_process_operation(
                effective_permissions=effective_permissions,
                command=command,
                cwd=Path.cwd().resolve(),
                action='headers_helper',
                server_name=str(server_config.get('name', '') or ''),
            )
            result = await SandboxManager().execute_async(
                SandboxCommandRequest(
                    argv=command,
                    cwd=Path.cwd().resolve(),
                    env=build_subprocess_env(),
                    effective_permissions=permissions,
                    timeout_seconds=max(1, int(server_config.get('timeout_seconds', 20) or 20)),
                )
            )
            if result.exit_code == 0 and result.stdout.strip():
                helper_headers = json.loads(result.stdout)
                if isinstance(helper_headers, dict):
                    headers.update({str(key): str(value) for key, value in helper_headers.items()})
        except (OSError, RuntimeError, ValueError, MCPClientError):
            pass
    return headers


def _persistent_connection_key(manager: StdioMCPClientManager) -> str:
    run_id = str(manager.run_id or '').strip()
    scope = 'run' if run_id else 'app'
    _permissions, action_hash, effect_hash = _authorize_mcp_process_operation(
        effective_permissions=manager.effective_permissions,
        command=tuple(manager.command),
        cwd=Path(manager.cwd or Path.cwd()).expanduser().resolve(),
        action='stdio_session',
        server_name=manager.server_name,
    )
    payload = {
        'command': list(manager.command),
        'cwd': manager.cwd,
        'env': dict(manager.env),
        'run_id': run_id,
        'scope': scope,
        'timeout_seconds': manager.timeout_seconds,
        'server_name': manager.server_name,
        'permission_policy_hash': (
            manager.effective_permissions.policy_hash
            if manager.effective_permissions is not None
            else ''
        ),
        'canonical_action_hash': action_hash,
        'canonical_effect_hash': effect_hash,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode('utf-8')
    ).hexdigest()[:16]
    return f'{manager.server_name or "stdio"}:{scope}:{digest}'


def _sandboxed_stdio_command(
    manager: StdioMCPClientManager,
    *,
    process_env: dict[str, str],
) -> list[str]:
    cwd = Path(manager.cwd or Path.cwd()).expanduser().resolve()
    permissions, _action_hash, _effect_hash = _authorize_mcp_process_operation(
        effective_permissions=manager.effective_permissions,
        command=tuple(manager.command),
        cwd=cwd,
        action='stdio_spawn',
        server_name=manager.server_name,
    )
    try:
        wrapped = SandboxManager().wrap_command(
            SandboxCommandRequest(
                argv=tuple(manager.command),
                cwd=cwd,
                env=process_env,
                effective_permissions=permissions,
                timeout_seconds=max(1, manager.timeout_seconds),
            )
        )
    except RuntimeError as exc:
        raise MCPClientError(f'MCP stdio sandbox capability unavailable: {exc}') from exc
    return list(wrapped)


def _stale_capability_from_notification(method: str) -> str:
    normalized = str(method or '').strip()
    if normalized == 'notifications/tools/list_changed':
        return 'tools'
    if normalized == 'notifications/resources/list_changed':
        return 'resources'
    if normalized == 'notifications/prompts/list_changed':
        return 'prompts'
    return ''


def _parse_sse_json_messages(text: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    event_lines: list[str] = []
    for raw_line in str(text or '').splitlines():
        line = raw_line.rstrip('\r')
        if not line:
            payloads.extend(_json_payloads_from_sse_event(event_lines))
            event_lines = []
            continue
        event_lines.append(line)
    payloads.extend(_json_payloads_from_sse_event(event_lines))
    return payloads


def _json_payloads_from_sse_event(lines: list[str]) -> list[dict[str, Any]]:
    data_parts = [line[5:].strip() for line in lines if line.startswith('data:')]
    if not data_parts:
        return []
    try:
        payload = json.loads('\n'.join(data_parts))
    except ValueError:
        return []
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, dict)]
    return []


def _normalize_elicitation_response(value: MCPElicitationResponse | dict[str, Any] | str | None) -> MCPElicitationResponse:
    if isinstance(value, MCPElicitationResponse):
        if value.action in {'accept', 'decline', 'cancel'}:
            return value
        return MCPElicitationResponse(action='cancel', reason=f'invalid elicitation action: {value.action}')
    if value is None:
        return MCPElicitationResponse(action='cancel', reason='mcp_elicitation_handler_returned_none')
    if isinstance(value, str):
        action = value.strip().casefold()
        if action in {'accept', 'decline', 'cancel'}:
            return MCPElicitationResponse(action=cast(MCPElicitationAction, action))
        return MCPElicitationResponse(action='cancel', reason=f'invalid elicitation action: {value}')
    if isinstance(value, dict):
        action = str(value.get('action', '') or '').strip().casefold()
        if action not in {'accept', 'decline', 'cancel'}:
            return MCPElicitationResponse(action='cancel', reason=f'invalid elicitation action: {action or "<missing>"}')
        content = value.get('content')
        normalized_content = dict(content) if isinstance(content, dict) else None
        return MCPElicitationResponse(
            action=cast(MCPElicitationAction, action),
            content=normalized_content,
            reason=str(value.get('reason', '') or ''),
        )
    return MCPElicitationResponse(action='cancel', reason=f'invalid elicitation response type: {type(value).__name__}')


def _elicitation_response_from_hook_outcome(outcome: Any) -> MCPElicitationResponse | None:
    if not isinstance(outcome, ElicitationOutcome):
        return None
    if outcome.action not in {'accept', 'decline', 'cancel'}:
        return None
    return MCPElicitationResponse(
        action=cast(MCPElicitationAction, outcome.action),
        content=dict(outcome.content) or None,
        reason=outcome.reason or 'mcp_elicitation_hook_response',
    )
