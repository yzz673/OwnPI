from __future__ import annotations

import re
from collections.abc import Iterable

from ownpi.runtime.semantic_decision.types import SemanticMatch, SemanticRule


NEGATION_CUES = (
    'not',
    'no',
    'without',
    'do not',
    'does not',
    'did not',
    'rather than',
    'instead of',
)
_CLAUSE_BOUNDARIES = frozenset('.;!?。；！？\n')


def match_phrase_rules(
    text: object,
    rules: Iterable[SemanticRule],
    *,
    source_field: str = '',
) -> tuple[SemanticMatch, ...]:
    raw_text = str(text or '')
    matches: list[SemanticMatch] = []
    for rule in rules:
        haystack = raw_text.casefold() if rule.casefold else raw_text
        for phrase in rule.phrases:
            normalized_phrase = _normalize_phrase(phrase.casefold() if rule.casefold else phrase)
            if not normalized_phrase:
                continue
            for start, end in _iter_phrase_spans(haystack=haystack, phrase=normalized_phrase, rule=rule):
                matches.append(
                    SemanticMatch(
                        rule_id=rule.rule_id,
                        category=rule.category,
                        phrase=phrase,
                        matched_text=raw_text[start:end],
                        span_start=start,
                        span_end=end,
                        source_field=source_field,
                        negated=rule.negation_sensitive and _is_negated(haystack=haystack, start=start),
                    )
                )
    return tuple(matches)


def has_category(
    matches: Iterable[SemanticMatch],
    category: str,
    *,
    include_negated: bool = False,
) -> bool:
    return any(
        match.category == category and (include_negated or not match.negated)
        for match in matches
    )


def first_match(
    matches: Iterable[SemanticMatch],
    category: str,
    *,
    include_negated: bool = False,
) -> SemanticMatch | None:
    for match in matches:
        if match.category == category and (include_negated or not match.negated):
            return match
    return None


def categories_hit(
    matches: Iterable[SemanticMatch],
    *,
    include_negated: bool = False,
) -> tuple[str, ...]:
    categories: list[str] = []
    seen: set[str] = set()
    for match in matches:
        if match.negated and not include_negated:
            continue
        if match.category in seen:
            continue
        seen.add(match.category)
        categories.append(match.category)
    return tuple(categories)


def detect_conflicts(
    matches: Iterable[SemanticMatch],
    *,
    exclusive_categories: Iterable[Iterable[str]],
) -> tuple[str, ...]:
    hit = set(categories_hit(matches))
    conflicts: list[str] = []
    for group in exclusive_categories:
        present = tuple(category for category in group if category in hit)
        if len(present) > 1:
            conflicts.append('|'.join(present))
    return tuple(conflicts)


def _iter_phrase_spans(
    *,
    haystack: str,
    phrase: str,
    rule: SemanticRule,
) -> Iterable[tuple[int, int]]:
    if rule.match_mode == 'exact':
        if haystack == phrase:
            yield 0, len(haystack)
        return

    for match in re.finditer(_phrase_pattern(phrase=phrase, word_boundary=rule.word_boundary), haystack):
        yield match.start(), match.end()


def _phrase_pattern(*, phrase: str, word_boundary: bool) -> re.Pattern[str]:
    body = r'\s+'.join(re.escape(part) for part in phrase.split())
    if word_boundary and _uses_ascii_word_boundary(phrase):
        body = rf'(?<![A-Za-z0-9_]){body}(?![A-Za-z0-9_])'
    return re.compile(body)


def _uses_ascii_word_boundary(phrase: str) -> bool:
    return any(character.isascii() and (character.isalnum() or character == '_') for character in phrase)


def _normalize_phrase(phrase: str) -> str:
    return ' '.join(str(phrase or '').split())


def _is_negated(*, haystack: str, start: int) -> bool:
    prefix = haystack[max(0, start - 90):start]
    boundary = max((index for index, char in enumerate(prefix) if char in _CLAUSE_BOUNDARIES), default=-1)
    clause_prefix = ' '.join(prefix[boundary + 1 :].split())
    return any(_negation_cue_near_end(clause_prefix, cue=cue) for cue in NEGATION_CUES)


def _negation_cue_near_end(prefix: str, *, cue: str) -> bool:
    if not prefix:
        return False
    pattern = rf'(?<![A-Za-z0-9_]){re.escape(cue)}(?![A-Za-z0-9_])'
    matches = list(re.finditer(pattern, prefix))
    if not matches:
        return False
    suffix = prefix[matches[-1].end() :].strip()
    return len(suffix.split()) <= 6
