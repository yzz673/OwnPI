from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

from ownpi.runtime.memory.external.contracts import (
    ExternalKnowledgeReadRequest,
    ExternalKnowledgeReadResult,
    ExternalKnowledgeReleasePin,
    ExternalKnowledgeResultStatus,
    ExternalKnowledgeSearchRequest,
    ExternalKnowledgeSearchHit,
    ExternalKnowledgeSearchResult,
    ExternalKnowledgeSourceId,
    parse_logical_ref,
)
from ownpi.runtime.memory.external.method_adapter import MethodKnowledgeAdapter
from ownpi.runtime.memory.external.research_adapter import ResearchStrategyAdapter


@dataclass(frozen=True)
class ExternalKnowledgeService:
    method: MethodKnowledgeAdapter | None
    research: ResearchStrategyAdapter | None
    diagnostics: tuple[str, ...] = ()

    @property
    def available_sources(self) -> tuple[ExternalKnowledgeSourceId, ...]:
        sources: list[ExternalKnowledgeSourceId] = []
        if self.method is not None and self.method.available:
            sources.append(ExternalKnowledgeSourceId.METHOD_KNOWLEDGE)
        if self.research is not None:
            sources.append(ExternalKnowledgeSourceId.RESEARCH_STRATEGY)
        return tuple(sources)

    @property
    def status(self) -> str:
        count = len(self.available_sources)
        if count == 2:
            return 'available'
        return 'degraded' if count else 'unavailable'

    def search(self, request: ExternalKnowledgeSearchRequest) -> ExternalKnowledgeSearchResult:
        requested = request.sources or tuple(ExternalKnowledgeSourceId)
        results: list[ExternalKnowledgeSearchResult] = []
        if ExternalKnowledgeSourceId.METHOD_KNOWLEDGE in requested and self.method is not None:
            results.append(self.method.search(request))
        if ExternalKnowledgeSourceId.RESEARCH_STRATEGY in requested and self.research is not None:
            results.append(self.research.search(request))
        if not results:
            return ExternalKnowledgeSearchResult(
                status=ExternalKnowledgeResultStatus.UNAVAILABLE,
                hits=(),
                matched_count=0,
                fallback='source_unavailable',
            )
        returned = _interleave_source_hits(results, limit=request.limit)
        matched_count = sum(result.matched_count for result in results)
        fallback = ','.join(
            dict.fromkeys(result.fallback for result in results if result.fallback)
        )[:120]
        if returned:
            status = (
                ExternalKnowledgeResultStatus.PARTIAL
                if any(result.status != ExternalKnowledgeResultStatus.SUCCESS for result in results)
                else ExternalKnowledgeResultStatus.SUCCESS
            )
        elif all(result.status == ExternalKnowledgeResultStatus.EMPTY for result in results):
            status = ExternalKnowledgeResultStatus.EMPTY
        elif any(result.status == ExternalKnowledgeResultStatus.UNAVAILABLE for result in results):
            status = ExternalKnowledgeResultStatus.UNAVAILABLE
        else:
            status = ExternalKnowledgeResultStatus.ERROR
        return ExternalKnowledgeSearchResult(
            status=status,
            hits=returned,
            matched_count=max(matched_count, len(returned)),
            fallback=fallback,
            truncated=matched_count > len(returned) or any(result.truncated for result in results),
        )

    def read(self, request: ExternalKnowledgeReadRequest) -> ExternalKnowledgeReadResult:
        parsed = parse_logical_ref(request.logical_ref)
        if parsed.source_id == ExternalKnowledgeSourceId.METHOD_KNOWLEDGE:
            if self.method is not None:
                return self.method.read(request)
        elif self.research is not None:
            return self.research.read(request)
        return ExternalKnowledgeReadResult(
            status=ExternalKnowledgeResultStatus.UNAVAILABLE,
            logical_ref=request.logical_ref,
            fallback='source_unavailable',
        )


def build_external_knowledge_service(
    *,
    delivery_root: Path,
    pin: ExternalKnowledgeReleasePin,
) -> ExternalKnowledgeService:
    canonical_root = str(delivery_root.expanduser().resolve())
    pin_payload = json.dumps(pin.to_dict(), sort_keys=True, separators=(',', ':'))
    pin_digest = hashlib.sha256(pin_payload.encode('utf-8')).hexdigest()
    return _cached_service(canonical_root, pin_digest, pin_payload)


@lru_cache(maxsize=8)
def _cached_service(
    delivery_root: str,
    pin_digest: str,
    pin_payload: str,
) -> ExternalKnowledgeService:
    del pin_digest
    pin = ExternalKnowledgeReleasePin.from_dict(json.loads(pin_payload))
    diagnostics: list[str] = []
    method = MethodKnowledgeAdapter(delivery_root=Path(delivery_root), pin=pin)
    if not method.available:
        diagnostics.append('method_unavailable')
    research: ResearchStrategyAdapter | None
    try:
        research = ResearchStrategyAdapter(Path(delivery_root), pin)
    except (OSError, UnicodeError, ValueError) as exc:
        diagnostics.append(f'research_unavailable:{type(exc).__name__}')
        research = None
    return ExternalKnowledgeService(
        method=method,
        research=research,
        diagnostics=tuple(diagnostics[:4]),
    )


def external_result_to_dict(result: object) -> dict[str, Any]:
    return _json_value(asdict(result))


def _interleave_source_hits(
    results: list[ExternalKnowledgeSearchResult],
    *,
    limit: int,
) -> tuple[ExternalKnowledgeSearchHit, ...]:
    returned: list[ExternalKnowledgeSearchHit] = []
    rank = 0
    while len(returned) < limit:
        added = False
        for result in results:
            if rank >= len(result.hits):
                continue
            returned.append(result.hits[rank])
            added = True
            if len(returned) == limit:
                break
        if not added:
            break
        rank += 1
    return tuple(returned)


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value
