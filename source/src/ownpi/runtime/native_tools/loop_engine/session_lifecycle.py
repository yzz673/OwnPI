from __future__ import annotations

from ownpi.runtime.native_tools.loop_engine.session import NativeLoopSession
from ownpi.runtime.native_tools.compaction.budget import resolve_native_compaction_budget
from ownpi.runtime.native_tools.loop_engine.contracts import (
    NativeExecutionRequest,
    NativeLoopInvocation,
    NativeLoopResult,
    NativeLoopSnapshot,
    NativeLoopTurnPlan,
    NativeLoopToolValue,
    NativeLoopLifecycleBindings,
)
from ownpi.runtime.native_tools.provider import ProviderOutputEnvelope


class NativeSessionLifecycle:
    def __init__(
        self,
        session: NativeLoopSession,
        bindings: NativeLoopLifecycleBindings | None = None,
    ) -> None:
        self._session = session
        self._bindings = bindings

    def bind_invocation(self, invocation: NativeLoopInvocation) -> None:
        del invocation

    def before_turn(self, *, turn_index: int) -> None:
        del turn_index
        if self._bindings is not None:
            self._bindings.before_turn()

    def after_recovery(self) -> None:
        try:
            self._persist_if_changed()
        finally:
            self.flush_pending()

    def before_response(
        self,
        *,
        turn_index: int,
        response: ProviderOutputEnvelope,
    ) -> None:
        del response, turn_index

    def after_provider(
        self,
        *,
        turn_index: int,
        response: ProviderOutputEnvelope,
    ) -> None:
        if self._bindings is not None:
            self._bindings.after_provider()

    def on_provider_failure(
        self,
        *,
        turn_index: int,
        reason_code: str,
        failure_class: str,
    ) -> None:
        del turn_index, failure_class
        session = self._session
        if (reason_code == 'provider_context_overflow' and session.current_request is not None
                and session.model_metadata is not None):
            limit = resolve_native_compaction_budget(
                metadata=session.model_metadata, requested_output_tokens=None,
            ).effective_input_limit_tokens
            session.history_usage.mark_full(
                session.history.snapshot, session.history.generation,
                session.current_request.measurement, limit,
            )
        try:
            self._abort_current_context()
        finally:
            self.flush_pending()

    def after_plan(
        self,
        *,
        snapshot: NativeLoopSnapshot,
        plan: NativeLoopTurnPlan,
    ) -> None:
        del snapshot, plan
        self._persist_if_changed()

    def before_tools(
        self,
        *,
        turn_index: int,
        calls: tuple[NativeExecutionRequest, ...],
    ) -> None:
        del calls, turn_index
        if self._bindings is not None:
            self._bindings.before_tools()

    def after_results(
        self,
        *,
        turn_index: int,
        calls: tuple[NativeExecutionRequest, ...],
        results: tuple[NativeLoopToolValue, ...],
    ) -> None:
        del turn_index, calls, results
        self._persist_if_changed()
        if self._bindings is not None:
            self._bindings.after_results()

    def on_terminal(self, result: NativeLoopResult) -> None:
        try:
            self._abort_current_context()
            self._persist_if_changed()
        finally:
            self.flush_pending()

    def flush_pending(self) -> bool:
        """Flush canonical history without confirming unrelated business state."""
        return self._session.history.flush()

    def _abort_current_context(self) -> None:
        request = self._session.current_request
        if request is None:
            return
        self._session.context_runtime_manager.abort_provider_context(
            request.context_preparation_id,
        )
        self._session.current_request = None

    def _persist_if_changed(self) -> None:
        self._session.history.flush()


__all__ = ["NativeSessionLifecycle"]
