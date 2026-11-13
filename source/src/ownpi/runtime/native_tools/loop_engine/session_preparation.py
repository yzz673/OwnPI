"""Prepare one request from live history, compacting that same owner if needed."""

from __future__ import annotations

from collections.abc import Callable

from ownpi.runtime.native_tools.compaction.budget import resolve_native_compaction_budget
from ownpi.runtime.native_tools.compaction.local import compact_live_history
from ownpi.runtime.native_tools.loop_engine.contracts import NativePreparedRequest
from ownpi.runtime.native_tools.loop_engine.session import NativeLoopSession
from ownpi.runtime.native_tools.loop_engine.contracts import NativeLoopToolSurfaceProvider
from ownpi.runtime.native_tools.provider_input import NativeHistoryProviderInput
from ownpi.runtime.native_tools.provider_json import FrozenProviderJson
from ownpi.runtime.native_tools.provider import ProviderOutputEnvelope
from ownpi.runtime.native_tools.delivery_budget import reduce_tool_deliveries
from ownpi.runtime.native_tools.provider_request_measurement import NativeProviderRequestMeasurement
from ownpi.runtime.native_tools.spec import NativeToolSpec


class NativeSessionPreparation:
    def __init__(
        self,
        session: NativeLoopSession,
        tool_surface: NativeLoopToolSurfaceProvider,
    ) -> None:
        self._session = session
        self._tool_surface = tool_surface

    def prepare(self, *, turn_index: int) -> NativePreparedRequest:
        session = self._session
        history = session.history
        assert history is not None
        surface = self._tool_surface.refresh(turn_index=turn_index)
        session.bindings.prepare_context()

        def build_input() -> NativeHistoryProviderInput:
            return NativeHistoryProviderInput(
                immutable_prefix=tuple(FrozenProviderJson.from_value(message) for message in session.canonical_ledger_messages),
                items=history.for_prompt(), history_generation=history.generation,
            )

        value = build_input()
        history.prepare_context(value, model=session.rollout_model,
                                        tools=[spec.to_dict() for spec in surface.provider_specs],
                                        effective_permissions=session.bindings.effective_permissions())
        measured = session.request_measure(value, surface.provider_specs)
        occupied = session.history_usage.estimate(history.snapshot, history.generation, measured)
        metadata = session.model_metadata
        if metadata is not None:
            budget = resolve_native_compaction_budget(metadata=metadata, requested_output_tokens=None)
            if session.compaction_invoke is not None and occupied >= budget.auto_compact_limit_tokens:
                compact_live_history(
                    history, value, self._invoke_compaction,
                    before=lambda: session.bindings.before_compact(turn_index),
                    after=lambda: self._after_compaction(turn_index),
                )
                session.history_usage.reset()
                session.bindings.prepare_context()
                value = build_input()
                measured = session.request_measure(value, surface.provider_specs)
                occupied = session.history_usage.estimate(
                    history.snapshot, history.generation, measured,
                )
            value, measured, _ = self._fit_deliveries(
                build_input, surface.provider_specs, value, measured, occupied,
                budget.effective_input_limit_tokens,
            )
        prepared = session.context_runtime_manager.prepare_provider_context(
            build_input=lambda: value, tool_specs=surface.provider_specs,
            measure=lambda *_: measured,
        )
        try:
            request = NativePreparedRequest(
                tool_specs=surface.provider_specs, conversation_input=value,
                measurement=prepared.measurement,
                context_preparation_id=prepared.context_preparation_id,
                immutable_prefix_digest=value.immutable_prefix_digest,
                provider_capabilities=session.provider_capabilities,
            )
        except BaseException:
            session.context_runtime_manager.abort_provider_context(
                prepared.context_preparation_id,
            )
            raise
        session.current_request = request
        history.prepare_context(value, model=session.rollout_model,
                                        tools=[spec.to_dict() for spec in surface.provider_specs],
                                        effective_permissions=session.bindings.effective_permissions())
        return request

    def _fit_deliveries(
        self, build_input: Callable[[], NativeHistoryProviderInput],
        specs: tuple[NativeToolSpec, ...], value: NativeHistoryProviderInput,
        measured: NativeProviderRequestMeasurement, occupied: int, limit: int,
    ) -> tuple[NativeHistoryProviderInput, NativeProviderRequestMeasurement, int]:
        session = self._session
        # Every iteration strictly reduces live text; metadata/media form a floor.
        while occupied > limit:
            original = tuple(session.history.snapshot)
            consumed = {id(entry) for entry in session.history.consumed_outputs}
            eligible = tuple(entry for entry in session.history.protected_suffix()
                             if id(entry) not in consumed and (entry.preserve_output or entry.page is not None))
            reduced = reduce_tool_deliveries(eligible)
            replacements = {id(entry): updated for entry, updated in zip(eligible, reduced)}
            replacement = tuple(replacements.get(id(entry), entry) for entry in original)
            if replacement == original:
                break
            session.history.replace(replacement)
            session.history_usage.reset()
            value = build_input()
            measured = session.request_measure(value, specs)
            occupied = session.history_usage.estimate(session.history.snapshot, session.history.generation, measured)
        return value, measured, occupied

    def _invoke_compaction(self, request: NativeHistoryProviderInput) -> ProviderOutputEnvelope:
        session = self._session
        invoke = session.compaction_invoke
        assert invoke is not None
        manager = session.context_runtime_manager
        prepared = manager.prepare_provider_context(
            build_input=lambda: request, tool_specs=(), measure=session.request_measure,
        )
        try:
            response = invoke(request)
        except BaseException:
            manager.abort_provider_context(
                prepared.context_preparation_id,
            )
            raise
        manager.commit_provider_usage(
            prepared.context_preparation_id, response.usage,
        )
        session.history.record_usage(response, purpose='compaction')
        return response

    def _after_compaction(self, turn: int) -> None:
        self._session.history_usage.reset()
        self._session.bindings.after_compact(turn)
