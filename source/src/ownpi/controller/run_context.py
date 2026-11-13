from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class FreshStartRejectedError(RuntimeError):
    """Fresh execution was refused before acquiring execution ownership."""


@dataclass(frozen=True)
class PreparedRunContext:
    session_id: str
    task_id: str
    run_id: str
    run_dir: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, 'session_id', _require_text(self.session_id, 'session_id'))
        object.__setattr__(self, 'task_id', _require_text(self.task_id, 'task_id'))
        object.__setattr__(self, 'run_id', _require_text(self.run_id, 'run_id'))
        object.__setattr__(self, 'run_dir', Path(self.run_dir).expanduser().resolve())

    def scratchpad_payload(self) -> dict[str, str]:
        return {
            'session_id': self.session_id,
            'task_id': self.task_id,
            'run_id': self.run_id,
            'run_dir': str(self.run_dir),
        }


def _require_text(value: str, field_name: str) -> str:
    normalized = str(value or '').strip()
    if not normalized:
        raise ValueError(f'{field_name} is required')
    return normalized
