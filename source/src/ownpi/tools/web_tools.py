from __future__ import annotations

import asyncio
from contextlib import contextmanager
from html import unescape
import ipaddress
import math
import re
import socket
import tempfile
import time
import urllib.error
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator, Mapping
from uuid import uuid4
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.environment.subprocess_env import build_subprocess_env
from ownpi.runtime.limits.resources import (
    MAX_HTTP_BODY_BYTES,
    MAX_HTTP_HEADER_BYTES,
    MAX_HTTP_REDIRECTS,
)
from ownpi.runtime.permissions.models import EffectivePermissions, FileSystemAccessMode
from ownpi.runtime.permissions.policy import require_file_access
from ownpi.runtime.sandbox import SandboxCommandRequest, SandboxManager
from ownpi.runtime.sandbox.denial import DenialOutputEvidence, is_sandbox_denied
from ownpi.tools.common import clamp_int, metadata_for_context, truncate_text

if TYPE_CHECKING:
    from ownpi.runtime.tools.definitions import ToolExecutionContext


_DEFAULT_USER_AGENT = 'ownpi/0.1 local-tool-runtime'
_DEFAULT_SEARCH_BACKENDS = ('duckduckgo_lite', 'arxiv_api', 'duckduckgo_html', 'duckduckgo_html_www')
_SEARCH_BACKEND_TIMEOUT_CAPS = {
    'duckduckgo_lite': 6,
    'arxiv_api': 10,
    'duckduckgo_html': 8,
    'duckduckgo_html_www': 8,
}
@dataclass(frozen=True)
class BufferedHTTPResponse:
    content: bytes
    url: str
    status_code: int
    headers: Mapping[str, str]

    @property
    def text(self) -> str:
        content_type = str(self.headers.get('content-type', ''))
        charset_match = re.search(r'charset=([^;\s]+)', content_type, re.IGNORECASE)
        charset = charset_match.group(1).strip('"\'') if charset_match else 'utf-8'
        try:
            return self.content.decode(charset, errors='ignore')
        except LookupError:
            return self.content.decode('utf-8', errors='ignore')

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code} for {self.url}')


