"""Await reaping of an already-owned Popen process, without detaching cleanup."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from typing import Any


async def reap_owned_process(process: subprocess.Popen[Any]) -> None:
    async def reap() -> None:
        for number in (signal.SIGTERM, signal.SIGKILL):
            if process.poll() is not None:
                return
            try:
                if os.name != 'nt':
                    os.killpg(os.getpgid(process.pid), number)
                elif number == signal.SIGTERM:
                    process.terminate()
                else:
                    process.kill()
            except ProcessLookupError:
                pass
            except OSError:
                process.terminate() if number == signal.SIGTERM else process.kill()
            deadline = asyncio.get_running_loop().time() + 1
            while process.poll() is None:
                if asyncio.get_running_loop().time() >= deadline:
                    break
                await asyncio.sleep(0.01)
        if process.poll() is None:
            raise subprocess.TimeoutExpired(process.args, 2)

    cleanup = asyncio.create_task(reap())
    cancelled = False
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            cancelled = True
    cleanup.result()
    if cancelled:
        raise asyncio.CancelledError
