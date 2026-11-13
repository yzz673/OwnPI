from __future__ import annotations

import hashlib
import json
import re
from typing import Any


STATE_DOMAINS = {
    'context',
    'tool',
    'hook',
    'permission',
    'environment',
    'task',
    'memory',
    'mcp',
    'skill',
    'planning',
    'runtime',
}

MUTATION_KINDS = {
    'state_snapshot',
    'state_delta',
    'request_created',
    'decision_recorded',
    'receipt_recorded',
    'result_recorded',
    'cache_refreshed',
}

EMPTY_PAYLOAD_HASH = ''


def stable_payload_hash(payload: Any) -> str:
    if payload is None:
        return EMPTY_PAYLOAD_HASH
    normalized = _json_safe(payload)
    encoded = json.dumps(
        normalized,
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=False,
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()[:16]


def compact_mutation_ref(
    *,
    ref_type: str,
    ref_id: str,
    path: str = '',
    summary: str = '',
) -> dict[str, Any]:
    payload = {
        'ref_type': str(ref_type or '').strip(),
        'ref_id': str(ref_id or '').strip(),
    }
    if str(path or '').strip():
        payload['path'] = str(path or '').strip()
    if str(summary or '').strip():
        payload['summary'] = str(summary or '').strip()
    return payload


def build_turn_event_id(
    *,
    sequence: int,
    event_name: str,
    request_id: str = '',
    session_id: str = '',
    tool_name: str = '',
    scope_id: str = '',
) -> str:
    identity = str(request_id or session_id or tool_name or sequence).strip()
    normalized_scope = str(scope_id or '').strip()
    suffix = f'{normalized_scope}:{identity}' if normalized_scope else identity
    return f'{int(sequence or 0):06d}:{str(event_name or "runtime_event").strip()}:{suffix}'


def next_runtime_event_sequence(events: Any) -> int:
    if not isinstance(events, (list, tuple)):
        return 1
    max_sequence = 0
    for index, event in enumerate(events, start=1):
        if not isinstance(event, dict) and not hasattr(event, 'sequence'):
            max_sequence = max(max_sequence, index)
            continue
        max_sequence = max(
            max_sequence,
            _coerce_positive_int(_event_value(event, 'sequence')),
            _sequence_prefix_from_event_id(_event_value(event, 'event_id')),
            index,
        )
    return max_sequence + 1


def summarize_mutation_event(payload: dict[str, Any], *, limit: int = 180) -> str:
    if not isinstance(payload, dict) or not payload:
        return ''
    summary_keys = (
        'summary',
        'reason',
        'status',
        'event',
        'event_name',
        'tool_name',
        'request_id',
        'session_id',
    )
    parts: list[str] = []
    for key in summary_keys:
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            parts.append(f'{key}={text}')
        if len('; '.join(parts)) >= limit:
            break
    if not parts:
        parts.append(json.dumps(_json_safe(payload), sort_keys=True, ensure_ascii=False, default=str))
    text = '; '.join(parts)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + '…'


def normalize_state_domain(value: str, fallback: str = 'runtime') -> str:
    normalized = str(value or '').strip()
    if normalized in STATE_DOMAINS:
        return normalized
    return fallback if fallback in STATE_DOMAINS else 'runtime'


def normalize_mutation_kind(value: str, fallback: str = 'state_delta') -> str:
    normalized = str(value or '').strip()
    if normalized in MUTATION_KINDS:
        return normalized
    return fallback if fallback in MUTATION_KINDS else 'state_delta'


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, 'to_dict') and callable(value.to_dict):
        return _json_safe(value.to_dict())
    return str(value)


def _coerce_positive_int(value: Any) -> int:
    try:
        coerced = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return coerced if coerced > 0 else 0


def _sequence_prefix_from_event_id(value: Any) -> int:
    match = re.match(r'^\s*(\d+):', str(value or ''))
    if match is None:
        return 0
    return _coerce_positive_int(match.group(1))


def _event_value(event: Any, key: str) -> Any:
    if isinstance(event, dict):
        return event.get(key)
    return getattr(event, key, None)
