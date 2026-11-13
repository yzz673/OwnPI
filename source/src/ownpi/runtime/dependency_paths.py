"""Canonical managed dependency paths owned by the Runtime state."""

from __future__ import annotations

from pathlib import Path


def canonical_managed_dependency_path(value: str | Path, *, label: str) -> str:
    """Normalize an absolute managed path without requiring its directory to exist."""
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    resolved = candidate.resolve(strict=False)
    canonical = str(resolved)
    if str(candidate) != canonical:
        raise ValueError(f"{label} must be canonical: {canonical}")
    return canonical


__all__ = ["canonical_managed_dependency_path"]
