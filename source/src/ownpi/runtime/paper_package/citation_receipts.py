from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
import unicodedata
from typing import Any, Mapping, Sequence

from ownpi.runtime.paper_package.bibliography import (
    BibliographyError, identity_matches, metadata_differences, parse_bibliography,
)


CITATION_RECEIPT_SCHEMA_VERSION = 1
_VALID_RESOLUTION_STATUSES = {'verified', 'ambiguous', 'unresolved', 'rejected'}
_VALID_CONTENT_ACCESS_STATUSES = {
    'not_assessed',
    'metadata_only',
    'accessible',
    'restricted',
    'unavailable',
}
_CITE_RE = re.compile(r'\\cite[a-zA-Z*]*\s*(?:\[[^\]]*\]\s*){0,2}\{(?P<keys>[^}]+)\}')


def citation_receipt_input_schema() -> dict[str, Any]:
    return {
        'type': 'object',
        'description': 'Pass the complete citation_resolve receipt object through unchanged.',
        'properties': {
            'schema_version': {
                'type': 'integer',
                'enum': [CITATION_RECEIPT_SCHEMA_VERSION],
            },
            'canonical_id': {'type': 'string'},
            'identifier_type': {'type': 'string'},
            'identifier_version': {'type': 'string'},
            'metadata': {
                'type': 'object',
                'properties': {
                    'title': {'type': 'string'},
                    'authors': {'type': 'array', 'items': {'type': 'string'}},
                    'year': {'type': 'string'},
                    'venue': {'type': 'string'},
                },
                'required': ['title', 'authors', 'year', 'venue'],
                'additionalProperties': False,
            },
            'source': {
                'type': 'object',
                'properties': {
                    'url': {'type': 'string'},
                    'metadata_source': {'type': 'string'},
                    'retrieved_at': {'type': 'string'},
                },
                'required': ['url', 'metadata_source', 'retrieved_at'],
                'additionalProperties': False,
            },
            'bibtex': {'type': 'string'},
            'bibtex_key': {'type': 'string'},
            'claim_support_refs': {'type': 'array', 'items': {'type': 'object'}},
            'status': {'type': 'string', 'enum': sorted(_VALID_RESOLUTION_STATUSES)},
            'content_access_status': {
                'type': 'string',
                'enum': sorted(_VALID_CONTENT_ACCESS_STATUSES),
            },
            'diagnostics': {'type': 'array', 'items': {'type': 'string'}},
            'receipt_id': {'type': 'string'},
            'content_hash': {'type': 'string'},
        },
        'required': [
            'schema_version',
            'canonical_id',
            'identifier_type',
            'identifier_version',
            'metadata',
            'source',
            'bibtex',
            'bibtex_key',
            'claim_support_refs',
            'status',
            'content_access_status',
            'diagnostics',
            'receipt_id',
            'content_hash',
        ],
        'additionalProperties': False,
    }


