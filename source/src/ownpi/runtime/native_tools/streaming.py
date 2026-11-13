from __future__ import annotations

from collections.abc import Iterable, Iterator
from copy import deepcopy
from dataclasses import replace
from typing import Any
from uuid import uuid4

from ownpi.llm.streaming import (
    CanonicalResponseEvent,
    CanonicalResponseEventType,
)
from ownpi.runtime.native_tools.provider import (
    FrozenProviderJson,
    ProviderOutputAttempt,
    ProviderOutputCompletion,
    ProviderOutputEnvelope,
    ProviderOutputItem,
    ProviderOutputLifecycleRecord,
    ProviderOutputSource,
    ProviderOutputSourceKind,
)
from ownpi.runtime.native_tools.provider_output_attempt import decode_prior_attempts
from ownpi.runtime.native_tools.provider_output_failure import (
    ProviderOutputFailureFacts,
    preserve_attached_failure_facts,
)
from ownpi.llm.sse_failure import empty_sse_failure


class CanonicalNativeStreamAccumulator:
    def __init__(self) -> None:
        self._attempt = 0
        self._attempt_discarded = False
        self._prior_attempts: list[ProviderOutputAttempt] = []
        self._source_prior_attempts: tuple[ProviderOutputAttempt, ...] = ()
        self._items: list[ProviderOutputItem] = []
        self._usage: dict[str, int] = {}
        self._provider_metadata: dict[str, Any] = {}
        self._lifecycle: list[ProviderOutputLifecycleRecord] = []
        self._response_id = ''
        self._exchange_id = f'nex-{uuid4().hex}'
        self._stop_reason = ''
        self._status = ''
        self._provider_protocol = ''
        self._source_kind = ProviderOutputSourceKind.STREAMING
        self._source_response_payload: FrozenProviderJson | None = None
        self._original_source: ProviderOutputSource | None = None
        self._completed = False

    def observe(self, event: CanonicalResponseEvent) -> None:
        if self._completed:
            return
        event_type = event.type_value
        if event_type == CanonicalResponseEventType.RESPONSE_STARTED.value:
            if event.attempt != self._attempt and not self._attempt_discarded:
                self._reset_attempt()
            self._attempt = event.attempt
            self._attempt_discarded = False
            self._capture_source(event)
            self._record_lifecycle(event)
        elif event_type == CanonicalResponseEventType.ITEM_STARTED.value:
            self._record_lifecycle(event)
        elif event_type == CanonicalResponseEventType.ITEM_COMPLETED.value:
            self._attempt = event.attempt
            self._admit_completed_item(event)
        elif event_type == CanonicalResponseEventType.USAGE_UPDATED.value:
            self._usage.update({str(key): int(value) for key, value in event.usage.items()})
        elif event_type == CanonicalResponseEventType.ATTEMPT_DISCARDED.value:
            self._record_lifecycle(event)
            if not self._attempt_discarded:
                self._reset_attempt()
            self._attempt_discarded = True
        elif event_type == CanonicalResponseEventType.STREAM_ERROR.value:
            self._record_lifecycle(event)
        elif event_type == CanonicalResponseEventType.RESPONSE_COMPLETED.value:
            self._capture_source(event)
            self._usage.update({str(key): int(value) for key, value in event.usage.items()})
            self._provider_metadata.update(deepcopy(dict(event.provider_metadata)))
            self._response_id = event.response_id
            self._stop_reason = event.stop_reason
            self._status = event.response_status or str(event.provider_metadata.get('status') or '')
            self._provider_protocol = event.provider_protocol
            self._record_lifecycle(event)
            self._completed = True

    def finalize(self) -> ProviderOutputEnvelope:
        if not self._completed:
            raise empty_sse_failure()
        items = tuple(self._items)
        return ProviderOutputEnvelope(
            completed_items=items,
            exchange_id=self._exchange_id,
            completion=ProviderOutputCompletion.create(
                response_id=self._response_id,
                status=self._status,
                stop_reason=self._stop_reason,
                usage=self._usage,
                provider_metadata=self._provider_metadata,
                provider_protocol=self._provider_protocol,
            ),
            source=self._original_source or ProviderOutputSource(
                kind=self._source_kind,
                response_payload=self._source_response_payload,
                completed_item_payloads=tuple(item.provider_payload for item in items),
            ),
            lifecycle=tuple(self._lifecycle),
            prior_attempts=(*self._prior_attempts, *self._source_prior_attempts),
        )

    def failure_facts(self, error: BaseException) -> ProviderOutputFailureFacts:
        last = self._lifecycle[-1] if self._lifecycle else None
        failure = ProviderOutputLifecycleRecord(
            event_type=CanonicalResponseEventType.STREAM_ERROR.value,
            attempt=last.attempt if last is not None else 1,
            response_id=last.response_id if last is not None else '',
            provider_protocol=last.provider_protocol if last is not None else '',
            payload=FrozenProviderJson.from_value({
                'exception_type': type(error).__name__, 'message': str(error),
            }),
        )
        observed = ProviderOutputFailureFacts(
            exchange_id=self._exchange_id,
            completed_items=tuple(self._items),
            lifecycle=(*self._lifecycle, failure),
            parser_source=getattr(error, 'provider_output_source', None),
            prior_attempts=(*self._prior_attempts, *self._source_prior_attempts),
        )
        return preserve_attached_failure_facts(observed, error, attempt=self._attempt)

    def _reset_attempt(self) -> None:
        self._prior_attempts.extend(self._source_prior_attempts)
        self._source_prior_attempts = ()
        if self._items:
            self._prior_attempts.append(ProviderOutputAttempt(
                exchange_id=self._exchange_id,
                attempt=self._attempt,
                completed_items=tuple(self._items),
            ))
        self._items.clear()
        self._usage.clear()
        self._provider_metadata.clear()
        self._response_id = ''
        self._exchange_id = f'nex-{uuid4().hex}'
        self._stop_reason = ''
        self._status = ''
        self._provider_protocol = ''
        self._source_kind = ProviderOutputSourceKind.STREAMING
        self._source_response_payload = None
        self._original_source = None
        self._completed = False

    def _admit_completed_item(self, event: CanonicalResponseEvent) -> None:
        item = event.completed_item
        if item is None:
            raise TypeError('item_completed requires a ProviderOutputItem')
        item = replace(item, sequence_index=len(self._items))
        self._record_lifecycle(event)
        self._items.append(item)

    def _record_lifecycle(self, event: CanonicalResponseEvent) -> None:
        self._lifecycle.append(
            ProviderOutputLifecycleRecord(
                event_type=event.type_value,
                attempt=event.attempt,
                response_id=event.response_id,
                item_id=event.item_id,
                item_type=event.item_type,
                sequence_index=event.sequence_index,
                provider_protocol=event.provider_protocol,
                payload=FrozenProviderJson.from_value(dict(event.payload)),
            )
        )

    def _capture_source(self, event: CanonicalResponseEvent) -> None:
        if 'prior_attempts' in event.payload:
            self._source_prior_attempts = decode_prior_attempts(event.payload['prior_attempts'])
        if 'source' in event.payload:
            self._original_source = ProviderOutputSource.from_dict(event.payload['source'])
        source_kind = str(event.payload.get('source_kind') or '')
        if source_kind:
            self._source_kind = ProviderOutputSourceKind(source_kind)
        exchange_id = event.payload.get('exchange_id')
        if exchange_id:
            self._exchange_id = str(exchange_id)
        original_lifecycle = event.payload.get('source_lifecycle')
        if isinstance(original_lifecycle, (list, tuple)):
            self._lifecycle.extend(ProviderOutputLifecycleRecord.from_dict(record)
                                   for record in original_lifecycle)
        response_payload = event.payload.get('response_payload')
        if response_payload is not None:
            self._source_response_payload = FrozenProviderJson.from_value(response_payload)


