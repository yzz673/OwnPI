from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Any, Mapping




class WebSearchExecutionMode(str, Enum):
    HOSTED = 'hosted'
    LOCAL_FALLBACK = 'local_fallback'
    DISABLED = 'disabled'


@dataclass(frozen=True)
class HostedWebSearchOptions:
    mode: str
    context_size: str | None = None
    allowed_domains: tuple[str, ...] = ()
    user_location: tuple[tuple[str, str], ...] = ()
    search_content_types: tuple[str, ...] = ()
    max_uses: int | None = None

    def build_spec(self) -> dict[str, Any] | None:
        if self.mode == 'disabled':
            return None
        external_web_access, indexed_web_access = {
            'cached': (False, None),
            'indexed': (True, True),
            'live': (True, None),
        }[self.mode]
        spec: dict[str, Any] = {
            'type': 'web_search',
            'external_web_access': external_web_access,
        }
        if indexed_web_access is not None:
            spec['indexed_web_access'] = indexed_web_access
        if self.allowed_domains:
            spec['filters'] = {'allowed_domains': list(self.allowed_domains)}
        if self.user_location:
            spec['user_location'] = {
                'type': 'approximate',
                **dict(self.user_location),
            }
        if self.context_size is not None:
            spec['search_context_size'] = self.context_size
        if self.search_content_types:
            spec['search_content_types'] = list(self.search_content_types)
        return spec

    def build_anthropic_spec(self) -> dict[str, Any] | None:
        if self.mode != 'live':
            return None
        spec: dict[str, Any] = {
            'type': 'web_search_20250305',
            'name': 'web_search',
        }
        if self.allowed_domains:
            spec['allowed_domains'] = list(self.allowed_domains)
        if self.user_location:
            spec['user_location'] = {
                'type': 'approximate',
                **dict(self.user_location),
            }
        if self.max_uses is not None:
            spec['max_uses'] = self.max_uses
        return spec

    @property
    def digest(self) -> str:
        spec = self.build_spec()
        if spec is None:
            return ''
        return hashlib.sha256(_canonical_json(spec)).hexdigest()

    @property
    def anthropic_digest(self) -> str:
        spec = self.build_anthropic_spec()
        if spec is None:
            return ''
        return hashlib.sha256(_canonical_json(spec)).hexdigest()


def select_web_search_execution_mode(
    *,
    supports_hosted_web_search: bool,
    web_search_mode: str,
) -> WebSearchExecutionMode:
    if web_search_mode == 'disabled':
        return WebSearchExecutionMode.DISABLED
    if supports_hosted_web_search:
        return WebSearchExecutionMode.HOSTED
    return WebSearchExecutionMode.LOCAL_FALLBACK


def hosted_web_search_trace_metadata(
    *,
    execution_mode: WebSearchExecutionMode,
    options: HostedWebSearchOptions,
) -> dict[str, Any]:
    return {
        'web_search_execution_mode': execution_mode.value,
        'hosted_tool_available': execution_mode == WebSearchExecutionMode.HOSTED,
        'hosted_web_search_mode': options.mode if execution_mode == WebSearchExecutionMode.HOSTED else '',
        'hosted_web_search_spec_digest': options.digest,
        'hosted_web_search_context_size': options.context_size or '',
        'hosted_web_search_domain_filter_digest': _string_list_digest(options.allowed_domains),
    }




def count_url_citations(payload: Mapping[str, Any]) -> int:
    count = 0
    content = payload.get('content')
    if not isinstance(content, list):
        return 0
    for block in content:
        if not isinstance(block, Mapping):
            continue
        annotations = block.get('annotations')
        if not isinstance(annotations, list):
            continue
        count += sum(
            1
            for annotation in annotations
            if isinstance(annotation, Mapping)
            and str(annotation.get('type') or '') in {'url_citation', 'citation'}
        )
    return count




def _string_list_digest(values: tuple[str, ...]) -> str:
    return hashlib.sha256(_canonical_json(list(values))).hexdigest() if values else ''


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')
