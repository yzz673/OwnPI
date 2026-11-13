from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable

from ownpi.llm.errors import ProviderContextOverflowError
from ownpi.diagnostics import record_exception
from concurrent.futures import CancelledError
from ownpi.runtime.native_tools.compaction.local import HistoryCompactionError
from ownpi.llm.provider_retry import RetryCancelled
from ownpi.runtime.native_tools.runtime_control import is_runtime_control_requested
from ownpi.runtime.native_tools.loop_engine.contracts import (
    NativeLoopConversation,
    NativeLoopDirective,
    NativeLoopExecutor,
    NativeLoopLifecycle,
    NativeLoopObservationSink,
    NativeLoopPolicy,
    NativeLoopResult,
    NativeLoopSnapshot,
    NativeLoopTurnPreparer,
    NativeLoopTurnRunner,
)
from ownpi.runtime.native_tools.loop_engine.batch_admission import (
    NativeToolBatchGrammar,
    admit_native_tool_batch,
)
from ownpi.runtime.native_tools.loop_engine.provider import merge_usage, provider_request_id
from ownpi.runtime.native_tools.loop_engine.execution import drain_tool_deliveries
from ownpi.runtime.native_tools.provider import ProviderOutputEnvelope
from ownpi.runtime.native_tools.spec import NativeToolError, NativeToolSpec


class _NoopLifecycle:
    def bind_invocation(self, invocation: object) -> None:
        del invocation

    def before_turn(self, *, turn_index: int) -> None:
        del turn_index

    def before_response(self, *, turn_index: int, response: object) -> None:
        del turn_index, response

    def after_provider(self, *, turn_index: int, response: object) -> None:
        del turn_index, response

    def on_provider_failure(
        self,
        *,
        turn_index: int,
        reason_code: str,
        failure_class: str,
    ) -> None:
        del turn_index, reason_code, failure_class

    def after_plan(self, *, snapshot: object, plan: object) -> None:
        del snapshot, plan

    def before_tools(self, *, turn_index: int, calls: tuple[object, ...]) -> None:
        del turn_index, calls

    def after_results(
        self,
        *,
        turn_index: int,
        calls: tuple[object, ...],
        results: tuple[object, ...],
    ) -> None:
        del turn_index, calls, results

    def on_terminal(self, result: object) -> None:
        del result


@dataclass(frozen=True)
class _ConversationTurnPreparer:
    conversation: NativeLoopConversation

    def prepare(self, *, turn_index: int) -> object:
        del turn_index
        return self.conversation.provider_input


