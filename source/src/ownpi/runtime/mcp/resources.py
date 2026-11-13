from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, unquote, urlparse

MCP_RESOURCE_TYPE = 'mcp_resource'
_MENTION_RE = re.compile(r'(?<![\w.])@([A-Za-z0-9_.-]+):([^\s<>"\']+)')
_RESOURCE_TEMPLATE_OPTIONAL_FIELDS = (
    'title',
    'description',
    'mimeType',
    'annotations',
    'icons',
    '_meta',
)


def mcp_resource_id(server_name: str, uri: str) -> str:
    server = quote(str(server_name or '').strip(), safe='')
    encoded_uri = quote(str(uri or '').strip(), safe='')
    return f'mcp://{server}/{encoded_uri}'


def mcp_resource_mention(server_name: str, uri: str) -> str:
    return f'@{str(server_name or "").strip()}:{str(uri or "").strip()}'


def parse_mcp_resource_id(value: str) -> tuple[str, str] | None:
    raw = str(value or '').strip()
    if not raw.startswith('mcp://'):
        return None
    parsed = urlparse(raw)
    server_name = unquote(parsed.netloc or '').strip()
    uri = unquote((parsed.path or '').lstrip('/')).strip()
    if not server_name or not uri:
        return None
    return server_name, uri


def parse_mcp_resource_mention(value: str) -> tuple[str, str] | None:
    raw = str(value or '').strip()
    if not raw.startswith('@') or ':' not in raw:
        return None
    server_name, uri = raw[1:].split(':', 1)
    server_name = server_name.strip()
    uri = uri.strip()
    if not server_name or not uri:
        return None
    return server_name, _strip_trailing_mention_punctuation(uri)


def extract_mcp_resource_mentions(text: str) -> tuple[dict[str, str], ...]:
    mentions: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for match in _MENTION_RE.finditer(str(text or '')):
        server_name = match.group(1).strip()
        uri = _strip_trailing_mention_punctuation(match.group(2).strip())
        if not server_name or not uri:
            continue
        key = (server_name.casefold(), uri)
        if key in seen:
            continue
        seen.add(key)
        mentions.append(
            {
                'server_name': server_name,
                'uri': uri,
                'resource_id': mcp_resource_id(server_name, uri),
                'mention': mcp_resource_mention(server_name, uri),
            }
        )
    return tuple(mentions)


def normalize_mcp_resource_entry(server_name: str, resource: dict[str, Any]) -> dict[str, Any]:
    uri = str(resource.get('uri', resource.get('resource_uri', resource.get('id', ''))) or '').strip()
    resource_id = (
        str(resource.get('resource_id', '') or '').strip()
        if str(resource.get('resource_id', '') or '').strip().startswith('mcp://')
        else ''
    )
    if not resource_id and uri:
        resource_id = mcp_resource_id(server_name, uri)
    payload = {
        **dict(resource),
        'server_name': str(server_name or '').strip(),
        'uri': uri,
        'resource_id': resource_id,
    }
    if uri:
        payload['mention'] = mcp_resource_mention(server_name, uri)
    title = str(payload.get('title', payload.get('name', '')) or '').strip()
    if title and not payload.get('name'):
        payload['name'] = title
    return payload


def normalize_mcp_resource_template_entry(
    server_name: str,
    resource_template: dict[str, Any],
) -> dict[str, Any] | None:
    """Return one server-qualified standard MCP ResourceTemplate entry."""

    uri_template = str(resource_template.get('uriTemplate', '') or '').strip()
    name = str(resource_template.get('name', '') or '').strip()
    if not uri_template or not name:
        return None
    payload: dict[str, Any] = {
        'server': str(server_name or '').strip(),
        'uriTemplate': uri_template,
        'name': name,
    }
    for field_name in _RESOURCE_TEMPLATE_OPTIONAL_FIELDS:
        if field_name in resource_template:
            payload[field_name] = resource_template[field_name]
    return payload


def mcp_resource_metadata(resource: dict[str, Any]) -> dict[str, Any]:
    """Return durable resource identity/metadata without copying resource bodies."""

    body_fields = {'contents', 'content', 'text', 'blob', 'data'}
    return {
        str(key): value
        for key, value in resource.items()
        if str(key) not in body_fields
    }


def resolve_mcp_resource_reference(
    reference: str,
    *,
    server_name: str = '',
    resource_cache: dict[str, dict[str, Any]] | None = None,
) -> tuple[str, str, str, dict[str, Any] | None]:
    raw = str(reference or '').strip()
    parsed_id = parse_mcp_resource_id(raw)
    parsed_mention = parse_mcp_resource_mention(raw)
    resolved_server = str(server_name or '').strip()
    resolved_uri = raw
    resource_id = raw
    if parsed_id is not None:
        resolved_server, resolved_uri = parsed_id
        resource_id = mcp_resource_id(resolved_server, resolved_uri)
    elif parsed_mention is not None:
        resolved_server, resolved_uri = parsed_mention
        resource_id = mcp_resource_id(resolved_server, resolved_uri)
    elif resolved_server:
        resolved_uri = raw
        resource_id = mcp_resource_id(resolved_server, resolved_uri)

    payload = None
    cache = resource_cache if isinstance(resource_cache, dict) else {}
    if resource_id in cache and isinstance(cache.get(resource_id), dict):
        payload = dict(cache[resource_id])
    elif raw in cache and isinstance(cache.get(raw), dict):
        payload = dict(cache[raw])
    else:
        for key, value in cache.items():
            if not isinstance(value, dict):
                continue
            cached_uri = str(value.get('uri', '') or '').strip()
            cached_server = str(value.get('server_name', '') or '').strip()
            cached_mention = str(value.get('mention', '') or '').strip()
            if raw == cached_mention or (
                cached_uri == resolved_uri and (not resolved_server or cached_server == resolved_server)
            ):
                payload = dict(value)
                resolved_server = cached_server or resolved_server
                resolved_uri = cached_uri or resolved_uri
                resource_id = str(value.get('resource_id') or key)
                break
    if payload is not None:
        resolved_server = str(payload.get('server_name', resolved_server) or resolved_server)
        resolved_uri = str(payload.get('uri', resolved_uri) or resolved_uri)
        resource_id = str(payload.get('resource_id', resource_id) or resource_id)
    return resolved_server, resolved_uri, resource_id, payload


def _strip_trailing_mention_punctuation(value: str) -> str:
    return str(value or '').rstrip('.,;:!?)］】》')
