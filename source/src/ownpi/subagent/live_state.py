"""Current-process child coordination, never a disk snapshot or resume store."""

from dataclasses import replace
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING

from .state import ChildState

if TYPE_CHECKING:
    from ownpi.runtime.rollout.completion_events import CompletionEvents


class LiveSubagentStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.completion_events: CompletionEvents | None = None
        self._states: dict[str, ChildState] = {}
        self._lock = RLock()

    def load(self) -> tuple[ChildState, ...]:
        with self._lock:
            values = []
            for state in self._states.values():
                committed = (self.completion_events.live_completion(state.child_id)
                             if self.completion_events is not None else None)
                if committed is not None:
                    state = replace(state, completion_committed=True,
                                    completion_content=committed.completion_content,
                                    terminal_status='completed')
                values.append(state)
            return tuple(values)

    def hydrate(self, states: tuple[ChildState, ...]) -> None:
        """Install verified historical states without replaying their writers."""
        with self._lock:
            for state in states:
                previous = self._states.get(state.child_id)
                if previous is not None and previous != state:
                    raise ValueError('Cannot replace active child state during hydration')
            self._states.update({state.child_id: state for state in states})

    def reserve(self, states: tuple[ChildState, ...], *,
                allow_reserved_ids: frozenset[str] = frozenset()) -> None:
        with self._lock:
            if len({state.child_id for state in states}) != len(states):
                raise ValueError('duplicate child identity in batch')
            for state in states:
                previous = self._states.get(state.child_id)
                if previous is not None:
                    if (state.child_id not in allow_reserved_ids or previous.status != 'created'
                            or previous.invocation_id != state.invocation_id
                            or previous.task_name != state.task_name
                            or previous.task_message != state.task_message):
                        raise ValueError('child identity cannot be rebound')
            self._states.update({state.child_id: state for state in states})

    def save(self, states: tuple[ChildState, ...]) -> None:
        with self._lock:
            for state in states:
                previous = self._states.get(state.child_id)
                if previous is None or (previous.invocation_id, previous.task_name, previous.task_message) != (
                        state.invocation_id, state.task_name, state.task_message):
                    raise ValueError('child state has no matching live admission')
                if self.completion_events is not None:
                    self.completion_events.record_live_state(state)
                self._states[state.child_id] = state

    def reclaim_confirmed(self, child_ids: frozenset[str], *,
                          reasons: dict[str, str] | None = None) -> tuple[ChildState, ...]:
        with self._lock:
            values = tuple(replace(state, status='reclaimed', error=(reasons or {}).get(state.child_id),
                                   terminal_status=state.terminal_status or 'cancelled')
                           for state in self.load() if state.child_id in child_ids
                           and state.status in {'created', 'running'})
            self.save(values)
            return values

    def batch_states(self, invocation_id: str, tasks: list[dict[str, str]]) -> tuple[ChildState, ...]:
        states = tuple(state for state in self.load() if state.invocation_id == invocation_id)
        if states and [(state.task_name, state.task_message) for state in states] != [
            (task['task_name'], task['message']) for task in tasks
        ]:
            raise ValueError('Child invocation tasks changed')
        return states
