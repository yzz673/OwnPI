from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from ownpi.runtime.memory.external.contracts import (
    ExternalKnowledgeContentKind,
    ExternalKnowledgeRelationItem,
    ExternalKnowledgeRelationPage,
    encode_logical_ref,
)
from ownpi.runtime.memory.external.research_index import (
    SOURCE_ID,
    ResearchPackageIndex,
    ResearchStrategyAdapterError,
    bounded,
    required_int,
    required_list,
    required_string,
)
from ownpi.runtime.limits.projection import (
    MAX_EXTERNAL_KNOWLEDGE_RELATION_PAGE_BYTES,
    MAX_EXTERNAL_KNOWLEDGE_RELATION_PAGE_ITEMS,
    MAX_EXTERNAL_KNOWLEDGE_RESULT_SUMMARY_CHARS,
)


class CursorError(ValueError):
    pass


@dataclass(frozen=True)
class RelationRecord:
    logical_ref: str
    relation_type: str
    manifest_locator: str
    cell_id: str
    strategy_ref: str = ""


class ResearchPaginator:
    """Manifest-order traversal and opaque cursors for Research relations."""

    def __init__(self, index: ResearchPackageIndex) -> None:
        self._index = index
        self._pin_binding = hashlib.sha256(_canonical(index.pin.to_dict())).hexdigest()[
            :20
        ]

    def paginate(
        self, relation: RelationRecord, cursor: str, limit: int
    ) -> ExternalKnowledgeRelationPage:
        manifest = self._index.component(relation.manifest_locator)
        digest, pages, item_count = self._validate_manifest(manifest, relation)
        row = self.decode_cursor(cursor, relation.logical_ref, digest) if cursor else 0
        if row > item_count:
            raise CursorError
        items: list[ExternalKnowledgeRelationItem] = []
        for page in pages:
            start, end = _row_range(page)
            if row >= end or len(items) >= limit:
                continue
            rows = self._load_page(page, manifest, relation)
            for item in rows[max(row, start) - start :]:
                if len(items) >= limit:
                    break
                items.append(self._relation_item(item))
                row += 1
        next_cursor = (
            self.encode_cursor(relation.logical_ref, digest, row)
            if row < item_count
            else ""
        )
        return ExternalKnowledgeRelationPage(
            relation.logical_ref,
            tuple(items),
            next_cursor,
            item_count,
            bool(next_cursor),
        )

    def encode_cursor(self, relation_ref: str, digest: str, next_row: int) -> str:
        core = {
            "n": next_row,
            "p": self._pin_binding,
            "r": hashlib.sha256(relation_ref.encode()).hexdigest()[:20],
            "s": digest,
        }
        checksum = hashlib.sha256(
            self._index.pin.release_content_digest.encode() + _canonical(core)
        ).hexdigest()[:16]
        encoded = (
            base64.urlsafe_b64encode(_canonical({**core, "c": checksum}))
            .decode()
            .rstrip("=")
        )
        return f"nkfc:{encoded}"

    def decode_cursor(self, cursor: str, relation_ref: str, digest: str) -> int:
        try:
            encoded = cursor.removeprefix("nkfc:")
            payload = json.loads(
                base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            )
            core = {key: payload[key] for key in ("n", "p", "r", "s")}
            checksum = hashlib.sha256(
                self._index.pin.release_content_digest.encode() + _canonical(core)
            ).hexdigest()[:16]
            expected = {
                "p": self._pin_binding,
                "r": hashlib.sha256(relation_ref.encode()).hexdigest()[:20],
                "s": digest,
                "c": checksum,
            }
            row = payload.get("n")
            if any(payload.get(key) != value for key, value in expected.items()):
                raise CursorError
            if isinstance(row, bool) or not isinstance(row, int) or row < 0:
                raise CursorError
            if self.encode_cursor(relation_ref, digest, row) != cursor:
                raise CursorError
            return row
        except (KeyError, TypeError, ValueError, UnicodeError) as exc:
            raise CursorError from exc

    def _validate_manifest(
        self, manifest: Mapping[str, Any], relation: RelationRecord
    ) -> tuple[str, tuple[Mapping[str, Any], ...], int]:
        if (
            manifest.get("relation_type") != relation.relation_type
            or manifest.get("cell_id") != relation.cell_id
            or (
                relation.strategy_ref
                and manifest.get("strategy_ref") != relation.strategy_ref
            )
        ):
            raise ResearchStrategyAdapterError("relation manifest identity mismatch")
        if manifest.get("page_policy") != {
            "max_items_per_page": MAX_EXTERNAL_KNOWLEDGE_RELATION_PAGE_ITEMS,
            "max_serialized_bytes_per_page": (
                MAX_EXTERNAL_KNOWLEDGE_RELATION_PAGE_BYTES
            ),
        }:
            raise ResearchStrategyAdapterError("relation page policy is invalid")
        pages: list[Mapping[str, Any]] = []
        expected_row = 0
        total_bytes = 0
        for number, page in enumerate(required_list(manifest, "pages"), start=1):
            if not isinstance(page, dict) or page.get("page_number") != number:
                raise ResearchStrategyAdapterError("relation pages are not 1-based")
            start, end = _row_range(page)
            count = required_int(page, "item_count")
            size = required_int(page, "size_bytes")
            if (
                start != expected_row
                or end - start != count
                or not 1 <= count <= MAX_EXTERNAL_KNOWLEDGE_RELATION_PAGE_ITEMS
                or not 1 <= size <= MAX_EXTERNAL_KNOWLEDGE_RELATION_PAGE_BYTES
            ):
                raise ResearchStrategyAdapterError(
                    "relation row ranges are not contiguous"
                )
            expected_row, total_bytes = end, total_bytes + size
            pages.append(page)
        if (
            required_int(manifest, "page_count") != len(pages)
            or required_int(manifest, "item_count") != expected_row
            or required_int(manifest, "serialized_total_bytes") != total_bytes
        ):
            raise ResearchStrategyAdapterError("relation manifest totals are invalid")
        return required_string(manifest, "semantic_digest"), tuple(pages), expected_row

    def _load_page(
        self,
        page: Mapping[str, Any],
        manifest: Mapping[str, Any],
        relation: RelationRecord,
    ) -> tuple[Mapping[str, Any], ...]:
        path = self._index.package.resolve_file(required_string(page, "locator"))
        declared_size = required_int(page, "size_bytes")
        if path.stat().st_size != declared_size:
            raise ResearchStrategyAdapterError("relation page byte count mismatch")
        raw = path.read_bytes()
        if len(raw) != declared_size:
            raise ResearchStrategyAdapterError("relation page byte count mismatch")
        if hashlib.sha256(raw).hexdigest() != required_string(page, "sha256"):
            raise ResearchStrategyAdapterError("relation page digest mismatch")
        try:
            rows = tuple(json.loads(line) for line in raw.decode().splitlines())
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ResearchStrategyAdapterError("relation page is unreadable") from exc
        if len(rows) != required_int(page, "item_count") or any(
            not isinstance(row, dict) for row in rows
        ):
            raise ResearchStrategyAdapterError("relation page item count mismatch")
        schema_id = required_string(manifest, "item_schema_id")
        for row in rows:
            self._index.registry.validate_schema_id(row, schema_id=schema_id)
            if row.get("relation_type") != relation.relation_type:
                raise ResearchStrategyAdapterError("relation row identity mismatch")
            if row.get("cell_id", relation.cell_id) != relation.cell_id:
                raise ResearchStrategyAdapterError("relation row owner mismatch")
            if (
                relation.strategy_ref
                and row.get("strategy_ref") != relation.strategy_ref
            ):
                raise ResearchStrategyAdapterError("relation row strategy mismatch")
            paper = self._index.papers_by_support.get(str(row.get("support_id") or ""))
            if paper is None or row.get("paper_support_path") != paper.locator:
                raise ResearchStrategyAdapterError(
                    "relation paper lookup is undeclared"
                )
        return rows

    def _relation_item(self, row: Mapping[str, Any]) -> ExternalKnowledgeRelationItem:
        paper = self._index.papers_by_support[required_string(row, "support_id")]
        title = str(
            paper.item.get("title") or paper.item.get("paper_id") or "Paper support"
        )
        summary = (
            " ".join(
                str(value)
                for value in (paper.item.get("journal"), paper.item.get("year"))
                if value
            )
            or "Declared paper support."
        )
        logical_ref = encode_logical_ref(
            release_id=self._index.pin.release_id,
            source_id=SOURCE_ID,
            content_kind=ExternalKnowledgeContentKind.RESEARCH_PAPER_SUPPORT,
            stable_id=paper.stable_id,
        )
        return ExternalKnowledgeRelationItem(
            logical_ref,
            bounded(title, 300),
            bounded(summary, MAX_EXTERNAL_KNOWLEDGE_RESULT_SUMMARY_CHARS),
        )


def _row_range(page: Mapping[str, Any]) -> tuple[int, int]:
    value = page.get("row_range")
    if not isinstance(value, dict):
        raise ResearchStrategyAdapterError("relation row range is missing")
    return required_int(value, "start"), required_int(value, "end")


def _canonical(payload: object) -> bytes:
    return json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode()
