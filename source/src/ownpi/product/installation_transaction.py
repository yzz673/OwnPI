from __future__ import annotations

import fcntl
import os
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class PathBackup:
    path: Path
    backup: Path | None


def backup_paths(paths: tuple[Path, ...]) -> tuple[PathBackup, ...]:
    backups: list[PathBackup] = []
    try:
        for path in paths:
            backup = path.with_name(f".{path.name}.previous.{os.getpid()}")
            remove_path(backup)
            if path.exists() or path.is_symlink():
                path.rename(backup)
                backups.append(PathBackup(path, backup))
            else:
                backups.append(PathBackup(path, None))
    except BaseException:
        restore_paths(tuple(backups))
        raise
    return tuple(backups)


def restore_paths(backups: tuple[PathBackup, ...]) -> None:
    for item in reversed(backups):
        remove_path(item.path)
        if item.backup is not None and (item.backup.exists() or item.backup.is_symlink()):
            item.backup.rename(item.path)


def discard_backups(backups: tuple[PathBackup, ...]) -> None:
    for item in backups:
        if item.backup is not None:
            remove_path(item.backup)


def read_link(path: Path) -> str | None:
    if path.is_symlink():
        return os.readlink(path)
    if path.exists():
        raise RuntimeError(f"managed path is not a symbolic link: {path}")
    return None


def replace_link(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    staging = link.with_name(f".{link.name}.staging")
    staging.unlink(missing_ok=True)
    staging.symlink_to(target)
    staging.replace(link)


def restore_link(path: Path, target: str | None) -> None:
    if path.is_symlink():
        path.unlink()
    elif path.exists():
        raise RuntimeError(f"cannot restore managed symbolic link over existing path: {path}")
    if target is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)


def restore_file(path: Path, content: bytes | None) -> None:
    path.with_suffix(f"{path.suffix}.staging").unlink(missing_ok=True)
    if content is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def restore_marker(path: Path, existed: bool) -> None:
    if existed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    else:
        path.unlink(missing_ok=True)


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


@contextmanager
def install_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another OwnPI installation is already running") from exc
        yield


__all__ = [
    "PathBackup",
    "backup_paths",
    "discard_backups",
    "install_lock",
    "read_link",
    "remove_path",
    "replace_link",
    "restore_file",
    "restore_link",
    "restore_marker",
    "restore_paths",
]