async def run_web_fetch(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
    url = str(arguments.get('url', '') or '').strip()
    if not url:
        return ToolResult(ok=False, tool_name='web_fetch', error='url is required')
    httpx = _load_httpx()
    timeout = clamp_int(arguments.get('timeout_seconds'), default=20, minimum=1, maximum=120)
    try:
        response = await _http_get(
            httpx=httpx,
            url=url,
            timeout=timeout,
            effective_permissions=context.effective_permissions,
        )
    except Exception as exc:
        failure = _classify_web_fetch_failure(exc, url=url)
        return ToolResult(
            ok=False,
            tool_name='web_fetch',
            error=f'web fetch failed: {failure["message"]}',
            data={
                'url': url,
                'failure_class': 'web_fetch_error',
                'failure_kind': failure['kind'],
                'retryable': failure['retryable'],
                'recommended_next_action': failure['next_action'],
                **({'status_code': failure['status_code']} if failure['status_code'] else {}),
            },
            metadata=metadata_for_context(context, url=url),
        )
    page = _extract_html_summary(response.text)
    title = page['title']
    headings = page['headings']
    text = page['text']
    excerpt, truncated = truncate_text(text, limit=clamp_int(arguments.get('max_chars'), default=12000, minimum=1000, maximum=40000))
    data = {
        'url': url,
        'final_url': str(response.url),
        'status_code': response.status_code,
        'title': title,
        'headings': headings,
        'link_count': int(page['link_count']),
        'text_excerpt': excerpt,
        'truncated': truncated,
    }
    return ToolResult(
        ok=True,
        tool_name='web_fetch',
        data=data,
        stdout=f'final_url={data["final_url"]}\ntitle={title}\nheadings={headings[:10]}\n{excerpt}',
        metadata=metadata_for_context(context, url=url, final_url=str(response.url)),
        warnings=['web text truncated for prompt budget'] if truncated else [],
    )


async def run_web_search(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
    query = str(arguments.get('query', '') or '').strip()
    if not query:
        return ToolResult(ok=False, tool_name='web_search', error='query is required')
    httpx = _load_httpx()
    limit = clamp_int(arguments.get('limit'), default=5, minimum=1, maximum=10)
    timeout = clamp_int(arguments.get('timeout_seconds'), default=18, minimum=3, maximum=60)
    backends = _search_backend_order(
        arguments.get('backend'),
        prefer_arxiv_first=_prefer_arxiv_first(context=context, query=query),
        exclude_arxiv=_is_dataset_or_repository_query(query),
    )
    attempted: list[dict[str, str]] = []
    selected_backend = ''
    selected_url = ''
    results: list[dict[str, str]] = []
    successful_empty_backend = ''
    started_at = time.monotonic()
    for backend in backends:
        url = _search_url_for_backend(query=query, backend=backend)
        selected_url = url
        backend_timeout = _remaining_backend_timeout(
            started_at=started_at,
            total_timeout=timeout,
            backend=backend,
        )
        if backend_timeout <= 0:
            attempted.append({
                'backend': backend,
                'url': url,
                'status': 'skipped',
                'error': 'search timeout budget exhausted',
                'timeout_seconds': '0',
            })
            break
        try:
            response = await _http_get(
                httpx=httpx,
                url=url,
                timeout=backend_timeout,
                effective_permissions=context.effective_permissions,
            )
        except Exception as exc:
            attempted.append({
                'backend': backend,
                'url': url,
                'status': 'failed',
                'error': _exception_summary(exc),
                'timeout_seconds': str(backend_timeout),
            })
            continue
        selected_backend = backend
        parsed_results = _extract_search_results(response.text, limit=limit)
        if parsed_results:
            results = parsed_results
            attempted.append({
                'backend': backend,
                'url': url,
                'status': 'completed',
                'error': '',
                'timeout_seconds': str(backend_timeout),
                'result_count': str(len(results)),
            })
            break
        successful_empty_backend = backend
        attempted.append({
            'backend': backend,
            'url': url,
            'status': 'empty',
            'error': '',
            'timeout_seconds': str(backend_timeout),
        })
    if not results and not selected_backend:
        errors = '; '.join(
            f"{item['backend']}: {item['error']}" for item in attempted if item.get('error')
        )
        return ToolResult(
            ok=False,
            tool_name='web_search',
            error=f'web search failed after {len(attempted)} backend attempt(s): {errors or "no response"}',
            metadata=metadata_for_context(context, query=query, attempted_backends=attempted),
        )
    if not results and successful_empty_backend:
        selected_backend = successful_empty_backend
    data = {
        'query': query,
        'results': results,
        'source_backend': selected_backend,
        'attempted_backends': attempted,
        'timeout_seconds': timeout,
        'timeout_strategy': 'total_budget_with_per_backend_caps',
        'execution_mode': 'local_fallback',
        'evidence_shape': 'candidate_list',
        'degraded': any(item.get('status') == 'failed' for item in attempted),
        'relevance_unverified': (
            selected_backend == 'arxiv_api'
            and not _prefer_arxiv_first(context=context, query=query)
        ),
    }
    stdout = '\n'.join([f'query={query}', *[f"- {item['title']} {item['url']}" for item in results]])
    return ToolResult(
        ok=True,
        tool_name='web_search',
        data=data,
        stdout=stdout,
        metadata=metadata_for_context(
            context,
            query=query,
            source_backend=selected_backend,
            final_url=selected_url,
            attempted_backends=attempted,
        ),
        warnings=[
            warning
            for warning, enabled in (
                ('web_search returned no results', not results),
                ('local web search used a degraded backend route', data['degraded']),
                ('local search result relevance is unverified', data['relevance_unverified']),
            )
            if enabled
        ],
    )


def _load_httpx():
    try:
        import httpx  # type: ignore
        return httpx
    except ImportError:
        return None


async def _http_get(
    *,
    httpx: Any | None,
    url: str,
    timeout: float,
    max_bytes: int | None = None,
    effective_permissions: EffectivePermissions | None = None,
    deadline: float | None = None,
) -> Any:
    _validate_public_http_url(url)
    if httpx is None or _is_real_httpx(httpx):
        return await _curl_pinned_get(
            url=url,
            timeout=timeout,
            max_bytes=max_bytes or MAX_HTTP_BODY_BYTES,
            effective_permissions=effective_permissions,
            **({'deadline': deadline} if deadline is not None else {}),
        )
    if httpx is not None:
        if hasattr(httpx, 'Client'):

            def validate_request(request: Any) -> None:
                _validate_public_http_url(str(request.url))

            with httpx.Client(
                follow_redirects=True,
                max_redirects=MAX_HTTP_REDIRECTS,
                timeout=timeout,
                headers={'User-Agent': _DEFAULT_USER_AGENT},
                event_hooks={'request': [validate_request]},
            ) as client:
                if max_bytes is not None:
                    with client.stream('GET', url) as response:
                        response.raise_for_status()
                        content = _read_bounded_chunks(response.iter_bytes(), max_bytes=max_bytes)
                        return BufferedHTTPResponse(
                            content=content,
                            url=str(response.url),
                            status_code=int(response.status_code),
                            headers={str(key).lower(): str(value) for key, value in response.headers.items()},
                        )
                response = client.get(url)
                response.raise_for_status()
                _validate_public_http_url(str(response.url))
                return response
        response = httpx.get(url, follow_redirects=False, timeout=timeout, headers={'User-Agent': _DEFAULT_USER_AGENT})
        _validate_public_http_url(str(response.url))
        if hasattr(response, 'raise_for_status'):
            response.raise_for_status()
        content = _response_content(response)
        if max_bytes is not None and len(content) > max_bytes:
            raise ValueError(f'HTTP response exceeds {max_bytes} byte limit')
        return response
    request = Request(url, headers={'User-Agent': _DEFAULT_USER_AGENT})
    opener = build_opener(_PublicHTTPRedirectHandler())
    with opener.open(request, timeout=timeout) as response:  # noqa: S310
        content = response.read(max_bytes + 1 if max_bytes is not None else -1)
        if max_bytes is not None and len(content) > max_bytes:
            raise ValueError(f'HTTP response exceeds {max_bytes} byte limit')
        return BufferedHTTPResponse(
            content=content,
            url=str(response.geturl()),
            status_code=int(response.status),
            headers={str(key).lower(): str(value) for key, value in response.headers.items()},
        )


def _is_real_httpx(httpx: Any) -> bool:
    return bool(getattr(httpx, '__version__', '')) and hasattr(httpx, 'Client')


@contextmanager
def _temporary_transfer_files(
    permissions: EffectivePermissions, *, prefix: str, names: tuple[str, ...],
) -> Iterator[Path]:
    """Keep transport scratch I/O under the request's effective file policy."""
    directory = Path(tempfile.gettempdir()) / f'{prefix}{uuid4().hex}'
    paths = tuple(directory / name for name in names)
    for path in (directory, *paths):
        require_file_access(permissions, path, FileSystemAccessMode.WRITE)
        require_file_access(permissions, path, FileSystemAccessMode.READ)
    directory.mkdir(mode=0o700)
    try:
        yield directory
    finally:
        # Check the entire cleanup before deleting anything, including symlink targets.
        for path in (*paths, directory):
            require_file_access(permissions, path, FileSystemAccessMode.WRITE)
        for path in paths:
            path.unlink(missing_ok=True)
        directory.rmdir()


async def _curl_pinned_get(
    *,
    url: str,
    timeout: float,
    max_bytes: int,
    effective_permissions: EffectivePermissions | None,
    deadline: float | None = None,
) -> BufferedHTTPResponse:
    if effective_permissions is None:
        raise RuntimeError('curl transport requires canonical effective permissions')
    current_url = url
    for redirect_index in range(MAX_HTTP_REDIRECTS + 1):
        parsed = urlparse(current_url)
        addresses = await _public_addresses_for_url(current_url)
        request_timeout = min(timeout, deadline - time.monotonic()) if deadline is not None else timeout
        if request_timeout <= 0:
            raise TimeoutError('HTTP total budget exhausted')
        address = addresses[0]
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        resolved_address = f'[{address}]' if ':' in address else address
        resolve_entry = f'{parsed.hostname}:{port}:{resolved_address}'
        with _temporary_transfer_files(
            effective_permissions, prefix='ownpi-http-', names=('headers.txt', 'body.bin'),
        ) as temp_dir:
            headers_path = Path(temp_dir) / 'headers.txt'
            body_path = Path(temp_dir) / 'body.bin'
            command = [
                'curl',
                '--silent',
                '--show-error',
                '--proto',
                '=http,https',
                '--noproxy',
                '*',
                '--max-redirs',
                '0',
                '--connect-timeout',
                str(request_timeout),
                '--max-time',
                str(request_timeout),
                '--max-filesize',
                str(max_bytes),
                '--user-agent',
                _DEFAULT_USER_AGENT,
                '--resolve',
                resolve_entry,
                '--dump-header',
                str(headers_path),
                '--output',
                str(body_path),
                current_url,
            ]
            sandbox_result = await SandboxManager().execute_async(
                SandboxCommandRequest(
                    argv=tuple(command),
                    cwd=Path(temp_dir),
                    env=build_subprocess_env(),
                    effective_permissions=effective_permissions,
                    timeout_seconds=request_timeout if deadline is not None else timeout + 2,
                )
            )
            if sandbox_result.timed_out:
                raise TimeoutError(f'HTTP request exceeded {timeout}s')
            if sandbox_result.exit_code != 0:
                evidence = DenialOutputEvidence()
                evidence.observe('stderr', sandbox_result.stderr)
                evidence.observe('stdout', sandbox_result.stdout)
                if is_sandbox_denied(
                    sandbox_backend=sandbox_result.backend_kind, closed=True,
                    exit_code=sandbox_result.exit_code, matched_marker=evidence.matched_marker,
                    timed_out=sandbox_result.timed_out,
                ):
                    raise PermissionError(evidence.output_excerpt or 'HTTP transport sandbox denied')
                detail = (
                    sandbox_result.stderr
                    or sandbox_result.stdout
                    or 'curl request failed'
                ).strip()
                raise RuntimeError(detail[:1000])
            require_file_access(effective_permissions, headers_path, FileSystemAccessMode.READ)
            header_bytes = headers_path.read_bytes()
            if len(header_bytes) > MAX_HTTP_HEADER_BYTES:
                raise ValueError('HTTP response headers exceed the configured limit')
            status_code, headers = _parse_curl_headers(header_bytes)
            require_file_access(effective_permissions, body_path, FileSystemAccessMode.READ)
            content = body_path.read_bytes()
            if len(content) > max_bytes:
                raise ValueError(f'HTTP response exceeds {max_bytes} byte limit')
        location = str(headers.get('location', '') or '').strip()
        if 300 <= status_code < 400 and location:
            if redirect_index >= MAX_HTTP_REDIRECTS:
                raise ValueError('HTTP redirect limit exceeded')
            current_url = urljoin(current_url, location)
            _validate_public_http_url(current_url)
            continue
        response = BufferedHTTPResponse(
            content=content,
            url=current_url,
            status_code=status_code,
            headers=headers,
        )
        response.raise_for_status()
        return response
    raise ValueError('HTTP redirect limit exceeded')


def _parse_curl_headers(payload: bytes) -> tuple[int, dict[str, str]]:
    text = payload.decode('iso-8859-1', errors='replace').replace('\r\n', '\n')
    blocks = [block for block in text.split('\n\n') if block.lstrip().startswith('HTTP/')]
    lines = (blocks[-1] if blocks else text).splitlines()
    if not lines:
        raise ValueError('HTTP response did not include headers')
    status_match = re.match(r'^HTTP/\S+\s+(\d{3})\b', lines[0].strip())
    if status_match is None:
        raise ValueError('HTTP response status line is invalid')
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line.strip():
            break
        name, separator, value = line.partition(':')
        if separator:
            headers[name.strip().casefold()] = value.strip()
    return int(status_match.group(1)), headers


async def _public_addresses_for_url(url: str) -> tuple[str, ...]:
    parsed = urlparse(str(url or '').strip())
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        raise ValueError('URL must use HTTP(S) with a hostname')
    host = parsed.hostname.rstrip('.').casefold()
    addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    try:
        addresses.add(ipaddress.ip_address(host))
    except ValueError:
        try:
            resolved = await asyncio.get_running_loop().getaddrinfo(
                host,
                parsed.port or (443 if parsed.scheme == 'https' else 80),
                type=socket.SOCK_STREAM,
            )
            for item in resolved:
                addresses.add(ipaddress.ip_address(item[4][0]))
        except socket.gaierror as exc:
            raise ValueError(f'URL hostname could not be resolved: {host}') from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError('URL target is not a public network address')
    return tuple(
        str(address)
        for address in sorted(addresses, key=lambda item: (item.version, item.compressed))
    )


def _validate_public_http_url(url: str) -> None:
    parsed = urlparse(str(url or '').strip())
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        raise ValueError('URL must use HTTP(S) with a hostname')
    if parsed.username or parsed.password:
        raise ValueError('URL credentials are not allowed')
    host = parsed.hostname.rstrip('.').casefold()
    if host == 'localhost' or host.endswith('.localhost'):
        raise ValueError('URL target is not a public network address')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if not address.is_global:
        raise ValueError('URL target is not a public network address')


class _PublicHTTPRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Any,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Any:
        _validate_public_http_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read_bounded_chunks(chunks: Any, *, max_bytes: int) -> bytes:
    content = bytearray()
    for chunk in chunks:
        content.extend(chunk)
        if len(content) > max_bytes:
            raise ValueError(f'HTTP response exceeds {max_bytes} byte limit')
    return bytes(content)


def _response_content(response: Any) -> bytes:
    content = getattr(response, 'content', None)
    if isinstance(content, bytes):
        return content
    if isinstance(content, bytearray):
        return bytes(content)
    return str(getattr(response, 'text', '') or '').encode('utf-8')


def _search_backend_order(
    raw_backend: Any,
    *,
    prefer_arxiv_first: bool = False,
    exclude_arxiv: bool = False,
) -> tuple[str, ...]:
    raw = str(raw_backend or '').strip()
    if not raw:
        if prefer_arxiv_first:
            return ('arxiv_api', 'duckduckgo_lite', 'duckduckgo_html', 'duckduckgo_html_www')
        return tuple(
            backend
            for backend in _DEFAULT_SEARCH_BACKENDS
            if not exclude_arxiv or backend != 'arxiv_api'
        )
    requested = [item.strip() for item in raw.split(',') if item.strip()]
    cleaned: list[str] = []
    for item in requested:
        if item in {'duckduckgo', 'duckduckgo_html'}:
            cleaned.extend(['duckduckgo_lite', 'duckduckgo_html'])
            continue
        cleaned.append(item)
    expanded = [item for item in cleaned if item in _DEFAULT_SEARCH_BACKENDS]
    return tuple(dict.fromkeys(expanded)) or _DEFAULT_SEARCH_BACKENDS


def _prefer_arxiv_first(*, context: ToolExecutionContext, query: str) -> bool:
    query_lower = query.lower()
    if _is_dataset_or_repository_query(query):
        return False
    if 'arxiv' in query_lower or re.search(r'\b\d{4}\.\d{4,5}\b', query_lower):
        return True
    stage = getattr(context.stage, 'value', str(context.stage))
    return stage == 'literature_grounding'


def _is_dataset_or_repository_query(query: str) -> bool:
    normalized = str(query or '').casefold()
    markers = (
        'dataset',
        'dandiset',
        'dandi',
        'openneuro',
        'repository',
        'accession',
        'official record',
        'official metadata',
        'data archive',
        'data release',
    )
    return any(marker in normalized for marker in markers) or bool(
        re.search(r'\bds\d{6}\b', normalized)
    )


def _search_url_for_backend(*, query: str, backend: str) -> str:
    encoded = quote_plus(query)
    if backend == 'duckduckgo_lite':
        return f'https://lite.duckduckgo.com/lite/?q={encoded}'
    if backend == 'arxiv_api':
        return f'https://export.arxiv.org/api/query?search_query={quote_plus(f"all:{query}")}&start=0&max_results=10'
    if backend == 'duckduckgo_html_www':
        return f'https://duckduckgo.com/html/?q={encoded}'
    return f'https://html.duckduckgo.com/html/?q={encoded}'


def _remaining_backend_timeout(*, started_at: float, total_timeout: int, backend: str) -> int:
    elapsed = time.monotonic() - started_at
    remaining = math.ceil(total_timeout - elapsed)
    if remaining <= 0:
        return 0
    backend_cap = _SEARCH_BACKEND_TIMEOUT_CAPS.get(backend, remaining)
    return max(1, min(remaining, backend_cap))


def _exception_summary(exc: Exception) -> str:
    message = str(exc).strip()
    exc_type = type(exc).__name__
    return f'{exc_type}: {message}' if message else exc_type


def _classify_web_fetch_failure(exc: Exception, *, url: str) -> dict[str, Any]:
    """Turn opaque network exceptions into a model-actionable, bounded diagnosis."""
    if isinstance(exc, PermissionError):
        return {'kind': 'permission_denied', 'status_code': None, 'retryable': False,
                'next_action': 'Resolve execution permissions before retrying this URL.',
                'message': _exception_summary(exc)}
    status_code = getattr(exc, 'response', None)
    status_code = getattr(status_code, 'status_code', None)
    if status_code is None and isinstance(exc, urllib.error.HTTPError):
        status_code = exc.code
    if status_code is None:
        match = re.search(r'\bHTTP\s+(\d{3})\b', str(exc), re.IGNORECASE)
        if match:
            status_code = int(match.group(1))
    if status_code is not None:
        status_code = int(status_code)
        retryable = status_code == 429 or status_code >= 500
        action = (
            'Retry web_fetch once with the same URL.' if retryable
            else 'Use another authoritative URL or web_search; do not repeat this URL unchanged.'
        )
        return {'kind': 'http_status', 'status_code': status_code,
                'retryable': retryable, 'next_action': action,
                'message': f'HTTP {status_code} for {url}'}
    if isinstance(exc, (TimeoutError, socket.timeout)) or 'timeout' in type(exc).__name__.lower():
        return {'kind': 'timeout', 'status_code': None, 'retryable': True,
                'next_action': 'Retry with a larger timeout_seconds or use web_search.',
                'message': _exception_summary(exc)}
    name = type(exc).__name__.lower()
    if isinstance(exc, (socket.gaierror,)) or 'dns' in name or 'name or service' in str(exc).lower():
        return {'kind': 'dns_error', 'status_code': None, 'retryable': False,
                'next_action': 'Check the URL or use web_search to find another source.',
                'message': _exception_summary(exc)}
    if 'ssl' in name or 'certificate' in str(exc).lower() or 'tls' in str(exc).lower():
        return {'kind': 'tls_error', 'status_code': None, 'retryable': False,
                'next_action': 'Use another HTTPS source; do not disable certificate verification.',
                'message': _exception_summary(exc)}
    return {'kind': 'connection_error', 'status_code': None, 'retryable': True,
            'next_action': 'Retry once; if it fails again, use web_search or another URL.',
            'message': _exception_summary(exc)}


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ''
        self._in_title = False
        self._heading_tag: str | None = None
        self._current_heading: list[str] = []
        self.headings: list[str] = []
        self.text_chunks: list[str] = []
        self.link_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == 'title':
            self._in_title = True
        if tag in {'h1', 'h2', 'h3'}:
            self._heading_tag = tag
            self._current_heading = []
        if tag == 'a':
            self.link_count += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == 'title':
            self._in_title = False
        if self._heading_tag == tag:
            heading = ''.join(self._current_heading).strip()
            if heading:
                self.headings.append(heading)
            self._heading_tag = None
            self._current_heading = []

    def handle_data(self, data: str) -> None:
        stripped = data.strip()
        if not stripped:
            return
        if self._in_title:
            self.title += stripped
        if self._heading_tag is not None:
            self._current_heading.append(stripped + ' ')
        self.text_chunks.append(stripped)


def _extract_html_summary(html: str) -> dict[str, Any]:
    parser = _HTMLTextExtractor()
    parser.feed(html)
    return {
        'title': parser.title.strip(),
        'headings': parser.headings[:20],
        'text': '\n'.join(parser.text_chunks),
        'link_count': parser.link_count,
    }


def _extract_search_results(html: str, *, limit: int) -> list[dict[str, str]]:
    arxiv_results = _extract_arxiv_results(html, limit=limit)
    if arxiv_results:
        return arxiv_results
    pattern = re.compile(
        r'<a[^>]*class="result__a"[^>]*href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>.*?'
        r'(?:class="result__snippet"[^>]*>(?P<snippet>.*?)</a>|class="result__snippet"[^>]*>(?P<snippet2>.*?)</div>)?',
        re.IGNORECASE | re.DOTALL,
    )
    results: list[dict[str, str]] = []
    for match in pattern.finditer(html):
        title = _clean_html_text(match.group('title') or '')
        snippet = _clean_html_text(match.group('snippet') or match.group('snippet2') or '')
        results.append({'title': title, 'url': _normalize_search_url(match.group('url') or ''), 'snippet': snippet})
        if len(results) >= limit:
            break
    if results:
        return results
    generic_anchor_pattern = re.compile(
        r'<a[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    seen: set[str] = set()
    for match in generic_anchor_pattern.finditer(html):
        url = _normalize_search_url(match.group('url') or '')
        title = _clean_html_text(match.group('title') or '')
        if not url or not title or url in seen:
            continue
        parsed = urlparse(url)
        if parsed.netloc.endswith('duckduckgo.com') or parsed.scheme not in {'http', 'https'}:
            continue
        seen.add(url)
        results.append({'title': title, 'url': url, 'snippet': ''})
        if len(results) >= limit:
            break
    return results


def _extract_arxiv_results(payload: str, *, limit: int) -> list[dict[str, str]]:
    if '<feed' not in payload or '<entry>' not in payload:
        return []
    entry_pattern = re.compile(r'<entry>(?P<body>.*?)</entry>', re.IGNORECASE | re.DOTALL)
    results: list[dict[str, str]] = []
    for match in entry_pattern.finditer(payload):
        body = match.group('body') or ''
        title = _xml_tag_text(body, 'title')
        url = _xml_tag_text(body, 'id')
        snippet = _xml_tag_text(body, 'summary')
        if not title or not url:
            continue
        results.append({'title': title, 'url': url, 'snippet': snippet})
        if len(results) >= limit:
            break
    return results


def _xml_tag_text(payload: str, tag: str) -> str:
    match = re.search(rf'<{tag}[^>]*>(?P<value>.*?)</{tag}>', payload, re.IGNORECASE | re.DOTALL)
    if match is None:
        return ''
    return ' '.join(unescape(match.group('value') or '').split())


def _clean_html_text(value: str) -> str:
    return unescape(re.sub(r'<[^>]+>', '', value or '')).strip()


def _normalize_search_url(raw_url: str) -> str:
    candidate = unescape(str(raw_url or '').strip())
    if not candidate:
        return ''
    if candidate.startswith('//'):
        candidate = f'https:{candidate}'
    parsed = urlparse(candidate)
    query = parse_qs(parsed.query)
    redirect_values = query.get('uddg') or query.get('url')
    if redirect_values:
        candidate = unquote(redirect_values[0])
    return candidate
