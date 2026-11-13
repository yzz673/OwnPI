from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from ownpi.runtime.memory.external.contracts import parse_logical_ref as parse_external_ref

from ._validation import (
    bounded_ref_segment,
    bounded_text,
    parse_utc_timestamp,
    require_enum,
    require_exact_keys,
    validate_sha256,
)


MAX_INTERNAL_REF_LENGTH = 240


class InternalMemoryDomain(str, Enum):
    GLOBAL = "global"
    STRUCTURED_RESEARCH = "structured_research"


class InternalMemoryContentKind(str, Enum):
    USER_PREFERENCE = "user_preference"
    ENVIRONMENT_FACT = "environment_fact"
    REPOSITORY_FACT = "repository_fact"
    VALIDATED_WORKFLOW = "validated_workflow"
    FAILURE_SHIELD = "failure_shield"
    IMPLEMENTED_DECISION = "implemented_decision"


GLOBAL_CONTENT_KINDS = frozenset(
    {
        InternalMemoryContentKind.USER_PREFERENCE,
        InternalMemoryContentKind.ENVIRONMENT_FACT,
    }
)
STRUCTURED_RESEARCH_CONTENT_KINDS = frozenset(
    {
        InternalMemoryContentKind.REPOSITORY_FACT,
        InternalMemoryContentKind.VALIDATED_WORKFLOW,
        InternalMemoryContentKind.FAILURE_SHIELD,
        InternalMemoryContentKind.IMPLEMENTED_DECISION,
    }
)


class InternalMemoryScopeType(str, Enum):
    USER_GLOBAL = "user_global"
    PROJECT = "project"
    REPOSITORY = "repository"
    WORKFLOW = "workflow"


class InternalMemoryEvidenceStrength(str, Enum):
    EXPLICIT = "explicit"
    VERIFIED = "verified"
    REPEATED = "repeated"
    INFERRED = "inferred"
    UNVERIFIED = "unverified"


COMMITTABLE_EVIDENCE = frozenset(
    {
        InternalMemoryEvidenceStrength.EXPLICIT,
        InternalMemoryEvidenceStrength.VERIFIED,
        InternalMemoryEvidenceStrength.REPEATED,
    }
)


class InternalMemoryState(str, Enum):
    CANDIDATE = "candidate"
    COMMITTED = "committed"
    ARCHIVED = "archived"


class InternalMemoryConflictStatus(str, Enum):
    NONE = "none"
    NEEDS_VERIFICATION = "needs_verification"
    RESOLVED = "resolved"


class InternalMemoryProvenanceKind(str, Enum):
    USER_STATEMENT = "user_statement"
    RUN_THREAD = "run_thread"
    ARTIFACT = "artifact"
    REPOSITORY_REVISION = "repository_revision"
    VERIFICATION_RECEIPT = "verification_receipt"
    EXTERNAL_LOGICAL_REF = "external_logical_ref"


@dataclass(frozen=True)
class InternalMemoryLogicalRef:
    domain: InternalMemoryDomain
    stable_id: str

    def __post_init__(self) -> None:
        require_enum(self.domain, InternalMemoryDomain, field_name="domain")
        bounded_ref_segment(self.stable_id, field_name="stable_id", max_length=160)
        if len(self.value) > MAX_INTERNAL_REF_LENGTH:
            raise ValueError("internal logical ref exceeds maximum length")

    @property
    def value(self) -> str:
        return f"nim:{self.domain.value}:{self.stable_id}"

    @classmethod
    def parse(cls, value: str) -> InternalMemoryLogicalRef:
        bounded_text(value, field_name="logical_ref", max_length=MAX_INTERNAL_REF_LENGTH)
        parts = value.split(":")
        if len(parts) != 3 or parts[0] != "nim":
            raise ValueError("logical ref must use the canonical nim format")
        try:
            ref = cls(InternalMemoryDomain(parts[1]), parts[2])
        except ValueError as exc:
            raise ValueError("invalid internal logical ref") from exc
        if ref.value != value:
            raise ValueError("internal logical ref is not canonical")
        return ref


def encode_internal_ref(*, domain: InternalMemoryDomain, stable_id: str) -> str:
    return InternalMemoryLogicalRef(domain=domain, stable_id=stable_id).value


def parse_internal_ref(value: str) -> InternalMemoryLogicalRef:
    return InternalMemoryLogicalRef.parse(value)


