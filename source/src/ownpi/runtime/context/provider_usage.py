from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def read_provider_usage(usage: Mapping[str, Any]) -> dict[str, int | None]:
    """Read extracted counters without changing provider facts or token semantics."""
    aliases = {
        'provider_prompt_tokens': ('provider_prompt_tokens', 'input_tokens', 'prompt_tokens'),
        'provider_completion_tokens': ('provider_completion_tokens', 'output_tokens', 'completion_tokens'),
        'provider_total_tokens': ('provider_total_tokens', 'total_tokens'),
        'provider_cache_read_tokens': ('provider_cache_read_tokens', 'cache_read_tokens', 'cached_input_tokens'),
        'provider_cache_write_tokens': ('provider_cache_write_tokens', 'cache_write_tokens'),
    }
    result: dict[str, int | None] = dict.fromkeys(aliases)
    for field, keys in aliases.items():
        for key in keys:
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                result[field] = value
                break
    return result
