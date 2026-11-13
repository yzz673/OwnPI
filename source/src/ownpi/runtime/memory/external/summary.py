from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ownpi.runtime.memory.external.contracts import (
    ExternalKnowledgeReleasePin,
    parse_logical_ref,
)
from ownpi.runtime.memory.map_contracts import MemoryDomain
from ownpi.runtime.memory.receipts import collect_memory_consumption_receipts
from ownpi.runtime.limits.projection import (
    MAX_MEMORY_FINAL_SUMMARY_FALLBACKS,
    MAX_MEMORY_FINAL_SUMMARY_REFS,
)


def build_external_knowledge_receipt_summary(
    *,
    pin: ExternalKnowledgeReleasePin | None,
    tool_call_history: Iterable[Any],
) -> dict[str, Any]:
    receipts = collect_memory_consumption_receipts(
        tool_call_history,
        domain=MemoryDomain.EXTERNAL_KNOWLEDGE,
    )
    refs: list[str] = []
    seen_refs: set[str] = set()
    fallbacks: list[str] = []
    seen_fallbacks: set[str] = set()
    source_ids: set[str] = set()
    matched_count = 0
    returned_count = 0
    truncated = False
    operation_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    for receipt in receipts:
        operation = receipt.operation.value
        status = receipt.status.value
        if operation:
            operation_counts[operation] = operation_counts.get(operation, 0) + 1
        if status:
            status_counts[status] = status_counts.get(status, 0) + 1
        matched_count += receipt.matched_count
        returned_count += receipt.returned_count
        truncated = truncated or receipt.truncated
        fallback = receipt.fallback
        if fallback and fallback not in seen_fallbacks:
            seen_fallbacks.add(fallback)
            fallbacks.append(fallback)
        for raw_ref in receipt.logical_refs:
            logical_ref = str(raw_ref or '').strip()
            if not logical_ref or logical_ref in seen_refs:
                continue
            try:
                parsed = parse_logical_ref(logical_ref)
            except (TypeError, ValueError):
                continue
            seen_refs.add(logical_ref)
            source_ids.add(parsed.source_id.value)
            if len(refs) < MAX_MEMORY_FINAL_SUMMARY_REFS:
                refs.append(logical_ref)
            else:
                truncated = True
    if len(fallbacks) > MAX_MEMORY_FINAL_SUMMARY_FALLBACKS:
        fallbacks = fallbacks[:MAX_MEMORY_FINAL_SUMMARY_FALLBACKS]
        truncated = True
    return {
        'release_id': pin.release_id if pin is not None else '',
        'snapshot_sha256': pin.snapshot_sha256 if pin is not None else '',
        'source_ids': sorted(source_ids),
        'invocation_count': len(receipts),
        'operation_counts': dict(sorted(operation_counts.items())),
        'status_counts': dict(sorted(status_counts.items())),
        'matched_count': matched_count,
        'returned_count': returned_count,
        'logical_refs': refs,
        'fallbacks': fallbacks,
        'truncated': truncated,
    }
