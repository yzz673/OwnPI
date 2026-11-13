from __future__ import annotations

from dataclasses import dataclass
import asyncio
import fcntl
from contextlib import contextmanager, asynccontextmanager
import json
import os
from pathlib import Path
import secrets
import time
from typing import Any, AsyncIterator
from urllib.parse import parse_qs, urlencode, urlparse
import webbrowser

import httpx


class MCPOAuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class MCPOAuthToken:
    access_token: str
    refresh_token: str = ''
    expires_at: float = 0.0
    token_type: str = 'Bearer'

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'MCPOAuthToken':
        return cls(
            access_token=str(data.get('access_token', '') or ''),
            refresh_token=str(data.get('refresh_token', '') or ''),
            expires_at=float(data.get('expires_at', 0.0) or 0.0),
            token_type=str(data.get('token_type', 'Bearer') or 'Bearer'),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            'access_token': self.access_token,
            'refresh_token': self.refresh_token,
            'expires_at': self.expires_at,
            'token_type': self.token_type,
        }

    def is_expired(self, *, skew_seconds: int = 60) -> bool:
        return bool(self.expires_at and time.time() >= self.expires_at - max(0, skew_seconds))


def mcp_oauth_token_path() -> Path:
    override = str(os.environ.get('OWNPI_MCP_OAUTH_TOKEN_FILE', '') or '').strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / '.ownpi' / 'mcp-oauth-tokens.json'


def load_mcp_oauth_token(server_name: str) -> MCPOAuthToken | None:
    data = _load_token_store()
    payload = data.get(str(server_name or ''))
    if not isinstance(payload, dict):
        return None
    token = MCPOAuthToken.from_dict(payload)
    return token if token.access_token else None


def save_mcp_oauth_token(server_name: str, token: MCPOAuthToken) -> None:
    path = mcp_oauth_token_path()
    with _oauth_token_store_lock(path):
        data = _load_token_store_unlocked(path)
        data[str(server_name or '')] = token.to_dict()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + '.tmp')
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')
        tmp_path.replace(path)
        try:
            path.chmod(0o600)
        except OSError:
            pass


def clear_mcp_oauth_token(server_name: str) -> None:
    path = mcp_oauth_token_path()
    with _oauth_token_store_lock(path):
        data = _load_token_store_unlocked(path)
        data.pop(str(server_name or ''), None)
        if data:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_suffix(path.suffix + '.tmp')
            tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')
            tmp_path.replace(path)
            try:
                path.chmod(0o600)
            except OSError:
                pass
        else:
            try:
                path.unlink()
            except FileNotFoundError:
                pass


async def mcp_authorization_header(server_config: dict[str, Any], *, server_name: str) -> str:
    token = _token_from_env_auth(server_config)
    if token:
        return token
    stored = load_mcp_oauth_token(server_name)
    if stored is None:
        return ''
    if stored.is_expired():
        refreshed = await refresh_mcp_oauth_token(server_config, server_name=server_name, token=stored)
        if refreshed is None:
            return ''
        stored = refreshed
    return f'{stored.token_type} {stored.access_token}'.strip()


async def refresh_mcp_oauth_token(
    server_config: dict[str, Any],
    *,
    server_name: str,
    token: MCPOAuthToken | None = None,
) -> MCPOAuthToken | None:
    token = token or load_mcp_oauth_token(server_name)
    if token is None or not token.refresh_token:
        return None
    metadata = await discover_mcp_oauth_metadata(server_config)
    token_endpoint = str(metadata.get('token_endpoint', '') or '').strip()
    if not token_endpoint:
        return None
    oauth_cfg = dict(server_config.get('oauth', {}) if isinstance(server_config.get('oauth'), dict) else {})
    client_id = str(oauth_cfg.get('client_id', oauth_cfg.get('clientId', 'ownpi')) or 'ownpi')
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.post(
                token_endpoint,
                data={
                    'grant_type': 'refresh_token',
                    'refresh_token': token.refresh_token,
                    'client_id': client_id,
                },
                timeout=max(1, int(server_config.get('timeout_seconds', 20) or 20)),
            )
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    refreshed = _token_from_token_response(response.json(), fallback_refresh_token=token.refresh_token)
    await _save_mcp_oauth_token(server_name, refreshed)
    return refreshed


