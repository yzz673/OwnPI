"""Awaitable batch coordination over the existing child execution owner."""

from __future__ import annotations

import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable, Iterable, Mapping
from uuid import uuid4

from ownpi.controller.runtime_control_signal import RuntimeControlRequested

from .contracts import SubagentResult, SubagentTask
from .live_state import LiveSubagentStateStore
from .state import ChildState
from ownpi.schemas.runtime_human_clarification import HumanClarificationSession


@dataclass(frozen=True)
class ChildOutcome:
    task_name: str
    status: str
    result: SubagentResult | None = None
    error: str | None = None
    reclaimed: bool = False
    child_id: str = ""
    feedback_path: str | None = None
    feedback_error: str | None = None
    feedback_submitted: bool = False
    feedback_source: dict[str, str] | None = None


@dataclass(frozen=True)
class BatchResult:
    status: str
    children: tuple[ChildOutcome, ...]

    @property
    def pending_interactions(self) -> tuple[HumanClarificationSession, ...]:
        return tuple(session for child in self.children if child.result is not None
                     for session in child.result.pending_interactions)

    @property
    def failure_class(self) -> str:
        return "subagent_batch_failure" if self.status in {"failed", "partial_failure"} else ""

    @property
    def failure_message(self) -> str:
        return "; ".join(
            item.error or ""
            for item in self.children
            if item.error
        )

    def to_dict(self) -> dict[str, object]:
        return {"feedback_delivery_version": 1, "status": self.status, "children": [
            {"task_name": item.task_name, "status": item.status,
             "content": item.result.content if item.result else "",
             "error": item.error, "reclaimed": item.reclaimed,
             "child_id": item.child_id, "feedback_path": item.feedback_path,
             "feedback_available": bool(item.feedback_submitted and item.feedback_path and not item.feedback_error),
             "feedback_source": item.feedback_source,
             "feedback_submitted": item.feedback_submitted,
             "feedback_error": item.feedback_error}
            for item in self.children
        ]}