@dataclass
class NativeToolLoopKernel:
    turn_runner: NativeLoopTurnRunner
    conversation: NativeLoopConversation
    executor: NativeLoopExecutor
    policy: NativeLoopPolicy
    observer: NativeLoopObservationSink
    request_id_resolver: Callable[[ProviderOutputEnvelope], str] = provider_request_id
    lifecycle: NativeLoopLifecycle | None = None
    turn_preparer: NativeLoopTurnPreparer | None = None
    batch_admission_specs: tuple[NativeToolSpec, ...] = ()
    batch_grammar: NativeToolBatchGrammar | None = None

    def run(self) -> NativeLoopResult:
        turns = 0
        tool_call_count = 0
        usage: dict[str, int] = {}
        request_ids: list[str] = []
        self.observer.loop_started()
        lifecycle = self.lifecycle or _NoopLifecycle()
        turn_preparer = self.turn_preparer or _ConversationTurnPreparer(self.conversation)

        while True:
            turns += 1
            lifecycle.before_turn(turn_index=turns)
            try:
                prepared_turn = turn_preparer.prepare(turn_index=turns)
            except (RetryCancelled, HistoryCompactionError, ProviderContextOverflowError) as error:
                return self._provider_failure(
                    error, reason_code="provider_context_preparation_failed",
                    turns=turns, tool_call_count=tool_call_count, usage=usage,
                    request_ids=request_ids,
                )
            except BaseException as error:
                lifecycle.on_provider_failure(
                    turn_index=turns, reason_code="provider_preparation_interrupted",
                    failure_class=type(error).__name__,
                )
                raise
            self.observer.provider_started(turn_index=turns)
            try:
                response = self.turn_runner.invoke(
                    prepared_turn=prepared_turn,
                    tool_choice=self.policy.tool_choice(turn_index=turns),
                )
            except CancelledError:
                lifecycle.on_provider_failure(
                    turn_index=turns, reason_code="provider_interrupted",
                    failure_class="cancelled",
                )
                raise
            except RetryCancelled as error:
                return self._provider_failure(
                    error, reason_code="provider_cancelled", turns=turns,
                    tool_call_count=tool_call_count, usage=usage, request_ids=request_ids,
                )
            except ProviderContextOverflowError as error:
                return self._provider_failure(
                    error, reason_code="provider_context_overflow", turns=turns,
                    tool_call_count=tool_call_count, usage=usage, request_ids=request_ids,
                )
            except Exception as error:
                if is_runtime_control_requested(error):
                    lifecycle.on_provider_failure(
                        turn_index=turns, reason_code="provider_interrupted",
                        failure_class="cancelled",
                    )
                    raise
                return self._provider_failure(
                    error, reason_code="provider_failure", turns=turns,
                    tool_call_count=tool_call_count, usage=usage, request_ids=request_ids,
                )
            except BaseException as error:
                lifecycle.on_provider_failure(
                    turn_index=turns, reason_code="provider_interrupted",
                    failure_class=type(error).__name__,
                )
                raise

            merge_usage(usage, response.usage)
            request_id = self.request_id_resolver(response)
            if request_id:
                request_ids.append(request_id)
            try:
                lifecycle.before_response(turn_index=turns, response=response)
                self.conversation.append_response(response, turn_index=turns)
                lifecycle.after_provider(turn_index=turns, response=response)
            except BaseException as error:
                lifecycle.on_provider_failure(
                    turn_index=turns,
                    reason_code="provider_response_processing_failure",
                    failure_class=type(error).__name__,
                )
                raise
            self.observer.provider_completed(
                turn_index=turns,
                response=response,
                provider_request_id=request_id,
            )
            snapshot = NativeLoopSnapshot(
                turn_index=turns,
                response=response,
                tool_call_count=tool_call_count,
                usage=dict(usage),
                provider_request_ids=tuple(request_ids),
            )
            plan = self.policy.plan_turn(snapshot)
            lifecycle.after_plan(snapshot=snapshot, plan=plan)
            if plan.directive is not None:
                terminal = self._apply_directive(
                    plan.directive,
                    turns=turns,
                    tool_call_count=tool_call_count,
                    usage=usage,
                    request_ids=request_ids,
                )
                if terminal is not None:
                    return terminal
                continue

            admission = admit_native_tool_batch(
                calls=plan.calls,
                tool_specs=self.batch_admission_specs,
                grammar=self.batch_grammar,
            )
            if not admission.accepted:
                for call in plan.calls:
                    self.conversation.append_error(
                        NativeToolError(
                            tool_name=call.tool_name,
                            request_id=call.request_id,
                            provider_call_id=call.provider_call_id,
                            failure_class=admission.violation_code,
                            error_message=admission.correction_text,
                            recovery_hint=admission.correction_text,
                            metadata={
                                'batch_executed': False,
                                'tool_names': list(admission.tool_names),
                            },
                        ),
                        turn_index=turns,
                        output_sequence_index=call.sequence_index,
                        call=call,
                    )
                terminal = self._apply_directive(
                    NativeLoopDirective.continue_with(
                        reason=admission.violation_code,
                        correction_text=admission.correction_text,
                        correction_index=turns,
                    ),
                    turns=turns,
                    tool_call_count=tool_call_count,
                    usage=usage,
                    request_ids=request_ids,
                )
                if terminal is not None:
                    return terminal
                continue
            lifecycle.before_tools(turn_index=turns, calls=plan.calls)
            execution_exit: BaseException | None = None
            try:
                self.executor.execute_many(plan.calls)
            except BaseException as error:
                execution_exit = error
                raise
            finally:
                deliveries = drain_tool_deliveries(
                    executor=self.executor, conversation=self.conversation, observer=self.observer,
                    turn_index=turns, execution_exit=execution_exit,
                    calls=plan.calls,
                )
            results = tuple(result for _, result in deliveries)
            tool_call_count += len(deliveries)
            lifecycle.after_results(
                turn_index=turns,
                calls=plan.calls,
                results=results,
            )
            snapshot = NativeLoopSnapshot(
                turn_index=turns,
                response=response,
                tool_call_count=tool_call_count,
                usage=dict(usage),
                provider_request_ids=tuple(request_ids),
            )
            directive = self.policy.after_results(snapshot, results)
            terminal = self._apply_directive(
                directive,
                turns=turns,
                tool_call_count=tool_call_count,
                usage=usage,
                request_ids=request_ids,
            )
            if terminal is not None:
                return terminal

    def _provider_failure(
        self,
        error: BaseException,
        *,
        reason_code: str,
        turns: int,
        tool_call_count: int,
        usage: dict[str, int],
        request_ids: list[str],
    ) -> NativeLoopResult:
        record_exception(error, component='native_tool_loop', reason_code=reason_code,
                         turn_index=turns, tool_call_count=tool_call_count,
                         provider_request_ids=request_ids)
        provider_error_handler = getattr(self.policy, "on_provider_error", None)
        resolved_directive = (
            provider_error_handler(
                error,
                turn_index=turns,
                tool_call_count=tool_call_count,
            )
            if callable(provider_error_handler)
            else NativeLoopDirective.fail(
                failure_class="provider_error",
                message=str(error),
            )
        )
        directive = (
            resolved_directive
            if isinstance(resolved_directive, NativeLoopDirective)
            else NativeLoopDirective.fail(
                failure_class="provider_error",
                message=str(error),
            )
        )
        lifecycle = self.lifecycle or _NoopLifecycle()
        lifecycle.on_provider_failure(
            turn_index=turns,
            reason_code=reason_code,
            failure_class=directive.failure_class,
        )
        return self._finish(
            directive,
            turns=turns,
            tool_call_count=tool_call_count,
            usage=usage,
            request_ids=request_ids,
        )

    def _apply_directive(
        self,
        directive: NativeLoopDirective,
        *,
        turns: int,
        tool_call_count: int,
        usage: dict[str, int],
        request_ids: list[str],
    ) -> NativeLoopResult | None:
        if directive.action == "continue":
            if directive.correction_text:
                self.conversation.append_correction(
                    directive.correction_text,
                    turn_index=turns,
                    correction_index=directive.correction_index,
                )
                self.observer.correction(
                    turn_index=turns,
                    text=directive.correction_text,
                    reason=directive.reason,
                )
            return None
        return self._finish(
            directive,
            turns=turns,
            tool_call_count=tool_call_count,
            usage=usage,
            request_ids=request_ids,
        )

    def _finish(
        self,
        directive: NativeLoopDirective,
        *,
        turns: int,
        tool_call_count: int,
        usage: dict[str, int],
        request_ids: list[str],
    ) -> NativeLoopResult:
        failed = directive.action == "fail"
        result = NativeLoopResult(
            status="failed" if failed else "completed",
            turns=turns,
            tool_call_count=tool_call_count,
            usage=dict(usage),
            provider_request_ids=tuple(request_ids),
            failure_class=directive.failure_class if failed else "",
            failure_message=directive.failure_message if failed else "",
            terminal_reason=directive.reason,
        )
        lifecycle = self.lifecycle or _NoopLifecycle()
        lifecycle.on_terminal(result)
        if failed:
            self.observer.failed(result)
        else:
            self.observer.completed(result)
        return result


__all__ = ["NativeToolLoopKernel"]
