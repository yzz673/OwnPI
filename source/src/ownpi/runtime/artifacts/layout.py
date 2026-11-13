from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
from typing import Any

RUN_ARTIFACT_LAYOUT_VERSION = 'session_workdir_artifact_v1'


@dataclass(frozen=True)
class RunArtifactScope:
    session_id: str
    task_id: str
    run_id: str

    @property
    def suffix_parts(self) -> tuple[str, ...]:
        return (
            'sessions',
            clean_path_part(self.session_id),
            'tasks',
            clean_path_part(self.task_id),
            'runs',
            clean_path_part(self.run_id),
        )

    @property
    def tmux_session(self) -> str:
        raw = f'na_{self.task_id}_{self.run_id}'
        return clean_tmux_name(raw)[:80] or 'na_remote_run'


@dataclass(frozen=True)
class RunArtifactLayout:
    session_id: str = ''
    task_id: str = ''
    run_id: str = ''
    session_root: Path | None = None
    run_dir: Path | None = None
    agent_workdir: Path | None = None
    remote_agent_workdir: str = ''
    artifact_root: Path | None = None
    layout_version: str = RUN_ARTIFACT_LAYOUT_VERSION

    def to_locations(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            'layout_version': self.layout_version,
            'session_root': _path_text(self.session_root),
            'run_dir': _path_text(self.run_dir),
            'agent_workdir': _path_text(self.agent_workdir),
            'remote_agent_workdir': self.remote_agent_workdir,
            'artifact_root': _path_text(self.artifact_root),
        }
        return {key: value for key, value in payload.items() if value not in ('', None)}


def resolve_run_artifact_layout(
    *,
    run_dir: Path | str | None = None,
    output_dir: Path | str | None = None,
    project_workspace_root: Path | str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    run_id: str | None = None,
) -> RunArtifactLayout:
    resolved_run_dir = _resolve_path(run_dir)
    inferred = infer_run_artifact_scope_from_run_dir(resolved_run_dir)
    resolved_session_id = str(session_id or inferred.get('session_id') or '').strip()
    resolved_task_id = str(task_id or inferred.get('task_id') or '').strip()
    resolved_run_id = str(run_id or inferred.get('run_id') or '').strip()
    session_root = _session_root_from_run_dir(resolved_run_dir)
    agent_workdir = _resolve_path(project_workspace_root)
    del output_dir
    return RunArtifactLayout(
        session_id=resolved_session_id,
        task_id=resolved_task_id,
        run_id=resolved_run_id,
        session_root=session_root,
        run_dir=resolved_run_dir,
        agent_workdir=agent_workdir,
        remote_agent_workdir='',
        artifact_root=None,
    )


def infer_run_artifact_scope_from_run_dir(run_dir: Path | str | None) -> dict[str, str]:
    if run_dir is None:
        return {}
    parts = Path(run_dir).expanduser().resolve(strict=False).parts
    try:
        session_index = parts.index('sessions')
        task_index = parts.index('tasks', session_index + 1)
        run_index = parts.index('runs', task_index + 1)
    except ValueError:
        return {}
    if session_index + 1 >= len(parts) or task_index + 1 >= len(parts) or run_index + 1 >= len(parts):
        return {}
    return {
        'session_id': parts[session_index + 1],
        'task_id': parts[task_index + 1],
        'run_id': parts[run_index + 1],
    }


def scope_local_path(raw_root: str, *, scope: RunArtifactScope) -> str:
    root = str(raw_root or '').strip()
    if not root:
        return ''
    suffix = scope.suffix_parts
    path = Path(root).expanduser()
    base_parts = _strip_run_scope_suffix(path.parts)
    if base_parts != path.parts:
        path = Path(*base_parts)
    return str(path.joinpath(*suffix))


def scope_posix_path(raw_root: str, *, scope: RunArtifactScope) -> str:
    root = str(raw_root or '').strip().rstrip('/')
    if not root:
        return ''
    suffix = scope.suffix_parts
    path = PurePosixPath(root)
    base_parts = _strip_run_scope_suffix(path.parts)
    if base_parts != path.parts:
        path = PurePosixPath(*base_parts)
    return str(path.joinpath(*suffix))


def clean_path_part(value: str) -> str:
    normalized = re.sub(r'[^A-Za-z0-9_.-]+', '_', str(value or '').strip())
    return normalized.strip('._') or 'unknown'


def clean_tmux_name(value: str) -> str:
    normalized = re.sub(r'[^A-Za-z0-9_.-]+', '_', str(value or '').strip())
    return normalized.strip('._')


def _session_root_from_run_dir(run_dir: Path | None) -> Path | None:
    if run_dir is None:
        return None
    parts = run_dir.parts
    try:
        session_index = parts.index('sessions')
        task_index = parts.index('tasks', session_index + 1)
    except ValueError:
        return None
    if task_index <= session_index + 1:
        return None
    return Path(*parts[: session_index + 2])


def _resolve_path(value: Path | str | None) -> Path | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    path = Path(text).expanduser()
    if path.is_absolute():
        return path
    return (Path.cwd() / path).absolute()


def _endswith_parts(parts: tuple[str, ...], suffix: tuple[str, ...]) -> bool:
    if len(parts) < len(suffix):
        return False
    return tuple(parts[-len(suffix):]) == suffix


def _strip_run_scope_suffix(parts: tuple[str, ...]) -> tuple[str, ...]:
    if len(parts) < 6:
        return parts
    suffix = parts[-6:]
    if suffix[0] != 'sessions' or suffix[2] != 'tasks' or suffix[4] != 'runs':
        return parts
    return parts[:-6]


def _path_text(path: Path | None) -> str:
    return str(path) if path is not None else ''