def blocking_native_response_events(
    response: ProviderOutputEnvelope,
    *,
    request_kind: str,
    provider_protocol: str = 'blocking',
    attempt: int = 1,
) -> Iterator[CanonicalResponseEvent]:
    response_id = response.completion.response_id
    provider_protocol = response.completion.provider_protocol or provider_protocol
    response_payload = (
        response.source.response_payload.to_value()
        if response.source.response_payload is not None
        else None
    )
    yield CanonicalResponseEvent(
        event_type=CanonicalResponseEventType.RESPONSE_STARTED,
        request_kind=request_kind,
        response_mode='native_tools',
        response_id=response_id,
        attempt=attempt,
        provider_protocol=provider_protocol,
        payload={
            'source_kind': response.source.kind.value,
            'source': response.source.to_dict(),
            'response_payload': response_payload,
            'exchange_id': response.exchange_id,
            'source_lifecycle': tuple(record.to_dict() for record in response.lifecycle),
            'prior_attempts': tuple(batch.to_dict() for batch in response.prior_attempts),
        },
    )
    for item in response.completed_items:
        item_id = f'item-{item.sequence_index}'
        yield CanonicalResponseEvent(
            event_type=CanonicalResponseEventType.ITEM_STARTED,
            request_kind=request_kind,
            response_mode='native_tools',
            response_id=response_id,
            item_id=item_id,
            item_type=item.kind.value,
            sequence_index=item.sequence_index,
            attempt=attempt,
            provider_protocol=provider_protocol,
        )
        yield CanonicalResponseEvent(
            event_type=CanonicalResponseEventType.ITEM_COMPLETED,
            request_kind=request_kind,
            response_mode='native_tools',
            response_id=response_id,
            item_id=item_id,
            item_type=item.kind.value,
            sequence_index=item.sequence_index,
            completed_item=item,
            attempt=attempt,
            provider_protocol=provider_protocol,
        )
    if response.usage:
        yield CanonicalResponseEvent(
            event_type=CanonicalResponseEventType.USAGE_UPDATED,
            request_kind=request_kind,
            response_mode='native_tools',
            response_id=response_id,
            usage=response.usage,
            attempt=attempt,
            provider_protocol=provider_protocol,
        )
    yield CanonicalResponseEvent(
        event_type=CanonicalResponseEventType.RESPONSE_COMPLETED,
        request_kind=request_kind,
        response_mode='native_tools',
        response_id=response_id,
        usage=response.usage,
        attempt=attempt,
        stop_reason=response.completion.stop_reason,
        response_status=response.completion.status,
        provider_protocol=response.completion.provider_protocol,
        provider_metadata=response.completion.provider_metadata.to_object(),
    )


def aggregate_native_response_events(
    events: Iterable[CanonicalResponseEvent],
) -> ProviderOutputEnvelope:
    accumulator = CanonicalNativeStreamAccumulator()
    try:
        for event in events:
            accumulator.observe(event)
            if event.type_value == CanonicalResponseEventType.RESPONSE_COMPLETED.value:
                break
        return accumulator.finalize()
    except BaseException as error:
        setattr(error, 'provider_output_failure', accumulator.failure_facts(error))
        raise


__all__ = [
    'CanonicalNativeStreamAccumulator',
    'aggregate_native_response_events',
    'blocking_native_response_events',
]
