from __future__ import annotations

from pathlib import Path
import asyncio
import subprocess

from ownpi.runtime.permissions.models import (
    FileSystemAccessMode,
    FileSystemPathKind,
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


SANDBOX_EXEC = Path('/usr/bin/sandbox-exec')

# Adapted from Codex seatbelt_base_policy.sbpl at commit
# 61a44880a85d2fd0d8770908dea5733495e571c8 (Apache-2.0).
_BASE_POLICY = '''
(version 1)
(deny default)
(allow process-exec)
(allow process-fork)
(allow signal (target same-sandbox))
(allow process-info* (target same-sandbox))
(allow sysctl-read)
(allow mach-lookup)
(allow ipc-posix-sem)
(allow pseudo-tty)
(allow file-read* file-write-data (literal "/dev/null"))
(allow file-read* file-write* (subpath "/dev/fd"))
(allow file-read* file-write* (regex #"^/dev/ttys[0-9]+$"))
(allow file-read* file-write* (literal "/dev/ptmx"))
'''.strip()


class MacOSSandboxBackend:
    kind = 'macos_seatbelt'

    def capability(self, profile: PermissionProfile) -> SandboxBackendCapability:
        unsupported_reason = _unsupported_profile_reason(profile)
        available = SANDBOX_EXEC.is_file() and not unsupported_reason
        if not SANDBOX_EXEC.is_file():
            reason = '/usr/bin/sandbox-exec is unavailable'
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

        capability = self.capability(request.effective_permissions.profile)
        if not capability.available:
            raise RuntimeError(capability.reason)
        command = build_seatbelt_command(request)
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


def build_seatbelt_command(request: SandboxCommandRequest) -> list[str]:
    policy, parameters = build_seatbelt_policy(request.effective_permissions.profile)
    command = [str(SANDBOX_EXEC), '-p', policy]
    for name, path in parameters:
        command.extend(('-D', f'{name}={path}'))
    command.extend(('--', *request.argv))
    return command


def build_seatbelt_policy(profile: PermissionProfile) -> tuple[str, tuple[tuple[str, Path], ...]]:
    unsupported_reason = _unsupported_profile_reason(profile)
    if unsupported_reason:
        raise RuntimeError(unsupported_reason)
    parameters: list[tuple[str, Path]] = []
    sections = [_BASE_POLICY]
    entries = tuple(
        entry
        for entry in profile.file_system.entries
        if entry.path.kind is FileSystemPathKind.PATH
    )
    if any(
        entry.access.can_read and lexical_absolute_path(entry.path.value) == Path('/')
        for entry in entries
    ):
        sections.append('(allow file-read*)')
    else:
        for entry in entries:
            if entry.access.can_read:
                sections.extend(
                    _parameter_rule('allow', 'file-read*', path, parameters)
                    for path in _entry_paths(entry.path.value)
                )
    write_roots = [entry for entry in entries if entry.access is FileSystemAccessMode.WRITE]
    for entry in write_roots:
        sections.extend(
            _parameter_rule('allow', 'file-write*', path, parameters)
            for path in _entry_paths(entry.path.value)
        )
    for entry in entries:
        if entry.access is FileSystemAccessMode.DENY:
            sections.extend(
                _parameter_rule('deny', 'file-read* file-write*', path, parameters)
                for path in _entry_paths(entry.path.value)
            )
        elif entry.access is FileSystemAccessMode.READ and any(
            _is_strict_descendant(entry.path.value, root.path.value) for root in write_roots
        ):
            sections.extend(
                _parameter_rule('deny', 'file-write*', path, parameters)
                for path in _entry_paths(entry.path.value)
            )
    if profile.network is NetworkSandboxPolicy.ENABLED:
        sections.append('(allow network-outbound)\n(allow network-inbound)')
    return '\n'.join(sections), tuple(parameters)


def _unsupported_profile_reason(profile: PermissionProfile) -> str:
    if any(
        entry.path.kind is FileSystemPathKind.GLOB
        for entry in profile.file_system.entries
    ):
        return 'macOS Seatbelt backend cannot enforce filesystem glob entries'
    return ''


def _parameter_rule(
    disposition: str,
    operations: str,
    path: str,
    parameters: list[tuple[str, Path]],
) -> str:
    name = f'PATH_{len(parameters)}'
    parameters.append((name, lexical_absolute_path(path)))
    return f'({disposition} {operations} (subpath (param "{name}")))'


def _entry_paths(path: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(str(candidate) for candidate in normalized_and_canonical_candidates(path))
    )


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
