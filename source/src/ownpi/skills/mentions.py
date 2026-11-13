from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shlex
from typing import Sequence

from ownpi.schemas.context import SkillSummary


_DOLLAR_MENTION_PATTERN = re.compile(r"(?<![\w$])\$([A-Za-z0-9][A-Za-z0-9_-]*)")


@dataclass(frozen=True)
class ExplicitSkillResolution:
    matches: tuple[SkillSummary, ...]
    unresolved: tuple[str, ...]
    ambiguous: tuple[str, ...]


def resolve_explicit_skill_mentions(
    message: str,
    catalog: Sequence[SkillSummary],
) -> ExplicitSkillResolution:
    requested = _requested_mentions(message)
    by_identifier = _catalog_index(catalog)
    matches: list[SkillSummary] = []
    unresolved: list[str] = []
    ambiguous: list[str] = []
    seen_paths: set[str] = set()
    for mention in requested:
        candidates = by_identifier.get(_normalize_identifier(mention), ())
        if not candidates:
            unresolved.append(mention)
            continue
        unique = {item.path: item for item in candidates}
        if len(unique) != 1:
            ambiguous.append(mention)
            continue
        summary = next(iter(unique.values()))
        if summary.path in seen_paths:
            continue
        seen_paths.add(summary.path)
        matches.append(summary)
    return ExplicitSkillResolution(
        matches=tuple(matches),
        unresolved=tuple(unresolved),
        ambiguous=tuple(ambiguous),
    )


def is_explicit_skill_slash_message(
    message: str,
    catalog: Sequence[SkillSummary],
) -> bool:
    first = _first_token(message)
    if not first.startswith('/'):
        return False
    resolution = resolve_explicit_skill_mentions(first, catalog)
    return len(resolution.matches) == 1 and not resolution.unresolved and not resolution.ambiguous


def _requested_mentions(message: str) -> tuple[str, ...]:
    text = str(message or '').strip()
    if not text:
        return ()
    mentions = [match.group(1) for match in _DOLLAR_MENTION_PATTERN.finditer(text)]
    first = _first_token(text)
    if first.startswith('/') and len(first) > 1:
        mentions.insert(0, first[1:])
    for token in _tokens(text):
        if token.casefold().endswith('/skill.md') or token.casefold() == 'skill.md':
            mentions.append(token)
    return tuple(dict.fromkeys(mention for mention in mentions if mention))


def _catalog_index(
    catalog: Sequence[SkillSummary],
) -> dict[str, tuple[SkillSummary, ...]]:
    mutable: dict[str, list[SkillSummary]] = {}
    for summary in catalog:
        identifiers = {
            summary.name,
            _slug(summary.name),
            summary.path,
            str(Path(summary.path).expanduser()) if not summary.path.startswith('bundled://') else '',
            summary.path.removeprefix('bundled://'),
            *(summary.aliases or ()),
        }
        for identifier in identifiers:
            normalized = _normalize_identifier(identifier)
            if normalized:
                mutable.setdefault(normalized, []).append(summary)
    return {key: tuple(value) for key, value in mutable.items()}


def _normalize_identifier(value: str) -> str:
    text = str(value or '').strip().removeprefix('$').removeprefix('/')
    return text.casefold()


def _slug(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', str(value).casefold()).strip('-')


def _first_token(message: str) -> str:
    tokens = _tokens(message)
    return tokens[0] if tokens else ''


def _tokens(message: str) -> tuple[str, ...]:
    try:
        return tuple(shlex.split(str(message or '')))
    except ValueError:
        return tuple(str(message or '').split())


__all__ = [
    'ExplicitSkillResolution',
    'is_explicit_skill_slash_message',
    'resolve_explicit_skill_mentions',
]
