from __future__ import annotations

import re
import unicodedata
from typing import Any, Mapping

from ownpi.runtime.memory.external.contracts import (
    ExternalKnowledgeContentKind,
    ExternalKnowledgeProvenance,
    ExternalKnowledgeResultStatus,
    ExternalKnowledgeSearchHit,
    ExternalKnowledgeSearchRequest,
    ExternalKnowledgeSearchResult,
    encode_logical_ref,
)
from ownpi.runtime.memory.external.research_index import (
    SOURCE_ID,
    ResearchPackageIndex,
    bounded,
    required_string,
)
from ownpi.runtime.limits.projection import (
    MAX_EXTERNAL_KNOWLEDGE_RESULT_SUMMARY_CHARS,
)


_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
_TERM_WEIGHTS = {"archetype": 6.0, "dimension": 7.0, "task_term": 1.0}


def search_research(
    index: ResearchPackageIndex, request: ExternalKnowledgeSearchRequest
) -> ExternalKnowledgeSearchResult:
    if request.sources and SOURCE_ID not in request.sources:
        return ExternalKnowledgeSearchResult(ExternalKnowledgeResultStatus.EMPTY, (), 0)
    query = _tokens(request.query)
    intents = frozenset(request.intents)
    ranked: list[tuple[float, int, str, ExternalKnowledgeSearchHit]] = []
    for dimension in index.dimensions.values():
        matched = _matched_intents(dimension.context, intents)
        dimension_score = _lexical_score(
            query, dimension.context.get("retrieval_terms")
        )
        score = _combined_score(dimension_score, matched, intents)
        if score:
            ranked.append(
                (
                    score,
                    0,
                    dimension.cell_id,
                    _hit(
                        index,
                        ExternalKnowledgeContentKind.RESEARCH_DIMENSION,
                        dimension.cell_id,
                        required_string(dimension.context, "title"),
                        required_string(dimension.context, "summary"),
                        score,
                        (dimension.archetype, dimension.dimension),
                        matched,
                    ),
                )
            )
        for strategy in dimension.strategies:
            own_score = _lexical_score(query, strategy.item.get("retrieval_terms"))
            score = _combined_score(
                0.75 * own_score + 0.25 * dimension_score, matched, intents
            )
            if not score:
                continue
            strategy_id = required_string(strategy.item, "strategy_id")
            ranked.append(
                (
                    score,
                    1,
                    strategy.stable_id,
                    _hit(
                        index,
                        ExternalKnowledgeContentKind.RESEARCH_STRATEGY,
                        strategy.stable_id,
                        required_string(strategy.item, "strategy_name"),
                        required_string(strategy.item, "one_sentence_use"),
                        score,
                        (strategy.archetype, strategy.dimension, strategy_id),
                        matched,
                    ),
                )
            )
    ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
    hits = tuple(item[3] for item in ranked[: request.limit])
    status = (
        ExternalKnowledgeResultStatus.SUCCESS
        if hits
        else ExternalKnowledgeResultStatus.EMPTY
    )
    return ExternalKnowledgeSearchResult(
        status, hits, len(ranked), truncated=len(ranked) > len(hits)
    )


def _hit(
    index: ResearchPackageIndex,
    kind: ExternalKnowledgeContentKind,
    stable_id: str,
    title: str,
    summary: str,
    score: float,
    lineage: tuple[str, ...],
    matched: tuple[str, ...],
) -> ExternalKnowledgeSearchHit:
    logical_ref = encode_logical_ref(
        release_id=index.pin.release_id,
        source_id=SOURCE_ID,
        content_kind=kind,
        stable_id=stable_id,
    )
    provenance = ExternalKnowledgeProvenance(
        index.pin.release_id,
        SOURCE_ID,
        index.source.package_id,
        index.source.package_version,
        logical_ref,
        lineage,
    )
    return ExternalKnowledgeSearchHit(
        logical_ref,
        bounded(title, 300),
        bounded(summary, MAX_EXTERNAL_KNOWLEDGE_RESULT_SUMMARY_CHARS),
        round(score, 6),
        provenance,
        matched,
    )


def _tokens(value: str) -> frozenset[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold().replace("_", " ")
    return frozenset(_TOKEN_PATTERN.findall(normalized))


def _lexical_score(query: frozenset[str], raw_terms: object) -> float:
    if not query or not isinstance(raw_terms, list):
        return 0.0
    best = {token: 0.0 for token in query}
    for item in raw_terms:
        if not isinstance(item, dict) or item.get("term_type") not in _TERM_WEIGHTS:
            continue
        term = item.get("term")
        if not isinstance(term, str):
            continue
        weight = _TERM_WEIGHTS[str(item["term_type"])]
        for token in query.intersection(_tokens(term)):
            best[token] = max(best[token], weight)
    return min(1.0, sum(best.values()) / (len(query) * max(_TERM_WEIGHTS.values())))


def _matched_intents(
    payload: Mapping[str, Any], requested: frozenset[str]
) -> tuple[str, ...]:
    raw = payload.get("usage_intents", [])
    if not requested or not isinstance(raw, list):
        return ()
    return tuple(sorted(requested.intersection(x for x in raw if isinstance(x, str))))


def _combined_score(
    lexical: float, matched: tuple[str, ...], requested: frozenset[str]
) -> float:
    intent = len(matched) / len(requested) if requested else 0.0
    return min(1.0, 0.85 * lexical + 0.15 * intent) if lexical or intent else 0.0
