from __future__ import annotations

import asyncio
import subprocess
import sys

from ownpi.runtime.permissions.models import SandboxEnforcement
from ownpi.runtime.sandbox.contracts import (
    SandboxBackendCapability,
    SandboxCommandRequest,
    SandboxExecutionResult,
    SandboxWrappedCommand,
)
from ownpi.runtime.sandbox.linux import LinuxSandboxBackend
from ownpi.runtime.sandbox.linux import build_bwrap_command
from ownpi.runtime.sandbox.macos import MacOSSandboxBackend, build_seatbelt_command
from ownpi.runtime.sandbox.windows import WindowsSandboxBackend


class SandboxManager:
    def capability(self, request: SandboxCommandRequest) -> SandboxBackendCapability:
        enforcement = request.effective_permissions.profile.enforcement
        if enforcement is SandboxEnforcement.DISABLED:
            return SandboxBackendCapability('direct', True, False, False)
        if enforcement is SandboxEnforcement.EXTERNAL:
            capability = request.external_capability
            if capability is None:
                return SandboxBackendCapability(
                    'external', False, False, False, 'external sandbox capability is missing'
                )
            policy_matches = capability.policy_hash == request.effective_permissions.policy_hash
            available = (
                policy_matches
                and capability.filesystem_enforced
                and capability.network_enforced
            )
            return SandboxBackendCapability(
                f'external:{capability.provider_kind}',
                available,
                capability.filesystem_enforced,
                capability.network_enforced,
                '' if available else 'external sandbox capability does not satisfy the policy',
            )
        backend = self._managed_backend()
        return backend.capability(request.effective_permissions.profile)

    def execute(self, request: SandboxCommandRequest) -> SandboxExecutionResult:
        return asyncio.run(self.execute_async(request))

    async def execute_async(self, request: SandboxCommandRequest) -> SandboxExecutionResult:
        capability = self.capability(request)
        if not capability.available:
            raise RuntimeError(capability.reason)
        enforcement = request.effective_permissions.profile.enforcement
        if enforcement is SandboxEnforcement.DISABLED:
            return await _execute_direct(request)
        if enforcement is SandboxEnforcement.EXTERNAL:
            raise RuntimeError('external sandbox commands must execute through their provider')
        return await self._managed_backend().execute_async(request)

    def wrap_command(self, request: SandboxCommandRequest) -> tuple[str, ...]:
        return self.wrap_command_with_provenance(request).argv

    def wrap_command_with_provenance(
        self, request: SandboxCommandRequest,
    ) -> SandboxWrappedCommand:
        enforcement = request.effective_permissions.profile.enforcement
        backend = self._managed_backend() if enforcement is SandboxEnforcement.MANAGED else None
        capability = (
            backend.capability(request.effective_permissions.profile)
            if backend is not None else self.capability(request)
        )
        if not capability.available:
            raise RuntimeError(capability.reason)
        if enforcement is SandboxEnforcement.DISABLED:
            return SandboxWrappedCommand(request.argv, 'direct')
        if enforcement is SandboxEnforcement.EXTERNAL:
            raise RuntimeError('external sandbox commands must execute through their provider')
        if isinstance(backend, MacOSSandboxBackend):
            return SandboxWrappedCommand(tuple(build_seatbelt_command(request)), 'seatbelt')
        if isinstance(backend, LinuxSandboxBackend):
            import shutil

            executable = shutil.which('bwrap')
            if executable is None:
                raise RuntimeError('bubblewrap is unavailable')
            return SandboxWrappedCommand(tuple(build_bwrap_command(executable, request)), 'bwrap')
        raise RuntimeError(capability.reason or 'managed sandbox backend cannot wrap commands')

    @staticmethod
    def _managed_backend() -> MacOSSandboxBackend | LinuxSandboxBackend | WindowsSandboxBackend:
        if sys.platform == 'darwin':
            return MacOSSandboxBackend()
        if sys.platform.startswith('linux'):
            return LinuxSandboxBackend()
        if sys.platform == 'win32':
            return WindowsSandboxBackend()
        raise RuntimeError(f'no managed sandbox backend for platform {sys.platform!r}')


async def _execute_direct(request: SandboxCommandRequest) -> SandboxExecutionResult:
    from ownpi.runtime.environment.process_capture import capture_private_process

    try:
        completed = await capture_private_process(
            request.argv, cwd=request.cwd, env=dict(request.env),
            input_text=request.stdin_text, timeout_seconds=request.timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return SandboxExecutionResult(
            backend_kind='direct',
            policy_hash=request.effective_permissions.policy_hash,
            exit_code=124,
            stdout=_timeout_text(exc.stdout),
            stderr=_timeout_text(exc.stderr),
            filesystem_enforced=False,
            network_enforced=False,
            timed_out=True,
        )
    return SandboxExecutionResult(
        backend_kind='direct',
        policy_hash=request.effective_permissions.policy_hash,
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        filesystem_enforced=False,
        network_enforced=False,
    )


def _timeout_text(value: bytes | str | None) -> str:
    if value is None:
        return ''
    return value.decode(errors='replace') if isinstance(value, bytes) else value
