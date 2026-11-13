from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
import random
from threading import Condition, RLock, Thread
import time
from typing import Callable

from ownpi.runtime.limits.recovery import (
    DEFAULT_EXECUTION_SCHEDULER_STOP_TIMEOUT_SECONDS,
)
from ownpi.runtime.limits.resources import (
    DEFAULT_EXECUTION_MONITOR_WORKERS,
    DEFAULT_EXECUTION_POLL_INTERVAL_SECONDS,
    MAX_EXECUTION_FAILURE_BACKOFF_EXPONENT,
    MAX_EXECUTION_POLL_INTERVAL_SECONDS,
)


@dataclass(frozen=True)
class PollSchedule:
    next_poll_at: float
    failure_count: int = 0
    in_flight: bool = False


@dataclass(frozen=True)
class SchedulerPollResult:
    terminal: bool
    succeeded: bool


class ExecutionScheduler:
    def __init__(
        self,
        *,
        lock: RLock,
        poll: Callable[[str, int, int], SchedulerPollResult],
        clock: Callable[[], float] = time.monotonic,
        jitter: Callable[[float], float] | None = None,
        condition_factory: Callable[[RLock], Condition] = Condition,
        base_interval_seconds: float = DEFAULT_EXECUTION_POLL_INTERVAL_SECONDS,
        max_interval_seconds: float = MAX_EXECUTION_POLL_INTERVAL_SECONDS,
        max_workers: int = DEFAULT_EXECUTION_MONITOR_WORKERS,
    ) -> None:
        self._condition = condition_factory(lock)
        self._poll = poll
        self._clock = clock
        self._jitter = jitter or (lambda interval: random.uniform(0.0, interval * 0.1))
        self._base_interval = max(0.01, base_interval_seconds)
        self._max_interval = max(self._base_interval, max_interval_seconds)
        self._max_workers = max(1, max_workers)
        self._schedule: dict[str, PollSchedule] = {}
        self._worker: Thread | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._futures: set[Future[None]] = set()
        self._manual_in_flight = 0
        self._generation = 0
        self._stopping = False

    @property
    def generation(self) -> int:
        with self._condition:
            return self._generation

    def start(self) -> int:
        with self._condition:
            if self._worker is not None and self._worker.is_alive():
                return self._generation
            self._prune_futures_locked()
            if self._futures or self._manual_in_flight:
                raise RuntimeError(
                    'previous execution scheduler generation is still shutting down'
                )
            self._executor = None
            self._stopping = False
            self._generation += 1
            self._executor = ThreadPoolExecutor(
                max_workers=self._max_workers,
                thread_name_prefix='execution-monitor',
            )
            self._worker = Thread(
                target=self._run,
                name='execution-scheduler',
                daemon=True,
            )
            self._worker.start()
            return self._generation

    def stop(
        self,
        *,
        timeout_seconds: float = DEFAULT_EXECUTION_SCHEDULER_STOP_TIMEOUT_SECONDS,
    ) -> bool:
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        with self._condition:
            self._stopping = True
            self._generation += 1
            self._condition.notify_all()
            worker = self._worker
            executor = self._executor
        if worker is not None:
            worker.join(timeout=max(0.0, deadline - time.monotonic()))
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        with self._condition:
            futures = tuple(self._futures)
        _, pending = (
            wait(futures, timeout=max(0.0, deadline - time.monotonic()))
            if futures
            else (set(), set())
        )
        with self._condition:
            while self._manual_in_flight and time.monotonic() < deadline:
                self._condition.wait(timeout=max(0.0, deadline - time.monotonic()))
            worker_done = worker is None or not worker.is_alive()
            completed = not pending and not self._manual_in_flight and worker_done
            if completed:
                self._prune_futures_locked()
                self._worker = None
                self._executor = None
            return completed

    def schedule(self, execution_id: str, *, immediate: bool = True) -> None:
        normalized = str(execution_id or '').strip()
        if not normalized:
            raise ValueError('execution_id is required')
        with self._condition:
            existing = self._schedule.get(normalized)
            if existing is not None:
                if immediate and not existing.in_flight:
                    self._schedule[normalized] = PollSchedule(
                        next_poll_at=min(existing.next_poll_at, self._clock()),
                        failure_count=existing.failure_count,
                    )
                self._condition.notify_all()
                return
            self._schedule[normalized] = PollSchedule(
                next_poll_at=self._clock() if immediate else self._next_poll_at(0),
            )
            self._condition.notify_all()

    def unschedule(self, execution_id: str) -> None:
        with self._condition:
            self._schedule.pop(execution_id, None)
            self._condition.notify_all()

    def schedule_now(self, execution_id: str) -> None:
        self.schedule(execution_id, immediate=True)

    def rebuild(self, execution_ids: tuple[str, ...]) -> None:
        with self._condition:
            self._schedule.clear()
            now = self._clock()
            for execution_id in dict.fromkeys(execution_ids):
                if execution_id:
                    self._schedule[execution_id] = PollSchedule(next_poll_at=now)
            self._condition.notify_all()

    def snapshot(self) -> dict[str, PollSchedule]:
        with self._condition:
            return dict(self._schedule)

    def run_due_once(self, *, now: float | None = None) -> tuple[str, ...]:
        with self._condition:
            due = self._claim_due_locked(self._clock() if now is None else now)
            generation = self._generation
            self._manual_in_flight += len(due)
        for execution_id in due:
            self._poll_one(execution_id, generation, manual=True)
        return due

    def _run(self) -> None:
        while True:
            with self._condition:
                if self._stopping:
                    return
                now = self._clock()
                due = self._claim_due_locked(now)
                if not due:
                    timeout = self._next_timeout_locked(now)
                    self._condition.wait(timeout=timeout)
                    continue
                executor = self._executor
                generation = self._generation
                if executor is None:
                    return
                self._prune_futures_locked()
                for execution_id in due:
                    future = executor.submit(
                        self._poll_one,
                        execution_id,
                        generation,
                        False,
                    )
                    self._futures.add(future)

    def _claim_due_locked(self, now: float) -> tuple[str, ...]:
        due: list[str] = []
        for execution_id, schedule in tuple(self._schedule.items()):
            if schedule.in_flight or schedule.next_poll_at > now:
                continue
            self._schedule[execution_id] = PollSchedule(
                next_poll_at=schedule.next_poll_at,
                failure_count=schedule.failure_count,
                in_flight=True,
            )
            due.append(execution_id)
        return tuple(due)

    def _poll_one(self, execution_id: str, generation: int, manual: bool) -> None:
        with self._condition:
            schedule = self._schedule.get(execution_id)
            if schedule is None:
                if manual:
                    self._manual_in_flight -= 1
                    self._condition.notify_all()
                return
            poll_attempt = schedule.failure_count + 1
        try:
            result = self._poll(execution_id, poll_attempt, generation)
        except Exception:
            result = SchedulerPollResult(terminal=False, succeeded=False)
        with self._condition:
            schedule = self._schedule.get(execution_id)
            if (
                schedule is not None
                and generation == self._generation
                and not self._stopping
            ):
                if result.terminal:
                    self._schedule.pop(execution_id, None)
                else:
                    failures = 0 if result.succeeded else schedule.failure_count + 1
                    self._schedule[execution_id] = PollSchedule(
                        next_poll_at=self._next_poll_at(failures),
                        failure_count=failures,
                    )
            if manual:
                self._manual_in_flight -= 1
            self._condition.notify_all()

    def _prune_futures_locked(self) -> None:
        self._futures = {future for future in self._futures if not future.done()}

    def _next_poll_at(self, failure_count: int) -> float:
        interval = min(
            self._max_interval,
            self._base_interval
            * (
                2
                ** min(
                    max(0, failure_count),
                    MAX_EXECUTION_FAILURE_BACKOFF_EXPONENT,
                )
            ),
        )
        return self._clock() + interval + max(0.0, self._jitter(interval))

    def _next_timeout_locked(self, now: float) -> float | None:
        pending = [
            schedule.next_poll_at
            for schedule in self._schedule.values()
            if not schedule.in_flight
        ]
        if not pending:
            return None
        return max(0.0, min(pending) - now)


__all__ = ['ExecutionScheduler', 'PollSchedule', 'SchedulerPollResult']
