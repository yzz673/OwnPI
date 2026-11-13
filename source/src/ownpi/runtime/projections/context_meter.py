from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Mapping


DEFAULT_CONTEXT_METER_WIDTH = 24


def build_context_meter_summary(
    *,
    query_working_set_summary: Mapping[str, Any] | None = None,
    token_budget_snapshot: Mapping[str, Any] | Any = None,
    display_stage: str | None = None,
    display_attempt: int | None = None,
    query_chain_id: str | None = None,
    continuation_index: int | None = None,
    provider_operation: str | None = None,
    provider_elapsed_ms: float | None = None,
    provider_inflight: bool | None = None,
    live_surface_capable: bool | None = None,
    meter_width: int | None = None,
    show_threshold: bool | None = None,
    meter_enabled: bool | None = None,
    surface_mode: str | None = None,
) -> dict[str, Any]:
    query_summary = dict(query_working_set_summary or {})
    snapshot = _normalize_mapping(token_budget_snapshot)

    rendered_tokens = int(query_summary.get('final_prompt_visible_tokens', 0) or 0)
    estimated_tokens = int(
        query_summary.get(
            'total_estimated_tokens',
            snapshot.get('estimated_input_tokens', 0),
        )
        or 0
    )
    max_context_tokens = int(
        query_summary.get(
            'max_context_tokens',
            snapshot.get('context_window_limit', 0),
        )
        or 0
    )
    output_reserve_tokens = int(
        query_summary.get(
            'output_reserve_tokens',
            snapshot.get('estimated_output_reserve', 0),
        )
        or 0
    )
    safety_margin_tokens = int(
        query_summary.get(
            'safety_margin_tokens',
            snapshot.get('safety_margin', 0),
        )
        or 0
    )
    threshold_tokens = int(query_summary.get('threshold_tokens', 0) or 0)
    if threshold_tokens <= 0 and max_context_tokens > 0:
        threshold_tokens = max(
            0,
            max_context_tokens - output_reserve_tokens - safety_margin_tokens,
        )

    distance_to_threshold = int(query_summary.get('distance_to_threshold', 0) or 0)
    if threshold_tokens > 0:
        distance_to_threshold = threshold_tokens - estimated_tokens

    usage_ratio_to_window = (
        estimated_tokens / max_context_tokens
        if max_context_tokens > 0
        else 0.0
    )
    usage_ratio_to_threshold = (
        estimated_tokens / threshold_tokens
        if threshold_tokens > 0
        else 0.0
    )
    usage_percentage = float(query_summary.get('usage_percentage', 0.0) or 0.0)
    if usage_percentage <= 0.0 and usage_ratio_to_window > 0.0:
        usage_percentage = round(usage_ratio_to_window * 100, 2)
    threshold_marker_ratio = (
        threshold_tokens / max_context_tokens
        if max_context_tokens > 0 and threshold_tokens > 0
        else 0.0
    )
    pressure_level = (
        str(query_summary.get('pressure_level', '')).strip()
        or str(snapshot.get('pressure_level', '')).strip()
        or _infer_pressure_level(
            estimated_tokens=estimated_tokens,
            max_context_tokens=max_context_tokens,
            output_reserve_tokens=output_reserve_tokens,
            safety_margin_tokens=safety_margin_tokens,
        )
    )
    accounting_strategy = str(
        query_summary.get(
            'accounting_strategy',
            snapshot.get('estimation_strategy', 'summary_visible_chars'),
        )
        or 'summary_visible_chars'
    )
    category_token_estimates = (
        dict(query_summary.get('category_token_estimates', {}))
        if isinstance(query_summary.get('category_token_estimates'), dict)
        else (
            dict(snapshot.get('category_token_estimates', {}))
            if isinstance(snapshot.get('category_token_estimates'), dict)
            else {}
        )
    )
    rendered_char_counts = (
        dict(query_summary.get('rendered_char_counts', {}))
        if isinstance(query_summary.get('rendered_char_counts'), dict)
        else (
            dict(snapshot.get('rendered_char_counts', {}))
            if isinstance(snapshot.get('rendered_char_counts'), dict)
            else {}
        )
    )
    provider_prompt_tokens = _optional_int(snapshot.get('provider_prompt_tokens'))
    provider_completion_tokens = _optional_int(snapshot.get('provider_completion_tokens'))
    provider_total_tokens = _optional_int(snapshot.get('provider_total_tokens'))
    provider_cache_read_tokens = _optional_int(snapshot.get('provider_cache_read_tokens'))
    provider_cache_write_tokens = _optional_int(snapshot.get('provider_cache_write_tokens'))
    estimate_vs_provider_delta = _optional_int(snapshot.get('estimate_vs_provider_delta'))
    usage_source = str(snapshot.get('usage_source', '') or ('provider_reported' if provider_total_tokens is not None else 'estimated_only'))
    return {
        'rendered_tokens': rendered_tokens,
        'estimated_tokens': estimated_tokens,
        'max_context_tokens': max_context_tokens,
        'threshold_tokens': threshold_tokens,
        'output_reserve_tokens': output_reserve_tokens,
        'safety_margin_tokens': safety_margin_tokens,
        'usage_ratio_to_window': usage_ratio_to_window,
        'usage_ratio_to_threshold': usage_ratio_to_threshold,
        'window_usage_ratio': usage_ratio_to_window,
        'threshold_usage_ratio': usage_ratio_to_threshold,
        'threshold_marker_ratio': threshold_marker_ratio,
        'distance_to_threshold': distance_to_threshold,
        'usage_percentage': usage_percentage,
        'pressure_level': pressure_level,
        'query_time_rendered': bool(query_summary.get('query_time_rendered', False)),
        'accounting_strategy': accounting_strategy,
        'category_token_estimates': {
            str(key): int(value or 0)
            for key, value in category_token_estimates.items()
        },
        'rendered_char_counts': {
            str(key): int(value or 0)
            for key, value in rendered_char_counts.items()
        },
        'budget_source': str(query_summary.get('budget_source', snapshot.get('budget_source', '')) or ''),
        'budget_limit_source': str(
            query_summary.get(
                'budget_limit_source',
                snapshot.get('budget_limit_source', ''),
            )
            or ''
        ),
        'provider_prompt_tokens': provider_prompt_tokens,
        'provider_completion_tokens': provider_completion_tokens,
        'provider_total_tokens': provider_total_tokens,
        'provider_cache_read_tokens': provider_cache_read_tokens,
        'provider_cache_write_tokens': provider_cache_write_tokens,
        'estimate_vs_provider_delta': estimate_vs_provider_delta,
        'usage_source': usage_source,
        'display_stage': str(display_stage or ''),
        'display_attempt': int(display_attempt or 0),
        'query_chain_id': str(query_chain_id or ''),
        'continuation_index': int(continuation_index or 0),
        'provider_operation': str(provider_operation or ''),
        'provider_elapsed_ms': float(provider_elapsed_ms or 0.0),
        'provider_inflight': bool(provider_inflight),
        'live_surface_capable': bool(live_surface_capable) if live_surface_capable is not None else False,
        'meter_width': max(8, int(meter_width or DEFAULT_CONTEXT_METER_WIDTH)),
        'threshold_visible': True if show_threshold is None else bool(show_threshold),
        'meter_enabled': True if meter_enabled is None else bool(meter_enabled),
        'surface_mode': str(surface_mode or ''),
        'has_usage_data': bool(
            max_context_tokens > 0
            or estimated_tokens > 0
            or rendered_tokens > 0
            or bool(snapshot)
            or bool(query_summary)
        ),
    }


