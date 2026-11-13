from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


def build_read_file_state_entry(
    *,
    path: Path,
    stage: str,
    attempt: int,
    continuation_index: int,
    total_lines: int | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
    line_window_truncated: bool = False,
    prompt_excerpt_truncated: bool = False,
    visible_char_count: int = 0,
    query_chain_id: str = '',
    query_chain_depth: int = 0,
    source: str = 'file_reader',
    observed_mtime_ns: int | None = None,
    observed_size: int | None = None,
    observed_content_fingerprint: str | None = None,
) -> dict[str, Any]:
    resolved = path.resolve()
    if observed_mtime_ns is None or observed_size is None or not observed_content_fingerprint:
        stat = resolved.stat()
        text = resolved.read_text(encoding='utf-8', errors='ignore')
        mtime_ns = int(getattr(stat, 'st_mtime_ns', int(stat.st_mtime * 1_000_000_000)))
        size = int(stat.st_size or 0)
        content_fingerprint = hashlib.sha256(text.encode('utf-8')).hexdigest()
        inferred_line_count = len(text.splitlines())
    else:
        mtime_ns = int(observed_mtime_ns)
        size = int(observed_size)
        content_fingerprint = str(observed_content_fingerprint)
        inferred_line_count = 0
    line_count = int(total_lines if total_lines is not None else inferred_line_count)
    read_start = int(start_line if start_line is not None else 1)
    read_end = int(end_line if end_line is not None else line_count)
    read_kind = _classify_read_kind(
        total_lines=line_count,
        start_line=read_start,
        end_line=read_end,
        line_window_truncated=line_window_truncated,
        prompt_excerpt_truncated=prompt_excerpt_truncated,
    )
    return {
        'path': str(resolved),
        'abs_path': str(resolved),
        'stage': str(stage or '').strip(),
        'attempt': int(attempt or 0),
        'continuation_index': int(continuation_index or 0),
        'query_chain_id': str(query_chain_id or '').strip(),
        'query_chain_depth': int(query_chain_depth or 0),
        'source': str(source or 'file_reader'),
        'mtime_ns': mtime_ns,
        'size': size,
        'content_fingerprint': content_fingerprint,
        'total_lines': line_count,
        'start_line': read_start,
        'end_line': read_end,
        'coverage_intervals': [[read_start, read_end]] if read_end >= read_start else [],
        'line_window_truncated': bool(line_window_truncated),
        'prompt_excerpt_truncated': bool(prompt_excerpt_truncated),
        'visible_char_count': int(visible_char_count or 0),
        'read_kind': read_kind,
        'is_full_read': read_kind == 'full_read',
        'validation_status': 'valid' if read_kind == 'full_read' else 'partial_read',
        'last_validation_error': '',
    }


def build_read_file_state_entry_from_content(
    *,
    path: Path,
    content: str,
    stage: str,
    attempt: int,
    continuation_index: int,
    query_chain_id: str = '',
    query_chain_depth: int = 0,
    source: str = 'workspace_mutation',
) -> dict[str, Any]:
    resolved = path.resolve()
    stat = resolved.stat()
    line_count = len(content.splitlines())
    return {
        'path': str(resolved),
        'abs_path': str(resolved),
        'stage': str(stage or '').strip(),
        'attempt': int(attempt or 0),
        'continuation_index': int(continuation_index or 0),
        'query_chain_id': str(query_chain_id or '').strip(),
        'query_chain_depth': int(query_chain_depth or 0),
        'source': str(source or 'workspace_mutation'),
        'mtime_ns': int(getattr(stat, 'st_mtime_ns', int(stat.st_mtime * 1_000_000_000))),
        'size': int(stat.st_size or 0),
        'content_fingerprint': hashlib.sha256(content.encode('utf-8')).hexdigest(),
        'total_lines': line_count,
        'start_line': 1,
        'end_line': line_count,
        'coverage_intervals': [[1, line_count]] if line_count > 0 else [],
        'line_window_truncated': False,
        'prompt_excerpt_truncated': False,
        'visible_char_count': len(content),
        'read_kind': 'full_read',
        'is_full_read': True,
        'validation_status': 'valid',
        'last_validation_error': '',
    }


