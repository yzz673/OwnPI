from __future__ import annotations

import hashlib
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ownpi.tools.patching.models import PatchError
from ownpi.runtime.permissions.models import EffectivePermissions, FileSystemAccessMode
from ownpi.runtime.permissions.policy import FileSystemPolicyMatcher, lexical_absolute_path


@dataclass(frozen=True)
class PathIdentity:
    exists: bool
    kind: str
    content_fingerprint: str | None
    mode: int | None
    symlink_target: str | None


class PatchPolicyError(PermissionError):
    """A filesystem operation rejected by the effective OwnPI policy."""


@dataclass(frozen=True)
class PatchExecutionAuthority:
    """The effective permission binding for one patch transaction."""

    permissions: EffectivePermissions
    cwd: Path


class PatchFileSystem(Protocol):
    supports_atomic_replace: bool

    def canonical_path(self, path: Path, *, cwd: Path) -> Path: ...

    def identity(self, path: Path) -> PathIdentity: ...

    def read_text(self, path: Path) -> str: ...

    def replace_text(
        self,
        path: Path,
        content: str,
        *,
        expected: PathIdentity,
        mode: int | None,
    ) -> PathIdentity: ...

    def remove(self, path: Path, *, expected: PathIdentity) -> PathIdentity: ...

    def move(
        self,
        source: Path,
        destination: Path,
        *,
        expected_source: PathIdentity,
        expected_destination: PathIdentity,
    ) -> tuple[PathIdentity, PathIdentity]: ...


class LocalPatchFileSystem:
    supports_atomic_replace = True

    def __init__(self, authority: PatchExecutionAuthority | None = None) -> None:
        self._authority = authority

    def canonical_path(self, path: Path, *, cwd: Path) -> Path:
        expanded = path.expanduser()
        candidate = expanded if expanded.is_absolute() else cwd / expanded
        return Path(os.path.abspath(os.path.normpath(candidate)))

    def identity(self, path: Path) -> PathIdentity:
        self._require_access(path, FileSystemAccessMode.READ)
        try:
            path_stat = path.lstat()
        except FileNotFoundError:
            return PathIdentity(False, 'missing', None, None, None)
        mode = stat.S_IMODE(path_stat.st_mode)
        if stat.S_ISDIR(path_stat.st_mode):
            return PathIdentity(True, 'directory', None, mode, None)
        if stat.S_ISLNK(path_stat.st_mode):
            return PathIdentity(True, 'symlink', None, mode, os.readlink(path))
        if stat.S_ISREG(path_stat.st_mode):
            return PathIdentity(True, 'file', self._content_fingerprint(path), mode, None)
        return PathIdentity(True, 'other', None, mode, None)

    def read_text(self, path: Path) -> str:
        self._require_access(path, FileSystemAccessMode.READ)
        return path.read_text(encoding='utf-8')

    def replace_text(
        self,
        path: Path,
        content: str,
        *,
        expected: PathIdentity,
        mode: int | None,
    ) -> PathIdentity:
        self._require_access(path, FileSystemAccessMode.WRITE)
        self._require_identity(path, expected)
        self._ensure_parent_directory(path.parent)
        temporary = path.parent / f'.{path.name}.patch-{uuid.uuid4().hex}.tmp'
        self._require_access(
            temporary,
            FileSystemAccessMode.WRITE,
            inherited_path=path,
        )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(temporary, flags, 0o666 if mode is None else mode)
        committed_mode: int | None = None
        try:
            if mode is not None:
                os.fchmod(descriptor, mode)
            with os.fdopen(descriptor, 'w', encoding='utf-8', newline='') as stream:
                descriptor = -1
                stream.write(content)
                stream.flush()
                committed_mode = stat.S_IMODE(os.fstat(stream.fileno()).st_mode)
            self._require_identity(path, expected)
            self._require_access(path, FileSystemAccessMode.WRITE)
            os.replace(temporary, path)
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                self._require_access(temporary, FileSystemAccessMode.WRITE)
                temporary.unlink()
            except OSError:
                pass
            raise
        return PathIdentity(
            True,
            'file',
            hashlib.sha256(content.encode('utf-8')).hexdigest(),
            committed_mode,
            None,
        )

    def remove(self, path: Path, *, expected: PathIdentity) -> PathIdentity:
        self._require_access(path, FileSystemAccessMode.WRITE)
        self._require_identity(path, expected)
        if expected.exists:
            path.unlink()
        return PathIdentity(False, 'missing', None, None, None)

    def move(
        self,
        source: Path,
        destination: Path,
        *,
        expected_source: PathIdentity,
        expected_destination: PathIdentity,
    ) -> tuple[PathIdentity, PathIdentity]:
        self._require_access(source, FileSystemAccessMode.WRITE)
        self._require_access(destination, FileSystemAccessMode.WRITE)
        self._require_identity(source, expected_source)
        self._require_identity(destination, expected_destination)
        self._ensure_parent_directory(destination.parent)
        self._require_identity(source, expected_source)
        self._require_identity(destination, expected_destination)
        os.replace(source, destination)
        return (
            PathIdentity(False, 'missing', None, None, None),
            expected_source,
        )

    def _require_identity(self, path: Path, expected: PathIdentity) -> None:
        if self.identity(path) != expected:
            raise PatchError(
                f'path identity changed after patch preflight: {path}',
                path=path,
                failure_kind='concurrent_modification',
            )

    def _require_access(
        self,
        path: Path,
        access: FileSystemAccessMode,
        *,
        inherited_path: Path | None = None,
    ) -> None:
        authority = getattr(self, '_authority', None)
        if authority is None:
            return
        normalized = lexical_absolute_path(path, cwd=authority.cwd)
        permissions = authority.permissions
        matcher = FileSystemPolicyMatcher(permissions.profile.file_system)
        decision = matcher.decide(
            normalized,
            requested_access=access,
            cwd=authority.cwd,
        )
        if not decision.allowed and inherited_path is not None:
            inherited = matcher.decide(
                inherited_path,
                requested_access=access,
                cwd=authority.cwd,
            )
            if inherited.allowed and not matcher.has_explicit_deny(
                normalized,
                cwd=authority.cwd,
            ):
                return
        if not decision.allowed:
            raise PatchPolicyError(
                f'filesystem policy denied {access.value}: {normalized}; {decision.reason}'
            )

    def _ensure_parent_directory(self, parent: Path) -> None:
        if parent.exists():
            return
        self._require_access(parent, FileSystemAccessMode.WRITE)
        parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _content_fingerprint(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        return digest.hexdigest()


__all__ = [
    'LocalPatchFileSystem',
    'PatchExecutionAuthority',
    'PatchFileSystem',
    'PatchPolicyError',
    'PathIdentity',
]
