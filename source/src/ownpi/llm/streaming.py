from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from ownpi.runtime.native_tools.provider import ProviderOutputItem


class CanonicalResponseEventType(str, Enum):
    RESPONSE_STARTED = 'response_started'
    ITEM_STARTED = 'item_started'
    ASSISTANT_TEXT_DELTA = 'assistant_text_delta'
    ASSISTANT_REASONING_DELTA = 'assistant_reasoning_delta'
    TOOL_CALL_ARGUMENTS_DELTA = 'tool_call_arguments_delta'
    ITEM_COMPLETED = 'item_completed'
    USAGE_UPDATED = 'usage_updated'
    ATTEMPT_DISCARDED = 'attempt_discarded'
    STREAM_ERROR = 'stream_error'
    RESPONSE_COMPLETED = 'response_completed'

    # Structured-output streaming uses the same event envelope during migration.
    REQUEST_STARTED = 'request_started'
    JSON_DELTA = 'json_delta'
    COMPLETED = 'completed'


@dataclass(frozen=True)
class CanonicalResponseEvent:
    event_type: str | CanonicalResponseEventType
    request_kind: str = ''
    response_mode: str = ''
    response_id: str = ''
    item_id: str = ''
    item_type: str = ''
    sequence_index: int = -1
    delta: str = ''
    completed_item: ProviderOutputItem | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    usage: Mapping[str, int] = field(default_factory=dict)
    attempt: int = 1
    stop_reason: str = ''
    response_status: str = ''
    provider_protocol: str = ''
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def type_value(self) -> str:
        value = self.event_type
        return value.value if isinstance(value, CanonicalResponseEventType) else str(value)


# Existing structured-stream call sites keep this import while sharing one envelope.
LLMStreamEvent = CanonicalResponseEvent


__all__ = [
    'CanonicalResponseEvent',
    'CanonicalResponseEventType',
    'LLMStreamEvent',
]
