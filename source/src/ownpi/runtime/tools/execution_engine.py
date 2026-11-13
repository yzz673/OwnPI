from __future__ import annotations

import asyncio
from concurrent.futures import CancelledError
import logging
from typing import Any, Awaitable, Callable, Generic, Sequence, TypeVar

from ownpi.controller.runtime_control_signal import RuntimeControlRequested
from ownpi.runtime.interactions.pause import HumanClarificationPausedError
from ownpi.llm.provider_retry import RetryCancelled

ToolOutcomeT = TypeVar('ToolOutcomeT')
ToolInvoker = Callable[[Any], Awaitable[ToolOutcomeT]]
ToolErrorHandler = Callable[[Any, Exception], ToolOutcomeT | None]
ToolOutcomeHandler = Callable[[Any, ToolOutcomeT], None]
ToolStopHandler = Callable[[Any], ToolOutcomeT | None]


class ToolExecutionEngine(Generic[ToolOutcomeT]):
    """Own invocation tasks until completion or cancellation cleanup finishes.

    The synchronous workflow entry drives this same async core. Each invocation
    owns its admission gate and resource cleanup; cancelling a task never detaches
    work or releases its claim ahead of that cleanup. Outcomes retain input order.
    """

    def __init__(
        self, *, invoke_tool: ToolInvoker[ToolOutcomeT],
        on_error: ToolErrorHandler[ToolOutcomeT] | None = None,
        on_cancel: ToolStopHandler[ToolOutcomeT] | None = None,
        on_outcome: ToolOutcomeHandler[ToolOutcomeT] | None = None,
        cancel_checker: Callable[[], bool] | None = None,
        terminal_checker: Callable[[Any], bool] | None = None,
    ) -> None:
        self._invoke_tool = invoke_tool
        self._on_error = on_error
        self._on_cancel = on_cancel
        self._on_outcome = on_outcome
        self._cancel_checker = cancel_checker
        self._terminal_checker = terminal_checker

    def execute(self, *, requests: Sequence[Any]) -> list[ToolOutcomeT]:
        return asyncio.run(self.execute_async(requests=requests))

    async def execute_async(self, *, requests: Sequence[Any]) -> list[ToolOutcomeT]:
        if not requests:
            return []
        outcomes: dict[int, ToolOutcomeT] = {}
        cancelled_reports: set[int] = set()
        fatal: BaseException | None = None
        stopping = False

        def retain_exit(error: BaseException) -> None:
            nonlocal fatal
            controls = (RuntimeControlRequested, HumanClarificationPausedError, asyncio.CancelledError, CancelledError, RetryCancelled)
            if fatal is None:
                fatal = error
            elif fatal is not error:
                if isinstance(fatal, controls) and not isinstance(error, controls):
                    error.add_note(f'Earlier tool control: {type(fatal).__name__}: {fatal}')
                    fatal = error
                else:
                    fatal.add_note(f'Additional tool exit: {type(error).__name__}: {error}')

        def diagnose(request: Any, index: int, error: Exception, boundary: str) -> None:
            logging.getLogger(__name__).error(
                'Tool %s failed: tool=%s request_index=%s', boundary,
                request.tool_name, index,
                exc_info=(type(error), error, error.__traceback__),
            )

        def publish(request: Any, index: int, outcome: ToolOutcomeT) -> None:
            outcomes[index] = outcome
            if self._on_outcome is not None:
                try:
                    self._on_outcome(request, outcome)
                except (RuntimeControlRequested, HumanClarificationPausedError, CancelledError):
                    raise
                except Exception as error:
                    diagnose(request, index, error, 'outcome callback')

        def report_error(request: Any, index: int, error: Exception) -> None:
            if self._on_error is None:
                diagnose(request, index, error, 'invocation')
                return
            try:
                outcome = self._on_error(request, error)
            except (RuntimeControlRequested, HumanClarificationPausedError, CancelledError):
                raise
            except Exception as callback_error:
                diagnose(request, index, error, 'invocation')
                diagnose(request, index, callback_error, 'error callback')
                return
            if outcome is not None:
                publish(request, index, outcome)

        def report_cancel(request: Any, index: int) -> None:
            if index in cancelled_reports or index in outcomes:
                return
            cancelled_reports.add(index)
            if self._on_cancel is None:
                return
            try:
                outcome = self._on_cancel(request)
            except (RuntimeControlRequested, HumanClarificationPausedError, CancelledError):
                raise
            except Exception as error:
                diagnose(request, index, error, 'stop callback')
                return
            if outcome is not None:
                publish(request, index, outcome)

        async def invoke(request: Any, index: int) -> None:
            nonlocal fatal, stopping
            try:
                try:
                    if stopping or (self._cancel_checker is not None and self._cancel_checker()):
                        report_cancel(request, index)
                        return
                    outcome = await self._invoke_tool(request)
                except (RuntimeControlRequested, HumanClarificationPausedError, CancelledError):
                    raise
                except asyncio.CancelledError:
                    task = asyncio.current_task()
                    if task is None or not task.cancelling():
                        raise
                    report_cancel(request, index)
                except Exception as error:
                    report_error(request, index, error)
                else:
                    publish(request, index, outcome)
            except BaseException as error:
                # Collect fatal control exceptions here so every sibling can
                # unwind before they reach the synchronous workflow boundary.
                stopping = True
                retain_exit(error)

        tasks = [asyncio.create_task(invoke(request, index))
                 for index, request in enumerate(requests, start=1)]
        remaining = set(tasks)
        requests_by_task = dict(zip(tasks, requests))

        def cancel_unfinished() -> None:
            for task in remaining:
                if task.done() or task.cancelling():
                    continue
                request = requests_by_task[task]
                try:
                    if self._terminal_checker is not None and self._terminal_checker(request):
                        continue
                except BaseException as error:
                    retain_exit(error)
                task.cancel()

        cancellation_sent = False
        try:
            while remaining:
                if fatal is not None or (self._cancel_checker is not None and self._cancel_checker()):
                    stopping = True
                    if not cancellation_sent:
                        cancel_unfinished()
                        cancellation_sent = True
                _, remaining = await asyncio.wait(
                    remaining, timeout=0.05, return_when=asyncio.FIRST_COMPLETED,
                )
        except BaseException as error:
            retain_exit(error)
            stopping = True
            cancel_unfinished()
        finally:
            # Await actual unwind, including owned-process cleanup. Shield only
            # collection, never the invocation's external wait or side effects.
            drain = asyncio.gather(*tasks, return_exceptions=True)
            while not drain.done():
                try:
                    await asyncio.shield(drain)
                except asyncio.CancelledError as error:
                    retain_exit(error)
            for index, task in enumerate(tasks, start=1):
                if task.cancelled():
                    try:
                        report_cancel(requests[index - 1], index)
                    except BaseException as error:
                        retain_exit(error)
        if fatal is not None:
            raise fatal
        return [outcomes[index] for index in sorted(outcomes)]
