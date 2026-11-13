"""Resolve the working directory independently of application and session storage."""

from pathlib import Path


def select_workspace(path: Path | None = None, *, create: bool = False) -> Path:
    root = (path or Path.cwd()).expanduser().resolve()
    if create:
        # A new project must be new. Never silently reuse or empty an old directory.
        root.mkdir(parents=True, exist_ok=False)
    elif not root.is_dir():
        raise ValueError(f"Project directory does not exist: {root}")
    return root
