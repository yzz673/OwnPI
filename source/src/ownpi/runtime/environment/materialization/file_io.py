from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
from typing import Iterator

from ownpi.runtime.permissions.models import EffectivePermissions, FileSystemAccessMode
from ownpi.runtime.permissions.policy import require_file_access


@dataclass(frozen=True)
class MaterializationFileIO:
    """Host file operations; authorization remains owned by permissions.policy.

    Model tool callers must supply their effective permissions. None is reserved
    for direct, trusted library callers without a model permission context.
    """

    permissions: EffectivePermissions | None = None

    def read(self, path: Path) -> None:
        if self.permissions is not None:
            require_file_access(self.permissions, path, FileSystemAccessMode.READ)

    def write(self, path: Path) -> None:
        if self.permissions is not None:
            require_file_access(self.permissions, path, FileSystemAccessMode.WRITE)

    def mkdir(self, path: Path, *, mode: int = 0o777, exist_ok: bool = True) -> None:
        self.write(path)
        missing = []
        parent = path
        while not parent.exists():
            missing.append(parent)
            parent = parent.parent
        for candidate in reversed(missing):
            self.write(candidate)
        path.mkdir(parents=True, exist_ok=exist_ok, mode=mode)

    def walk(self, root: Path) -> Iterator[Path]:
        self.read(root)
        for path in root.iterdir():
            self.read(path)
            yield path
            if not path.is_symlink() and path.is_dir():
                yield from self.walk(path)

    def replace(self, source: Path, target: Path) -> None:
        self.check_replace(source, target)
        os.replace(source, target)

    def check_replace(self, source: Path, target: Path) -> None:
        self.write(source)
        self.write(target)
        if source.is_dir() and not source.is_symlink():
            # Renaming a directory also moves its children into the destination policy.
            for path in self.walk(source):
                self.write(path)
                self.write(target / path.relative_to(source))
        if target.is_dir() and not target.is_symlink():
            for path in self.walk(target):
                self.write(path)

    def remove_tree(self, root: Path) -> None:
        self.write(root)
        if not root.exists():
            return
        for path in self.walk(root):
            self.write(path)
        shutil.rmtree(root)

    def unlink(self, path: Path) -> None:
        self.write(path)
        path.unlink(missing_ok=True)

    def read_text(self, path: Path) -> str:
        self.read(path)
        return path.read_text(encoding='utf-8')
