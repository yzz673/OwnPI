from __future__ import annotations

from pathlib import Path


class PaperPathError(ValueError):
    """A paper-package path does not satisfy its declared containment contract."""

    def __init__(self, message: str, *, field_name: str, value: str) -> None:
        super().__init__(message)
        self.field_name = field_name
        self.value = value


def resolve_contained_path(
    root: Path,
    value: str,
    *,
    field_name: str,
    allow_absolute: bool = True,
    default: str = ".",
) -> Path:
    resolved_root = root.expanduser().resolve()
    raw_value = str(value or default).strip() or default
    raw = Path(raw_value).expanduser()
    if raw.is_absolute() and not allow_absolute:
        raise PaperPathError(
            f"{field_name} must be relative to its declared project root: {raw_value}",
            field_name=field_name,
            value=raw_value,
        )
    candidate = raw if raw.is_absolute() else resolved_root / raw
    resolved = candidate.resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise PaperPathError(
            f"{field_name} must stay inside its declared workspace: {raw_value}",
            field_name=field_name,
            value=raw_value,
        ) from exc
    return resolved


def relative_path(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


__all__ = ["PaperPathError", "relative_path", "resolve_contained_path"]
