from __future__ import annotations

import codecs
import re
import unicodedata
from typing import Any
from urllib.parse import unquote, urlsplit

import latexcodec  # noqa: F401 -- registers the Unicode LaTeX codec
from pybtex.database import Entry, Person
from pybtex.database.input.bibtex import Parser
from pybtex.exceptions import PybtexError


class BibliographyError(ValueError):
    def __init__(self, code: str, message: str, key: str = '') -> None:
        super().__init__(message)
        self.code = code
        self.key = key


class _StrictParser(Parser):
    def handle_error(self, error: Exception) -> None:
        raise error

    def process_entry(self, entry_type: str, key: str, fields: Any) -> None:
        if key in self.data.entries:
            raise BibliographyError('duplicate_bibliography_key', 'Repeated bibliography key', key)
        super().process_entry(entry_type, key, fields)


def parse_bibliography(text: str) -> dict[str, Entry]:
    try:
        entries = dict(_StrictParser().parse_string(text).entries.items())
        canonical_keys = {key.casefold(): key for key in entries}
        complete: set[str] = set()

        def inherit(key: str, active: set[str]) -> None:
            if key in complete:
                return
            if key in active or len(active) >= 100:
                raise BibliographyError('invalid_bibliography_crossref', 'Cyclic or too deep crossref', key)
            entry = entries[key]
            parent_key = entry.fields.get('crossref')
            if parent_key:
                parent_key = canonical_keys.get(parent_key.casefold())
                if parent_key is None:
                    raise BibliographyError('invalid_bibliography_crossref', 'Missing crossref parent', key)
                inherit(parent_key, active | {key})
                for name, value in entries[parent_key].fields.items():
                    # A containing volume cannot supply the child's scholarly identity.
                    if name not in {'doi', 'eprint', 'archiveprefix', 'url', 'crossref'}:
                        entry.fields.setdefault(name, value)
            complete.add(key)

        for key in entries:
            inherit(key, set())
        return entries
    except BibliographyError:
        raise
    except (PybtexError, ValueError, UnicodeError, RecursionError) as exc:
        raise BibliographyError('invalid_bibliography', str(exc)[:320]) from exc


def normalize_doi(value: str) -> str:
    value = value.strip()
    if value.lower().startswith('doi:'):
        value = value[4:].strip()
    elif '://' in value:
        parsed = urlsplit(value)
        if parsed.scheme not in {'http', 'https'} or parsed.netloc.lower() not in {'doi.org', 'dx.doi.org'}:
            return ''
        value = unquote(parsed.path.lstrip('/'))
    return value.casefold() if re.fullmatch(r'10\.\d{4,9}/\S+', value) else ''


def normalize_arxiv(value: str) -> str:
    value = value.strip().casefold()
    if value.startswith('arxiv:'):
        value = value[6:]
    elif '://' in value:
        parsed = urlsplit(value)
        if parsed.scheme not in {'http', 'https'} or parsed.netloc not in {'arxiv.org', 'www.arxiv.org'}:
            return ''
        value = re.sub(r'^/(?:abs|pdf)/', '', parsed.path).removesuffix('.pdf')
    return value if re.fullmatch(r'(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[a-z-]+)?/\d{7})(?:v\d+)?', value) else ''


def identity_matches(entry: Entry, kind: str, canonical_id: str, version: str = '') -> bool:
    if kind == 'doi':
        expected = normalize_doi(canonical_id)
        identifiers = [entry.fields[name] for name in ('doi', 'url') if entry.fields.get(name)]
        actual = normalize_doi(entry.fields.get('doi', '') or entry.fields.get('url', ''))
        return bool(expected and actual == expected and all(
            not normalize_doi(value) or normalize_doi(value) == expected for value in identifiers
        ))
    if kind != 'arxiv':
        return False
    expected = normalize_arxiv(canonical_id)
    actual = normalize_arxiv(entry.fields.get('eprint', '') or entry.fields.get('url', ''))
    if not expected or not actual:
        return False
    expected_base = re.sub(r'v\d+$', '', expected)
    expected_version = expected[len(expected_base):]
    if version and (not re.fullmatch(r'v\d+', version) or (expected_version and version != expected_version)):
        return False
    pinned = version or expected_version
    if re.sub(r'v\d+$', '', actual) != expected_base or (pinned and actual != expected_base + pinned):
        return False
    url_identity = normalize_arxiv(entry.fields.get('url', ''))
    return not url_identity or url_identity == actual


def _text(value: str) -> str:
    decoded = codecs.decode(value, 'ulatex')
    return ' '.join(unicodedata.normalize('NFC', decoded).replace('{', '').replace('}', '').split()).casefold()


def _person(person: Person) -> tuple[tuple[str, ...], ...]:
    return tuple(tuple(_text(part) for part in parts) for parts in (
        person.first_names, person.middle_names, person.prelast_names, person.last_names, person.lineage_names,
    ))


def metadata_differences(key: str, original: Entry, final: Entry) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    fields = (set(original.fields) | set(final.fields)) - {'doi', 'eprint', 'url', 'crossref', 'archiveprefix'}
    for field in sorted(fields | set(original.persons) | set(final.persons) | {'entry_type'}):
        if field == 'entry_type':
            before, after = original.type, final.type
            same = before.casefold() == after.casefold()
        elif field in original.persons or field in final.persons:
            old_people, new_people = original.persons.get(field, []), final.persons.get(field, [])
            before = ' and '.join(str(person) for person in old_people)
            after = ' and '.join(str(person) for person in new_people)
            same = [_person(p) for p in old_people] == [_person(p) for p in new_people]
        else:
            before, after = original.fields.get(field, ''), final.fields.get(field, '')
            same = _text(before) == _text(after)
        if not same:
            warnings.append({
                'code': 'bibliographic_metadata_difference' if before else 'bibliographic_field_added',
                'bibtex_key': key, 'field': field,
                'receipt_value': before[:320], 'final_value': after[:320],
            })
    return warnings