@dataclass(frozen=True)
class InternalMemoryScope:
    scope_type: InternalMemoryScopeType
    scope_id: str
    reuse_boundary: str

    def __post_init__(self) -> None:
        require_enum(self.scope_type, InternalMemoryScopeType, field_name="scope_type")
        bounded_text(
            self.scope_id,
            field_name="scope_id",
            max_length=240,
            required=self.scope_type != InternalMemoryScopeType.USER_GLOBAL,
        )
        if self.scope_type == InternalMemoryScopeType.USER_GLOBAL and self.scope_id:
            raise ValueError("user-global scope cannot carry a scope id")
        bounded_text(
            self.reuse_boundary,
            field_name="reuse_boundary",
            reject_secrets=True,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "scope_type": self.scope_type.value,
            "scope_id": self.scope_id,
            "reuse_boundary": self.reuse_boundary,
        }

    @classmethod
    def from_dict(cls, payload: object) -> InternalMemoryScope:
        data = require_exact_keys(
            payload,
            required=frozenset({"scope_type", "scope_id", "reuse_boundary"}),
            label="internal memory scope",
        )
        try:
            return cls(
                scope_type=InternalMemoryScopeType(data["scope_type"]),
                scope_id=data["scope_id"],
                reuse_boundary=data["reuse_boundary"],
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid serialized internal memory scope") from exc


@dataclass(frozen=True)
class InternalMemoryProvenanceRef:
    kind: InternalMemoryProvenanceKind
    ref: str
    observed_at: str
    evidence_digest: str = ""

    def __post_init__(self) -> None:
        require_enum(self.kind, InternalMemoryProvenanceKind, field_name="kind")
        bounded_text(self.ref, field_name="ref", max_length=640, reject_secrets=True)
        parse_utc_timestamp(self.observed_at, field_name="observed_at")
        if self.evidence_digest:
            validate_sha256(self.evidence_digest, field_name="evidence_digest")
        if self.kind == InternalMemoryProvenanceKind.EXTERNAL_LOGICAL_REF:
            parse_external_ref(self.ref)

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind.value,
            "ref": self.ref,
            "observed_at": self.observed_at,
            "evidence_digest": self.evidence_digest,
        }

    @classmethod
    def from_dict(cls, payload: object) -> InternalMemoryProvenanceRef:
        data = require_exact_keys(
            payload,
            required=frozenset({"kind", "ref", "observed_at", "evidence_digest"}),
            label="internal memory provenance ref",
        )
        try:
            return cls(
                kind=InternalMemoryProvenanceKind(data["kind"]),
                ref=data["ref"],
                observed_at=data["observed_at"],
                evidence_digest=data["evidence_digest"],
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid serialized internal memory provenance ref") from exc


@dataclass(frozen=True)
class InternalMemoryFreshness:
    created_at: str
    updated_at: str
    verified_at: str = ""
    drift_prone: bool = False
    verification_guidance: str = ""

    def __post_init__(self) -> None:
        created = parse_utc_timestamp(self.created_at, field_name="created_at")
        updated = parse_utc_timestamp(self.updated_at, field_name="updated_at")
        verified = (
            parse_utc_timestamp(self.verified_at, field_name="verified_at")
            if self.verified_at
            else None
        )
        if updated < created or (verified is not None and verified < created):
            raise ValueError("freshness timestamps are not monotonic")
        if not isinstance(self.drift_prone, bool):
            raise TypeError("drift_prone must be a boolean")
        bounded_text(
            self.verification_guidance,
            field_name="verification_guidance",
            required=self.drift_prone,
            reject_secrets=True,
        )
        if not self.drift_prone and self.verification_guidance:
            raise ValueError("verification guidance requires drift-prone content")

    def to_dict(self) -> dict[str, object]:
        return {
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "verified_at": self.verified_at,
            "drift_prone": self.drift_prone,
            "verification_guidance": self.verification_guidance,
        }

    @classmethod
    def from_dict(cls, payload: object) -> InternalMemoryFreshness:
        data = require_exact_keys(
            payload,
            required=frozenset(
                {
                    "created_at",
                    "updated_at",
                    "verified_at",
                    "drift_prone",
                    "verification_guidance",
                }
            ),
            label="internal memory freshness",
        )
        try:
            return cls(**data)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid serialized internal memory freshness") from exc
