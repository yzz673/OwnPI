from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable

from ownpi.runtime.execution.owner_lost import owner_lost_observations
from ownpi.runtime.execution.release import attempt_release_and_reconcile
from ownpi.runtime.execution.shutdown import ExecutionShutdownPhase
from ownpi.runtime.execution.state import ExecutionObservation


class ExecutionLifecycleMixin:
    commit: Callable[[Any], Any]
    flush_pending_board_update: Callable[[], None]
    flush_pending_events: Callable[[], bool]
    _lock: Any
    _state_store: Any
    _release_coordinator: Any
    _scheduler: Any
    _execution_dispatch: Any
    _revision_hub: Any
    _shutdown_phase: ExecutionShutdownPhase
    _quiescing_generations: set[int]
    persistence_diagnostic: str

    def _normalize_tool_observation_locked(
        self, observation: ExecutionObservation,
    ) -> ExecutionObservation | None:
        if observation.origin.tool_name != 'remote_pull':
            return observation
        match = self._state_store.state.execution_registry.matching_record(observation)
        if match is None:
            return None
        return replace(
            observation, execution_id=match.execution_id, status=match.status,
            readback_status=(observation.readback_status if match.terminal else match.readback_status),
            issue=observation.issue if match.terminal else match.issue,
        )

    def commit_owner_lost(
        self,
        *,
        owner_id: str,
        process_id: str = "",
    ) -> tuple[Any, ...]:
        with self._lock:
            observations = owner_lost_observations(
                self._state_store.state.execution_registry.records,
                owner_id=owner_id,
                process_id=process_id,
            )
        return tuple(self.commit(observation) for observation in observations)

    def _attempt_release(self, execution_id: str) -> Any:
        return attempt_release_and_reconcile(
            coordinator=self._release_coordinator,
            execution_id=execution_id,
            lock=self._lock,
            state_store=self._state_store,
            scheduler=self._scheduler,
        )

    def mark_closed(self) -> bool:
        with self._lock:
            if not self.flush_pending_events():
                return False
            self._shutdown_phase = ExecutionShutdownPhase.CLOSED
            self._quiescing_generations.clear()
        self._revision_hub.cancel_all()
        self.flush_pending_board_update()
        return True

    def _scheduler_generation_allowed(self, generation: int) -> bool:
        if self._shutdown_phase is ExecutionShutdownPhase.CLOSED:
            return False
        return (
            generation == self._scheduler.generation
            or generation in self._quiescing_generations
        )


__all__ = ["ExecutionLifecycleMixin"]
