from __future__ import annotations

from dataclasses import replace

from ownpi.runtime.native_tools.hosted_web_search import (
    WebSearchExecutionMode,
)
from ownpi.runtime.native_tools.provider import (
    NativeToolProviderCapabilities,
)
from ownpi.runtime.permissions.models import NetworkSandboxPolicy
from ownpi.runtime.tools.runtime import ToolRuntimeContext












def record_provider_capability_fact(
    context: ToolRuntimeContext,
    capabilities: NativeToolProviderCapabilities,
) -> None:
    from ownpi.runtime.environment.facts import (
        environment_fact_scope,
        record_environment_fact,
    )

    record_environment_fact(
        context.state_store,
        fact_kind="provider_capability",
        scope_identity=environment_fact_scope(context),
        subject="hosted_web_search",
        value=capabilities.web_search_execution_mode.value,
        evidence_ref="native_tool_provider_capabilities",
        validation_token=(
            f"{capabilities.web_search_execution_mode.value}:"
            f"{capabilities.hosted_web_search_spec_digest}"
        ),
    )


def permission_projected_provider_capabilities(
    context: ToolRuntimeContext,
    capabilities: NativeToolProviderCapabilities,
) -> NativeToolProviderCapabilities:
    if context.canonical_permissions.profile.network is NetworkSandboxPolicy.ENABLED:
        return capabilities
    if capabilities.web_search_execution_mode is WebSearchExecutionMode.HOSTED:
        return replace(
            capabilities,
            web_search_execution_mode=WebSearchExecutionMode.LOCAL_FALLBACK,
            hosted_web_search_spec_digest="",
        )
    return capabilities


__all__ = [
    "permission_projected_provider_capabilities",
    "record_provider_capability_fact",
]
