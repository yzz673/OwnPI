from __future__ import annotations

from ownpi.runtime.permissions.models import PermissionProfile
from ownpi.runtime.sandbox.contracts import SandboxBackendCapability, SandboxCommandRequest


class WindowsSandboxBackend:
    kind = 'windows_restricted_token'

    def capability(self, profile: PermissionProfile) -> SandboxBackendCapability:
        return SandboxBackendCapability(
            backend_kind=self.kind,
            available=False,
            filesystem_enforced=False,
            network_enforced=False,
            reason='Windows restricted-token backend is not available in this Python build',
        )

    def execute(self, request: SandboxCommandRequest) -> None:
        raise RuntimeError(self.capability(request.effective_permissions.profile).reason)
