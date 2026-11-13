from __future__ import annotations

from pathlib import Path
from typing import Any

from ownpi.runtime.limits.projection import (
    TRACE_PAYLOAD_MAX_DEPTH,
    TRACE_PAYLOAD_MAX_KEYS,
    TRACE_PAYLOAD_MAX_LIST_ITEMS,
    TRACE_PAYLOAD_MAX_STRING_CHARS,
)

_SENSITIVE_KEYWORDS = (
    'api_key',
    'apikey',
    'authorization',
    'password',
    'secret',
    'access_token',
    'auth_token',
    'bearer_token',
    'id_token',
    'refresh_token',
)


def redact_payload(
    payload: Any,
    *,
    max_depth: int = TRACE_PAYLOAD_MAX_DEPTH,
    max_keys: int = TRACE_PAYLOAD_MAX_KEYS,
) -> Any:
    return _redact_payload(
        payload,
        depth=0,
        max_depth=max_depth,
        max_keys=max_keys,
        active_ids=set(),
    )


def _redact_payload(
    payload: Any,
    *,
    depth: int,
    max_depth: int,
    max_keys: int,
    active_ids: set[int],
) -> Any:
    if depth >= max_depth:
        return {'truncated': True, 'truncation_reason': 'redaction_max_depth_exceeded'}
    if isinstance(payload, (dict, list, tuple, set)):
        object_id = id(payload)
        if object_id in active_ids:
            return {'truncated': True, 'truncation_reason': 'redaction_cycle_detected'}
        active_ids.add(object_id)
    try:
        if isinstance(payload, dict):
            items = list(payload.items())
            visible = items[:max_keys]
            redacted: dict[str, Any] = {}
            for key, value in visible:
                normalized_key = str(key or '').lower()
                if _is_sensitive_key(normalized_key):
                    redacted[str(key)] = '[REDACTED]'
                    continue
                redacted[str(key)] = _redact_payload(
                    value,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_keys=max_keys,
                    active_ids=active_ids,
                )
            if len(items) > len(visible):
                redacted['__redaction_truncated__'] = {
                    'truncated': True,
                    'truncation_reason': 'redaction_max_keys_exceeded',
                    'omitted_key_count': len(items) - len(visible),
                }
            return redacted
        if isinstance(payload, list):
            return [
                _redact_payload(
                    item,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_keys=max_keys,
                    active_ids=active_ids,
                )
                for item in payload
            ]
        if isinstance(payload, tuple):
            return _redact_payload(
                list(payload),
                depth=depth,
                max_depth=max_depth,
                max_keys=max_keys,
                active_ids=active_ids,
            )
        if isinstance(payload, set):
            return _redact_payload(
                sorted(payload, key=str),
                depth=depth,
                max_depth=max_depth,
                max_keys=max_keys,
                active_ids=active_ids,
            )
        if isinstance(payload, Path):
            return str(payload)
        return payload
    finally:
        if isinstance(payload, (dict, list, tuple, set)):
            active_ids.discard(id(payload))


def _is_sensitive_key(normalized_key: str) -> bool:
    if normalized_key == 'token' or normalized_key.endswith('_token'):
        return True
    return any(keyword in normalized_key for keyword in _SENSITIVE_KEYWORDS)


def budget_payload(
    payload: Any,
    *,
    max_depth: int = TRACE_PAYLOAD_MAX_DEPTH,
    max_keys: int = TRACE_PAYLOAD_MAX_KEYS,
    max_list_items: int = TRACE_PAYLOAD_MAX_LIST_ITEMS,
    max_string_chars: int = TRACE_PAYLOAD_MAX_STRING_CHARS,
) -> Any:
    return _budget_payload(
        payload,
        depth=0,
        max_depth=max_depth,
        max_keys=max_keys,
        max_list_items=max_list_items,
        max_string_chars=max_string_chars,
    )


def _budget_payload(
    payload: Any,
    *,
    depth: int,
    max_depth: int,
    max_keys: int,
    max_list_items: int,
    max_string_chars: int,
) -> Any:
    if depth >= max_depth:
        return {'truncated': True, 'truncation_reason': 'max_depth_exceeded'}
    if isinstance(payload, dict):
        items = list(payload.items())
        visible = items[:max_keys]
        result = {
            str(key): _budget_payload(
                value,
                depth=depth + 1,
                max_depth=max_depth,
                max_keys=max_keys,
                max_list_items=max_list_items,
                max_string_chars=max_string_chars,
            )
            for key, value in visible
        }
        if len(items) > len(visible):
            result['__truncated__'] = {
                'truncated': True,
                'truncation_reason': 'max_keys_exceeded',
                'omitted_key_count': len(items) - len(visible),
            }
        return result
    if isinstance(payload, list):
        visible = payload[:max_list_items]
        result = [
            _budget_payload(
                item,
                depth=depth + 1,
                max_depth=max_depth,
                max_keys=max_keys,
                max_list_items=max_list_items,
                max_string_chars=max_string_chars,
            )
            for item in visible
        ]
        omitted = len(payload) - len(visible)
        if omitted > 0:
            result.append(
                {
                    'truncated': True,
                    'truncation_reason': 'max_list_items_exceeded',
                    'omitted_item_count': omitted,
                }
            )
        return result
    if isinstance(payload, tuple):
        return _budget_payload(
            list(payload),
            depth=depth,
            max_depth=max_depth,
            max_keys=max_keys,
            max_list_items=max_list_items,
            max_string_chars=max_string_chars,
        )
    if isinstance(payload, set):
        return _budget_payload(
            sorted(payload, key=str),
            depth=depth,
            max_depth=max_depth,
            max_keys=max_keys,
            max_list_items=max_list_items,
            max_string_chars=max_string_chars,
        )
    if isinstance(payload, Path):
        return str(payload)
    if isinstance(payload, str):
        if len(payload) <= max_string_chars:
            return payload
        return payload[:max_string_chars] + f'...[truncated {len(payload) - max_string_chars} chars]'
    return payload
