from __future__ import annotations

from pathlib import Path


def canonical_project_workspace_root(value: str | Path, *, label: str) -> str:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        raise ValueError(f'{label} must be an absolute path')
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f'{label} does not exist: {candidate}') from exc
    if not resolved.is_dir():
        raise NotADirectoryError(f'{label} is not a directory: {resolved}')
    canonical = str(resolved)
    if str(candidate) != canonical:
        raise ValueError(f'{label} must be canonical: {canonical}')
    return canonical


__all__ = ['canonical_project_workspace_root']
