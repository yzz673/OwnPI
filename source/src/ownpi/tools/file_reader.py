from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.limits.projection import (
    DEFAULT_FILE_READER_LINES,
    MAX_FILE_READER_LINES,
)
from ownpi.tools.common import resolve_file_tool_path

if TYPE_CHECKING:
    from ownpi.runtime.tools.definitions import ToolExecutionContext


_BINARY_SAMPLE_BYTES = 4096


async def run_file_reader(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
    try:
        path = resolve_file_tool_path(
            context,
            arguments.get('file_path'),
            tool_name='file_reader',
            access='read',
        )
    except (OSError, ValueError) as exc:
        return ToolResult(ok=False, tool_name='file_reader', error=str(exc))
    requested_offset = _parse_int(arguments.get('offset'), default=1)
    offset = max(requested_offset, 1)
    requested_column_offset = _parse_int(arguments.get('column_offset'), default=0)
    column_offset = max(requested_column_offset, 0)
    requested_limit = _parse_int(arguments.get('limit'), default=DEFAULT_FILE_READER_LINES)
    effective_limit = max(1, min(requested_limit, MAX_FILE_READER_LINES))
    if not path.exists():
        return ToolResult(
            ok=False,
            tool_name='file_reader',
            error=f'file does not exist: {path}',
            stdout=f'path={path}\nstatus=missing',
            metadata=_base_metadata(context=context, path=path),
        )
    if path.is_dir():
        return ToolResult(
            ok=False,
            tool_name='file_reader',
            error=f'path is a directory, not a file: {path}',
            stdout=f'path={path}\nstatus=directory',
            metadata=_base_metadata(context=context, path=path),
        )
    if _looks_binary(path):
        return ToolResult(
            ok=False,
            tool_name='file_reader',
            error=f'binary file cannot be rendered as text: {path}',
            stdout=f'path={path}\nstatus=binary',
            metadata=_base_metadata(context=context, path=path),
        )
    text = path.read_text(encoding='utf-8', errors='ignore')
    stat = path.stat()
    mtime_ns = int(getattr(stat, 'st_mtime_ns', int(stat.st_mtime * 1_000_000_000)))
    size = int(stat.st_size or 0)
    content_fingerprint = hashlib.sha256(text.encode('utf-8')).hexdigest()
    lines = text.splitlines()
    start_index = min(offset - 1, len(lines))
    end_index = min(start_index + effective_limit, len(lines))
    excerpt_lines = lines[start_index:end_index]
    if excerpt_lines and column_offset:
        excerpt_lines[0] = excerpt_lines[0][column_offset:]
    rendered_excerpt = _render_excerpt(
        excerpt_lines=excerpt_lines,
        start_line=start_index + 1,
        include_line_numbers=True,
    )
    excerpt = rendered_excerpt
    line_window_truncated = end_index < len(lines)
    execution_has_more = end_index < len(lines)
    execution_next_offset = end_index + 1 if execution_has_more else None
    summary = (
        f'path={path}\n'
        f'requested_limit={requested_limit}\n'
        f'effective_limit={effective_limit}\n'
        f'execution_window={start_index + 1}-{max(end_index, start_index)} '
        f'of {len(lines)}\n'
        f'has_more={execution_has_more}\n'
        f'next_offset={execution_next_offset}'
    ).strip()
    result = ToolResult(
        ok=True,
        tool_name='file_reader',
        data={
            'file_path': str(path),
            'total_lines': len(lines),
            'requested_offset': requested_offset,
            'requested_column_offset': requested_column_offset,
            'offset': start_index + 1,
            'column_offset': column_offset,
            'start_line': start_index + 1,
            'end_line': end_index,
            'requested_limit': requested_limit,
            'effective_limit': effective_limit,
            'truncated': line_window_truncated,
            'excerpt_truncated': False,
            'has_more': execution_has_more,
            'next_offset': execution_next_offset,
            'next_column_offset': None,
            'visible_char_count': len(excerpt),
            'read_kind': (
                'full_read'
                if start_index == 0 and column_offset == 0 and end_index == len(lines)
                else 'partial_read'
            ),
            'excerpt': excerpt,
            'mtime_ns': mtime_ns,
            'size': size,
            'content_fingerprint': content_fingerprint,
        },
        stdout=summary,
        metadata=_base_metadata(
            context=context,
            path=path,
            mtime_ns=mtime_ns,
            size=size,
            content_fingerprint=content_fingerprint,
            total_lines=len(lines),
            requested_offset=requested_offset,
            requested_column_offset=requested_column_offset,
            start_line=start_index + 1,
            column_offset=column_offset,
            end_line=end_index,
            requested_limit=requested_limit,
            effective_limit=effective_limit,
            line_window_truncated=line_window_truncated,
            prompt_excerpt_truncated=False,
            execution_has_more=execution_has_more,
            execution_next_offset=execution_next_offset,
            next_column_offset=None,
            visible_char_count=len(excerpt),
            read_kind=(
                'full_read'
                if start_index == 0 and column_offset == 0 and end_index == len(lines)
                else 'partial_read'
            ),
            read_state_source='file_reader',
        ),
    )
    return result


def _parse_int(value: Any, *, default: int) -> int:
    try:
        return int(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _render_excerpt(
    *,
    excerpt_lines: list[str],
    start_line: int,
    include_line_numbers: bool,
) -> str:
    rendered: list[str] = []
    for index, line in enumerate(excerpt_lines, start=start_line):
        if include_line_numbers:
            rendered.append(f'{index:>5}: {line}')
        else:
            rendered.append(line)
    return '\n'.join(rendered)


def _looks_binary(path: Path) -> bool:
    try:
        sample = path.read_bytes()[:_BINARY_SAMPLE_BYTES]
    except OSError:
        return False
    return b'\x00' in sample


def _base_metadata(*, context: ToolExecutionContext, path: Path, **extra: Any) -> dict[str, Any]:
    return {
        'path': str(path),
        'stage': context.stage.value,
        'attempt': context.attempt,
        'continuation_index': context.continuation_index,
        'query_chain_id': str(getattr(context, 'query_chain_id', '') or '').strip(),
        'query_chain_depth': int(getattr(context, 'query_chain_depth', 0) or 0),
        **extra,
    }
