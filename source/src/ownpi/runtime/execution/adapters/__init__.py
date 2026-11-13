from typing import Any

from ownpi.runtime.execution.backend_adapter import ExecutionBackendAdapter
from ownpi.runtime.execution.adapters.local_process import (
    LOCAL_PROCESS_OBSERVATION_TOOLS,
    LocalProcessExecutionAdapter,
    local_process_adapter,
)
from ownpi.runtime.execution.adapters.remote_workflow import (
    REMOTE_WORKFLOW_OBSERVATION_TOOLS,
    RemoteWorkflowExecutionAdapter,
    remote_workflow_adapter,
)
from ownpi.runtime.execution.state import ExecutionObservation
from ownpi.runtime.native_tools.messages import NativeToolMessage


EXECUTION_BACKEND_ADAPTERS: tuple[ExecutionBackendAdapter, ...] = (
    remote_workflow_adapter,
    local_process_adapter,
)


def observation_from_native_message(
    message: NativeToolMessage,
    *,
    tool_name: str,
    data: dict[str, Any],
    metadata: dict[str, Any],
) -> ExecutionObservation | None:
    for adapter in EXECUTION_BACKEND_ADAPTERS:
        observation = adapter.observation_from_native_message(
            message=message,
            tool_name=tool_name,
            data=data,
            metadata=metadata,
        )
        if observation is not None:
            return observation
    return None


__all__ = [
    'EXECUTION_BACKEND_ADAPTERS',
    'LOCAL_PROCESS_OBSERVATION_TOOLS',
    'LocalProcessExecutionAdapter',
    'REMOTE_WORKFLOW_OBSERVATION_TOOLS',
    'RemoteWorkflowExecutionAdapter',
    'observation_from_native_message',
    'local_process_adapter',
    'remote_workflow_adapter',
]
