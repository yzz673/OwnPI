from __future__ import annotations

from dataclasses import dataclass

from ownpi.schemas.runtime import LLMConfig


@dataclass(frozen=True)
class OutputTokenLimit:
    effective: int
    source: str
    upper_limit: int | None = None


def resolve_output_token_limit(config: LLMConfig) -> OutputTokenLimit:
    """Resolve provider output cap without conflating it with context reserve."""

    explicit = _positive_int_or_none(config.max_output_tokens)
    if explicit is not None:
        return OutputTokenLimit(
            effective=explicit,
            source='max_output_tokens',
            upper_limit=_known_upper_limit(config.model),
        )

    compatibility = _positive_int_or_none(config.output_reserve_tokens)
    if compatibility is not None:
        return OutputTokenLimit(
            effective=compatibility,
            source='output_reserve_tokens_compat',
            upper_limit=_known_upper_limit(config.model),
        )

    default, source, upper_limit = _model_default(config.model)
    return OutputTokenLimit(effective=default, source=source, upper_limit=upper_limit)


def _positive_int_or_none(value: int | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _model_default(model: str) -> tuple[int, str, int | None]:
    normalized = _normalize_model(model)
    upper_limit = _known_upper_limit(model)
    if 'glm-5.1' in normalized or 'glm5.1' in normalized:
        return 32768, 'model_default:glm-5.1', upper_limit
    if 'kimi-k2.7' in normalized or 'kimi-k2.6' in normalized or 'kimi-k2.5' in normalized:
        return 32768, 'model_default:kimi-k2.x', upper_limit
    if 'kimi-for-coding' in normalized:
        return 32768, 'model_default:kimi-for-coding', upper_limit
    if 'claude-3-opus' in normalized or 'claude-3-haiku' in normalized:
        return 4096, 'model_default:legacy_claude_3', upper_limit
    if 'claude-3-sonnet' in normalized:
        return 8192, 'model_default:claude_3_sonnet', upper_limit
    if (
        'claude-4' in normalized
        or 'sonnet-4' in normalized
        or 'opus-4' in normalized
        or 'haiku-4' in normalized
    ):
        return 32768, 'model_default:modern_claude', upper_limit
    return 8192, 'model_default:unknown_anthropic_compatible', upper_limit


def _known_upper_limit(model: str) -> int | None:
    normalized = _normalize_model(model)
    if 'glm-5.1' in normalized or 'glm5.1' in normalized:
        return 128000
    if (
        'claude-4' in normalized
        or 'sonnet-4' in normalized
        or 'opus-4' in normalized
        or 'haiku-4' in normalized
    ):
        return 64000
    return None


def _normalize_model(model: str) -> str:
    return str(model or '').strip().lower().replace('_', '-')
