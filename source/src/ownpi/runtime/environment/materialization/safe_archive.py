from __future__ import annotations

from pathlib import Path, PurePosixPath
import shutil
import tarfile

from ownpi.runtime.environment.materialization.contracts import MaterializationFailure
from ownpi.runtime.environment.materialization.file_io import MaterializationFileIO


def extract_tar_gz_safely(
    archive_path: Path,
    destination: Path,
    *,
    max_members: int = 20_000,
    max_expanded_bytes: int = 2_000_000_000,
    io: MaterializationFileIO | None = None,
) -> tuple[int, int]:
    io = io or MaterializationFileIO()
    io.mkdir(destination)
    io.read(archive_path)
    try:
        archive = tarfile.open(archive_path, mode='r:gz')
    except PermissionError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise MaterializationFailure(
            f'invalid repository archive: {exc}',
            owner='runtime_capability_missing',
            code='invalid_archive',
        ) from exc
    with archive:
        members = archive.getmembers()
        if not members or len(members) > max_members:
            raise _unsafe('archive member count is outside the allowed range')
        expanded_bytes = sum(max(0, member.size) for member in members)
        if expanded_bytes > max_expanded_bytes:
            raise _unsafe('archive expanded size exceeds the configured limit')
        roots = {_safe_parts(member.name)[0] for member in members if _safe_parts(member.name)}
        if len(roots) != 1:
            raise _unsafe('archive must contain one repository root')
        root_name = next(iter(roots))
        extracted_files = 0
        for member in members:
            parts = _safe_parts(member.name)
            if not parts or parts[0] != root_name:
                continue
            relative_parts = parts[1:]
            if not relative_parts:
                continue
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise _unsafe(f'archive contains unsupported member: {member.name}')
            target = destination.joinpath(*relative_parts)
            io.write(target)
            if member.isdir():
                io.mkdir(target)
                continue
            if not member.isfile():
                raise _unsafe(f'archive contains unsupported member: {member.name}')
            source = archive.extractfile(member)
            if source is None:
                raise _unsafe(f'archive member cannot be read: {member.name}')
            io.mkdir(target.parent)
            io.write(target)
            with source, target.open('wb') as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            extracted_files += 1
        return extracted_files, expanded_bytes


def _safe_parts(name: str) -> tuple[str, ...]:
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts:
        raise _unsafe(f'archive path escapes destination: {name}')
    return tuple(part for part in path.parts if part not in {'', '.'})


def _unsafe(message: str) -> MaterializationFailure:
    return MaterializationFailure(
        message,
        owner='runtime_capability_missing',
        code='unsafe_archive',
    )
