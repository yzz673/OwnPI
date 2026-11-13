from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CanonicalTextRef:
    """Durable reference to one authoritative UTF-8 text file."""

    path: str
    content_sha256: str

    @classmethod
    def from_path(
        cls,
        path: Path,
        *,
        label: str,
        require_nonempty: bool = True,
    ) -> 'CanonicalTextRef':
        resolved = path.expanduser().resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f'{label} file is missing: {resolved}')
        content = resolved.read_text(encoding='utf-8')
        if require_nonempty and not content.strip():
            raise ValueError(f'{label} file is empty: {resolved}')
        return cls(
            path=str(resolved),
            content_sha256=sha256(content.encode('utf-8')).hexdigest(),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, label: str) -> 'CanonicalTextRef':
        path = str(data.get('path', '') or '').strip()
        content_sha256 = str(data.get('content_sha256', '') or '').strip()
        if not path or not content_sha256:
            raise ValueError(f'{label} reference is incomplete')
        return cls(path=path, content_sha256=content_sha256)

    def read(self, *, label: str, require_nonempty: bool = True) -> str:
        resolved = Path(self.path).expanduser().resolve()
        if str(resolved) != self.path:
            raise ValueError(f'{label} reference path is not canonical: {self.path}')
        if not resolved.is_file():
            raise FileNotFoundError(f'{label} file is missing: {resolved}')
        content = resolved.read_text(encoding='utf-8')
        if require_nonempty and not content.strip():
            raise ValueError(f'{label} file is empty: {resolved}')
        actual_sha256 = sha256(content.encode('utf-8')).hexdigest()
        if actual_sha256 != self.content_sha256:
            raise ValueError(f'{label} content hash mismatch')
        return content

    def to_dict(self) -> dict[str, str]:
        return {
            'path': self.path,
            'content_sha256': self.content_sha256,
        }


@dataclass(frozen=True)
class RuntimeTaskContext:
    """Canonical task-scoped semantic sources owned by RuntimeState."""

    raw_user_message_ref: CanonicalTextRef
    task_spec_ref: CanonicalTextRef | None = None

    @classmethod
    def from_paths(
        cls,
        *,
        raw_user_message_path: Path,
        task_spec_path: Path | None,
    ) -> 'RuntimeTaskContext':
        return cls(
            raw_user_message_ref=CanonicalTextRef.from_path(
                raw_user_message_path,
                label='raw user message',
            ),
            task_spec_ref=(
                CanonicalTextRef.from_path(task_spec_path, label='task specification')
                if task_spec_path is not None
                else None
            ),
        )

    def read_raw_user_message(self) -> str:
        return self.raw_user_message_ref.read(label='raw user message')

    def read_task_spec(self) -> str:
        if self.task_spec_ref is None:
            return ''
        return self.task_spec_ref.read(label='task specification')


def read_runtime_raw_user_message(state: Any) -> str:
    return state.task_context.read_raw_user_message()


def read_runtime_task_spec(state: Any) -> str:
    return state.task_context.read_task_spec()


__all__ = [
    'CanonicalTextRef',
    'RuntimeTaskContext',
    'read_runtime_raw_user_message',
    'read_runtime_task_spec',
]
