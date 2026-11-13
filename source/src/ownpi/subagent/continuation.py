"""Continuation controller for the subagent native-loop boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .adapter import SubagentLoopAdapter
from .contracts import SubagentResult
from ownpi.runtime.interactions.pause import HumanClarificationPausedError
from ownpi.controller.runtime_control_signal import RuntimeControlRequested


@dataclass(frozen=True)
class ContinuationResult:
    result: SubagentResult | None
    status: str
    continuation_count: int
    error: str | None = None


@dataclass
class SubagentContinuationLoop:
    adapter: SubagentLoopAdapter
    nudge: Callable[[Any, int], Any] | None = None

    def run(self, context: Any) -> ContinuationResult:
        try:
            return self._run_segments(context)
        finally:
            # The continuation owner also covers a failing nudge or cancellation
            # between segments, when the engine adapter is no longer on stack.
            live = getattr(context, 'live', None)
            session = getattr(live, 'session', None)
            if session is not None:
                session.continuation.close()

    def _run_segments(self, context: Any) -> ContinuationResult:
        count = 0
        while True:
            try:
                result = self.adapter.run(context)
            except (HumanClarificationPausedError, RuntimeControlRequested):
                raise
            except Exception as exc:  # noqa: BLE001 - project child failure
                return ContinuationResult(
                    result=None,
                    status="failed",
                    continuation_count=count,
                    error=f"{type(exc).__name__}: {exc}",
                )
            if result.status == "completed":
                return ContinuationResult(result, "completed", count)
            if result.status != "incomplete":
                return ContinuationResult(result, result.status, count)
            count += 1
            if self.nudge is not None:
                updated_context = self.nudge(context, count)
                if updated_context is not None:
                    context = updated_context
