from __future__ import annotations
from ownpi.runtime.tools.definitions import ToolExposure

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.limits.projection import (
    DEFAULT_INTERNAL_MEMORY_READ_BODY_BYTES,
    DEFAULT_INTERNAL_MEMORY_SEARCH_RESULTS,
    DEFAULT_MEMORY_TOOL_RESULT_BYTES,
)
from ownpi.runtime.memory.internal import (
    InternalMemoryContentKind,
    InternalMemoryDomain,
    InternalMemoryReadRequest,
    InternalMemoryRepository,
    InternalMemoryRepositorySnapshot,
    InternalMemoryRepositoryStatus,
    InternalMemoryResultStatus,
    InternalMemoryScopeFilter,
    InternalMemoryScopeType,
    InternalMemorySearchRequest,
    InternalMemoryService,
)
from ownpi.runtime.memory.internal.service import internal_result_to_dict
from ownpi.runtime.memory.internal.scope import build_runtime_scope_filters
from ownpi.runtime.memory.map_contracts import MemoryDomain
from ownpi.runtime.memory.payloads import (
    drop_last_list_item,
    serialized_payload_size_bytes,
    truncate_utf8,
)
from ownpi.runtime.memory.receipts import (
    MemoryConsumptionOperation,
    MemoryConsumptionStatus,
)
from ownpi.runtime.memory.tool_results import build_memory_tool_result
from ownpi.runtime.tools.definitions import ToolDefinition, ToolExecutionContext


INTERNAL_MEMORY_SEARCH_TOOL = "internal_memory_search"
INTERNAL_MEMORY_READ_TOOL = "internal_memory_read"
def build_internal_memory_tool_definitions(
    state: Any,
    *,
    repository_snapshot: InternalMemoryRepositorySnapshot | None = None,
) -> tuple[ToolDefinition, ...]:
    config = getattr(state.runtime_preferences, "internal_memory", {})
    if not bool(config.get("enabled", False)):
        return ()
    root = str(config.get("root", "") or "").strip()
    if not root:
        return ()
    try:
        repository = InternalMemoryRepository(Path(root))
        snapshot = repository_snapshot or repository.snapshot()
        if snapshot.status in {
            InternalMemoryRepositoryStatus.CORRUPT,
            InternalMemoryRepositoryStatus.UNAVAILABLE,
        }:
            return ()
    except (OSError, TypeError, ValueError):
        return ()
    return build_internal_memory_tool_definitions_for_service(
        InternalMemoryService(repository)
    )


def build_internal_memory_tool_definitions_for_service(
    service: InternalMemoryService,
    *,
    max_read_body_bytes: int = DEFAULT_INTERNAL_MEMORY_READ_BODY_BYTES,
    max_result_bytes: int = DEFAULT_MEMORY_TOOL_RESULT_BYTES,
) -> tuple[ToolDefinition, ...]:
    return (
        _search_definition(service, max_result_bytes),
        _read_definition(service, max_read_body_bytes, max_result_bytes),
    )


