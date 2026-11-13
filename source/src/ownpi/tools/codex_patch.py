from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ownpi.tools.patching.filesystem import (
    LocalPatchFileSystem,
    PatchExecutionAuthority,
    PatchFileSystem,
    PatchPolicyError,
    PathIdentity,
)
from ownpi.tools.patching.matcher import seek_sequence
from ownpi.tools.patching.models import (
    PatchChange,
    PatchCommitReport,
    PatchError,
    PatchOperation,
    UpdateChunk,
)
from ownpi.tools.patching.parser import parse_patch, patch_environment_id as patch_environment_id
from ownpi.tools.patching.summary import summarize_operations as summarize_operations


@dataclass(frozen=True)
class _PreparedPatch:
    changes: tuple[PatchChange, ...]
    initial: dict[Path, str | None]
    identities: dict[Path, PathIdentity]
    final: dict[Path, str | None]
    final_modes: dict[Path, int | None]


def apply_patch(
    patch: str,
    *,
    cwd: Path,
    resolve_path: Callable[[Path], Path] | None = None,
) -> tuple[PatchChange, ...]:
    return apply_patch_report(
        patch,
        cwd=cwd,
        resolve_path=resolve_path,
    ).changes


def apply_patch_report(
    patch: str,
    *,
    cwd: Path,
    resolve_path: Callable[[Path], Path] | None = None,
    filesystem: PatchFileSystem | None = None,
    authority: PatchExecutionAuthority | None = None,
) -> PatchCommitReport:
    patch_filesystem = filesystem or LocalPatchFileSystem(authority=authority)
    if not patch_filesystem.supports_atomic_replace:
        raise PatchError(
            'patch filesystem must support atomic replace',
            failure_kind='unsupported_filesystem',
        )
    operations = parse_patch(patch)
    if not operations:
        raise PatchError('No files were modified', failure_kind='no_changes')
    resolver = resolve_path or (lambda path: patch_filesystem.canonical_path(path, cwd=cwd))
    prepared = _preflight(operations, resolver, patch_filesystem)
    _commit(prepared, patch_filesystem)
    summary = summarize_operations(operations)
    return PatchCommitReport(
        changes=prepared.changes,
        operation_count=summary.operation_count,
        touched_paths=_affected_paths(prepared.changes),
    )


def _preflight(
    operations: tuple[PatchOperation, ...],
    resolve_path: Callable[[Path], Path],
    filesystem: PatchFileSystem,
) -> _PreparedPatch:
    initial: dict[Path, str | None] = {}
    identities: dict[Path, PathIdentity] = {}
    current: dict[Path, str | None] = {}
    current_modes: dict[Path, int | None] = {}
    changes: list[PatchChange] = []

    def read(path: Path) -> str | None:
        if path in current:
            return current[path]
        identity = filesystem.identity(path)
        if identity.kind == 'directory':
            raise PatchError(
                f'path is a directory: {path}',
                path=path,
                failure_kind='invalid_target',
            )
        if identity.kind == 'symlink':
            raise PatchError(
                f'patch_apply refuses symbolic link paths: {path}',
                path=path,
                failure_kind='invalid_target',
            )
        if identity.kind == 'other':
            raise PatchError(
                f'unsupported filesystem entry: {path}',
                path=path,
                failure_kind='invalid_target',
            )
        content = filesystem.read_text(path) if identity.exists else None
        initial[path] = content
        identities[path] = identity
        current[path] = content
        current_modes[path] = identity.mode
        return content

    for operation in operations:
        source = resolve_path(operation.path)
        before = read(source)
        if operation.kind == 'add':
            if before is not None:
                raise PatchError(
                    f'Add File target already exists: {source}. Read it and use Update File instead.',
                    path=source,
                    failure_kind='path_already_exists',
                )
            current[source] = operation.content
            changes.append(PatchChange('add', source, None, before, operation.content))
            continue
        if before is None:
            raise PatchError(
                f'file not found: {source}',
                path=source,
                failure_kind='path_not_found',
            )
        if operation.kind == 'delete':
            current[source] = None
            changes.append(PatchChange('delete', source, None, before, None))
            continue
        after = _apply_chunks(before, source, operation.chunks)
        if operation.move_path is None:
            current[source] = after
            changes.append(PatchChange('update', source, None, before, after))
            continue
        destination = resolve_path(operation.move_path)
        destination_before = read(destination)
        source_mode = current_modes[source]
        current[source] = None
        current_modes[source] = None
        current[destination] = after
        current_modes[destination] = source_mode
        changes.append(PatchChange('move', source, destination, before, after, destination_before))
    return _PreparedPatch(tuple(changes), initial, identities, current, current_modes)


