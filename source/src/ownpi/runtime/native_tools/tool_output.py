"""Convert raw tool deliveries once; history owns ordinary output truncation."""

from __future__ import annotations

import logging
from concurrent.futures import CancelledError
from typing import TYPE_CHECKING

from ownpi.controller.runtime_control_signal import RuntimeControlRequested
from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.limits.tool_output import (
    DEFAULT_TOOL_RESULT_TRUNCATION_POLICY, DEFAULT_TOOL_RESULT_TRUNCATION_POLICY_LIMIT, TruncationPolicy,
)
from ownpi.runtime.model_content import ModelContentItem
from ownpi.runtime.native_tools.history_items import HistoryEntry, ToolOutputItem, ToolSearchOutputItem
from ownpi.runtime.native_tools.spec import NativeToolError, NativeToolResult
from ownpi.runtime.native_tools.provider_json import FrozenProviderJson
from ownpi.runtime.native_tools.search_history import native_search_failure, search_history_output
from ownpi.runtime.tools.result_body import canonical_ordinary_result_body
from ownpi.runtime.tools.result_projection import project_tool_result

if TYPE_CHECKING:
    from ownpi.runtime.native_tools.executor import NativeToolExecutionOutcome
    from ownpi.runtime.native_tools.loop_engine.contracts import NativeExecutionRequest
    from ownpi.runtime.tools.runtime import ToolRuntimeContext


def error_output(
    error: NativeToolError, *, call: NativeExecutionRequest | None = None,
) -> ToolOutputItem | ToolSearchOutputItem:
    if call is not None:
        search_failure = native_search_failure(call)
        if search_failure is not None:
            return search_failure
    body = canonical_ordinary_result_body(ToolResult(
        ok=False, tool_name=error.tool_name, error=error.error_message,
        data={key: value for key, value in (
            ('failure_class', error.failure_class), ('recovery_hint', error.recovery_hint),
        ) if value},
    ))
    return ToolOutputItem(error.tool_name, error.provider_call_id, error.request_id, body, False)


def local_error_result(error: NativeToolError) -> NativeToolResult:
    """Local handlers retain their business result ABI, without a frozen view."""
    return NativeToolResult(
        tool_name=error.tool_name, request_id=error.request_id,
        provider_call_id=error.provider_call_id, status='failed',
        output_text=str(error_output(error).body),
        metadata={**error.metadata, 'failure_class': error.failure_class},
    )


def local_output(result: NativeToolResult, *, exchange_id: str, sequence: int | None) -> HistoryEntry:
    body: str | tuple[ModelContentItem, ...] = result.output_text
    body = _with_content(body, result.content_items)
    return HistoryEntry(ToolOutputItem(
        result.tool_name, result.provider_call_id, result.request_id, body,
        result.status == 'completed',
    ), exchange_id, sequence)


def outcome_output(
    outcome: NativeToolExecutionOutcome, *, exchange_id: str = '',
    context: ToolRuntimeContext | None = None,
    policy: TruncationPolicy = DEFAULT_TOOL_RESULT_TRUNCATION_POLICY,
) -> HistoryEntry | None:
    call = outcome.call
    sequence = getattr(call, 'sequence_index', None)
    try:
        search_entry = search_history_output(outcome, exchange_id=exchange_id)
    except (TypeError, ValueError, KeyError, OverflowError, RecursionError) as error:
        return HistoryEntry(error_output(NativeToolError(
            tool_name=call.tool_name, provider_call_id=call.provider_call_id,
            request_id=call.request_id, failure_class='tool_output_delivery_error',
            error_message=f'Tool execution returned, but its output could not be delivered: {type(error).__name__}',
        ), call=call), exchange_id, sequence)
    if search_entry is not None:
        return search_entry
    if outcome.native_error is not None:
        return HistoryEntry(error_output(outcome.native_error), exchange_id, sequence)
    invocation = outcome.invocation
    if invocation is None or invocation.pending_interaction_session is not None:
        return None
    result = invocation.tool_result
    if result is None:
        record = invocation.record
        effects = record.context_effects
        return HistoryEntry(error_output(NativeToolError(
            tool_name=call.tool_name, provider_call_id=call.provider_call_id,
            request_id=call.request_id,
            failure_class=str(effects.get('failure_class') or record.status),
            error_message=record.error or record.warning or record.status,
            recovery_hint=str(effects.get('schema_validation_hint') or effects.get('recovery_hint') or ''),
        )), exchange_id, sequence)
    try:
        return _result_output(outcome, exchange_id=exchange_id, context=context, policy=policy)
    except (RuntimeControlRequested, CancelledError):
        raise
    except (TypeError, ValueError, KeyError, OverflowError, RecursionError) as error:
        logging.getLogger(__name__).warning('Tool output delivery failed: %s', call.tool_name, exc_info=True)
        return HistoryEntry(error_output(NativeToolError(
            tool_name=call.tool_name, provider_call_id=call.provider_call_id,
            request_id=call.request_id, failure_class='tool_output_delivery_error',
            error_message=f'Tool execution returned, but its output could not be delivered: {type(error).__name__}',
        )), exchange_id, sequence)


def _result_output(
    outcome: NativeToolExecutionOutcome, *, exchange_id: str,
    context: ToolRuntimeContext | None, policy: TruncationPolicy,
) -> HistoryEntry:
    invocation = outcome.invocation
    assert invocation is not None and invocation.tool_result is not None
    result = invocation.tool_result
    call = outcome.call
    if invocation.record.context_effects.get('post_tool_use_blocked'):
        reason = str(invocation.record.context_effects.get('post_tool_use_block_reason') or 'PostToolUse hook blocked the tool result')
        return HistoryEntry(error_output(NativeToolError(
            tool_name=call.tool_name, provider_call_id=call.provider_call_id,
            request_id=call.request_id, failure_class='post_tool_use_blocked',
            error_message='Tool executed; PostToolUse hook rejected the result: ' + reason,
        )), exchange_id, getattr(call, 'sequence_index', None))

    def page_byte_limit() -> int:
        definition = context.tool_router.resolve(call.tool_name) if context is not None else None
        summary = definition.to_summary() if definition is not None else None
        return int(
            (summary.max_result_size_chars or summary.max_prompt_result_chars
             or DEFAULT_TOOL_RESULT_TRUNCATION_POLICY_LIMIT)
            if summary is not None else DEFAULT_TOOL_RESULT_TRUNCATION_POLICY_LIMIT
        )

    projection = project_tool_result(result, policy=policy, page_byte_limit=page_byte_limit)
    if projection.diagnostic:
        return HistoryEntry(error_output(NativeToolError(
            tool_name=call.tool_name, provider_call_id=call.provider_call_id,
            request_id=call.request_id, failure_class='tool_output_delivery_error',
            error_message='Tool execution returned, but its output could not be delivered: ' + projection.diagnostic,
        )), exchange_id, getattr(call, 'sequence_index', None))
    body = _with_content(projection.body, result.content_items)
    item = ToolOutputItem(
        call.tool_name, call.provider_call_id, call.request_id, body, outcome.ok,
    )
    return HistoryEntry(
        item, exchange_id, getattr(call, 'sequence_index', None),
        projection.preserve_output, projection.page,
        projection_source=FrozenProviderJson.from_value(projection.source)
        if projection.source is not None else None,
    )


def _with_content(body: str, content: tuple[ModelContentItem, ...]) -> str | tuple[ModelContentItem, ...]:
    if not content:
        return body
    if not body or any(item.type == 'input_text' and item.text == body for item in content):
        return content
    return (ModelContentItem.input_text(body), *content)
