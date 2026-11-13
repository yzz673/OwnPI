from __future__ import annotations

import os
from pathlib import Path
import tempfile
from typing import Mapping

from ownpi.runtime.permissions.models import (
    BUILT_IN_PERMISSION_PROFILE_DANGER_FULL_ACCESS,
    BUILT_IN_PERMISSION_PROFILE_READ_ONLY,
    BUILT_IN_PERMISSION_PROFILE_WORKSPACE,
    PROTECTED_METADATA_NAMES,
    FileSystemAccessMode,
    FileSystemPath,
    FileSystemSandboxEntry,
    FileSystemSandboxKind,
    FileSystemSandboxPolicy,
    FileSystemSpecialPath,
    NetworkSandboxPolicy,
    PermissionProfile,
    SandboxEnforcement,
)


def read_only_profile() -> PermissionProfile:
    return PermissionProfile(
        profile_id=BUILT_IN_PERMISSION_PROFILE_READ_ONLY,
        enforcement=SandboxEnforcement.MANAGED,
        file_system=FileSystemSandboxPolicy(
            kind=FileSystemSandboxKind.RESTRICTED,
            entries=(
                FileSystemSandboxEntry(
                    FileSystemPath.special(FileSystemSpecialPath.ROOT),
                    FileSystemAccessMode.READ,
                ),
            ),
        ),
        network=NetworkSandboxPolicy.RESTRICTED,
        description='Read files without writing to the filesystem.',
    )


def workspace_profile() -> PermissionProfile:
    entries = [
        FileSystemSandboxEntry(
            FileSystemPath.special(FileSystemSpecialPath.ROOT),
            FileSystemAccessMode.READ,
        ),
        FileSystemSandboxEntry(
            FileSystemPath.special(FileSystemSpecialPath.PROJECT_ROOTS),
            FileSystemAccessMode.WRITE,
        ),
        FileSystemSandboxEntry(
            FileSystemPath.special(FileSystemSpecialPath.SLASH_TMP),
            FileSystemAccessMode.WRITE,
            skip_if_missing=True,
        ),
        FileSystemSandboxEntry(
            FileSystemPath.special(FileSystemSpecialPath.TMPDIR),
            FileSystemAccessMode.WRITE,
            skip_if_missing=True,
        ),
    ]
    entries.extend(
        FileSystemSandboxEntry(
            FileSystemPath.special(FileSystemSpecialPath.PROJECT_ROOTS, subpath=name),
            FileSystemAccessMode.READ,
            skip_if_missing=True,
        )
        for name in PROTECTED_METADATA_NAMES
    )
    return PermissionProfile(
        profile_id=BUILT_IN_PERMISSION_PROFILE_WORKSPACE,
        enforcement=SandboxEnforcement.MANAGED,
        file_system=FileSystemSandboxPolicy(
            kind=FileSystemSandboxKind.RESTRICTED,
            entries=tuple(entries),
        ),
        network=NetworkSandboxPolicy.RESTRICTED,
        description='Read broadly and write within active workspace roots.',
    )


def danger_full_access_profile() -> PermissionProfile:
    return PermissionProfile(
        profile_id=BUILT_IN_PERMISSION_PROFILE_DANGER_FULL_ACCESS,
        enforcement=SandboxEnforcement.DISABLED,
        file_system=FileSystemSandboxPolicy(FileSystemSandboxKind.UNRESTRICTED),
        network=NetworkSandboxPolicy.ENABLED,
        description='Disable managed filesystem and network restrictions.',
    )


def built_in_profiles() -> dict[str, PermissionProfile]:
    profiles = (read_only_profile(), workspace_profile(), danger_full_access_profile())
    return {profile.profile_id: profile for profile in profiles}


def resolve_profile(
    profile_id: str,
    *,
    custom_profiles: Mapping[str, PermissionProfile] | None = None,
) -> PermissionProfile:
    profiles = {**built_in_profiles(), **dict(custom_profiles or {})}
    return _resolve_profile(profile_id, profiles=profiles, resolving=())


