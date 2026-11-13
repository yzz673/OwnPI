from __future__ import annotations

import ast
import asyncio
import atexit
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time
from typing import Any, AsyncIterator, Iterable, Iterator
from urllib.parse import unquote, urlparse

from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.tasks.runtime_task_framework import (
    fail_runtime_task,
    register_runtime_task,
    runtime_task_id_for_lsp,
    update_runtime_task,
)
from ownpi.runtime.environment.process_capture import popen_with_process_group
from ownpi.runtime.environment.owned_process_cleanup import reap_owned_process
from ownpi.runtime.environment.subprocess_env import build_subprocess_env
from ownpi.runtime.limits.projection import (
    DEFAULT_LSP_RESULTS,
    MAX_LSP_RESULTS_PER_REQUEST,
    MAX_LSP_SNIPPET_CHARS,
)
from ownpi.runtime.permissions.action_compiler import OperationEffects
from ownpi.runtime.permissions.models import EffectivePermissions, FileSystemAccessMode
from ownpi.runtime.permissions.orchestrator import FileSystemAccessRequest
from ownpi.runtime.permissions.policy import (
    FileSystemPolicyMatcher, lexical_absolute_path, require_file_access,
)
from ownpi.runtime.sandbox import SandboxCommandRequest, SandboxManager
from ownpi.tools.common import display_path, resolve_workspace_path


_SUPPORTED_OPERATIONS = {
    'definition',
    'references',
    'hover',
    'document_symbols',
    'workspace_symbols',
    'diagnostics',
}
_LSP_POSITION_OPERATIONS = {'definition', 'references', 'hover'}
_SKIP_DIRS = {
    '.agents',
    '.codex',
    '.git',
    '.mypy_cache',
    '.pytest_cache',
    '.venv',
    '__pycache__',
    'node_modules',
    'venv',
}
_FALLBACK_BACKEND = 'fallback_code_search'
_LSP_BACKEND = 'lsp_stdio'
_LspSessionKey = tuple[
    str,
    str,
    tuple[str, ...],
    tuple[tuple[str, str], ...],
    str,
    str,
    str,
    str,
]
_LSP_KIND_NAMES = {
    1: 'file',
    2: 'module',
    3: 'namespace',
    4: 'package',
    5: 'class',
    6: 'method',
    7: 'property',
    8: 'field',
    9: 'constructor',
    10: 'enum',
    11: 'interface',
    12: 'function',
    13: 'variable',
    14: 'constant',
    15: 'string',
    16: 'number',
    17: 'boolean',
    18: 'array',
    19: 'object',
    20: 'key',
    21: 'null',
    22: 'enum_member',
    23: 'struct',
    24: 'event',
    25: 'operator',
    26: 'type_parameter',
}


async def run_lsp_query(context: Any, arguments: dict[str, Any]) -> ToolResult:
    operation = str(arguments.get('operation', '') or '').strip().lower()
    if operation not in _SUPPORTED_OPERATIONS:
        return ToolResult(
            ok=False,
            tool_name='lsp_query',
            error=f'unsupported lsp_query operation: {operation}',
            data={'supported_operations': sorted(_SUPPORTED_OPERATIONS)},
        )
    max_results = _clamp_int(arguments.get('max_results'), default=DEFAULT_LSP_RESULTS)
    language = str(arguments.get('language', '') or '').strip().lower()
    symbol = str(arguments.get('symbol', '') or '').strip()
    raw_path = str(arguments.get('path', '') or '').strip()
    path: Path | None = None
    if raw_path:
        try:
            path = resolve_workspace_path(context.project_root, raw_path)
        except ValueError as exc:
            return ToolResult(ok=False, tool_name='lsp_query', error=str(exc), data={'operation': operation})

    permissions = _canonical_permissions(context)
    if path is not None:
        try:
            require_file_access(
                permissions, lexical_absolute_path(raw_path, cwd=context.project_root),
                FileSystemAccessMode.READ,
            )
            require_file_access(permissions, path, FileSystemAccessMode.READ)
        except PermissionError as exc:
            return ToolResult(ok=False, tool_name='lsp_query', error=str(exc), data={'operation': operation})
    try:
        lsp_attempt = await _try_configured_lsp_query(
            context=context,
            operation=operation,
            path=path,
            language=language,
            symbol=symbol,
            arguments=arguments,
            max_results=max_results,
        )
    except PermissionError as exc:
        return ToolResult(ok=False, tool_name='lsp_query', error=str(exc), data={'operation': operation})
    if lsp_attempt is not None:
        if lsp_attempt.get('backend') == _LSP_BACKEND and lsp_attempt.get('backend_status') == 'connected':
            return ToolResult(
                ok=True,
                tool_name='lsp_query',
                data={
                    'operation': operation,
                    'result_count': len(lsp_attempt.get('results', [])) if isinstance(lsp_attempt.get('results'), list) else 0,
                    **lsp_attempt,
                },
                stdout=_summary_line(operation=operation, result=lsp_attempt),
            )
        fallback_status = str(lsp_attempt.get('backend_status', '') or '')
    else:
        fallback_status = ''

    try:
        if operation == 'document_symbols':
            result = _document_symbols(context.project_root, path, language=language, max_results=max_results)
        elif operation == 'diagnostics':
            result = _diagnostics(context.project_root, path, language=language)
        elif operation == 'hover':
            result = _hover(context.project_root, path, arguments, symbol=symbol)
        elif operation == 'definition':
            result = _definition(context.project_root, path, symbol=symbol, max_results=max_results, permissions=permissions)
        elif operation == 'references':
            result = _references(context.project_root, path, symbol=symbol, max_results=max_results, permissions=permissions)
        else:
            result = _workspace_symbols(context.project_root, symbol=symbol, max_results=max_results, permissions=permissions)
    except OSError as exc:
        return ToolResult(ok=False, tool_name='lsp_query', error=str(exc), data={'operation': operation})
    except SyntaxError as exc:
        result = {
            'operation': operation,
            'backend': _FALLBACK_BACKEND,
            'lsp_available': False,
            'backend_status': fallback_status or 'fallback_python_ast_parse_error',
            'fallback_mode': 'python_ast_parse_error',
            'result_count': 1,
            'results': [
                {
                    'severity': 'error',
                    'message': exc.msg,
                    'path': display_path(path, base=context.project_root) if path is not None else '',
                    'line': exc.lineno or 0,
                    'character': exc.offset or 0,
                }
            ],
        }
        return ToolResult(
            ok=True,
            tool_name='lsp_query',
            data=result,
            stdout=f'lsp_query {operation}: 1 diagnostic via python_ast_parse_error',
        )
    if fallback_status:
        result['backend_status'] = fallback_status
    return ToolResult(
        ok=True,
        tool_name='lsp_query',
        data={
            'operation': operation,
            'backend': result.get('backend', _FALLBACK_BACKEND),
            'lsp_available': bool(result.get('lsp_available', False)),
            'backend_status': result.get('backend_status', 'no_lsp_server_configured'),
            'fallback_mode': result.get('fallback_mode', 'ast_grep'),
            'result_count': len(result.get('results', [])) if isinstance(result.get('results'), list) else 0,
            **result,
        },
        stdout=_summary_line(operation=operation, result=result),
    )


