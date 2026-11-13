from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ownpi.runtime.limits.projection import (
    MAX_EXTERNAL_KNOWLEDGE_RESULT_BODY_CHARS,
    MAX_EXTERNAL_KNOWLEDGE_RESULT_SUMMARY_CHARS,
)


MAX_LOGICAL_REF_LENGTH = 320
MAX_QUERY_LENGTH = 2_000
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_REF_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_CURSOR_PATTERN = re.compile(r"^nkfc:[A-Za-z0-9_-]{16,280}$")
_RELEASE_ROOT_PATTERN = re.compile(
    r"^installed_releases/([A-Za-z0-9][A-Za-z0-9._+-]*)/release_packages$"
)


class ExternalKnowledgeSourceId(str, Enum):
    METHOD_KNOWLEDGE = "method_knowledge"
    RESEARCH_STRATEGY = "research_strategy"


class ExternalKnowledgeContentKind(str, Enum):
    METHOD_SUBTARGET = "method_subtarget"
    METHOD_KNOWLEDGE = "method_knowledge"
    METHOD_PATTERN = "method_pattern"
    METHOD_CANONICAL_CASE = "method_canonical_case"
    PATTERN_CASE_RELEVANCE = "pattern_case_relevance"
    RESEARCH_ARCHETYPE = "research_archetype"
    RESEARCH_DIMENSION = "research_dimension"
    RESEARCH_STRATEGY = "research_strategy"
    RESEARCH_PAPER_SUPPORT = "research_paper_support"
    CONTRIBUTED_TO_STRATEGY_SYNTHESIS = "contributed_to_strategy_synthesis"
    SUPPORTS_DIMENSION = "supports_dimension"


METHOD_CONTENT_KINDS = frozenset(
    {
        ExternalKnowledgeContentKind.METHOD_SUBTARGET,
        ExternalKnowledgeContentKind.METHOD_KNOWLEDGE,
        ExternalKnowledgeContentKind.METHOD_PATTERN,
        ExternalKnowledgeContentKind.METHOD_CANONICAL_CASE,
        ExternalKnowledgeContentKind.PATTERN_CASE_RELEVANCE,
    }
)
RESEARCH_CONTENT_KINDS = frozenset(
    {
        ExternalKnowledgeContentKind.RESEARCH_ARCHETYPE,
        ExternalKnowledgeContentKind.RESEARCH_DIMENSION,
        ExternalKnowledgeContentKind.RESEARCH_STRATEGY,
        ExternalKnowledgeContentKind.RESEARCH_PAPER_SUPPORT,
        ExternalKnowledgeContentKind.CONTRIBUTED_TO_STRATEGY_SYNTHESIS,
        ExternalKnowledgeContentKind.SUPPORTS_DIMENSION,
    }
)


class ExternalKnowledgeResultStatus(str, Enum):
    SUCCESS = "success"
    EMPTY = "empty"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    INVALID_REQUEST = "invalid_request"
    ERROR = "error"


class ExternalKnowledgeAcceleratorStatus(str, Enum):
    ACTIVE = "active"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ExternalKnowledgeLogicalRef:
    release_id: str
    source_id: ExternalKnowledgeSourceId
    content_kind: ExternalKnowledgeContentKind
    stable_id: str

    def __post_init__(self) -> None:
        _bounded_ref_segment(self.release_id, field_name="release_id", max_length=96)
        _require_enum(self.source_id, ExternalKnowledgeSourceId, field_name="source_id")
        _require_enum(
            self.content_kind, ExternalKnowledgeContentKind, field_name="content_kind"
        )
        _bounded_ref_segment(self.stable_id, field_name="stable_id", max_length=160)
        _validate_source_kind(self.source_id, self.content_kind)
        if len(self.value) > MAX_LOGICAL_REF_LENGTH:
            raise ValueError("logical ref exceeds maximum length")

    @property
    def value(self) -> str:
        return ":".join(
            (
                "nkf",
                self.release_id,
                self.source_id.value,
                self.content_kind.value,
                self.stable_id,
            )
        )

    @classmethod
    def parse(cls, value: str) -> ExternalKnowledgeLogicalRef:
        _bounded_scalar(
            value, field_name="logical_ref", max_length=MAX_LOGICAL_REF_LENGTH
        )
        parts = value.split(":")
        if len(parts) != 5 or parts[0] != "nkf":
            raise ValueError("logical ref must use the canonical nkf format")
        try:
            source_id = ExternalKnowledgeSourceId(parts[2])
            content_kind = ExternalKnowledgeContentKind(parts[3])
        except ValueError as exc:
            raise ValueError(
                "logical ref has an unsupported source or content kind"
            ) from exc
        ref = cls(parts[1], source_id, content_kind, parts[4])
        if ref.value != value:
            raise ValueError("logical ref is not canonical")
        return ref