def remember_read_file_state(
    *,
    state_store: Any,
    entry: dict[str, Any],
) -> None:
    if state_store is None or not isinstance(entry, dict):
        return
    tool_state = getattr(getattr(state_store, 'state', None), 'tool_state', None)
    if tool_state is None:
        return
    path = str(entry.get('path', '') or '').strip()
    if not path:
        return
    previous = tool_state.read_file_state.get(path)
    remembered = _merge_read_file_state_entries(previous, entry)
    tool_state.read_file_state.pop(path, None)
    tool_state.read_file_state[path] = remembered
    tool_state.read_file_state_history.append(dict(remembered))
    if len(tool_state.read_file_state_history) > 128:
        tool_state.read_file_state_history = tool_state.read_file_state_history[-128:]
    if hasattr(state_store, '_touch'):
        state_store._touch()


def validate_read_file_state(
    *,
    state_store: Any,
    path: Path,
) -> tuple[bool, str | None, dict[str, Any] | None]:
    if state_store is None:
        return False, 'read state unavailable without state_store', None
    tool_state = getattr(getattr(state_store, 'state', None), 'tool_state', None)
    if tool_state is None:
        return False, 'read state unavailable', None
    resolved = str(path.resolve())
    read_state = dict(getattr(tool_state, 'read_file_state', {}) or {})
    entry = read_state.get(resolved)
    if entry is None:
        return False, 'file overwrite requires a current fingerprint receipt from file_reader', None
    if str(entry.get('read_kind', '') or '').strip() != 'full_read':
        message = 'read state partial because prior file_reader did not expose the full file; reread it before overwriting'
        _record_validation_status(
            state_store=state_store,
            path=resolved,
            status='partial_read',
            error=message,
        )
        return False, message, entry
    if not path.exists() or not path.is_file():
        return False, f'file not found: {path}', entry
    stat = path.stat()
    current_mtime_ns = int(getattr(stat, 'st_mtime_ns', int(stat.st_mtime * 1_000_000_000)))
    current_size = int(stat.st_size or 0)
    current_text = path.read_text(encoding='utf-8', errors='ignore')
    current_fingerprint = hashlib.sha256(current_text.encode('utf-8')).hexdigest()
    recorded_mtime_ns = int(entry.get('mtime_ns', 0) or 0)
    recorded_size = int(entry.get('size', 0) or 0)
    recorded_fingerprint = str(entry.get('content_fingerprint', '') or '')
    if not recorded_fingerprint:
        message = 'file overwrite requires a read receipt with a content fingerprint'
        _record_validation_status(state_store=state_store, path=resolved, status='invalid', error=message)
        return False, message, entry
    if recorded_mtime_ns and current_mtime_ns != recorded_mtime_ns:
        message = 'read state stale because file modified after read'
        _record_validation_status(state_store=state_store, path=resolved, status='stale', error=message)
        return False, message, entry
    if recorded_size and current_size != recorded_size:
        message = 'read state stale because file size changed after read'
        _record_validation_status(state_store=state_store, path=resolved, status='stale', error=message)
        return False, message, entry
    if recorded_fingerprint and current_fingerprint != recorded_fingerprint:
        message = 'read state stale because file fingerprint changed after read'
        _record_validation_status(state_store=state_store, path=resolved, status='stale', error=message)
        return False, message, entry
    _record_validation_status(state_store=state_store, path=resolved, status='valid', error='')
    return True, None, entry


def summarize_read_file_state(
    read_file_state: dict[str, dict[str, Any]] | None,
    *,
    current_stage: str = '',
    current_attempt: int = 0,
    current_query_chain_id: str = '',
    limit: int = 6,
) -> dict[str, Any]:
    entries = [dict(item) for item in (read_file_state or {}).values() if isinstance(item, dict)]
    entries.sort(
        key=lambda item: (
            int(item.get('attempt', 0) or 0),
            int(item.get('continuation_index', 0) or 0),
            str(item.get('path', '') or ''),
        )
    )
    stage_entries = [
        item
        for item in entries
        if str(item.get('stage', '') or '') == str(current_stage or '')
        and int(item.get('attempt', 0) or 0) == int(current_attempt or 0)
    ]
    chain_entries = [
        item
        for item in stage_entries
        if str(item.get('query_chain_id', '') or '').strip() == str(current_query_chain_id or '').strip()
    ]
    recent_entries = chain_entries or stage_entries or entries
    source_breakdown: dict[str, int] = {}
    read_kind_breakdown: dict[str, int] = {}
    validation_status_breakdown: dict[str, int] = {}
    for item in entries:
        source = str(item.get('source', '') or 'unknown').strip() or 'unknown'
        source_breakdown[source] = source_breakdown.get(source, 0) + 1
        read_kind = str(item.get('read_kind', '') or 'unknown').strip() or 'unknown'
        read_kind_breakdown[read_kind] = read_kind_breakdown.get(read_kind, 0) + 1
        validation_status = str(item.get('validation_status', '') or 'unknown').strip() or 'unknown'
        validation_status_breakdown[validation_status] = validation_status_breakdown.get(validation_status, 0) + 1
    return {
        'entry_count': len(entries),
        'current_stage_attempt_entry_count': len(stage_entries),
        'current_query_chain_entry_count': len(chain_entries),
        'source_breakdown': dict(sorted(source_breakdown.items(), key=lambda pair: (-pair[1], pair[0]))),
        'read_kind_breakdown': dict(sorted(read_kind_breakdown.items(), key=lambda pair: (-pair[1], pair[0]))),
        'validation_status_breakdown': dict(
            sorted(validation_status_breakdown.items(), key=lambda pair: (-pair[1], pair[0]))
        ),
        'full_read_count': int(read_kind_breakdown.get('full_read', 0) or 0),
        'partial_read_count': int(read_kind_breakdown.get('partial_read', 0) or 0),
        'stale_entry_count': int(validation_status_breakdown.get('stale', 0) or 0),
        'recent_entries': recent_entries[-limit:],
    }


