from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ownpi.llm.base import LLMClient
from ownpi.runtime.native_tools.loop_outcome import is_loop_cancellation, kernel_outcome
from ownpi.runtime.native_tools.loop_engine.provider import ProviderTurnRunScope
from ownpi.runtime.native_tools.loop_engine.session import NativeLoopSession, build_native_session_engine
from ownpi.runtime.native_tools.loop_engine.session_conversation import NativeSessionConversation
from ownpi.runtime.native_tools.loop_engine.tool_surface import FixedNativeToolSurfaceProvider
from ownpi.runtime.native_tools.history_items import HistoryEntry, HistoryMessage
from ownpi.runtime.rollout.session import RecordingTurnRunner
from ownpi.runtime.native_tools.tool_invocation import build_tool_invocations
from ownpi.runtime.native_tools.spec import NativeToolSpec
from ownpi.runtime.native_tools.loop_engine import (
    NativeLoopContinuation,
    NativeLoopObserver,
)
from ownpi.runtime.native_tools.loop_engine.contracts import (
    NativeLoopDirective,
    NativeLoopExecutor,
    NativeLoopPolicy,
    NativeLoopSnapshot,
    NativeLoopTurnPlan,
    NativeLoopTurnRunner,
)
from ownpi.runtime.native_tools.plan_mode.contracts import (
    PlanModeLoopRequest,
    PlanModeSegmentResult,
)
from ownpi.runtime.native_tools.plan_mode.parser import (
    ProposedPlanParseResult,
    ProposedPlanParser,
)


@dataclass
class PlanModePolicy(NativeLoopPolicy):
    parser: ProposedPlanParser
    execution_boundary: Callable[[], dict[str, Any] | None] | None = None
    latest_parse: ProposedPlanParseResult | None = None
    pending_boundary: dict[str, Any] | None = None

    def tool_choice(self, *, turn_index: int) -> str:
        del turn_index
        return "auto"

    def plan_turn(self, snapshot: NativeLoopSnapshot) -> NativeLoopTurnPlan:
        self.latest_parse = self.parser.parse(snapshot.response.assistant_text)
        calls = build_tool_invocations(snapshot.response)
        if calls:
            return NativeLoopTurnPlan(calls=calls)
        if (
            self.latest_parse.found
            and self.latest_parse.plan_text
            and not self.latest_parse.has_unclosed_block
            and not self.latest_parse.has_invalid_nested_block
        ):
            return NativeLoopTurnPlan(
                directive=NativeLoopDirective.complete(reason="plan_completed")
            )
        return NativeLoopTurnPlan()

    def after_results(
        self,
        snapshot: NativeLoopSnapshot,
        results: tuple[Any, ...],
    ) -> NativeLoopDirective:
        boundary = self.execution_boundary() if self.execution_boundary is not None else None
        if boundary is not None:
            self.pending_boundary = {**boundary, 'turn_index': snapshot.turn_index}
            return NativeLoopDirective.complete(reason=boundary['kind'])
        return NativeLoopDirective.continue_with(reason="plan_tool_results_received")

    def on_provider_error(
        self,
        error: BaseException,
        *,
        turn_index: int,
        tool_call_count: int,
    ) -> NativeLoopDirective:
        del turn_index, tool_call_count
        if is_loop_cancellation(error):
            return NativeLoopDirective.complete(reason='user_cancelled')
        return NativeLoopDirective.fail(
            failure_class="plan_mode_provider_error",
            message=str(error),
        )


