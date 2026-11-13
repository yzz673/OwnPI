from __future__ import annotations

from pathlib import Path
import shutil
import asyncio
import subprocess

from ownpi.runtime.permissions.models import (
    FileSystemAccessMode,
    FileSystemPathKind,
    FileSystemSandboxEntry,
    NetworkSandboxPolicy,
    PermissionProfile,
)
from ownpi.runtime.permissions.policy import (
    lexical_absolute_path,
    normalized_and_canonical_candidates,
)
from ownpi.runtime.sandbox.contracts import (
    SandboxBackendCapability,
    SandboxCommandRequest,
    SandboxExecutionResult,
)


_STANDARD_DEVICE_PATHS = frozenset(
    {
        Path('/dev/null'),
        Path('/dev/zero'),
        Path('/dev/random'),
        Path('/dev/urandom'),
    }
)


class LinuxSandboxBackend:
    kind = 'linux_bwrap'

    def capability(self, profile: PermissionProfile) -> SandboxBackendCapability:
        executable = shutil.which('bwrap')
        unsupported_reason = _unsupported_profile_reason(profile)
        available = executable is not None and not unsupported_reason
        if executable is None:
            reason = 'bubblewrap is unavailable'
        else:
            reason = unsupported_reason
        return SandboxBackendCapability(
            backend_kind=self.kind,
            available=available,
            filesystem_enforced=available,
            network_enforced=available,
            reason=reason,
        )

    def execute(self, request: SandboxCommandRequest) -> SandboxExecutionResult:
        return asyncio.run(self.execute_async(request))

    async def execute_async(self, request: SandboxCommandRequest) -> SandboxExecutionResult:
        from ownpi.runtime.environment.process_capture import capture_private_process

        executable = shutil.which('bwrap')
        if executable is None:
            raise RuntimeError('bubblewrap is unavailable')
        command = build_bwrap_command(executable, request)
        try:
            completed = await capture_private_process(
                command, cwd=request.cwd, env=dict(request.env),
                input_text=request.stdin_text, timeout_seconds=request.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            return SandboxExecutionResult(
                backend_kind=self.kind,
                policy_hash=request.effective_permissions.policy_hash,
                exit_code=124,
                stdout=_timeout_text(exc.stdout),
                stderr=_timeout_text(exc.stderr),
                filesystem_enforced=True,
                network_enforced=True,
                timed_out=True,
            )
        return SandboxExecutionResult(
            backend_kind=self.kind,
            policy_hash=request.effective_permissions.policy_hash,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            filesystem_enforced=True,
            network_enforced=True,
        )


def build_bwrap_command(executable: str, request: SandboxCommandRequest) -> list[str]:
    profile = request.effective_permissions.profile
    unsupported_reason = _unsupported_profile_reason(profile)
    if unsupported_reason:
        raise RuntimeError(unsupported_reason)
    command = [
        executable,
        '--new-session',
        '--die-with-parent',
        '--ro-bind',
        '/',
        '/',
        '--dev',
        '/dev',
        '--proc',
        '/proc',
        '--unshare-user',
        '--unshare-pid',
    ]
    if profile.network is not NetworkSandboxPolicy.ENABLED:
        command.append('--unshare-net')
    entries = tuple(
        entry
        for entry in profile.file_system.entries
        if entry.path.kind is FileSystemPathKind.PATH
    )
    write_roots = [entry for entry in entries if entry.access is FileSystemAccessMode.WRITE]
    deny_paths = [
        lexical_absolute_path(entry.path.value)
        for entry in entries
        if entry.access is FileSystemAccessMode.DENY
    ]

    # Mask deny ancestors before reopening writable descendants. This mirrors
    # Codex's bwrap ordering and keeps a narrow writable mount usable when a
    # protected parent directory is hidden by a synthetic tmpfs.
    bound_writable_paths: set[Path] = set()
    for deny_path in sorted(
        (
            path
            for path in deny_paths
            if any(
                path != lexical_absolute_path(root.path.value)
                and lexical_absolute_path(root.path.value).is_relative_to(path)
                for root in write_roots
            )
        ),
        key=lambda path: len(path.parts),
    ):
        bound_writable_paths.update(
            _append_deny_mask(command, deny_path, write_roots)
        )
    for entry in sorted(write_roots, key=lambda item: len(Path(item.path.value).parts)):
        path = lexical_absolute_path(entry.path.value)
        if path not in bound_writable_paths and path.exists():
            command.extend(('--bind', str(path), str(path)))
    for entry in entries:
        if entry.access is FileSystemAccessMode.READ and any(
            _is_strict_descendant(entry.path.value, root.path.value) for root in write_roots
        ):
            path = lexical_absolute_path(entry.path.value)
            if path.exists():
                command.extend(('--ro-bind', str(path), str(path)))
    for deny_path in sorted(
        (
            path
            for path in deny_paths
            if not any(
                path != lexical_absolute_path(root.path.value)
                and lexical_absolute_path(root.path.value).is_relative_to(path)
                for root in write_roots
            )
        ),
        key=lambda path: len(path.parts),
    ):
        _append_deny_mask(command, deny_path, write_roots)
    command.extend(('--chdir', str(request.cwd), '--', *request.argv))
    return command


def _unsupported_profile_reason(profile: PermissionProfile) -> str:
    for entry in profile.file_system.entries:
        if entry.access is not FileSystemAccessMode.DENY:
            continue
        if entry.path.kind is FileSystemPathKind.GLOB:
            return 'Linux deny glob entries require a concrete path expansion'
        if entry.path.kind is not FileSystemPathKind.PATH:
            return 'Linux deny entries must be materialized as literal paths'
        path = lexical_absolute_path(entry.path.value)
        if path in _STANDARD_DEVICE_PATHS:
            return f'Linux deny entries cannot mask standard devices: {path}'
        symlink = _first_symlink_component(path)
        if symlink is not None:
            return (
                'Linux deny paths crossing symlink components are unsupported: '
                f'{path} crosses {symlink}'
            )
    return ''


def _append_deny_mask(
    command: list[str],
    denied_path: Path,
    write_roots: list[FileSystemSandboxEntry],
) -> set[Path]:
    if not denied_path.exists():
        first_missing = _first_missing_component(denied_path)
        if first_missing is None:
            return set()
        _append_missing_directory_mask(command, first_missing)
        return set()

    if denied_path.is_dir():
        writable_descendants = [
            lexical_absolute_path(root.path.value)
            for root in write_roots
            if lexical_absolute_path(root.path.value) != denied_path
            and lexical_absolute_path(root.path.value).is_relative_to(denied_path)
        ]
        command.extend(('--tmpfs', str(denied_path)))
        for descendant in sorted(writable_descendants, key=lambda path: len(path.parts)):
            _append_mount_target_parent_dirs(command, descendant, denied_path)
            if descendant.exists():
                command.extend(('--bind', str(descendant), str(descendant)))
        command.extend(('--remount-ro', str(denied_path)))
        return {path for path in writable_descendants if path.exists()}

    # bwrap 0.4, the baseline on server1, has no --perms flag. A readonly
    # bind over /dev/null hides the protected file body and prevents writes;
    # direct resource access is still rejected by the canonical tool policy.
    command.extend(('--ro-bind', '/dev/null', str(denied_path)))
    return set()


def _append_missing_directory_mask(command: list[str], path: Path) -> None:
    parent = path.parent
    if parent != path:
        _append_mount_target_parent_dirs(command, path, Path('/'))
    command.extend(('--tmpfs', str(path), '--remount-ro', str(path)))


def _append_mount_target_parent_dirs(command: list[str], mount_target: Path, anchor: Path) -> None:
    parent = mount_target if mount_target.is_dir() else mount_target.parent
    directories: list[Path] = []
    current = parent
    while current != anchor and current != current.parent:
        directories.append(current)
        current = current.parent
    for directory in reversed(directories):
        command.extend(('--dir', str(directory)))


def _first_missing_component(path: Path) -> Path | None:
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        if not current.exists() and not current.is_symlink():
            return current
    return None


def _first_symlink_component(path: Path) -> Path | None:
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        if current.is_symlink():
            return current
    return None


def _is_strict_descendant(path: str, root: str) -> bool:
    for candidate in normalized_and_canonical_candidates(path):
        for parent in normalized_and_canonical_candidates(root):
            try:
                relative = candidate.relative_to(parent)
            except ValueError:
                continue
            if relative.parts:
                return True
    return False


def _timeout_text(value: bytes | str | None) -> str:
    if value is None:
        return ''
    return value.decode(errors='replace') if isinstance(value, bytes) else value
