"""Generation-only adapters; no request projection or durable model views."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import CancelledError
from typing import TYPE_CHECKING

from ownpi.controller.runtime_control_signal import RuntimeControlRequested
from ownpi.runtime.interactions.pause import HumanClarificationPausedError
from ownpi.llm.provider_retry import RetryCancelled
from ownpi.runtime.native_tools.history import NativeHistory
from ownpi.runtime.native_tools.history_items import HistoryEntry
from ownpi.runtime.native_tools.history_items import HistoryMessage
from ownpi.runtime.native_tools.provider_json import FrozenProviderJson
from ownpi.runtime.hooks.context_delivery import mark_hook_deliveries_consumed, select_active_hook_fragments
from ownpi.runtime.hooks.types import HookDeliveryTarget
from ownpi.runtime.rollout.hook_events import record_hook_delivery
from ownpi.runtime.native_tools.spec import NativeToolResult
from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.native_tools.tool_output import local_output, outcome_output
from ownpi.runtime.tools.visible_read_state import remember_visible_file_page

if TYPE_CHECKING:
    from ownpi.runtime.rollout.session import RolloutHistory
    from ownpi.runtime.native_tools.executor import NativeToolExecutionOutcome
    from ownpi.runtime.native_tools.loop_engine.contracts import NativeExecutionRequest
    from ownpi.runtime.tools.runtime import ToolRuntimeContext


def record_tool_delivery(
    history: NativeHistory | RolloutHistory, delivery: NativeToolExecutionOutcome | NativeToolResult, *,
    call: NativeExecutionRequest, exchange_id: str, context: ToolRuntimeContext | None = None,
) -> HistoryEntry | None:
    entry = (
        local_output(delivery, exchange_id=exchange_id, sequence=getattr(call, 'sequence_index', None))
        if isinstance(delivery, NativeToolResult)
        else outcome_output(delivery, exchange_id=exchange_id, context=context, policy=history.policy)
    )
    if entry is None:
        return None
    recorded = history.record_items((entry,))[0]
    if context is not None:
        from ownpi.runtime.rollout.delivery_events import record_result_delivery

        record_result_delivery(context, history, recorded, call)
    return recorded


def confirm_visible_file_pages(
    entries: tuple[HistoryEntry, ...], *, context: ToolRuntimeContext,
) -> None:
    """Only successful normal sampling confirms the page actually sent."""
    for entry in entries:
        if (entry.page is None or entry.projection_source is None
                or entry.item.tool_name != 'file_reader' or not entry.item.success):
            continue
        try:
            remember_visible_file_page(
                context=context, tool_result=ToolResult(
                    ok=True, tool_name='file_reader', data=entry.projection_source.to_object(),
                ), page=entry.page,
            )
        except (RuntimeControlRequested, CancelledError, RetryCancelled):
            raise
        except Exception:
            logging.getLogger(__name__).warning('Visible file read-state update failed; history retained', exc_info=True)


def record_tool_context(
    history: NativeHistory | RolloutHistory, *, context: ToolRuntimeContext | None, exchange_id: str,
    calls: tuple[NativeExecutionRequest, ...],
) -> None:
    if context is None:
        return
    call_ids = {value for call in calls for value in (call.request_id, call.provider_call_id) if value}
    state = context.state_store.state.context_state
    fragments = select_active_hook_fragments(
        state, target=HookDeliveryTarget.CONVERSATION,
        run_id=context.run_id, loop_kind=context.actor_kind, stage=context.stage.value,
        attempt=context.attempt,
    )
    # Producers already append deliveries in order. Drain only on the loop owner,
    # after all workers return, rather than regrouping by call or hook name.
    retained_exit: BaseException | None = None
    for fragment in fragments:
        if fragment.scope.tool_call_id not in call_ids:
            continue
        if fragment.event_name not in {'PreToolUse', 'PostToolUse', 'PermissionRequest', 'Elicitation'}:
            continue
        try:
            recorded = history.record_items((HistoryEntry(
                HistoryMessage('developer', fragment.text, FrozenProviderJson.from_value(fragment.to_dict())),
                exchange_id,
            ),))
            if recorded and record_hook_delivery(
                state, (fragment.delivery_id,), history=history, entry=recorded[0],
                consumer='native_history',
            ):
                mark_hook_deliveries_consumed(state, (fragment.delivery_id,), consumer='native_history')
        except BaseException as error:
            retained_exit = retain_delivery_exit(retained_exit, error)
    if retained_exit is not None:
        raise retained_exit


def retain_delivery_exit(
    retained: BaseException | None, error: BaseException,
) -> BaseException:
    def priority(exit_error: BaseException) -> int:
        if isinstance(exit_error, (RuntimeControlRequested, HumanClarificationPausedError, CancelledError, asyncio.CancelledError, RetryCancelled)):
            return 1
        return 0 if isinstance(exit_error, Exception) else 2

    if retained is None or error is retained:
        return error
    if priority(error) > priority(retained):
        error.add_note(f'Earlier exit before history delivery: {type(retained).__name__}: {retained}')
        return error
    retained.add_note(f'Additional history delivery exit: {type(error).__name__}: {error}')
    return retained