async def discover_mcp_oauth_metadata(server_config: dict[str, Any]) -> dict[str, Any]:
    oauth_cfg = dict(server_config.get('oauth', {}) if isinstance(server_config.get('oauth'), dict) else {})
    configured = str(oauth_cfg.get('auth_server_metadata_url', oauth_cfg.get('authServerMetadataUrl', '')) or '').strip()
    candidates = []
    if configured:
        candidates.append(configured)
    base_url = str(server_config.get('url', '') or '').strip()
    if base_url:
        parsed = urlparse(base_url)
        if parsed.scheme and parsed.netloc:
            candidates.append(f'{parsed.scheme}://{parsed.netloc}/.well-known/oauth-authorization-server')
    for url in candidates:
        try:
            async with httpx.AsyncClient(trust_env=False) as client:
                response = await client.get(
                    url,
                    timeout=max(1, int(server_config.get('timeout_seconds', 20) or 20)),
                )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            continue
        if isinstance(payload, dict):
            return dict(payload)
    return {}


async def perform_mcp_oauth_browser_flow(server_config: dict[str, Any], *, server_name: str) -> dict[str, Any]:
    metadata = await discover_mcp_oauth_metadata(server_config)
    authorization_endpoint = str(metadata.get('authorization_endpoint', '') or '').strip()
    token_endpoint = str(metadata.get('token_endpoint', '') or '').strip()
    if not authorization_endpoint or not token_endpoint:
        raise MCPOAuthError('OAuth metadata did not provide authorization_endpoint and token_endpoint')
    oauth_cfg = dict(server_config.get('oauth', {}) if isinstance(server_config.get('oauth'), dict) else {})
    callback_port = int(oauth_cfg.get('callback_port', oauth_cfg.get('callbackPort', 0)) or 0)
    client_id = str(oauth_cfg.get('client_id', oauth_cfg.get('clientId', 'ownpi')) or 'ownpi')
    state = secrets.token_urlsafe(24)
    code, redirect_uri = await _wait_for_oauth_code(
        authorization_endpoint=authorization_endpoint,
        client_id=client_id,
        callback_port=callback_port,
        state=state,
    )
    async with httpx.AsyncClient(trust_env=False) as client:
        response = await client.post(
            token_endpoint,
            data={
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': redirect_uri,
                'client_id': client_id,
            },
            timeout=max(1, int(server_config.get('timeout_seconds', 20) or 20)),
        )
    response.raise_for_status()
    token = _token_from_token_response(response.json())
    await _save_mcp_oauth_token(server_name, token)
    return {
        'server_name': server_name,
        'status': 'authenticated',
        'token_type': token.token_type,
        'expires_at': token.expires_at,
        'token_file': str(mcp_oauth_token_path()),
    }


def _load_token_store() -> dict[str, dict[str, Any]]:
    return _load_token_store_unlocked(mcp_oauth_token_path())


def _load_token_store_unlocked(path: Path) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError, OSError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): dict(value) for key, value in payload.items() if isinstance(value, dict)}


@contextmanager
def _oauth_token_store_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + '.lock')
    with lock_path.open('a+', encoding='utf-8') as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _token_from_env_auth(server_config: dict[str, Any]) -> str:
    auth = server_config.get('auth')
    if not isinstance(auth, dict):
        return ''
    token_env = str(auth.get('token_env', '') or '').strip()
    if not token_env:
        return ''
    token = os.environ.get(token_env, '')
    if not token:
        return ''
    scheme = str(auth.get('scheme', 'Bearer') or 'Bearer').strip()
    return f'{scheme} {token}'.strip()


