"""Model-facing read-only access to registered static semantic contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.limits.projection import (
    DEFAULT_SKILL_RESOURCE_PAGE_LINES,
    MAX_SKILL_RESOURCE_PAGE_LINES,
)
from ownpi.static_semantic_contracts.store import get_static_semantic_contract
from ownpi.tools.common import metadata_for_context

if TYPE_CHECKING:
    from ownpi.runtime.tools.definitions import ToolExecutionContext


async def run_contract_read(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> ToolResult:
    contract_id = str(arguments.get("contract_id") or "").strip()
    if not contract_id:
        return ToolResult(
            ok=False,
            tool_name="contract_read",
            error="contract_id is required",
        )
    pagination_error = _validate_pagination(arguments)
    if pagination_error:
        return ToolResult(
            ok=False,
            tool_name="contract_read",
            error=pagination_error,
        )
    try:
        contract = get_static_semantic_contract(contract_id)
    except (KeyError, FileNotFoundError, ValueError) as exc:
        return ToolResult(
            ok=False,
            tool_name="contract_read",
            error=str(exc),
        )

    lines = contract.content.splitlines()
    offset = int(arguments.get("offset", 1))
    limit = int(arguments.get("limit", DEFAULT_SKILL_RESOURCE_PAGE_LINES))
    column_offset = max(0, int(arguments.get("column_offset", 0)))
    start = min(offset - 1, len(lines))
    end = min(start + limit, len(lines))
    page_lines = lines[start:end]
    if page_lines and column_offset:
        page_lines[0] = page_lines[0][column_offset:]
    contents = "\n".join(page_lines)
    next_offset = end + 1 if end < len(lines) else None
    return ToolResult(
        ok=True,
        tool_name="contract_read",
        data={
            "contract_id": contract.contract_id,
            "resource_path": contract.resource_path,
            "contents": contents,
            "start_line": start + 1,
            "end_line": end,
            "total_lines": len(lines),
            "next_offset": next_offset,
            "requested_column_offset": column_offset,
            "column_offset": column_offset,
            "next_column_offset": None,
            "content_sha256": contract.content_sha256,
        },
        stdout=(
            f"contract_id={contract.contract_id}\n"
            f"lines={start + 1}-{end}/{len(lines)}\n"
            f"next_offset={next_offset}\n"
            f"content_sha256={contract.content_sha256}"
        ),
        metadata=metadata_for_context(
            context,
            contract_id=contract.contract_id,
            resource_path=contract.resource_path,
            start_line=start + 1,
            end_line=end,
            total_lines=len(lines),
            next_offset=next_offset,
            requested_column_offset=column_offset,
            column_offset=column_offset,
            next_column_offset=None,
            content_sha256=contract.content_sha256,
        ),
    )


def _validate_pagination(arguments: dict[str, Any]) -> str:
    offset = arguments.get("offset", 1)
    limit = arguments.get("limit", DEFAULT_SKILL_RESOURCE_PAGE_LINES)
    column_offset = arguments.get("column_offset", 0)
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 1:
        return "offset must be a positive integer (1-based line number)"
    if isinstance(column_offset, bool) or not isinstance(column_offset, int) or column_offset < 0:
        return "column_offset must be a non-negative integer"
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or limit < 1
        or limit > MAX_SKILL_RESOURCE_PAGE_LINES
    ):
        return f"limit must be an integer from 1 to {MAX_SKILL_RESOURCE_PAGE_LINES}"
    return ""


__all__ = ["run_contract_read"]
