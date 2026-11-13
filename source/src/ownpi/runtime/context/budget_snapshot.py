from __future__ import annotations

from ownpi.runtime.limits.context import resolve_context_budget_limits
from ownpi.runtime.native_tools.provider_request_measurement import (
    NativeProviderRequestMeasurement,
)
from ownpi.runtime.state_models import TokenBudgetSnapshot
from ownpi.schemas.runtime import LLMConfig


def budget_snapshot_from_measurement(
    measurement: NativeProviderRequestMeasurement,
    *,
    llm_config: LLMConfig | None,
) -> TokenBudgetSnapshot:
    limits = resolve_context_budget_limits(llm_config)
    estimated = int(measurement.estimated_tokens)
    remaining = limits["context_window_limit"] - estimated
    output_reserve = limits["output_reserve"]
    safety_margin = limits["safety_margin"]
    if remaining <= output_reserve + safety_margin:
        pressure = "critical"
    elif remaining <= 24_000:
        pressure = "high"
    elif remaining <= 48_000:
        pressure = "medium"
    else:
        pressure = "low"
    return TokenBudgetSnapshot(
        estimated_input_tokens=estimated,
        estimated_output_reserve=output_reserve,
        context_window_limit=limits["context_window_limit"],
        raw_context_window_limit=limits["raw_context_window_limit"],
        auto_compact_token_limit=limits["auto_compact_token_limit"],
        safety_margin=safety_margin,
        pressure_level=pressure,
        estimation_strategy="native_provider_request_measurement",
        provider_hint=(llm_config.provider if llm_config is not None else None),
        category_token_estimates={
            "immutable_prefix": measurement.prefix_tokens,
            "conversation": measurement.conversation_tokens,
            "tool_schemas": measurement.tool_schema_tokens,
            "tool_search_outputs": measurement.tool_search_output_tokens,
            "provider_envelope": measurement.envelope_tokens,
        },
        remaining_input_tokens=remaining,
        usage_ratio=(
            estimated / limits["context_window_limit"]
            if limits["context_window_limit"] > 0
            else 0.0
        ),
        budget_source="native_provider_envelope",
        budget_limit_source=limits["source"],
    )


__all__ = ["budget_snapshot_from_measurement"]
