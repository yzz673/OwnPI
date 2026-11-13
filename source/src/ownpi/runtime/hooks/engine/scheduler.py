from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock
from typing import Callable

from ownpi.runtime.hooks.types import HookRunReceipt


DEFAULT_ASYNC_HOOK_CONCURRENCY = 4


class HookScheduler:
    def __init__(self, max_workers: int = DEFAULT_ASYNC_HOOK_CONCURRENCY) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max(1, max_workers), thread_name_prefix="hook")
        self._lock = Lock()
        self._futures: set[Future[HookRunReceipt]] = set()

    def submit(self, operation: Callable[[], HookRunReceipt]) -> None:
        future = self._executor.submit(operation)
        with self._lock:
            self._futures.add(future)
        future.add_done_callback(self._discard)

    def close(self, *, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=not wait)
        with self._lock:
            self._futures.clear()

    def _discard(self, future: Future[HookRunReceipt]) -> None:
        with self._lock:
            self._futures.discard(future)
