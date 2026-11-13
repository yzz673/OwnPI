from __future__ import annotations

from typing import Any

from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.limits.recovery import (
    DEFAULT_WAIT_FOR_EXECUTION_UPDATE_TIMEOUT_SECONDS,
)
from ownpi.runtime.tools.definitions import (
    ToolDefinition,
    ToolExecutionContext,
    ToolExposure,
)
from ownpi.runtime.tools.role_policy import tool_role_allowed


def build_wait_for_execution_update_definition() -> ToolDefinition:
    return ToolDefinition(
        name='wait_for_execution_update',
        exposure=ToolExposure.DIRECT,
        kind='execution_wait',
        description='Wait in the current tool loop for a material Execution Board update.',
        detail=(
            'Use only after reading the current Execution Board when active jobs remain and '
            'there is no independent work to do. Pass the board material_revision. The call '
            'returns on a material update, runtime cancellation, or the runtime-owned timeout.'
        ),
        read_only=True,
        concurrency_safe=False,
        permission_class='inspection',
        invocation_mode='agent_requested',
        handler=run_wait_for_execution_update,
        input_schema_builder=lambda: {
            'type': 'object',
            'properties': {
                'after_revision': {'type': 'integer', 'minimum': 0},
            },
            'required': ['after_revision'],
            'additionalProperties': False,
        },
        observation_family='execution_board_update',
        result_family='execution_wait',
        toolset_family='control',
        backend_kind='runtime',
        execution_class='read_only',
        side_effect_level='none',
        resume_strategy='stateless',
        max_prompt_result_chars=800,
    )


async def run_wait_for_execution_update(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> ToolResult:
    if not tool_role_allowed('wait_for_execution_update', context.actor_kind):
        return _failure('wait_for_execution_update is available only to Core')
    service = getattr(context.state_store, 'execution_runtime_service', None)
    if service is None:
        return _failure('execution runtime service is unavailable')
    board_ref = service.active_board_ref
    if board_ref is None:
        return _failure('the run-level Execution Board is unavailable')
    registry = service.registry_snapshot()
    if not registry.active_non_terminal:
        return _failure('no active execution is available to wait for')
    after_revision = arguments.get('after_revision')
    if isinstance(after_revision, bool) or not isinstance(after_revision, int):
        return _failure('after_revision must be a non-negative integer')
    if after_revision < 0:
        return _failure('after_revision must be a non-negative integer')
    if after_revision > registry.material_revision:
        return _failure('after_revision is newer than the canonical Registry revision')
    status, current_revision, current_board = await service.wait_for_update_async(
        after_revision=after_revision,
        timeout_seconds=DEFAULT_WAIT_FOR_EXECUTION_UPDATE_TIMEOUT_SECONDS,
        cancel_checker=context.cancel_checker,
    )
    board = current_board or board_ref
    return ToolResult(
        ok=True,
        tool_name='wait_for_execution_update',
        data={
            'status': status,
            'previous_revision': after_revision,
            'current_revision': current_revision,
            'execution_board': board.to_dict(),
        },
        stdout=(
            f'Execution Board wait finished with status={status}; '
            f'revision={after_revision}->{current_revision}.'
        ),
    )


def _failure(message: str) -> ToolResult:
    return ToolResult(
        ok=False,
        tool_name='wait_for_execution_update',
        data={'status': 'rejected'},
        error=message,
    )


__all__ = [
    'build_wait_for_execution_update_definition',
    'run_wait_for_execution_update',
]