@dataclass(frozen=True)
class CitationReceipt:
    canonical_id: str
    identifier_type: str
    title: str
    authors: tuple[str, ...]
    year: str
    venue: str
    source_url: str
    metadata_source: str
    retrieved_at: str
    bibtex: str
    bibtex_key: str
    identifier_version: str = ''
    claim_support_refs: tuple[dict[str, Any], ...] = ()
    status: str = 'verified'
    content_access_status: str = 'not_assessed'
    diagnostics: tuple[str, ...] = ()
    schema_version: int = CITATION_RECEIPT_SCHEMA_VERSION
    receipt_id: str = ''
    content_hash: str = ''

    def to_dict(self) -> dict[str, Any]:
        payload = self._content_payload()
        payload['receipt_id'] = self.receipt_id or _receipt_id('citation', payload)
        payload['content_hash'] = self.content_hash or _stable_hash(payload)
        return payload

    def _content_payload(self) -> dict[str, Any]:
        return {
            'schema_version': self.schema_version,
            'canonical_id': self.canonical_id,
            'identifier_type': self.identifier_type,
            'identifier_version': self.identifier_version,
            'metadata': {
                'title': self.title,
                'authors': list(self.authors),
                'year': self.year,
                'venue': self.venue,
            },
            'source': {
                'url': self.source_url,
                'metadata_source': self.metadata_source,
                'retrieved_at': self.retrieved_at,
            },
            'bibtex': self.bibtex,
            'bibtex_key': self.bibtex_key,
            'claim_support_refs': [dict(item) for item in self.claim_support_refs],
            'status': self.status,
            'content_access_status': self.content_access_status,
            'diagnostics': list(self.diagnostics),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, verify_hash: bool = True) -> 'CitationReceipt':
        metadata = _mapping(data.get('metadata'))
        source = _mapping(data.get('source'))
        receipt = cls(
            schema_version=int(data.get('schema_version', 0) or 0),
            canonical_id=str(data.get('canonical_id', '') or ''),
            identifier_type=str(data.get('identifier_type', '') or ''),
            identifier_version=str(data.get('identifier_version', '') or ''),
            title=str(metadata.get('title', '') or ''),
            authors=tuple(str(item) for item in metadata.get('authors', []) or []),
            year=str(metadata.get('year', '') or ''),
            venue=str(metadata.get('venue', '') or ''),
            source_url=str(source.get('url', '') or ''),
            metadata_source=str(source.get('metadata_source', '') or ''),
            retrieved_at=str(source.get('retrieved_at', '') or ''),
            bibtex=str(data.get('bibtex', '') or ''),
            bibtex_key=str(data.get('bibtex_key', '') or ''),
            claim_support_refs=tuple(dict(item) for item in data.get('claim_support_refs', []) or []),
            status=str(data.get('status', '') or ''),
            content_access_status=str(data.get('content_access_status', '') or ''),
            diagnostics=tuple(str(item) for item in data.get('diagnostics', []) or []),
            receipt_id=str(data.get('receipt_id', '') or ''),
            content_hash=str(data.get('content_hash', '') or ''),
        )
        if receipt.schema_version != CITATION_RECEIPT_SCHEMA_VERSION:
            raise ValueError(f'unsupported citation receipt schema_version: {receipt.schema_version}')
        if receipt.status not in _VALID_RESOLUTION_STATUSES:
            raise ValueError(f'unsupported citation resolution status: {receipt.status}')
        if receipt.content_access_status not in _VALID_CONTENT_ACCESS_STATUSES:
            raise ValueError(f'unsupported citation content access status: {receipt.content_access_status}')
        expected = _stable_hash(receipt._content_payload() | {'receipt_id': receipt.receipt_id})
        if verify_hash and (not receipt.content_hash or receipt.content_hash != expected):
            raise ValueError('citation receipt content hash mismatch')
        return receipt


@dataclass(frozen=True)
class CitationPackageValidation:
    passed: bool
    errors: tuple[dict[str, Any], ...] = ()
    warnings: tuple[dict[str, Any], ...] = ()
    summary: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            'passed': self.passed,
            'errors': [dict(item) for item in self.errors],
            'summary': dict(self.summary),
            'warnings': [dict(item) for item in self.warnings],
        }


