from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable

from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.limits.projection import DEFAULT_NATIVE_TOOL_SEARCH_RESULTS
from ownpi.runtime.native_tools.provider_json import FrozenProviderJson
from ownpi.runtime.native_tools.spec import NativeToolSpec
from ownpi.runtime.tools.definitions import ToolDefinition, ToolExecutionContext, ToolExposure


SEARCH_HISTORY_METADATA_KEY = 'native_tool_search_definitions'


@dataclass(frozen=True)
class NativeToolSearchEntry:
    canonical_name: str
    namespace: str
    aliases: tuple[str, ...]
    search_text: str
    search_keywords: tuple[str, ...]
    schema_name: str
    schema_variant: str
    spec: NativeToolSpec
    digest: str
    namespace_description: str = ''

    @classmethod
    def from_definition(
        cls,
        definition: ToolDefinition,
    ) -> 'NativeToolSearchEntry':
        summary = definition.to_summary()
        abi = definition.tool_abi()
        spec = NativeToolSpec.from_tool_definition(definition)
        namespace = _definition_namespace(definition)
        schema_name = str(abi.schema_name or f'{summary.name}_input')
        schema_variant = str(summary.schema_cache_variant or '')
        schema_terms = tuple(
            sorted(
                str(key)
                for key in spec.input_schema.get('properties', {})
                if str(key).strip()
            )
        )
        search_parts = (
            summary.name,
            *summary.aliases,
            summary.description or '',
            summary.detail or '',
            summary.search_hint or '',
            *summary.search_keywords,
            namespace,
            schema_name,
            schema_variant,
            *schema_terms,
        )
        search_text = ' '.join(
            str(part).strip() for part in search_parts if str(part).strip()
        )
        digest = _stable_digest(
            {
                'canonical_name': summary.name,
                'namespace': namespace,
                'schema_variant': schema_variant,
                'spec': spec.provider_neutral_schema(),
            }
        )
        return cls(
            canonical_name=summary.name,
            namespace=namespace,
            aliases=tuple(summary.aliases),
            search_text=search_text,
            search_keywords=tuple(summary.search_keywords),
            schema_name=schema_name,
            schema_variant=schema_variant,
            spec=spec,
            digest=digest,
            namespace_description=definition.namespace_description,
        )


@dataclass(frozen=True)
class NativeToolSearchMatchV1:
    entry: NativeToolSearchEntry
    match_score: int
    match_reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            'canonical_name': self.entry.canonical_name,
            'namespace': self.entry.namespace,
            'digest': self.entry.digest,
            'match_score': self.match_score,
            'match_reason': self.match_reason,
        }


@dataclass(frozen=True)
class NativeToolSearchResultV1:
    query: str
    requested_limit: int
    effective_limit: int
    matches: tuple[NativeToolSearchMatchV1, ...]
    receipt_ref: str
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            'schema_version': self.schema_version,
            'query': self.query,
            'requested_limit': self.requested_limit,
            'effective_limit': self.effective_limit,
            'match_count': len(self.matches),
            'matches': [match.to_dict() for match in self.matches],
            'receipt_ref': self.receipt_ref,
        }

    def to_trace_projection(self) -> dict[str, Any]:
        return {
            'schema_version': self.schema_version,
            'query': self.query,
            'requested_limit': self.requested_limit,
            'effective_limit': self.effective_limit,
            'match_count': len(self.matches),
            'matched_names': [match.entry.canonical_name for match in self.matches],
            'matched_digests': [match.entry.digest for match in self.matches],
            'receipt_ref': self.receipt_ref,
        }


def search_native_tool_entries(
    entries: Iterable[NativeToolSearchEntry],
    *,
    query: str,
    limit: int = DEFAULT_NATIVE_TOOL_SEARCH_RESULTS,
) -> NativeToolSearchResultV1:
    normalized_query = str(query or '').strip()
    if isinstance(limit, bool):
        raise ValueError('tool search limit must be a positive integer')
    requested_limit = int(limit)
    if requested_limit < 1:
        raise ValueError('tool search limit must be a positive integer')
    effective_limit = requested_limit
    selected_name, required_terms, terms = _parse_query(normalized_query)
    exact_query = normalized_query.casefold()
    scored: list[NativeToolSearchMatchV1] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        identity = (entry.canonical_name.casefold(), entry.digest)
        if identity in seen:
            continue
        seen.add(identity)
        canonical = entry.canonical_name.casefold()
        aliases = tuple(alias.casefold() for alias in entry.aliases)
        haystack = entry.search_text.casefold()
        if selected_name and selected_name not in (canonical, *aliases):
            continue
        if required_terms and any(term not in haystack for term in required_terms):
            continue
        exact_match = bool(
            selected_name
            or exact_query == canonical
            or exact_query in aliases
        )
        score = 100 if selected_name else 80 if exact_match else 0
        reasons: list[str] = []
        if selected_name:
            reasons.append('select')
        elif exact_match:
            reasons.append('exact_match')
        name_terms = set(canonical.replace('_', ' ').split())
        alias_terms = {
            part
            for alias in aliases
            for part in alias.replace('_', ' ').split()
            if part
        }
        keywords = {keyword.casefold() for keyword in entry.search_keywords}
        for term in terms:
            if term == canonical:
                score += 12
                reasons.append('canonical_name')
            elif term in name_terms:
                score += 8
                reasons.append('name_term')
            elif term in aliases or term in alias_terms:
                score += 6
                reasons.append('alias')
            elif term in keywords:
                score += 4
                reasons.append('keyword')
            elif term in haystack:
                score += 1
                reasons.append('metadata')
        if score <= 0:
            continue
        scored.append(
            NativeToolSearchMatchV1(
                entry=entry,
                match_score=score,
                match_reason=','.join(dict.fromkeys(reasons)) or 'query_match',
            )
        )
    scored.sort(
        key=lambda match: (
            -match.match_score,
            match.entry.namespace,
            match.entry.canonical_name,
            match.entry.digest,
        )
    )
    matches = tuple(scored[:effective_limit])
    receipt_ref = f'native_tool_search:{_stable_digest({"query": normalized_query, "limit": effective_limit, "matches": [match.entry.digest for match in matches]})[:20]}'
    return NativeToolSearchResultV1(
        query=normalized_query,
        requested_limit=requested_limit,
        effective_limit=effective_limit,
        matches=matches,
        receipt_ref=receipt_ref,
    )


