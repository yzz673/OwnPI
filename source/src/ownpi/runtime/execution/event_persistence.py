"""Transient B3 retry ownership. Only storage is retried, never external work."""

from __future__ import annotations

from dataclasses import dataclass, replace
import logging
from typing import Any, Callable
from uuid import uuid4

from ownpi.runtime.execution.board import ExecutionBoardTargetError
from ownpi.runtime.execution.registry import ExecutionRegistry
from ownpi.runtime.execution.state import ExecutionObservation, ExecutionRecord
from ownpi.runtime.native_tools.provider_json import FrozenProviderJson
from ownpi.runtime.process_owner.bindings import ProcessOwnerBinding
from ownpi.runtime.rollout.execution_events import (
    execution_state_evidence, execution_transition_facts, project_execution_events,
)
from ownpi.runtime.rollout.run_events import RunEventStore
from ownpi.runtime.rollout.process_owner_events import process_owner_binding_fact


@dataclass(frozen=True)
class PendingExecutionEvent:
    kind: str
    event_id: str
    diagnostic_id: str
    data: FrozenProviderJson


class ExecutionEventPersistence:
    def __init__(self, store: Callable[[], RunEventStore | None]) -> None:
        self._store = store
        self._pending: list[PendingExecutionEvent] = []
        self._records: dict[str, ExecutionRecord] = {}
        self.owner_bindings: dict[str, ProcessOwnerBinding] = {}
        self._evidence: dict[str, dict[str, Any]] = {}
        self._evidence_observed_at: dict[str, str] = {}
        self._loaded = False
        self.diagnostic = ''

    @property
    def enabled(self) -> bool:
        return self._store() is not None

    @property
    def pending(self) -> tuple[PendingExecutionEvent, ...]:
        return tuple(self._pending)

    def launch_registry(self) -> ExecutionRegistry:
        self._load()
        return ExecutionRegistry(tuple(self._records.values()))

    def _load(self) -> None:
        store = self._store()
        if self._loaded or store is None:
            return
        projection = project_execution_events(store.events)
        self._records = {record.execution_id: record for record in projection.registry.records}
        self._loaded = True

    def record_transition(
        self, record: ExecutionRecord, observation: ExecutionObservation,
    ) -> None:
        if not self.enabled:
            return
        self._load()
        evidence_at = self._evidence_observed_at.get(record.execution_id, '')
        if ((observation.observed_at and observation.observed_at < record.updated_at)
                or (record.terminal and not observation.terminal)
                or (observation.observed_at and observation.observed_at < evidence_at)):
            # Registry no-ops can be rejected observations, not fresh evidence.
            observation = replace(
                observation, evidence={}, observed_at=max(record.updated_at, evidence_at),
            )
        previous = self._records.get(record.execution_id)
        for kind, data in execution_transition_facts(previous, record, observation):
            self.enqueue(kind, data)
        self._records[record.execution_id] = record
        self._evidence[record.execution_id] = {
            **self._evidence.get(record.execution_id, {}), **execution_state_evidence(observation),
        }
        if execution_state_evidence(observation):
            self._evidence_observed_at[record.execution_id] = max(
                evidence_at, observation.observed_at,
            )
        self.flush()

    def enqueue(self, kind: str, data: dict[str, Any]) -> None:
        self._pending.append(PendingExecutionEvent(
            kind, f'execution:{uuid4().hex}', data['execution_id'],
            FrozenProviderJson.from_value(data),
        ))

    def record_owner_binding(self, binding: ProcessOwnerBinding) -> None:
        if binding.transport_kind not in {'remote_ssh_unix', 'local_unix'} or not self.enabled:
            self.owner_bindings[binding.owner_id] = binding
            return
        identity, data = process_owner_binding_fact(binding)
        value = FrozenProviderJson.from_value(data)
        pending = next((fact for fact in self._pending if fact.event_id == identity), None)
        store = self._store()
        recorded = next((event for event in store.events if event.event_id == identity), None) if store else None
        for previous in (pending, recorded):
            if previous is not None and (previous.kind != 'process_owner_bound' or previous.data != value):
                raise ValueError(f'Conflicting business event: {identity}')
        if pending is None and (recorded is None or store.closed):
            self._pending.append(PendingExecutionEvent(
                'process_owner_bound', identity, binding.owner_id, value,
            ))
        self.owner_bindings[binding.owner_id] = binding
        self.flush()

    def flush(self) -> bool:
        store = self._store()
        if store is None:
            return not self._pending
        while self._pending:
            fact = self._pending[0]
            try:
                store.emit(fact.kind, fact.event_id, fact.data.to_object())
            except OSError:
                self.diagnostic = f'Execution persistence unconfirmed: {fact.diagnostic_id}'
                logging.getLogger(__name__).warning(self.diagnostic)
                return False
            self._pending.pop(0)
        self.diagnostic = ''
        return True