def build_citation_receipt(
    *,
    canonical_id: str,
    identifier_type: str,
    title: str,
    authors: Sequence[str],
    year: str,
    venue: str,
    source_url: str,
    metadata_source: str,
    retrieved_at: str,
    identifier_version: str = '',
    status: str = 'verified',
    content_access_status: str = 'not_assessed',
    diagnostics: Sequence[str] = (),
    entry_type: str | None = None,
    publication_fields: Mapping[str, str] | None = None,
) -> CitationReceipt:
    normalized_status = status.strip().lower()
    if normalized_status not in _VALID_RESOLUTION_STATUSES:
        raise ValueError(f'unsupported citation resolution status: {status}')
    normalized_access_status = content_access_status.strip().lower()
    if normalized_access_status not in _VALID_CONTENT_ACCESS_STATUSES:
        raise ValueError(f'unsupported citation content access status: {content_access_status}')
    normalized_authors = tuple(
        _bounded_text(item, 160) for item in authors if str(item).strip()
    )
    clean_title = _bounded_text(title, 500)
    clean_year = _year(year)
    clean_source = _bounded_text(source_url, 1_024)
    clean_provider = _bounded_text(metadata_source, 80)
    problems = list(str(item)[:320] for item in diagnostics)
    if normalized_status == 'verified':
        for field_name, value in (
            ('canonical_id', canonical_id),
            ('identifier_type', identifier_type),
            ('title', clean_title),
            ('authors', normalized_authors),
            ('year', clean_year),
            ('source_url', clean_source),
            ('metadata_source', clean_provider),
            ('retrieved_at', retrieved_at),
        ):
            if not value:
                problems.append(f'missing required verified field: {field_name}')
        if problems:
            normalized_status = 'unresolved'
    bibtex_key = normalized_citation_key(
        title=clean_title,
        authors=normalized_authors,
        year=clean_year,
        canonical_id=canonical_id,
    )
    bibtex = ''
    if normalized_status == 'verified':
        bibtex = materialize_bibtex(
            key=bibtex_key,
            identifier_type=identifier_type,
            canonical_id=canonical_id,
            title=clean_title,
            authors=normalized_authors,
            year=clean_year,
            venue=_bounded_text(venue, 300),
            source_url=clean_source,
            entry_type=entry_type,
            publication_fields=publication_fields,
        )
    receipt = CitationReceipt(
        canonical_id=_bounded_text(canonical_id, 400),
        identifier_type=_bounded_text(identifier_type, 40),
        identifier_version=_bounded_text(identifier_version, 40),
        title=clean_title,
        authors=normalized_authors,
        year=clean_year,
        venue=_bounded_text(venue, 300),
        source_url=clean_source,
        metadata_source=clean_provider,
        retrieved_at=_bounded_text(retrieved_at, 80),
        bibtex=bibtex,
        bibtex_key=bibtex_key,
        status=normalized_status,
        content_access_status=normalized_access_status,
        diagnostics=tuple(dict.fromkeys(problems)),
    )
    payload = receipt._content_payload()
    receipt_id = _receipt_id('citation', payload)
    return CitationReceipt(**{**receipt.__dict__, 'receipt_id': receipt_id, 'content_hash': _stable_hash(payload | {'receipt_id': receipt_id})})


def validate_citation_package(
    *,
    manuscript_text: str,
    bibliography_text: str,
    citation_receipts: Sequence[Any],
) -> CitationPackageValidation:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    manuscript_keys = _citation_keys(manuscript_text)
    try:
        entries = parse_bibliography(bibliography_text)
    except BibliographyError as exc:
        entries = {}
        errors.append({'code': exc.code, 'bibtex_key': exc.key, 'message': str(exc)[:320]})
    bibliography_keys = set(entries)
    receipts: dict[str, CitationReceipt] = {}
    for index, raw in enumerate(citation_receipts):
        try:
            if not isinstance(raw, Mapping):
                raise TypeError('citation receipt must be an object')
            receipt = CitationReceipt.from_dict(raw)
        except (TypeError, ValueError) as exc:
            errors.append({'code': 'invalid_citation_receipt', 'index': index, 'message': str(exc)[:320]})
            continue
        existing = receipts.get(receipt.bibtex_key)
        if existing and existing.content_hash != receipt.content_hash:
            errors.append({'code': 'conflicting_citation_receipts', 'bibtex_key': receipt.bibtex_key})
        receipts[receipt.bibtex_key] = receipt
        if receipt.status != 'verified':
            errors.append({'code': 'citation_receipt_not_verified', 'bibtex_key': receipt.bibtex_key})
        else:
            try:
                original = parse_bibliography(receipt.bibtex)
                if set(original) != {receipt.bibtex_key} or not identity_matches(
                    original[receipt.bibtex_key], receipt.identifier_type, receipt.canonical_id, receipt.identifier_version,
                ):
                    raise ValueError('receipt BibTeX and canonical identity disagree')
            except (BibliographyError, ValueError) as exc:
                errors.append({'code': 'invalid_citation_receipt', 'index': index, 'message': str(exc)[:320]})
                continue
            entry = entries.get(receipt.bibtex_key)
            if entry is not None:
                if not identity_matches(entry, receipt.identifier_type, receipt.canonical_id, receipt.identifier_version):
                    errors.append({'code': 'bibliography_identity_mismatch', 'bibtex_key': receipt.bibtex_key})
                else:
                    warnings.extend(metadata_differences(receipt.bibtex_key, original[receipt.bibtex_key], entry))
    receipt_keys = set(receipts)
    for key in sorted(manuscript_keys - bibliography_keys):
        errors.append({'code': 'citation_missing_from_bibliography', 'bibtex_key': key})
    for key in sorted(manuscript_keys - receipt_keys):
        errors.append({'code': 'citation_missing_verified_receipt', 'bibtex_key': key})
    for key in sorted(bibliography_keys - receipt_keys):
        errors.append({'code': 'bibliography_missing_verified_receipt', 'bibtex_key': key})
    for key in sorted(receipt_keys - bibliography_keys):
        errors.append({'code': 'receipt_missing_from_bibliography', 'bibtex_key': key})
    return CitationPackageValidation(
        passed=not errors,
        errors=tuple(errors),
        warnings=tuple(warnings),
        summary={
            'manuscript_key_count': len(manuscript_keys),
            'bibliography_key_count': len(bibliography_keys),
            'citation_receipt_count': len(citation_receipts),
        },
    )


