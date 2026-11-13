"""Cancellable admission to cross-event-loop MCP connection locks."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator


@asynccontextmanager
async def claim_lock(lock: Any) -> AsyncIterator[None]:
    while not lock.acquire(blocking=False):
        await asyncio.sleep(0.01)
    try:
        yield
    finally:
        lock.release()
