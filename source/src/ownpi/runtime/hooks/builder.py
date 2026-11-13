from __future__ import annotations

from ownpi.runtime.hooks.engine import HookEngine
from ownpi.runtime.hooks.engine.mcp_runner import MCPHookExecutor
from ownpi.runtime.hooks.observability import HookObservability
from ownpi.runtime.hooks.registry import HookRegistration, HookRegistry
from ownpi.runtime.hooks.trust import HookTrustStore
from ownpi.runtime.permissions.models import EffectivePermissions
from ownpi.schemas.runtime import HookRegistryConfig


def build_hook_engine(
    config: HookRegistryConfig | tuple[HookRegistryConfig, ...] | list[HookRegistryConfig] | None,
    managed_registrations: tuple[HookRegistration, ...] | list[HookRegistration] = (),
    *,
    effective_permissions: EffectivePermissions | None = None,
    mcp_executor: MCPHookExecutor | None = None,
    trust_store: HookTrustStore | None = None,
    observability: HookObservability | None = None,
) -> HookEngine:
    if isinstance(config, (tuple, list)):
        merged = HookRegistryConfig.merge(*config)
    else:
        merged = config or HookRegistryConfig()
    registry = HookRegistry.from_config(
        merged,
        callbacks=tuple(managed_registrations),
        trust_store=trust_store,
    )
    return HookEngine(
        registry,
        effective_permissions=effective_permissions,
        mcp_executor=mcp_executor,
        observability=observability,
    )