class ExecutionPersistenceMixin:
    _lock: Any
    _state_store: Any
    _projector: Any
    _events: ExecutionEventPersistence
    _board_update_pending: bool
    def record_owner_binding(self, binding: ProcessOwnerBinding) -> None:
        """Accept an established manager binding, including its actual session ID."""
        with self._lock:
            self._events.record_owner_binding(binding)

    def confirm_release_evidence(
        self, observation: ExecutionObservation, *, final_seq: int,
    ) -> bool:
        """Pre-release handoff for callers that already hold the closed response."""
        with self._lock:
            if not self._events.enabled:
                return True
            if not observation.terminal or final_seq < 0 or observation.evidence.get('closed') is not True:
                return False
            match = self._state_store.state.execution_registry.matching_record(observation)
            execution_id = match.execution_id if match is not None else observation.execution_id
            if not self._release_coordinator.reserve_external(
                execution_id, owner_id=observation.handle.owner_id,
                process_id=observation.handle.process_id, final_seq=final_seq,
            ):
                return False
            committed = False
            try:
                self._commit_locked(observation)
                committed = self._events.flush()
                return committed
            finally:
                if not committed:
                    self._release_coordinator.cancel_external(execution_id)

    def cancel_external_release(self, execution_id: str) -> None:
        with self._lock:
            self._release_coordinator.cancel_external(execution_id)

    def record_external_release(
        self, execution_id: str, *, final_seq: int,
    ) -> bool:
        """Record a successful external release without invoking it again."""
        with self._lock:
            record = self._state_store.state.execution_registry.get(execution_id)
            if record is None:
                return False
            self._release_coordinator.complete_external(execution_id)
            return True

    @property
    def persistence_diagnostic(self) -> str:
        with self._lock:
            return self._events.diagnostic

    def flush_pending_events(self) -> bool:
        with self._lock:
            return self._events.flush()

    def _board_registry_locked(self) -> ExecutionRegistry:
        live = self._state_store.state.execution_registry
        if not self._events.enabled:
            return live
        records = {item.execution_id: item for item in self._events.launch_registry().records}
        return ExecutionRegistry(tuple(records.values()), live.material_revision)

    def _mark_board_pending_locked(self) -> None:
        self._board_update_pending = True

    def flush_pending_board_update(self) -> None:
        with self._lock:
            enabled = self._events.enabled
            if not self._board_update_pending:
                return
            registry = self._board_registry_locked()
            try:
                # current_ref checks symlink/hard-link safety before reading.
                ref = self._projector.current_ref(registry)
                if not (enabled and ref is not None and self._projector.is_canonical(
                    registry, self._projector.path.read_bytes(),
                )):
                    self._projector.record_new_execution(registry)
            except (OSError, ExecutionBoardTargetError):
                logging.getLogger(__name__).warning(
                    'Execution board update deferred: path=%s', self._projector.path, exc_info=True,
                )
            else:
                self._board_update_pending = False
