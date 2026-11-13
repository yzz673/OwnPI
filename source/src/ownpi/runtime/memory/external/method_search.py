from __future__ import annotations

import json
import math
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ownpi.runtime.memory.external.contracts import (
    ExternalKnowledgeAcceleratorStatus,
    ExternalKnowledgeContentKind,
    ExternalKnowledgeProvenance,
    ExternalKnowledgeReleasePin,
    ExternalKnowledgeResultStatus,
    ExternalKnowledgeSearchHit,
    ExternalKnowledgeSearchRequest,
    ExternalKnowledgeSearchResult,
    ExternalKnowledgeSourceId,
    encode_logical_ref,
)
from ownpi.runtime.memory.external.method_index import (
    MethodPackageIndex,
    required,
)
from ownpi.runtime.limits.projection import (
    MAX_EXTERNAL_KNOWLEDGE_RESULT_SUMMARY_CHARS,
)


METHOD_SOURCE = ExternalKnowledgeSourceId.METHOD_KNOWLEDGE
MethodVectorQuery = Callable[..., Mapping[str, float]]


@dataclass(frozen=True)
class _Candidate:
    context: Mapping[str, Any]
    kind: ExternalKnowledgeContentKind
    stable_id: str
    title: str
    summary: str
    terms: frozenset[str]
    vector_score: float = 0.0


class MethodSearchEngine:
    def __init__(
        self,
        *,
        index: MethodPackageIndex,
        pin: ExternalKnowledgeReleasePin,
        vector_query: MethodVectorQuery | None,
    ) -> None:
        self._index = index
        self._pin = pin
        self._vector_query = vector_query

    def search(
        self, request: ExternalKnowledgeSearchRequest
    ) -> ExternalKnowledgeSearchResult:
        if request.sources and METHOD_SOURCE not in request.sources:
            return _empty(ExternalKnowledgeResultStatus.EMPTY)
        try:
            query_terms = _terms(request.query)
            contexts, fallback = self._rank_contexts(request.query, query_terms)
            candidates = self._discover(contexts, request.limit)
            ranked = sorted(
                candidates,
                key=lambda item: (
                    -_candidate_score(item, query_terms),
                    _kind_order(item.kind),
                    item.stable_id,
                ),
            )
            ranked = [
                item for item in ranked if _candidate_score(item, query_terms) > 0
            ]
            hits = tuple(
                self._hit(item, request, query_terms)
                for item in ranked[: request.limit]
            )
            if not hits:
                return ExternalKnowledgeSearchResult(
                    status=ExternalKnowledgeResultStatus.EMPTY,
                    hits=(),
                    matched_count=0,
                    fallback=fallback,
                )
            return ExternalKnowledgeSearchResult(
                status=(
                    ExternalKnowledgeResultStatus.PARTIAL
                    if fallback
                    else ExternalKnowledgeResultStatus.SUCCESS
                ),
                hits=hits,
                matched_count=len(ranked),
                fallback=fallback,
                truncated=len(ranked) > len(hits),
            )
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            return _empty(ExternalKnowledgeResultStatus.ERROR)

    def _rank_contexts(
        self, query: str, query_terms: frozenset[str]
    ) -> tuple[list[tuple[Mapping[str, Any], float]], str]:
        lexical = {
            str(context["source_ref"]): _overlap(query_terms, _context_terms(context))
            for context in self._index.contexts
        }
        vectors, fallback = self._query_vector(query)
        ranked: list[tuple[Mapping[str, Any], float]] = []
        for context in self._index.contexts:
            source_ref = str(context["source_ref"])
            vector_score = float(vectors.get(source_ref, 0.0))
            score = (
                lexical[source_ref] * 0.8 + vector_score * 0.2
                if vectors
                else lexical[source_ref]
            )
            if score > 0:
                ranked.append((context, vector_score))
        ranked.sort(
            key=lambda pair: (
                -(lexical[str(pair[0]["source_ref"])] * 0.8 + pair[1] * 0.2),
                str(pair[0]["source_ref"]),
            )
        )
        return ranked, fallback

    def _query_vector(self, query: str) -> tuple[Mapping[str, float], str]:
        active = next(
            (
                item
                for item in self._pin.accelerators
                if item.accelerates_source_id == METHOD_SOURCE
                and item.status == ExternalKnowledgeAcceleratorStatus.ACTIVE
            ),
            None,
        )
        if active is None:
            return {}, "lexical_vector_unavailable"
        try:
            root, manifest = self._index.validate_vector(active.accelerator_id)
        except (OSError, UnicodeError, ValueError):
            return {}, "lexical_vector_unavailable"
        if self._vector_query is None:
            return {}, "lexical_vector_query_unavailable"
        try:
            raw = self._vector_query(query, accelerator_root=root, manifest=manifest)
            valid_ids = set(self._index.contexts_by_subtarget)
            scores = {
                key: float(value)
                for key, value in raw.items()
                if key in valid_ids
                and not isinstance(value, bool)
                and math.isfinite(float(value))
                and 0.0 <= float(value) <= 1.0
            }
            if len(scores) != len(raw):
                raise ValueError("vector query returned invalid context scores")
            return scores, ""
        except Exception:
            return {}, "lexical_vector_query_failed"

    def _discover(
        self, contexts: list[tuple[Mapping[str, Any], float]], limit: int
    ) -> list[_Candidate]:
        candidates: list[_Candidate] = []
        for context, vector_score in contexts:
            common = _context_terms(context)
            candidates.extend(
                (
                    _Candidate(
                        context,
                        ExternalKnowledgeContentKind.METHOD_SUBTARGET,
                        str(context["source_ref"]),
                        str(context["title"]),
                        str(context["summary"]),
                        common,
                        vector_score,
                    ),
                    _Candidate(
                        context,
                        ExternalKnowledgeContentKind.METHOD_KNOWLEDGE,
                        str(context["knowledge_id"]),
                        str(context["title"]),
                        str(context["summary"]),
                        common,
                        vector_score,
                    ),
                )
            )
            if len(candidates) >= max(2, limit * 4):
                continue
            for record in self._index.pattern_records(context):
                item = record.item
                candidates.append(
                    _Candidate(
                        context,
                        ExternalKnowledgeContentKind.METHOD_PATTERN,
                        required(item, "pattern_id"),
                        required(item, "name"),
                        _first_text(
                            item, "pattern_preview_summary", "core_rule_preview"
                        ),
                        _terms(
                            " ".join(
                                str(item.get(key, ""))
                                for key in (
                                    "pattern_id",
                                    "group_id",
                                    "name",
                                    "trigger_condition",
                                    "pattern_preview_summary",
                                    "core_rule_preview",
                                )
                            )
                        ),
                    )
                )
        return candidates

    def _hit(
        self,
        candidate: _Candidate,
        request: ExternalKnowledgeSearchRequest,
        query_terms: frozenset[str],
    ) -> ExternalKnowledgeSearchHit:
        ref = encode_logical_ref(
            release_id=self._pin.release_id,
            source_id=METHOD_SOURCE,
            content_kind=candidate.kind,
            stable_id=candidate.stable_id,
        )
        intents = tuple(
            sorted(
                set(request.intents).intersection(
                    _string_set(candidate.context.get("usage_intents"))
                )
            )
        )
        lineage = tuple(
            dict.fromkeys(
                (
                    str(candidate.context.get("source_ref", "")),
                    str(candidate.context.get("knowledge_id", "")),
                    candidate.stable_id,
                )
            )
        )
        return ExternalKnowledgeSearchHit(
            logical_ref=ref,
            title=_truncate(candidate.title, 300),
            summary=_truncate(
                candidate.summary,
                MAX_EXTERNAL_KNOWLEDGE_RESULT_SUMMARY_CHARS,
            ),
            score=_candidate_score(candidate, query_terms),
            provenance=ExternalKnowledgeProvenance(
                release_id=self._pin.release_id,
                source_id=METHOD_SOURCE,
                package_id=self._index.source.package_id,
                package_version=self._index.source.package_version,
                logical_ref=ref,
                content_lineage=tuple(value for value in lineage if value),
            ),
            matched_intents=intents,
        )


