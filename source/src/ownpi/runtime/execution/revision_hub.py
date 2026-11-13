from __future__ import annotations

import asyncio
from threading import Event, RLock
import time
from typing import Callable


class ExecutionRevisionHub:
    def __init__(
        self,
        *,
        lock: RLock,
        revision: Callable[[], int],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._lock = lock
        self._revision = revision
        self._clock = clock
        self._cancelled = False

    def reset_locked(self) -> None:
        self._cancelled = False

    def cancel_all(self) -> None:
        with self._lock:
            self._cancelled = True

    def wait(
        self,
        *,
        after_revision: int,
        timeout_seconds: float,
        cancel_checker: Callable[[], bool] | Event | None = None,
    ) -> tuple[str, int]:
        return asyncio.run(self.wait_async(
            after_revision=after_revision, timeout_seconds=timeout_seconds,
            cancel_checker=cancel_checker,
        ))

    async def wait_async(
        self, *, after_revision: int, timeout_seconds: float,
        cancel_checker: Callable[[], bool] | Event | None = None,
    ) -> tuple[str, int]:
        deadline = self._clock() + max(0.0, timeout_seconds)
        while True:
            # The existing hub owns revision and shutdown state; no second
            # notification registry or scheduler is needed by an async waiter.
            with self._lock:
                current = self._revision()
                if current > after_revision:
                    return 'updated', current
                cancelled = (
                    cancel_checker.is_set() if isinstance(cancel_checker, Event)
                    else bool(cancel_checker()) if callable(cancel_checker) else False
                )
                if self._cancelled or cancelled:
                    return 'cancelled', current
                remaining = deadline - self._clock()
                if remaining <= 0:
                    return 'no_material_update', current
            await asyncio.sleep(min(remaining, 0.05))


__all__ = ['ExecutionRevisionHub']
