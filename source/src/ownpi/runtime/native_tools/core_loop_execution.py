from __future__ import annotations

import asyncio
from concurrent.futures import CancelledError

from ownpi.controller.runtime_control_signal import RuntimeControlRequested
from ownpi.runtime.interactions.pause import HumanClarificationPausedError
from ownpi.llm.provider_retry import RetryCancelled
from ownpi.runtime.native_tools.core_loop_session import CoreLoopSession
from ownpi.runtime.native_tools.loop_engine.execution import NativeToolBatchExecutor
from ownpi.runtime.tools.runtime import ToolRuntimeContext


def _flush_pending_board_update(
    context: ToolRuntimeContext, execution_exit: BaseException | None,
) -> None:
    service = context.execution_runtime_service
    if service is None:
        return

    def priority(error: BaseException) -> int:
        if isinstance(error, (RuntimeControlRequested, HumanClarificationPausedError, CancelledError, asyncio.CancelledError, RetryCancelled)):
            return 1
        return 0 if isinstance(error, Exception) else 2

    try:
        service.flush_pending_board_update()
    except BaseException as error:
        # Board I/O is handled by the service; other exits follow F5 priority.
        if execution_exit is None or error is execution_exit:
            raise
        if priority(error) > priority(execution_exit):
            error.add_note(f'Earlier execution exit before board flush: {type(execution_exit).__name__}: {execution_exit}')
            raise
        execution_exit.add_note(f'Additional board flush exit: {type(error).__name__}: {error}')


def build_core_loop_executor(session: CoreLoopSession, context: ToolRuntimeContext) -> NativeToolBatchExecutor:
    return NativeToolBatchExecutor(
        executor=session.executor, context=context, state=session.execution,
        exchange_id=lambda: (
            session.shared.current_response.exchange_id if session.shared.current_response is not None else ''
        ),
        on_started=lambda: session.on_tool_started,
        on_finished=lambda error: _flush_pending_board_update(context, error),
    )


__all__ = ["build_core_loop_executor"]