class PlanModeLoopAdapter:
    """Runs one resumable Plan Mode segment on the shared native loop engine."""

    def __init__(
        self,
        *,
        request: PlanModeLoopRequest,
        session: NativeLoopSession,
        turn_runner: NativeLoopTurnRunner,
        executor: NativeLoopExecutor,
        batch_admission_specs: tuple[NativeToolSpec, ...],
        observer: NativeLoopObserver | None = None,
        llm_client: LLMClient | None = None,
        execution_boundary: Callable[[], dict[str, Any] | None] | None = None,
        deliver_answers: Callable[[], None] = lambda: None,
    ) -> None:
        self.request = request
        self.session = session
        self.deliver_answers = deliver_answers
        self.turn_runner = turn_runner
        self.executor = executor
        self.llm_client = llm_client
        self.observer = observer or NativeLoopObserver(event_prefix="plan_mode")
        self.parser = ProposedPlanParser(
            start_tag=request.plan_start_tag,
            end_tag=request.plan_end_tag,
        )
        self.policy = PlanModePolicy(
            parser=self.parser,
            execution_boundary=execution_boundary,
        )
        self.engine = build_native_session_engine(
            session=session, tool_surface=FixedNativeToolSurfaceProvider(request.tool_specs),
            batch_admission_specs=batch_admission_specs,
            turn_runner=RecordingTurnRunner(self.turn_runner, session.history),
            executor=self.executor,
            policy=self.policy,
            observer=self.observer,
            run_scope=ProviderTurnRunScope(
                cancel_checker_setter=self.llm_client.set_cancel_checker
                if self.llm_client is not None else None,
            ),
        )
        self.continuation = NativeLoopContinuation(session)
        self.conversation = NativeSessionConversation(session)

    def run_segment(self) -> PlanModeSegmentResult:
        return self.continuation.run_segment(self._run_segment, lambda result: {
            'status': ('interrupted' if result.status in {'failed', 'human_input_pending', 'user_cancelled'}
                       else 'completed'),
            'reason': result.failure_class or result.status,
        })

    def _run_segment(self) -> PlanModeSegmentResult:
        self.policy.latest_parse = None
        self.policy.pending_boundary = None
        boundary = self.policy.execution_boundary() if self.policy.execution_boundary is not None else None
        if boundary is not None:
            return PlanModeSegmentResult(status=boundary['kind'], pending_boundary=boundary)
        invocation = self.request.invocation
        if invocation is not None and invocation.services.cancel_checker is not None and invocation.services.cancel_checker():
            return PlanModeSegmentResult(status='user_cancelled')
        native_result = self.engine.run(self.request.invocation)
        parsed = self.policy.latest_parse or ProposedPlanParseResult()
        boundary = kernel_outcome(native_result)
        if boundary.kind == 'cancelled':
            return PlanModeSegmentResult(
                status='user_cancelled',
                pending_boundary=self.policy.pending_boundary,
                native_result=native_result,
            )
        if boundary.kind == 'failed':
            return PlanModeSegmentResult(
                status="failed",
                native_result=native_result,
                failure_class=native_result.failure_class,
                failure_message=native_result.failure_message,
            )
        if boundary.kind == 'waiting':
            return PlanModeSegmentResult(
                status="human_input_pending",
                pending_boundary=self.policy.pending_boundary,
                native_result=native_result,
            )
        return PlanModeSegmentResult(
            status="plan_completed" if parsed.found else "completed_without_plan",
            plan_text=parsed.plan_text,
            visible_text=parsed.visible_text,
            native_result=native_result,
            metadata={
                "block_count": parsed.block_count,
                "has_unclosed_block": parsed.has_unclosed_block,
                "has_invalid_nested_block": parsed.has_invalid_nested_block,
            },
        )

    def append_human_answer(self, answer: str) -> None:
        self.continuation.append_input((HistoryEntry(HistoryMessage('user', answer)),))

    def latest_proposed_plan(self) -> ProposedPlanParseResult:
        from ownpi.runtime.rollout.source_reader import original_items
        from ownpi.runtime.rollout.codec import decode_entry
        from ownpi.runtime.native_tools.provider import ProviderOutputItem, ProviderOutputItemKind

        self.session.history.flush()
        _, records = original_items(self.session.history.recorder.path)
        for record in reversed(records):
            item = decode_entry(record).item
            if not isinstance(item, ProviderOutputItem) or item.kind != ProviderOutputItemKind.MESSAGE:
                continue
            parsed = self.parser.parse(item.text)
            if parsed.found and parsed.plan_text:
                return parsed
        return ProposedPlanParseResult()


__all__ = ["PlanModeLoopAdapter", "PlanModePolicy"]
