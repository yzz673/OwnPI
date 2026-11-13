from __future__ import annotations

import json
from pathlib import Path

from ownpi.runtime.memory.external.contracts import (
    ExternalKnowledgeContentKind,
    ExternalKnowledgeLogicalRef,
    ExternalKnowledgeProvenance,
    ExternalKnowledgeReadRequest,
    ExternalKnowledgeReadResult,
    ExternalKnowledgeRelationItem,
    ExternalKnowledgeRelationPage,
    ExternalKnowledgeReleasePin,
    ExternalKnowledgeResultStatus,
    ExternalKnowledgeSearchRequest,
    ExternalKnowledgeSearchResult,
    derive_relation_stable_id,
    encode_logical_ref,
    parse_logical_ref,
)
from ownpi.runtime.memory.external.research_index import (
    SOURCE_ID,
    DimensionRecord,
    ResearchPackageIndex,
    ResearchStrategyAdapterError,
    StrategyRecord,
    bounded,
    bounded_body,
    required_string,
)
from ownpi.runtime.memory.external.research_pagination import (
    CursorError,
    RelationRecord,
    ResearchPaginator,
)
from ownpi.runtime.memory.external.research_search import search_research
from ownpi.runtime.limits.projection import (
    MAX_EXTERNAL_KNOWLEDGE_RESULT_BODY_CHARS,
    MAX_EXTERNAL_KNOWLEDGE_RESULT_SUMMARY_CHARS,
)


