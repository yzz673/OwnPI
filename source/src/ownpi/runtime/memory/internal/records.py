from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from ._validation import (
    bounded_text,
    require_enum,
    require_exact_keys,
    require_tuple,
    require_unique,
)
from .contracts import (
    COMMITTABLE_EVIDENCE,
    GLOBAL_CONTENT_KINDS,
    STRUCTURED_RESEARCH_CONTENT_KINDS,
    InternalMemoryConflictStatus,
    InternalMemoryContentKind,
    InternalMemoryDomain,
    InternalMemoryEvidenceStrength,
    InternalMemoryFreshness,
    InternalMemoryProvenanceRef,
    InternalMemoryScope,
    InternalMemoryScopeType,
    InternalMemoryState,
    parse_internal_ref,
)


@dataclass(frozen=True)
class InternalMemoryRelations:
    conflict_status: InternalMemoryConflictStatus = InternalMemoryConflictStatus.NONE
    conflicts_with: tuple[str, ...] = ()
    conflict_resolution: str = ""
    supersedes: tuple[str, ...] = ()
    superseded_by: str = ""

    def __post_init__(self) -> None:
        require_enum(
            self.conflict_status,
            InternalMemoryConflictStatus,
            field_name="conflict_status",
        )
        for field_name in ("conflicts_with", "supersedes"):
            values = require_tuple(getattr(self, field_name), field_name=field_name, max_items=16)
            require_unique(values, field_name=field_name)
            for ref in values:
                parse_internal_ref(ref)
        if self.superseded_by:
            parse_internal_ref(self.superseded_by)
        bounded_text(
            self.conflict_resolution,
            field_name="conflict_resolution",
            required=self.conflict_status == InternalMemoryConflictStatus.RESOLVED,
            reject_secrets=True,
        )
        if self.conflict_status == InternalMemoryConflictStatus.NONE and (
            self.conflicts_with or self.conflict_resolution
        ):
            raise ValueError("no-conflict relation cannot carry conflict details")
        if self.conflict_status != InternalMemoryConflictStatus.NONE and not self.conflicts_with:
            raise ValueError("conflict relation requires conflicting refs")
        if (
            self.conflict_status == InternalMemoryConflictStatus.NEEDS_VERIFICATION
            and self.conflict_resolution
        ):
            raise ValueError("unresolved conflict cannot carry a resolution")

    def to_dict(self) -> dict[str, object]:
        return {
            "conflict_status": self.conflict_status.value,
            "conflicts_with": list(self.conflicts_with),
            "conflict_resolution": self.conflict_resolution,
            "supersedes": list(self.supersedes),
            "superseded_by": self.superseded_by,
        }

    @classmethod
    def from_dict(cls, payload: object) -> InternalMemoryRelations:
        data = require_exact_keys(
            payload,
            required=frozenset(
                {
                    "conflict_status",
                    "conflicts_with",
                    "conflict_resolution",
                    "supersedes",
                    "superseded_by",
                }
            ),
            label="internal memory relations",
        )
        try:
            return cls(
                conflict_status=InternalMemoryConflictStatus(data["conflict_status"]),
                conflicts_with=tuple(data["conflicts_with"]),
                conflict_resolution=data["conflict_resolution"],
                supersedes=tuple(data["supersedes"]),
                superseded_by=data["superseded_by"],
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid serialized internal memory relations") from exc


_TRANSITIONS: Mapping[InternalMemoryState, frozenset[InternalMemoryState]] = {
    InternalMemoryState.CANDIDATE: frozenset(
        {InternalMemoryState.COMMITTED, InternalMemoryState.ARCHIVED}
    ),
    InternalMemoryState.COMMITTED: frozenset({InternalMemoryState.ARCHIVED}),
    InternalMemoryState.ARCHIVED: frozenset(),
}


def can_transition_internal_memory(
    *, current: InternalMemoryState, target: InternalMemoryState
) -> bool:
    require_enum(current, InternalMemoryState, field_name="current")
    require_enum(target, InternalMemoryState, field_name="target")
    return current == target or target in _TRANSITIONS[current]


def remove_internal_relation_ref(
    record: InternalMemoryRecord,
    logical_ref: str,
) -> InternalMemoryRecord:
    relations = record.relations
    conflicts = tuple(item for item in relations.conflicts_with if item != logical_ref)
    revised = replace(
        relations,
        conflict_status=(
            relations.conflict_status
            if conflicts
            else InternalMemoryConflictStatus.NONE
        ),
        conflicts_with=conflicts,
        conflict_resolution=relations.conflict_resolution if conflicts else "",
        supersedes=tuple(item for item in relations.supersedes if item != logical_ref),
        superseded_by=("" if relations.superseded_by == logical_ref else relations.superseded_by),
    )
    return replace(record, relations=revised)


@dataclass(frozen=True)
class InternalMemoryRecord:
    logical_ref: str
    domain: InternalMemoryDomain
    content_kind: InternalMemoryContentKind
    summary: str
    body: str
    scope: InternalMemoryScope
    evidence_strength: InternalMemoryEvidenceStrength
    provenance: tuple[InternalMemoryProvenanceRef, ...]
    freshness: InternalMemoryFreshness
    state: InternalMemoryState
    relations: InternalMemoryRelations = InternalMemoryRelations()

    def __post_init__(self) -> None:
        parsed = parse_internal_ref(self.logical_ref)
        require_enum(self.domain, InternalMemoryDomain, field_name="domain")
        require_enum(self.content_kind, InternalMemoryContentKind, field_name="content_kind")
        require_enum(
            self.evidence_strength,
            InternalMemoryEvidenceStrength,
            field_name="evidence_strength",
        )
        require_enum(self.state, InternalMemoryState, field_name="state")
        if parsed.domain != self.domain:
            raise ValueError("logical ref domain does not match record domain")
        allowed = (
            GLOBAL_CONTENT_KINDS
            if self.domain == InternalMemoryDomain.GLOBAL
            else STRUCTURED_RESEARCH_CONTENT_KINDS
        )
        if self.content_kind not in allowed:
            raise ValueError("content kind does not belong to the selected domain")
        if self.domain == InternalMemoryDomain.GLOBAL and (
            self.scope.scope_type != InternalMemoryScopeType.USER_GLOBAL
        ):
            raise ValueError("global records require user-global scope")
        bounded_text(
            self.summary,
            field_name="summary",
            reject_secrets=True,
        )
        bounded_text(
            self.body,
            field_name="body",
            reject_secrets=True,
        )
        provenance = require_tuple(self.provenance, field_name="provenance")
        if not provenance:
            raise ValueError("internal memory record requires provenance")
        require_unique(provenance, field_name="provenance")
        if self.state == InternalMemoryState.COMMITTED:
            if self.evidence_strength not in COMMITTABLE_EVIDENCE:
                raise ValueError("committed record requires promotable evidence")
        if self.relations.supersedes and self.state == InternalMemoryState.CANDIDATE:
            raise ValueError("candidate records cannot claim supersession lineage")
        if self.relations.superseded_by and self.state != InternalMemoryState.ARCHIVED:
            raise ValueError("superseded records must be archived")
        related_refs = (
            self.relations.conflicts_with
            + self.relations.supersedes
            + ((self.relations.superseded_by,) if self.relations.superseded_by else ())
        )
        if self.logical_ref in related_refs:
            raise ValueError("record cannot conflict with or supersede itself")

    @property
    def is_consumable(self) -> bool:
        return self.state == InternalMemoryState.COMMITTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "logical_ref": self.logical_ref,
            "domain": self.domain.value,
            "content_kind": self.content_kind.value,
            "summary": self.summary,
            "body": self.body,
            "scope": self.scope.to_dict(),
            "evidence_strength": self.evidence_strength.value,
            "provenance": [item.to_dict() for item in self.provenance],
            "freshness": self.freshness.to_dict(),
            "state": self.state.value,
            "relations": self.relations.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: object) -> InternalMemoryRecord:
        data = require_exact_keys(
            payload,
            required=frozenset(
                {
                    "logical_ref",
                    "domain",
                    "content_kind",
                    "summary",
                    "body",
                    "scope",
                    "evidence_strength",
                    "provenance",
                    "freshness",
                    "state",
                    "relations",
                }
            ),
            label="internal memory record",
        )
        try:
            return cls(
                logical_ref=data["logical_ref"],
                domain=InternalMemoryDomain(data["domain"]),
                content_kind=InternalMemoryContentKind(data["content_kind"]),
                summary=data["summary"],
                body=data["body"],
                scope=InternalMemoryScope.from_dict(data["scope"]),
                evidence_strength=InternalMemoryEvidenceStrength(data["evidence_strength"]),
                provenance=tuple(
                    InternalMemoryProvenanceRef.from_dict(item)
                    for item in data["provenance"]
                ),
                freshness=InternalMemoryFreshness.from_dict(data["freshness"]),
                state=InternalMemoryState(data["state"]),
                relations=InternalMemoryRelations.from_dict(data["relations"]),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid serialized internal memory record") from exc
