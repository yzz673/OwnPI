from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from ownpi.runtime.environment.contracts import EnvironmentDescriptor, SandboxPolicy


@dataclass(frozen=True)
class EnforcementCapability:
    category: Literal['filesystem', 'network', 'process', 'env', 'artifact']
    requested: bool
    enforced: bool
    enforcement_layer: Literal['provider', 'os_sandbox', 'container', 'preflight', 'unsupported']
    reason: str
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compile_enforcement_capabilities(
    *,
    descriptor: EnvironmentDescriptor,
    sandbox_policy: SandboxPolicy,
    provider_kind: str,
) -> tuple[EnforcementCapability, ...]:
    if provider_kind == 'docker_sandbox':
        filesystem_layer = (
            'unsupported' if sandbox_policy.blocked_host_roots else 'container'
        )
        filesystem_enforced = not sandbox_policy.blocked_host_roots
        network_disabled_layer = 'container'
        process_layer = 'container'
        process_enforced = bool(sandbox_policy.requires_isolation or descriptor.sandboxed)
    elif provider_kind == 'ssh_remote':
        filesystem_layer = 'unsupported'
        filesystem_enforced = False
        network_disabled_layer = 'unsupported'
        process_layer = 'unsupported'
        process_enforced = False
    else:
        managed_host_sandbox = sandbox_policy.enforcement_mode == 'canonical_profile'
        filesystem_layer = 'os_sandbox' if managed_host_sandbox else 'preflight'
        filesystem_enforced = managed_host_sandbox
        network_disabled_layer = 'os_sandbox' if managed_host_sandbox else 'preflight'
        process_layer = 'os_sandbox' if managed_host_sandbox else 'preflight'
        process_enforced = managed_host_sandbox

    network_requested = (
        not sandbox_policy.allow_network
    )
    if not sandbox_policy.allow_network:
        network_layer = network_disabled_layer
        network_enforced = provider_kind == 'docker_sandbox' or (
            provider_kind == 'host_local'
            and sandbox_policy.enforcement_mode == 'canonical_profile'
        )
        network_reason = 'network_disabled'
    else:
        network_layer = 'provider'
        network_enforced = True
        network_reason = 'network_unrestricted'

    return (
        EnforcementCapability(
            category='filesystem',
            requested=bool(
                sandbox_policy.readable_host_roots
                or sandbox_policy.writable_host_roots
                or sandbox_policy.blocked_host_roots
            ),
            enforced=filesystem_enforced,
            enforcement_layer=filesystem_layer,  # type: ignore[arg-type]
            reason=(
                'filesystem_roots_os_sandbox_enforced'
                if filesystem_layer == 'os_sandbox'
                else (
                    'filesystem_roots_provider_enforced'
                    if filesystem_enforced
                    else 'filesystem_roots_preflight_only'
                )
            ),
            evidence={
                'readable_roots': list(sandbox_policy.readable_host_roots),
                'writable_roots': list(sandbox_policy.writable_host_roots),
                'blocked_host_roots': list(sandbox_policy.blocked_host_roots),
            },
        ),
        EnforcementCapability(
            category='network',
            requested=network_requested,
            enforced=network_enforced,
            enforcement_layer=network_layer,  # type: ignore[arg-type]
            reason=network_reason,
            evidence={
                'allow_network': bool(sandbox_policy.allow_network),
            },
        ),
        EnforcementCapability(
            category='process',
            requested=bool(sandbox_policy.requires_isolation or descriptor.sandboxed),
            enforced=process_enforced,
            enforcement_layer=process_layer,  # type: ignore[arg-type]
            reason='process_isolated' if process_enforced else 'process_preflight_only',
            evidence={
                'requires_isolation': bool(sandbox_policy.requires_isolation),
                'sandboxed': bool(descriptor.sandboxed),
            },
        ),
    )


def classify_enforcement_status(capabilities: tuple[EnforcementCapability, ...]) -> str:
    requested = [item for item in capabilities if item.requested]
    if not requested:
        return 'enforced'
    if all(item.enforced for item in requested):
        return 'enforced'
    if any(item.enforcement_layer == 'unsupported' for item in requested):
        return 'unsupported'
    if all(item.enforcement_layer == 'preflight' for item in requested):
        return 'preflight_only'
    return 'partial'
