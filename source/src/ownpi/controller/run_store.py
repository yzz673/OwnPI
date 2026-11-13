from pathlib import Path

from ownpi.runtime.sessions import parse_session_run_dir


def list_run_dirs(output_dir: Path) -> list[Path]:
    if not output_dir.exists():
        return []
    sessions_dir = output_dir / "sessions"
    if not sessions_dir.exists():
        return []
    return sorted(
        [
            path.expanduser().resolve()
            for path in sessions_dir.glob("*/tasks/*/runs/*")
            if path.is_dir() and parse_session_run_dir(path) is not None
        ],
        reverse=True,
    )