def encode_logical_ref(
    *,
    release_id: str,
    source_id: ExternalKnowledgeSourceId,
    content_kind: ExternalKnowledgeContentKind,
    stable_id: str,
) -> str:
    return ExternalKnowledgeLogicalRef(
        release_id, source_id, content_kind, stable_id
    ).value


def parse_logical_ref(value: str) -> ExternalKnowledgeLogicalRef:
    return ExternalKnowledgeLogicalRef.parse(value)


def derive_relation_stable_id(
    *,
    source_id: ExternalKnowledgeSourceId,
    content_kind: ExternalKnowledgeContentKind,
    endpoints: tuple[tuple[str, str], ...],
) -> str:
    _validate_source_kind(source_id, content_kind)
    if content_kind not in {
        ExternalKnowledgeContentKind.PATTERN_CASE_RELEVANCE,
        ExternalKnowledgeContentKind.CONTRIBUTED_TO_STRATEGY_SYNTHESIS,
        ExternalKnowledgeContentKind.SUPPORTS_DIMENSION,
    }:
        raise ValueError("relation identity requires a relation content kind")
    if not isinstance(endpoints, tuple) or not 2 <= len(endpoints) <= 8:
        raise ValueError("relation endpoints must be a tuple with two to eight entries")
    normalized: list[tuple[str, str]] = []
    for name, stable_id in endpoints:
        _bounded_ref_segment(name, field_name="endpoint_name", max_length=48)
        _bounded_ref_segment(stable_id, field_name="endpoint_stable_id", max_length=160)
        normalized.append((name, stable_id))
    if len({name for name, _ in normalized}) != len(normalized):
        raise ValueError("relation endpoint names must be unique")
    canonical = json.dumps(sorted(normalized), ensure_ascii=True, separators=(",", ":"))
    digest = hashlib.sha256(
        f"{source_id.value}\x1f{content_kind.value}\x1f{canonical}".encode("utf-8")
    ).hexdigest()
    return f"rel-{digest[:32]}"


@dataclass(frozen=True)
class ExternalKnowledgeSourceIdentity:
    source_id: ExternalKnowledgeSourceId
    package_id: str
    package_version: str
    schema_version: str
    manifest_sha256: str
    content_inventory_sha256: str
    package_content_digest: str

    def __post_init__(self) -> None:
        _require_enum(self.source_id, ExternalKnowledgeSourceId, field_name="source_id")
        for name in ("package_id", "package_version", "schema_version"):
            _bounded_scalar(getattr(self, name), field_name=name, max_length=128)
        for name in (
            "manifest_sha256",
            "content_inventory_sha256",
            "package_content_digest",
        ):
            _validate_sha256(getattr(self, name), field_name=name)

    def to_dict(self) -> dict[str, str]:
        return {
            "source_id": self.source_id.value,
            **_dataclass_scalars(self, skip={"source_id"}),
        }


@dataclass(frozen=True)
class ExternalKnowledgeAcceleratorIdentity:
    accelerator_id: str
    accelerates_source_id: ExternalKnowledgeSourceId
    accelerator_version: str
    manifest_sha256: str
    package_content_digest: str
    status: ExternalKnowledgeAcceleratorStatus
    fallback: str = ""

    def __post_init__(self) -> None:
        _bounded_ref_segment(
            self.accelerator_id, field_name="accelerator_id", max_length=96
        )
        _require_enum(
            self.accelerates_source_id,
            ExternalKnowledgeSourceId,
            field_name="source_id",
        )
        _require_enum(
            self.status, ExternalKnowledgeAcceleratorStatus, field_name="status"
        )
        required_identity = self.status == ExternalKnowledgeAcceleratorStatus.ACTIVE
        _bounded_scalar(
            self.accelerator_version,
            field_name="accelerator_version",
            max_length=64,
            required=required_identity,
        )
        for name in ("manifest_sha256", "package_content_digest"):
            value = getattr(self, name)
            if value:
                _validate_sha256(value, field_name=name)
            elif required_identity:
                raise ValueError(f"active accelerator requires {name}")
        _bounded_scalar(
            self.fallback, field_name="fallback", max_length=120, required=False
        )
        if self.status == ExternalKnowledgeAcceleratorStatus.ACTIVE and self.fallback:
            raise ValueError("active accelerator cannot declare a fallback")

    def to_dict(self) -> dict[str, str]:
        return {
            "accelerator_id": self.accelerator_id,
            "accelerates_source_id": self.accelerates_source_id.value,
            "accelerator_version": self.accelerator_version,
            "manifest_sha256": self.manifest_sha256,
            "package_content_digest": self.package_content_digest,
            "status": self.status.value,
            "fallback": self.fallback,
        }


