from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ownpi.runtime.memory.external.contracts import (
    ExternalKnowledgeContentKind,
    ExternalKnowledgeProvenance,
    ExternalKnowledgeReadRequest,
    ExternalKnowledgeReadResult,
    ExternalKnowledgeRelationItem,
    ExternalKnowledgeRelationPage,
    ExternalKnowledgeReleasePin,
    ExternalKnowledgeResultStatus,
    ExternalKnowledgeSearchRequest,
    ExternalKnowledgeSearchResult,
    ExternalKnowledgeSourceId,
    encode_logical_ref,
    parse_logical_ref,
)
from ownpi.runtime.memory.external.method_index import (
    MethodPackageIndex,
    required,
)
from ownpi.runtime.memory.external.method_render import render_payload
from ownpi.runtime.memory.external.method_relations import (
    read_pattern_case_slice,
    relation_id,
)
from ownpi.runtime.memory.external.method_search import (
    MethodSearchEngine,
    MethodVectorQuery,
)
from ownpi.runtime.limits.projection import (
    MAX_EXTERNAL_KNOWLEDGE_RESULT_SUMMARY_CHARS,
)


METHOD_SOURCE = ExternalKnowledgeSourceId.METHOD_KNOWLEDGE
PATTERNS_RELATION = "patterns"
PATTERN_CASE_RELATION = "pattern_case_relevance"