def _apply_chunks(content: str, path: Path, chunks: tuple[UpdateChunk, ...]) -> str:
    lines = content.split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    replacements: list[tuple[int, int, tuple[str, ...]]] = []
    start = 0
    for chunk in chunks:
        if chunk.change_context is not None:
            context_index = seek_sequence(lines, (chunk.change_context,), start, False)
            if context_index is None:
                raise PatchError(
                    f"Failed to find context '{chunk.change_context}' in {path}",
                    path=path,
                    failure_kind='context_mismatch',
                )
            start = context_index + 1
        if not chunk.old_lines:
            insertion = len(lines)
            replacements.append((insertion, 0, chunk.new_lines))
            continue
        old_lines = chunk.old_lines
        new_lines = chunk.new_lines
        found = seek_sequence(lines, old_lines, start, chunk.is_end_of_file)
        if found is None and old_lines and old_lines[-1] == '':
            old_lines = old_lines[:-1]
            if new_lines and new_lines[-1] == '':
                new_lines = new_lines[:-1]
            found = seek_sequence(lines, old_lines, start, chunk.is_end_of_file)
        if found is None:
            expected_lines = '\n'.join(chunk.old_lines)
            raise PatchError(
                f'Failed to find expected lines in {path}:\n{expected_lines}',
                path=path,
                failure_kind='context_mismatch',
            )
        replacements.append((found, len(old_lines), new_lines))
        start = found + len(old_lines)
    for start, old_count, new_lines in reversed(replacements):
        lines[start : start + old_count] = new_lines
    return '\n'.join(lines) + '\n'


def _commit(prepared: _PreparedPatch, filesystem: PatchFileSystem) -> None:
    _validate_snapshot(prepared.initial, prepared.identities, filesystem)
    mutated: dict[Path, PathIdentity] = {}
    try:
        for path, content in prepared.final.items():
            if content is not None:
                mutated[path] = filesystem.replace_text(
                    path,
                    content,
                    expected=prepared.identities[path],
                    mode=prepared.final_modes[path],
                )
        for path, content in prepared.final.items():
            if content is None:
                mutated[path] = filesystem.remove(path, expected=prepared.identities[path])
    except (OSError, PatchError) as exc:
        rollback_errors = _rollback(prepared, filesystem, mutated)
        rollback_complete = not rollback_errors and _snapshot_matches(
            prepared.initial,
            prepared.identities,
            filesystem,
        )
        raise PatchError(
            f'failed to commit patch: {exc}',
            failure_kind=(
                'permission_denied'
                if isinstance(exc, PatchPolicyError)
                else 'os_permission_denied'
                if isinstance(exc, PermissionError)
                else 'commit_failed'
            ),
            delta_exact=_delta_is_observable(prepared.initial, filesystem),
            rollback_status='complete' if rollback_complete else 'partial',
            rollback_errors=rollback_errors,
        ) from exc


def _validate_snapshot(
    initial: dict[Path, str | None],
    identities: dict[Path, PathIdentity],
    filesystem: PatchFileSystem,
) -> None:
    for path, expected in initial.items():
        if filesystem.identity(path) != identities[path]:
            raise PatchError(
                f'path identity changed after patch preflight: {path}',
                path=path,
                failure_kind='concurrent_modification',
            )
        actual = filesystem.read_text(path) if identities[path].exists else None
        if actual != expected:
            raise PatchError(
                f'path changed after patch preflight: {path}',
                path=path,
                failure_kind='concurrent_modification',
            )


def _rollback(
    prepared: _PreparedPatch,
    filesystem: PatchFileSystem,
    mutated: dict[Path, PathIdentity],
) -> tuple[str, ...]:
    errors: list[str] = []
    for path in reversed(mutated):
        content = prepared.initial[path]
        current_identity = mutated[path]
        try:
            if content is None:
                filesystem.remove(path, expected=current_identity)
            else:
                filesystem.replace_text(
                    path,
                    content,
                    expected=current_identity,
                    mode=prepared.identities[path].mode,
                )
        except (OSError, PatchError) as exc:
            if len(errors) < 8:
                errors.append(f'{path}: {str(exc)[:240]}')
    return tuple(errors)


def _snapshot_matches(
    initial: dict[Path, str | None],
    identities: dict[Path, PathIdentity],
    filesystem: PatchFileSystem,
) -> bool:
    try:
        _validate_snapshot(initial, identities, filesystem)
    except (OSError, PatchError):
        return False
    return True


def _delta_is_observable(initial: dict[Path, str | None], filesystem: PatchFileSystem) -> bool:
    try:
        for path in initial:
            identity = filesystem.identity(path)
            if identity.exists and identity.kind != 'directory':
                filesystem.read_text(path)
    except OSError:
        return False
    return True


def _affected_paths(changes: tuple[PatchChange, ...]) -> tuple[Path, ...]:
    paths: list[Path] = []
    for change in changes:
        for path in (change.path, change.destination):
            if path is not None and path not in paths:
                paths.append(path)
    return tuple(paths)