def _resolve_profile(
    profile_id: str,
    *,
    profiles: Mapping[str, PermissionProfile],
    resolving: tuple[str, ...],
) -> PermissionProfile:
    if profile_id not in profiles:
        raise ValueError(f'unknown permission profile: {profile_id!r}')
    if profile_id in resolving:
        cycle = ' -> '.join((*resolving, profile_id))
        raise ValueError(f'permission profile inheritance cycle: {cycle}')
    profile = profiles[profile_id]
    if profile.extends is None:
        return profile
    parent = _resolve_profile(
        profile.extends,
        profiles=profiles,
        resolving=(*resolving, profile_id),
    )
    if profile.enforcement is not parent.enforcement:
        return profile
    inherited_entries = (*parent.file_system.entries, *profile.file_system.entries)
    return PermissionProfile(
        profile_id=profile.profile_id,
        enforcement=profile.enforcement,
        file_system=FileSystemSandboxPolicy(
            profile.file_system.kind,
            inherited_entries,
            profile.file_system.glob_scan_max_depth,
        ),
        network=profile.network,
        description=profile.description,
        extends=profile.extends,
    )


def materialize_profile(
    profile: PermissionProfile,
    *,
    workspace_roots: tuple[Path, ...],
    tmpdir: Path | None = None,
) -> PermissionProfile:
    roots = _dedupe_absolute_paths(workspace_roots)
    if profile.enforcement is not SandboxEnforcement.MANAGED:
        return profile
    entries: list[FileSystemSandboxEntry] = []
    for entry in profile.file_system.entries:
        entries.extend(_materialize_entry(entry, workspace_roots=roots, tmpdir=tmpdir))
    return PermissionProfile(
        profile_id=profile.profile_id,
        enforcement=profile.enforcement,
        file_system=FileSystemSandboxPolicy(
            profile.file_system.kind,
            tuple(entries),
            profile.file_system.glob_scan_max_depth,
        ),
        network=profile.network,
        description=profile.description,
        extends=profile.extends,
    )


def _materialize_entry(
    entry: FileSystemSandboxEntry,
    *,
    workspace_roots: tuple[Path, ...],
    tmpdir: Path | None,
) -> tuple[FileSystemSandboxEntry, ...]:
    if entry.path.kind.value != 'special':
        if entry.skip_if_missing and not _entry_path_exists(entry):
            return ()
        return (entry,)
    special = FileSystemSpecialPath(entry.path.value)
    if special is FileSystemSpecialPath.PROJECT_ROOTS:
        return tuple(
            FileSystemSandboxEntry(
                FileSystemPath.path(root / entry.path.subpath if entry.path.subpath else root),
                entry.access,
                entry.skip_if_missing,
            )
            for root in workspace_roots
        )
    if special is FileSystemSpecialPath.TMPDIR:
        path = tmpdir or Path(tempfile.gettempdir())
    elif special is FileSystemSpecialPath.SLASH_TMP:
        path = Path('/tmp')
    elif special is FileSystemSpecialPath.ROOT:
        path = Path(workspace_roots[0].anchor if workspace_roots else os.path.abspath(os.sep))
    elif special is FileSystemSpecialPath.MINIMAL:
        return ()
    else:  # pragma: no cover - enum exhaustiveness
        raise ValueError(f'unsupported special path: {special.value}')
    materialized = FileSystemSandboxEntry(
        FileSystemPath.path(path),
        entry.access,
        entry.skip_if_missing,
    )
    if materialized.skip_if_missing and not _entry_path_exists(materialized):
        return ()
    return (materialized,)


def _entry_path_exists(entry: FileSystemSandboxEntry) -> bool:
    if entry.path.kind.value == 'glob':
        return True
    path = Path(entry.path.value)
    return path.exists() or path.is_symlink()


def _dedupe_absolute_paths(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    values: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        normalized = Path(os.path.abspath(os.path.expanduser(str(path))))
        key = os.path.normcase(str(normalized))
        if key not in seen:
            seen.add(key)
            values.append(normalized)
    return tuple(values)