class MethodKnowledgeAdapter:
    """Pinned, bounded consumer for the Method Knowledge package."""

    def __init__(
        self,
        *,
        delivery_root: Path,
        pin: ExternalKnowledgeReleasePin,
        vector_query: MethodVectorQuery | None = None,
    ) -> None:
        self._pin = pin
        self._index: MethodPackageIndex | None = None
        self._search: MethodSearchEngine | None = None
        try:
            self._index = MethodPackageIndex(delivery_root=delivery_root, pin=pin)
            self._search = MethodSearchEngine(
                index=self._index,
                pin=pin,
                vector_query=vector_query,
            )
        except (OSError, UnicodeError, ValueError):
            pass

    @property
    def available(self) -> bool:
        return self._index is not None

    def search(
        self, request: ExternalKnowledgeSearchRequest
    ) -> ExternalKnowledgeSearchResult:
        if self._search is None:
            return ExternalKnowledgeSearchResult(
                status=ExternalKnowledgeResultStatus.UNAVAILABLE,
                hits=(),
                matched_count=0,
            )
        return self._search.search(request)

    def read(
        self, request: ExternalKnowledgeReadRequest
    ) -> ExternalKnowledgeReadResult:
        parsed = parse_logical_ref(request.logical_ref)
        if not self.available:
            return _empty(
                request.logical_ref, ExternalKnowledgeResultStatus.UNAVAILABLE
            )
        if (
            parsed.release_id != self._pin.release_id
            or parsed.source_id != METHOD_SOURCE
        ):
            return _empty(
                request.logical_ref,
                ExternalKnowledgeResultStatus.INVALID_REQUEST,
            )
        try:
            if parsed.content_kind == ExternalKnowledgeContentKind.METHOD_SUBTARGET:
                return self._read_subtarget(request, parsed.stable_id)
            if parsed.content_kind == ExternalKnowledgeContentKind.METHOD_KNOWLEDGE:
                return self._read_knowledge(request, parsed.stable_id)
            if parsed.content_kind == ExternalKnowledgeContentKind.METHOD_PATTERN:
                return self._read_pattern(request, parsed.stable_id)
            if (
                parsed.content_kind
                == ExternalKnowledgeContentKind.PATTERN_CASE_RELEVANCE
            ):
                return self._read_relation(request, None)
            if (
                parsed.content_kind
                == ExternalKnowledgeContentKind.METHOD_CANONICAL_CASE
            ):
                return self._read_case(request, parsed.stable_id)
            return _empty(
                request.logical_ref,
                ExternalKnowledgeResultStatus.INVALID_REQUEST,
            )
        except (OSError, UnicodeError, ValueError):
            return _empty(request.logical_ref, ExternalKnowledgeResultStatus.ERROR)

    def _read_subtarget(
        self,
        request: ExternalKnowledgeReadRequest,
        stable_id: str,
    ) -> ExternalKnowledgeReadResult:
        index = self._required_index()
        context = index.contexts_by_subtarget.get(stable_id)
        if context is None:
            return _empty(request.logical_ref, ExternalKnowledgeResultStatus.EMPTY)
        if request.relation not in {"", PATTERNS_RELATION}:
            return _empty(
                request.logical_ref,
                ExternalKnowledgeResultStatus.INVALID_REQUEST,
            )
        records = index.pattern_records(context)
        if request.relation == PATTERNS_RELATION:
            refs = tuple(
                self._ref(
                    ExternalKnowledgeContentKind.METHOD_PATTERN,
                    str(record.item["pattern_id"]),
                )
                for record in records[: request.limit]
            )
            return self._content(
                request.logical_ref,
                str(context["title"]),
                index.pattern_index(context),
                context,
                stable_id,
                refs,
                len(records) > len(refs),
            )
        payload = index.read_context_resource(
            context,
            collection="recommended_initial_resources",
            content_kind="method_subtarget_card",
            component_type="knowledge_subtarget_package",
        )
        refs = [
            self._ref(
                ExternalKnowledgeContentKind.METHOD_KNOWLEDGE,
                str(context["knowledge_id"]),
            )
        ]
        refs.extend(
            self._ref(
                ExternalKnowledgeContentKind.METHOD_PATTERN,
                str(record.item["pattern_id"]),
            )
            for record in records[: max(0, request.limit - 1)]
        )
        return self._content(
            request.logical_ref,
            str(context["title"]),
            payload,
            context,
            stable_id,
            tuple(refs),
            len(records) + 1 > len(refs),
        )

    def _read_knowledge(
        self,
        request: ExternalKnowledgeReadRequest,
        stable_id: str,
    ) -> ExternalKnowledgeReadResult:
        index = self._required_index()
        context = index.contexts_by_knowledge.get(stable_id)
        if context is None or request.relation:
            status = (
                ExternalKnowledgeResultStatus.EMPTY
                if context is None
                else ExternalKnowledgeResultStatus.INVALID_REQUEST
            )
            return _empty(request.logical_ref, status)
        payload = index.read_context_resource(
            context,
            collection="recommended_initial_resources",
            content_kind="method_knowledge",
            component_type="knowledge_item_package",
        )
        return self._content(
            request.logical_ref,
            str(context["title"]),
            payload,
            context,
            stable_id,
        )

    def _read_pattern(
        self,
        request: ExternalKnowledgeReadRequest,
        stable_id: str,
    ) -> ExternalKnowledgeReadResult:
        index = self._required_index()
        record = index.find_pattern(stable_id)
        if record is None:
            return _empty(request.logical_ref, ExternalKnowledgeResultStatus.EMPTY)
        if request.relation == PATTERN_CASE_RELATION:
            return self._read_relation(request, stable_id)
        if request.relation:
            return _empty(
                request.logical_ref,
                ExternalKnowledgeResultStatus.INVALID_REQUEST,
            )
        relation_stable_id = relation_id(record.context, stable_id)
        related_ref = self._ref(
            ExternalKnowledgeContentKind.PATTERN_CASE_RELEVANCE,
            relation_stable_id,
        )
        return self._content(
            request.logical_ref,
            required(record.item, "name"),
            index.pattern_detail(record),
            record.context,
            stable_id,
            (related_ref,),
        )

    def _read_relation(
        self,
        request: ExternalKnowledgeReadRequest,
        pattern_id: str | None,
    ) -> ExternalKnowledgeReadResult:
        index = self._required_index()
        record = (
            index.find_pattern(pattern_id)
            if pattern_id
            else index.find_pattern_for_relation(parse_logical_ref(request.logical_ref).stable_id)
        )
        if record is None:
            return _empty(request.logical_ref, ExternalKnowledgeResultStatus.EMPTY)
        page = read_pattern_case_slice(
            index=index,
            record=record,
            cursor=request.cursor,
            limit=request.limit,
        )
        items: list[ExternalKnowledgeRelationItem] = []
        for edge in page.rows:
            case_id = required(edge, "case_id")
            items.append(
                ExternalKnowledgeRelationItem(
                    logical_ref=self._ref(
                        ExternalKnowledgeContentKind.METHOD_CANONICAL_CASE,
                        case_id,
                    ),
                    title=_truncate(case_id, 300),
                    summary=_truncate(
                        str(edge.get("case_relevance_summary", "")),
                        MAX_EXTERNAL_KNOWLEDGE_RESULT_SUMMARY_CHARS,
                    ),
                )
            )
        if not items:
            return _empty(request.logical_ref, ExternalKnowledgeResultStatus.EMPTY)
        relation_ref = self._ref(
            ExternalKnowledgeContentKind.PATTERN_CASE_RELEVANCE,
            page.relation_id,
        )
        return ExternalKnowledgeReadResult(
            status=ExternalKnowledgeResultStatus.SUCCESS,
            logical_ref=request.logical_ref,
            title=required(record.item, "name"),
            provenance=self._provenance(
                request.logical_ref,
                record.context,
                str(record.item["pattern_id"]),
            ),
            relation_page=ExternalKnowledgeRelationPage(
                relation_ref=relation_ref,
                items=tuple(items),
                next_cursor=page.next_cursor,
                matched_count=page.matched_count,
                truncated=page.truncated,
            ),
            truncated=page.truncated,
        )

    def _read_case(
        self,
        request: ExternalKnowledgeReadRequest,
        case_id: str,
    ) -> ExternalKnowledgeReadResult:
        resolved = self._required_index().find_case(case_id)
        if resolved is None:
            return _empty(request.logical_ref, ExternalKnowledgeResultStatus.EMPTY)
        locator, context = resolved
        payload = self._required_index().read_case(locator, case_id)
        return self._content(
            request.logical_ref,
            str(payload.get("title", case_id)),
            payload,
            context,
            case_id,
        )

    def _content(
        self,
        logical_ref: str,
        title: str,
        payload: Mapping[str, Any],
        context: Mapping[str, Any],
        stable_id: str,
        related_refs: tuple[str, ...] = (),
        related_truncated: bool = False,
    ) -> ExternalKnowledgeReadResult:
        body, body_truncated = render_payload(payload)
        truncated = body_truncated or related_truncated
        return ExternalKnowledgeReadResult(
            status=(
                ExternalKnowledgeResultStatus.PARTIAL
                if truncated
                else ExternalKnowledgeResultStatus.SUCCESS
            ),
            logical_ref=logical_ref,
            title=_truncate(title, 300),
            body=body,
            provenance=self._provenance(logical_ref, context, stable_id),
            related_refs=related_refs,
            truncated=truncated,
        )

    def _ref(self, kind: ExternalKnowledgeContentKind, stable_id: str) -> str:
        return encode_logical_ref(
            release_id=self._pin.release_id,
            source_id=METHOD_SOURCE,
            content_kind=kind,
            stable_id=stable_id,
        )

    def _provenance(
        self,
        logical_ref: str,
        context: Mapping[str, Any],
        stable_id: str,
    ) -> ExternalKnowledgeProvenance:
        index = self._required_index()
        lineage = tuple(
            dict.fromkeys(
                (
                    str(context.get("source_ref", "")),
                    str(context.get("knowledge_id", "")),
                    stable_id,
                )
            )
        )
        return ExternalKnowledgeProvenance(
            release_id=self._pin.release_id,
            source_id=METHOD_SOURCE,
            package_id=index.source.package_id,
            package_version=index.source.package_version,
            logical_ref=logical_ref,
            content_lineage=tuple(value for value in lineage if value),
        )

    def _required_index(self) -> MethodPackageIndex:
        if self._index is None:
            raise ValueError("Method package is unavailable")
        return self._index


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 3].rstrip() + "..."


def _empty(
    logical_ref: str,
    status: ExternalKnowledgeResultStatus,
) -> ExternalKnowledgeReadResult:
    return ExternalKnowledgeReadResult(status=status, logical_ref=logical_ref)