def resolve_lsp_query_effects(
    context: Any,
    arguments: dict[str, Any],
) -> OperationEffects:
    config = _lsp_config_from_context(context)
    raw_path = str(arguments.get('path', '') or '').strip()
    try:
        path = (
            resolve_workspace_path(context.project_root, raw_path)
            if raw_path
            else None
        )
    except ValueError:
        return OperationEffects()
    fallback = OperationEffects.declared(
        file_system_accesses=(FileSystemAccessRequest(
            lexical_absolute_path(raw_path, cwd=context.project_root)
            if raw_path else Path(context.project_root).resolve(), FileSystemAccessMode.READ,
        ),),
        source='lsp_document_or_search_read',
    )
    if not config.get('enabled'):
        return fallback
    language = str(arguments.get('language', '') or '').strip().lower()
    operation = str(arguments.get('operation', '') or '').strip().lower()
    server = _select_lsp_server(config=config, path=path, language=language)
    if server is None or not _configured_lsp_query_can_run(
        operation=operation,
        path=path,
        arguments=arguments,
    ):
        return fallback
    command = tuple(
        str(item) for item in server.get('command', []) if str(item).strip()
    )
    if not command:
        return fallback
    root = Path(context.project_root).resolve()
    read_paths = [root, *([lexical_absolute_path(raw_path, cwd=root)] if path is not None else [])]
    read_paths.extend(
        Path(item).expanduser().resolve()
        for item in command
        if Path(item).expanduser().is_absolute()
    )
    accesses = tuple(
        FileSystemAccessRequest(path=item, access=FileSystemAccessMode.READ)
        for item in dict.fromkeys(read_paths)
    )
    return OperationEffects.declared(
        file_system_accesses=accesses,
        process_required=True,
        process_argv=command,
        source='configured_lsp_server',
    )


