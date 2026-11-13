from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ownpi.llm.sse_failure import normalize_failure_exception
from ownpi.runtime.native_tools.loop_engine.contracts import (
    NativeLoopDirective, NativeLoopSnapshot, NativeLoopTurnPlan,
)
from ownpi.runtime.native_tools.provider import (
    ProviderOutputEnvelope, ProviderOutputItem, ProviderOutputItemKind,
)
from ownpi.runtime.native_tools.tool_invocation import build_tool_invocations


def incomplete_hosted_web_search_statuses(
    response_items: tuple[ProviderOutputItem, ...],
) -> tuple[str, ...]:
    return tuple(
        _hosted_web_search_status(item)
        for item in response_items
        if item.kind is ProviderOutputItemKind.HOSTED_TOOL_CALL
        and _hosted_web_search_status(item) != "completed"
    )


def _hosted_web_search_status(item: ProviderOutputItem) -> str:
    payload_status = str(item.provider_payload.to_object().get('status') or '')
    if payload_status:
        return payload_status
    return item.provider_status


@dataclass
class NativeProviderResponseDecision:
    hosted_web_search_call_count: int = 0

    def record(self, response: ProviderOutputEnvelope) -> None:
        self.hosted_web_search_call_count += sum(
            item.kind is ProviderOutputItemKind.HOSTED_TOOL_CALL
            for item in response.completed_items
        )

    def plan_turn(self, snapshot: NativeLoopSnapshot) -> NativeLoopTurnPlan | None:
        response = snapshot.response
        turn_index = snapshot.turn_index
        calls = build_tool_invocations(response)
        invalid_hosted_statuses = incomplete_hosted_web_search_statuses(response.completed_items)
        if invalid_hosted_statuses and not calls:
            return NativeLoopTurnPlan(directive=NativeLoopDirective.continue_with(
                reason="hosted_web_search_incomplete",
                correction_text=(
                    "The hosted web-search response was incomplete. "
                    "Retry the search or use another available evidence path."
                ),
                correction_index=turn_index,
            ))
        if not calls and is_max_tokens_truncation(response.provider_metadata):
            return NativeLoopTurnPlan(directive=NativeLoopDirective.continue_with(
                reason="max_tokens_recovery",
                correction_text=max_tokens_recovery_text(),
                correction_index=turn_index,
            ))
        if not calls and is_provider_continuation(response.provider_metadata):
            return NativeLoopTurnPlan(directive=NativeLoopDirective.continue_with(
                reason='provider_requested_continuation',
            ))
        if not calls and self.hosted_web_search_call_count and not response.assistant_text.strip():
            return NativeLoopTurnPlan(directive=NativeLoopDirective.continue_with(
                reason="hosted_web_search_missing_assistant_message",
                correction_text=(
                    "The hosted web-search response contained no assistant message. "
                    "Retry the search and provide a response."
                ),
                correction_index=turn_index,
            ))
        return NativeLoopTurnPlan(calls=calls) if calls else None


def is_max_tokens_truncation(provider_metadata: dict[str, Any]) -> bool:
    stop_reason = str(provider_metadata.get("stop_reason", "") or "").strip().lower()
    finish_reason = str(provider_metadata.get("finish_reason", "") or "").strip().lower()
    status = str(provider_metadata.get("status", "") or "").strip().lower()
    if stop_reason == "max_tokens":
        return True
    if finish_reason == "length":
        return True
    return status in {"incomplete", "max_output_tokens", "length"}


def is_provider_continuation(provider_metadata: dict[str, Any]) -> bool:
    return str(provider_metadata.get('stop_reason') or '').strip().lower() == 'pause_turn'


def max_tokens_recovery_text() -> str:
    return (
        "Output token limit hit. Resume directly; do not apologize, recap, or restate the plan. "
        "Continue with the next concrete action. Break the remaining work into smaller "
        "tool-backed steps."
    )






def provider_failure_details(error: BaseException) -> dict[str, Any]:
    from ownpi.runtime.native_tools.compaction.local import HistoryCompactionError

    while isinstance(error, HistoryCompactionError) and error.__cause__ is not None:
        error = error.__cause__
    error = normalize_failure_exception(error)
    failure_class = str(getattr(error, "failure_class", "") or "")
    diagnostics: dict[str, Any] = {}
    to_trace_metadata = getattr(error, "to_trace_metadata", None)
    if callable(to_trace_metadata):
        candidate = to_trace_metadata()
        if isinstance(candidate, dict):
            diagnostics = dict(candidate)
    if failure_class not in {
        "transient_provider_failure",
        "deterministic_provider_input_failure",
        "provider_protocol_failure",
        "local_runtime_failure",
    }:
        failure_class = "provider_protocol_failure"
    auto_resume_safe = failure_class == "transient_provider_failure"
    recommended_recovery = {
        "transient_provider_failure": "retry_current_provider_turn",
        "deterministic_provider_input_failure": "repair_provider_input_then_retry",
        "provider_protocol_failure": "inspect_provider_protocol_then_retry",
        "local_runtime_failure": "repair_local_runtime_then_retry",
    }[failure_class]
    return {
        "failure_class": failure_class,
        "failure_diagnostics": diagnostics,
        "auto_resume_safe": auto_resume_safe,
        "recommended_recovery": recommended_recovery,
    }


__all__ = [
    "NativeProviderResponseDecision",
    "incomplete_hosted_web_search_statuses",
    "is_max_tokens_truncation",
    "is_provider_continuation",
    "max_tokens_recovery_text",
    "provider_failure_details",
]
