from __future__ import annotations

from typing import Any

from ownpi.controller.stages import Stage
from ownpi.runtime.events import now_utc_iso


def make_query_chain_id(
    *,
    stage: Stage | str,
    attempt: int,
    ordinal: int,
) -> str:
    stage_value = stage.value if isinstance(stage, Stage) else str(stage or '').strip() or 'unknown'
    return f'qc:{stage_value}:a{int(attempt or 0)}:{int(ordinal or 0)}'


def query_chain_depth_for_parent(
    history: list[dict[str, Any]],
    parent_query_chain_id: str | None,
) -> int:
    if not parent_query_chain_id:
        return 0
    for item in reversed(history):
        if str(item.get('query_chain_id', '')).strip() == str(parent_query_chain_id).strip():
            return int(item.get('query_chain_depth', 0) or 0) + 1
    return 1


def build_query_chain_record(
    *,
    query_chain_id: str,
    stage: Stage | str,
    attempt: int,
    query_chain_depth: int,
    parent_query_chain_id: str | None,
    reason: str,
    source: str,
    created_at: str | None = None,
) -> dict[str, Any]:
    stage_value = stage.value if isinstance(stage, Stage) else str(stage or '').strip()
    return {
        'query_chain_id': str(query_chain_id).strip(),
        'stage': stage_value,
        'attempt': int(attempt or 0),
        'query_chain_depth': int(query_chain_depth or 0),
        'parent_query_chain_id': (
            str(parent_query_chain_id).strip() if str(parent_query_chain_id or '').strip() else None
        ),
        'reason': str(reason or '').strip(),
        'source': str(source or '').strip() or 'runtime',
        'created_at': created_at or now_utc_iso(),
    }


def query_chain_scope_dict(
    *,
    stage: Stage | str,
    attempt: int,
    query_chain_id: str,
    query_chain_depth: int,
    continuation_index: int,
) -> dict[str, Any]:
    stage_value = stage.value if isinstance(stage, Stage) else str(stage or '').strip()
    return {
        'stage': stage_value,
        'attempt': int(attempt or 0),
        'query_chain_id': str(query_chain_id or '').strip(),
        'query_chain_depth': int(query_chain_depth or 0),
        'continuation_index': int(continuation_index or 0),
    }
