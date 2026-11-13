from __future__ import annotations

from pathlib import Path

from ownpi.tools.patching.models import PatchOperation, PatchOperationSummary


def summarize_operations(
    operations: tuple[PatchOperation, ...],
) -> PatchOperationSummary:
    paths: list[Path] = []
    destructive_kinds: list[str] = []

    def add_path(path: Path | None) -> None:
        if path is not None and path not in paths:
            paths.append(path)

    def add_kind(kind: str) -> None:
        if kind not in destructive_kinds:
            destructive_kinds.append(kind)

    for operation in operations:
        add_path(operation.path)
        add_path(operation.move_path)
        if operation.kind == 'add':
            add_kind('potential_overwrite_existing_file')
            continue
        if operation.kind == 'delete':
            add_kind('delete_file')
            continue
        if operation.move_path is not None:
            add_kind('remove_source_path')
            add_kind('potential_overwrite_destination')
        if any(len(chunk.new_lines) < len(chunk.old_lines) for chunk in operation.chunks):
            add_kind('delete_content')

    return PatchOperationSummary(
        operation_count=len(operations),
        touched_paths=tuple(paths),
        destructive_kinds=tuple(destructive_kinds),
    )


__all__ = ['summarize_operations']