def _search_definition(
    service: InternalMemoryService,
    max_result_bytes: int,
) -> ToolDefinition:
    async def handler(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
        try:
            runtime_scope_filters = _runtime_scope_filters(context)
            request = InternalMemorySearchRequest(
                query=str(arguments.get("query", "")),
                domains=tuple(
                    InternalMemoryDomain(str(item))
                    for item in arguments.get("domains", ())
                ),
                content_kinds=tuple(
                    InternalMemoryContentKind(str(item))
                    for item in arguments.get("content_kinds", ())
                ),
                scope_filters=tuple(
                    InternalMemoryScopeFilter(
                        scope_type=InternalMemoryScopeType(str(item["scope_type"])),
                        scope_id=str(item.get("scope_id", "")),
                    )
                    for item in arguments.get("scope_filters", ())
                    if isinstance(item, Mapping)
                ),
                limit=int(
                    arguments.get(
                        "limit",
                        DEFAULT_INTERNAL_MEMORY_SEARCH_RESULTS,
                    )
                ),
            )
        except (KeyError, TypeError, ValueError):
            return _error_result(
                tool_name=INTERNAL_MEMORY_SEARCH_TOOL,
                operation=MemoryConsumptionOperation.SEARCH,
                fallback="invalid_request",
            )
        result = service.search(
            request,
            runtime_scope_filters=runtime_scope_filters,
        )
        payload = _bounded_payload(
            internal_result_to_dict(result),
            max_bytes=max_result_bytes,
            item_paths=(("hits",),),
        )
        return _tool_result(
            tool_name=INTERNAL_MEMORY_SEARCH_TOOL,
            payload=payload,
            operation=MemoryConsumptionOperation.SEARCH,
        )

    return ToolDefinition(
        exposure=ToolExposure.DIRECT,
        **_summary(
            name=INTERNAL_MEMORY_SEARCH_TOOL,
            description="Search committed Agent-owned Internal Memory records.",
        ),
        input_schema_builder=lambda: {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 2_000},
                "domains": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [item.value for item in InternalMemoryDomain],
                    },
                    "maxItems": 2,
                },
                "content_kinds": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [item.value for item in InternalMemoryContentKind],
                    },
                    "maxItems": 6,
                },
                "scope_filters": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "scope_type": {
                                "type": "string",
                                "enum": [item.value for item in InternalMemoryScopeType],
                            },
                            "scope_id": {"type": "string", "maxLength": 240},
                        },
                        "required": ["scope_type"],
                        "additionalProperties": False,
                    },
                    "maxItems": 16,
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "default": DEFAULT_INTERNAL_MEMORY_SEARCH_RESULTS,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=handler,
        observation_family="internal_memory",
        result_family="internal_memory",
        max_prompt_result_chars=max_result_bytes,
        max_result_size_chars=max_result_bytes,
        result_identity_fields=("status", "logical_ref"),
    )


def _read_definition(
    service: InternalMemoryService,
    max_read_body_bytes: int,
    max_result_bytes: int,
) -> ToolDefinition:
    async def handler(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
        try:
            runtime_scope_filters = _runtime_scope_filters(context)
            request = InternalMemoryReadRequest(
                logical_ref=str(arguments.get("logical_ref", "")),
                max_body_bytes=min(
                    int(arguments.get("max_body_bytes", max_read_body_bytes)),
                    max_read_body_bytes,
                ),
                include_provenance=bool(arguments.get("include_provenance", True)),
            )
        except (KeyError, TypeError, ValueError):
            return _error_result(
                tool_name=INTERNAL_MEMORY_READ_TOOL,
                operation=MemoryConsumptionOperation.READ,
                fallback="invalid_request",
            )
        result = service.read(
            request,
            runtime_scope_filters=runtime_scope_filters,
        )
        payload = _bounded_payload(
            internal_result_to_dict(result),
            max_bytes=max_result_bytes,
            item_paths=(("provenance",),),
        )
        return _tool_result(
            tool_name=INTERNAL_MEMORY_READ_TOOL,
            payload=payload,
            operation=MemoryConsumptionOperation.READ,
        )

    return ToolDefinition(
        exposure=ToolExposure.DIRECT,
        **_summary(
            name=INTERNAL_MEMORY_READ_TOOL,
            description="Read bounded detail from one committed Internal Memory ref.",
        ),
        input_schema_builder=lambda: {
            "type": "object",
            "properties": {
                "logical_ref": {"type": "string", "minLength": 1, "maxLength": 240},
                "max_body_bytes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": max_read_body_bytes,
                },
                "include_provenance": {"type": "boolean"},
            },
            "required": ["logical_ref"],
            "additionalProperties": False,
        },
        handler=handler,
        observation_family="internal_memory",
        result_family="internal_memory",
        max_prompt_result_chars=max_result_bytes,
        max_result_size_chars=max_result_bytes,
        result_identity_fields=("status", "logical_ref"),
    )


def _summary(*, name: str, description: str) -> dict[str, Any]:
    return {
        "name": name,
        "kind": "memory_read",
        "description": description,
        "detail": (
            "Model-requested, read-only access to committed Agent-owned operational "
            "memory. Historical memory is guidance below current user and repository evidence."
        ),
        "search_hint": "Search or progressively read committed Internal Memory.",
        "search_keywords": ("internal", "memory", "preference", "workflow", "failure"),
        "read_only": True,
        "concurrency_safe": True,
        "permission_class": "artifact",
        "invocation_mode": "agent_requested",
    }


