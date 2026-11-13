from __future__ import annotations

from dataclasses import asdict, dataclass
from collections.abc import Callable, Iterator
import hashlib
import io
import json
import logging
import math
from typing import Any

from ownpi.llm.cancellation import run_provider_io
from ownpi.llm.failure_diagnostics import diagnostic_message
from ownpi.llm.errors import ProviderContextOverflowError
from ownpi.llm.transport import classify_transport_error

from ownpi.llm.provider_error_classification import (
    ProviderErrorDisposition,
    ProviderFailureClass,
    ProviderHTTPErrorClassification,
    ProviderRetryKind,
    ProviderRetryOwner,
    classify_provider_error,
)


def decode_native_json(data: str) -> Any:
    """Decode the native wire JSON domain accepted by serde_json."""
    def reject_constant(value: str) -> None:
        raise ValueError(f'Invalid JSON constant: {value}')

    def finite_float(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('JSON number out of range')
        return number

    try:
        value = json.loads(data, parse_constant=reject_constant, parse_float=finite_float)
        # Python accepts lone escaped surrogates; serde_json strings do not.
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (ValueError, UnicodeError, RecursionError) as error:
        raise json.JSONDecodeError(str(error), data, 0) from error
    return value


def iter_native_sse_events(
    response: Any,
    *,
    check_cancelled: Callable[[str], None],
    done_marker: bool = False,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Decode native SSE without treating an error event as stream termination."""
    if not hasattr(response, 'readline'):
        check_cancelled('sse_read_before_body')
        response = io.BytesIO(run_provider_io(response.read))
        check_cancelled('sse_read_after_body')
    name = ''
    chunks: list[str] = []
    while True:
        check_cancelled('sse_read_before_line')
        raw = run_provider_io(response.readline)
        check_cancelled('sse_read_after_line')
        line = raw if isinstance(raw, str) else raw.decode('utf-8', errors='replace')
        line = line.rstrip('\r\n')
        if not line:
            if chunks:
                data = '\n'.join(chunks)
                if done_marker and data == '[DONE]':
                    return
                try:
                    payload = decode_native_json(data)
                except json.JSONDecodeError:
                    logging.getLogger(__name__).debug('Skipping malformed native SSE JSON')
                else:
                    if isinstance(payload, dict):
                        kind = payload.get('type')
                        event_name = kind if isinstance(kind, str) else name
                        if not event_name and isinstance(payload.get('choices'), list):
                            event_name = 'chat.completion.chunk'
                        yield event_name, payload
            name, chunks = '', []
            if not raw:
                return
        elif line.startswith('event:'):
            name = line[6:].lstrip(' ')
        elif line.startswith('data:'):
            chunks.append(line[5:].removeprefix(' '))


def native_responses_event_decodable(payload: dict[str, Any]) -> bool:
    """Match the typed ResponsesStreamEvent wire fields in local Codex."""
    if not isinstance(payload.get('type'), str):
        return False
    for key in ('item_id', 'call_id', 'delta', 'text'):
        if payload.get(key) is not None and not isinstance(payload[key], str):
            return False
    return all(
        payload.get(key) is None or _is_i64(payload[key])
        for key in ('summary_index', 'content_index')
    )


def _is_i64(value: Any) -> bool:
    return type(value) is int and -(2**63) <= value < 2**63


def native_response_completion_decodable(response: Any) -> bool:
    """Completion identity is independent of optional accounting fields."""
    if not isinstance(response, dict) or not isinstance(response.get('id'), str):
        return False
    if response.get('end_turn') is not None and type(response['end_turn']) is not bool:
        return False
    return True


def native_responses_error(
    event_name: str, payload: dict[str, Any], *, tracker: SSEEventTracker,
) -> ProviderSSEFailure | None:
    """Translate process_responses_event errors, without deciding when to stop."""
    response = payload.get('response')
    if event_name == 'response.completed':
        if response is None or native_response_completion_decodable(response):
            return None
        return tracker.failure(
            failure_kind='malformed_completion',
            failure_class='transient_provider_failure', retryable=True,
            semantic_disposition='retryable_transport',
        )
    if event_name == 'response.incomplete':
        details = response.get('incomplete_details') if isinstance(response, dict) else None
        reason = details.get('reason') if isinstance(details, dict) else None
        return tracker.failure(
            failure_kind='response_incomplete',
            failure_class='transient_provider_failure', retryable=True,
            semantic_disposition='retryable_transport',
            incomplete_reason=reason if isinstance(reason, str) else 'unknown',
            error_payload={'message': f'Incomplete response returned, reason: {reason or "unknown"}'},
        )
    if event_name != 'response.failed':
        return None
    error = response.get('error') if isinstance(response, dict) else None
    if not isinstance(error, dict) or any(
        error.get(key) is not None and not isinstance(error[key], str)
        for key in ('code', 'message', 'plan_type', 'type')
    ) or (isinstance(error, dict) and error.get('resets_at') is not None and not _is_i64(error['resets_at'])):
        error = {}
    code = error.get('code')
    disposition: ProviderErrorDisposition = {
        'context_length_exceeded': 'deterministic_input',
        'insufficient_quota': 'deterministic_auth_or_quota',
        'usage_not_included': 'deterministic_auth_or_quota',
        'cyber_policy': 'deterministic_policy',
        'misalignment_policy_violation': 'deterministic_policy',
        'invalid_prompt': 'deterministic_input',
        'bio_policy': 'deterministic_input',
    }.get(code, 'retryable_provider')
    retryable = disposition == 'retryable_provider' and code not in {'slow_down', 'server_is_overloaded'}
    classification = classify_provider_error(error)
    return tracker.failure(
        failure_kind='response_failed',
        failure_class=('transient_provider_failure' if disposition == 'retryable_provider' else 'deterministic_provider_input_failure'),
        retryable=retryable, error_payload=error, semantic_disposition=disposition,
        retry_after_seconds=(classification.retry_after_seconds if code == 'rate_limit_exceeded' else None),
    )


def native_blocking_response_error(
    payload: dict[str, Any], *, responses: bool,
) -> ProviderSSEFailure | None:
    """Map an HTTP JSON body's explicit failure to the native stream error route."""
    tracker = SSEEventTracker()
    status = payload.get('status')
    if responses:
        if status == 'incomplete':
            return native_responses_error('response.incomplete', {'response': payload}, tracker=tracker)
        if status == 'failed' or payload.get('error') is not None:
            return native_responses_error('response.failed', {'response': payload}, tracker=tracker)
    elif isinstance(payload.get('error'), dict):
        return tracker.explicit_error_failure(payload['error'])
    return None


_DETERMINISTIC_INCOMPLETE_REASONS = {
    'context_length_exceeded',
    'input_too_large',
    'max_input_tokens',
    'max_output_tokens',
    'model_context_window_exceeded',
}
@dataclass(frozen=True)
class SSEFailureDiagnostics:
    failure_class: ProviderFailureClass
    failure_kind: str
    retryable: bool
    event_count: int = 0
    terminal_event: str = ''
    response_id: str = ''
    response_status: str = ''
    incomplete_reason: str = ''
    error_code: str = ''
    error_type: str = ''
    error_message: str = ''
    semantic_disposition: ProviderErrorDisposition = 'protocol_invalid'
    retry_after_seconds: float | None = None
    retry_kind: ProviderRetryKind | None = None
    retry_owner: ProviderRetryOwner | None = None
    http_status: int | None = None
    partial_output_observed: bool = False
    hosted_tool_execution_observed: bool = False
    hosted_tool_item_count: int = 0
    hosted_tool_item_ids: tuple[str, ...] = ()
    hosted_tool_item_types: tuple[str, ...] = ()
    hosted_tool_statuses: tuple[str, ...] = ()
    transport_exception_type: str = ''
    transport_exception_message: str = ''
    event_bytes: int = 0
    payload_sha256: str = ''
    failure_reason: str = 'unknown'
    failure_origin: str = 'provider_response'
    classification_basis: str = ''
    recovery_disposition: str = 'unknown'
    provider_error_code: str = ''
    provider_request_id: str = ''
    transport_phase: str = ''

    def __post_init__(self) -> None:
        if not self.classification_basis:
            object.__setattr__(self, 'classification_basis', 'stream:' + self.failure_kind)
        if self.failure_reason == 'unknown' and self.failure_kind not in {'terminal_error_without_payload', 'response_failed_without_error', 'explicit_provider_error', 'provider_http_error', 'provider_transport_error'}:
            reason = 'connection_interrupted' if self.failure_kind in {'empty_stream', 'premature_eof'} else 'malformed_response' if self.failure_kind.startswith('malformed') else 'protocol_violation'
            object.__setattr__(self, 'failure_reason', reason)
        if self.failure_kind in {'empty_stream', 'premature_eof'}:
            object.__setattr__(self, 'failure_origin', 'transport')
            object.__setattr__(self, 'semantic_disposition', 'retryable_transport')
        if self.recovery_disposition == 'unknown' and self.failure_reason != 'unknown':
            object.__setattr__(self, 'recovery_disposition', 'delayed_retry_candidate' if self.retryable else 'repair_required')
        for key in ('error_message', 'transport_exception_message', 'error_code', 'error_type', 'provider_error_code', 'provider_request_id'):
            object.__setattr__(self, key, diagnostic_message(getattr(self, key)))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProviderSSEFailure(RuntimeError):
    def __init__(self, diagnostics: SSEFailureDiagnostics) -> None:
        self.diagnostics = diagnostics
        super().__init__(_failure_message(diagnostics))

    @property
    def failure_class(self) -> ProviderFailureClass:
        return self.diagnostics.failure_class

    @property
    def retryable(self) -> bool:
        return self.diagnostics.retryable

    @property
    def retry_after_seconds(self) -> float | None:
        return self.diagnostics.retry_after_seconds

    @property
    def hosted_tool_execution_observed(self) -> bool:
        return self.diagnostics.hosted_tool_execution_observed

    def to_trace_metadata(self) -> dict[str, Any]:
        return self.diagnostics.to_dict()


class SSEEventTracker:
    def __init__(self) -> None:
        self._event_count = 0
        self._event_bytes = 0
        self._digest = hashlib.sha256()
        self._terminal_event = ''
        self._response_id = ''
        self._response_status = ''
        self._partial_output_observed = False
        self._hosted_items: dict[str, tuple[str, str, str]] = {}

    def observe(self, event_name: str, payload: dict[str, Any]) -> None:
        encoded = json.dumps(
            {'event': event_name, 'payload': payload},
            ensure_ascii=False,
            sort_keys=True,
            separators=(',', ':'),
            default=str,
        ).encode('utf-8')
        self._event_count += 1
        self._event_bytes += len(encoded)
        self._digest.update(encoded)
        if event_name in {
            'message_stop',
            'response.completed',
            'response.failed',
            'response.incomplete',
            'error',
        }:
            self._terminal_event = event_name
        response = payload.get('response')
        if isinstance(response, dict):
            self._response_id = str(response.get('id') or self._response_id)[:256]
            self._response_status = str(response.get('status') or self._response_status)[:160]
        if _payload_contains_partial_output(event_name, payload):
            self._partial_output_observed = True
        if event_name in {
            'response.output_item.added',
            'response.output_item.in_progress',
            'response.output_item.done',
        }:
            item = payload.get('item')
            if isinstance(item, dict) and str(item.get('type') or '') == 'web_search_call':
                output_index = payload.get('output_index')
                slot = (
                    f'output-index-{output_index}'
                    if output_index is not None
                    else str(item.get('id') or f'hosted-{len(self._hosted_items)}')[:160]
                )
                item_id = str(item.get('id') or slot)[:160]
                self._hosted_items[slot] = (
                    item_id,
                    'web_search_call',
                    str(item.get('status') or '')[:80],
                )
        if event_name == 'content_block_start':
            item = payload.get('content_block')
            if isinstance(item, dict) and str(item.get('type') or '') in {
                'server_tool_use',
                'web_search_tool_result',
            }:
                index = payload.get('index')
                slot = f'content-index-{index}'
                item_type = str(item.get('type') or '')
                item_id = str(item.get('id') or item.get('tool_use_id') or slot)[
                    :160
                ]
                self._hosted_items[slot] = (item_id, item_type, 'completed')

    def failure(
        self,
        *,
        failure_kind: str,
        failure_class: ProviderFailureClass,
        retryable: bool,
        incomplete_reason: str = '',
        error_payload: dict[str, Any] | None = None,
        semantic_disposition: ProviderErrorDisposition = 'protocol_invalid',
        retry_after_seconds: float | None = None,
        hosted_tool_execution_observed: bool = False,
        hosted_tool_item_count: int = 0,
        hosted_tool_item_ids: tuple[str, ...] = (),
        hosted_tool_item_types: tuple[str, ...] = (),
        hosted_tool_statuses: tuple[str, ...] = (),
        transport_exception: BaseException | None = None,
    ) -> ProviderSSEFailure:
        error = error_payload if isinstance(error_payload, dict) else {}
        details: dict[str, Any] = {}
        if transport_exception is not None:
            transport = classify_transport_error(transport_exception)
            retryable = transport.retriable
            failure_class = 'transient_provider_failure' if retryable else 'provider_protocol_failure'
            semantic_disposition = 'retryable_transport' if retryable else 'protocol_invalid'
            details = dict(failure_reason=transport.failure_reason, failure_origin=transport.failure_origin,
                           classification_basis='exception_type:' + transport.kind, transport_phase=transport.phase)
        elif error_payload is not None and failure_kind == 'explicit_provider_error':
            classification = classify_provider_error(error)
            details = {key: getattr(classification, key) for key in ('failure_reason', 'failure_origin', 'classification_basis', 'recovery_disposition')}
        elif incomplete_reason:
            details = dict(failure_reason='context_limit' if incomplete_reason in _DETERMINISTIC_INCOMPLETE_REASONS else 'protocol_violation')
        tracked_hosted_items = tuple(self._hosted_items.items())[:16]
        if tracked_hosted_items:
            hosted_tool_execution_observed = True
            hosted_tool_item_count = len(tracked_hosted_items)
            hosted_tool_item_ids = tuple(
                item_id for _, (item_id, _, _) in tracked_hosted_items
            )
            hosted_tool_item_types = tuple(
                item_type for _, (_, item_type, _) in tracked_hosted_items
            )
            hosted_tool_statuses = tuple(
                status for _, (_, _, status) in tracked_hosted_items
            )
        return ProviderSSEFailure(
            SSEFailureDiagnostics(
                failure_class=failure_class,
                failure_kind=str(failure_kind)[:160],
                **details,
                retryable=retryable,
                event_count=self._event_count,
                terminal_event=self._terminal_event,
                response_id=self._response_id,
                response_status=self._response_status,
                incomplete_reason=str(incomplete_reason)[:160],
                error_code=str(error.get('code') or '')[:160],
                provider_error_code=str(error.get('code') or '')[:160],
                provider_request_id=str(error.get('request_id') or '')[:160],
                error_type=str(error.get('type') or '')[:160],
                error_message=_bounded_diagnostic_message(error.get('message')),
                semantic_disposition=semantic_disposition,
                retry_after_seconds=retry_after_seconds,
                partial_output_observed=self._partial_output_observed,
                hosted_tool_execution_observed=hosted_tool_execution_observed,
                hosted_tool_item_count=max(0, int(hosted_tool_item_count)),
                hosted_tool_item_ids=tuple(
                    str(item_id or '')[:160] for item_id in hosted_tool_item_ids[:16]
                ),
                hosted_tool_item_types=tuple(
                    str(item_type or '')[:80]
                    for item_type in hosted_tool_item_types[:16]
                ),
                hosted_tool_statuses=tuple(
                    str(status or '')[:80] for status in hosted_tool_statuses[:16]
                ),
                transport_exception_type=(
                    type(transport_exception).__name__[:160]
                    if transport_exception is not None
                    else ''
                ),
                transport_exception_message=(
                    _bounded_diagnostic_message(transport_exception)
                    if transport_exception is not None
                    else ''
                ),
                event_bytes=self._event_bytes,
                payload_sha256=self._digest.hexdigest(),
            )
        )

    def incomplete_failure(self, payload: dict[str, Any]) -> ProviderSSEFailure:
        response = payload.get('response')
        response = response if isinstance(response, dict) else {}
        details = response.get('incomplete_details')
        details = details if isinstance(details, dict) else {}
        reason = str(details.get('reason') or payload.get('reason') or 'unknown')
        failure_class: ProviderFailureClass = (
            'deterministic_provider_input_failure'
            if reason.casefold() in _DETERMINISTIC_INCOMPLETE_REASONS
            else 'provider_protocol_failure'
        )
        return self.failure(
            failure_kind='response_incomplete',
            failure_class=failure_class,
            retryable=False,
            incomplete_reason=reason,
            semantic_disposition=(
                'deterministic_input'
                if failure_class == 'deterministic_provider_input_failure'
                else 'protocol_invalid'
            ),
        )

    def explicit_error_failure(self, error_payload: dict[str, Any]) -> ProviderSSEFailure:
        classification = classify_provider_error(error_payload)
        return self.failure(
            failure_kind='explicit_provider_error',
            failure_class=classification.failure_class,
            retryable=classification.retryable,
            error_payload=error_payload,
            semantic_disposition=classification.semantic_disposition,
            retry_after_seconds=classification.retry_after_seconds,
        )

    def finalize(self, *, has_output: bool) -> None:
        del has_output
        if self._event_count == 0:
            raise empty_sse_failure()
        if self._terminal_event in {'error', 'response.failed'}:
            raise self.failure(
                failure_kind='terminal_error_without_payload',
                failure_class='transient_provider_failure',
                retryable=True,
                semantic_disposition='retryable_transport',
            )
        if not self._terminal_event:
            raise self.failure(
                failure_kind='premature_eof',
                failure_class='transient_provider_failure',
                retryable=True,
            )


def malformed_sse_failure(*, data: str, event_count: int) -> ProviderSSEFailure:
    encoded = data.encode('utf-8', errors='replace')
    return ProviderSSEFailure(
        SSEFailureDiagnostics(
            failure_class='provider_protocol_failure',
            failure_kind='malformed_event_data',
            retryable=False,
            event_count=max(0, int(event_count)),
            event_bytes=len(encoded),
            payload_sha256=hashlib.sha256(encoded).hexdigest(),
        )
    )


def _bounded_diagnostic_message(value: Any) -> str:
    return diagnostic_message(value)


def empty_sse_failure() -> ProviderSSEFailure:
    return ProviderSSEFailure(
        SSEFailureDiagnostics(
            failure_class='transient_provider_failure',
            failure_kind='empty_stream',
            retryable=True,
            semantic_disposition='retryable_transport',
            payload_sha256=hashlib.sha256(b'').hexdigest(),
        )
    )


def protocol_sse_failure(
    failure_kind: str,
    *,
    error_message: str = '',
    hosted_tool_execution_observed: bool = False,
    hosted_tool_item_count: int = 0,
    hosted_tool_item_ids: tuple[str, ...] = (),
    hosted_tool_item_types: tuple[str, ...] = (),
    hosted_tool_statuses: tuple[str, ...] = (),
) -> ProviderSSEFailure:
    return ProviderSSEFailure(
        SSEFailureDiagnostics(
            failure_class='provider_protocol_failure',
            failure_kind=failure_kind,
            retryable=False,
            error_message=diagnostic_message(error_message),
            hosted_tool_execution_observed=hosted_tool_execution_observed,
            hosted_tool_item_count=max(0, int(hosted_tool_item_count)),
            hosted_tool_item_ids=tuple(
                str(item_id or '')[:160] for item_id in hosted_tool_item_ids[:16]
            ),
            hosted_tool_item_types=tuple(
                str(item_type or '')[:80]
                for item_type in hosted_tool_item_types[:16]
            ),
            hosted_tool_statuses=tuple(
                str(status or '')[:80] for status in hosted_tool_statuses[:16]
            ),
            payload_sha256=hashlib.sha256(
                error_message.encode('utf-8', errors='replace')
            ).hexdigest(),
        )
    )


def retryable_malformed_response(
    *,
    failure_kind: str = "malformed_response",
    error_message: str = "",
) -> ProviderSSEFailure:
    """Describe a malformed provider response while preserving its retry route."""

    return ProviderSSEFailure(
        SSEFailureDiagnostics(
            failure_class="transient_provider_failure",
            failure_kind=failure_kind,
            retryable=True,
            semantic_disposition="retryable_transport",
            failure_reason="malformed_response",
            failure_origin="provider_response",
            classification_basis="response_shape:" + failure_kind,
            recovery_disposition="delayed_retry_candidate",
            error_message=diagnostic_message(error_message),
            payload_sha256=hashlib.sha256(
                str(error_message).encode("utf-8", errors="replace")
            ).hexdigest(),
        )
    )


def provider_http_failure(
    *,
    classification: ProviderHTTPErrorClassification,
    detail: str,
) -> ProviderSSEFailure:
    bounded_detail = str(detail or '')[:400]
    error_payload = classification.error_payload
    return ProviderSSEFailure(
        SSEFailureDiagnostics(
            failure_class=classification.failure_class,
            failure_reason=classification.failure_reason,
            failure_origin=classification.failure_origin,
            classification_basis=classification.classification_basis,
            recovery_disposition=classification.recovery_disposition,
            failure_kind=classification.failure_kind,
            retryable=classification.retryable,
            semantic_disposition=classification.semantic_disposition,
            retry_kind=classification.retry_kind,
            retry_owner=classification.retry_owner,
            http_status=classification.status_code,
            retry_after_seconds=classification.retry_after_seconds,
            error_code=str(error_payload.get('code') or ''),
            provider_error_code=str(error_payload.get('code') or ''),
            provider_request_id=str(error_payload.get('request_id') or ''),
            error_type=str(error_payload.get('type') or 'http_status')[:160],
            error_message=str(error_payload.get('message') or bounded_detail)[:400],
            event_bytes=len(str(detail or '').encode('utf-8', errors='replace')),
            payload_sha256=hashlib.sha256(
                str(detail or '').encode('utf-8', errors='replace')
            ).hexdigest(),
        )
    )


def provider_transport_failure(
    *,
    phase: str,
    kind: str,
    detail: str,
    retryable: bool,
    exception: BaseException | None = None,
) -> ProviderSSEFailure:
    bounded_detail = str(detail or '')[:400]
    transport = classify_transport_error(exception) if exception is not None else None
    if transport is not None:
        retryable = transport.retriable
    local_processing = transport is not None and transport.failure_reason == 'local_processing_error'
    return ProviderSSEFailure(
        SSEFailureDiagnostics(
            failure_class=(
                'local_runtime_failure'
                if local_processing
                else 'transient_provider_failure'
                if retryable
                else 'provider_protocol_failure'
            ),
            failure_kind='local_processing_error' if local_processing else 'provider_transport_error',
            failure_reason=transport.failure_reason if transport else ('connection_interrupted' if retryable else 'unknown'),
            failure_origin=transport.failure_origin if transport else 'transport',
            classification_basis='exception_type:' + kind,
            transport_phase=phase,
            retryable=retryable,
            retry_owner='inner_transport' if retryable else None,
            semantic_disposition=(
                'local_processing_error' if local_processing
                else 'retryable_transport' if retryable else 'protocol_invalid'
            ),
            error_code=str(phase or 'unknown')[:160],
            error_type=str(kind or 'unknown')[:160],
            error_message=bounded_detail,
            event_bytes=len(str(detail or '').encode('utf-8', errors='replace')),
            payload_sha256=hashlib.sha256(
                str(detail or '').encode('utf-8', errors='replace')
            ).hexdigest(),
        )
    )


def normalize_failure_exception(error: BaseException) -> BaseException:
    if callable(getattr(error, 'to_trace_metadata', None)):
        return error
    if isinstance(error, ProviderContextOverflowError):
        return ProviderSSEFailure(SSEFailureDiagnostics(
            failure_class='deterministic_provider_input_failure',
            failure_kind='context_overflow', retryable=False,
            failure_reason='context_limit', classification_basis='typed_context_overflow',
            semantic_disposition='deterministic_input', error_message=str(error),
        ))
    transport = classify_transport_error(error)
    return provider_transport_failure(
        phase=transport.phase, kind=transport.kind, detail=str(error),
        retryable=transport.retriable, exception=error,
    )


def _failure_message(diagnostics: SSEFailureDiagnostics) -> str:
    parts = [
        f'SSE {diagnostics.failure_kind}',
        f'class={diagnostics.failure_class}',
        f'retryable={str(diagnostics.retryable).lower()}',
        f'events={diagnostics.event_count}',
    ]
    if diagnostics.terminal_event:
        parts.append(f'terminal={diagnostics.terminal_event}')
    if diagnostics.response_id:
        parts.append(f'response_id={diagnostics.response_id}')
    if diagnostics.incomplete_reason:
        parts.append(f'reason={diagnostics.incomplete_reason}')
    if diagnostics.error_code:
        parts.append(f'error_code={diagnostics.error_code}')
    if diagnostics.http_status is not None:
        parts.append(f'HTTP {diagnostics.http_status}')
    if diagnostics.error_message:
        parts.append(f'message={diagnostics.error_message}')
    return '; '.join(parts)


def _payload_contains_partial_output(
    event_name: str,
    payload: dict[str, Any],
) -> bool:
    if event_name == 'chat.completion.chunk':
        return bool(payload.get('choices'))
    if event_name == 'response.output_text.delta':
        return bool(payload.get('delta'))
    if event_name == 'response.output_text.done':
        return bool(payload.get('text'))
    if event_name == 'content_block_delta':
        return bool(payload.get('delta'))
    return event_name in {
        'content_block_start',
        'content_block_stop',
        'response.output_item.added',
        'response.output_item.done',
        'response.content_part.added',
        'response.content_part.done',
    }
