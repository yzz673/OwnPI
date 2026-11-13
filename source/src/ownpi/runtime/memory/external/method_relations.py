from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ownpi.runtime.memory.external.contracts import (
    ExternalKnowledgeContentKind,
    ExternalKnowledgeSourceId,
    derive_relation_stable_id,
)
from ownpi.runtime.memory.external.method_index import (
    MethodPackageIndex,
    PatternRecord,
    required,
)


METHOD_SOURCE = ExternalKnowledgeSourceId.METHOD_KNOWLEDGE


@dataclass(frozen=True)
class MethodRelationSlice:
    relation_id: str
    rows: tuple[dict[str, Any], ...]
    next_cursor: str
    matched_count: int
    truncated: bool


def relation_id(context: Mapping[str, Any], pattern_id: str) -> str:
    return derive_relation_stable_id(
        source_id=METHOD_SOURCE,
        content_kind=ExternalKnowledgeContentKind.PATTERN_CASE_RELEVANCE,
        endpoints=(
            ("pattern", pattern_id),
            ("subtarget", str(context["source_ref"])),
        ),
    )


def read_pattern_case_slice(
    *,
    index: MethodPackageIndex,
    record: PatternRecord,
    cursor: str,
    limit: int,
) -> MethodRelationSlice:
    pattern_id = required(record.item, "pattern_id")
    detail = index.pattern_detail(record)
    case_count = detail.get("case_count")
    if (
        not isinstance(case_count, int)
        or isinstance(case_count, bool)
        or case_count < 0
    ):
        raise ValueError("pattern case count is invalid")
    stable_id = relation_id(record.context, pattern_id)
    offset = decode_cursor(cursor, stable_id)
    if offset > case_count:
        raise ValueError("relation cursor exceeds declared count")
    rows: list[dict[str, Any]] = []
    path = index.package.resolve_file(required(detail, "pattern_case_edges_path"))
    exhausted = True
    with path.open("r", encoding="utf-8") as handle:
        for position, line in enumerate(handle):
            if position < offset:
                continue
            if len(rows) >= limit or position >= case_count:
                exhausted = False
                break
            rows.append(index.validate_edge(json.loads(line), pattern_id))
    next_offset = offset + len(rows)
    if exhausted and next_offset < case_count:
        raise ValueError("pattern relation ended before its declared count")
    truncated = next_offset < case_count
    return MethodRelationSlice(
        relation_id=stable_id,
        rows=tuple(rows),
        next_cursor=encode_cursor(stable_id, next_offset) if truncated else "",
        matched_count=case_count,
        truncated=truncated,
    )


def encode_cursor(stable_id: str, offset: int) -> str:
    payload = json.dumps(
        {"o": offset, "r": stable_id}, sort_keys=True, separators=(",", ":")
    ).encode()
    return "nkfc:" + base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_cursor(cursor: str, stable_id: str) -> int:
    if not cursor:
        return 0
    try:
        encoded = cursor.removeprefix("nkfc:")
        payload = json.loads(
            base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        )
        offset = payload["o"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("relation cursor is invalid") from exc
    if (
        payload.get("r") != stable_id
        or not isinstance(offset, int)
        or isinstance(offset, bool)
        or offset < 0
    ):
        raise ValueError("relation cursor does not match the requested relation")
    return offset
