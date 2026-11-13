from __future__ import annotations

import re
import time
from dataclasses import replace
from typing import Any, Callable

from ownpi.runtime.paper_package.bibliography import normalize_doi
from ownpi.runtime.paper_package.citation_receipts import build_citation_receipt
from ownpi.runtime.permissions.models import EffectivePermissions
from ownpi.tools.paper_fetch_sources import PaperCandidate, PaperIdentity, _metadata_routes, _normalize_identity
from ownpi.tools.web_tools import _http_get, _load_httpx


def metadata_identity(identifier: str) -> PaperIdentity:
    identity, error = _normalize_identity(identifier)
    if identity is None or error or identity.kind not in {'doi', 'arxiv'}:
        raise ValueError(error or 'identifier must resolve to DOI or arXiv')
    return identity


def metadata_routes(identity: PaperIdentity) -> list[tuple[str, str, Callable]]:
    routes = _metadata_routes(identity)
    priority = {'crossref': 0, 'publisher': 1, 'openalex': 2, 'europe_pmc': 3}
    return sorted(routes, key=lambda route: priority.get(route[0], 0))


def publication_fields(candidate: PaperCandidate) -> tuple[str, dict[str, str]]:
    kind = candidate.publication_type.casefold()
    entry_type = {'journal-article': 'article', 'article': 'article', 'proceedings-article': 'inproceedings',
                  'book': 'book', 'monograph': 'book', 'book-chapter': 'incollection'}.get(kind, 'misc')
    fields = dict(candidate.publication_fields)
    container = fields.pop('journal', '')
    if container:
        name = {'article': 'journal', 'inproceedings': 'booktitle', 'incollection': 'booktitle'}.get(entry_type, 'howpublished')
        fields.setdefault(name, container)
    if entry_type == 'misc' and kind:
        fields['note'] = '; '.join(filter(None, (fields.get('note'), candidate.publication_type)))
    return entry_type, fields


def resolved_candidate(candidate: PaperCandidate, *, kind: str, source_url: str, retrieved_at: str) -> dict[str, Any]:
    canonical_id = f'doi:{normalize_doi(candidate.doi)}' if kind == 'doi' else f'arxiv:{candidate.arxiv_id.lower()}'
    version_match = re.search(r'v\d+$', candidate.arxiv_id, re.IGNORECASE) if kind == 'arxiv' else None
    entry_type, fields = publication_fields(candidate)
    receipt = build_citation_receipt(
        canonical_id=canonical_id, identifier_type=kind,
        identifier_version=version_match.group().lower() if version_match else '',
        title=candidate.title, authors=candidate.authors, year=candidate.publication_year,
        venue=fields.get('journal') or fields.get('booktitle') or fields.get('howpublished') or '',
        source_url=source_url, metadata_source=candidate.source, retrieved_at=retrieved_at,
        status='verified', content_access_status='not_assessed', entry_type=entry_type, publication_fields=fields,
    )
    return {'outcome': receipt.status, 'canonical_id': receipt.canonical_id,
            'receipt': receipt.to_dict(), 'diagnostics': list(receipt.diagnostics)}


async def resolve_metadata(*, identifier: str, timeout: int, retrieved_at: str,
                           permissions: EffectivePermissions) -> dict[str, Any]:
    try:
        identity = metadata_identity(identifier)
    except ValueError as exc:
        return {'outcome': 'invalid_input', 'error': str(exc)}
    deadline = time.monotonic() + timeout
    attempts: list[dict[str, Any]] = []
    for source, url, parser in metadata_routes(identity):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        attempt: dict[str, Any] = {'source': source, 'url': url}
        attempts.append(attempt)
        try:
            response = await _http_get(httpx=_load_httpx(), url=url, timeout=remaining, deadline=deadline,
                                       max_bytes=2_000_000, effective_permissions=permissions)
            final_url = str(getattr(response, 'url', '') or url)
            candidate = parser(str(response.text), replace(identity, canonical_url=final_url))
            if identity.kind == 'doi' and normalize_doi(candidate.doi) != identity.doi:
                attempt.update(outcome='identity_mismatch')
                continue
            if identity.kind == 'arxiv' and not candidate.arxiv_id:
                attempt.update(outcome='identity_unconfirmed')
                continue
            result = resolved_candidate(candidate, kind=identity.kind, source_url=final_url, retrieved_at=retrieved_at)
            attempt.update(outcome=result['outcome'])
            if result['outcome'] == 'verified':
                result['attempted_sources'] = attempts
                return result
            attempt['diagnostics'] = result['diagnostics']
        except PermissionError:
            raise
        except Exception as exc:
            attempt.update(outcome='unavailable', error=f'{type(exc).__name__}: {str(exc)[:280]}')
    return {'outcome': 'unresolved', 'canonical_id': '', 'attempted_sources': attempts,
            'diagnostics': ['No source established complete basic citation identity within the lookup budget.']}