@dataclass(frozen=True)
class ExternalKnowledgeReleasePin:
    release_id: str
    release_root_relative: str
    snapshot_sha256: str
    release_content_digest: str
    catalog_sha256: str
    sources: tuple[ExternalKnowledgeSourceIdentity, ...]
    accelerators: tuple[ExternalKnowledgeAcceleratorIdentity, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported external release pin schema version")
        _bounded_ref_segment(self.release_id, field_name="release_id", max_length=96)
        match = _RELEASE_ROOT_PATTERN.fullmatch(self.release_root_relative)
        if match is None or match.group(1) != self.release_id:
            raise ValueError(
                "release root must canonically identify the pinned release"
            )
        for name in ("snapshot_sha256", "release_content_digest", "catalog_sha256"):
            _validate_sha256(getattr(self, name), field_name=name)
        _require_tuple(self.sources, field_name="sources", max_items=2)
        expected = tuple(ExternalKnowledgeSourceId)
        if tuple(source.source_id for source in self.sources) != expected:
            raise ValueError("release pin must contain both sources in canonical order")
        _require_tuple(self.accelerators, field_name="accelerators", max_items=8)
        if len({item.accelerator_id for item in self.accelerators}) != len(
            self.accelerators
        ):
            raise ValueError("release pin accelerator identities must be unique")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "release_id": self.release_id,
            "release_root_relative": self.release_root_relative,
            "snapshot_sha256": self.snapshot_sha256,
            "release_content_digest": self.release_content_digest,
            "catalog_sha256": self.catalog_sha256,
            "sources": [source.to_dict() for source in self.sources],
            "accelerators": [item.to_dict() for item in self.accelerators],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ExternalKnowledgeReleasePin:
        try:
            sources = tuple(
                ExternalKnowledgeSourceIdentity(
                    source_id=ExternalKnowledgeSourceId(item["source_id"]),
                    package_id=item["package_id"],
                    package_version=item["package_version"],
                    schema_version=item["schema_version"],
                    manifest_sha256=item["manifest_sha256"],
                    content_inventory_sha256=item["content_inventory_sha256"],
                    package_content_digest=item["package_content_digest"],
                )
                for item in payload["sources"]
            )
            accelerators = tuple(
                ExternalKnowledgeAcceleratorIdentity(
                    accelerator_id=item["accelerator_id"],
                    accelerates_source_id=ExternalKnowledgeSourceId(
                        item["accelerates_source_id"]
                    ),
                    accelerator_version=item["accelerator_version"],
                    manifest_sha256=item["manifest_sha256"],
                    package_content_digest=item["package_content_digest"],
                    status=ExternalKnowledgeAcceleratorStatus(item["status"]),
                    fallback=item.get("fallback", ""),
                )
                for item in payload.get("accelerators", [])
            )
            return cls(
                release_id=payload["release_id"],
                release_root_relative=payload["release_root_relative"],
                snapshot_sha256=payload["snapshot_sha256"],
                release_content_digest=payload["release_content_digest"],
                catalog_sha256=payload["catalog_sha256"],
                sources=sources,
                accelerators=accelerators,
                schema_version=payload.get("schema_version", 1),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid serialized external release pin") from exc


@dataclass(frozen=True)
class ExternalKnowledgeSearchRequest:
    query: str
    sources: tuple[ExternalKnowledgeSourceId, ...] = ()
    intents: tuple[str, ...] = ()
    limit: int = 5

    def __post_init__(self) -> None:
        _bounded_scalar(self.query, field_name="query", max_length=MAX_QUERY_LENGTH)
        _require_tuple(self.sources, field_name="sources", max_items=2)
        if len(set(self.sources)) != len(self.sources):
            raise ValueError("search sources must be unique")
        for source in self.sources:
            _require_enum(source, ExternalKnowledgeSourceId, field_name="source")
        _bounded_string_tuple(
            self.intents, field_name="intents", max_items=8, max_length=64
        )
        _bounded_limit(self.limit, field_name="limit")


@dataclass(frozen=True)
class ExternalKnowledgeReadRequest:
    logical_ref: str
    relation: str = ""
    cursor: str = ""
    limit: int = 10

    def __post_init__(self) -> None:
        parse_logical_ref(self.logical_ref)
        _bounded_scalar(
            self.relation, field_name="relation", max_length=96, required=False
        )
        if self.relation and _REF_SEGMENT_PATTERN.fullmatch(self.relation) is None:
            raise ValueError("relation must be a canonical identifier")
        _validate_cursor(self.cursor)
        _bounded_limit(self.limit, field_name="limit")


@dataclass(frozen=True)
class ExternalKnowledgeProvenance:
    release_id: str
    source_id: ExternalKnowledgeSourceId
    package_id: str
    package_version: str
    logical_ref: str
    content_lineage: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        parsed = parse_logical_ref(self.logical_ref)
        if parsed.release_id != self.release_id or parsed.source_id != self.source_id:
            raise ValueError("provenance identity does not match logical ref")
        _bounded_scalar(self.package_id, field_name="package_id", max_length=128)
        _bounded_scalar(
            self.package_version, field_name="package_version", max_length=64
        )
        _bounded_string_tuple(
            self.content_lineage,
            field_name="content_lineage",
            max_items=8,
            max_length=160,
        )


@dataclass(frozen=True)
class ExternalKnowledgeSearchHit:
    logical_ref: str
    title: str
    summary: str
    score: float
    provenance: ExternalKnowledgeProvenance
    matched_intents: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        parse_logical_ref(self.logical_ref)
        if self.provenance.logical_ref != self.logical_ref:
            raise ValueError("search hit provenance must describe its logical ref")
        _bounded_scalar(self.title, field_name="title", max_length=300)
        _bounded_scalar(
            self.summary,
            field_name="summary",
            max_length=MAX_EXTERNAL_KNOWLEDGE_RESULT_SUMMARY_CHARS,
        )
        if isinstance(self.score, bool) or not isinstance(self.score, (int, float)):
            raise TypeError("score must be numeric")
        if not 0.0 <= float(self.score) <= 1.0:
            raise ValueError("score must be between zero and one")
        _bounded_string_tuple(
            self.matched_intents,
            field_name="matched_intents",
            max_items=8,
            max_length=64,
        )


@dataclass(frozen=True)
class ExternalKnowledgeRelationItem:
    logical_ref: str
    title: str
    summary: str

    def __post_init__(self) -> None:
        parse_logical_ref(self.logical_ref)
        _bounded_scalar(self.title, field_name="title", max_length=300)
        _bounded_scalar(
            self.summary,
            field_name="summary",
            max_length=MAX_EXTERNAL_KNOWLEDGE_RESULT_SUMMARY_CHARS,
        )


@dataclass(frozen=True)
class ExternalKnowledgeRelationPage:
    relation_ref: str
    items: tuple[ExternalKnowledgeRelationItem, ...]
    next_cursor: str = ""
    matched_count: int = 0
    truncated: bool = False

    def __post_init__(self) -> None:
        parse_logical_ref(self.relation_ref)
        _require_tuple(self.items, field_name="items")
        _validate_cursor(self.next_cursor)
        _bounded_count(self.matched_count, field_name="matched_count")
        if self.matched_count < len(self.items):
            raise ValueError(
                "relation matched_count cannot be smaller than returned items"
            )
        if not isinstance(self.truncated, bool):
            raise TypeError("truncated must be a boolean")


@dataclass(frozen=True)
class ExternalKnowledgeSearchResult:
    status: ExternalKnowledgeResultStatus
    hits: tuple[ExternalKnowledgeSearchHit, ...]
    matched_count: int
    fallback: str = ""
    truncated: bool = False

    def __post_init__(self) -> None:
        _require_enum(self.status, ExternalKnowledgeResultStatus, field_name="status")
        _require_tuple(self.hits, field_name="hits")
        _bounded_count(self.matched_count, field_name="matched_count")
        if self.matched_count < len(self.hits):
            raise ValueError("matched_count cannot be smaller than returned hits")
        _bounded_scalar(
            self.fallback, field_name="fallback", max_length=120, required=False
        )
        _validate_result_status(self.status, len(self.hits))
        if not isinstance(self.truncated, bool):
            raise TypeError("truncated must be a boolean")


@dataclass(frozen=True)
class ExternalKnowledgeReadResult:
    status: ExternalKnowledgeResultStatus
    logical_ref: str
    title: str = ""
    body: str = ""
    provenance: ExternalKnowledgeProvenance | None = None
    related_refs: tuple[str, ...] = ()
    relation_page: ExternalKnowledgeRelationPage | None = None
    fallback: str = ""
    truncated: bool = False

    def __post_init__(self) -> None:
        _require_enum(self.status, ExternalKnowledgeResultStatus, field_name="status")
        parse_logical_ref(self.logical_ref)
        _bounded_scalar(self.title, field_name="title", max_length=300, required=False)
        _bounded_scalar(
            self.body,
            field_name="body",
            max_length=MAX_EXTERNAL_KNOWLEDGE_RESULT_BODY_CHARS,
            required=False,
        )
        _require_tuple(self.related_refs, field_name="related_refs")
        for related_ref in self.related_refs:
            parse_logical_ref(related_ref)
        _bounded_scalar(
            self.fallback, field_name="fallback", max_length=120, required=False
        )
        has_content = bool(self.body or self.relation_page)
        _validate_result_status(self.status, int(has_content))
        if (
            self.provenance is not None
            and self.provenance.logical_ref != self.logical_ref
        ):
            raise ValueError("read provenance must describe the requested logical ref")
        if not isinstance(self.truncated, bool):
            raise TypeError("truncated must be a boolean")


def _validate_source_kind(
    source_id: ExternalKnowledgeSourceId,
    content_kind: ExternalKnowledgeContentKind,
) -> None:
    allowed = (
        METHOD_CONTENT_KINDS
        if source_id == ExternalKnowledgeSourceId.METHOD_KNOWLEDGE
        else RESEARCH_CONTENT_KINDS
    )
    if content_kind not in allowed:
        raise ValueError("content kind does not belong to the selected source")


def _validate_result_status(
    status: ExternalKnowledgeResultStatus, returned_count: int
) -> None:
    empty_statuses = {
        ExternalKnowledgeResultStatus.EMPTY,
        ExternalKnowledgeResultStatus.UNAVAILABLE,
        ExternalKnowledgeResultStatus.INVALID_REQUEST,
        ExternalKnowledgeResultStatus.ERROR,
    }
    if status == ExternalKnowledgeResultStatus.SUCCESS and returned_count == 0:
        raise ValueError("successful result must contain model-visible content")
    if status in empty_statuses and returned_count:
        raise ValueError(f"{status.value} result cannot contain model-visible content")


def _bounded_scalar(
    value: object,
    *,
    field_name: str,
    max_length: int,
    required: bool = True,
) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if required and not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    if len(value) > max_length:
        raise ValueError(f"{field_name} exceeds maximum length")
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise ValueError(f"{field_name} cannot contain control characters")


def _bounded_ref_segment(value: object, *, field_name: str, max_length: int) -> None:
    _bounded_scalar(value, field_name=field_name, max_length=max_length)
    if _REF_SEGMENT_PATTERN.fullmatch(str(value)) is None:
        raise ValueError(f"{field_name} must be a canonical opaque segment")


def _bounded_string_tuple(
    values: tuple[str, ...], *, field_name: str, max_items: int, max_length: int
) -> None:
    _require_tuple(values, field_name=field_name, max_items=max_items)
    if len(set(values)) != len(values):
        raise ValueError(f"{field_name} must contain unique values")
    for value in values:
        _bounded_ref_segment(value, field_name=field_name, max_length=max_length)


def _require_tuple(
    values: object,
    *,
    field_name: str,
    max_items: int | None = None,
) -> None:
    if not isinstance(values, tuple):
        raise TypeError(f"{field_name} must use an immutable tuple")
    if max_items is not None and len(values) > max_items:
        raise ValueError(f"{field_name} exceeds maximum item count")


def _require_enum(value: object, enum_type: type[Enum], *, field_name: str) -> None:
    if not isinstance(value, enum_type):
        raise TypeError(f"{field_name} must use {enum_type.__name__}")


def _validate_sha256(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")


def _validate_cursor(value: object) -> None:
    _bounded_scalar(value, field_name="cursor", max_length=320, required=False)
    if value and _CURSOR_PATTERN.fullmatch(str(value)) is None:
        raise ValueError("cursor must be an opaque canonical cursor")


def _bounded_limit(value: object, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")


def _bounded_count(value: object, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")


def _dataclass_scalars(value: object, *, skip: set[str]) -> dict[str, str]:
    return {
        name: str(getattr(value, name))
        for name in value.__dataclass_fields__  # type: ignore[attr-defined]
        if name not in skip
    }


ExternalKnowledgeSource = ExternalKnowledgeSourceId
