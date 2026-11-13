from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ownpi.runtime.native_tools.core_loop_execution import _flush_pending_board_update
from ownpi.runtime.native_tools.executor import NativeToolExecutor, NativeToolExecutionOutcome
from ownpi.runtime.native_tools.loop_engine.contracts import NativeExecutionRequest, NativeLoopToolDelivery
from ownpi.runtime.native_tools.loop_engine.execution import (
    NativeToolBatchExecutor,
    NativeToolBatchState,
)
from ownpi.runtime.tools.runtime import ToolRuntimeContext
from ownpi.runtime.native_tools.loop_outcome import pending_interaction, pending_tool_outcome, tool_execution_cancelled


@dataclass
class PlanModeToolExecutor:
    """Adapt the shared runtime tool executor to the generic plan loop ABI."""

    executor: NativeToolExecutor
    context: ToolRuntimeContext
    _state: NativeToolBatchState = field(default_factory=NativeToolBatchState, init=False, repr=False)
    _batch_executor: NativeToolBatchExecutor | None = field(default=None, init=False, repr=False)
    _batch_context: ToolRuntimeContext | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._refresh_batch_executor()

    @property
    def turn_outcomes(self) -> tuple[NativeToolExecutionOutcome, ...]:
        return self._state.turn_outcomes

    @turn_outcomes.setter
    def turn_outcomes(self, value: tuple[NativeToolExecutionOutcome, ...]) -> None:
        self._state.turn_outcomes = value

    @property
    def all_outcomes(self) -> list[NativeToolExecutionOutcome]:
        return self._state.all_outcomes

    def _refresh_batch_executor(self) -> NativeToolBatchExecutor:
        if self._batch_executor is not None and self._batch_context is self.context:
            return self._batch_executor
        context = self.context
        self._batch_context = context
        self._batch_executor = NativeToolBatchExecutor(
            executor=self.executor,
            context=context,
            state=self._state,
            exchange_id=lambda: self.context.conversation_exchange_id,
            on_started=lambda: None,
            on_finished=lambda error: _flush_pending_board_update(self.context, error),
        )
        return self._batch_executor

    def drain_retained_deliveries(self) -> tuple[NativeLoopToolDelivery, ...]:
        return self._refresh_batch_executor().drain_retained_deliveries()

    def execution_boundary(self) -> dict[str, Any] | None:
        checker = self.context.cancel_checker
        if (checker is not None and checker()) or tool_execution_cancelled(self.turn_outcomes):
            return {'kind': 'user_cancelled'}
        session = pending_interaction(self.context.state_store.current_human_clarification_session())
        if session is not None:
            outcome = pending_tool_outcome(self.turn_outcomes)
            if outcome is not None and outcome.invocation.pending_interaction_session.session_id == session.session_id:
                return {
                    'kind': 'human_input_pending',
                    'session_id': session.session_id,
                    'tool_name': outcome.call.tool_name,
                    'request_id': outcome.call.request_id,
                    'provider_call_id': outcome.call.provider_call_id,
                    'tool_request_id': session.tool_request_id,
                    'data': {'questions': [question.to_dict() for question in session.questions]},
                }
        return None

    def execute_many(
        self,
        calls: tuple[NativeExecutionRequest, ...],
    ) -> tuple[NativeLoopToolDelivery, ...]:
        return self._refresh_batch_executor().execute_many(
            calls, tool_call_id=self.context.tool_call_id,
        )


__all__ = ["PlanModeToolExecutor"]