async def _try_configured_lsp_query(
    *,
    context: Any,
    operation: str,
    path: Path | None,
    language: str,
    symbol: str,
    arguments: dict[str, Any],
    max_results: int,
) -> dict[str, Any] | None:
    config = _lsp_config_from_context(context)
    if not config.get('enabled'):
        return None
    server = _select_lsp_server(config=config, path=path, language=language)
    if server is None:
        _record_lsp_status(
            context,
            {
                'server_name': '',
                'backend': _FALLBACK_BACKEND,
                'status': 'unavailable',
                'backend_status': 'no_matching_lsp_server_configured',
                'operation': operation,
                'language': language,
            },
        )
        return {'backend_status': 'no_matching_lsp_server_configured'}
    command = [str(item) for item in server.get('command', []) if str(item).strip()]
    server_env = {
        str(key): str(value)
        for key, value in dict(server.get('env', {})).items()
        if str(key).strip() and value is not None
    } if isinstance(server.get('env'), dict) else {}
    server_name = str(server.get('name', 'language-server') or 'language-server')
    if not command:
        _record_lsp_status(
            context,
            {
                'server_name': server_name,
                'backend': _FALLBACK_BACKEND,
                'status': 'unavailable',
                'backend_status': 'lsp_server_command_missing',
                'operation': operation,
                'language': language,
            },
        )
        return {'backend_status': 'lsp_server_command_missing'}
    if not _configured_lsp_query_can_run(
        operation=operation,
        path=path,
        arguments=arguments,
    ):
        if operation in _LSP_POSITION_OPERATIONS:
            return {'backend_status': 'lsp_position_required_fallback'}
        return {'backend_status': 'lsp_document_required_fallback'}
    timeout = max(1, int(config.get('request_timeout_seconds', 5) or 5))
    root = Path(context.project_root)
    manager = _get_lsp_session_manager()
    session: _LspSession | None = None
    session_reused = False
    protocol_started = False
    try:
        session, session_reused = await manager.acquire(
            command=command,
            env=server_env,
            root=root,
            server_name=server_name,
            initialization_options=dict(server.get('initialization_options', {})),
            timeout_seconds=timeout,
            run_id=_run_id_from_context(context),
            effective_permissions=_canonical_permissions(context),
            canonical_action_hash=str(
                getattr(context, 'canonical_action_hash', '') or ''
            ),
            canonical_effect_hash=str(
                getattr(context, 'canonical_effect_hash', '') or ''
            ),
        )
        _record_lsp_runtime_task(
            context,
            session=session,
            server_name=server_name,
            status='running',
            description=f'lsp {server_name}',
            metadata={'session_reused': session_reused, 'operation': operation},
        )
        async with _claim_lsp_lock(session.lock):
            protocol_started = True
            if path is not None:
                await session.ensure_document_open(path, language_id=_language_id(language=language, path=path))
            result = await _run_lsp_operation(
                client=session.client,
                operation=operation,
                root=root,
                path=path,
                symbol=symbol,
                arguments=arguments,
                max_results=max_results,
            )
            session.request_count += 1
            request_count = session.request_count
            opened_document_count = len(session.opened_documents)
        _record_lsp_runtime_task(
            context,
            session=session,
            server_name=server_name,
            status='running',
            description=f'lsp {server_name}',
            metadata={
                'session_reused': session_reused,
                'operation': operation,
                'request_count': request_count,
                'opened_document_count': opened_document_count,
            },
        )
        payload = {
            'backend': _LSP_BACKEND,
            'lsp_available': True,
            'backend_status': 'connected',
            'fallback_mode': '',
            'lsp_server_name': server_name,
            'lsp_capabilities': dict(session.capabilities),
            'lsp_session_id': session.session_id,
            'lsp_session_reused': session_reused,
            'lsp_request_count': request_count,
            'lsp_opened_document_count': opened_document_count,
            **result,
        }
        _record_lsp_status(
            context,
            {
                'server_name': server_name,
                'backend': _LSP_BACKEND,
                'status': 'connected',
                'backend_status': 'connected',
                'operation': operation,
                'language': language or _language_id(language='', path=path),
                'result_count': len(payload.get('results', [])) if isinstance(payload.get('results'), list) else 0,
                'session_id': session.session_id,
                'session_reused': session_reused,
                'request_count': request_count,
                'opened_document_count': opened_document_count,
            },
        )
        return payload
    except asyncio.CancelledError:
        if session is not None and protocol_started:
            await manager.discard(session.key)
        raise
    except Exception as exc:
        if session is not None:
            _record_lsp_runtime_task(
                context,
                session=session,
                server_name=server_name,
                status='failed',
                description=f'lsp {server_name}',
                metadata={'operation': operation, 'error': str(exc)},
            )
            await manager.discard(session.key)
        _record_lsp_status(
            context,
            {
                'server_name': server_name,
                'backend': _LSP_BACKEND,
                'status': 'error',
                'backend_status': 'lsp_error_fallback',
                'operation': operation,
                'language': language,
                'error': str(exc),
            },
        )
        if isinstance(exc, PermissionError):
            raise
        return {'backend_status': 'lsp_error_fallback', 'lsp_server_name': server_name, 'lsp_error': str(exc)}


def _lsp_config_from_context(context: Any) -> dict[str, Any]:
    config_snapshot = getattr(context, 'environment_config_snapshot', None)
    if not isinstance(config_snapshot, dict):
        state_store = getattr(context, 'state_store', None)
        state = getattr(state_store, 'state', None)
        environment_state = getattr(state, 'environment_state', None)
        config_snapshot = getattr(environment_state, 'config_snapshot', None)
    if not isinstance(config_snapshot, dict):
        return {}
    lsp = config_snapshot.get('lsp')
    return dict(lsp) if isinstance(lsp, dict) else {}


def _canonical_permissions(context: Any) -> EffectivePermissions:
    value = getattr(context, 'effective_permissions', None)
    if isinstance(value, EffectivePermissions):
        return value
    state = getattr(getattr(context, 'state_store', None), 'state', None)
    value = getattr(getattr(state, 'tool_state', None), 'effective_permissions', None)
    if isinstance(value, EffectivePermissions):
        return value
    raise RuntimeError('configured LSP requires canonical effective permissions')


def _configured_lsp_query_can_run(
    *,
    operation: str,
    path: Path | None,
    arguments: dict[str, Any],
) -> bool:
    if operation in _LSP_POSITION_OPERATIONS:
        return path is not None and 'line' in arguments and 'character' in arguments
    if operation in {'document_symbols', 'diagnostics'}:
        return path is not None
    return operation in _SUPPORTED_OPERATIONS


def _run_id_from_context(context: Any) -> str:
    direct = str(getattr(context, 'run_id', '') or '').strip()
    if direct:
        return direct
    state = getattr(getattr(context, 'state_store', None), 'state', None)
    run_metadata = getattr(state, 'run_metadata', None)
    return str(getattr(run_metadata, 'run_id', '') or '').strip()


def _select_lsp_server(*, config: dict[str, Any], path: Path | None, language: str) -> dict[str, Any] | None:
    servers = [dict(item) for item in config.get('servers', []) if isinstance(item, dict)]
    suffix = (path.suffix.casefold() if path is not None else '')
    normalized_language = str(language or '').casefold()
    for server in servers:
        languages = {str(item).casefold() for item in server.get('languages', []) if str(item).strip()}
        extensions = {str(item).casefold() for item in server.get('file_extensions', []) if str(item).strip()}
        if normalized_language and normalized_language in languages:
            return server
        if suffix and suffix in extensions:
            return server
    return servers[0] if servers and not normalized_language and not suffix else None


