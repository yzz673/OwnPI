from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from concurrent.futures import CancelledError
from typing import Callable, Sequence

from ownpi.controller.runtime_control_signal import RuntimeControlRequested
from ownpi.runtime.interactions.pause import HumanClarificationPausedError
from ownpi.llm.provider_retry import RetryCancelled
from ownpi.runtime.native_tools.spec import NativeToolCall, NativeToolError
from ownpi.runtime.native_tools.tool_invocation import NativeToolInvocation, parse_tool_invocation
from ownpi.runtime.tools.execution_engine import ToolExecutionEngine
from ownpi.runtime.tools.runtime import ToolInvocationOutcome, ToolRuntime, ToolRuntimeContext
from ownpi.schemas.runtime_tooling import ToolCall


NativeExecutionRequest = NativeToolInvocation | NativeToolCall
@dataclass(frozen=True)
class _ExecutionOccurrence:
    index: int
    call: NativeExecutionRequest

    @property
    def tool_name(self) -> str:
        return self.call.tool_name


@dataclass(frozen=True)
class NativeToolExecutionOutcome:
    call: NativeExecutionRequest
    invocation: ToolInvocationOutcome | None = None
    native_error: NativeToolError | None = None
    diagnostic: str = ''

    @property
    def ok(self) -> bool:
        if self.native_error is not None or self.invocation is None:
            return False
        return (
            self.invocation.record.status == 'completed'
            and self.invocation.tool_result is not None
            and self.invocation.tool_result.ok
        )


class NativeToolExecutor:
    """Execute calls independently; model-result projection is a later consumer."""

    def __init__(self, tool_runtime: ToolRuntime, *, native_tool_strict: bool = False) -> None:
        self._tool_runtime = tool_runtime
        self.native_tool_strict = native_tool_strict

    def execute(
        self, *, context: ToolRuntimeContext,
        calls: Sequence[NativeExecutionRequest],
        on_started: Callable[[NativeExecutionRequest], None] | None = None,
        on_outcome: Callable[[NativeToolExecutionOutcome], None] | None = None,
    ) -> list[NativeToolExecutionOutcome]:
        terminal_calls: set[int] = set()
        retained: dict[int, NativeToolExecutionOutcome] = {}
        occurrences = tuple(_ExecutionOccurrence(index, call) for index, call in enumerate(calls))

        def mark_terminal(item: _ExecutionOccurrence) -> None:
            terminal_calls.add(item.index)

        def retain(item: _ExecutionOccurrence, outcome: NativeToolExecutionOutcome) -> None:
            retained[item.index] = outcome

        engine = ToolExecutionEngine[NativeToolExecutionOutcome](
            invoke_tool=lambda item: self._execute_one(
                context, item.call, on_started, lambda: mark_terminal(item),
                lambda raw: retain(item, NativeToolExecutionOutcome(
                    call=item.call, invocation=raw, diagnostic=raw.diagnostic,
                )),
            ),
            on_error=lambda item, error: self._diagnostic(item.call, error),
            on_cancel=lambda item: self._not_started(item.call, 'tool_execution_cancelled'),
            on_outcome=retain,
            cancel_checker=context.cancel_checker,
            terminal_checker=lambda item: item.index in terminal_calls,
        )
        execution_exit: BaseException | None = None
        try:
            return engine.execute(
                requests=occurrences,
            )
        except BaseException as error:
            execution_exit = error
            raise
        finally:
            # Raw callbacks may update a call more than once before it unwinds.
            # Hand off each occurrence once, after every invocation is collected.
            if on_outcome is not None:
                _handoff_outcomes(
                    [retained[index] for index in sorted(retained)], on_outcome, execution_exit,
                )

    async def _execute_one(
        self, context: ToolRuntimeContext, requested: NativeExecutionRequest,
        on_started: Callable[[NativeExecutionRequest], None] | None = None,
        on_terminal: Callable[[], None] | None = None,
        on_raw_outcome: Callable[[ToolInvocationOutcome], None] | None = None,
    ) -> NativeToolExecutionOutcome:
        call = self.parse_call(context, requested)
        if isinstance(call, NativeToolError):
            return NativeToolExecutionOutcome(call=requested, native_error=call)
        outcome: ToolInvocationOutcome | None = None
        execution_started = False
        terminal = False

        def mark_terminal() -> None:
            nonlocal terminal
            terminal = True
            if on_terminal is not None:
                on_terminal()
            if context.on_tool_terminal is not None:
                context.on_tool_terminal()

        def retain_raw(produced: ToolInvocationOutcome) -> None:
            nonlocal outcome
            outcome = produced
            if on_raw_outcome is not None:
                on_raw_outcome(produced)
            if context.on_raw_tool_outcome is not None:
                context.on_raw_tool_outcome(produced)

        def before_handler(request: ToolCall) -> None:
            nonlocal execution_started
            if on_started is not None:
                on_started(requested)
            if context.before_tool_handler is not None:
                context.before_tool_handler(request)
            execution_started = True

        try:
            outcome = await self._tool_runtime.run_tool_use(
                context=replace(
                    context, on_raw_tool_outcome=retain_raw,
                    before_tool_handler=before_handler,
                    on_tool_terminal=mark_terminal,
                    output_sequence_index=(
                        requested.sequence_index if isinstance(requested, NativeToolInvocation) else None
                    ),
                ),
                request=_to_tool_call(context=context, call=call),
                execution_protocol='native_tool',
            )
        except (RuntimeControlRequested, HumanClarificationPausedError):
            raise
        except (Exception, asyncio.CancelledError) as error:
            if isinstance(error, (CancelledError, asyncio.CancelledError)) and not terminal:
                return replace(
                    self._cancelled(requested, execution_started=execution_started),
                    invocation=outcome,
                    diagnostic=outcome.diagnostic if outcome is not None else '',
                )
            if outcome is None:
                raise
            diagnostic = (
                f'{type(error).__name__}: {error}' if isinstance(error, (CancelledError, asyncio.CancelledError))
                else self._diagnostic(requested, error).diagnostic
            )
            return NativeToolExecutionOutcome(
                call=requested, invocation=outcome, diagnostic=diagnostic,
            )
        return NativeToolExecutionOutcome(
            call=requested, invocation=outcome, diagnostic=outcome.diagnostic,
        )

    def parse_call(
        self, context: ToolRuntimeContext, requested: NativeExecutionRequest,
    ) -> NativeToolCall | NativeToolError:
        if not isinstance(requested, NativeToolInvocation):
            return requested
        definition = self._tool_runtime._resolve_eligible_definition(context, requested.tool_name)
        if definition is None:
            return NativeToolError(
                tool_name=requested.tool_name, request_id=requested.request_id,
                provider_call_id=requested.provider_call_id,
                failure_class='unknown_tool', error_message=f'unknown tool: {requested.tool_name}',
            )
        return parse_tool_invocation(
            requested, parse_arguments=definition.parse_arguments,
            input_schema=definition.tool_abi().input_schema,
            native_tool_strict=self.native_tool_strict,
        )

    @staticmethod
    def _diagnostic(call: NativeExecutionRequest, error: Exception) -> NativeToolExecutionOutcome:
        logging.getLogger(__name__).error(
            'Tool dispatch failed: tool=%s call_id=%s', call.tool_name, call.provider_call_id,
            exc_info=(type(error), error, error.__traceback__),
        )
        return NativeToolExecutionOutcome(call=call, diagnostic=f'{type(error).__name__}: {error}')

    @staticmethod
    def _cancelled(
        call: NativeExecutionRequest, *, execution_started: bool,
    ) -> NativeToolExecutionOutcome:
        if not execution_started:
            return NativeToolExecutor._not_started(call, 'tool_execution_cancelled')
        return NativeToolExecutionOutcome(
            call=call,
            native_error=NativeToolError(
                tool_name=call.tool_name, request_id=call.request_id,
                provider_call_id=call.provider_call_id,
                failure_class='tool_execution_cancelled',
                error_message='Tool invocation was cancelled after entering execution; side effects may have occurred.',
                metadata={'execution_started': True, 'boundary': 'tool_execution_cancelled'},
            ),
        )

    @staticmethod
    def _not_started(call: NativeExecutionRequest, reason: str) -> NativeToolExecutionOutcome:
        return NativeToolExecutionOutcome(
            call=call,
            native_error=NativeToolError(
                tool_name=call.tool_name, request_id=call.request_id,
                provider_call_id=call.provider_call_id, failure_class=reason,
                error_message='Tool invocation was not started.',
                metadata={'execution_started': False, 'boundary': reason},
            ),
        )


