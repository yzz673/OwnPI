from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import TYPE_CHECKING, Any, Mapping
from urllib.parse import quote_plus

from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.permissions.action_compiler import OperationEffects
from ownpi.runtime.permissions.models import EffectivePermissions
from ownpi.runtime.tools.errors import ToolPreflightValidationError
from ownpi.runtime.limits.projection import DEFAULT_CITATION_SEARCH_RESULTS
from ownpi.runtime.limits.resources import MAX_CITATION_TITLE_CHARS
from ownpi.runtime.paper_package.citation_receipts import (
    build_citation_receipt,
    validate_citation_package,
)
from ownpi.runtime.paper_package.bibliography import normalize_doi
from ownpi.tools.common import clamp_int, metadata_for_context
from ownpi.tools.paper_citation_metadata import metadata_identity, metadata_routes, publication_fields, resolve_metadata
from ownpi.tools.paper_fetch_sources import PaperIdentity, _crossref_candidate
from ownpi.tools.web_tools import _http_get, _load_httpx

if TYPE_CHECKING:
    from ownpi.runtime.tools.definitions import ToolExecutionContext


async def run_citation_resolve(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
    try:
        return await _run_citation_resolve(context, arguments)
    except PermissionError as exc:
        return ToolResult(
            ok=False, tool_name='citation_resolve', error=str(exc),
            data={'outcome': 'permission_denied', 'recovery_hint':
                  'Resolve the execution permission before retrying the same citation; its identity has not been assessed.'},
        )


def resolve_citation_effects(context: ToolExecutionContext, arguments: dict[str, Any]) -> OperationEffects:
    identifier = str(arguments.get('identifier', '') or '').strip()
    title = _bounded(arguments.get('title', ''), MAX_CITATION_TITLE_CHARS)
    if bool(identifier) == bool(title):
        raise ToolPreflightValidationError(
            'provide exactly one of identifier or title', validation_code='citation_input_invalid',
        )
    if identifier:
        try:
            identity = metadata_identity(identifier)
        except ValueError as exc:
            raise ToolPreflightValidationError(str(exc), validation_code='citation_input_invalid') from exc
        return OperationEffects.declared(
            network_required=True, network_endpoints=tuple(route[1] for route in metadata_routes(identity)),
            source='citation_metadata_lookup',
        )
    return OperationEffects.declared(
        network_required=True, network_endpoints=(_title_search_url(title),),
        source='citation_title_lookup',
    )


async def _run_citation_resolve(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
    identifier = str(arguments.get('identifier', '') or '').strip()
    title = _bounded(arguments.get('title', ''), MAX_CITATION_TITLE_CHARS)
    if bool(identifier) == bool(title):
        return _error(context, 'citation_resolve', 'provide exactly one of identifier or title')
    retrieved_at = _utc_now()
    if identifier:
        result = await _resolve_identifier(context, identifier=identifier, arguments=arguments, retrieved_at=retrieved_at)
    else:
        result = await _resolve_title(
            effective_permissions=context.effective_permissions,
            title=title,
            author=_bounded(arguments.get('author', ''), 160),
            year=_bounded(arguments.get('year', ''), 20),
            timeout=clamp_int(arguments.get('timeout_seconds'), default=20, minimum=1, maximum=60),
            retrieved_at=retrieved_at,
        )
    outcome = str(result.get('outcome', 'unresolved'))
    receipt = result.get('receipt') if isinstance(result.get('receipt'), Mapping) else {}
    content_access_status = str(receipt.get('content_access_status', '') or 'not_assessed')
    return ToolResult(
        ok=outcome not in {'invalid_input'},
        tool_name='citation_resolve',
        data=result,
        stdout=(
            f"outcome={outcome}\n"
            f"content_access_status={content_access_status}\n"
            f"canonical_id={result.get('canonical_id', '')}\n"
        ),
        error=str(result.get('error', '') or '') or None,
        metadata=metadata_for_context(
            context,
            citation_outcome=outcome,
            citation_content_access_status=content_access_status,
            citation_canonical_id=result.get('canonical_id'),
            citation_receipt_id=receipt.get('receipt_id'),
        ),
        warnings=list(result.get('diagnostics', []) or []),
    )


async def run_citation_package_validate(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
    try:
        validation = validate_citation_package(
            manuscript_text=str(arguments.get('manuscript_text', '') or ''),
            bibliography_text=str(arguments.get('bibliography_text', '') or ''),
            citation_receipts=tuple(
                item for item in arguments.get('citation_receipts', []) or []
            ),
        )
    except (TypeError, ValueError) as exc:
        return _error(context, 'citation_package_validate', str(exc))
    data = {'outcome': 'passed' if validation.passed else 'failed', 'validation': validation.to_dict()}
    return ToolResult(
        ok=True,
        tool_name='citation_package_validate',
        data=data,
        stdout=f"outcome={data['outcome']}\nerrors={len(validation.errors)}\n",
        metadata=metadata_for_context(
            context,
            citation_package_passed=validation.passed,
            citation_package_error_count=len(validation.errors),
        ),
        warnings=list(dict.fromkeys(str(item.get('code', 'citation_warning')) for item in validation.warnings)),
    )


async def _resolve_identifier(
    context: ToolExecutionContext,
    *,
    identifier: str,
    arguments: Mapping[str, Any],
    retrieved_at: str,
) -> dict[str, Any]:
    return await resolve_metadata(
        identifier=identifier,
        timeout=clamp_int(arguments.get('timeout_seconds'), default=20, minimum=1, maximum=60),
        retrieved_at=retrieved_at, permissions=context.effective_permissions,
    )


async def _resolve_title(*, title: str, author: str, year: str, timeout: int, retrieved_at: str,
                   effective_permissions: EffectivePermissions) -> dict[str, Any]:
    candidates, diagnostics, search_projection = await _fetch_title_candidates(
        title=title,
        timeout=timeout,
        effective_permissions=effective_permissions,
    )
    exact = [item for item in candidates if _normalized(item.get('title')) == _normalized(title)]
    matches = [item for item in exact if _identity_filters_match(item, author=author, year=year)]
    deduplicated = _deduplicate_candidates(matches)
    if len(deduplicated) > 1:
        return {
            'outcome': 'ambiguous',
            'canonical_id': '',
            'candidates': [
                _candidate_summary(item) for item in deduplicated
            ],
            'search_projection': search_projection,
            'diagnostics': ['title lookup returned conflicting or multiple identity candidates'],
        }
    if not deduplicated:
        return {
            'outcome': 'unresolved',
            'canonical_id': '',
            'candidates': [_candidate_summary(item) for item in candidates],
            'search_projection': search_projection,
            'diagnostics': diagnostics or ['no exact title identity match'],
        }
    candidate = deduplicated[0]
    doi = normalize_doi(str(candidate.get('doi', '') or ''))
    source_url = str(candidate.get('url', '') or '')
    receipt = build_citation_receipt(
        canonical_id=f'doi:{doi}',
        identifier_type='doi',
        title=str(candidate.get('title', '') or ''),
        authors=tuple(str(item) for item in candidate.get('authors', []) or []),
        year=str(candidate.get('year', '') or ''),
        venue=str(candidate.get('venue', '') or ''),
        source_url=source_url,
        metadata_source='crossref-title-search',
        retrieved_at=retrieved_at,
        status='verified' if doi and source_url else 'unresolved',
        content_access_status='not_assessed',
        diagnostics=diagnostics,
        entry_type=candidate.get('entry_type', 'misc'),
        publication_fields=candidate.get('publication_fields', {}),
    )
    return {
        'outcome': receipt.status,
        'canonical_id': receipt.canonical_id,
        'receipt': receipt.to_dict(),
        'diagnostics': list(receipt.diagnostics),
        'search_projection': search_projection,
    }


def _title_search_url(title: str) -> str:
    return (
        'https://api.crossref.org/works?'
        f'query.title={quote_plus(title)}&rows={DEFAULT_CITATION_SEARCH_RESULTS}'
        '&select=DOI,title,author,published,published-print,published-online,issued,container-title,URL,type,volume,issue,page,article-number,publisher'
    )


async def _fetch_title_candidates(
    *,
    title: str,
    timeout: int,
    effective_permissions: EffectivePermissions,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    url = _title_search_url(title)
    try:
        response = await _http_get(httpx=_load_httpx(), url=url, timeout=timeout,
                             max_bytes=2_000_000, effective_permissions=effective_permissions)
        message = json.loads(str(getattr(response, 'text', '') or '')).get('message') or {}
        items = message.get('items') or []
        search_result_count = max(0, int(message.get('total-results', 0) or 0))
    except PermissionError:
        raise
    except Exception as exc:
        return [], [f'{type(exc).__name__}: {_bounded(exc, 280)}'], {
            'returned_count': 0,
            'search_result_count': 0,
            'projection_truncated': False,
        }
    candidates: list[dict[str, Any]] = []
    for item in items[:DEFAULT_CITATION_SEARCH_RESULTS]:
        if not isinstance(item, Mapping):
            continue
        parsed = _crossref_candidate(dict(item), PaperIdentity('', 'doi'))
        entry_type, fields = publication_fields(parsed)
        candidates.append(
            {
                'doi': normalize_doi(parsed.doi), 'title': parsed.title, 'authors': parsed.authors,
                'year': parsed.publication_year,
                'venue': fields.get('journal') or fields.get('booktitle') or fields.get('howpublished') or '',
                'url': parsed.landing_url, 'entry_type': entry_type, 'publication_fields': fields,
            }
        )
    projection_truncated = max(search_result_count, len(items)) > len(candidates)
    projection = {
        'returned_count': len(candidates),
        'search_result_count': search_result_count,
        'projection_truncated': projection_truncated,
    }
    if projection_truncated:
        projection['recovery_guidance'] = (
            'Narrow the exact-title query with author/year or resolve a DOI/arXiv identifier.'
        )
    return candidates, [], projection


def _identity_filters_match(candidate: Mapping[str, Any], *, author: str, year: str) -> bool:
    author_match = not author or _normalized(author).split()[-1] in {
        _normalized(item).split()[-1] for item in candidate.get('authors', []) or [] if _normalized(item)
    }
    year_match = not year or str(candidate.get('year', '') or '') == str(year)
    return author_match and year_match


def _deduplicate_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for item in candidates:
        key = normalize_doi(str(item.get('doi', '') or '')) or json.dumps(item, sort_keys=True)
        unique.setdefault(key, item)
    return list(unique.values())


def _candidate_summary(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        'doi': _bounded(candidate.get('doi', ''), 300),
        'title': _bounded(candidate.get('title', ''), 500),
        'authors': [
            _bounded(item, 160) for item in candidate.get('authors', []) or []
        ],
        'year': _bounded(candidate.get('year', ''), 20),
        'venue': _bounded(candidate.get('venue', ''), 300),
        'url': _bounded(candidate.get('url', ''), 1_024),
    }


def _normalized(value: Any) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', str(value or '').casefold()).strip()


def _bounded(value: Any, limit: int) -> str:
    return ' '.join(str(value or '').split())[:limit]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error(context: ToolExecutionContext, tool_name: str, message: str) -> ToolResult:
    return ToolResult(
        ok=False,
        tool_name=tool_name,
        data={'outcome': 'invalid_input'},
        error=_bounded(message, 320),
        metadata=metadata_for_context(context),
    )