def _terms(value: str) -> frozenset[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold().replace("_", " ")
    return frozenset(
        token for token in re.findall(r"[^\W_]+", normalized) if len(token) >= 2
    )


def _context_terms(context: Mapping[str, Any]) -> frozenset[str]:
    retrieval = context.get("retrieval_terms")
    declared = (
        " ".join(
            str(item.get("term", "")) for item in retrieval if isinstance(item, dict)
        )
        if isinstance(retrieval, list)
        else ""
    )
    return _terms(
        " ".join(
            str(context.get(key, ""))
            for key in ("source_ref", "knowledge_id", "title", "summary")
        )
        + " "
        + declared
    )


def _candidate_score(candidate: _Candidate, query_terms: frozenset[str]) -> float:
    lexical = _overlap(query_terms, candidate.terms)
    if (
        lexical == 0
        and candidate.vector_score > 0
        and candidate.kind != ExternalKnowledgeContentKind.METHOD_PATTERN
    ):
        return round(candidate.vector_score * 0.5, 6)
    return round(min(1.0, lexical + candidate.vector_score * 0.05), 6)


def _overlap(query: frozenset[str], candidate: frozenset[str]) -> float:
    return round(len(query.intersection(candidate)) / len(query), 6) if query else 0.0


def _kind_order(kind: ExternalKnowledgeContentKind) -> int:
    return {
        ExternalKnowledgeContentKind.METHOD_PATTERN: 0,
        ExternalKnowledgeContentKind.METHOD_SUBTARGET: 1,
        ExternalKnowledgeContentKind.METHOD_KNOWLEDGE: 2,
    }.get(kind, 9)


def _first_text(payload: Mapping[str, Any], *keys: str) -> str:
    return next((str(payload[key]) for key in keys if payload.get(key)), "")


def _string_set(value: object) -> set[str]:
    return {str(item) for item in value} if isinstance(value, list) else set()


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 3].rstrip() + "..."


def _empty(status: ExternalKnowledgeResultStatus) -> ExternalKnowledgeSearchResult:
    return ExternalKnowledgeSearchResult(status=status, hits=(), matched_count=0)
