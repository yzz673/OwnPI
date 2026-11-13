from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


MatchMode = Literal['phrase', 'exact']


@dataclass(frozen=True)
class SemanticRule:
    rule_id: str
    phrases: tuple[str, ...]
    category: str
    source: str = ''
    match_mode: MatchMode = 'phrase'
    casefold: bool = True
    word_boundary: bool = True
    negation_sensitive: bool = True


@dataclass(frozen=True)
class SemanticMatch:
    rule_id: str
    category: str
    phrase: str
    matched_text: str
    span_start: int
    span_end: int
    source_field: str = ''
    negated: bool = False


@dataclass(frozen=True)
class SemanticDecisionTrace:
    decision_name: str
    source_ref: str = ''
    source_field: str = ''
    result: str = ''
    matches: tuple[SemanticMatch, ...] = ()
    conflicts: tuple[str, ...] = ()
    fallback_reason: str = ''
    warnings: tuple[str, ...] = ()
