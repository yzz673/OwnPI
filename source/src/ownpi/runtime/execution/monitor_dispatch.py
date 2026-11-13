from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from uuid import uuid4

from ownpi.controller.stages import Stage
from ownpi.runtime.events import now_utc_iso
from ownpi.runtime.execution.observations import (
    execution_observation_from_native_message,
)
from ownpi.runtime.execution.state import ExecutionObservation, ExecutionRecord
from ownpi.runtime.native_tools.messages import NativeToolMessage
from ownpi.runtime.tools.planning import ToolEnvironmentSnapshot, ToolPlanContext
from ownpi.runtime.tools.router import ToolRouter
from ownpi.runtime.tools.runtime import ToolRuntime, ToolRuntimeContext
from ownpi.schemas.runtime_tooling import ToolCall


class ExecutionToolDispatcher:
    def __init__(
        self,
        *,
        tool_runtime: ToolRuntime,
        state_store: Any,
        run_dir: Path,
        project_root: Path,
        user_config: Any,
        hook_engine: Any | None = None,
        app_paths: Any | None = None,
        process_owner_manager: Any | None = None,
    ) -> None:
        self._tool_runtime = tool_runtime
        self._state_store = state_store
        self._run_dir = Path(run_dir)
        self._project_root = Path(project_root)
        self._user_config = user_config
        self._hook_engine = hook_engine
        self._app_paths = app_paths
        self._process_owner_manager = process_owner_manager

    def monitor(self, record: ExecutionRecord) -> ExecutionObservation:
        tool_name, arguments = monitor_request_for_record(record)
        return self._dispatch(record, tool_name=tool_name, arguments=arguments)

    def readback(self, record: ExecutionRecord) -> ExecutionObservation:
        tool_name, arguments = readback_request_for_record(record)
        return self._dispatch(record, tool_name=tool_name, arguments=arguments)

    def _dispatch(
        self,
        record: ExecutionRecord,
        *,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ExecutionObservation:
        context = self._context(record)
        request_id = f'execution-monitor-{uuid4().hex}'
        outcome = asyncio.run(self._tool_runtime.run_tool_use(
            context=context,
            request=ToolCall(
                request_id=request_id,
                tool_name=tool_name,
                input=arguments,
                reason='runtime-owned bounded execution monitor poll',
                expected_output='one typed execution monitor observation',
                stage=record.origin.stage,
                attempt=record.origin.attempt,
                continuation_index=record.origin.continuation_index,
                requested_by='runtime_policy',
            ),
            record_request=False,
            execution_protocol='runtime_policy',
        ))
        tool_result = outcome.tool_result
        if tool_result is None:
            raise RuntimeError('monitor tool produced no ToolResult')
        message = NativeToolMessage(
            message_type='tool_result',
            stage=record.origin.stage,
            attempt=record.origin.attempt,
            continuation_index=record.origin.continuation_index,
            request_id=request_id,
            tool_name=tool_name,
            text=tool_result.stdout or '',
            payload=tool_result.to_dict(),
            created_at=now_utc_iso(),
        )
        observation = execution_observation_from_native_message(message)
        if observation is not None and (
            tool_result.ok
            or (tool_name != 'remote_pull' and (observation.terminal or observation.status == 'unknown'))
        ):
            return observation
        if not tool_result.ok:
            raise RuntimeError(str(tool_result.error or 'execution monitor failed'))
        raise RuntimeError(f'{tool_name} result did not produce an execution observation')

    def _context(self, record: ExecutionRecord) -> ToolRuntimeContext:
        state = self._state_store.state
        stage = Stage(record.origin.stage)
        router = ToolRouter.from_context(
            ToolPlanContext.from_runtime_state(
                state,
                tool_search_enabled=True,
                supports_image_input=False,
                allow_human_question=False,
                actor_kind='runtime',
                environment=ToolEnvironmentSnapshot(),
            )
        )
        return ToolRuntimeContext(
            state_store=self._state_store,
            raw_user_message='',
            task_spec_text='',
            project_root=self._project_root,
            run_dir=self._run_dir,
            stage=stage,
            attempt=record.origin.attempt,
            tool_router=router,
            continuation_index=record.origin.continuation_index,
            requested_by='runtime_policy',
            actor_kind='runtime',
            actor_id='execution_scheduler',
            user_config=self._user_config,
            hook_engine=self._hook_engine,
            app_paths=self._app_paths,
            process_owner_manager=self._process_owner_manager,
            execution_handle=record.handle,
        )


def monitor_request_for_record(record: ExecutionRecord) -> tuple[str, dict[str, Any]]:
    handle = record.handle
    if handle.backend == 'local_process':
        return 'execution_monitor', {'execution_id': record.execution_id}
    if handle.backend == 'remote_workflow':
        if not handle.profile:
            raise ValueError('remote execution handle is missing profile')
        arguments = {
            'profile': handle.profile,
            'artifact_root': handle.artifact_root,
        }
        return 'remote_monitor', {
            key: value for key, value in arguments.items() if value
        }
    raise ValueError(f'unsupported durable execution backend: {handle.backend}')


def readback_request_for_record(record: ExecutionRecord) -> tuple[str, dict[str, Any]]:
    handle = record.handle
    if handle.backend != 'remote_workflow':
        raise ValueError(f'execution backend does not support terminal readback: {handle.backend}')
    if not handle.profile or not handle.artifact_root:
        raise ValueError('remote execution handle is missing profile or artifact_root')
    return 'remote_pull', {
        'profile': handle.profile,
        'artifact_scope': 'light',
        'artifact_root': handle.artifact_root,
    }


__all__ = [
    'ExecutionToolDispatcher',
    'monitor_request_for_record',
    'readback_request_for_record',
]