def build_native_tool_search_definition(
    entries: tuple[NativeToolSearchEntry, ...],
) -> ToolDefinition:
    if not entries:
        raise ValueError('native tool_search requires at least one deferred entry')

    async def _handler(_context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
        result = search_native_tool_entries(
            entries,
            query=str(arguments.get('query', '') or ''),
            limit=int(arguments.get('limit', DEFAULT_NATIVE_TOOL_SEARCH_RESULTS)),
        )
        trace_projection = result.to_trace_projection()
        return ToolResult(
            ok=True,
            tool_name='tool_search',
            data=result.to_dict(),
            stdout=f"tool_search matched {len(result.matches)} tool(s); receipt={result.receipt_ref}",
            metadata={
                SEARCH_HISTORY_METADATA_KEY: FrozenProviderJson.from_value({
                    'receipt_ref': result.receipt_ref,
                    'tools': [
                        {
                            **match.entry.spec.to_dict(),
                            'namespace': match.entry.namespace,
                            'namespace_description': match.entry.namespace_description,
                            'digest': match.entry.digest,
                        }
                        for match in result.matches
                    ],
                }).canonical_json,
                'context_effects': {
                    'result_family': 'native_tool_search',
                    'native_tool_search_receipt': trace_projection,
                }
            },
        )

    return ToolDefinition(
        exposure=ToolExposure.DIRECT,
        name='tool_search',
               kind='search',
               description=(
                'Discover tools whose calling definitions have not been provided yet. '
                'Already available tools can be called directly. '
                'Search by capability keywords, tool name, or namespace; use '
                'select:<canonical_name> to select an exact tool name. '
                'Matching tool definitions become available on the next model call; '
                'then invoke the tool using its supplied parameters. If no tools match, '
                'revise the query. For MCP tool discovery use tool_search, not resource '
                'listing tools, which list resources rather than callable tools.'
            ),
               search_hint='Search deferred tools by capability, name, namespace, or schema field.',
               search_keywords=('discover', 'search', 'tools', 'capability', 'deferred'),




               read_only=True,
               concurrency_safe=True,
               permission_class='search',
               invocation_mode='agent_requested',
               execution_class='read_only',
        input_schema_builder=lambda: {
            'type': 'object',
            'properties': {
                'query': {
                    'type': 'string',
                    'description': (
                        'Capability keywords, tool name, or namespace. '
                        'Use select:<canonical_name> for an exact name.'
                    ),
                },
                'limit': {
                    'type': 'integer',
                    'description': (
                        'Maximum number of matching tools to load; a positive integer. '
                        f'Defaults to {DEFAULT_NATIVE_TOOL_SEARCH_RESULTS}.'
                    ),
                },
            },
            'required': ['query'],
            'additionalProperties': False,
        },
        handler=_handler,
        summary_builder=lambda result: str(result.stdout or ''),
        observation_family='native_tool_search',
        result_family='native_tool_search',
        toolset_family='control',
        side_effect_level='none',
        result_identity_fields=('receipt_ref',),
    )


def _definition_namespace(definition: ToolDefinition) -> str:
    summary = definition.to_summary()
    if definition.name.startswith('mcp__'):
        parts = definition.name.split('__', 2)
        if len(parts) == 3 and parts[1]:
            return f'mcp:{parts[1]}'
    return str(summary.toolset_family or summary.backend_kind or 'runtime')


def _parse_query(query: str) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    selected_name = ''
    required: list[str] = []
    terms: list[str] = []
    for token in query.split():
        lowered = token.casefold()
        if lowered.startswith('select:') and lowered.split(':', 1)[1].strip():
            selected_name = lowered.split(':', 1)[1].strip()
            continue
        if lowered.startswith('+') and len(lowered) > 1:
            required.append(lowered[1:])
            terms.append(lowered[1:])
            continue
        terms.append(lowered)
    return selected_name, tuple(required), tuple(terms)


def _stable_digest(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(',', ':'),
        default=str,
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()