async def _run_lsp_operation(
    *,
    client: '_StdioLspClient',
    operation: str,
    root: Path,
    path: Path | None,
    symbol: str,
    arguments: dict[str, Any],
    max_results: int,
) -> dict[str, Any]:
    if operation == 'document_symbols':
        response = await client.request('textDocument/documentSymbol', {'textDocument': {'uri': path.as_uri() if path is not None else ''}})
        return {'results': _convert_document_symbols(response, root=root)[:max_results]}
    if operation == 'workspace_symbols':
        response = await client.request('workspace/symbol', {'query': symbol})
        return {'results': _convert_symbol_locations(response, root=root)[:max_results]}
    if operation == 'definition':
        response = await client.request(
            'textDocument/definition',
            {
                'textDocument': {'uri': path.as_uri() if path is not None else ''},
                'position': _lsp_position(arguments),
            },
        )
        return {'results': _convert_symbol_locations(response, root=root)[:max_results]}
    if operation == 'references':
        response = await client.request(
            'textDocument/references',
            {
                'textDocument': {'uri': path.as_uri() if path is not None else ''},
                'position': _lsp_position(arguments),
                'context': {'includeDeclaration': True},
            },
        )
        return {'results': _convert_symbol_locations(response, root=root)[:max_results]}
    if operation == 'hover':
        response = await client.request(
            'textDocument/hover',
            {
                'textDocument': {'uri': path.as_uri() if path is not None else ''},
                'position': _lsp_position(arguments),
            },
        )
        return {'results': _convert_hover(response, root=root, path=path)}
    if operation == 'diagnostics':
        response = await client.request('textDocument/diagnostic', {'textDocument': {'uri': path.as_uri() if path is not None else ''}})
        return {'results': _convert_diagnostics(response, root=root, path=path)}
    return {'results': []}


def _lsp_position(arguments: dict[str, Any]) -> dict[str, int]:
    return {
        'line': max(0, int(arguments.get('line', 1) or 1) - 1),
        'character': max(0, int(arguments.get('character', 0) or 0)),
    }


def _language_id(*, language: str, path: Path | None) -> str:
    if language:
        return language
    if path is not None and path.suffix == '.py':
        return 'python'
    if path is not None and path.suffix in {'.ts', '.tsx'}:
        return 'typescript'
    if path is not None and path.suffix in {'.js', '.jsx'}:
        return 'javascript'
    return 'plaintext'


def _client_capabilities() -> dict[str, Any]:
    return {
        'textDocument': {
            'synchronization': {
                'dynamicRegistration': False,
                'willSave': False,
                'willSaveWaitUntil': False,
                'didSave': True,
            },
            'hover': {
                'dynamicRegistration': False,
                'contentFormat': ['markdown', 'plaintext'],
            },
            'definition': {
                'dynamicRegistration': False,
                'linkSupport': True,
            },
            'references': {'dynamicRegistration': False},
            'documentSymbol': {
                'dynamicRegistration': False,
                'hierarchicalDocumentSymbolSupport': True,
            },
            'publishDiagnostics': {
                'relatedInformation': True,
                'tagSupport': {'valueSet': [1, 2]},
            },
        },
        'workspace': {
            'symbol': {'dynamicRegistration': False},
            'workspaceFolders': False,
            'configuration': False,
        },
        'window': {'workDoneProgress': False},
    }


def _read_lsp_message_blocking(stream: Any) -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = stream.readline()
        if not line:
            return None
        decoded = line.decode('ascii', errors='replace').strip()
        if not decoded:
            break
        if ':' in decoded:
            key, value = decoded.split(':', 1)
            headers[key.casefold()] = value.strip()
    length = int(headers.get('content-length', '0') or 0)
    if length <= 0:
        return None
    body = stream.read(length)
    if not body:
        return None
    return json.loads(body.decode('utf-8'))


@asynccontextmanager
async def _claim_lsp_lock(lock: Any) -> AsyncIterator[None]:
    # Sessions survive individual event loops; use a nonblocking, cross-loop lock.
    while not lock.acquire(blocking=False):
        await asyncio.sleep(0.01)
    try:
        yield
    finally:
        lock.release()