def _runtime_scope_filters(context: ToolExecutionContext) -> tuple[InternalMemoryScopeFilter, ...]:
    if not isinstance(context, ToolExecutionContext):
        raise TypeError("internal memory tools require execution context")
    return build_runtime_scope_filters(
        project_root=context.project_root,
        workflow_id=str(context.run_id or ""),
    )


def _tool_result(
    *,
    tool_name: str,
    payload: dict[str, Any],
    operation: MemoryConsumptionOperation,
) -> ToolResult:
    status = str(payload.get("status", InternalMemoryResultStatus.ERROR.value))
    refs = _visible_logical_refs(payload)
    return build_memory_tool_result(
        tool_name=tool_name,
        payload=payload,
        domain=MemoryDomain.INTERNAL_MEMORY,
        invocation_id=f"internal-{operation.value}:{uuid4().hex}",
        operation=operation,
        receipt_status=_receipt_status(status),
        logical_refs=refs,
        matched_count=_matched_count(payload),
        fallback=str(payload.get("fallback", "") or status),
        truncated=bool(payload.get("truncated", False)),
    )


def _error_result(
    *,
    tool_name: str,
    operation: MemoryConsumptionOperation,
    fallback: str,
) -> ToolResult:
    return _tool_result(
        tool_name=tool_name,
        payload={
            "status": InternalMemoryResultStatus.INVALID_REQUEST.value,
            "fallback": fallback,
            "truncated": False,
        },
        operation=operation,
    )


def _bounded_payload(
    payload: dict[str, Any],
    *,
    max_bytes: int,
    item_paths: tuple[tuple[str, ...], ...],
) -> dict[str, Any]:
    bounded = json.loads(json.dumps(payload, ensure_ascii=False))
    while serialized_payload_size_bytes(bounded) > max_bytes:
        if drop_last_list_item(bounded, item_paths):
            bounded["truncated"] = True
            bounded["status"] = InternalMemoryResultStatus.PARTIAL.value
            continue
        body = bounded.get("body")
        if isinstance(body, str) and body:
            excess = serialized_payload_size_bytes(bounded) - max_bytes
            body_bytes = len(body.encode("utf-8"))
            bounded["body"], _ = truncate_utf8(
                body,
                max(0, body_bytes - max(256, excess)),
            )
            bounded["truncated"] = True
            bounded["status"] = InternalMemoryResultStatus.PARTIAL.value
            continue
        return {
            "status": InternalMemoryResultStatus.PARTIAL.value,
            "logical_ref": str(bounded.get("logical_ref", "") or "")[:240],
            "fallback": "tool_result_budget",
            "truncated": True,
        }
    return bounded


def _visible_logical_refs(payload: Mapping[str, Any]) -> tuple[str, ...]:
    hits = payload.get("hits")
    if isinstance(hits, list):
        return tuple(
            str(item.get("logical_ref", ""))
            for item in hits
            if isinstance(item, Mapping) and str(item.get("logical_ref", "")).strip()
        )[:32]
    logical_ref = str(payload.get("logical_ref", "") or "").strip()
    return (logical_ref,) if logical_ref and payload.get("body") else ()


def _matched_count(payload: Mapping[str, Any]) -> int:
    value = payload.get("matched_count", 0)
    return int(value) if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _receipt_status(status: str) -> MemoryConsumptionStatus:
    mapping = {
        InternalMemoryResultStatus.SUCCESS.value: MemoryConsumptionStatus.SUCCESS,
        InternalMemoryResultStatus.EMPTY.value: MemoryConsumptionStatus.EMPTY,
        InternalMemoryResultStatus.UNAVAILABLE.value: MemoryConsumptionStatus.UNAVAILABLE,
        InternalMemoryResultStatus.PARTIAL.value: MemoryConsumptionStatus.PARTIAL,
    }
    return mapping.get(status, MemoryConsumptionStatus.ERROR)
