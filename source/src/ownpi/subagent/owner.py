"""Unified owner for the optional subagent capability."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from threading import Event, Lock
from time import monotonic
from typing import TYPE_CHECKING, Any, Callable

from .adapter import SubagentLoopAdapter
from .contracts import SubagentContext, SubagentResult, SubagentTask
from .coordinator import BatchCoordinator, BatchResult, ChildOutcome
from .feedback_delivery import project_batch_feedback
from .live_state import LiveSubagentStateStore
from .state import ChildState
from .continuation import SubagentContinuationLoop

if TYPE_CHECKING:
    from ownpi.runtime.rollout.agent_relationships import AgentRelationships
    from ownpi.runtime.rollout.resume import ResumedConversation
    from ownpi.runtime.tools.definitions import ToolDefinition


@dataclass(frozen=True)
class SubagentShutdownReport:
    """Codex-style bounded shutdown outcome for child ownership."""

    completed: tuple[str, ...] = ()
    timed_out: tuple[str, ...] = ()
    submit_failed: tuple[str, ...] = ()
    ownership_unresolved: tuple[str, ...] = ()
    batch_drained: bool = True

    @property
    def ok(self) -> bool:
        return (
            self.batch_drained
            and not self.timed_out
            and not self.submit_failed
            and not self.ownership_unresolved
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "completed": list(self.completed),
            "timed_out": list(self.timed_out),
            "submit_failed": list(self.submit_failed),
            "ownership_unresolved": list(self.ownership_unresolved),
            "batch_drained": self.batch_drained,
            "ok": self.ok,
        }


@dataclass(frozen=True)
class SubagentOwner:
    """Owns the subagent boundary without changing the Core execution path."""

    enabled: bool = False
    max_count: int = 8
    adapter: SubagentLoopAdapter | None = None
    coordinator: BatchCoordinator = field(default_factory=BatchCoordinator)
    continuation: SubagentContinuationLoop | None = None
    context_builder: Callable[[SubagentTask, str], Any] | None = None
    execution_factory: Any | None = None
    historical_agent_relationships: AgentRelationships | None = field(default=None, init=False, repr=False)

    def install_historical_agents(self, relationships: AgentRelationships) -> None:
        """Install metadata only; never register active children or restore resources."""
        with self._lock:
            object.__setattr__(self, 'historical_agent_relationships', relationships)

    def read_historical_child(
        self, *, child_id: str, conversation_id: str, parent_conversation_id: str,
    ) -> ResumedConversation:
        with self._lock:
            relationships = self.historical_agent_relationships
        if relationships is None:
            raise ValueError('Historical agent metadata is not installed')
        return relationships.read_child(
            child_id=child_id, conversation_id=conversation_id,
            parent_conversation_id=parent_conversation_id,
        )

    @classmethod
    def from_config(
        cls,
        enabled: bool,
        *,
        max_count: int = 8,
        adapter: SubagentLoopAdapter | None = None,
        state_store: LiveSubagentStateStore | None = None,
        context_builder: Callable[[SubagentTask, str], Any] | None = None,
        execution_factory: Any | None = None,
    ) -> 'SubagentOwner':
        """Construct the owner without initializing subagent state when disabled."""
        if not enabled:
            return cls(enabled=False)
        if type(max_count) is not int or max_count < 1:
            raise ValueError("runtime.subagent_max_count must be a positive integer")
        return cls(
            enabled=True,
            max_count=max_count,
            adapter=adapter,
            coordinator=BatchCoordinator(state_store=state_store),
            continuation=SubagentContinuationLoop(adapter) if adapter is not None else None,
            context_builder=context_builder,
            execution_factory=execution_factory,
        )

    @classmethod
    def from_runtime_config(
        cls,
        runtime_config: object | None,
        *,
        adapter: SubagentLoopAdapter | None = None,
        state_store: LiveSubagentStateStore | None = None,
        context_builder: Callable[[SubagentTask, str], Any] | None = None,
        execution_factory: Any | None = None,
    ) -> 'SubagentOwner':
        """Build the owner from the user-facing runtime switch.

        Missing or falsey configuration keeps the subsystem inert.  The owner
        does not inspect or construct subagent context in this boundary.
        """
        enabled = bool(getattr(runtime_config, 'subagent_enabled', False))
        return cls.from_config(
            enabled,
            max_count=getattr(runtime_config, 'subagent_max_count', 8),
            adapter=adapter,
            state_store=state_store,
            context_builder=context_builder,
            execution_factory=execution_factory,
        )
    _completed: dict[str, SubagentResult] = field(default_factory=dict, init=False, repr=False)
    _child_tasks: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _reserved_children: set[str] = field(default_factory=set, init=False, repr=False)
    _batch_reserved_children: set[str] = field(default_factory=set, init=False, repr=False)
    _registration_attempted: set[str] = field(default_factory=set, init=False, repr=False)
    _registered_children: set[str] = field(default_factory=set, init=False, repr=False)
    _known_children: set[str] = field(default_factory=set, init=False, repr=False)
    _unresolved_startup_children: set[str] = field(default_factory=set, init=False, repr=False)
    _active_children: set[str] = field(default_factory=set, init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _shutdown_event: Event = field(default_factory=Event, init=False, repr=False)
    _batches_drained: Event = field(default_factory=lambda: _set_event(), init=False, repr=False)
    _active_batches: list[int] = field(default_factory=lambda: [0], init=False, repr=False)

    def run(self, task: SubagentTask) -> SubagentResult:
        if not self.enabled:
            raise RuntimeError("subagent mode is disabled")
        if self.adapter is None:
            raise RuntimeError("subagent loop adapter is unavailable")
        assert self.adapter is not None
        if self.continuation is None:
            return self.adapter.run(SubagentContext(task=task))
        outcome = self.continuation.run(SubagentContext(task=task))
        if outcome.result is not None:
            return outcome.result
        return SubagentResult(task_name=task.task_name, status=outcome.status, content=outcome.error or '')

    def run_resumed_child(
        self, facts: Any, *, cancel_checker: Callable[[], bool] | None = None,
        _batch_owned: bool = False,
    ) -> SubagentResult:
        """Continue an admitted child on its original rollout, without a new batch."""
        if not self.enabled or self.adapter is None or self.continuation is None:
            raise RuntimeError('subagent execution is unavailable')
        child_id = str(facts.conversation.metadata.get('actor_id') or '')
        admitted = facts.admitted_child(child_id)
        if admitted is None:
            raise ValueError('Resumed child lacks a durable task admission')
        completion_events = self.coordinator.completion_events
        if completion_events is None:
            raise RuntimeError('Resumed child requires the run-event completion store')
        if self.execution_factory is None:
            raise RuntimeError('Resumed child requires its execution factory')
        source = next(event.data.to_object()['source'] for event in facts.events
                      if event.kind == 'child_admitted' and event.data.to_object()['child_id'] == child_id)
        completion_events.read_session = facts.read_session
        projection = completion_events.hydrate(facts.conversation.metadata['parent_id'], source=source)
        admitted = next(state for state in projection.states if state.child_id == child_id)
        live = self.coordinator.state_store
        if isinstance(self.coordinator.state_store, LiveSubagentStateStore):
            previous = next((state for state in live.load() if state.child_id == child_id), None)
            if previous is None:
                live.hydrate((admitted,))
            elif (previous.invocation_id, previous.task_name, previous.task_message) != (
                    admitted.invocation_id, admitted.task_name, admitted.task_message):
                raise ValueError('Recovered child admission differs from current owner state')
        if admitted.completion_committed or admitted.terminal_status in {'failed', 'cancelled'}:
            completion_events.restore_admission(admitted)
            result = SubagentResult(admitted.task_name, admitted.terminal_status or 'completed',
                                    admitted.completion_content or admitted.error or '')
            if admitted.completion_committed:
                with self._lock:
                    self._completed[child_id] = result
            return result
        return self._run_recovered_child(
            admitted, facts=facts, cancel_checker=cancel_checker, _batch_owned=_batch_owned,
        )

    def _run_recovered_child(
        self, admitted: ChildState, *, facts: Any | None = None,
        cancel_checker: Callable[[], bool] | None = None, _batch_owned: bool = False,
    ) -> SubagentResult:
        child_id = admitted.child_id
        if not _batch_owned:
            with self._lock:
                if self._shutdown_event.is_set() or self._active_batches[0]:
                    raise RuntimeError('child resume requires an idle, active owner')
                self._active_batches[0] += 1
                self._batches_drained.clear()

        def cancelled() -> bool:
            return self._shutdown_event.is_set() or bool(cancel_checker and cancel_checker())

        registered = False
        result = None
        try:
            self.coordinator.completion_events.restore_admission(admitted)
            from ownpi.runtime.interactions.pause import HumanClarificationPausedError

            if facts is None and cancelled():
                result = SubagentResult(admitted.task_name, 'cancelled', 'batch cancelled before start')
                self.coordinator.state_store.save((replace(
                    admitted, status='cancelled', terminal_status='cancelled', error=result.content,
                ),))
                return result
            try:
                if facts is None:
                    self.register_child(child_id, admitted.task_name)
                    registered = True
                    self.coordinator.state_store.save((replace(admitted, status='running'),))
                    bundle = self.execution_factory.build(
                        SubagentTask(admitted.task_name, admitted.task_message), child_id, cancelled,
                    )
                else:
                    bundle = self.execution_factory.build_resumed(facts, cancel_checker=cancelled)
            except HumanClarificationPausedError as paused:
                return SubagentResult(admitted.task_name, 'waiting',
                                      pending_interactions=paused.pending_interactions)
            if not registered:
                self.register_child(child_id, admitted.task_name)
                registered = True
                if admitted.status == 'created' and self.coordinator.state_store is not None:
                    self.coordinator.state_store.save((replace(admitted, status='running'),))
            outcome = self.continuation.run(bundle)
            result = outcome.result or SubagentResult(
                admitted.task_name, outcome.status, outcome.error or '',
            )
            if result.status == 'completed' and self.completed(child_id) is None:
                result = SubagentResult(admitted.task_name, 'failed',
                    'subagent adapter reported completion without a durable complete_subagent submission')
            if self.coordinator.state_store is not None:
                self.coordinator.state_store.save((replace(admitted, status=result.status,
                    terminal_status='' if result.status == 'waiting' else result.status, completion_content=result.content,
                    completion_committed=self.completed(child_id) is not None),))
            return result
        finally:
            try:
                if registered:
                    try:
                        self._reclaim_child(
                            SubagentTask(admitted.task_name, admitted.task_message),
                            result, child_id=child_id,
                        )
                        if result is not None and result.status != 'waiting':
                            current = next(state for state in self.coordinator.state_store.load()
                                           if state.child_id == child_id)
                            self.coordinator.state_store.save((replace(current, status='reclaimed'),))
                    except Exception as error:
                        from ownpi.controller.runtime_control_signal import RuntimeControlRequested

                        if isinstance(error, RuntimeControlRequested):
                            raise
                        logging.getLogger(__name__).warning('Child cleanup unconfirmed: %s', child_id, exc_info=True)
            finally:
                if not _batch_owned:
                    with self._lock:
                        self._active_batches[0] -= 1
                    if self._active_batches[0] == 0:
                        self._batches_drained.set()

    def run_recovered_children(
        self, states: tuple[ChildState, ...], *, facts_by_child: dict[str, Any],
        cancel_checker: Callable[[], bool] | None = None,
    ) -> tuple[SubagentResult | Exception, ...]:
        if not states:
            return ()
        with self._lock:
            if self._shutdown_event.is_set() or self._active_batches[0]:
                raise RuntimeError('child batch resume requires an idle, active owner')
            self._active_batches[0] += 1
            self._batches_drained.clear()
        try:
            with ThreadPoolExecutor(max_workers=self.coordinator.max_workers) as pool:
                futures = tuple(
                    pool.submit(self.run_resumed_child, facts_by_child[state.child_id],
                                cancel_checker=cancel_checker, _batch_owned=True)
                    if state.child_id in facts_by_child else
                    pool.submit(self._run_recovered_child, state,
                                cancel_checker=cancel_checker, _batch_owned=True)
                    for state in states
                )
                results: list[SubagentResult | Exception] = []
                for future in futures:
                    try:
                        results.append(future.result())
                    except Exception as error:
                        from ownpi.controller.runtime_control_signal import RuntimeControlRequested

                        if isinstance(error, RuntimeControlRequested):
                            raise
                        results.append(error)
                return tuple(results)
        finally:
            with self._lock:
                self._active_batches[0] -= 1
                if self._active_batches[0] == 0:
                    self._batches_drained.set()

    def complete(self, task_name: str, content: str, *, source: dict[str, str] | None = None) -> SubagentResult:
        return asyncio.run(self.complete_async(task_name, content, source=source))

    async def complete_async(self, task_name: str, content: str, *,
                             source: dict[str, str] | None = None) -> SubagentResult:
        """Record the only owner-recognized completion signal for one child."""
        if not self.enabled:
            raise RuntimeError("subagent mode is disabled")
        if self.adapter is None:
            raise RuntimeError("subagent loop adapter is unavailable")
        child_key = str(task_name or '').strip()
        text = str(content or '').strip()
        if not child_key or not text:
            raise ValueError("complete_subagent requires task_name and non-empty content")
        with self._lock:
            if self._child_tasks and child_key not in self._child_tasks:
                raise ValueError(
                    "complete_subagent received an unknown child identity"
                )
            name = self._child_tasks.get(child_key, child_key)
            existing = self._completed.get(child_key)
            if existing is not None:
                if existing.content != text:
                    raise ValueError('Conflicting child completion')
                return existing
            if self._child_tasks and child_key not in self._active_children:
                raise ValueError(f'Child is no longer active: {child_key}')
            result = SubagentResult(task_name=name, status="completed", content=text)
            if self.coordinator.completion_events is not None:
                committed = self.coordinator.completion_events.commit(child_key, text, source=source)
                result = SubagentResult(committed.task_name, 'completed', committed.completion_content)
            elif self.coordinator.state_store is not None:
                raise RuntimeError("Subagent completion requires the rollout event store")
            self._completed[child_key] = result
            return result

    def register_child(self, child_id: str, task_name: str) -> None:
        """Bind a runtime child identity before its loop can call completion."""
        child_key = str(child_id or '').strip()
        name = str(task_name or '').strip()
        if not child_key or not name:
            raise ValueError("child identity and task name are required")
        with self._lock:
            existing = self._child_tasks.get(child_key)
            if existing is not None and existing != name:
                raise ValueError("child identity is already bound to another task")
            self._child_tasks[child_key] = name
            self._active_children.add(child_key)
            self._known_children.add(child_key)

    def validate_batch_size(self, count: int) -> None:
        if count > self.max_count:
            raise ValueError(
                f"run_subagents requested {count} tasks, exceeding "
                f"runtime.subagent_max_count={self.max_count}. "
                "No children were started. Submit a smaller batch."
            )

    def bind_execution(
        self, *, adapter: SubagentLoopAdapter, execution_factory: Any,
        state_store: LiveSubagentStateStore, max_count: int,
    ) -> None:
        """Bind the idle run owner to the current caller without replacing its identity."""
        if type(max_count) is not int or max_count < 1:
            raise ValueError("runtime.subagent_max_count must be a positive integer")
        with self._lock:
            if not self.enabled or self._shutdown_event.is_set() or self._active_batches[0]:
                raise RuntimeError("subagent caller binding requires an idle, enabled owner")
        with self._lock:
            if self._active_batches[0] or self._shutdown_event.is_set():
                raise RuntimeError("subagent owner changed during caller binding")
            if self.coordinator.state_store is not None and self.coordinator.state_store.path != state_store.path:
                raise ValueError("subagent owner cannot be rebound to a different run")
            self.coordinator.state_store = state_store
            object.__setattr__(self, "adapter", adapter)
            object.__setattr__(self, "execution_factory", execution_factory)
            object.__setattr__(self, "continuation", SubagentContinuationLoop(adapter))
            object.__setattr__(self, "max_count", max_count)

    @property
    def is_shutdown(self) -> bool:
        return self._shutdown_event.is_set()

    def close_run_events_if_idle(self, close: Callable[[], bool]) -> bool:
        """Pause persistence only when no child producer can still publish."""
        with self._lock:
            if self._active_batches[0] or self._active_children:
                return False
            return close()

    def run_batch(
        self,
        tasks: tuple[SubagentTask, ...],
        *,
        cancel_checker: Callable[[], bool] | None = None,
        invocation_id: str = "",
    ) -> BatchResult:
        return asyncio.run(self.run_batch_async(
            tasks, cancel_checker=cancel_checker, invocation_id=invocation_id,
        ))

    async def run_batch_async(
        self,
        tasks: tuple[SubagentTask, ...],
        *,
        cancel_checker: Callable[[], bool] | None = None,
        invocation_id: str = "",
    ) -> BatchResult:
        if not self.enabled:
            raise RuntimeError("subagent mode is disabled")
        if self.adapter is None:
            raise RuntimeError("subagent loop adapter is unavailable")
        task_list = tuple(tasks)
        self.validate_batch_size(len(task_list))
        with self._lock:
            if self._shutdown_event.is_set():
                raise RuntimeError("subagent owner is shut down")
            if self._active_batches[0]:
                raise RuntimeError("a subagent batch is already active on this owner")
            self._active_batches[0] += 1
            self._batches_drained.clear()
        try:
            prior: dict[str, ChildOutcome] = {}
            pending: list[SubagentTask] = []
            reusable_child_ids: dict[str, str] = {}
            if isinstance(self.coordinator.state_store, LiveSubagentStateStore):
                states = self.coordinator.state_store.batch_states(invocation_id, [
                    {'task_name': task.task_name, 'message': task.message} for task in task_list
                ])
                if not states:
                    raise ValueError('Child batch has no prepared admission')
                for task, state in zip(task_list, states, strict=True):
                    if state.status == 'created':
                        pending.append(task)
                        reusable_child_ids[task.task_name] = state.child_id
                    elif (state.status == 'reclaimed' or state.completion_committed
                          or state.terminal_status in {'failed', 'cancelled'}):
                        result = self.completed(state.child_id)
                        if result is None and state.completion_committed:
                            result = SubagentResult(task.task_name, 'completed', state.completion_content)
                        prior[task.task_name] = ChildOutcome(
                            task_name=task.task_name, child_id=state.child_id,
                            status='completed' if state.completion_committed else state.terminal_status or 'cancelled',
                            result=result, error=state.error, reclaimed=state.status == 'reclaimed',
                        )
                    else:
                        raise RuntimeError(f'Child still owns unresolved live state: {state.child_id}')
            else:
                pending = list(tasks)
            if not pending:
                return project_batch_feedback(BatchResult(
                    status=_batch_status(tuple(prior.values())),
                    children=tuple(
                        prior[task.task_name]
                        for task in task_list
                    ),
                ), self.coordinator.completion_events)

            invocation_cancelled = Event()

            def combined_cancel_checker() -> bool:
                return invocation_cancelled.is_set() or self._shutdown_event.is_set() or bool(
                    cancel_checker is not None and cancel_checker()
                )

            batch_child_ids: dict[str, str] = {}

            def register_batch_child(child_id: str, task_name: str) -> None:
                # Retain the reserved identity even if registration only partially succeeds.
                batch_child_ids[task_name] = child_id
                with self._lock:
                    self._registration_attempted.add(child_id)
                self.register_child(child_id, task_name)
                with self._lock:
                    self._registered_children.add(child_id)

            def reserve_batch_child(child_id: str, task_name: str) -> None:
                batch_child_ids[task_name] = child_id
                with self._lock:
                    self._reserved_children.add(child_id)
                    self._batch_reserved_children.add(child_id)
                    self._known_children.add(child_id)

            result = await self.coordinator.run_async(
                tuple(pending),
                self.run,
                child_registered=register_batch_child,
                child_reserved=reserve_batch_child,
                worker_with_child_id=(
                    lambda task, child_id: self._run_child(
                        task, child_id, combined_cancel_checker
                    )
                ),
                reclaimer=lambda task, outcome: self._reclaim_child_async(
                    task, outcome, child_id=batch_child_ids[task.task_name]
                ),
                cancel_checker=combined_cancel_checker,
                notify_cancel=invocation_cancelled.set,
                reusable_child_ids=reusable_child_ids,
                invocation_id=invocation_id,
            )
            with self._lock:
                self._unresolved_startup_children.update(
                    batch_child_ids[child.task_name]
                    for child in result.children
                    if child.error and "state persistence failed" in child.error
                    and child.task_name in batch_child_ids
                )
            # A reservation can be durable even when shutdown cancels the
            # batch before registration.  This is an owner-local startup
            # outcome, so release it here instead of waiting for runtime
            # teardown to discover an unresolved created record.
            if not any(
                "state persistence failed" in (child.error or "")
                for child in result.children
            ):
                self._reclaim_unregistered_reservations()
                self._reclaim_prepared_reservations(invocation_id)
            children = {
                child.task_name: child
                for child in result.children
            }
            ordered = tuple(
                prior[task.task_name]
                if task.task_name in prior
                else children[task.task_name]
                for task in task_list
            )
            status = _batch_status(ordered) if result.status == 'completed' else result.status
            return project_batch_feedback(BatchResult(status=status, children=ordered),
                                          self.coordinator.completion_events)
        finally:
            with self._lock:
                self._active_batches[0] -= 1
                if self._active_batches[0] == 0:
                    self._batches_drained.set()

    def prepare_batch_invocation(self, *, invocation_id: str, payload: dict[str, Any],
                                 source: dict[str, str] | None = None) -> None:
        if not self.enabled:
            return
        tasks = payload.get("tasks")
        if isinstance(tasks, list) and len(tasks) > self.max_count:
            # Leave invalid model input to ordinary tool validation, without reserving children.
            return
        from .preparation import prepare_batch_invocation

        if self.coordinator.state_store is None:
            raise RuntimeError("subagent preparation requires a state store")
        with self._lock:
            if self._shutdown_event.is_set() or self._active_batches[0]:
                raise RuntimeError("subagent preparation requires an idle, active owner")
            self._active_batches[0] += 1
            self._batches_drained.clear()
        try:
            if self.coordinator.completion_events is not None:
                self.coordinator.completion_events.bind_source(invocation_id, source)
            prepare_batch_invocation(self.coordinator.state_store, invocation_id=invocation_id, payload=payload)
            with self._lock:
                self._reserved_children.update(
                    state.child_id
                    for state in self.coordinator.state_store.load()
                    if state.invocation_id == invocation_id
                )
                self._known_children.update(self._reserved_children)
        finally:
            with self._lock:
                self._active_batches[0] -= 1
                if self._active_batches[0] == 0:
                    self._batches_drained.set()

    def shutdown_report(self, *, timeout: float = 5.0) -> "SubagentShutdownReport":
        """Cancel child batches and return a bounded, inspectable shutdown report."""
        if not self.enabled:
            return SubagentShutdownReport()
        deadline = monotonic() + max(0.0, float(timeout))
        self._shutdown_event.set()
        # Synchronize with admission before trusting the drained marker.
        if not self._lock.acquire(timeout=max(0.0, deadline - monotonic())):
            # Do not call active_child_ids() here: it needs the same lock and
            # would turn a bounded shutdown into an unbounded wait.
            return SubagentShutdownReport(
                timed_out=("batch",),
                batch_drained=False,
            )
        self._lock.release()
        if not self._batches_drained.wait(timeout=max(0.0, deadline - monotonic())):
            return self._shutdown_report(
                batch_drained=False,
                timed_out=True,
            )
        self._reclaim_unregistered_reservations()
        self._reclaim_prepared_reservations("")
        # A drained batch may still own resources after a failed reclaimer.
        return self._shutdown_report(
            batch_drained=True,
            timed_out=False,
        )

    def shutdown(self, *, timeout: float = 5.0) -> bool:
        """Compatibility wrapper returning whether bounded shutdown completed."""
        return self.shutdown_report(timeout=timeout).ok

    def _shutdown_report(
        self,
        *,
        batch_drained: bool,
        timed_out: bool,
    ) -> "SubagentShutdownReport":
        store = self.coordinator.state_store
        states = store.load() if store is not None else ()
        known = set(self._known_children)
        known.update(state.child_id for state in states)
        completed: set[str] = set()
        # This report describes lifecycle shutdown. A child can finish its
        # research task with status failed/cancelled and still be completely
        # shut down; that business outcome is not a shutdown submission
        # failure.
        submit_failed: set[str] = set()
        ownership_unresolved = set(self.active_child_ids())
        for state in states:
            if state.status == "reclaimed":
                completed.add(state.child_id)
            elif state.status in {"failed", "cancelled"}:
                ownership_unresolved.add(state.child_id)
            elif state.status in {"created", "running", "reclamation_failed"}:
                ownership_unresolved.add(state.child_id)
            elif state.completion_committed:
                ownership_unresolved.add(state.child_id)
        timed_out_ids = set()
        if timed_out:
            timed_out_ids.update(known - completed - submit_failed)
        if not batch_drained:
            timed_out_ids.add("batch")
        completed.difference_update(timed_out_ids, submit_failed, ownership_unresolved)
        ownership_unresolved.difference_update(timed_out_ids)
        report = SubagentShutdownReport(
            completed=tuple(sorted(completed)),
            timed_out=tuple(sorted(timed_out_ids)),
            submit_failed=tuple(sorted(submit_failed)),
            ownership_unresolved=tuple(sorted(ownership_unresolved)),
            batch_drained=batch_drained,
        )
        return report

    def _reclaim_unregistered_reservations(self) -> None:
        """Release reservations that never crossed the child registration boundary."""
        if self.coordinator.state_store is None:
            return
        with self._lock:
            abandoned = self._batch_reserved_children - self._registration_attempted
        if not abandoned:
            return
        self.coordinator.state_store.reclaim_confirmed(
            frozenset(abandoned),
            reasons={
                child_id: "child reservation was abandoned before registration"
                for child_id in abandoned
            },
        )

    def _reclaim_prepared_reservations(self, invocation_id: str) -> None:
        """Release this owner's preparation records after their batch attempt."""
        if self.coordinator.state_store is None:
            return
        with self._lock:
            prepared = frozenset(self._reserved_children - self._registered_children)
        if not prepared:
            return
        eligible = frozenset(
            state.child_id
            for state in self.coordinator.state_store.load()
            if state.child_id in prepared
            and (not invocation_id or state.invocation_id == invocation_id)
            and state.status == "created"
        )
        self.coordinator.state_store.reclaim_confirmed(
            eligible,
            reasons={
                child_id: "owner-local preparation was abandoned before child registration"
                for child_id in eligible
            },
        )

    def _reclaim_child(self, task: SubagentTask, outcome: Any, *, child_id: str | None = None) -> None:
        asyncio.run(self._reclaim_child_async(task, outcome, child_id=child_id))

    async def _reclaim_child_async(self, task: SubagentTask, outcome: Any, *, child_id: str | None = None) -> None:
        child_id = child_id if child_id is not None else self._child_id_for_task(task)
        if self.execution_factory is None:
            with self._lock:
                self._active_children.discard(child_id)
            return
        try:
            reclaim = getattr(self.execution_factory, "reclaim_async", None)
            if callable(reclaim):
                await reclaim(child_id, outcome)
        except Exception:
            logging.getLogger(__name__).warning('Child worker cleanup failed: %s', child_id, exc_info=True)
            raise
        finally:
            with self._lock:
                self._active_children.discard(child_id)

    def _child_id_for_task(self, task: SubagentTask) -> str:
        with self._lock:
            for child_id, task_name in reversed(tuple(self._child_tasks.items())):
                if task_name == task.task_name:
                    return child_id
        raise KeyError(f"child identity is not registered: {task.task_name}")

    def _run_child(
        self,
        task: SubagentTask,
        child_id: str,
        cancel_checker: Callable[[], bool] | None = None,
    ) -> SubagentResult:
        """Run one child with its stable identity available to the adapter."""
        if self.adapter is None:
            raise RuntimeError("subagent loop adapter is unavailable")
        context = (
            self.context_builder(task, child_id)
            if self.context_builder is not None
            else self._build_execution_context(task, child_id, cancel_checker)
            if self.execution_factory is not None
            else SubagentContext(task=task, metadata={"child_id": child_id})
        )
        if self.continuation is None:
            result = self.adapter.run(context)
        else:
            outcome = self.continuation.run(context)
            result = outcome.result or SubagentResult(
                task_name=task.task_name,
                status=outcome.status,
                content=outcome.error or "",
            )
        if (
            result.status == "completed"
            and self.coordinator.state_store is not None
            and self.completed(child_id) is None
        ):
            result = SubagentResult(
                task_name=task.task_name,
                status="failed",
                content=(
                    "subagent adapter reported completion without a durable "
                    "complete_subagent submission"
                ),
            )
        if result.task_name == task.task_name:
            return result
        return SubagentResult(
            task_name=task.task_name,
            status=result.status,
            content=result.content,
            metadata=result.metadata,
            pending_interactions=result.pending_interactions,
        )

    def _build_execution_context(
        self,
        task: SubagentTask,
        child_id: str,
        cancel_checker: Callable[[], bool] | None,
    ) -> Any:
        return self.execution_factory.build(task, child_id, cancel_checker)

    def completed(self, task_name: str) -> SubagentResult | None:
        with self._lock:
            return self._completed.get(str(task_name or '').strip())

    def active_child_ids(self) -> tuple[str, ...]:
        """Return the currently loaded child identities for lifecycle shutdown."""
        with self._lock:
            return tuple(sorted(self._active_children))

    def tool_definitions(self, actor_kind: str) -> tuple['ToolDefinition', ...]:
        """Return the owner-controlled lifecycle surface for one actor scope."""
        if not self.enabled:
            return ()
        from .tools import tool_definitions_for_actor

        return tool_definitions_for_actor(actor_kind)


def _set_event() -> Event:
    event = Event()
    event.set()
    return event


def _batch_status(children: tuple[ChildOutcome, ...]) -> str:
    if any(child.status == 'waiting' for child in children):
        return 'waiting'
    if all(child.status == 'completed' for child in children):
        return 'completed'
    if all(child.status == 'cancelled' for child in children):
        return 'cancelled'
    if all(child.status in {'failed', 'cancelled', 'reclamation_failed'} for child in children):
        return 'failed'
    return 'partial_failure'