class ResearchStrategyAdapter:
    """Deterministic search and progressive read over a pinned Research package."""

    def __init__(self, delivery_root: Path, pin: ExternalKnowledgeReleasePin) -> None:
        self._index = ResearchPackageIndex(delivery_root, pin)
        self._relations = self._index_relations()
        self._paginator = ResearchPaginator(self._index)

    def search(
        self, request: ExternalKnowledgeSearchRequest
    ) -> ExternalKnowledgeSearchResult:
        return search_research(self._index, request)

    def read(
        self, request: ExternalKnowledgeReadRequest
    ) -> ExternalKnowledgeReadResult:
        ref = parse_logical_ref(request.logical_ref)
        if ref.release_id != self._index.pin.release_id or ref.source_id != SOURCE_ID:
            return self._invalid(ref.value, "ref_not_owned")
        try:
            if request.relation:
                return self._read_named_relation(ref, request)
            relation_kinds = {
                ExternalKnowledgeContentKind.CONTRIBUTED_TO_STRATEGY_SYNTHESIS,
                ExternalKnowledgeContentKind.SUPPORTS_DIMENSION,
            }
            if ref.content_kind in relation_kinds:
                relation = self._relations.get(ref.value)
                if relation is None:
                    return self._invalid(ref.value, "unknown_relation")
                return self._read_relation(relation, request.cursor, request.limit)
            if request.cursor:
                return self._invalid(ref.value, "cursor_requires_relation")
            if ref.content_kind == ExternalKnowledgeContentKind.RESEARCH_DIMENSION:
                return self._read_dimension(ref)
            if ref.content_kind == ExternalKnowledgeContentKind.RESEARCH_STRATEGY:
                return self._read_strategy(ref)
            if ref.content_kind == ExternalKnowledgeContentKind.RESEARCH_PAPER_SUPPORT:
                return self._read_paper(ref)
            return self._invalid(ref.value, "unsupported_kind")
        except CursorError:
            return self._invalid(ref.value, "invalid_or_stale_cursor")

    def _index_relations(self) -> dict[str, RelationRecord]:
        relations: dict[str, RelationRecord] = {}
        for dimension in self._index.dimensions.values():
            ref = self._relation_ref(
                ExternalKnowledgeContentKind.SUPPORTS_DIMENSION,
                (
                    ("archetype", dimension.archetype),
                    ("dimension", dimension.dimension),
                ),
            )
            relations[ref] = RelationRecord(
                ref,
                "supports_dimension",
                dimension.supports_locator,
                dimension.cell_id,
            )
            for strategy in dimension.strategies:
                strategy_id = required_string(strategy.item, "strategy_id")
                ref = self._relation_ref(
                    ExternalKnowledgeContentKind.CONTRIBUTED_TO_STRATEGY_SYNTHESIS,
                    (
                        ("archetype", strategy.archetype),
                        ("dimension", strategy.dimension),
                        ("strategy", strategy_id),
                    ),
                )
                relations[ref] = RelationRecord(
                    ref,
                    "contributed_to_strategy_synthesis",
                    strategy.relation_locator,
                    strategy.cell_id,
                    required_string(strategy.item, "strategy_ref"),
                )
        return relations

    def _read_named_relation(
        self, ref: ExternalKnowledgeLogicalRef, request: ExternalKnowledgeReadRequest
    ) -> ExternalKnowledgeReadResult:
        if (
            ref.content_kind == ExternalKnowledgeContentKind.RESEARCH_DIMENSION
            and request.relation == "strategies"
        ):
            return self._read_strategy_index(ref, request.cursor, request.limit)
        expected = {
            "contributed_to_strategy_synthesis": (
                ExternalKnowledgeContentKind.RESEARCH_STRATEGY
            ),
            "supports_dimension": ExternalKnowledgeContentKind.RESEARCH_DIMENSION,
        }.get(request.relation)
        if expected != ref.content_kind:
            return self._invalid(ref.value, "relation_not_supported")
        owner = (
            self._index.strategies.get(ref.stable_id)
            if expected == ExternalKnowledgeContentKind.RESEARCH_STRATEGY
            else self._index.dimensions.get(ref.stable_id)
        )
        relation = self._owner_relation(owner)
        if relation is None:
            return self._invalid(ref.value, "unknown_owner")
        return self._read_relation(relation, request.cursor, request.limit)

    def _read_dimension(
        self, ref: ExternalKnowledgeLogicalRef
    ) -> ExternalKnowledgeReadResult:
        dimension = self._index.dimensions.get(ref.stable_id)
        if dimension is None:
            return self._invalid(ref.value, "unknown_dimension")
        card = self._index.component(dimension.card_locator)
        related = tuple(
            self._ref(ExternalKnowledgeContentKind.RESEARCH_STRATEGY, item.stable_id)
            for item in dimension.strategies[:49]
        )
        relation = self._owner_relation(dimension)
        if relation is not None:
            related += (relation.logical_ref,)
        return self._object_result(
            ref.value,
            required_string(card, "title"),
            bounded_body(
                card,
                (
                    "summary",
                    "dimension_meaning",
                    "question_answered",
                    "usage_intents",
                    "strategy_count",
                    "top_strategy_names",
                ),
            ),
            (dimension.archetype, dimension.dimension),
            related,
        )

    def _read_strategy(
        self, ref: ExternalKnowledgeLogicalRef
    ) -> ExternalKnowledgeReadResult:
        strategy = self._index.strategies.get(ref.stable_id)
        if strategy is None:
            return self._invalid(ref.value, "unknown_strategy")
        detail = self._index.component(strategy.detail_locator)
        strategy_id = required_string(strategy.item, "strategy_id")
        if (
            detail.get("cell_id") != strategy.cell_id
            or detail.get("strategy_id") != strategy_id
        ):
            raise ResearchStrategyAdapterError("strategy detail identity mismatch")
        relation = self._owner_relation(strategy)
        return self._object_result(
            ref.value,
            required_string(detail, "strategy_name"),
            bounded_body(
                detail,
                (
                    "one_sentence_use",
                    "core_principle",
                    "when_to_use",
                    "how_to_execute",
                    "agent_decision_checklist",
                    "failure_modes",
                ),
            ),
            (strategy.archetype, strategy.dimension, strategy_id),
            (relation.logical_ref,) if relation else (),
        )

    def _read_paper(
        self, ref: ExternalKnowledgeLogicalRef
    ) -> ExternalKnowledgeReadResult:
        paper = self._index.papers_by_stable.get(ref.stable_id)
        if paper is None:
            return self._invalid(ref.value, "unknown_paper_support")
        payload = self._index.component(paper.locator)
        if payload.get("support_id") != paper.support_id:
            raise ResearchStrategyAdapterError("paper support identity mismatch")
        omitted = {
            "package_paths",
            "source_artifacts",
            "source_signature",
            "internal_provenance",
        }
        visible = {key: value for key, value in payload.items() if key not in omitted}
        title = str(payload.get("title") or paper.item.get("title") or "Paper support")
        return self._object_result(
            ref.value,
            title,
            json.dumps(visible, ensure_ascii=False, sort_keys=True),
            (paper.stable_id,),
        )

    def _read_strategy_index(
        self, ref: ExternalKnowledgeLogicalRef, cursor: str, limit: int
    ) -> ExternalKnowledgeReadResult:
        dimension = self._index.dimensions.get(ref.stable_id)
        if dimension is None:
            return self._invalid(ref.value, "unknown_dimension")
        start = (
            self._paginator.decode_cursor(cursor, ref.value, dimension.strategy_digest)
            if cursor
            else 0
        )
        if start > len(dimension.strategies):
            raise CursorError
        selected = dimension.strategies[start : start + limit]
        items = tuple(
            ExternalKnowledgeRelationItem(
                self._ref(
                    ExternalKnowledgeContentKind.RESEARCH_STRATEGY, item.stable_id
                ),
                bounded(required_string(item.item, "strategy_name"), 300),
                bounded(
                    required_string(item.item, "one_sentence_use"),
                    MAX_EXTERNAL_KNOWLEDGE_RESULT_SUMMARY_CHARS,
                ),
            )
            for item in selected
        )
        next_row = start + len(selected)
        next_cursor = (
            self._paginator.encode_cursor(
                ref.value, dimension.strategy_digest, next_row
            )
            if next_row < len(dimension.strategies)
            else ""
        )
        page = ExternalKnowledgeRelationPage(
            ref.value, items, next_cursor, len(dimension.strategies), bool(next_cursor)
        )
        return ExternalKnowledgeReadResult(
            ExternalKnowledgeResultStatus.SUCCESS,
            ref.value,
            "Strategy index",
            f"Strategies declared for {dimension.cell_id}.",
            self._provenance(ref.value, (dimension.archetype, dimension.dimension)),
            relation_page=page,
            truncated=bool(next_cursor),
        )

    def _read_relation(
        self, relation: RelationRecord, cursor: str, limit: int
    ) -> ExternalKnowledgeReadResult:
        page = self._paginator.paginate(relation, cursor, limit)
        lineage = tuple(
            part
            for part in (
                relation.cell_id,
                relation.strategy_ref.rsplit(":", 1)[-1]
                if relation.strategy_ref
                else "",
            )
            if part
        )
        return ExternalKnowledgeReadResult(
            ExternalKnowledgeResultStatus.SUCCESS,
            relation.logical_ref,
            relation.relation_type,
            f"{relation.relation_type} relation page.",
            self._provenance(relation.logical_ref, lineage),
            relation_page=page,
            truncated=bool(page.next_cursor),
        )

    def _owner_relation(
        self, owner: DimensionRecord | StrategyRecord | None
    ) -> RelationRecord | None:
        if owner is None:
            return None
        strategy_ref = (
            required_string(owner.item, "strategy_ref")
            if isinstance(owner, StrategyRecord)
            else ""
        )
        relation_type = (
            "contributed_to_strategy_synthesis"
            if strategy_ref
            else "supports_dimension"
        )
        return next(
            (
                item
                for item in self._relations.values()
                if item.relation_type == relation_type
                and item.cell_id == owner.cell_id
                and item.strategy_ref == strategy_ref
            ),
            None,
        )

    def _relation_ref(
        self,
        kind: ExternalKnowledgeContentKind,
        endpoints: tuple[tuple[str, str], ...],
    ) -> str:
        stable_id = derive_relation_stable_id(
            source_id=SOURCE_ID, content_kind=kind, endpoints=endpoints
        )
        return self._ref(kind, stable_id)

    def _ref(self, kind: ExternalKnowledgeContentKind, stable_id: str) -> str:
        return encode_logical_ref(
            release_id=self._index.pin.release_id,
            source_id=SOURCE_ID,
            content_kind=kind,
            stable_id=stable_id,
        )

    def _provenance(
        self, logical_ref: str, lineage: tuple[str, ...]
    ) -> ExternalKnowledgeProvenance:
        source = self._index.source
        return ExternalKnowledgeProvenance(
            self._index.pin.release_id,
            SOURCE_ID,
            source.package_id,
            source.package_version,
            logical_ref,
            lineage,
        )

    def _object_result(
        self,
        logical_ref: str,
        title: str,
        body: str,
        lineage: tuple[str, ...],
        related: tuple[str, ...] = (),
    ) -> ExternalKnowledgeReadResult:
        return ExternalKnowledgeReadResult(
            ExternalKnowledgeResultStatus.SUCCESS,
            logical_ref,
            bounded(title, 300),
            bounded(body, MAX_EXTERNAL_KNOWLEDGE_RESULT_BODY_CHARS),
            self._provenance(logical_ref, lineage),
            related,
            truncated=len(body) > MAX_EXTERNAL_KNOWLEDGE_RESULT_BODY_CHARS,
        )

    @staticmethod
    def _invalid(logical_ref: str, fallback: str) -> ExternalKnowledgeReadResult:
        return ExternalKnowledgeReadResult(
            ExternalKnowledgeResultStatus.INVALID_REQUEST,
            logical_ref,
            fallback=fallback,
        )


__all__ = [
    "ResearchStrategyAdapter",
    "ResearchStrategyAdapterError",
]
