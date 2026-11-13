from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


PatchKind = Literal['add', 'update', 'delete']
ChangeKind = Literal['add', 'update', 'delete', 'move']
RollbackStatus = Literal['not_needed', 'complete', 'partial', 'unsupported']
PatchFailureKind = Literal[
    'invalid_patch',
    'no_changes',
    'unsupported_filesystem',
    'invalid_target',
    'path_not_found',
    'path_already_exists',
    'context_mismatch',
    'concurrent_modification',
    'commit_failed',
    'unknown',
]


class PatchError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        line_number: int | None = None,
        operation_index: int | None = None,
        path: Path | None = None,
        failure_kind: PatchFailureKind = 'unknown',
        delta_exact: bool = True,
        rollback_status: RollbackStatus = 'not_needed',
        rollback_errors: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.line_number = line_number
        self.operation_index = operation_index
        self.path = path
        self.failure_kind = failure_kind
        self.delta_exact = delta_exact
        self.rollback_status = rollback_status
        self.rollback_errors = rollback_errors


@dataclass(frozen=True)
class UpdateChunk:
    change_context: str | None
    old_lines: tuple[str, ...]
    new_lines: tuple[str, ...]
    is_end_of_file: bool = False


@dataclass(frozen=True)
class PatchOperation:
    kind: PatchKind
    path: Path
    content: str = ''
    move_path: Path | None = None
    chunks: tuple[UpdateChunk, ...] = ()


@dataclass(frozen=True)
class PatchChange:
    kind: ChangeKind
    path: Path
    destination: Path | None
    old_content: str | None
    new_content: str | None
    destination_old_content: str | None = None


@dataclass(frozen=True)
class PatchOperationSummary:
    operation_count: int
    touched_paths: tuple[Path, ...]
    destructive_kinds: tuple[str, ...]


@dataclass(frozen=True)
class PatchCommitReport:
    changes: tuple[PatchChange, ...]
    operation_count: int
    touched_paths: tuple[Path, ...]
    delta_exact: bool = True
    rollback_status: RollbackStatus = 'not_needed'
    rollback_errors: tuple[str, ...] = ()


__all__ = [
    'ChangeKind',
    'PatchChange',
    'PatchCommitReport',
    'PatchError',
    'PatchFailureKind',
    'PatchKind',
    'PatchOperation',
    'PatchOperationSummary',
    'RollbackStatus',
    'UpdateChunk',
]
