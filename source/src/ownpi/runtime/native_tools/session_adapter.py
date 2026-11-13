"""Bind main-loop effects and request identity outside the shared session."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from ownpi.runtime.hooks.context_delivery import mark_hook_deliveries_consumed, persist_hook_delivery_batch
from ownpi.runtime.hooks.types import CompactOutcome, PostCompactRequest, PreCompactRequest
from ownpi.runtime.native_tools.compaction.local import HistoryCompactionStopped
from ownpi.runtime.native_tools.core_loop_models import NativeToolLoopRequest
from ownpi.runtime.native_tools.core_loop_models import NativeToolProviderTurnCallable, NativeToolProviderTurn
from ownpi.runtime.native_tools.history_items import HistoryEntry
from ownpi.runtime.native_tools.loop_engine.contracts import NativePreparedRequest, NativeLoopToolValue
from ownpi.runtime.native_tools.history_context import (
    compact_hook_entries_in_request, record_compact_hook_context, record_permission_context,
)
from ownpi.runtime.native_tools.history_recording import confirm_visible_file_pages, record_tool_context, record_tool_delivery
from ownpi.runtime.native_tools.loop_engine.session_bindings import NativeSessionBindings
from ownpi.runtime.native_tools.provider import ProviderOutputEnvelope
from ownpi.runtime.native_tools.spec import NativeToolResult
from ownpi.runtime.rollout.hook_events import record_hook_delivery
from ownpi.runtime.rollout.session import RolloutHistory
from ownpi.runtime.native_tools.tool_invocation import NativeToolInvocation

if TYPE_CHECKING:
    from ownpi.runtime.native_tools.core_loop_session import CoreLoopSession


def build_native_session_bindings(owner: CoreLoopSession) -> NativeSessionBindings:
    def prepare_context() -> None:
        record_permission_context(owner.shared.history, owner.context)
        record_compact_hook_context(owner.shared.history, owner.context)

    def provider_recorded(response: ProviderOutputEnvelope) -> None:
        owner.provider_decision.record(response)

    def record_delivery(
        history: RolloutHistory, result: NativeLoopToolValue, *,
        call: NativeToolInvocation, exchange_id: str,
    ) -> HistoryEntry | None:
        entry = record_tool_delivery(history, result, call=call, exchange_id=exchange_id, context=owner.context)
        if not isinstance(result, NativeToolResult) and entry is not None and entry.item.success:
            changed = owner.tool_surface.record_search_invocation(
                tool_result=result.invocation.tool_result,
                provider_call_id=call.provider_call_id, request_id=call.request_id,
            )
            if changed:
                owner._publish_loaded_tools()
        return entry

    def consume_hook_deliveries(history: RolloutHistory, items: tuple[HistoryEntry, ...]) -> None:
        context_state = owner.context.state_store.state.context_state
        for entry in compact_hook_entries_in_request(history, items):
            delivery_id = entry.item.metadata.to_object()['delivery_id']
            if record_hook_delivery(context_state, (delivery_id,), history=history, entry=entry,
                                    consumer='native_provider_sampling'):
                mark_hook_deliveries_consumed(context_state, (delivery_id,), consumer='native_provider_sampling')

    def compact_hook(request_type: type[PreCompactRequest] | type[PostCompactRequest], turn: int) -> None:
        context = owner.context
        if context.hook_engine is None:
            return
        outcome = context.hook_engine.run(
            request_type(payload={'trigger': 'auto', 'phase': 'pre_sampling' if turn == 1 else 'mid_loop'}),
            effective_permissions=context.canonical_permissions,
        )
        if not isinstance(outcome, CompactOutcome):
            raise TypeError('Compaction hook returned an incompatible outcome')
        persist_hook_delivery_batch(context.state_store.state.context_state, outcome.delivery)
        if outcome.blocked:
            raise HistoryCompactionStopped(outcome.reason or 'Compaction stopped by hook')


    return NativeSessionBindings(
        prepare_context=prepare_context,
        effective_permissions=lambda: owner.context.canonical_permissions.to_dict(),
        provider_recorded=provider_recorded,
        consume_compact_hook_deliveries=consume_hook_deliveries,
        record_tool_delivery=record_delivery,
        record_tool_context=lambda history, **kwargs: record_tool_context(history, context=owner.context, **kwargs),
        before_compact=lambda turn: compact_hook(PreCompactRequest, turn),
        after_compact=lambda turn: compact_hook(PostCompactRequest, turn),
        confirm_visible_file_pages=lambda items: confirm_visible_file_pages(items, context=owner.context),
    )


def bind_main_provider_request(
    owner: CoreLoopSession, provider_turn: NativeToolProviderTurnCallable,
) -> Callable[[NativePreparedRequest], NativeToolProviderTurn]:
    def invoke(prepared: NativePreparedRequest) -> NativeToolProviderTurn:
        try:
            request = NativeToolLoopRequest(
                stage=owner.context.stage.value, attempt=owner.context.attempt,
                native_tool_loop_index=owner.context.continuation_index,
                conversation_input=prepared.conversation_input, tool_specs=prepared.tool_specs,
                measurement=prepared.measurement, context_preparation_id=prepared.context_preparation_id,
                immutable_prefix_digest=prepared.immutable_prefix_digest,
                provider_capabilities=prepared.provider_capabilities,
            )
        except BaseException:
            owner.shared.context_runtime_manager.abort_provider_context(
                prepared.context_preparation_id,
            )
            owner.shared.current_request = None
            raise
        return provider_turn(request)
    return invoke
