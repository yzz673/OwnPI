from __future__ import annotations

from ownpi.runtime.native_tools.compaction.contracts import (
    NativeModelContextMetadataV1,
    NativeResolvedCompactionBudgetV1,
)
from ownpi.runtime.limits.projection import AUTO_COMPACTION_CONTEXT_PERCENT


def resolve_native_compaction_budget(
    *,
    metadata: NativeModelContextMetadataV1,
    requested_output_tokens: int | None,
) -> NativeResolvedCompactionBudgetV1:
    requested = max(
        1,
        min(
            int(requested_output_tokens or metadata.maximum_output_tokens),
            metadata.maximum_output_tokens,
        ),
    )
    if metadata.context_budget_mode == "combined":
        effective_input = (
            metadata.context_window_tokens
            - requested
            - metadata.protocol_overhead_tokens
        )
    else:
        effective_input = (
            metadata.context_window_tokens - metadata.protocol_overhead_tokens
        )
    if effective_input < 1:
        raise ValueError("model metadata leaves no usable input budget")
    default_auto_limit = max(
        1,
        effective_input * AUTO_COMPACTION_CONTEXT_PERCENT // 100,
    )
    auto_limit = min(
        effective_input,
        metadata.auto_compact_limit_tokens or default_auto_limit,
    )
    return NativeResolvedCompactionBudgetV1(
        model_identity=metadata.model_identity,
        provider_identity=metadata.provider_identity,
        metadata_source=metadata.metadata_source,
        context_window_tokens=metadata.context_window_tokens,
        effective_input_limit_tokens=effective_input,
        requested_response_reserve_tokens=requested,
        auto_compact_limit_tokens=auto_limit,
        protocol_overhead_tokens=metadata.protocol_overhead_tokens,
    )


__all__ = ["resolve_native_compaction_budget"]
