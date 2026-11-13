from __future__ import annotations

from html import escape
import re
from typing import Any
from urllib.parse import quote

_MCP_MENTION_RE = re.compile(r'(?<![\w.])@([A-Za-z0-9_.-]+):([^\s<>"\']+)')


def build_command_attachments(
    text: str,
    *,
    mcp_resource_cache: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    return _dedupe_attachments(
        _mcp_resource_attachments(text, resource_cache=mcp_resource_cache)
    )


def append_command_attachments(prompt: str, attachments: list[dict[str, Any]]) -> str:
    if not attachments:
        return prompt
    lines = ['<command_attachments>']
    for item in attachments:
        if item.get('type') == 'mcp_resource':
            lines.extend(
                [
                    '  <attachment>',
                    '    <type>mcp_resource</type>',
                    f"    <mention>{_xml_text(item.get('mention', ''))}</mention>",
                    f"    <resource_id>{_xml_text(item.get('resource_id', ''))}</resource_id>",
                    f"    <server_name>{_xml_text(item.get('server_name', ''))}</server_name>",
                    f"    <uri>{_xml_text(item.get('uri', ''))}</uri>",
                    f"    <status>{_xml_text(item.get('status', ''))}</status>",
                    '  </attachment>',
                ]
            )
            continue
    lines.append('</command_attachments>')
    return '\n'.join([prompt, '', *lines])


def command_attachment_messages(attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for item in attachments:
        if item.get('type') == 'mcp_resource':
            messages.append(
                {
                    'role': 'user',
                    'content': [
                        {
                            'type': 'mcp_resource_attachment',
                            'mention': str(item.get('mention', '') or ''),
                            'resource_id': str(item.get('resource_id', '') or ''),
                            'server_name': str(item.get('server_name', '') or ''),
                            'uri': str(item.get('uri', '') or ''),
                            'status': str(item.get('status', '') or ''),
                        }
                    ],
                }
            )
            continue
    return messages


def _mcp_resource_attachments(
    text: str,
    *,
    resource_cache: dict[str, dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    cache = resource_cache if isinstance(resource_cache, dict) else {}
    attachments: list[dict[str, Any]] = []
    for mention in _extract_mcp_resource_mentions(text):
        resource_id = mention['resource_id']
        cached = cache.get(resource_id)
        attachments.append(
            {
                'type': 'mcp_resource',
                'mention': mention['mention'],
                'resource_id': resource_id,
                'server_name': mention['server_name'],
                'uri': mention['uri'],
                'status': 'cached' if isinstance(cached, dict) else 'referenced',
            }
        )
    return attachments


def _extract_mcp_resource_mentions(text: str) -> tuple[dict[str, str], ...]:
    mentions: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for match in _MCP_MENTION_RE.finditer(str(text or '')):
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
                'resource_id': f'mcp://{quote(server_name, safe="")}/{quote(uri, safe="")}',
                'mention': f'@{server_name}:{uri}',
            }
        )
    return tuple(mentions)


def _strip_trailing_mention_punctuation(value: str) -> str:
    return str(value or '').rstrip('.,;:!?)］】》')


def _xml_text(value: Any) -> str:
    return escape(str(value or ''), quote=False)


def _dedupe_attachments(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for item in items:
        key = (str(item.get('type', '')), str(item.get('resource_id') or item.get('name') or item.get('mention') or ''))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(dict(item))
    return deduped
