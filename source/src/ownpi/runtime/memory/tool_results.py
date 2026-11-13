from __future__ import annotations

from typing import Any

from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.memory.map_contracts import MemoryDomain
from ownpi.runtime.memory.receipts import (
    MemoryConsumptionOperation,
    MemoryConsumptionReceipt,
    MemoryConsumptionStatus,
    memory_consumption_receipt_from_metadata,
    with_memory_consumption_receipt,
)
from ownpi.runtime.tools.result_state import mechanical_result_metadata


def build_memory_tool_result(
    *,
    tool_name: str,
    payload: dict[str, Any],
    domain: MemoryDomain,
    invocation_id: str,
    operation: MemoryConsumptionOperation,
    receipt_status: MemoryConsumptionStatus,
    logical_refs: tuple[str, ...],
    matched_count: int,
    fallback: str,
    truncated: bool,
) -> ToolResult:
    visible_refs = (
        ()
        if receipt_status
        in {
            MemoryConsumptionStatus.EMPTY,
            MemoryConsumptionStatus.UNAVAILABLE,
            MemoryConsumptionStatus.ERROR,
        }
        else logical_refs
    )
    receipt = MemoryConsumptionReceipt(
        domain=domain,
        invocation_id=invocation_id,
        operation=operation,
        status=receipt_status,
        logical_refs=visible_refs,
        matched_count=max(matched_count, len(visible_refs)),
        returned_count=len(visible_refs),
        fallback=fallback[:120],
        truncated=truncated,
    )
    ok = receipt_status in {
        MemoryConsumptionStatus.SUCCESS,
        MemoryConsumptionStatus.EMPTY,
        MemoryConsumptionStatus.PARTIAL,
    }
    return ToolResult(
        ok=ok,
        tool_name=tool_name,
        data=payload,
        metadata=with_memory_consumption_receipt({}, receipt),
        error=None if ok else fallback or receipt_status.value,
    )


def memory_receipt_only_tool_result(tool_result: ToolResult) -> ToolResult:
    receipt = memory_consumption_receipt_from_metadata(tool_result.metadata)
    data = tool_result.data if isinstance(tool_result.data, dict) else {}
    return ToolResult(
        ok=tool_result.ok,
        tool_name=tool_result.tool_name,
        data={
            "status": str(data.get("status", "") or ""),
            "fallback": str(data.get("fallback", "") or "")[:120],
            "truncated": bool(data.get("truncated", False)),
        },
        metadata={
            **mechanical_result_metadata(tool_result.metadata),
            "context_effects": (
                {"memory_consumption": receipt.to_dict()} if receipt is not None else {}
            ),
            "transcript_compacted": True,
            "compaction_policy": "memory_receipt_only",
        },
        warnings=list(tool_result.warnings),
        error=tool_result.error,
        duration=tool_result.duration,
        exit_code=tool_result.exit_code,
    )
