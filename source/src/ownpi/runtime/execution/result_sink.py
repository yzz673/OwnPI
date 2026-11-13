from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ownpi.runtime.events import now_utc_iso
from ownpi.runtime.execution.observations import (
    execution_observation_from_native_message,
)
from ownpi.runtime.native_tools.messages import NativeToolMessage
from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.execution.state import ExecutionHandle, ExecutionObservation, ExecutionOrigin

if TYPE_CHECKING:
    from ownpi.runtime.execution.commit_gateway import ExecutionCommitReceipt
    from ownpi.runtime.process_owner.manager import ProcessOwnerSessionView


def commit_execution_tool_result(
    *,
    service: Any | None,
    stage: str,
    attempt: int,
    continuation_index: int,
    request_id: str,
    provider_call_id: str,
    result: ToolResult,
) -> ExecutionCommitReceipt | None:
    if service is None or result.metadata.get('recorded_terminal'):
        return None
    observation = execution_observation_from_native_message(
        NativeToolMessage(
            message_type='tool_result',
            stage=stage,
            attempt=attempt,
            continuation_index=continuation_index,
            provider_call_id=provider_call_id,
            request_id=request_id,
            tool_name=result.tool_name,
            text=result.stdout or '',
            payload=result.to_dict(),
            created_at=now_utc_iso(),
        )
    )
    if observation is None:
        return None
    if not result.ok and not result.metadata.get('wait_cancelled', False) and (
        result.tool_name not in {
            'execution_monitor',
            'execution_kill',
            'write_stdin',
            'remote_monitor',
        }
        or not (observation.terminal or observation.status == 'unknown')
    ):
        return None
    return service.commit_tool_observation(observation)


def publish_execution_tool_result(context: Any, result: ToolResult) -> ToolResult:
    """Execution-tool owner handoff, independent of native-loop dispatch."""
    try:
        _publish_owner_bindings(context)
        commit_execution_tool_result(
            service=context.execution_runtime_service,
            stage=str(getattr(context.stage, 'value', context.stage)),
            attempt=context.attempt, continuation_index=context.continuation_index,
            request_id=context.tool_call_id, provider_call_id='', result=result,
        )
    except Exception:
        logging.getLogger(__name__).exception(
            'Execution observation handoff failed: tool=%s call=%s',
            result.tool_name, context.tool_call_id,
        )
    return result


def publish_acquired_execution_on_failure(context: Any, view: ProcessOwnerSessionView) -> None:
    """Retain an acquired process when its initial readback cannot return a result."""
    service = context.execution_runtime_service
    if service is None:
        return
    try:
        _publish_owner_bindings(context)
        service.commit_tool_observation(ExecutionObservation(
            execution_id=f'local:{view.owner_id}:{view.process_id}',
            status='unknown',
            handle=ExecutionHandle(
                backend='local_process', tool_name='exec_command',
                owner_id=view.owner_id, process_id=view.process_id,
                public_session_id=view.public_session_id, local_workdir=view.cwd,
                stdout_ref=view.stdout_ref, stderr_ref=view.stderr_ref,
                timeout_seconds=view.timeout_seconds, sandbox_backend=view.sandbox_backend,
                command_display=view.command_display,
            ),
            origin=ExecutionOrigin(
                stage=str(getattr(context.stage, 'value', context.stage)),
                attempt=context.attempt, continuation_index=context.continuation_index,
                tool_name='exec_command',
            ),
            observed_at=now_utc_iso(),
            evidence={'request_id': str(getattr(context, 'tool_call_id', '') or '')},
        ))
    except Exception:
        logging.getLogger(__name__).exception(
            'Acquired execution handoff failed: owner=%s process=%s', view.owner_id, view.process_id,
        )


def _publish_owner_bindings(context: Any) -> None:
    manager = getattr(context, 'process_owner_manager', None)
    bindings = getattr(getattr(manager, 'bindings', None), 'bindings', {})
    publish = getattr(context.execution_runtime_service, 'record_owner_binding', None)
    if callable(publish):
        for binding in tuple(bindings.values()):
            publish(binding)


__all__ = ['commit_execution_tool_result', 'publish_execution_tool_result',
           'publish_acquired_execution_on_failure']
