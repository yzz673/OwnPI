from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any

from ownpi.runtime.execution.registry import RegistryCommit
from ownpi.runtime.execution.state import ExecutionObservation
from ownpi.runtime.execution.terminal_readback import assess_terminal_readback


@dataclass(frozen=True)
class ExecutionCommitReceipt:
    execution_id: str
    material_revision: int
    material_change: bool
    created: bool
    terminal: bool
    persistence_confirmed: bool | None = None


class ExecutionCommitGateway:
    def __init__(
        self,
        *,
        state_store: Any,
        lock: RLock,
    ) -> None:
        self._state_store = state_store
        self._lock = lock

    @property
    def lock(self) -> RLock:
        return self._lock

    def commit_locked(
        self,
        observation: ExecutionObservation,
        *, launch_order_floor: int = 0,
    ) -> tuple[RegistryCommit, ExecutionCommitReceipt]:
        assessed = assess_terminal_readback(observation)
        current = self._state_store.state.execution_registry
        commit = current.commit(assessed, launch_order_floor=launch_order_floor)
        self._state_store.state.execution_registry = commit.registry
        receipt = ExecutionCommitReceipt(
            execution_id=commit.record.execution_id,
            material_revision=commit.registry.material_revision,
            material_change=commit.material_change,
            created=commit.created,
            terminal=commit.record.terminal,
        )
        return commit, receipt

    def commit(
        self,
        observation: ExecutionObservation,
    ) -> tuple[RegistryCommit, ExecutionCommitReceipt]:
        with self._lock:
            return self.commit_locked(observation)

__all__ = ['ExecutionCommitGateway', 'ExecutionCommitReceipt']
