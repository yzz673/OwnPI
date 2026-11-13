from __future__ import annotations

from pathlib import Path

from ownpi.tools.patching.models import PatchError, PatchOperation, UpdateChunk


_BEGIN_MARKER = '*** Begin Patch'
_END_MARKER = '*** End Patch'
_ENVIRONMENT_MARKER = '*** Environment ID:'
_OPERATION_MARKERS = (
    ('*** Add File:', 'add'),
    ('*** Delete File:', 'delete'),
    ('*** Update File:', 'update'),
)
_MOVE_MARKER = '*** Move to:'
_EOF_MARKER = '*** End of File'


def parse_patch(patch: str) -> tuple[PatchOperation, ...]:
    lines = patch.strip().splitlines()
    if not lines or lines[0].strip() != _BEGIN_MARKER:
        raise PatchError(
            "The first line of the patch must be '*** Begin Patch'",
            line_number=1,
            failure_kind='invalid_patch',
        )
    if len(lines) < 2 or lines[-1].strip() != _END_MARKER:
        raise PatchError(
            "The last line of the patch must be '*** End Patch'",
            line_number=len(lines) if lines else 1,
            failure_kind='invalid_patch',
        )

    operations: list[PatchOperation] = []
    index = 1
    environment_seen = False
    while index < len(lines) - 1:
        stripped = lines[index].strip()
        if not stripped:
            index += 1
            continue
        if stripped.startswith(_ENVIRONMENT_MARKER):
            if environment_seen or operations:
                raise _error('duplicate or misplaced environment ID', index)
            environment_id = stripped[len(_ENVIRONMENT_MARKER) :].strip()
            if not environment_id:
                raise _error('invalid environment ID', index)
            environment_seen = True
            index += 1
            continue

        operation_index = len(operations)
        marker = _operation_marker(stripped)
        if marker == 'add':
            operation, index = _parse_add(lines, index, operation_index)
        elif marker == 'delete':
            operation, index = _parse_delete(lines, index, operation_index)
        elif marker == 'update':
            operation, index = _parse_update(lines, index, operation_index)
        else:
            raise _error(
                'invalid hunk: expected Add, Delete, or Update File header',
                index,
                operation_index=operation_index,
            )
        operations.append(operation)
    return tuple(operations)


def patch_environment_id(patch: str) -> str:
    lines = patch.strip().splitlines()
    if len(lines) < 2:
        return ''
    stripped = lines[1].strip()
    if not stripped.startswith(_ENVIRONMENT_MARKER):
        return ''
    return stripped[len(_ENVIRONMENT_MARKER) :].strip()


def _parse_add(
    lines: list[str],
    index: int,
    operation_index: int,
) -> tuple[PatchOperation, int]:
    path = _parse_path(lines[index], '*** Add File:', index, operation_index)
    content: list[str] = []
    index += 1
    while index < len(lines) - 1 and _operation_marker(lines[index].strip()) is None:
        line = lines[index]
        if line.strip().startswith(_ENVIRONMENT_MARKER):
            raise _error('duplicate or misplaced environment ID', index, operation_index=operation_index)
        if not line.startswith('+'):
            raise _error(
                "invalid hunk: Add File lines must start with '+'",
                index,
                operation_index=operation_index,
                path=path,
            )
        content.append(line[1:])
        index += 1
    if not content:
        raise _error(
            f"invalid hunk: Add File '{path}' has no content",
            index - 1,
            operation_index=operation_index,
            path=path,
        )
    return PatchOperation(kind='add', path=path, content='\n'.join(content) + '\n'), index


def _parse_delete(
    lines: list[str],
    index: int,
    operation_index: int,
) -> tuple[PatchOperation, int]:
    path = _parse_path(lines[index], '*** Delete File:', index, operation_index)
    next_index = index + 1
    if next_index < len(lines) - 1 and _operation_marker(lines[next_index].strip()) is None:
        raise _error(
            'invalid hunk: Delete File does not accept body lines',
            next_index,
            operation_index=operation_index,
            path=path,
        )
    return PatchOperation(kind='delete', path=path), next_index


def _parse_update(
    lines: list[str],
    index: int,
    operation_index: int,
) -> tuple[PatchOperation, int]:
    start_index = index
    path = _parse_path(lines[index], '*** Update File:', index, operation_index)
    index += 1
    move_path: Path | None = None
    if index < len(lines) - 1 and lines[index].strip().startswith(_MOVE_MARKER):
        move_path = _parse_path(lines[index], _MOVE_MARKER, index, operation_index)
        index += 1

    chunks: list[UpdateChunk] = []
    context: str | None = None
    old_lines: list[str] = []
    new_lines: list[str] = []
    is_eof = False

    def finish_chunk() -> None:
        nonlocal context, old_lines, new_lines, is_eof
        if old_lines or new_lines:
            chunks.append(UpdateChunk(context, tuple(old_lines), tuple(new_lines), is_eof))
        context, old_lines, new_lines, is_eof = None, [], [], False

    while index < len(lines) - 1 and _operation_marker(lines[index].strip()) is None:
        line = lines[index]
        stripped = line.strip()
        if is_eof:
            if not stripped:
                index += 1
                continue
            raise _error(
                'invalid hunk: End of File must be the final update line',
                index,
                operation_index=operation_index,
                path=path,
            )
        if stripped == '@@' or stripped.startswith('@@ '):
            finish_chunk()
            context = stripped[3:] if stripped.startswith('@@ ') else None
        elif stripped == _EOF_MARKER:
            if not old_lines and not new_lines:
                raise _error(
                    'invalid hunk: End of File requires change lines',
                    index,
                    operation_index=operation_index,
                    path=path,
                )
            is_eof = True
        elif line.startswith(' '):
            old_lines.append(line[1:])
            new_lines.append(line[1:])
        elif line.startswith('+'):
            new_lines.append(line[1:])
        elif line.startswith('-'):
            old_lines.append(line[1:])
        elif line == '':
            old_lines.append('')
            new_lines.append('')
        else:
            raise _error(
                "invalid hunk: every update line must start with ' ', '+', '-', or '@@'",
                index,
                operation_index=operation_index,
                path=path,
            )
        index += 1
    finish_chunk()
    if not chunks and move_path is None:
        raise _error(
            f"invalid hunk: Update File '{path}' is empty",
            start_index,
            operation_index=operation_index,
            path=path,
        )
    return PatchOperation(kind='update', path=path, move_path=move_path, chunks=tuple(chunks)), index


def _parse_path(
    line: str,
    marker: str,
    index: int,
    operation_index: int,
) -> Path:
    stripped = line.strip()
    raw_path = stripped[len(marker) :].strip()
    if not raw_path:
        raise _error('invalid hunk: path is required', index, operation_index=operation_index)
    return Path(raw_path).expanduser()


def _operation_marker(stripped_line: str) -> str | None:
    for marker, kind in _OPERATION_MARKERS:
        if stripped_line.startswith(marker):
            return kind
    return None


def _error(
    message: str,
    zero_based_line: int,
    *,
    operation_index: int | None = None,
    path: Path | None = None,
) -> PatchError:
    line_number = zero_based_line + 1
    return PatchError(
        f'{message} at line {line_number}',
        line_number=line_number,
        operation_index=operation_index,
        path=path,
        failure_kind='invalid_patch',
    )


__all__ = ['parse_patch', 'patch_environment_id']
