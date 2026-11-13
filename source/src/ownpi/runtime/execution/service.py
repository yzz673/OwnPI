from __future__ import annotations

from dataclasses import replace
import logging
from pathlib import Path
from threading import Event, RLock
from typing import Any, Callable

from ownpi.runtime.execution.board import (
    ExecutionBoardProjector,
    ExecutionBoardRef,
    ExecutionBoardTargetError,
)
from ownpi.runtime.execution.commit_gateway import (
    ExecutionCommitGateway,
    ExecutionCommitReceipt,
)
from ownpi.runtime.execution.event_persistence import (
    ExecutionEventPersistence, ExecutionPersistenceMixin,
)
from ownpi.runtime.execution.revision_hub import ExecutionRevisionHub
from ownpi.runtime.execution.runtime_work import (
    ExecutionDispatch,
    record_needs_runtime_work,
)
from ownpi.runtime.execution.registry import ExecutionRegistry
from ownpi.runtime.execution.release import (
    ExecutionReleaseCoordinator,
    ProcessReleasePort,
)
from ownpi.runtime.execution.scheduler import (
    ExecutionScheduler,
    SchedulerPollResult,
)
from ownpi.runtime.execution.service_lifecycle import ExecutionLifecycleMixin
from ownpi.runtime.execution.shutdown import ExecutionShutdownPhase, ExecutionSchedulerShutdownTimeout
from ownpi.runtime.execution.state import ExecutionObservation, ExecutionRecord
from ownpi.runtime.events import now_utc_iso
from ownpi.runtime.limits.recovery import (
    DEFAULT_EXECUTION_SCHEDULER_STOP_TIMEOUT_SECONDS,
)
from ownpi.runtime.rollout.run_events import RunEventStore


