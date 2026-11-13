from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SessionRunPath:
    session_id: str
    task_id: str
    run_id: str
    run_dir: Path

    def to_dict(self) -> dict[str, str]:
        return {
            'session_id': self.session_id,
            'task_id': self.task_id,
            'run_id': self.run_id,
            'run_dir': str(self.run_dir),
        }


def parse_session_run_dir(path: Path) -> SessionRunPath | None:
    run_dir = Path(path).expanduser().resolve()
    parts = run_dir.parts
    if len(parts) < 6:
        return None
    if parts[-6] != 'sessions' or parts[-4] != 'tasks' or parts[-2] != 'runs':
        return None
    session_id = parts[-5].strip()
    task_id = parts[-3].strip()
    run_id = parts[-1].strip()
    if not session_id or not task_id or not run_id:
        return None
    return SessionRunPath(
        session_id=session_id,
        task_id=task_id,
        run_id=run_id,
        run_dir=run_dir,
    )


def is_session_run_dir(path: Path) -> bool:
    return parse_session_run_dir(path) is not None
