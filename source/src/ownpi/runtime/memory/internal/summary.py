from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ownpi.runtime.memory.map_contracts import MemoryDomain
from ownpi.runtime.memory.receipts import collect_memory_consumption_receipts
from ownpi.runtime.limits.projection import (
    MAX_MEMORY_FINAL_SUMMARY_FALLBACKS,
    MAX_MEMORY_FINAL_SUMMARY_REFS,
    MAX_MEMORY_REPOSITORY_IDENTITY_HISTORY,
)

from .contracts import parse_internal_ref
from .repository import InternalMemoryRepositorySnapshot


def build_internal_memory_receipt_summary(
    *,
    snapshot: InternalMemoryRepositorySnapshot,
    tool_call_history: Iterable[Any],
    repository_identity_history: Iterable[str] = (),
) -> dict[str, Any]:
    receipts = collect_memory_consumption_receipts(
        tool_call_history,
        domain=MemoryDomain.INTERNAL_MEMORY,
    )
    refs: list[str] = []
    seen_refs: set[str] = set()
    domains: set[str] = set()
    fallbacks: list[str] = []
    seen_fallbacks: set[str] = set()
    matched_count = 0
    returned_count = 0
    truncated = False
    operation_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    for receipt in receipts:
        operation = receipt.operation.value
        status = receipt.status.value
        operation_counts[operation] = operation_counts.get(operation, 0) + 1
        status_counts[status] = status_counts.get(status, 0) + 1
        matched_count += receipt.matched_count
        returned_count += receipt.returned_count
        truncated = truncated or receipt.truncated
        if receipt.fallback and receipt.fallback not in seen_fallbacks:
            seen_fallbacks.add(receipt.fallback)
            fallbacks.append(receipt.fallback)
        for raw_ref in receipt.logical_refs:
            logical_ref = str(raw_ref or "").strip()
            if not logical_ref or logical_ref in seen_refs:
                continue
            try:
                parsed = parse_internal_ref(logical_ref)
            except (TypeError, ValueError):
                continue
            seen_refs.add(logical_ref)
            domains.add(parsed.domain.value)
            if len(refs) < MAX_MEMORY_FINAL_SUMMARY_REFS:
                refs.append(logical_ref)
            else:
                truncated = True
    if len(fallbacks) > MAX_MEMORY_FINAL_SUMMARY_FALLBACKS:
        fallbacks = fallbacks[:MAX_MEMORY_FINAL_SUMMARY_FALLBACKS]
        truncated = True
    identities = list(
        dict.fromkeys(
            str(item)
            for item in repository_identity_history
            if str(item).strip()
        )
    )[-MAX_MEMORY_REPOSITORY_IDENTITY_HISTORY:]
    if snapshot.identity and snapshot.identity not in identities:
        identities.append(snapshot.identity)
    return {
        "repository_status": snapshot.status.value,
        "repository_identity": snapshot.identity,
        "repository_identity_history": identities[
            -MAX_MEMORY_REPOSITORY_IDENTITY_HISTORY:
        ],
        "record_count": snapshot.record_count,
        "committed_count": snapshot.committed_count,
        "domains": sorted(domains),
        "invocation_count": len(receipts),
        "operation_counts": dict(sorted(operation_counts.items())),
        "status_counts": dict(sorted(status_counts.items())),
        "matched_count": matched_count,
        "returned_count": returned_count,
        "logical_refs": refs,
        "fallbacks": fallbacks,
        "truncated": truncated,
    }
