from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ._validation import require_exact_keys, validate_sha256
from .contracts import (
    InternalMemoryContentKind,
    InternalMemoryDomain,
    InternalMemoryEvidenceStrength,
    InternalMemoryFreshness,
    InternalMemoryProvenanceRef,
    InternalMemoryScope,
    InternalMemoryState,
)
from .records import InternalMemoryRecord, InternalMemoryRelations


@dataclass(frozen=True)
class StoredInternalMemoryRecord:
    logical_ref: str
    domain: InternalMemoryDomain
    content_kind: InternalMemoryContentKind
    summary: str
    scope: InternalMemoryScope
    evidence_strength: InternalMemoryEvidenceStrength
    provenance: tuple[InternalMemoryProvenanceRef, ...]
    freshness: InternalMemoryFreshness
    state: InternalMemoryState
    relations: InternalMemoryRelations
    body_sha256: str

    @classmethod
    def from_record(cls, record: InternalMemoryRecord) -> StoredInternalMemoryRecord:
        digest = hashlib.sha256(record.body.encode("utf-8")).hexdigest()
        return cls(
            logical_ref=record.logical_ref,
            domain=record.domain,
            content_kind=record.content_kind,
            summary=record.summary,
            scope=record.scope,
            evidence_strength=record.evidence_strength,
            provenance=record.provenance,
            freshness=record.freshness,
            state=record.state,
            relations=record.relations,
            body_sha256=digest,
        )

    def to_record(self, body: str) -> InternalMemoryRecord:
        if hashlib.sha256(body.encode("utf-8")).hexdigest() != self.body_sha256:
            raise ValueError("internal memory body digest mismatch")
        return InternalMemoryRecord(
            logical_ref=self.logical_ref,
            domain=self.domain,
            content_kind=self.content_kind,
            summary=self.summary,
            body=body,
            scope=self.scope,
            evidence_strength=self.evidence_strength,
            provenance=self.provenance,
            freshness=self.freshness,
            state=self.state,
            relations=self.relations,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "logical_ref": self.logical_ref,
            "domain": self.domain.value,
            "content_kind": self.content_kind.value,
            "summary": self.summary,
            "scope": self.scope.to_dict(),
            "evidence_strength": self.evidence_strength.value,
            "provenance": [item.to_dict() for item in self.provenance],
            "freshness": self.freshness.to_dict(),
            "state": self.state.value,
            "relations": self.relations.to_dict(),
            "body_sha256": self.body_sha256,
        }

    @classmethod
    def from_dict(cls, payload: object) -> StoredInternalMemoryRecord:
        data = require_exact_keys(
            payload,
            required=frozenset(
                {
                    "logical_ref",
                    "domain",
                    "content_kind",
                    "summary",
                    "scope",
                    "evidence_strength",
                    "provenance",
                    "freshness",
                    "state",
                    "relations",
                    "body_sha256",
                }
            ),
            label="stored internal memory record",
        )
        try:
            digest = validate_sha256(data["body_sha256"], field_name="body_sha256")
            record = cls(
                logical_ref=data["logical_ref"],
                domain=InternalMemoryDomain(data["domain"]),
                content_kind=InternalMemoryContentKind(data["content_kind"]),
                summary=data["summary"],
                scope=InternalMemoryScope.from_dict(data["scope"]),
                evidence_strength=InternalMemoryEvidenceStrength(data["evidence_strength"]),
                provenance=tuple(
                    InternalMemoryProvenanceRef.from_dict(item)
                    for item in data["provenance"]
                ),
                freshness=InternalMemoryFreshness.from_dict(data["freshness"]),
                state=InternalMemoryState(data["state"]),
                relations=InternalMemoryRelations.from_dict(data["relations"]),
                body_sha256=digest,
            )
            # Reuse public metadata invariants without reading the real body object.
            placeholder = "metadata-validation-placeholder"
            replace(
                record,
                body_sha256=hashlib.sha256(placeholder.encode("utf-8")).hexdigest(),
            ).to_record(placeholder)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid stored internal memory record") from exc
        return record


def canonical_index_bytes(records: tuple[StoredInternalMemoryRecord, ...]) -> bytes:
    payload = {
        "schema_version": 1,
        "records": [item.to_dict() for item in sorted(records, key=lambda item: item.logical_ref)],
    }
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def verify_stored_body(path: Path, expected_digest: str) -> None:
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected_digest:
        raise ValueError("internal memory body digest mismatch")
