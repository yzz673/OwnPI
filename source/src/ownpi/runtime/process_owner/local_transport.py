"""Local access to the shared detached process-owner server."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import socket
import threading

from ownpi.runtime.process_owner.client import NotificationHandler, ProcessOwnerClient


async def launch_local_owner(
    *, idle_grace: float, notification_handler: NotificationHandler | None = None,
) -> tuple[ProcessOwnerClient, str]:
    if not hasattr(socket, 'AF_UNIX'):
        raise OSError('local long-running execution requires Unix sockets')
    directory = Path(tempfile.mkdtemp(prefix="opi-owner-", dir="/tmp"))
    socket_path = directory / "owner.sock"
    environment = dict(os.environ)
    source_root = str(Path(__file__).resolve().parents[3])
    environment["PYTHONPATH"] = os.pathsep.join(
        item for item in (source_root, environment.get("PYTHONPATH", "")) if item
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "ownpi.runtime.process_owner.server",
         "--unix-socket", str(socket_path), "--endpoint-manifest", str(directory / "endpoint.json"),
         "--idle-grace-seconds", str(idle_grace)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, env=environment,
    )
    try:
        async with asyncio.timeout(10):
            while not socket_path.exists():
                if process.poll() is not None:
                    raise OSError("local process owner exited before opening its socket")
                await asyncio.sleep(0.01)
        client = await ProcessOwnerClient.connect_unix_async(
            socket_path=socket_path, notification_handler=notification_handler,
        )
        def reap() -> None:
            process.wait()
            try:
                directory.rmdir()
            except OSError:
                pass

        threading.Thread(target=reap, daemon=True).start()
        return client, str(socket_path)
    except BaseException:
        if process.poll() is None:
            process.terminate()
        await asyncio.to_thread(process.wait)
        socket_path.unlink(missing_ok=True)
        (directory / 'endpoint.json').unlink(missing_ok=True)
        directory.rmdir()
        raise
