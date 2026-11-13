from __future__ import annotations

import re

from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.tools.projection_errors import ToolResultProjectionError
from ownpi.runtime.tools.result_projection_utils import FilePageProjection, project_file_page, truncate_output_text


def build_content_delivery_page_projection(
    *,
    tool_result: ToolResult,
    byte_limit: int,
    raw_result_ref: str | None = None,
) -> tuple[str, FilePageProjection]:
    """Project a complete content page without replacing its body with a summary."""

    data = dict(tool_result.data or {})
    contents = str(data.get('contents', '') or '')
    start_line = int(data.get('start_line', 1))
    end_line = int(data.get('end_line', 0) or 0)
    count = max(0, end_line - start_line + 1)
    source_lines = contents.split('\n') if count else []
    lines = list(enumerate(source_lines, start=start_line))
    total_lines = int(data.get('total_lines', 0) or 0)
    if byte_limit <= 0:
        raise ToolResultProjectionError('content delivery projection requires a positive model-visible budget')
    page_budget = max(96, byte_limit - 900)
    page = project_file_page(
        lines,
        total_lines=total_lines,
        max_output_bytes=page_budget,
    )
    projected = _render_content_delivery_projection(
        tool_result=tool_result,
        data=data,
        page=page,
    )
    for _ in range(4):
        overflow = len(projected.encode('utf-8')) - byte_limit
        if overflow <= 0:
            break
        page_budget = max(96, page_budget - overflow)
        page = project_file_page(
            lines,
            total_lines=total_lines,
            max_output_bytes=page_budget,
        )
        projected = _render_content_delivery_projection(
            tool_result=tool_result,
            data=data,
            page=page,
        )
    if len(projected.encode('utf-8')) > byte_limit:
        raise ToolResultProjectionError('content delivery projection metadata exceeds model-visible byte limit')
    return projected, page


def _render_content_delivery_projection(
    *,
    tool_result: ToolResult,
    data: dict[str, object],
    page: FilePageProjection,
) -> str:
    start_line = int(data.get('start_line', 1))
    end_line = int(data.get('end_line', 0) or 0)
    total_lines = int(data.get('total_lines', 0) or 0)
    visible_range = (
        f'{page.visible_start}-{page.visible_end}'
        if page.visible_start is not None and page.visible_end is not None
        else 'none'
    )
    next_offset = page.next_offset if page.next_offset is not None else 'none'
    next_column = (
        int(data.get('column_offset', 0) or 0) + page.next_column
        if page.next_column is not None
        else 'none'
    )
    resource = str(
        data.get('resource')
        or data.get('resource_path')
        or data.get('file_path')
        or ''
    )
    cursor_name = 'next_cursor' if tool_result.tool_name == 'skills_read' else 'next_offset'
    next_cursor = (
        page.next_offset if page.next_offset is not None else 'none'
    ) if tool_result.tool_name == 'skills_read' else next_offset
    lines = [
        f'{tool_result.tool_name} (ok)',
        f'resource: {resource}',
        f"package: {data.get('package', '')}",
        *([f"skill_root: {data.get('skill_root', '')}",
           f"resource_kind: {data.get('resource_kind', '')}"]
          if tool_result.tool_name == 'skills_read' else []),
        f"contract_id: {data.get('contract_id', '')}",
        f"content_sha256: {data.get('content_sha256', '')}",
        f'execution_range: {start_line}-{end_line} of {total_lines}',
        f'visible_range: {visible_range}',
        f'visible_line_count: {page.visible_line_count}',
        f'has_more: {str(page.has_more).lower()}',
        f'{cursor_name}: {next_cursor}',
        f'next_column_offset: {next_column}',
        f'cursor_kind: {page.cursor_kind}',
        f'content_truncated: {str(page.content_truncated).lower()}',
        f'projection_truncated: {str(page.projection_truncated).lower()}',
        'content_begin',
    ]
    if page.text:
        lines.append(page.text)
    lines.append('content_end')
    return '\n'.join(lines)


def build_file_reader_page_projection(
    *,
    tool_result: ToolResult,
    byte_limit: int,
    raw_result_ref: str | None = None,
) -> tuple[str, FilePageProjection]:
    """Return a complete file page and its exact model-visible line window."""

    data = dict(tool_result.data or {})
    excerpt_lines = _numbered_file_lines(
        str(data.get('excerpt', '') or ''),
        fallback_start=int(data.get('start_line', data.get('offset', 1)) or 1),
        expected_line_count=max(
            0,
            int(data.get('end_line', 0) or 0)
            - int(data.get('start_line', data.get('offset', 1)) or 1)
            + 1,
        ),
    )
    total_lines = int(data.get('total_lines', 0) or 0)
    page_budget = max(96, byte_limit - 1_100)
    page = project_file_page(
        excerpt_lines,
        total_lines=total_lines,
        max_output_bytes=page_budget,
    )
    projected = _render_file_reader_projection(
        data=data,
        raw_result_ref=raw_result_ref,
        page=page,
    )
    for _ in range(3):
        overflow = len(projected.encode('utf-8')) - byte_limit
        if overflow <= 0:
            break
        page_budget = max(96, page_budget - overflow)
        page = project_file_page(
            excerpt_lines,
            total_lines=total_lines,
            max_output_bytes=page_budget,
        )
        projected = _render_file_reader_projection(
            data=data,
            raw_result_ref=raw_result_ref,
            page=page,
        )
    if len(projected.encode('utf-8')) > byte_limit:
        raise ToolResultProjectionError('file_reader projection metadata exceeds model-visible byte limit')
    return projected, page


