from __future__ import annotations

from collections.abc import Callable, Iterator
from contextvars import copy_context
from dataclasses import dataclass, field
from queue import Empty, Full, Queue
from threading import Event, Thread
from typing import Any

from ownpi.llm.streaming import CanonicalResponseEvent


@dataclass(frozen=True)
class NativeStreamBridgeItem:
    event: CanonicalResponseEvent | None = None
    result: Any = None
    error: BaseException | None = None
    terminal: bool = False
    acknowledged: Event | None = field(default=None, repr=False, compare=False)


def stream_callback_operation(
    operation: Callable[[Callable[[CanonicalResponseEvent], None]], Any],
    *,
    queue_size: int = 128,
) -> Iterator[NativeStreamBridgeItem]:
    queue: Queue[NativeStreamBridgeItem] = Queue(maxsize=max(1, queue_size))
    stopped = Event()
    context = copy_context()

    def put(item: NativeStreamBridgeItem) -> bool:
        while not stopped.is_set():
            try:
                queue.put(item, timeout=0.1)
                return True
            except Full:
                continue
        return False

    def emit(event: CanonicalResponseEvent) -> None:
        acknowledged = Event()
        if not put(NativeStreamBridgeItem(event=event, acknowledged=acknowledged)):
            raise RuntimeError('native stream consumer stopped')
        while not acknowledged.wait(timeout=0.1):
            if stopped.is_set():
                raise RuntimeError('native stream consumer stopped')
        if stopped.is_set():
            raise RuntimeError('native stream consumer stopped')

    def worker() -> None:
        try:
            result = context.run(operation, emit)
        except BaseException as exc:
            if not stopped.is_set():
                put(NativeStreamBridgeItem(error=exc, terminal=True))
        else:
            if not stopped.is_set():
                put(NativeStreamBridgeItem(result=result, terminal=True))

    thread = Thread(target=worker, name='native-stream-bridge', daemon=True)
    thread.start()
    pending_acknowledgment: Event | None = None
    try:
        while True:
            if pending_acknowledgment is not None:
                pending_acknowledgment.set()
                pending_acknowledgment = None
            try:
                item = queue.get(timeout=0.1)
            except Empty:
                if thread.is_alive():
                    continue
                raise RuntimeError('native stream worker exited without a terminal result')
            if item.error is not None:
                raise item.error
            pending_acknowledgment = item.acknowledged
            yield item
            if item.terminal:
                return
    finally:
        stopped.set()
        if pending_acknowledgment is not None:
            pending_acknowledgment.set()
        thread.join(timeout=1.0)


__all__ = ['NativeStreamBridgeItem', 'stream_callback_operation']
