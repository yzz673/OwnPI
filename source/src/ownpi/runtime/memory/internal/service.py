from __future__ import annotations

from dataclasses import asdict
from enum import Enum
from typing import Any

from ownpi.runtime.memory.payloads import truncate_utf8

from .queries import (
    InternalMemoryReadRequest,
    InternalMemoryReadResult,
    InternalMemoryResultStatus,
    InternalMemorySearchHit,
    InternalMemorySearchRequest,
    InternalMemorySearchResult,
)
from .queries import InternalMemoryScopeFilter
from .ranking import internal_scope_matches, rank_internal_records
from .repository import InternalMemoryRepository, InternalMemoryRepositoryError


class InternalMemoryService:
    def __init__(self, repository: InternalMemoryRepository) -> None:
        self._repository = repository

    def search(
        self,
        request: InternalMemorySearchRequest,
        *,
        runtime_scope_filters: tuple[InternalMemoryScopeFilter, ...],
    ) -> InternalMemorySearchResult:
        try:
            ranked = rank_internal_records(
                self._repository.committed_metadata(),
                request,
                runtime_scope_filters=runtime_scope_filters,
            )
            hits = tuple(
                InternalMemorySearchHit(
                    logical_ref=record.logical_ref,
                    domain=record.domain,
                    content_kind=record.content_kind,
                    summary=record.summary,
                    scope=record.scope,
                    evidence_strength=record.evidence_strength,
                    freshness=record.freshness,
                    score=score,
                )
                for record, score in ranked[: request.limit]
            )
            if not hits:
                return InternalMemorySearchResult(
                    status=InternalMemoryResultStatus.EMPTY,
                    hits=(),
                    matched_count=0,
                )
            return InternalMemorySearchResult(
                status=InternalMemoryResultStatus.SUCCESS,
                hits=hits,
                matched_count=len(ranked),
                truncated=len(ranked) > len(hits),
            )
        except (InternalMemoryRepositoryError, OSError, UnicodeError, ValueError):
            return InternalMemorySearchResult(
                status=InternalMemoryResultStatus.UNAVAILABLE,
                hits=(),
                matched_count=0,
                fallback="internal_store_unavailable",
            )

    def read(
        self,
        request: InternalMemoryReadRequest,
        *,
        runtime_scope_filters: tuple[InternalMemoryScopeFilter, ...],
    ) -> InternalMemoryReadResult:
        try:
            record = self._repository.read_committed(request.logical_ref)
            if record is None:
                return InternalMemoryReadResult(
                    status=InternalMemoryResultStatus.EMPTY,
                    logical_ref=request.logical_ref,
                    fallback="not_found_or_not_committed",
                )
            if not internal_scope_matches(
                record.scope.scope_type,
                record.scope.scope_id,
                runtime_scope_filters=runtime_scope_filters,
            ):
                return InternalMemoryReadResult(
                    status=InternalMemoryResultStatus.EMPTY,
                    logical_ref=request.logical_ref,
                    fallback="not_found_or_out_of_scope",
                )
            body, truncated = truncate_utf8(record.body, request.max_body_bytes)
            return InternalMemoryReadResult(
                status=InternalMemoryResultStatus.SUCCESS,
                logical_ref=record.logical_ref,
                domain=record.domain,
                content_kind=record.content_kind,
                summary=record.summary,
                body=body,
                scope=record.scope,
                evidence_strength=record.evidence_strength,
                freshness=record.freshness,
                provenance=record.provenance if request.include_provenance else (),
                truncated=truncated,
            )
        except (InternalMemoryRepositoryError, OSError, UnicodeError, ValueError):
            return InternalMemoryReadResult(
                status=InternalMemoryResultStatus.UNAVAILABLE,
                logical_ref=request.logical_ref,
                fallback="internal_store_unavailable",
            )


def internal_result_to_dict(result: object) -> dict[str, Any]:
    return _json_value(asdict(result))


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value