class _StdioLspClient:
    def __init__(
        self,
        *,
        command: list[str],
        root: Path,
        timeout_seconds: int,
        env: dict[str, str] | None = None,
        run_id: str = '',
        session_id: str = '',
        effective_permissions: EffectivePermissions,
    ) -> None:
        self.command = command
        self._permissions = effective_permissions
        self.root = root
        self.timeout_seconds = timeout_seconds
        self._next_id = 1
        self._stderr_chunks: list[str] = []
        self._stderr_lock = threading.Lock()
        self._stdout_queue: queue.Queue[dict[str, Any] | BaseException] = queue.Queue()
        process_env = build_subprocess_env(extra_env=env)
        wrapped_command = SandboxManager().wrap_command(
            SandboxCommandRequest(
                argv=tuple(command),
                cwd=root.resolve(),
                env=process_env,
                effective_permissions=effective_permissions,
                timeout_seconds=max(1, timeout_seconds),
            )
        )
        self._process = popen_with_process_group(
            list(wrapped_command),
            cwd=str(root),
            env=process_env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if self._process.stdin is not None:
            os.set_blocking(self._process.stdin.fileno(), False)
        self._stdout_thread = threading.Thread(target=self._drain_stdout, name='ownpi-lsp-stdout', daemon=True)
        self._stdout_thread.start()
        self._stderr_thread = threading.Thread(target=self._drain_stderr, name='ownpi-lsp-stderr', daemon=True)
        self._stderr_thread.start()

    async def initialize(self, *, initialization_options: dict[str, Any]) -> dict[str, Any]:
        result = await self.request(
            'initialize',
            {
                'processId': None,
                'rootUri': self.root.as_uri(),
                'capabilities': _client_capabilities(),
                'initializationOptions': initialization_options,
            },
        )
        await self.notify('initialized', {})
        return dict(result) if isinstance(result, dict) else {}

    async def did_open(self, path: Path, *, language_id: str) -> None:
        require_file_access(self._permissions, path, FileSystemAccessMode.READ)
        text = path.read_text(encoding='utf-8', errors='replace')
        await self.notify(
            'textDocument/didOpen',
            {
                'textDocument': {
                    'uri': path.as_uri(),
                    'languageId': language_id,
                    'version': 1,
                    'text': text,
                }
            },
        )

    async def did_change(self, path: Path, *, version: int) -> None:
        require_file_access(self._permissions, path, FileSystemAccessMode.READ)
        text = path.read_text(encoding='utf-8', errors='replace')
        await self.notify(
            'textDocument/didChange',
            {
                'textDocument': {'uri': path.as_uri(), 'version': int(version)},
                'contentChanges': [{'text': text}],
            },
        )

    async def did_save(self, path: Path) -> None:
        await self.notify('textDocument/didSave', {'textDocument': {'uri': path.as_uri()}})

    async def did_close(self, path: Path) -> None:
        await self.notify('textDocument/didClose', {'textDocument': {'uri': path.as_uri()}})

    async def request(self, method: str, params: dict[str, Any]) -> Any:
        request_id = self._next_id
        self._next_id += 1
        deadline = time.monotonic() + self.timeout_seconds
        await self._write({'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params}, deadline=deadline)
        while True:
            message = await self._read(deadline=deadline)
            if message is None:
                raise TimeoutError(f'LSP request timed out: {method}')
            if message.get('id') != request_id:
                continue
            if isinstance(message.get('error'), dict):
                raise RuntimeError(str(message['error'].get('message', message['error'])))
            return message.get('result')

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        await self._write({'jsonrpc': '2.0', 'method': method, 'params': params})

    def close(self, *, graceful: bool = True) -> None:
        # Teardown also runs at interpreter exit, outside an event loop.
        asyncio.run(self.aclose(graceful=graceful))

    async def aclose(self, *, graceful: bool = False) -> None:
        try:
            if graceful and self._process.poll() is None:
                try:
                    await self.request('shutdown', {})
                    await self.notify('exit', {})
                    deadline = time.monotonic() + self.timeout_seconds
                    while self._process.poll() is None and time.monotonic() < deadline:
                        await asyncio.sleep(0.01)
                except Exception:
                    pass
        finally:
            await reap_owned_process(self._process)
            for thread in (self._stdout_thread, self._stderr_thread):
                while thread.is_alive():
                    await asyncio.sleep(0.01)
            for pipe in (self._process.stdin, self._process.stdout, self._process.stderr):
                if pipe is not None:
                    pipe.close()

    async def _write(self, payload: dict[str, Any], *, deadline: float | None = None) -> None:
        if self._process.stdin is None:
            raise RuntimeError('LSP stdin unavailable')
        body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        header = f'Content-Length: {len(body)}\r\n\r\n'.encode('ascii')
        remaining = memoryview(header + body)
        deadline = deadline if deadline is not None else time.monotonic() + self.timeout_seconds
        while remaining:
            if time.monotonic() >= deadline:
                raise TimeoutError('LSP stdin write timed out')
            try:
                written = os.write(self._process.stdin.fileno(), remaining)
            except BlockingIOError:
                await asyncio.sleep(0.01)
                continue
            if written == 0:
                raise BrokenPipeError('LSP stdin closed')
            remaining = remaining[written:]
            if remaining:
                await asyncio.sleep(0)

    async def _read(self, *, deadline: float) -> dict[str, Any] | None:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                item = self._stdout_queue.get_nowait()
                break
            except queue.Empty:
                if self._process.poll() is not None:
                    raise RuntimeError(self.stderr_preview() or 'LSP server exited')
                await asyncio.sleep(min(0.01, remaining))
        if isinstance(item, BaseException):
            raise RuntimeError(str(item) or self.stderr_preview() or 'LSP reader failed')
        return item

    def is_alive(self) -> bool:
        return self._process.poll() is None and self._process.stdin is not None and self._process.stdout is not None

    def stderr_preview(self, *, limit: int = 2000) -> str:
        with self._stderr_lock:
            text = ''.join(self._stderr_chunks)
        return text[-limit:].strip()

    def _drain_stderr(self) -> None:
        stream = self._process.stderr
        if stream is None:
            return
        try:
            while True:
                chunk = stream.readline()
                if not chunk:
                    break
                decoded = chunk.decode('utf-8', errors='replace')
                with self._stderr_lock:
                    self._stderr_chunks.append(decoded)
                    if len(self._stderr_chunks) > 80:
                        self._stderr_chunks = self._stderr_chunks[-80:]
        except Exception:
            return

    def _drain_stdout(self) -> None:
        stream = self._process.stdout
        if stream is None:
            self._stdout_queue.put(RuntimeError('LSP stdout unavailable'))
            return
        try:
            while True:
                message = _read_lsp_message_blocking(stream)
                if message is None:
                    if self._process.poll() is not None:
                        self._stdout_queue.put(RuntimeError(self.stderr_preview() or 'LSP server exited'))
                    return
                self._stdout_queue.put(message)
        except Exception as exc:
            self._stdout_queue.put(exc)


@dataclass
class _LspSession:
    key: _LspSessionKey
    server_name: str
    client: _StdioLspClient
    capabilities: dict[str, Any]
    session_id: str
    started_at: float
    lock: Any = field(default_factory=threading.Lock)
    request_count: int = 0
    opened_documents: set[str] = field(default_factory=set)
    document_versions: dict[str, int] = field(default_factory=dict)

    async def ensure_document_open(self, path: Path, *, language_id: str) -> None:
        uri = path.as_uri()
        if uri in self.opened_documents:
            return
        await self.client.did_open(path, language_id=language_id)
        self.opened_documents.add(uri)
        self.document_versions[uri] = 1

    async def notify_document_saved(self, path: Path) -> bool:
        uri = path.as_uri()
        if uri not in self.opened_documents:
            return False
        version = int(self.document_versions.get(uri, 1) or 1) + 1
        await self.client.did_change(path, version=version)
        await self.client.did_save(path)
        self.document_versions[uri] = version
        return True

    async def close_document(self, path: Path) -> bool:
        uri = path.as_uri()
        if uri not in self.opened_documents:
            return False
        await self.client.did_close(path)
        self.opened_documents.discard(uri)
        self.document_versions.pop(uri, None)
        return True


class _LspSessionManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._creation_lock = threading.Lock()
        self._sessions: dict[_LspSessionKey, _LspSession] = {}

    async def acquire(
        self,
        *,
        command: list[str],
        env: dict[str, str],
        root: Path,
        server_name: str,
        initialization_options: dict[str, Any],
        timeout_seconds: int,
        run_id: str = '',
        effective_permissions: EffectivePermissions | None = None,
        canonical_action_hash: str = '',
        canonical_effect_hash: str = '',
    ) -> tuple[_LspSession, bool]:
        if effective_permissions is None:
            raise RuntimeError('configured LSP requires canonical effective permissions')
        if not canonical_action_hash or not canonical_effect_hash:
            raise RuntimeError('configured LSP requires canonical action/effect identity')
        key = (
            str(root.resolve()),
            server_name,
            tuple(command),
            tuple(sorted((str(k), str(v)) for k, v in env.items())),
            str(run_id or ''),
            effective_permissions.policy_hash,
            canonical_action_hash,
            canonical_effect_hash,
        )
        async with _claim_lsp_lock(self._creation_lock):
            with self._lock:
                existing = self._sessions.get(key)
            if existing is not None and existing.client.is_alive():
                return existing, True
            if existing is not None:
                await existing.client.aclose()
                with self._lock:
                    self._sessions.pop(key, None)
            client = _StdioLspClient(
                command=command,
                root=root,
                timeout_seconds=timeout_seconds,
                env=env,
                run_id=run_id,
                session_id=f'lsp:{server_name}',
                effective_permissions=effective_permissions,
            )
            try:
                initialize_result = await client.initialize(initialization_options=initialization_options)
            except (Exception, asyncio.CancelledError):
                await client.aclose()
                raise
            session = _LspSession(
                key=key,
                server_name=server_name,
                client=client,
                capabilities=(
                    dict(initialize_result.get('capabilities', {})) if isinstance(initialize_result, dict) else {}
                ),
                session_id=_new_lsp_session_id(key),
                started_at=time.time(),
            )
            with self._lock:
                self._sessions[key] = session
            return session, False

    async def discard(self, key: _LspSessionKey) -> None:
        with self._lock:
            session = self._sessions.pop(key, None)
        if session is not None:
            await session.client.aclose()

    def close_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.client.close()

    async def notify_file_saved(self, *, root: Path, path: Path) -> int:
        root_resolved = str(root.resolve())
        notified = 0
        with self._lock:
            sessions = [session for key, session in self._sessions.items() if key[0] == root_resolved]
        for session in sessions:
            if not session.client.is_alive():
                continue
            async with _claim_lsp_lock(session.lock):
                try:
                    if await session.notify_document_saved(path):
                        notified += 1
                except asyncio.CancelledError:
                    await self.discard(session.key)
                    raise
                except Exception:
                    await self.discard(session.key)
        return notified


_LSP_SESSION_MANAGER = _LspSessionManager()


def _get_lsp_session_manager() -> _LspSessionManager:
    return _LSP_SESSION_MANAGER


def _new_lsp_session_id(key: _LspSessionKey) -> str:
    payload = json.dumps(
        {
            'root': key[0],
            'server_name': key[1],
            'command': list(key[2]),
            'env_keys': [item[0] for item in key[3]],
            'run_id': key[4],
            'permission_policy_hash': key[5],
            'started_at_ns': time.time_ns(),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]


def _reset_lsp_session_manager_for_tests() -> None:
    _LSP_SESSION_MANAGER.close_all()


async def notify_lsp_file_saved(*, root: Path, path: Path) -> int:
    """Notify active LSP sessions that an already-open file changed on disk."""

    return await _LSP_SESSION_MANAGER.notify_file_saved(root=root, path=path)


atexit.register(_LSP_SESSION_MANAGER.close_all)


def _convert_document_symbols(value: Any, *, root: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    def visit(item: dict[str, Any], parent: str = '') -> None:
        name = str(item.get('name', '') or '')
        range_payload = item.get('selectionRange') if isinstance(item.get('selectionRange'), dict) else item.get('range', {})
        start = dict(range_payload.get('start', {})) if isinstance(range_payload, dict) else {}
        results.append(
            {
                'name': name,
                'container_name': parent,
                'kind': _LSP_KIND_NAMES.get(int(item.get('kind', 0) or 0), str(item.get('kind', 'symbol'))),
                'line': int(start.get('line', 0) or 0) + 1,
                'character': int(start.get('character', 0) or 0),
            }
        )
        for child in item.get('children', []) if isinstance(item.get('children'), list) else []:
            if isinstance(child, dict):
                visit(child, parent=name or parent)

    if isinstance(value, list):
        for entry in value:
            if isinstance(entry, dict):
                if 'location' in entry:
                    results.extend(_convert_symbol_locations([entry], root=root))
                else:
                    visit(entry)
    return results


def _convert_symbol_locations(value: Any, *, root: Path) -> list[dict[str, Any]]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    results: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        location = item.get('location') if isinstance(item.get('location'), dict) else item
        uri = str(location.get('uri') or location.get('targetUri') or '')
        range_payload = location.get('range') if isinstance(location.get('range'), dict) else location.get('targetSelectionRange', {})
        start = dict(range_payload.get('start', {})) if isinstance(range_payload, dict) else {}
        path = _path_from_uri(uri)
        results.append(
            {
                'name': str(item.get('name', '') or ''),
                'kind': _LSP_KIND_NAMES.get(int(item.get('kind', 0) or 0), str(item.get('kind', 'symbol'))),
                'path': display_path(path, base=root) if path is not None else uri,
                'line': int(start.get('line', 0) or 0) + 1,
                'character': int(start.get('character', 0) or 0),
            }
        )
    return results


def _convert_hover(value: Any, *, root: Path, path: Path | None) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    contents = value.get('contents')
    if isinstance(contents, dict):
        text = str(contents.get('value') or contents.get('language') or '')
    elif isinstance(contents, list):
        text = '\n'.join(str(item.get('value', item)) if isinstance(item, dict) else str(item) for item in contents)
    else:
        text = str(contents or '')
    return [
        {
            'path': display_path(path, base=root) if path is not None else '',
            'snippet': _truncate(text),
        }
    ] if text.strip() else []


def _convert_diagnostics(value: Any, *, root: Path, path: Path | None) -> list[dict[str, Any]]:
    diagnostics = []
    if isinstance(value, dict) and isinstance(value.get('items'), list):
        diagnostics = value.get('items', [])
    elif isinstance(value, list):
        diagnostics = value
    results: list[dict[str, Any]] = []
    for item in diagnostics:
        if not isinstance(item, dict):
            continue
        range_payload = item.get('range') if isinstance(item.get('range'), dict) else {}
        start = dict(range_payload.get('start', {})) if isinstance(range_payload, dict) else {}
        results.append(
            {
                'severity': str(item.get('severity', '')),
                'message': str(item.get('message', '') or ''),
                'path': display_path(path, base=root) if path is not None else '',
                'line': int(start.get('line', 0) or 0) + 1,
                'character': int(start.get('character', 0) or 0),
            }
        )
    return results


def _path_from_uri(uri: str) -> Path | None:
    if not uri:
        return None
    parsed = urlparse(uri)
    if parsed.scheme != 'file':
        return None
    return Path(unquote(parsed.path))


def _record_lsp_status(context: Any, status: dict[str, Any]) -> None:
    state_store = getattr(context, 'state_store', None)
    state = getattr(state_store, 'state', None)
    environment_state = getattr(state, 'environment_state', None)
    if environment_state is None:
        return
    payload = {str(key): value for key, value in status.items()}
    existing = list(getattr(environment_state, 'latest_lsp_status', []) or [])
    existing.append(payload)
    environment_state.latest_lsp_status = existing[-12:]


def _record_lsp_runtime_task(
    context: Any,
    *,
    session: _LspSession,
    server_name: str,
    status: str,
    description: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    state = getattr(getattr(context, 'state_store', None), 'state', None)
    if state is None:
        return
    task_id = runtime_task_id_for_lsp(session.session_id)
    payload = {
        'server_name': server_name,
        'lsp_session_id': session.session_id,
        'process_session_id': f'lsp:{server_name}',
        **dict(metadata or {}),
    }
    if status == 'failed':
        fail_runtime_task(state, task_id=task_id, status='failed', metadata=payload)
        return
    existing = state.runtime_tasks.get(task_id)
    if existing is None:
        register_runtime_task(
            state,
            task_id=task_id,
            task_type='lsp',
            status=status,
            description=description,
            provider_kind='lsp_stdio',
            run_id=_run_id_from_context(context),
            stage=getattr(getattr(context, 'stage', None), 'value', str(getattr(context, 'stage', '') or '')),
            attempt=int(getattr(context, 'attempt', 0) or 0),
            session_id=f'lsp:{server_name}',
            metadata=payload,
        )
        return
    update_runtime_task(
        state,
        task_id=task_id,
        status=status,
        description=description,
        metadata=payload,
    )


def _document_symbols(root: Path, path: Path | None, *, language: str, max_results: int) -> dict[str, Any]:
    if path is None:
        return _fallback_result([], fallback_mode='no_file')
    text = path.read_text(encoding='utf-8', errors='replace')
    if language in {'python', 'py'} or path.suffix == '.py':
        tree = ast.parse(text)
        results = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                results.append(
                    {
                        'name': node.name,
                        'kind': 'class' if isinstance(node, ast.ClassDef) else 'function',
                        'path': display_path(path, base=root),
                        'line': int(getattr(node, 'lineno', 1)),
                        'character': int(getattr(node, 'col_offset', 0)),
                    }
                )
        return _fallback_result(results[:max_results], fallback_mode='python_ast')
    results = []
    for index, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if re.match(r'^(class|def|function|const|let|var)\s+[A-Za-z_][A-Za-z0-9_]*', stripped):
            results.append(
                {
                    'name': stripped[:80],
                    'kind': 'symbol',
                    'path': display_path(path, base=root),
                    'line': index,
                    'character': max(0, line.find(stripped)),
                }
            )
            if len(results) >= max_results:
                break
    return _fallback_result(results, fallback_mode='regex_symbols')


def _diagnostics(root: Path, path: Path | None, *, language: str) -> dict[str, Any]:
    if path is None:
        return _fallback_result([], fallback_mode='no_file')
    if language not in {'python', 'py', ''} and path.suffix != '.py':
        return _fallback_result([], fallback_mode='no_language_server')
    try:
        ast.parse(path.read_text(encoding='utf-8', errors='replace'))
    except SyntaxError as exc:
        return {
            'results': [
                {
                    'severity': 'error',
                    'message': exc.msg,
                    'path': display_path(path, base=root),
                    'line': exc.lineno or 0,
                    'character': exc.offset or 0,
                }
            ],
            **_fallback_meta('python_ast_parse'),
        }
    return _fallback_result([], fallback_mode='python_ast_parse')


def _hover(root: Path, path: Path | None, arguments: dict[str, Any], *, symbol: str) -> dict[str, Any]:
    if path is None:
        return _fallback_result([], fallback_mode='no_file')
    lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    line_index = max(0, int(arguments.get('line', 1) or 1) - 1)
    if line_index >= len(lines):
        line_index = max(0, len(lines) - 1)
    snippet_lines = lines[max(0, line_index - 2): min(len(lines), line_index + 3)]
    return {
        'results': [
            {
                'symbol': symbol,
                'path': display_path(path, base=root),
                'line': line_index + 1,
                'snippet': _truncate('\n'.join(snippet_lines)),
            }
        ],
        **_fallback_meta('line_context'),
    }


def _definition(root: Path, path: Path | None, *, symbol: str, max_results: int, permissions: EffectivePermissions) -> dict[str, Any]:
    if not symbol:
        return _fallback_result([], fallback_mode='missing_symbol')
    pattern = re.compile(rf'^\s*(class|def|async\s+def|function|const|let|var)\s+{re.escape(symbol)}\b')
    return _search_files(root, path, pattern=pattern, max_results=max_results,
                         permissions=permissions, fallback_mode='bounded_grep_definition')


def _references(root: Path, path: Path | None, *, symbol: str, max_results: int, permissions: EffectivePermissions) -> dict[str, Any]:
    if not symbol:
        return _fallback_result([], fallback_mode='missing_symbol')
    pattern = re.compile(rf'\b{re.escape(symbol)}\b')
    return _search_files(root, path, pattern=pattern, max_results=max_results,
                         permissions=permissions, fallback_mode='bounded_grep_references')


def _workspace_symbols(root: Path, *, symbol: str, max_results: int, permissions: EffectivePermissions) -> dict[str, Any]:
    if not symbol:
        return _fallback_result([], fallback_mode='missing_symbol')
    pattern = re.compile(rf'\b{re.escape(symbol)}\b')
    return _search_files(root, None, pattern=pattern, max_results=max_results,
                         permissions=permissions, fallback_mode='bounded_grep_workspace_symbols')


def _search_files(
    root: Path, path: Path | None, *, pattern: re.Pattern[str], max_results: int,
    permissions: EffectivePermissions, fallback_mode: str,
) -> dict[str, Any]:
    denied: list[Path] = []
    matcher = FileSystemPolicyMatcher(permissions.profile.file_system)
    files = [path] if path is not None else _iter_candidate_files(root, matcher, denied)
    results = _grep_files(root, files, pattern=pattern, max_results=max_results,
                          matcher=matcher, denied=denied, selected_target=path is not None)
    return {
        **_fallback_result(results, fallback_mode=fallback_mode),
        'coverage_restricted': bool(denied),
        'permission_skipped_count': len(denied),
    }


def _grep_files(root: Path, files: Iterable[Path], *, pattern: re.Pattern[str], max_results: int,
                matcher: FileSystemPolicyMatcher, denied: list[Path], selected_target: bool) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for file_path in files:
        if file_path is None:
            continue
        if not matcher.can_read(file_path):
            denied.append(file_path)
            continue
        try:
            if not file_path.is_file():
                continue
            lines = file_path.read_text(encoding='utf-8', errors='replace').splitlines()
        except PermissionError:
            if selected_target:
                raise
            denied.append(file_path)
            continue
        except OSError:
            continue
        for line_number, line in enumerate(lines, start=1):
            match = pattern.search(line)
            if not match:
                continue
            results.append(
                {
                    'path': display_path(file_path, base=root),
                    'line': line_number,
                    'character': int(match.start()),
                    'snippet': _truncate(line.strip()),
                }
            )
            if len(results) >= max_results:
                return results
    return results


def _iter_candidate_files(
    root: Path, matcher: FileSystemPolicyMatcher, denied: list[Path],
) -> Iterator[Path]:
    if not matcher.can_read(root):
        denied.append(root)
        return
    def on_walk_error(error: OSError) -> None:
        if isinstance(error, PermissionError):
            denied.append(Path(error.filename) if error.filename else root)

    for dirpath, dirnames, filenames in os.walk(root, onerror=on_walk_error):
        allowed_dirs = []
        for name in dirnames:
            if name in _SKIP_DIRS:
                continue
            candidate = Path(dirpath) / name
            if matcher.can_read(candidate):
                allowed_dirs.append(name)
            else:
                denied.append(candidate)
        dirnames[:] = allowed_dirs
        for filename in filenames:
            path = Path(dirpath) / filename
            if path.suffix.lower() in {'.py', '.js', '.ts', '.tsx', '.jsx', '.md', '.json', '.yaml', '.yml', '.toml'}:
                yield path


def _fallback_result(results: list[dict[str, Any]], *, fallback_mode: str) -> dict[str, Any]:
    return {'results': results, **_fallback_meta(fallback_mode)}


def _fallback_meta(fallback_mode: str) -> dict[str, Any]:
    return {
        'backend': _FALLBACK_BACKEND,
        'lsp_available': False,
        'backend_status': 'no_lsp_server_configured',
        'fallback_mode': fallback_mode,
    }


def _clamp_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value if value is not None else default)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(MAX_LSP_RESULTS_PER_REQUEST, parsed))


def _truncate(text: str) -> str:
    if len(text) <= MAX_LSP_SNIPPET_CHARS:
        return text
    return text[: MAX_LSP_SNIPPET_CHARS - 18] + '...[truncated]'


def _summary_line(*, operation: str, result: dict[str, Any]) -> str:
    count = len(result.get('results', [])) if isinstance(result.get('results'), list) else 0
    backend = str(result.get('backend', _FALLBACK_BACKEND) or _FALLBACK_BACKEND)
    status = str(result.get('backend_status', '') or result.get('fallback_mode', 'fallback'))
    return f'lsp_query {operation}: {count} result(s) via {backend}/{status}'
