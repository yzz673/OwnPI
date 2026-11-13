"""Bounded lexical support for caller-owned semantic decisions."""

from ownpi.runtime.semantic_decision.matcher import (
    categories_hit,
    detect_conflicts,
    first_match,
    has_category,
    match_phrase_rules,
)
from ownpi.runtime.semantic_decision.trace import bounded_trace_payload
from ownpi.runtime.semantic_decision.types import (
    SemanticDecisionTrace,
    SemanticMatch,
    SemanticRule,
)

__all__ = [
    'SemanticDecisionTrace',
    'SemanticMatch',
    'SemanticRule',
    'bounded_trace_payload',
    'categories_hit',
    'detect_conflicts',
    'first_match',
    'has_category',
    'match_phrase_rules',
]