@dataclass
class BatchCoordinator:
    """Run independent children in parallel and return only after reclamation."""

    max_workers: int | None = None
    state_store: LiveSubagentStateStore | None = None
    completion_events: Any | None = None
    def run(
        self,
        tasks: Iterable[SubagentTask],
        worker: Callable[[SubagentTask], SubagentResult],
        reclaimer: Callable[[SubagentTask, ChildOutcome], Awaitable[None]] | None = None,
        child_registered: Callable[[str, str], None] | None = None,
        worker_with_child_id: Callable[[SubagentTask, str], SubagentResult] | None = None,
        child_reserved: Callable[[str, str], None] | None = None,
        cancel_checker: Callable[[], bool] | None = None,
        reusable_child_ids: Mapping[str, str] | None = None,
        invocation_id: str = "",
    ) -> BatchResult:
        return asyncio.run(self.run_async(
            tasks, worker, reclaimer, child_registered, worker_with_child_id,
            child_reserved, cancel_checker, reusable_child_ids,
            invocation_id,
        ))

    async def run_async(
        self,
        tasks: Iterable[SubagentTask],
        worker: Callable[[SubagentTask], SubagentResult],
        reclaimer: Callable[[SubagentTask, ChildOutcome], Awaitable[None]] | None = None,
        child_registered: Callable[[str, str], None] | None = None,
        worker_with_child_id: Callable[[SubagentTask, str], SubagentResult] | None = None,
        child_reserved: Callable[[str, str], None] | None = None,
        cancel_checker: Callable[[], bool] | None = None,
        reusable_child_ids: Mapping[str, str] | None = None,
        invocation_id: str = "",
        notify_cancel: Callable[[], None] | None = None,
    ) -> BatchResult:
        task_list = tuple(tasks)
        self._validate_tasks(task_list)
        if _is_cancelled(cancel_checker):
            return BatchResult(
                status="cancelled",
                children=tuple(
                    ChildOutcome(task_name=task.task_name, status="cancelled", error="batch cancelled before start")
                    for task in task_list
                ),
            )
        batch_id = uuid4().hex
        reusable_child_ids = dict(reusable_child_ids or {})
        child_ids = {
            task.task_name: reusable_child_ids.get(task.task_name, uuid4().hex)
            for task in task_list
        }
        state_by_name = {
            task.task_name: ChildState(
                batch_id, task.task_name, child_ids[task.task_name], "created",
                task_message=task.message, invocation_id=invocation_id,
            )
            for task in task_list
        }
        if len(set(child_ids.values())) != len(task_list):
            raise ValueError("duplicate child identity in batch")
        if self.state_store is not None:
            reserve_kwargs = {}
            if reusable_child_ids:
                reserve_kwargs["allow_reserved_ids"] = frozenset(
                    reusable_child_ids.values()
                )
            self.state_store.reserve(tuple(state_by_name.values()), **reserve_kwargs)
        if self.completion_events is not None:
            self.completion_events.register(tuple(state_by_name.values()))
        if child_reserved is not None:
            for task in task_list:
                child_reserved(child_ids[task.task_name], task.task_name)
        reclaimed: dict[str, ChildOutcome] = {}
        registration_attempted: set[str] = set()
        startup_phase = "child registration"
        try:
            if child_registered is not None:
                for task in task_list:
                    registration_attempted.add(task.task_name)
                    child_registered(child_ids[task.task_name], task.task_name)
            startup_phase = "state persistence"
            self._persist(tuple(state_by_name.values()))
        except Exception as exc:
            # No worker has started yet. Release every reservation that was
            # registered with the owner, then expose a failed batch instead
            # of leaking active child identities through an exception.
            failure = f"{startup_phase} failed before child start: {type(exc).__name__}: {exc}"
            startup_cancel: asyncio.CancelledError | None = None
            for task in task_list:
                outcome = ChildOutcome(
                    task_name=task.task_name,
                    status="failed",
                    error=failure,
                )
                try:
                    if reclaimer is not None and (
                        child_registered is None or task.task_name in registration_attempted
                    ):
                        cleanup = asyncio.ensure_future(reclaimer(task, outcome))
                        while True:
                            try:
                                await asyncio.shield(cleanup)
                                break
                            except asyncio.CancelledError as cancel_exc:
                                if cleanup.cancelled():
                                    raise RuntimeError("child reclamation was cancelled") from None
                                # Finish all registered children before propagating cancellation.
                                if startup_cancel is None:
                                    startup_cancel = cancel_exc
                    outcome = replace(outcome, reclaimed=True)
                except RuntimeControlRequested:
                    raise
                except Exception as reclaim_exc:  # noqa: BLE001
                    outcome = replace(
                        outcome,
                        error=f"{failure}; reclamation failed: {type(reclaim_exc).__name__}: {reclaim_exc}",
                    )
                reclaimed[task.task_name] = outcome
                state_by_name[task.task_name] = ChildState(
                    batch_id,
                    task.task_name,
                    child_ids[task.task_name],
                    "reclaimed" if outcome.reclaimed else outcome.status,
                    error=outcome.error,
                    task_message=task.message,
                    terminal_status="failed",
                    invocation_id=invocation_id,
                )
                try:
                    self._persist((state_by_name[task.task_name],))
                except Exception as persist_exc:
                    # The original state write remains the durable recovery
                    # record when the filesystem is still unavailable.
                    reclaimed[task.task_name] = replace(
                        outcome, status="reclamation_failed", reclaimed=False,
                        error=(f"{outcome.error}; state persistence failed after startup cleanup: "
                               f"{type(persist_exc).__name__}: {persist_exc}"),
                    )
            if startup_cancel is not None:
                raise startup_cancel
            return BatchResult(
                status="failed",
                children=tuple(replace(reclaimed[task.task_name], child_id=child_ids[task.task_name]) for task in task_list),
            )
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            def start_child(task: SubagentTask, child_id: str) -> SubagentResult:
                # Only a worker that actually crosses this boundary is marked
                # running. Tasks queued behind max_workers remain created and
                # can be safely reclaimed/replayed after a parent crash.
                try:
                    self._persist((replace(state_by_name[task.task_name], status="running"),))
                except Exception as exc:
                    raise RuntimeError(
                        "state persistence failed before child start: "
                        f"{type(exc).__name__}: {exc}"
                    ) from exc
                if worker_with_child_id is not None:
                    return worker_with_child_id(task, child_id)
                return worker(task)

            futures: dict[str, Future[SubagentResult]] = {
                task.task_name: pool.submit(
                    start_child,
                    task,
                    child_ids[task.task_name],
                )
                for task in task_list
            }
            tasks_by_future = {futures[task.task_name]: task for task in task_list}
            cancellation_requested = False
            cancellation_failure: str | None = None
            pending = set(tasks_by_future)
            while pending:
                try:
                    cancelled = cancellation_requested or _is_cancelled(cancel_checker)
                except Exception as exc:
                    cancellation_failure = f"cancellation probe failed: {type(exc).__name__}: {exc}"
                    cancelled = True
                if cancelled and not cancellation_requested:
                    cancellation_requested = True
                    if notify_cancel is not None:
                        notify_cancel()
                    for unfinished in pending:
                        unfinished.cancel()
                completed = next((item for item in pending if item.done()), None)
                if completed is None:
                    try:
                        await asyncio.sleep(0.01)
                    except asyncio.CancelledError:
                        if not cancellation_requested:
                            cancellation_requested = True
                            if notify_cancel is not None:
                                notify_cancel()
                            for unfinished in pending:
                                unfinished.cancel()
                    continue
                future = completed
                pending.remove(future)
                task = tasks_by_future[future]
                cancelled = cancellation_requested
                try:
                    result = future.result()
                    outcome = ChildOutcome(
                        task_name=task.task_name,
                        status="cancelled" if cancelled and result.status != "completed" else result.status,
                        result=result,
                        error="batch cancelled" if cancelled and result.status != "completed" else None,
                    )
                except RuntimeControlRequested:
                    raise
                except Exception as exc:  # noqa: BLE001 - child failure is projected
                    outcome = ChildOutcome(
                        task_name=task.task_name,
                        status="cancelled" if cancelled else "failed",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                if cancellation_failure is not None:
                    outcome = replace(
                        outcome,
                        error="; ".join(filter(None, (outcome.error, cancellation_failure))),
                    )
                state_by_name[task.task_name] = ChildState(
                    batch_id, task.task_name, child_ids[task.task_name], outcome.status,
                    outcome.result.content if outcome.result else "", outcome.error,
                    task_message=task.message, terminal_status='' if outcome.status == 'waiting' else outcome.status,
                    invocation_id=invocation_id,
                )
                try:
                    self._persist((state_by_name[task.task_name],))
                except Exception as exc:
                    outcome = ChildOutcome(
                        task_name=task.task_name,
                        status="failed",
                        result=outcome.result,
                        error=(
                            f"state persistence failed after child execution: "
                            f"{type(exc).__name__}: {exc}"
                        ),
                    )

                try:
                    if reclaimer is not None:
                        cleanup = asyncio.ensure_future(reclaimer(task, outcome))
                        while True:
                            try:
                                await asyncio.shield(cleanup)
                                break
                            except asyncio.CancelledError:
                                if cleanup.cancelled():
                                    raise RuntimeError("child reclamation was cancelled") from None
                                cancellation_requested = True
                                if notify_cancel is not None:
                                    notify_cancel()
                                for unfinished in pending:
                                    unfinished.cancel()
                    reclaimed[task.task_name] = ChildOutcome(
                        **{**outcome.__dict__, "reclaimed": True}
                    )
                except RuntimeControlRequested:
                    raise
                except Exception as exc:  # noqa: BLE001 - preserve cleanup failure
                    reclaimed[task.task_name] = replace(
                        outcome,
                        error='; '.join(filter(None, (outcome.error, f'cleanup failed: {type(exc).__name__}: {exc}'))),
                    )
                final = reclaimed[task.task_name]
                state_by_name[task.task_name] = ChildState(
                    batch_id, task.task_name, child_ids[task.task_name],
                    "waiting" if outcome.status == 'waiting' else "reclaimed" if final.reclaimed else final.status,
                    final.result.content if final.result else "", final.error,
                    task_message=task.message, terminal_status='' if outcome.status == 'waiting' else outcome.status,
                    invocation_id=invocation_id,
                )
                try:
                    self._persist((state_by_name[task.task_name],))
                except Exception as exc:
                    reclaimed[task.task_name] = replace(
                        final,
                        error=(
                            f"state persistence failed after reclamation: "
                            f"{type(exc).__name__}: {exc}"
                        ),
                    )

        ordered = tuple(replace(reclaimed[task.task_name], child_id=child_ids[task.task_name]) for task in task_list)
        if cancellation_failure is not None:
            status = "failed"
        elif any(child.status == 'waiting' for child in ordered):
            status = 'waiting'
        elif all(child.status == "completed" for child in ordered):
            status = "completed"
        elif all(child.status == "cancelled" for child in ordered):
            status = "cancelled"
        elif all(child.status in {"failed", "cancelled", "reclamation_failed"} for child in ordered):
            status = "failed"
        else:
            status = "partial_failure"
        return BatchResult(status=status, children=ordered)

    def _persist(self, states: tuple[ChildState, ...]) -> None:
        if self.state_store is not None:
            try:
                self.state_store.save(states)
            except Exception:
                # State writes are atomic. A single transient filesystem
                # failure must not strand registered children before the
                # coordinator can reach their reclamation boundary.
                self.state_store.save(states)

    @staticmethod
    def _validate_tasks(tasks: tuple[SubagentTask, ...]) -> None:
        if not tasks:
            raise ValueError("subagent batch must contain at least one task")
        names = [task.task_name for task in tasks]
        if len(set(names)) != len(names):
            raise ValueError("subagent task_name values must be unique within a batch")


def _is_cancelled(cancel_checker: Callable[[], bool] | None) -> bool:
    return bool(cancel_checker()) if cancel_checker is not None else False