def normalized_citation_key(*, title: str, authors: Sequence[str], year: str, canonical_id: str) -> str:
    author = _ascii_token(authors[0].split()[-1] if authors else 'source') or 'source'
    title_words = [_ascii_token(word) for word in title.split()]
    title_token = next((word for word in title_words if len(word) >= 3), 'paper')
    suffix = hashlib.sha256(canonical_id.casefold().encode('utf-8')).hexdigest()[:6]
    return f'{author}{_year(year) or "nd"}{title_token}{suffix}'[:80]


def materialize_bibtex(
    *,
    key: str,
    identifier_type: str,
    canonical_id: str,
    title: str,
    authors: Sequence[str],
    year: str,
    venue: str,
    source_url: str,
    entry_type: str | None = None,
    publication_fields: Mapping[str, str] | None = None,
) -> str:
    entry_type = entry_type or ('article' if identifier_type == 'doi' else 'misc')
    if entry_type not in {'article', 'inproceedings', 'book', 'incollection', 'misc'}:
        raise ValueError(f'unsupported bibliography entry type: {entry_type}')
    fields = [('title', title), ('author', ' and '.join(authors)), ('year', year)]
    if venue:
        venue_field = {'article': 'journal', 'inproceedings': 'booktitle', 'incollection': 'booktitle'}.get(entry_type, 'howpublished')
        fields.append((venue_field, venue))
    allowed = {'journal', 'booktitle', 'volume', 'number', 'pages', 'publisher', 'edition', 'howpublished', 'note'}
    for name, value in (publication_fields or {}).items():
        if name not in allowed:
            raise ValueError(f'unsupported publication field: {name}')
        if value and name not in dict(fields):
            fields.append((name, _bounded_text(value, 500)))
    if identifier_type == 'doi':
        fields.append(('doi', canonical_id.removeprefix('doi:')))
    elif identifier_type == 'arxiv':
        fields.extend((('eprint', canonical_id.removeprefix('arxiv:')), ('archivePrefix', 'arXiv')))
    fields.append(('url', source_url))
    lines = [f'@{entry_type}{{{key},']
    lines.extend(f'  {name} = {{{_bibtex_value(value)}}},' for name, value in fields if value)
    lines.append('}')
    return '\n'.join(lines)


def _citation_keys(text: str) -> set[str]:
    return {
        key.strip()
        for match in _CITE_RE.finditer(text)
        for key in match.group('keys').split(',')
        if key.strip()
    }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _year(value: Any) -> str:
    match = re.search(r'\b(?:18|19|20|21)\d{2}\b', str(value or ''))
    return match.group(0) if match else ''


def _ascii_token(value: str) -> str:
    normalized = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode('ascii').lower()
    return re.sub(r'[^a-z0-9]+', '', normalized)


def _bibtex_value(value: str) -> str:
    return ' '.join(str(value).replace('\\', '\\\\').replace('{', '\\{').replace('}', '\\}').split())


def _bounded_text(value: Any, limit: int) -> str:
    return ' '.join(str(value or '').split())[:limit]


def _stable_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _receipt_id(prefix: str, payload: Mapping[str, Any]) -> str:
    return f'{prefix}-{_stable_hash(payload)[:20]}'