def _handoff_outcomes(
    outcomes: Sequence[NativeToolExecutionOutcome],
    on_outcome: Callable[[NativeToolExecutionOutcome], None],
    execution_exit: BaseException | None,
) -> None:
    retained_exit = execution_exit

    def priority(error: BaseException) -> int:
        if isinstance(error, (RuntimeControlRequested, HumanClarificationPausedError, CancelledError, asyncio.CancelledError, RetryCancelled)):
            return 1
        return 0 if isinstance(error, Exception) else 2

    for outcome in outcomes:
        try:
            on_outcome(outcome)
        except BaseException as error:
            # Finish independent handoffs, but never report a failed handoff as success.
            if retained_exit is None:
                retained_exit = error
            elif error is not retained_exit:
                if priority(error) > priority(retained_exit):
                    error.add_note(f'Earlier raw handoff exit: {type(retained_exit).__name__}: {retained_exit}')
                    retained_exit = error
                else:
                    retained_exit.add_note(f'Additional raw handoff exit: {type(error).__name__}: {error}')
    if retained_exit is not None and retained_exit is not execution_exit:
        raise retained_exit


def _to_tool_call(*, context: ToolRuntimeContext, call: NativeToolCall) -> ToolCall:
    return ToolCall(
        request_id=call.request_id or call.provider_call_id,
        tool_name=call.tool_name,
        input=dict(call.input),
        reason='provider-native tool call',
        expected_output='provider-native tool result',
        stage=context.stage.value,
        attempt=context.attempt,
        query_chain_id=context.query_chain_id,
        query_chain_depth=context.query_chain_depth,
        continuation_index=context.continuation_index,
        requested_by='native_tool_loop',
    )
