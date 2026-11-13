from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import stat
from typing import Any

from ownpi.runtime.execution.registry import ExecutionRegistry
from ownpi.runtime.execution.state import ExecutionRecord
from ownpi.utils.io import write_text_atomic


_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r'(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|password|secret|token)'
    r'(\s*[:=]\s*)([^\s,;&]+)'
)
_BEARER_PATTERN = re.compile(r'(?i)\bbearer\s+[^\s,;&]+')
_URL_CREDENTIAL_PATTERN = re.compile(r'([a-z][a-z0-9+.-]*://)([^/@\s]+)@', re.IGNORECASE)


@dataclass(frozen=True)
class ExecutionBoardRef:
    path: str
    material_revision: int
    resource_kind: str = 'execution_board'

    def to_dict(self) -> dict[str, Any]:
        return {
            'resource_kind': self.resource_kind,
            'path': self.path,
            'material_revision': self.material_revision,
            'reader_tool': 'file_reader',
            'read_only': True,
        }


class ExecutionBoardTargetError(ValueError):
    """The optional board cannot be projected safely at its current path."""


class ExecutionBoardProjector:
    def __init__(self, *, run_dir: Path) -> None:
        self._run_dir = Path(run_dir).resolve()
        self._path = execution_board_path(run_dir=self._run_dir)
        self._opened = False

    @property
    def path(self) -> Path:
        return self._path

    def open(self, registry: ExecutionRegistry) -> ExecutionBoardRef:
        if not self._opened:
            _validate_board_target(path=self._path, run_dir=self._run_dir)
            self._opened = True
        return self._ref(registry)

    def record_new_execution(
        self,
        registry: ExecutionRegistry,
    ) -> ExecutionBoardRef:
        self.open(registry)
        self._write(registry)
        return self._ref(registry)

    def current_ref(self, registry: ExecutionRegistry) -> ExecutionBoardRef | None:
        _validate_board_target(path=self._path, run_dir=self._run_dir)
        if not self._path.exists():
            return None
        return self.open(registry)

    def is_canonical(self, registry: ExecutionRegistry, content: bytes) -> bool:
        return content == self._render(registry).encode('utf-8')

    def _write(self, registry: ExecutionRegistry) -> None:
        _validate_board_target(path=self._path, run_dir=self._run_dir)
        write_text_atomic(self._path, self._render(registry))

    def _render(self, registry: ExecutionRegistry) -> str:
        return render_execution_board(registry)

    def _ref(self, registry: ExecutionRegistry) -> ExecutionBoardRef:
        return ExecutionBoardRef(
            path=str(self._path.resolve()),
            material_revision=registry.material_revision,
        )


def execution_board_path(
    *,
    run_dir: Path,
) -> Path:
    return Path(run_dir).resolve() / 'execution_board.md'


def render_execution_board(
    registry: ExecutionRegistry,
) -> str:
    ordered = sorted(
        registry.records,
        key=lambda item: (item.started_at, item.launch_order),
        reverse=True,
    )
    lines = [
        '# Execution Board',
        '',
        'This file is an index of managed long-running executions.',
        'Each entry records only the execution ID, a short summary, and its start time.',
        'Use `execution_monitor` with the corresponding `execution_id` for a status overview',
        'and recent output. Use `execution_logs` only when you need a longer or more detailed',
        'stdout/stderr log tail than the monitor overview provides. Use `execution_kill` only',
        'to terminate a managed execution that should stop before it finishes.',
        'Use `write_stdin` with `execution_id` to continue interacting after resume.',
        '',
        '## Executions',
    ]
    if not ordered:
        lines.extend(['', 'No executions have been registered for this run.'])
    for record in ordered:
        lines.extend(_render_record(record))
    return _board_text(lines)


def _render_record(record: ExecutionRecord) -> list[str]:
    lines = [
        '',
        f'- execution_id: `{_safe_text(record.execution_id, 240)}`',
        f'  summary: `{_safe_text(record.summary, 240)}`',
        f'  started_at: `{_safe_text(record.started_at, 80)}`',
    ]
    return lines


def _safe_text(value: str, limit: int) -> str:
    normalized = ' '.join(str(value or '').replace('`', "'").split())
    redacted = _URL_CREDENTIAL_PATTERN.sub(r'\1[REDACTED]@', normalized)
    redacted = _BEARER_PATTERN.sub('Bearer [REDACTED]', redacted)
    redacted = _SECRET_ASSIGNMENT_PATTERN.sub(r'\1\2[REDACTED]', redacted)
    return redacted[:limit]


def _board_text(lines: list[str]) -> str:
    return '\n'.join(lines).rstrip() + '\n'


def _validate_board_target(*, path: Path, run_dir: Path) -> None:
    candidate = path.absolute()
    candidate.relative_to(run_dir)
    try:
        file_stat = candidate.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(file_stat.st_mode):
        raise ExecutionBoardTargetError('execution board must not be a symbolic link')
    if not stat.S_ISREG(file_stat.st_mode):
        raise ExecutionBoardTargetError('execution board must be a regular file')
    if file_stat.st_nlink != 1:
        raise ExecutionBoardTargetError('execution board must have exactly one hard link')


__all__ = [
    'ExecutionBoardProjector',
    'ExecutionBoardRef',
    'execution_board_path',
    'render_execution_board',
]
