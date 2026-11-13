from __future__ import annotations

from typing import Any

from ownpi.runtime.limits.projection import (
    MAX_SEMANTIC_TRACE_LIST_ITEMS,
    MAX_SEMANTIC_TRACE_MATCHES,
    MAX_SEMANTIC_TRACE_TEXT_CHARS,
)
from ownpi.runtime.semantic_decision.types import SemanticDecisionTrace, SemanticMatch


def bounded_trace_payload(trace: SemanticDecisionTrace) -> dict[str, Any]:
    """Project one decision into a bounded diagnostic payload.

    The caller owns persistence. This helper deliberately does not create a
    scratchpad history or any routing authority.
    """

    return {
        'decision_name': _bounded_text(trace.decision_name),
        'source_ref': _bounded_text(trace.source_ref),
        'source_field': _bounded_text(trace.source_field),
        'result': _bounded_text(trace.result),
        'matches': [_match_payload(match) for match in trace.matches[:MAX_SEMANTIC_TRACE_MATCHES]],
        'conflicts': [_bounded_text(item) for item in trace.conflicts[:MAX_SEMANTIC_TRACE_LIST_ITEMS]],
        'fallback_reason': _bounded_text(trace.fallback_reason),
        'warnings': [_bounded_text(item) for item in trace.warnings[:MAX_SEMANTIC_TRACE_LIST_ITEMS]],
        'truncated_match_count': max(0, len(trace.matches) - MAX_SEMANTIC_TRACE_MATCHES),
    }


def _match_payload(match: SemanticMatch) -> dict[str, Any]:
    return {
        'rule_id': _bounded_text(match.rule_id),
        'category': _bounded_text(match.category),
        'phrase': _bounded_text(match.phrase),
        'matched_text': _bounded_text(match.matched_text),
        'span_start': int(match.span_start),
        'span_end': int(match.span_end),
        'source_field': _bounded_text(match.source_field),
        'negated': bool(match.negated),
    }


def _bounded_text(value: object) -> str:
    return str(value or '')[:MAX_SEMANTIC_TRACE_TEXT_CHARS]