def content_page_source(tool_result: ToolResult) -> dict[str, object]:
    """Retain only the snapshot fields needed to reproject or confirm a page."""
    keys = (
        'excerpt', 'contents', 'file_path', 'resource', 'resource_path', 'package',
        'contract_id', 'content_sha256', 'content_fingerprint', 'start_line',
        'end_line', 'total_lines', 'offset', 'column_offset', 'requested_offset',
        'requested_limit', 'effective_limit', 'mtime_ns', 'size',
        'skill_root', 'resource_kind',
    )
    return {key: tool_result.data[key] for key in keys if key in tool_result.data}


def reproject_content_page(
    *, tool_name: str, data: dict[str, object], byte_limit: int,
) -> tuple[str, FilePageProjection]:
    result = ToolResult(ok=True, tool_name=tool_name, data=data)
    # Render the irreducible metadata plus a progressing partial line first.
    if tool_name == 'file_reader':
        lines = _numbered_file_lines(
            str(data.get('excerpt') or ''),
            fallback_start=int(data.get('start_line', data.get('offset', 1)) or 1),
            expected_line_count=max(0, int(data.get('end_line', 0) or 0)
                                    - int(data.get('start_line', data.get('offset', 1)) or 1) + 1),
        )
        page = project_file_page(lines, total_lines=int(data.get('total_lines', 0)), max_output_bytes=96)
        minimum = _render_file_reader_projection(data=data, raw_result_ref=None, page=page)
        builder = build_file_reader_page_projection
    else:
        start = int(data.get('start_line', 1))
        lines = list(enumerate(str(data.get('contents') or '').split('\n'), start)) if int(data.get('end_line', 0)) >= start else []
        page = project_file_page(lines, total_lines=int(data.get('total_lines', 0)), max_output_bytes=96)
        minimum = _render_content_delivery_projection(tool_result=result, data=data, page=page)
        builder = build_content_delivery_page_projection
    return builder(tool_result=result, byte_limit=max(byte_limit, len(minimum.encode('utf-8'))))


def _numbered_file_lines(
    excerpt: str,
    *,
    fallback_start: int,
    expected_line_count: int,
) -> list[tuple[int, str]]:
    rendered_lines = excerpt.splitlines()
    if len(rendered_lines) < expected_line_count:
        rendered_lines.extend('' for _ in range(expected_line_count - len(rendered_lines)))
    numbered: list[tuple[int, str]] = []
    for index, rendered_line in enumerate(rendered_lines, start=max(1, fallback_start)):
        match = re.match(r'^\s*(\d+):(?: (.*))?$', rendered_line)
        if match is None:
            numbered.append((index, rendered_line))
            continue
        numbered.append((int(match.group(1)), str(match.group(2) or '')))
    return numbered


def _render_file_reader_projection(
    *,
    data: dict[str, object],
    raw_result_ref: str | None,
    page: FilePageProjection,
) -> str:
    start_line = int(data.get('start_line', data.get('offset', 1)) or 1)
    end_line = int(data.get('end_line', 0) or 0)
    execution_range = f'{start_line}-{end_line}' if end_line >= start_line else 'none'
    visible_range = (
        f'{page.visible_start}-{page.visible_end}'
        if page.visible_start is not None and page.visible_end is not None
        else 'none'
    )
    next_column = (
        int(data.get('column_offset', 0) or 0) + page.next_column
        if page.next_column is not None
        else 'none'
    )
    path = str(data.get('file_path', '') or '')
    result_ref = truncate_output_text(
        str(raw_result_ref or ''),
        max_output_bytes=720,
        position='middle',
    ).text
    lines = [
        'file_reader (ok)',
        f'file_path: {path}',
        f"content_fingerprint: {data.get('content_fingerprint', '')}",
        f"requested_offset: {int(data.get('requested_offset', 1) or 1)}",
        f"effective_offset: {int(data.get('offset', 1) or 1)}",
        f"requested_limit: {int(data.get('requested_limit', 0) or 0)}",
        f"effective_limit: {int(data.get('effective_limit', 0) or 0)}",
        f"execution_range: {execution_range} of {int(data.get('total_lines', 0) or 0)}",
        f'visible_range: {visible_range}',
        f'visible_line_count: {page.visible_line_count}',
        f'has_more: {str(page.has_more).lower()}',
        f"next_offset: {page.next_offset if page.next_offset is not None else 'none'}",
        f'next_column_offset: {next_column}',
        f'cursor_kind: {page.cursor_kind}',
        f"partial_line: {page.partial_line if page.partial_line is not None else 'none'}",
        f'content_truncated: {str(page.content_truncated).lower()}',
        f'projection_truncated: {str(page.projection_truncated).lower()}',
        f'raw_result_ref: {result_ref}',
        'source_excerpt_begin',
    ]
    if page.text:
        lines.append(page.text)
    lines.append('source_excerpt_end')
    return '\n'.join(lines)
