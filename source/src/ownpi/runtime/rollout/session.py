"""Session-owned fan-out into live history and the canonical rollout."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import replace
import logging
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any, Iterator
from uuid import uuid4
import weakref

from ownpi.runtime.limits.tool_output import TruncationPolicy
from ownpi.runtime.native_tools.history import NativeHistory
from ownpi.runtime.native_tools.history_items import HistoryEntry, HistoryMessage
from ownpi.runtime.native_tools.provider import ProviderOutputEnvelope, ProviderOutputItem, ProviderOutputItemKind
from ownpi.runtime.native_tools.provider_json import FrozenProviderJson
from ownpi.runtime.native_tools.provider_output_failure import ProviderOutputFailureFacts
from ownpi.runtime.native_tools.provider_output_attempt import iter_history_batches
from ownpi.runtime.rollout.codec import RolloutRecord, compacted_record, encode_entry
from ownpi.runtime.rollout.recorder import RolloutRecorder
from ownpi.runtime.rollout.resources import RecorderCloseOwner
from ownpi.runtime.rollout.resume_read_session import ConversationMetadata, file_version

if TYPE_CHECKING:
    from ownpi.runtime.native_tools.provider_input import NativeHistoryProviderInput
    from ownpi.runtime.rollout.resume import ResumedConversation


class RolloutHistory:
    """Own a pure NativeHistory; never persist a filtered/truncated snapshot as input."""

    def __init__(self, *, directory: Path, policy: TruncationPolicy,
                 metadata: dict[str, Any], prefix: list[dict[str, Any]],
                 conversation_id: str | None = None) -> None:
        self.conversation_id = conversation_id or uuid4().hex
        self.live = NativeHistory(policy)
        self._lock = RLock()
        self._entry_ids: list[tuple[HistoryEntry, str]] = []
        self._entry_index: dict[int, tuple[HistoryEntry, str]] = {}
        self._call_index: dict[tuple[str, str], list[HistoryEntry]] = {}
        self._source_item_ids: set[str] = set()
        self._source_contexts: set[FrozenProviderJson] = set()
        self._source_exchanges: set[str] = set()
        self._usage_exchanges: set[str] = set()
        self._turn_id = ''
        self.diagnostic = ''
        self._closed = False
        meta = RolloutRecord.create('session_meta', {
            **metadata, 'id': self.conversation_id, 'instructions': prefix,
        })
        self.recorder = RolloutRecorder(directory / f'{self.conversation_id}.jsonl', meta)
        self._close_owner = RecorderCloseOwner(self.recorder)
        self._finalizer = weakref.finalize(self, self._close_owner.finalize)

    @classmethod
    def resume(cls, replay: ResumedConversation | ConversationMetadata, *, policy: TruncationPolicy) -> RolloutHistory:
        self = cls.__new__(cls)
        recorder = RolloutRecorder(replay.path, None)
        try:
            if isinstance(replay, ConversationMetadata) or (
                    replay.read_session is not None and file_version(replay.path) != replay.file_version):
                current = replay.read_session.replay(replay.path, conversation_id=replay.conversation_id)
                current.check_identity(replay.metadata)
                replay = current
            self.live, self._entry_ids = replay.build_history(policy)
        except BaseException as error:
            try:
                recorder.shutdown()
            except BaseException as close_error:
                error.add_note(f'Resume recorder shutdown failed: {close_error!r}')
            if replay.read_session is not None:
                replay.read_session.release_replay(replay.path)
            raise
        self.conversation_id = replay.conversation_id
        self._entry_index = {}
        self._call_index = {}
        self._rebuild_source_index()
        self._source_item_ids = set(replay.original_item_ids)
        self._source_contexts = set(replay.source_contexts)
        self._source_exchanges = set(replay.usage_exchanges)
        self._lock = RLock()
        self._usage_exchanges = set(replay.usage_exchanges)
        self._turn_id = replay.turn_id
        self.diagnostic = ''
        self._closed = False
        self.recorder = recorder
        if replay.read_session is not None:
            replay.read_session.release_replay(replay.path)
        self._close_owner = RecorderCloseOwner(self.recorder)
        self._finalizer = weakref.finalize(self, self._close_owner.finalize)
        return self

    @property
    def policy(self) -> TruncationPolicy:
        return self.live.policy

    @property
    def snapshot(self) -> tuple[HistoryEntry, ...]:
        return self.live.snapshot

    @property
    def generation(self) -> int:
        return self.live.generation

    def for_prompt(self, input_modalities: Iterable[str] = ('text', 'image', 'audio')) -> tuple[HistoryEntry, ...]:
        return self.live.for_prompt(input_modalities)

    def protected_suffix(self) -> tuple[HistoryEntry, ...]:
        return self.live.protected_suffix()

    @property
    def consumed_outputs(self) -> tuple[HistoryEntry, ...]:
        return self.live.consumed_outputs

    def record_items(self, entries: Iterable[HistoryEntry]) -> tuple[HistoryEntry, ...]:
        with self._lock:
            self._ensure_open()
            prepared = tuple(self._stamp(entry) for entry in entries)
            recorded = []
            records = []
            for original in prepared:
                retained = self.live.record_items((original,))
                identity = uuid4().hex
                records.append(encode_entry(original, item_id=identity))
                for value in retained:
                    self._entry_ids.append((value, identity))
                    self._index_entry(value, identity)
                recorded.extend(retained)
            self._append(tuple(records))
            return tuple(recorded)

    def _stamp(self, entry: HistoryEntry) -> HistoryEntry:
        return replace(entry, turn_id=self._turn_id) if self._turn_id and not entry.turn_id else entry

    def _index_entry(self, entry: HistoryEntry, identity: str) -> None:
        self._entry_index.setdefault(id(entry), (entry, identity))
        item = entry.item
        if isinstance(item, ProviderOutputItem) and item.kind is ProviderOutputItemKind.TOOL_CALL:
            for call_id in {item.call_id, item.request_id} - {''}:
                for tool_name in {item.tool_name, ''}:
                    self._call_index.setdefault((call_id, tool_name), []).append(entry)

    def _rebuild_source_index(self) -> None:
        self._entry_index.clear()
        self._call_index.clear()
        for entry, identity in self._entry_ids:
            self._index_entry(entry, identity)

    def _occurrence_ids(self, entries: Iterable[HistoryEntry]) -> list[str]:
        available: dict[int, deque[str]] = defaultdict(deque)
        for entry, identity in self._entry_ids:
            available[id(entry)].append(identity)
        return [available[id(entry)].popleft() if available[id(entry)] else uuid4().hex
                for entry in entries]

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError('Cannot record into a closed history session')

    def record_provider(self, response: ProviderOutputEnvelope | ProviderOutputFailureFacts) -> tuple[HistoryEntry, ...]:
        return self.record_items(HistoryEntry(item, batch.exchange_id, item.sequence_index)
                                 for batch in iter_history_batches(response) for item in batch.completed_items)

    def record_message(self, role: str, text: str, *, exchange_id: str = '') -> None:
        self.record_items((HistoryEntry(HistoryMessage(role, text), exchange_id),))

    def replace(self, entries: Iterable[HistoryEntry]) -> None:
        with self._lock:
            self._ensure_open()
            replacement = tuple(self._stamp(entry) for entry in entries)
            identities = self._occurrence_ids(replacement)
            self.live.replace(replacement)
            self._append((compacted_record(tuple(encode_entry(entry, item_id=identity)
                                                 for entry, identity in zip(replacement, identities, strict=True))),))
            self._entry_ids = list(zip(replacement, identities, strict=True))
            self._rebuild_source_index()
            if self.live.reference_context is not None:
                baseline = self.live.reference_context.to_object()
                baseline.update(turn_id=self._turn_id, history_generation=self.generation)
                self._append((RolloutRecord.create('turn_context', baseline),))
                self.live.reference_context = FrozenProviderJson.from_value(baseline)

    def mark_request_consumed(self, entries: Iterable[HistoryEntry]) -> None:
        with self._lock:
            self._ensure_open()
            self.live.mark_request_consumed(entries)
            ids = self._occurrence_ids(self.live.consumed_outputs)
            if ids:
                self.event('protected_output_consumed', {'item_ids': ids})

    def prepare_context(self, request: NativeHistoryProviderInput, *, model: dict[str, Any],
                        tools: list[dict[str, Any]], effective_permissions: dict[str, Any] | None = None) -> None:
        with self._lock:
            current = FrozenProviderJson.from_value({
                'turn_id': self._turn_id, 'model': model, 'tools': tools,
                'history_generation': request.history_generation,
                'effective_permissions': effective_permissions,
            })
            if current != self.live.reference_context:
                self._append((RolloutRecord.create('turn_context', current.to_object()),))
                self.live.reference_context = current

    def record_usage(self, response: ProviderOutputEnvelope, *, purpose: str = 'normal') -> None:
        if response.exchange_id in self._usage_exchanges:
            return
        self._usage_exchanges.add(response.exchange_id)
        completion = response.completion
        self.event('token_count', {
            'exchange_id': response.exchange_id, 'purpose': purpose,
            'response_id': completion.response_id, 'status': completion.status,
            'stop_reason': completion.stop_reason, 'usage': completion.usage,
            'exact_usage': completion.exact_usage, 'provider_protocol': completion.provider_protocol,
        })

    def record_failure(self, error: BaseException) -> None:
        facts = getattr(error, 'provider_output_failure', None)
        if isinstance(facts, ProviderOutputFailureFacts):
            self.record_provider(facts)

    def event(self, kind: str, payload: dict[str, Any]) -> None:
        if kind in {'turn_started', 'turn_complete', 'turn_aborted'}:
            self.live.turn_status = {'turn_started': 'interrupted', 'turn_complete': 'completed',
                                     'turn_aborted': 'aborted'}[kind]
        elif kind == 'token_count' and payload.get('purpose', 'normal') == 'normal':
            self.live.token_info = FrozenProviderJson.from_value(payload)
        self._append((RolloutRecord.create('event_msg', {
            **payload, 'type': kind, 'turn_id': self._turn_id,
        }),))

    def communication(self, *, source: str, target: str, text: str = '', kind: str) -> None:
        self._append((RolloutRecord.create('inter_agent_communication', {
            'source': source, 'target': target, 'text': text, 'kind': kind,
        }),))

    def _append(self, records: tuple[RolloutRecord, ...]) -> None:
        with self._lock:
            self._ensure_open()
            try:
                accepted = self.recorder.record_items(records)
                self._accept_sources(accepted)
                self.recorder.flush()
                self.diagnostic = ''
            except OSError as error:
                self.diagnostic = str(error)
                logging.getLogger(__name__).warning('Rollout persistence unconfirmed: %s', str(error))

    def _accept_sources(self, records: tuple[RolloutRecord, ...]) -> None:
        # Queue acceptance precedes flush; a later sync may finish a failed write.
        for record in records:
            if record.type == 'response_item':
                identity = record.metadata.to_object().get('item_id')
                if identity:
                    self._source_item_ids.add(identity)
            elif record.type == 'turn_context':
                self._source_contexts.add(record.payload)
            elif record.type == 'event_msg':
                payload = record.payload.to_object()
                if payload.get('type') == 'token_count' and isinstance(payload.get('exchange_id'), str):
                    self._source_exchanges.add(payload['exchange_id'])

    def flush(self) -> bool:
        try:
            self.recorder.flush()
            self.diagnostic = ''
            return True
        except OSError as error:
            self.diagnostic = str(error)
            logging.getLogger(__name__).warning('Rollout flush unconfirmed: %s', str(error))
            return False

    def confirmed_reference(self, *, entry: HistoryEntry | None = None,
                            exchange_id: str = '') -> dict[str, Any] | None:
        """Confirm a B4 source without changing ordinary history error handling."""
        with self._lock:
            indexed = self._entry_index.get(id(entry)) if entry is not None else None
            identity = indexed[1] if indexed is not None else None
            if entry is not None:
                if identity not in self._source_item_ids:
                    return None
            elif (not exchange_id or self.live.reference_context not in self._source_contexts
                  or exchange_id not in self._source_exchanges):
                return None
            try:
                self.recorder.sync()
            except OSError:
                logging.getLogger(__name__).warning('History delivery unconfirmed', exc_info=True)
                return None
            return {'conversation_id': self.conversation_id, 'item_id': identity,
                    'turn_id': self._turn_id,
                    'exchange_id': exchange_id or (entry.exchange_id if entry else ''),
                    'path': str(self.recorder.path)}

    def confirmed_call_source(self, call_id: str, *, run_dir: Path,
                              tool_name: str = '') -> dict[str, str]:
        """Confirm an original call before a business writer stores its reference."""
        with self._lock:
            matches = self._call_index.get((call_id, tool_name), ())
            if len(matches) != 1:
                raise ValueError('Business source must identify exactly one original tool call')
            reference = self.confirmed_reference(entry=matches[0])
            if reference is None:
                raise OSError('Business source call persistence is unconfirmed')
            return {'path': str(self.recorder.path.resolve().relative_to(run_dir.resolve())),
                    'item_id': reference['item_id']}

    def close(self) -> bool:
        with self._lock:
            return self._close()

    def _close(self) -> bool:
        if self._close_owner.close():
            self._closed = True
            self._finalizer.detach()
            self.diagnostic = ''
            return True
        self.diagnostic = self._close_owner.diagnostic
        return False

    @contextmanager
    def turn(self, *, close: bool = True) -> Iterator[dict[str, str]]:
        self._turn_id = uuid4().hex
        outcome = {'status': 'interrupted', 'reason': ''}
        primary: BaseException | None = None
        try:
            self.event('turn_started', {})
            yield outcome
        except BaseException as error:
            primary = error
            outcome.update(status='interrupted', reason=type(error).__name__)
            raise
        finally:
            try:
                try:
                    self.event('turn_complete' if outcome['status'] == 'completed' else 'turn_aborted', outcome)
                finally:
                    if close:
                        self.close()
                    else:
                        self.flush()
            except Exception as recording_error:
                if primary is None:
                    raise
                primary.add_note(f'Rollout finalization failed: {recording_error!r}')
                logging.getLogger(__name__).exception('Rollout finalization failed during an existing exit')


class RecordingTurnRunner:
    def __init__(self, delegate: Any, history: RolloutHistory) -> None:
        self.delegate, self.history = delegate, history

    def invoke(self, *, prepared_turn: object, tool_choice: str) -> ProviderOutputEnvelope:
        try:
            return self.delegate.invoke(prepared_turn=prepared_turn, tool_choice=tool_choice)
        except BaseException as error:
            try:
                self.history.record_failure(error)
            except Exception as recording_error:
                error.add_note(f'Failure facts could not be recorded: {recording_error!r}')
                logging.getLogger(__name__).exception('Failure fact recording failed')
            raise
