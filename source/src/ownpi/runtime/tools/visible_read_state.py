from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.read_file_state import build_read_file_state_entry, remember_read_file_state
from ownpi.runtime.tools.result_projection_utils import FilePageProjection

if TYPE_CHECKING:
    from ownpi.runtime.tools.runtime import ToolRuntimeContext


def remember_visible_file_page(
    *,
    context: ToolRuntimeContext,
    tool_result: ToolResult,
    page: FilePageProjection | None,
) -> None:
    if tool_result.tool_name != "file_reader" or not tool_result.ok or page is None:
        return
    data = tool_result.data if isinstance(tool_result.data, dict) else {}
    raw_path = str(data.get("file_path", "") or "").strip()
    if not raw_path:
        return
    execution_start = int(data.get("start_line", data.get("offset", 1)) or 1)
    visible_start = (
        page.visible_start if page.visible_start is not None else execution_start
    )
    visible_end = (
        page.visible_end if page.visible_end is not None else max(0, visible_start - 1)
    )
    if int(data.get('column_offset', 0) or 0) > 0:
        # A continuation exposes only a suffix, not the entire first line.
        visible_start += 1
    remember_read_file_state(
        state_store=context.state_store,
        entry=build_read_file_state_entry(
            path=Path(raw_path),
            stage=context.stage.value,
            attempt=context.attempt,
            continuation_index=context.continuation_index,
            total_lines=int(data.get("total_lines", 0) or 0),
            start_line=visible_start,
            end_line=visible_end,
            line_window_truncated=page.has_more,
            prompt_excerpt_truncated=page.projection_truncated,
            visible_char_count=len(page.text),
            query_chain_id=context.query_chain_id,
            query_chain_depth=context.query_chain_depth,
            source="file_reader_projection",
            observed_mtime_ns=int(data.get("mtime_ns", 0) or 0),
            observed_size=int(data.get("size", 0) or 0),
            observed_content_fingerprint=str(data.get("content_fingerprint", "") or ""),
        ),
    )