def _token_from_token_response(payload: dict[str, Any], *, fallback_refresh_token: str = '') -> MCPOAuthToken:
    access_token = str(payload.get('access_token', '') or '').strip()
    if not access_token:
        raise MCPOAuthError('OAuth token response did not include access_token')
    expires_in = float(payload.get('expires_in', 0.0) or 0.0)
    return MCPOAuthToken(
        access_token=access_token,
        refresh_token=str(payload.get('refresh_token', fallback_refresh_token) or ''),
        expires_at=(time.time() + expires_in if expires_in > 0 else 0.0),
        token_type=str(payload.get('token_type', 'Bearer') or 'Bearer'),
    )


async def _wait_for_oauth_code(
    *,
    authorization_endpoint: str,
    client_id: str,
    callback_port: int,
    state: str,
) -> tuple[str, str]:
    result = asyncio.get_running_loop().create_future()
    handlers: set[asyncio.Task[None]] = set()
    writers: set[asyncio.StreamWriter] = set()
    transports: set[asyncio.Transport] = set()
    closing = False

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            header = await reader.readuntil(b'\r\n\r\n')
            target = header.split(b' ', 2)[1].decode('ascii')
            query = parse_qs(urlparse(target).query)
            body = b'OwnPI MCP OAuth completed. You can close this tab.'
            writer.write(
                b'HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\n'
                + f'Content-Length: {len(body)}\r\nConnection: close\r\n\r\n'.encode()
                + body
            )
            await writer.drain()
            if not result.done():
                result.set_result({key: str((query.get(key) or [''])[0]) for key in ('code', 'state')})
        except (OSError, ValueError, IndexError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as error:
            if not result.done():
                result.set_exception(MCPOAuthError(f'Invalid OAuth callback: {error}'))
        finally:
            writer.close()
            await writer.wait_closed()

    def connected(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writers.add(writer)
        if closing:
            writer.close()
            return
        task = asyncio.create_task(handle(reader, writer))
        handlers.add(task)

    class CallbackProtocol(asyncio.StreamReaderProtocol):
        def connection_made(self, transport: asyncio.Transport) -> None:
            transports.add(transport)
            super().connection_made(transport)
            if closing:
                transport.close()

    server = await asyncio.get_running_loop().create_server(
        lambda: CallbackProtocol(asyncio.StreamReader(), connected),
        '127.0.0.1', max(0, callback_port),
    )
    host, port = server.sockets[0].getsockname()[:2]
    redirect_uri = f'http://{host}:{port}/callback'
    auth_url = authorization_endpoint + '?' + urlencode(
        {
            'response_type': 'code',
            'client_id': client_id,
            'redirect_uri': redirect_uri,
            'state': state,
        }
    )
    try:
        webbrowser.open(auth_url)
        received = await asyncio.wait_for(result, 300)
    finally:
        closing = True
        server.close()
        for transport in transports:
            transport.close()
        # A handler cancelled before its first step never enters its finally.
        for writer in writers:
            writer.close()
        for task in handlers:
            task.cancel()
        await asyncio.gather(*handlers, return_exceptions=True)
        await asyncio.gather(*(writer.wait_closed() for writer in writers), return_exceptions=True)
        await server.wait_closed()
    if received.get('state') != state:
        raise MCPOAuthError('OAuth callback state mismatch')
    code = str(received.get('code', '') or '').strip()
    if not code:
        raise MCPOAuthError('OAuth callback did not include code')
    return code, redirect_uri


@asynccontextmanager
async def _async_token_store_lock(path: Path) -> AsyncIterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix + '.lock').open('a+', encoding='utf-8') as lock_file:
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                await asyncio.sleep(0.01)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


async def _save_mcp_oauth_token(server_name: str, token: MCPOAuthToken) -> None:
    path = mcp_oauth_token_path()
    async with _async_token_store_lock(path):
        # The replacement is an atomic synchronous commit after lock admission.
        data = _load_token_store_unlocked(path)
        data[str(server_name or '')] = token.to_dict()
        temporary = path.with_suffix(path.suffix + '.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')
        temporary.replace(path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
