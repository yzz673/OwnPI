from __future__ import annotations

from ownpi.llm.output_limits import resolve_output_token_limit
from ownpi.runtime.native_tools.compaction.contracts import (
    ContextBudgetMode,
    NativeModelContextMetadataV1,
)
from ownpi.runtime.limits.context import resolve_context_budget_limits
from ownpi.schemas.runtime import (
    LLMConfig,
    NativeConversationCompactionConfig,
)


def model_context_metadata_from_config(
    *,
    llm_config: LLMConfig | None,
    compaction_config: NativeConversationCompactionConfig,
) -> NativeModelContextMetadataV1 | None:
    if not compaction_config.enabled or llm_config is None:
        return None

    limits = resolve_context_budget_limits(llm_config)
    if limits["source"] in {"default", "model_heuristic"}:
        return None

    output_limit = resolve_output_token_limit(llm_config)
    response_reserve = (
        compaction_config.response_reserve_tokens_override
        or limits["output_reserve"]
        or output_limit.effective
    )
    auto_limit = compaction_config.auto_compact_limit_tokens_override
    if auto_limit is None:
        auto_limit = limits["auto_compact_token_limit"]

    raw_context_window = limits["raw_context_window_limit"]
    input_context_window = limits["context_window_limit"]
    budget_mode: ContextBudgetMode = "input_only" if str(llm_config.provider).startswith('anthropic') else "combined"
    overhead = max(0, raw_context_window - input_context_window)
    return NativeModelContextMetadataV1(
        provider_identity=str(llm_config.provider),
        model_identity=str(llm_config.model),
        metadata_source=(
            "runtime_context_budget:" + limits["source"] + "+" + output_limit.source
        ),
        context_budget_mode=budget_mode,
        context_window_tokens=int(raw_context_window),
        maximum_output_tokens=int(response_reserve),
        auto_compact_limit_tokens=auto_limit,
        protocol_overhead_tokens=overhead,
    )


__all__ = ["model_context_metadata_from_config"]