def build_context_meter_from_runtime_state(
    state: Any,
    *,
    provider_operation: str | None = None,
    provider_elapsed_ms: float | None = None,
    provider_inflight: bool | None = None,
    live_surface_capable: bool | None = None,
) -> dict[str, Any]:
    context_state = getattr(state, 'context_state', None)
    workflow_state = getattr(state, 'workflow_state', None)
    if context_state is None or workflow_state is None:
        return build_context_meter_summary(
            live_surface_capable=live_surface_capable,
            provider_operation=provider_operation,
            provider_elapsed_ms=provider_elapsed_ms,
            provider_inflight=provider_inflight,
        )
    scratchpad = getattr(workflow_state, 'scratchpad', {})
    continuation_index = (
        int(scratchpad.get('continuation_index', 0) or 0)
        if isinstance(scratchpad, dict)
        else 0
    )
    display_stage = str(getattr(getattr(workflow_state, 'current_stage', None), 'value', '') or '')
    display_attempt = int(getattr(workflow_state, 'current_stage_attempt', 0) or 0)
    query_chain_id = str(getattr(workflow_state, 'current_query_chain_id', '') or '')
    token_budget = _normalize_mapping(
        getattr(context_state, 'token_budget_snapshot', None)
    )
    runtime_preferences = getattr(state, 'runtime_preferences', None)
    context_meter_preferences = (
        getattr(runtime_preferences, 'context_meter', {})
        if runtime_preferences is not None
        else {}
    )
    meter_enabled = bool(context_meter_preferences.get('enabled', True)) if isinstance(context_meter_preferences, dict) else True
    surface_mode = str(context_meter_preferences.get('mode', 'auto') or 'auto') if isinstance(context_meter_preferences, dict) else 'auto'
    return build_context_meter_summary(
        token_budget_snapshot=token_budget,
        display_stage=display_stage,
        display_attempt=display_attempt,
        query_chain_id=query_chain_id,
        continuation_index=continuation_index,
        provider_operation=provider_operation,
        provider_elapsed_ms=provider_elapsed_ms,
        provider_inflight=provider_inflight,
        live_surface_capable=live_surface_capable,
        meter_width=int(context_meter_preferences.get('width', DEFAULT_CONTEXT_METER_WIDTH) or DEFAULT_CONTEXT_METER_WIDTH)
        if isinstance(context_meter_preferences, dict)
        else DEFAULT_CONTEXT_METER_WIDTH,
        show_threshold=bool(context_meter_preferences.get('show_threshold', True))
        if isinstance(context_meter_preferences, dict)
        else True,
        meter_enabled=meter_enabled,
        surface_mode=surface_mode,
    )