def _classify_read_kind(
    *,
    total_lines: int,
    start_line: int,
    end_line: int,
    line_window_truncated: bool,
    prompt_excerpt_truncated: bool,
) -> str:
    if line_window_truncated or prompt_excerpt_truncated:
        return 'partial_read'
    if total_lines <= 0:
        return 'full_read' if start_line == 1 and end_line == 0 else 'partial_read'
    if start_line > 1:
        return 'partial_read'
    if end_line < total_lines:
        return 'partial_read'
    return 'full_read'


def _merge_read_file_state_entries(
    previous: Any,
    current: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(current)
    merged['coverage_intervals'] = _coverage_intervals(current)
    if not isinstance(previous, dict) or not _same_file_revision(previous, current):
        return _refresh_coverage_classification(merged)
    intervals = [*_coverage_intervals(previous), *_coverage_intervals(current)]
    merged['coverage_intervals'] = _merge_intervals(intervals)
    return _refresh_coverage_classification(merged)


def _same_file_revision(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return all(
        left.get(field) == right.get(field)
        for field in ('content_fingerprint', 'size', 'mtime_ns', 'total_lines')
    )


def _coverage_intervals(entry: dict[str, Any]) -> list[list[int]]:
    raw_intervals = entry.get('coverage_intervals')
    if not isinstance(raw_intervals, list):
        start = int(entry.get('start_line', 1) or 1)
        end = int(entry.get('end_line', 0) or 0)
        return [[start, end]] if end >= start else []
    intervals: list[list[int]] = []
    for item in raw_intervals:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        start, end = int(item[0]), int(item[1])
        if end >= start:
            intervals.append([start, end])
    return intervals


def _merge_intervals(intervals: list[list[int]]) -> list[list[int]]:
    merged: list[list[int]] = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1] + 1:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return merged


def _refresh_coverage_classification(entry: dict[str, Any]) -> dict[str, Any]:
    intervals = _coverage_intervals(entry)
    total_lines = int(entry.get('total_lines', 0) or 0)
    is_full = (total_lines == 0 and not intervals) or (total_lines > 0 and intervals == [[1, total_lines]])
    entry['start_line'] = intervals[0][0] if intervals else 1
    entry['end_line'] = intervals[-1][1] if intervals else 0
    entry['read_kind'] = 'full_read' if is_full else 'partial_read'
    entry['is_full_read'] = is_full
    entry['validation_status'] = 'valid' if is_full else 'partial_read'
    entry['last_validation_error'] = ''
    return entry


def _record_validation_status(
    *,
    state_store: Any,
    path: str,
    status: str,
    error: str,
) -> None:
    tool_state = getattr(getattr(state_store, 'state', None), 'tool_state', None)
    if tool_state is None:
        return
    read_file_state = getattr(tool_state, 'read_file_state', None)
    if not isinstance(read_file_state, dict):
        return
    entry = read_file_state.get(path)
    if not isinstance(entry, dict):
        return
    entry['validation_status'] = str(status or 'unknown').strip() or 'unknown'
    entry['last_validation_error'] = str(error or '')
    read_file_state[path] = entry
    if hasattr(state_store, '_touch'):
        state_store._touch()