_LONG_EXECUTION_LAUNCH_TOOLS = frozenset({'exec_command', 'remote_run'})
class ExecutionRuntimeService(ExecutionPersistenceMixin, ExecutionLifecycleMixin):
    def __init__(
        self,
        *,
        state_store: Any,
        run_dir: Path,
        execution_dispatch: ExecutionDispatch | None = None,
        scheduler_options: dict[str, Any] | None = None,
        process_release_port: ProcessReleasePort | None = None,
        event_store: RunEventStore | None = None,
    ) -> None:
        self._state_store = state_store
        self._lock = RLock()
        self._events = ExecutionEventPersistence(
            lambda: event_store if event_store is not None else
            getattr(self._state_store, 'run_event_store', None)
        )
        self._board_update_pending = bool(self._events.launch_registry().records)
        self._gateway = ExecutionCommitGateway(
            state_store=state_store,
            lock=self._lock,
        )
        self._projector = ExecutionBoardProjector(run_dir=run_dir)
        self._execution_dispatch = execution_dispatch
        self._release_coordinator = ExecutionReleaseCoordinator(
            process_release_port,
        )
        self._shutdown_phase = ExecutionShutdownPhase.RUNNING
        self._quiescing_generations: set[int] = set()
        self._dirty_owner_processes: set[tuple[str, str]] = set()
        self._revision_hub = ExecutionRevisionHub(
            lock=self._lock,
            revision=lambda: self._state_store.state.execution_registry.material_revision,
        )
        self._scheduler = ExecutionScheduler(
            lock=self._lock,
            poll=self._poll_execution,
            **dict(scheduler_options or {}),
        )

    @property
    def scheduler(self) -> ExecutionScheduler:
        return self._scheduler

    @property
    def shutdown_phase(self) -> ExecutionShutdownPhase:
        with self._lock:
            return self._shutdown_phase

    @property
    def active_board_ref(self) -> ExecutionBoardRef | None:
        with self._lock:
            registry = self._board_registry_locked()
            try:
                return self._projector.current_ref(registry)
            except (OSError, ExecutionBoardTargetError):
                self._board_update_pending = True
                logging.getLogger(__name__).warning('Execution board reference unavailable', exc_info=True)
                return None

    def registry_snapshot(self) -> ExecutionRegistry:
        with self._lock:
            return self._state_store.state.execution_registry

    def board_content_is_canonical(self, content: bytes) -> bool:
        with self._lock:
            return self._projector.is_canonical(
                self._board_registry_locked(),
                content,
            )

    def set_execution_dispatch(self, dispatch: ExecutionDispatch) -> None:
        self._execution_dispatch = dispatch

    def start(self) -> bool:
        with self._lock:
            if self._shutdown_phase is ExecutionShutdownPhase.CLOSED:
                raise RuntimeError('closed execution runtime service cannot restart')
            self._revision_hub.reset_locked()
            records = self._state_store.state.execution_registry.records
            # Completed checkpoint entries can be retained only for board repair.
            # Existing in-process release requests remain owned by the coordinator.
            release_pending = self._release_coordinator.rebuild(tuple(
                record for record in records
                if not (record.terminal and record.readback_status == 'complete')
            ))
            runtime_pending = tuple(
                record.execution_id
                for record in records
                if record_needs_runtime_work(record)
            )
            pending = tuple(dict.fromkeys(runtime_pending + release_pending))
            self._scheduler.rebuild(pending)
        try:
            self._scheduler.start()
        except Exception:
            with self._lock:
                self._shutdown_phase = ExecutionShutdownPhase.QUIESCING
            raise
        with self._lock:
            self._shutdown_phase = ExecutionShutdownPhase.RUNNING
            self._quiescing_generations.clear()
        self.flush_pending_board_update()
        return self.flush_pending_events()

    def stop(
        self,
        *,
        timeout_seconds: float = DEFAULT_EXECUTION_SCHEDULER_STOP_TIMEOUT_SECONDS,
    ) -> bool:
        with self._lock:
            if self._shutdown_phase is ExecutionShutdownPhase.CLOSED:
                return self.flush_pending_events()
            self._shutdown_phase = ExecutionShutdownPhase.QUIESCING
            self._quiescing_generations.add(self._scheduler.generation)
        self._revision_hub.cancel_all()
        if not self._scheduler.stop(timeout_seconds=timeout_seconds):
            raise ExecutionSchedulerShutdownTimeout('execution scheduler shutdown timed out')
        with self._lock:
            self._quiescing_generations.clear()
        self.flush_pending_board_update()
        return self.flush_pending_events()

    def commit(self, observation: ExecutionObservation) -> ExecutionCommitReceipt:
        with self._lock:
            receipt = self._commit_locked(observation)
        self._attempt_release(receipt.execution_id)
        return receipt

    def commit_tool_observation(
        self,
        observation: ExecutionObservation,
    ) -> ExecutionCommitReceipt | None:
        with self._lock:
            normalized = self._normalize_tool_observation_locked(observation)
            if normalized is None:
                return None
            receipt = self._commit_locked(normalized)
        self._attempt_release(receipt.execution_id)
        return receipt

    def wait_for_update(
        self,
        *,
        after_revision: int,
        timeout_seconds: float,
        cancel_checker: Callable[[], bool] | Event | None = None,
    ) -> tuple[str, int, ExecutionBoardRef | None]:
        status, current = self._revision_hub.wait(
            after_revision=after_revision,
            timeout_seconds=timeout_seconds,
            cancel_checker=cancel_checker,
        )
        return status, current, self.active_board_ref

    async def wait_for_update_async(
        self, *, after_revision: int, timeout_seconds: float,
        cancel_checker: Callable[[], bool] | Event | None = None,
    ) -> tuple[str, int, ExecutionBoardRef | None]:
        status, current = await self._revision_hub.wait_async(
            after_revision=after_revision, timeout_seconds=timeout_seconds,
            cancel_checker=cancel_checker,
        )
        return status, current, self.active_board_ref

    def notify_owner_event(
        self,
        *,
        owner_id: str,
        process_id: str,
        method: str,
    ) -> None:
        """Wake canonical executions without creating a second ownership map."""
        if method not in {'process/exited', 'process/closed'}:
            return
        normalized_owner = str(owner_id or '').strip()
        normalized_process = str(process_id or '').strip()
        if not normalized_owner:
            return
        with self._lock:
            registry = self._state_store.state.execution_registry
            execution_ids = (
                registry.execution_ids_for_owner_process(
                    owner_id=normalized_owner,
                    process_id=normalized_process,
                )
                if normalized_process
                else registry.execution_ids_for_owner(owner_id=normalized_owner)
            )
            if execution_ids:
                for execution_id in execution_ids:
                    self._scheduler.schedule_now(execution_id)
                return
            if normalized_process:
                self._dirty_owner_processes.add((normalized_owner, normalized_process))

    def _poll_execution(
        self,
        execution_id: str,
        poll_attempt: int,
        generation: int,
    ) -> SchedulerPollResult:
        with self._lock:
            if not self._scheduler_generation_allowed(generation):
                return SchedulerPollResult(terminal=True, succeeded=False)
            if not self._events.flush():
                return SchedulerPollResult(terminal=False, succeeded=False)
        release_attempt = self._attempt_release(execution_id)
        if release_attempt.pending:
            return SchedulerPollResult(terminal=False, succeeded=False)
        with self._lock:
            record = self._state_store.state.execution_registry.get(execution_id)
            if record is None or not record_needs_runtime_work(record):
                return SchedulerPollResult(terminal=True, succeeded=True)
        operation_record = record
        dispatch = self._execution_dispatch
        if dispatch is None:
            self._commit_poll_issue(
                record=record,
                poll_attempt=poll_attempt,
                generation=generation,
            )
            return SchedulerPollResult(terminal=False, succeeded=False)
        try:
            observation = (
                dispatch.readback(record)
                if record.terminal
                else dispatch.monitor(record)
            )
            receipt = self._commit_scheduler_observation(
                observation,
                generation=generation,
            )
            if receipt is None:
                return SchedulerPollResult(terminal=True, succeeded=False)
            with self._lock:
                updated = self._state_store.state.execution_registry.get(
                    receipt.execution_id
                )
            if (
                updated is not None
                and not record.terminal
                and updated.terminal
                and record_needs_runtime_work(updated)
            ):
                if not self.flush_pending_events():
                    return SchedulerPollResult(terminal=False, succeeded=False)
                operation_record = updated
                readback = dispatch.readback(updated)
                receipt = self._commit_scheduler_observation(
                    readback,
                    generation=generation,
                )
                if receipt is None:
                    return SchedulerPollResult(terminal=True, succeeded=False)
        except Exception:
            self._commit_poll_issue(
                record=operation_record,
                poll_attempt=poll_attempt,
                generation=generation,
            )
            return SchedulerPollResult(terminal=False, succeeded=False)
        with self._lock:
            updated = self._state_store.state.execution_registry.get(receipt.execution_id)
            done = (
                updated is None
                or (
                    not record_needs_runtime_work(updated)
                    and not self._release_coordinator.needs_release(execution_id)
                    and not self._events.pending
                )
            )
        return SchedulerPollResult(terminal=done, succeeded=True)

    def _commit_locked(
        self,
        observation: ExecutionObservation,
    ) -> ExecutionCommitReceipt:
        if self._shutdown_phase is ExecutionShutdownPhase.CLOSED:
            raise RuntimeError("closed execution runtime service rejects commits")
        launches = self._events.launch_registry()
        commit, receipt = self._gateway.commit_locked(
            observation, launch_order_floor=max(
                (item.launch_order for item in launches.records), default=0,
            ),
        )
        self._events.record_transition(commit.record, observation)
        receipt = replace(receipt, persistence_confirmed=(
            not self._events.pending if self._events.enabled else None
        ))
        release_pending = self._release_coordinator.register(commit.record)
        owner_key = (commit.record.handle.owner_id, commit.record.handle.process_id)
        dirty_wake = owner_key in self._dirty_owner_processes
        if dirty_wake:
            self._dirty_owner_processes.discard(owner_key)
        if (record_needs_runtime_work(commit.record) or release_pending or dirty_wake
                or self._events.pending):
            self._scheduler.schedule(receipt.execution_id)
        else:
            self._scheduler.unschedule(receipt.execution_id)
        if receipt.created and observation.origin.tool_name in _LONG_EXECUTION_LAUNCH_TOOLS:
            self._mark_board_pending_locked()
            self.flush_pending_board_update()
        return receipt

    def _commit_scheduler_observation(
        self,
        observation: ExecutionObservation,
        *,
        generation: int,
    ) -> ExecutionCommitReceipt | None:
        with self._lock:
            if (
                not self._scheduler_generation_allowed(generation)
            ):
                return None
            receipt = self._commit_locked(observation)
        self._attempt_release(receipt.execution_id)
        return receipt

    def _commit_poll_issue(
        self,
        *,
        record: ExecutionRecord,
        poll_attempt: int,
        generation: int,
    ) -> None:
        if poll_attempt < 3 or record.terminal:
            return
        self._commit_scheduler_observation(
            ExecutionObservation(
                execution_id=record.execution_id,
                status=record.status,
                handle=record.handle,
                origin=record.origin,
                observed_at=now_utc_iso(),
                result_refs=record.result_refs,
                readback_status=record.readback_status,
                issue='Execution monitor is temporarily unavailable; Scheduler is retrying.',
            ),
            generation=generation,
        )
__all__ = ['ExecutionDispatch', 'ExecutionRuntimeService']