def render_context_meter_bar(
    summary: Mapping[str, Any] | None,
    *,
    width: int = DEFAULT_CONTEXT_METER_WIDTH,
    show_threshold: bool = True,
) -> str:
    safe_width = max(8, int(width or DEFAULT_CONTEXT_METER_WIDTH))
    normalized = dict(summary or {})
    if not normalized.get('has_usage_data', False):
        return f"[{'?' * safe_width}]"
    ratio = _clamp_ratio(float(normalized.get('usage_ratio_to_window', 0.0) or 0.0))
    threshold_ratio = _clamp_ratio(float(normalized.get('threshold_marker_ratio', 0.0) or 0.0))
    filled = min(safe_width, max(0, int(ratio * safe_width)))
    if ratio > 0.0 and filled == 0:
        filled = 1
    bar = ['█' if index < filled else '░' for index in range(safe_width)]
    if show_threshold and threshold_ratio > 0.0:
        marker_index = min(
            safe_width - 1,
            max(0, int(round(threshold_ratio * (safe_width - 1)))),
        )
        bar[marker_index] = '┃' if marker_index < filled else '│'
    return '[' + ''.join(bar) + ']'


def format_context_meter_details(summary: Mapping[str, Any] | None) -> str:
    normalized = dict(summary or {})
    if not normalized.get('has_usage_data', False):
        return 'estimated=unavailable threshold=unavailable max=unavailable'
    provider_segment = (
        f"provider={int(normalized.get('provider_total_tokens'))} "
        if normalized.get('provider_total_tokens') is not None
        else ''
    )
    return (
        f"estimated={int(normalized.get('estimated_tokens', 0) or 0)} "
        f"{provider_segment}"
        f"threshold={int(normalized.get('threshold_tokens', 0) or 0)} "
        f"max={int(normalized.get('max_context_tokens', 0) or 0)} "
        f"delta={int(normalized.get('distance_to_threshold', 0) or 0)} "
        f"pressure={normalized.get('pressure_level', 'unknown')}"
    )


