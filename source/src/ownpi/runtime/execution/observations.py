from __future__ import annotations

from dataclasses import replace

from ownpi.runtime.execution.adapters import observation_from_native_message
from ownpi.runtime.execution.state import ExecutionObservation
from ownpi.runtime.native_tools.messages import NativeToolMessage


def execution_observation_from_native_message(
    message: NativeToolMessage,
) -> ExecutionObservation | None:
    if message.message_type != 'tool_result':
        return None
    payload = message.payload if isinstance(message.payload, dict) else {}
    tool_name = str(payload.get('tool_name', message.tool_name) or message.tool_name)
    data = payload.get('data') if isinstance(payload.get('data'), dict) else {}
    metadata = payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {}
    observation = observation_from_native_message(
        message=message,
        tool_name=tool_name,
        data=data,
        metadata=metadata,
    )
    if observation is None:
        return None
    return replace(observation, evidence={
        **observation.evidence,
        **{key: data[key] for key in ('exit_code', 'closed', 'final_seq') if key in data},
        **{key: value for key, value in (
            ('request_id', message.request_id), ('provider_call_id', message.provider_call_id),
        ) if value},
    })


__all__ = ['execution_observation_from_native_message']
