from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ownpi.runtime.native_tools.history_recording import retain_delivery_exit
from ownpi.runtime.native_tools.loop_engine.contracts import (
    NativeExecutionRequest, NativeLoopConversation, NativeLoopExecutor,
    NativeLoopObservationSink, NativeLoopToolDelivery,
)
if TYPE_CHECKING:
    from ownpi.runtime.native_tools.executor import NativeToolExecutionOutcome, NativeToolExecutor


@dataclass
class NativeToolBatchState:
    turn_outcomes: tuple[NativeToolExecutionOutcome, ...] = ()
    all_outcomes: list[NativeToolExecutionOutcome] = field(default_factory=list)
    retained_deliveries: tuple[NativeLoopToolDelivery, ...] = ()


class NativeToolBatchExecutor:
    def __init__(
        self, *, executor: NativeToolExecutor, context: Any, state: NativeToolBatchState,
        exchange_id: Callable[[], str], on_started: Callable[[], Callable[[NativeExecutionRequest], None] | None],
        on_finished: Callable[[BaseException | None], None],
    ) -> None:
        self._executor = executor
        self._context = context
        self._state = state
        self._exchange_id = exchange_id
        self._on_started = on_started
        self._on_finished = on_finished

    def drain_retained_deliveries(self) -> tuple[NativeLoopToolDelivery, ...]:
        deliveries = self._state.retained_deliveries
        self._state.retained_deliveries = ()
        return deliveries

    def execute_many(
        self, calls: tuple[NativeExecutionRequest, ...], *, tool_call_id: str = '',
    ) -> tuple[NativeLoopToolDelivery, ...]:
        state = self._state
        state.retained_deliveries = ()
        state.turn_outcomes = ()
        collected: list[NativeToolExecutionOutcome] = []
        execution_exit: BaseException | None = None
        try:
            self._executor.execute(
                context=replace(
                    self._context, tool_call_id=tool_call_id,
                    conversation_exchange_id=self._exchange_id(),
                ),
                calls=calls, on_started=self._on_started(), on_outcome=collected.append,
            )
        except BaseException as error:
            execution_exit = error
            raise
        finally:
            state.turn_outcomes = tuple(collected)
            state.retained_deliveries = tuple((outcome.call, outcome) for outcome in collected)
            state.all_outcomes.extend(collected)
            self._on_finished(execution_exit)
        return state.retained_deliveries


def drain_tool_deliveries(
    *, executor: NativeLoopExecutor, conversation: NativeLoopConversation,
    observer: NativeLoopObservationSink, turn_index: int, execution_exit: BaseException | None,
    calls: tuple[NativeExecutionRequest, ...],
) -> tuple[NativeLoopToolDelivery, ...]:
    retained_exit = execution_exit

    def attempt(operation: Callable[[], object]) -> None:
        nonlocal retained_exit
        try:
            operation()
        except BaseException as error:
            # Finish independent handoffs without demoting control/fatal exits.
            retained_exit = retain_delivery_exit(retained_exit, error)

    deliveries: tuple[NativeLoopToolDelivery, ...] = ()
    try:
        deliveries = executor.drain_retained_deliveries()
    except BaseException as error:
        retained_exit = retain_delivery_exit(retained_exit, error)
    attempt(lambda: conversation.flush_tool_context(calls=calls))
    for call, result in deliveries:
        attempt(lambda: observer.tool_called(turn_index=turn_index, call=call))
        attempt(lambda: conversation.append_result(result, call=call, turn_index=turn_index))
        attempt(lambda: observer.tool_result(turn_index=turn_index, call=call, result=result))
    if retained_exit is not None and retained_exit is not execution_exit:
        raise retained_exit
    return deliveries


__all__ = ["drain_tool_deliveries"]
