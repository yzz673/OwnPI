"""Build self-contained search history from raw call facts and produced specs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ownpi.runtime.native_tools.history_items import HistoryEntry, ToolSearchOutputItem
from ownpi.runtime.native_tools.provider_json import FrozenProviderJson
from ownpi.runtime.native_tools.search import SEARCH_HISTORY_METADATA_KEY
from ownpi.runtime.tools.result_body import ordinary_body_with_diagnostic

if TYPE_CHECKING:
    from ownpi.runtime.native_tools.executor import NativeToolExecutionOutcome
    from ownpi.runtime.native_tools.loop_engine.contracts import NativeExecutionRequest


def _call_type(call: NativeExecutionRequest) -> str:
    payload = call.raw_provider_payload
    if isinstance(payload, FrozenProviderJson):
        payload = payload.to_object()
    return str(payload.get('type') or 'function_call')


def native_search_failure(call: NativeExecutionRequest) -> ToolSearchOutputItem | None:
    """Preserve the native protocol on failures before or during delivery."""
    if _call_type(call) != 'tool_search_call':
        return None
    return ToolSearchOutputItem(
        tool_name=call.tool_name, call_id=call.provider_call_id,
        request_id=call.request_id, tools=(), success=False, call_type='tool_search_call',
    )


def search_history_output(
    outcome: NativeToolExecutionOutcome, *, exchange_id: str = '',
) -> HistoryEntry | None:
    """Call before ordinary conversion, including from the rejection branch.

    A native search failure completes with empty tools. Function failures and
    results without search metadata remain the ordinary output owner's concern.
    """
    call = outcome.call
    call_type = _call_type(call)
    native = call_type == 'tool_search_call'
    invocation = outcome.invocation
    if outcome.native_error is None and (
        invocation is None or invocation.pending_interaction_session is not None
    ):
        return None
    result = invocation.tool_result if invocation is not None else None
    blocked = invocation is not None and bool(
        invocation.record.context_effects.get('post_tool_use_blocked'),
    )
    success = outcome.ok and not blocked
    if not native and not success:
        return None
    frozen = result.metadata.get(SEARCH_HISTORY_METADATA_KEY) if result is not None else None
    if not native and not isinstance(frozen, str):
        return None
    tools: tuple[FrozenProviderJson, ...] = ()
    receipt_ref = ''
    if success and isinstance(frozen, str):
        definitions = FrozenProviderJson(frozen).to_object()
        tools = tuple(FrozenProviderJson.from_value(spec) for spec in definitions['tools'])
        receipt_ref = definitions['receipt_ref']
    body = ''
    if not native and result is not None:
        body, diagnostic = ordinary_body_with_diagnostic(result)
        if diagnostic:
            raise ValueError(diagnostic)
    return HistoryEntry(
        ToolSearchOutputItem(
            tool_name=call.tool_name, call_id=call.provider_call_id,
            request_id=call.request_id, tools=tools, success=success,
            call_type=call_type, receipt_ref=receipt_ref, body=body,
        ),
        exchange_id=exchange_id,
        output_sequence_index=getattr(call, 'sequence_index', None),
    )
