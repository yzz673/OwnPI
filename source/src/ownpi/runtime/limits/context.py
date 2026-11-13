from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from ownpi.schemas.runtime import LLMConfig


DEFAULT_CONTEXT_WINDOW_LIMIT = 128000
DEFAULT_OUTPUT_RESERVE = 8000
DEFAULT_SAFETY_MARGIN = 4000


@dataclass(frozen=True)
class ModelContextCapability:
    context_window: int
    effective_context_window_percent: int = 95
    auto_compact_token_limit: int | None = None

    @property
    def effective_context_window(self) -> int:
        return self.context_window * self.effective_context_window_percent // 100

    @property
    def resolved_auto_compact_token_limit(self) -> int:
        default_limit = self.context_window * 9 // 10
        if self.auto_compact_token_limit is None:
            return default_limit
        return min(default_limit, self.auto_compact_token_limit)


class ContextBudgetLimits(TypedDict):
    context_window_limit: int
    raw_context_window_limit: int
    auto_compact_token_limit: int | None
    output_reserve: int
    safety_margin: int
    source: str


_MODEL_CONTEXT_CAPABILITIES = {
    model: ModelContextCapability(context_window=272_000)
    for model in (
        'gpt-5.2',
        'gpt-5.3-codex',
        'gpt-5.4',
        'gpt-5.4-mini',
        'gpt-5.5',
        'gpt-5.6-luna',
        'gpt-5.6-sol',
        'gpt-5.6-terra',
        'gpt-6-astra',
        'gpt-6.1-sol',
    )
}

_ANTHROPIC_MODEL_CONTEXT_CAPABILITIES = {
    'claude-haiku-4-5-20251001': ModelContextCapability(context_window=200_000),
}


def resolve_context_budget_limits(
    llm_config: LLMConfig | None = None,
    *,
    context_window_limit: int | None = None,
    output_reserve: int | None = None,
    safety_margin: int | None = None,
) -> ContextBudgetLimits:
    explicit_window = _positive_int(context_window_limit)
    explicit_output = _positive_int(output_reserve)
    explicit_margin = _positive_int(safety_margin)
    if explicit_window is not None:
        return {
            'context_window_limit': explicit_window,
            'raw_context_window_limit': explicit_window,
            'auto_compact_token_limit': None,
            'output_reserve': explicit_output or DEFAULT_OUTPUT_RESERVE,
            'safety_margin': explicit_margin or DEFAULT_SAFETY_MARGIN,
            'source': 'explicit_runtime',
        }
    if llm_config is not None and _positive_int(llm_config.context_window_limit) is not None:
        return {
            'context_window_limit': int(llm_config.context_window_limit or DEFAULT_CONTEXT_WINDOW_LIMIT),
            'raw_context_window_limit': int(
                llm_config.context_window_limit or DEFAULT_CONTEXT_WINDOW_LIMIT
            ),
            'auto_compact_token_limit': None,
            'output_reserve': _positive_int(llm_config.output_reserve_tokens) or DEFAULT_OUTPUT_RESERVE,
            'safety_margin': _positive_int(llm_config.context_safety_margin_tokens) or DEFAULT_SAFETY_MARGIN,
            'source': 'llm_config',
        }
    capability = _resolve_model_context_capability(llm_config)
    if capability is not None:
        return {
            'context_window_limit': capability.effective_context_window,
            'raw_context_window_limit': capability.context_window,
            'auto_compact_token_limit': capability.resolved_auto_compact_token_limit,
            'output_reserve': explicit_output or DEFAULT_OUTPUT_RESERVE,
            'safety_margin': explicit_margin or DEFAULT_SAFETY_MARGIN,
            'source': 'model_capability',
        }
    inferred = _infer_context_window_from_model(llm_config)
    if inferred is not None:
        return {
            'context_window_limit': inferred,
            'raw_context_window_limit': inferred,
            'auto_compact_token_limit': None,
            'output_reserve': explicit_output or DEFAULT_OUTPUT_RESERVE,
            'safety_margin': explicit_margin or DEFAULT_SAFETY_MARGIN,
            'source': 'model_heuristic',
        }
    return {
        'context_window_limit': DEFAULT_CONTEXT_WINDOW_LIMIT,
        'raw_context_window_limit': DEFAULT_CONTEXT_WINDOW_LIMIT,
        'auto_compact_token_limit': None,
        'output_reserve': explicit_output or DEFAULT_OUTPUT_RESERVE,
        'safety_margin': explicit_margin or DEFAULT_SAFETY_MARGIN,
        'source': 'default',
    }


def _positive_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _resolve_model_context_capability(
    llm_config: LLMConfig | None,
) -> ModelContextCapability | None:
    if llm_config is None:
        return None
    provider = str(llm_config.provider or '').casefold()
    model = str(llm_config.model or '').strip().casefold()
    if provider in {'openai', 'openai_compatible'}:
        return _MODEL_CONTEXT_CAPABILITIES.get(model)
    if provider in {'anthropic', 'anthropic_compatible'}:
        return _ANTHROPIC_MODEL_CONTEXT_CAPABILITIES.get(model)
    return None


def _infer_context_window_from_model(llm_config: LLMConfig | None) -> int | None:
    if llm_config is None:
        return None
    provider = str(llm_config.provider or '').casefold()
    model = str(llm_config.model or '').casefold()
    if not model:
        return None
    if provider in {'openai', 'openai_compatible'}:
        if 'gpt-4.1' in model or 'gpt-4o' in model or 'o3' in model or 'o4' in model or 'gpt-5' in model:
            return 128000
        if 'gpt-4' in model:
            return 128000 if '128k' in model or 'turbo' in model else 8192
        if 'gpt-3.5' in model:
            return 16385 if '16k' in model else 4096
    if provider == 'anthropic' or 'claude' in model:
        return 200000
    return None


__all__ = [
    'ContextBudgetLimits',
    'DEFAULT_CONTEXT_WINDOW_LIMIT',
    'DEFAULT_OUTPUT_RESERVE',
    'DEFAULT_SAFETY_MARGIN',
    'ModelContextCapability',
    'resolve_context_budget_limits',
]
