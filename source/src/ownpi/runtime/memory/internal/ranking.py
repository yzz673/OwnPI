from __future__ import annotations

import re
from datetime import datetime

from .contracts import InternalMemoryEvidenceStrength, InternalMemoryScopeType
from .queries import InternalMemoryScopeFilter, InternalMemorySearchRequest
from .storage import StoredInternalMemoryRecord


_TOKEN_PATTERN = re.compile(r"[\w+-]+", re.UNICODE)
_SCOPE_RANK = {
    InternalMemoryScopeType.WORKFLOW: 0,
    InternalMemoryScopeType.REPOSITORY: 1,
    InternalMemoryScopeType.PROJECT: 2,
    InternalMemoryScopeType.USER_GLOBAL: 3,
}
_EVIDENCE_RANK = {
    InternalMemoryEvidenceStrength.EXPLICIT: 0,
    InternalMemoryEvidenceStrength.VERIFIED: 1,
    InternalMemoryEvidenceStrength.REPEATED: 2,
    InternalMemoryEvidenceStrength.INFERRED: 3,
    InternalMemoryEvidenceStrength.UNVERIFIED: 4,
}


def rank_internal_records(
    records: tuple[StoredInternalMemoryRecord, ...],
    request: InternalMemorySearchRequest,
    *,
    runtime_scope_filters: tuple[InternalMemoryScopeFilter, ...],
) -> tuple[tuple[StoredInternalMemoryRecord, float], ...]:
    query_terms = _terms(request.query)
    ranked: list[tuple[StoredInternalMemoryRecord, float]] = []
    for record in records:
        if request.domains and record.domain not in request.domains:
            continue
        if request.content_kinds and record.content_kind not in request.content_kinds:
            continue
        if not internal_scope_matches(
            record.scope.scope_type,
            record.scope.scope_id,
            runtime_scope_filters=runtime_scope_filters,
            request_scope_filters=request.scope_filters,
        ):
            continue
        searchable = " ".join(
            (
                record.summary,
                record.content_kind.value.replace("_", " "),
                record.scope.scope_id,
                record.scope.reuse_boundary,
            )
        )
        record_terms = _terms(searchable)
        overlap = len(query_terms & record_terms)
        if overlap == 0:
            continue
        ranked.append((record, overlap / len(query_terms)))
    ranked.sort(
        key=lambda pair: (
            -pair[1],
            _SCOPE_RANK[pair[0].scope.scope_type],
            _EVIDENCE_RANK[pair[0].evidence_strength],
            -_timestamp(pair[0].freshness.updated_at),
            pair[0].logical_ref,
        )
    )
    return tuple(ranked)


def internal_scope_matches(
    scope_type: InternalMemoryScopeType,
    scope_id: str,
    *,
    runtime_scope_filters: tuple[InternalMemoryScopeFilter, ...],
    request_scope_filters: tuple[InternalMemoryScopeFilter, ...] = (),
) -> bool:
    identity = (scope_type, scope_id)
    allowed = {(item.scope_type, item.scope_id) for item in runtime_scope_filters}
    if identity not in allowed:
        return False
    if not request_scope_filters:
        return True
    requested = {(item.scope_type, item.scope_id) for item in request_scope_filters}
    return identity in requested


def _terms(value: str) -> frozenset[str]:
    return frozenset(match.group(0).casefold() for match in _TOKEN_PATTERN.finditer(value))


def _timestamp(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
