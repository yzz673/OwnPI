from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from threading import Lock
from typing import AsyncIterator, Callable


@dataclass
class _Claim:
    shared: bool
    ready: asyncio.Future[None]
    active: bool = False


class ToolInvocationConcurrencyGate:
    def __init__(self) -> None:
        # Synchronous outer workflow owners may drive different event loops.
        # This lock protects only counters/queue, never an invocation or wait.
        self._lock = Lock()
        self._active_shared = 0
        self._exclusive_active = False
        self._waiters: list[_Claim] = []

    @asynccontextmanager
    async def claim(
        self, *, concurrency_safe: bool,
        cancel_checker: Callable[[], bool] | None = None,
    ) -> AsyncIterator[None]:
        if cancel_checker is not None and cancel_checker():
            raise asyncio.CancelledError('Tool invocation cancelled before execution gate')
        claim = _Claim(concurrency_safe, asyncio.get_running_loop().create_future())
        with self._lock:
            self._waiters.append(claim)
            self._admit()
        try:
            while not claim.ready.done():
                if cancel_checker is not None and cancel_checker():
                    raise asyncio.CancelledError('Tool invocation cancelled at execution gate')
                await asyncio.wait((claim.ready,), timeout=0.05)
            await claim.ready
            if cancel_checker is not None and cancel_checker():
                raise asyncio.CancelledError('Tool invocation cancelled at execution gate')
            yield
        finally:
            with self._lock:
                if claim.active:
                    if claim.shared:
                        self._active_shared -= 1
                    else:
                        self._exclusive_active = False
                else:
                    self._waiters.remove(claim)
                claim.ready.cancel()
                self._admit()

    def _admit(self) -> None:
        if self._exclusive_active:
            return
        while self._waiters:
            claim = self._waiters[0]
            if not claim.shared and self._active_shared:
                return
            self._waiters.pop(0)
            claim.active = True
            if claim.shared:
                self._active_shared += 1
            else:
                self._exclusive_active = True
            claim.ready.get_loop().call_soon_threadsafe(self._wake, claim.ready)
            if not claim.shared:
                return

    @staticmethod
    def _wake(future: asyncio.Future[None]) -> None:
        if not future.done():
            future.set_result(None)


__all__ = ['ToolInvocationConcurrencyGate']
