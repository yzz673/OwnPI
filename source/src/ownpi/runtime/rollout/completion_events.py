"""The committed child result is a run fact, not a subagent JSON snapshot."""

from __future__ import annotations

from dataclasses import replace
from threading import RLock

from ownpi.runtime.rollout.run_events import RunEventStore
from ownpi.runtime.rollout.call_source import validate_call_source
from ownpi.subagent.state import ChildState
from ownpi.runtime.rollout.resume_read_session import ResumeReadSession


class CompletionEvents:
    def __init__(self, store: RunEventStore) -> None:
        self.store = store
        self._registered: dict[str, ChildState] = {}
        self._committed: dict[str, ChildState] = {}
        self._completion_sources: dict[str, dict[str, str]] = {}
        self._sources: dict[str, dict[str, str]] = {}
        self._task_indexes: dict[str, int] = {}
        self._terminal_outcomes: dict[str, dict[str, object]] = {}
        self._parent_conversation_id: str | None = None
        self._recovery_source: dict[str, str] | None = None
        self._lock = RLock()
        self.read_session: ResumeReadSession | None = None
        self._hydrated = None

    def bind_source(self, invocation_id: str, source: dict[str, str] | None) -> None:
        source = validate_call_source(source)
        with self._lock:
            previous = self._sources.get(invocation_id)
            if previous is not None and previous != source:
                raise ValueError('Child invocation source changed')
            self._sources[invocation_id] = source

    def register(self, states: tuple[ChildState, ...]) -> None:
        with self._lock:
            for index, state in enumerate(states):
                source = self._sources.get(state.invocation_id)
                if source is None:
                    raise ValueError('Child admission has no confirmed parent call')
                index = self._task_indexes.setdefault(state.child_id, index)
                self.store.emit('child_admitted',
                                f'child-admitted:{source["path"]}:{source["item_id"]}:{index}',
                                {'child_id': state.child_id, 'source': source, 'task_index': index})
                self._registered[state.child_id] = state

    def restore_admission(self, state: ChildState) -> None:
        """Reinstall a previously recorded admission without appending a new fact."""
        if state.child_id in self._registered and self._registered[state.child_id] == state:
            return
        projection = self.hydrate(self._parent_conversation_id)
        if state not in projection.states:
            raise ValueError('Child admission is not recorded in this run')

    def hydrate(self, parent_conversation_id: str | None = None, *, source: dict[str, str] | None = None):
        from ownpi.runtime.rollout.child_projection import project_children

        parent_conversation_id = parent_conversation_id or self._parent_conversation_id
        source = source or self._recovery_source
        with self._lock:
            reader = self.read_session or ResumeReadSession()
            events = self.store.events
            root = self.store.path.parent.parent
            key = (events, parent_conversation_id, source, reader.signature(root))
            if self._hydrated is None or self._hydrated[0] != key:
                projection = project_children(events, run_root=root, run_id=self.store.run_id,
                                              parent_conversation_id=parent_conversation_id,
                                              source=source, read_session=reader)
                self._hydrated = key, projection
            projection = self._hydrated[1]
            self._parent_conversation_id = parent_conversation_id
            self._recovery_source = source
            for state in projection.states:
                self._registered.setdefault(state.child_id, state)
                source = projection.sources[state.child_id]
                previous = self._sources.get(state.invocation_id)
                if previous is not None and previous != source:
                    raise ValueError('Child recovery requires an unambiguous parent invocation')
                self._sources[state.invocation_id] = source
                self._task_indexes[state.child_id] = projection.task_indexes[state.child_id]
                if state.completion_committed:
                    self._committed[state.child_id] = state
                    self._completion_sources[state.child_id] = dict(projection.completion_sources[state.child_id])
        return projection

    def load(self) -> tuple[ChildState, ...]:
        return tuple(state for state in self.hydrate().states if state.completion_committed)

    def commit(self, child_id: str, content: str, *, source: dict[str, str] | None) -> ChildState:
        if not content.strip():
            raise ValueError('completion content must be non-empty')
        source = validate_call_source(source)
        with self._lock:
            return self._commit(child_id, content, source)

    def _commit(self, child_id: str, content: str, source: dict[str, str]) -> ChildState:
        previous = self._committed.get(child_id)
        if previous is not None:
            if previous.completion_content != content:
                raise ValueError('Conflicting child completion')
            self.store.emit('child_completion_committed', f'child-completion:{child_id}',
                            {'child_id': child_id, 'source': source})
            return previous
        state = self._registered.get(child_id)
        if state is None:
            raise ValueError('Child completion has no admitted invocation')
        committed = replace(state, status='completed', completion_content=content,
                            completion_committed=True, terminal_status='completed')
        self.store.emit('child_completion_committed', f'child-completion:{child_id}',
                        {'child_id': child_id, 'source': source})
        self._committed[child_id] = committed
        self._completion_sources[child_id] = dict(source)
        return committed

    def committed_feedback(self, child_id: str) -> tuple[ChildState, dict[str, str]] | None:
        with self._lock:
            state = self._committed.get(child_id)
            if state is None:
                return None
            return state, dict(self._completion_sources[child_id])

    def record_live_state(self, state: ChildState) -> None:
        with self._lock:
            if state.child_id not in self._registered:
                return
            if state.status == 'running':
                self.store.emit('child_execution_started', f'child-started:{state.child_id}',
                                {'child_id': state.child_id})
            elif state.child_id not in self._committed and (
                state.status in {'failed', 'cancelled'}
                or state.terminal_status in {'failed', 'cancelled', 'interrupted'}
            ):
                # Retry an unconfirmed outcome unchanged, even after a cleanup error.
                status = state.terminal_status or state.status
                data = self._terminal_outcomes.setdefault(state.child_id, {
                    'child_id': state.child_id,
                    'status': 'cancelled' if status == 'interrupted' else status,
                    'error': state.error or state.completion_content or None,
                })
                self.store.emit('child_execution_finished', f'child-finished:{state.child_id}', data)
            if state.status == 'reclaimed':
                self.store.emit('child_worker_reclaimed', f'child-worker-reclaimed:{state.child_id}',
                                {'child_id': state.child_id})

    def live_completion(self, child_id: str) -> ChildState | None:
        with self._lock:
            return self._committed.get(child_id)

    def find(self, *, invocation_id: str, task_name: str, task_message: str) -> ChildState | None:
        return next((state for state in reversed(self.load()) if state.invocation_id == invocation_id
                     and state.task_name == task_name and state.task_message == task_message), None)

    def overlay(self, states: tuple[ChildState, ...]) -> tuple[ChildState, ...]:
        values = {state.child_id: state for state in states}
        for completed in self.load():
            previous = values.get(completed.child_id)
            status = previous.status if previous and previous.status in {'reclaimed', 'reclamation_failed'} else completed.status
            values[completed.child_id] = replace(completed, status=status)
        return tuple(values.values())
