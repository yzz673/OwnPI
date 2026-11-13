from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ownpi.stage_ids import canonical_stage_id


CALL_CONTINUATION_OPERATION_SCRATCHPAD_KEY = 'call_continuation_v2_operation'
OP_NATIVE_TOOL_LOOP = 'native_tool_loop'

CALL_CONTINUATION_OPERATION_STATUSES = {
    OP_NATIVE_TOOL_LOOP: frozenset({'pending'}),
}
_CALL_CONTINUATION_OPERATION_FIELDS = frozenset(
    {
        'operation',
        'stage',
        'attempt',
        'status',
        'continuation_index',
        'reason',
        'payload',
    }
)


@dataclass(frozen=True)
class CallContinuationOperation:
    operation: str
    stage: str
    attempt: int
    status: str = 'pending'
    continuation_index: int = 0
    reason: str = ''
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Any) -> 'CallContinuationOperation | None':
        if not isinstance(data, dict) or not data:
            return None
        if set(data) - _CALL_CONTINUATION_OPERATION_FIELDS:
            return None
        operation = str(data.get('operation', '') or '').strip()
        stage = canonical_stage_id(data.get('stage', '') or '')
        status = str(data.get('status', '') or '').strip()
        attempt = data.get('attempt')
        continuation_index = data.get('continuation_index', 0)
        payload = data.get('payload', {})
        if (
            not operation
            or not stage
            or status not in CALL_CONTINUATION_OPERATION_STATUSES.get(operation, ())
            or isinstance(attempt, bool)
            or not isinstance(attempt, int)
            or attempt <= 0
            or isinstance(continuation_index, bool)
            or not isinstance(continuation_index, int)
            or continuation_index < 0
            or not isinstance(payload, dict)
        ):
            return None
        return cls(
            operation=operation,
            stage=stage,
            attempt=attempt,
            status=status,
            continuation_index=continuation_index,
            reason=str(data.get('reason', '') or ''),
            payload=dict(payload),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            'operation': self.operation,
            'stage': self.stage,
            'attempt': self.attempt,
            'status': self.status,
            'continuation_index': self.continuation_index,
            'reason': self.reason,
            'payload': dict(self.payload),
        }


def pending_operation(
    *,
    operation: str,
    stage: str,
    attempt: int,
    continuation_index: int,
    reason: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return CallContinuationOperation(
        operation=operation,
        stage=stage,
        attempt=attempt,
        continuation_index=continuation_index,
        reason=reason,
        payload=dict(payload or {}),
    ).to_dict()


__all__ = [
    'CALL_CONTINUATION_OPERATION_SCRATCHPAD_KEY',
    'CALL_CONTINUATION_OPERATION_STATUSES',
    'OP_NATIVE_TOOL_LOOP',
    'CallContinuationOperation',
    'pending_operation',
]