def format_context_meter_summary_line(
    summary: Mapping[str, Any] | None,
    *,
    width: int = DEFAULT_CONTEXT_METER_WIDTH,
    show_threshold: bool = True,
) -> str:
    normalized = dict(summary or {})
    bar = render_context_meter_bar(normalized, width=width, show_threshold=show_threshold)
    if not normalized.get('has_usage_data', False):
        return f"{bar} unavailable"
    provider_segment = (
        f"provider={int(normalized.get('provider_total_tokens'))} "
        if normalized.get('provider_total_tokens') is not None
        else ''
    )
    return (
        f"{bar} rendered={int(normalized.get('rendered_tokens', 0) or 0)} "
        f"estimated={int(normalized.get('estimated_tokens', 0) or 0)}/"
        f"{int(normalized.get('max_context_tokens', 0) or 0)} "
        f"{provider_segment}"
        f"threshold={int(normalized.get('threshold_tokens', 0) or 0)} "
        f"({round(float(normalized.get('usage_ratio_to_threshold', 0.0) or 0.0) * 100, 2)}% of threshold)"
    )


def summarize_context_meter_projection(
    summary: Mapping[str, Any] | None,
    *,
    width: int | None = None,
    show_threshold: bool | None = None,
) -> dict[str, Any]:
    normalized = dict(summary or {})
    resolved_width = max(
        8,
        int(normalized.get('meter_width', width or DEFAULT_CONTEXT_METER_WIDTH) or DEFAULT_CONTEXT_METER_WIDTH),
    )
    resolved_show_threshold = (
        bool(normalized.get('threshold_visible', True))
        if show_threshold is None
        else bool(show_threshold)
    )
    return {
        **normalized,
        'bar': render_context_meter_bar(normalized, width=resolved_width, show_threshold=resolved_show_threshold),
        'detail_line': format_context_meter_details(normalized),
        'summary_line': format_context_meter_summary_line(
            normalized,
            width=resolved_width,
            show_threshold=resolved_show_threshold,
        ),
        'meter_width': resolved_width,
        'threshold_visible': resolved_show_threshold,
    }


def _normalize_mapping(payload: Any) -> dict[str, Any]:
    if payload is None:
        return {}
    if isinstance(payload, dict):
        return dict(payload)
    if isinstance(payload, Mapping):
        return dict(payload)
    if is_dataclass(payload):
        return asdict(payload)
    if hasattr(payload, '__dict__'):
        return {
            str(key): value
            for key, value in vars(payload).items()
            if not str(key).startswith('_')
        }
    return {}


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _infer_pressure_level(
    *,
    estimated_tokens: int,
    max_context_tokens: int,
    output_reserve_tokens: int,
    safety_margin_tokens: int,
) -> str:
    if max_context_tokens <= 0:
        return 'unknown'
    remaining_budget = max_context_tokens - estimated_tokens
    if remaining_budget <= output_reserve_tokens + safety_margin_tokens:
        return 'critical'
    if remaining_budget <= 24000:
        return 'high'
    if remaining_budget <= 48000:
        return 'medium'
    return 'low'


def _clamp_ratio(value: float) -> float:
    return max(0.0, min(1.0, float(value or 0.0)))
