"""Responses item identity and event type mappings."""

from typing import Any

from ownpi.llm.streaming import CanonicalResponseEventType
from ownpi.runtime.native_tools.provider import ProviderOutputItemKind


def _responses_item_id(
    item: dict[str, Any],
    payload: dict[str, Any],
    *,
    fallback_index: int,
) -> str:
    return str(
        item.get("id")
        or payload.get("item_id")
        or item.get("call_id")
        or f"output-{payload.get('output_index', fallback_index)}"
    )


def _responses_delta_type(
    event_name: str,
) -> tuple[str, CanonicalResponseEventType]:
    if "reasoning" in event_name:
        return (
            ProviderOutputItemKind.REASONING.value,
            CanonicalResponseEventType.ASSISTANT_REASONING_DELTA,
        )
    if "arguments" in event_name or "tool_call_input" in event_name:
        return (
            ProviderOutputItemKind.TOOL_CALL.value,
            CanonicalResponseEventType.TOOL_CALL_ARGUMENTS_DELTA,
        )
    return (
        ProviderOutputItemKind.MESSAGE.value,
        CanonicalResponseEventType.ASSISTANT_TEXT_DELTA,
    )


def _integer_usage(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): item
        for key, item in value.items()
        if type(item) is int and item >= 0
    }
