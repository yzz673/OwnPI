from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ownpi.runtime.limits.projection import (
    DEFAULT_INTERNAL_MEMORY_READ_BODY_BYTES,
)

from ._validation import (
    bounded_count,
    bounded_positive_int,
    bounded_text,
    require_enum,
    require_tuple,
    require_unique,
)
from .contracts import (
    InternalMemoryContentKind,
    InternalMemoryDomain,
    InternalMemoryEvidenceStrength,
    InternalMemoryFreshness,
    InternalMemoryProvenanceRef,
    InternalMemoryScope,
    InternalMemoryScopeType,
    parse_internal_ref,
)


MAX_QUERY_LENGTH = 2_000
class InternalMemoryResultStatus(str, Enum):
    SUCCESS = "success"
    EMPTY = "empty"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    INVALID_REQUEST = "invalid_request"
    ERROR = "error"


@dataclass(frozen=True)
class InternalMemoryScopeFilter:
    scope_type: InternalMemoryScopeType
    scope_id: str = ""

    def __post_init__(self) -> None:
        require_enum(self.scope_type, InternalMemoryScopeType, field_name="scope_type")
        bounded_text(
            self.scope_id,
            field_name="scope_id",
            max_length=240,
            required=self.scope_type != InternalMemoryScopeType.USER_GLOBAL,
        )
        if self.scope_type == InternalMemoryScopeType.USER_GLOBAL and self.scope_id:
            raise ValueError("user-global scope filter cannot carry a scope id")


@dataclass(frozen=True)
class InternalMemorySearchRequest:
    query: str
    domains: tuple[InternalMemoryDomain, ...] = ()
    content_kinds: tuple[InternalMemoryContentKind, ...] = ()
    scope_filters: tuple[InternalMemoryScopeFilter, ...] = ()
    limit: int = 5

    def __post_init__(self) -> None:
        bounded_text(self.query, field_name="query", max_length=MAX_QUERY_LENGTH)
        for field_name, values, enum_type, maximum in (
            ("domains", self.domains, InternalMemoryDomain, 2),
            ("content_kinds", self.content_kinds, InternalMemoryContentKind, 6),
        ):
            items = require_tuple(values, field_name=field_name, max_items=maximum)
            require_unique(items, field_name=field_name)
            for item in items:
                require_enum(item, enum_type, field_name=field_name)
        filters = require_tuple(
            self.scope_filters, field_name="scope_filters", max_items=16
        )
        require_unique(filters, field_name="scope_filters")
        bounded_positive_int(self.limit, field_name="limit")


@dataclass(frozen=True)
class InternalMemoryReadRequest:
    logical_ref: str
    max_body_bytes: int = DEFAULT_INTERNAL_MEMORY_READ_BODY_BYTES
    include_provenance: bool = True

    def __post_init__(self) -> None:
        parse_internal_ref(self.logical_ref)
        bounded_positive_int(
            self.max_body_bytes,
            field_name="max_body_bytes",
        )
        if not isinstance(self.include_provenance, bool):
            raise TypeError("include_provenance must be a boolean")


@dataclass(frozen=True)
class InternalMemorySearchHit:
    logical_ref: str
    domain: InternalMemoryDomain
    content_kind: InternalMemoryContentKind
    summary: str
    scope: InternalMemoryScope
    evidence_strength: InternalMemoryEvidenceStrength
    freshness: InternalMemoryFreshness
    score: float

    def __post_init__(self) -> None:
        parsed = parse_internal_ref(self.logical_ref)
        require_enum(self.domain, InternalMemoryDomain, field_name="domain")
        require_enum(self.content_kind, InternalMemoryContentKind, field_name="content_kind")
        require_enum(
            self.evidence_strength,
            InternalMemoryEvidenceStrength,
            field_name="evidence_strength",
        )
        if parsed.domain != self.domain:
            raise ValueError("search hit domain does not match logical ref")
        bounded_text(self.summary, field_name="summary")
        if isinstance(self.score, bool) or not isinstance(self.score, (int, float)):
            raise TypeError("score must be numeric")
        if not 0.0 <= float(self.score) <= 1.0:
            raise ValueError("score must be between zero and one")


