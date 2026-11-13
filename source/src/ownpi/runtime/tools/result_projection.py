"""Tool-specific model delivery formats, separate from native history assembly."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.limits.tool_output import TruncationPolicy, truncate_text
from ownpi.runtime.tools.content_pages import (
    build_content_delivery_page_projection, build_file_reader_page_projection,
    content_page_source, reproject_content_page,
)
from ownpi.runtime.tools.result_body import ordinary_body_with_diagnostic
from ownpi.runtime.tools.result_projection_utils import FilePageProjection


@dataclass(frozen=True)
class ToolResultProjection:
    body: str
    preserve_output: bool = False
    page: FilePageProjection | None = None
    source: dict[str, object] | None = None
    diagnostic: str = ''


def project_tool_result(
    result: ToolResult, *, policy: TruncationPolicy,
    page_byte_limit: Callable[[], int],
) -> ToolResultProjection:
    if result.ok and result.tool_name in {'file_reader', 'skills_read', 'contract_read'}:
        builder = (
            build_file_reader_page_projection if result.tool_name == 'file_reader'
            else build_content_delivery_page_projection
        )
        body, page = builder(tool_result=result, byte_limit=page_byte_limit())
        return ToolResultProjection(body, True, page, content_page_source(result))
    if result.tool_name == 'run_subagents' and (result.data or {}).get('feedback_delivery_version') == 1:
        from ownpi.subagent.result_projection import format_batch_result

        body, _ = format_batch_result(result, policy)
        return ToolResultProjection(body, True)
    # Keep live content in its original order; do not repeat its text as stdout.
    body_result = result
    if result.stdout and any(
        item.type == 'input_text' and item.text == result.stdout
        for item in result.content_items
    ):
        body_result = replace(result, stdout='')
    body, diagnostic = ordinary_body_with_diagnostic(body_result)
    return ToolResultProjection(
        body, result.ok and result.tool_name == 'skills_list', diagnostic=diagnostic,
    )


def reduce_tool_result_projection(
    *, tool_name: str, text: str, byte_limit: int,
    page: FilePageProjection | None, source: dict[str, object] | None,
) -> tuple[str, FilePageProjection | None]:
    if page is not None:
        if source is None:
            return text, page
        return reproject_content_page(tool_name=tool_name, data=source, byte_limit=byte_limit)
    if tool_name == 'run_subagents':
        from ownpi.subagent.result_projection import reduce_batch_result

        return reduce_batch_result(text, byte_limit), None
    if tool_name == 'skills_list':
        from ownpi.skills.result_projection import reduce_skills_list

        return reduce_skills_list(text, byte_limit), None
    return truncate_text(text, TruncationPolicy.bytes(byte_limit)), None
