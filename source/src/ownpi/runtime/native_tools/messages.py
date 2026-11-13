from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

@dataclass(frozen=True)
class NativeToolMessage:
    message_type: str
    stage: str
    attempt: int
    continuation_index: int
    turn_index: int = 0
    provider_call_id: str = ''
    request_id: str = ''
    tool_name: str = ''
    evidence_lane: str = 'control'
    text: str = ''
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = ''
    native_message_id: str = ''
    native_exchange_id: str = ''
    native_content_hash: str = ''
    native_revision: str = ''

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'NativeToolMessage':
        return cls(
            message_type=str(data.get('message_type', '') or ''),
            stage=str(data.get('stage', '') or ''),
            attempt=int(data.get('attempt', 0) or 0),
            continuation_index=int(data.get('continuation_index', 0) or 0),
            turn_index=int(data.get('turn_index', 0) or 0),
            provider_call_id=str(data.get('provider_call_id', '') or ''),
            request_id=str(data.get('request_id', '') or ''),
            tool_name=str(data.get('tool_name', '') or ''),
            evidence_lane=str(data.get('evidence_lane', 'control') or 'control'),
            text=str(data.get('text', '') or ''),
            payload=dict(data.get('payload', {})) if isinstance(data.get('payload'), dict) else {},
            created_at=str(data.get('created_at', '') or ''),
            native_message_id=str(data.get('native_message_id', '') or ''),
            native_exchange_id=str(data.get('native_exchange_id', '') or ''),
            native_content_hash=str(data.get('native_content_hash', '') or ''),
            native_revision=str(data.get('native_revision', '') or ''),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