@dataclass(frozen=True)
class InternalMemorySearchResult:
    status: InternalMemoryResultStatus
    hits: tuple[InternalMemorySearchHit, ...]
    matched_count: int
    truncated: bool = False
    fallback: str = ""

    def __post_init__(self) -> None:
        require_enum(self.status, InternalMemoryResultStatus, field_name="status")
        hits = require_tuple(self.hits, field_name="hits")
        bounded_count(self.matched_count, field_name="matched_count")
        if self.matched_count < len(hits):
            raise ValueError("matched_count cannot be smaller than returned hits")
        bounded_text(self.fallback, field_name="fallback", max_length=120, required=False)
        _validate_result_status(self.status, len(hits))
        if not isinstance(self.truncated, bool):
            raise TypeError("truncated must be a boolean")


@dataclass(frozen=True)
class InternalMemoryReadResult:
    status: InternalMemoryResultStatus
    logical_ref: str
    domain: InternalMemoryDomain | None = None
    content_kind: InternalMemoryContentKind | None = None
    summary: str = ""
    body: str = ""
    scope: InternalMemoryScope | None = None
    evidence_strength: InternalMemoryEvidenceStrength | None = None
    freshness: InternalMemoryFreshness | None = None
    provenance: tuple[InternalMemoryProvenanceRef, ...] = ()
    truncated: bool = False
    fallback: str = ""

    def __post_init__(self) -> None:
        parsed = parse_internal_ref(self.logical_ref)
        require_enum(self.status, InternalMemoryResultStatus, field_name="status")
        bounded_text(self.summary, field_name="summary", required=False)
        bounded_text(self.body, field_name="body", required=False)
        provenance = require_tuple(self.provenance, field_name="provenance")
        require_unique(provenance, field_name="provenance")
        bounded_text(self.fallback, field_name="fallback", max_length=120, required=False)
        has_content = bool(self.body)
        _validate_result_status(self.status, int(has_content))
        if has_content:
            if self.domain is None or parsed.domain != self.domain:
                raise ValueError("read result domain must match logical ref")
            if None in (self.content_kind, self.scope, self.evidence_strength, self.freshness):
                raise ValueError("successful read requires complete record metadata")
        elif any(
            value is not None
            for value in (
                self.domain,
                self.content_kind,
                self.scope,
                self.evidence_strength,
                self.freshness,
            )
        ) or self.summary or provenance:
            raise ValueError("empty read result cannot carry record content")
        if not isinstance(self.truncated, bool):
            raise TypeError("truncated must be a boolean")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "logical_ref": self.logical_ref,
            "domain": self.domain.value if self.domain else "",
            "content_kind": self.content_kind.value if self.content_kind else "",
            "summary": self.summary,
            "body": self.body,
            "scope": self.scope.to_dict() if self.scope else None,
            "evidence_strength": self.evidence_strength.value if self.evidence_strength else "",
            "freshness": self.freshness.to_dict() if self.freshness else None,
            "provenance": [item.to_dict() for item in self.provenance],
            "truncated": self.truncated,
            "fallback": self.fallback,
        }


def _validate_result_status(status: InternalMemoryResultStatus, returned_count: int) -> None:
    empty_statuses = {
        InternalMemoryResultStatus.EMPTY,
        InternalMemoryResultStatus.UNAVAILABLE,
        InternalMemoryResultStatus.INVALID_REQUEST,
        InternalMemoryResultStatus.ERROR,
    }
    if status == InternalMemoryResultStatus.SUCCESS and returned_count == 0:
        raise ValueError("successful result must contain model-visible content")
    if status in empty_statuses and returned_count:
        raise ValueError(f"{status.value} result cannot contain model-visible content")
