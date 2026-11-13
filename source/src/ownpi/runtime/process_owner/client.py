from __future__ import annotations

import asyncio
from collections import deque
from concurrent.futures import CancelledError
import json
import os
import io
from pathlib import Path
import socket
import select
import signal
import subprocess
import threading
import time
from typing import Any, Awaitable, BinaryIO, Callable, Iterator, TypeVar

from ownpi.runtime.process_owner.protocol import (
    DEFAULT_CAPABILITIES,
    PACKAGE_VERSION,
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    SANDBOX_DIAGNOSTICS_CAPABILITY,
    ProcessOwnerProtocolError,
    ProtocolVersion,
    require_identifier,
    require_mapping,
    reject_unknown_fields,
    require_non_empty_string,
    require_string_list,
)


NotificationHandler = Callable[[str, dict[str, Any]], None]
_T = TypeVar('_T')


async def complete_on_cancel(operation: Awaitable[_T]) -> _T:
    """Finish an owned acquisition/cleanup before propagating cancellation."""
    task = asyncio.ensure_future(operation)
    cancelled: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            cancelled = error
        except Exception:
            break
    if cancelled is not None:
        try:
            task.result()
        except BaseException as error:
            cancelled.add_note(f'Owned operation cleanup failed: {type(error).__name__}: {error}')
        raise cancelled
    return task.result()


class ProcessOwnerClient:
    def __init__(
        self,
        *,
        reader: BinaryIO,
        writer: BinaryIO,
        process: subprocess.Popen[bytes] | None = None,
        notification_handler: NotificationHandler | None = None,
        transport_closer: Callable[[], None] | None = None,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._process = process
        self._notification_handler = notification_handler
        self._transport_closer = transport_closer
        self._write_lock = threading.Lock()
        self._condition = threading.Condition()
        self._next_request_id = 1
        self._responses: dict[int, dict[str, Any]] = {}
        self._pending_request_ids: set[int] = set()
        self._notifications: deque[dict[str, Any]] = deque()
        self._closed = False
        self.session_id = ""
        self.capabilities: frozenset[str] = frozenset()
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._closed

    @classmethod
    async def connect_unix_async(
        cls,
        *,
        socket_path: str | Path,
        resume_session_id: str = "",
        notification_handler: NotificationHandler | None = None,
    ) -> "ProcessOwnerClient":
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.setblocking(False)
        try:
            await asyncio.get_running_loop().sock_connect(connection, str(Path(socket_path)))
        except BaseException:
            connection.close()
            raise
        connection.setblocking(True)
        reader = connection.makefile("rb")
        writer = connection.makefile("wb")

        def _close_connection() -> None:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()

        client = cls(
            reader=reader,
            writer=writer,
            notification_handler=notification_handler,
            transport_closer=_close_connection,
        )
        try:
            await client.initialize_async(resume_session_id=resume_session_id)
        except BaseException:
            await complete_on_cancel(client.close_async())
            raise
        return client

    async def initialize_async(self, *, resume_session_id: str = "") -> dict[str, Any]:
        result = await self.request_async(
            "initialize",
            {
                "protocol_version": {"major": PROTOCOL_MAJOR, "minor": PROTOCOL_MINOR},
                "capabilities": sorted(DEFAULT_CAPABILITIES),
                "resume_session_id": resume_session_id,
                "package_version": PACKAGE_VERSION,
            },
        )
        reject_unknown_fields(
            result,
            allowed={
                "session_id",
                "protocol_version",
                "capabilities",
                "package_version",
            },
            name="initialize result",
        )
        version = ProtocolVersion.from_value(result.get("protocol_version"))
        version.negotiate(ProtocolVersion(major=PROTOCOL_MAJOR, minor=PROTOCOL_MINOR))
        self.session_id = require_identifier(
            result.get("session_id"), name="session_id"
        )
        capabilities = set(
            require_string_list(result.get("capabilities"), name="capabilities")
        )
        missing = sorted(set(DEFAULT_CAPABILITIES) - capabilities)
        if missing:
            raise ProcessOwnerProtocolError(f"server missing capabilities: {missing}")
        self.capabilities = frozenset(capabilities)
        package_version = require_non_empty_string(
            result.get("package_version"),
            name="package_version",
            max_length=200,
        )
        if package_version != PACKAGE_VERSION:
            raise ProcessOwnerProtocolError(
                f"server package version mismatch: server={package_version} client={PACKAGE_VERSION}"
            )
        await self.request_async("initialized", {})
        return result

    async def request_async(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout_seconds: float = 30.0,
        cancel_checker: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        if cancel_checker is not None and cancel_checker():
            raise CancelledError(f'Process owner wait cancelled: {method}')
        with self._condition:
            if self._closed:
                raise ProcessOwnerProtocolError("process owner client is closed")
            request_id = self._next_request_id
            self._next_request_id += 1
            self._pending_request_ids.add(request_id)
        if method == 'process/start' and SANDBOX_DIAGNOSTICS_CAPABILITY not in self.capabilities:
            params = {key: value for key, value in params.items() if key != 'sandbox_backend'}
        payload = {"id": request_id, "method": method, "params": params}
        try:
            encoded = (
                json.dumps(payload, separators=(",", ":"), ensure_ascii=True) + "\n"
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            with self._condition:
                self._pending_request_ids.discard(request_id)
            if method == 'process/start':
                raise ProcessOwnerProtocolError(
                    'process owner request could not be encoded', start_outcome='not_created',
                ) from exc
            raise
        deadline = time.monotonic() + max(0.01, timeout_seconds)
        try:
            await self._send_request(encoded, deadline=deadline)
            while True:
                with self._condition:
                    response = self._responses.pop(request_id, None)
                    closed = self._closed
                if response is not None:
                    break
                if closed:
                    raise ProcessOwnerProtocolError("process owner connection closed")
                if cancel_checker is not None and cancel_checker():
                    raise CancelledError(f'Process owner wait cancelled: {method}')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"process owner request timed out: {method}")
                await asyncio.sleep(min(remaining, 0.01))
        finally:
            with self._condition:
                self._pending_request_ids.discard(request_id)
                self._responses.pop(request_id, None)
        if "error" in response:
            error_value = response.get("error")
            error = error_value if isinstance(error_value, dict) else {}
            raise ProcessOwnerProtocolError(
                str(error.get("message", "process owner request failed")),
                start_outcome=(
                    'not_created' if method == 'process/start'
                    and error.get('start_outcome') == 'not_created' else ''
                ),
            )
        result = response.get("result")
        return dict(result) if isinstance(result, dict) else {}

    async def start_async(
        self,
        *,
        process_id: str,
        argv: list[str],
        cwd: str,
        env: dict[str, str] | None = None,
        tty: bool = False,
        pipe_stdin: bool = False,
        pty_columns: int | None = None,
        pty_rows: int | None = None,
        timeout_seconds: int = 0,
        stdout_path: str = "",
        stderr_path: str = "",
        sandbox_backend: str = "unknown",
    ) -> dict[str, Any]:
        return await self.request_async(
            "process/start",
            {
                "process_id": process_id,
                "argv": argv,
                "cwd": str(Path(cwd)),
                "env": dict(env or {}),
                "tty": tty,
                "pipe_stdin": pipe_stdin,
                "pty_columns": pty_columns,
                "pty_rows": pty_rows,
                "timeout_seconds": max(0, int(timeout_seconds)),
                "stdout_path": str(stdout_path or ""),
                "stderr_path": str(stderr_path or ""),
                "sandbox_backend": sandbox_backend,
            },
        )

    async def read_async(
        self,
        *,
        process_id: str,
        after_seq: int = 0,
        max_bytes: int = 65536,
        wait_ms: int = 0,
        cancel_checker: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        return await self.request_async(
            "process/read",
            {
                "process_id": process_id,
                "after_seq": after_seq,
                "max_bytes": max_bytes,
                "wait_ms": wait_ms,
            },
            timeout_seconds=max(30.0, wait_ms / 1000.0 + 5.0),
            cancel_checker=cancel_checker,
        )

    async def write_async(self, *, process_id: str, chunk: str, write_id: str) -> dict[str, Any]:
        return await self.request_async(
            "process/write",
            {"process_id": process_id, "chunk": chunk, "write_id": write_id},
        )

    async def signal_async(self, *, process_id: str, signal_number: int) -> dict[str, Any]:
        return await self.request_async(
            "process/signal",
            {"process_id": process_id, "signal": signal_number},
        )

    async def terminate_async(self, *, process_id: str, grace_ms: int = 2000) -> dict[str, Any]:
        return await self.request_async(
            "process/terminate",
            {"process_id": process_id, "grace_ms": grace_ms},
        )

    async def release_async(self, *, process_id: str, final_seq: int) -> dict[str, Any]:
        return await self.request_async(
            "process/release",
            {"process_id": process_id, "final_seq": final_seq},
        )

    async def drain_and_shutdown_async(self, *, grace_ms: int = 2000) -> dict[str, Any]:
        return await self.request_async("session/drain_and_shutdown", {"grace_ms": grace_ms})

    async def _send_request(self, encoded: bytes, *, deadline: float) -> None:
        while not self._write_lock.acquire(blocking=False):
            if time.monotonic() >= deadline:
                raise TimeoutError('process owner send lock timed out')
            await asyncio.sleep(0.01)
        offset = 0
        cancelled: asyncio.CancelledError | None = None
        try:
            descriptor = self._writer.fileno()
            os.set_blocking(descriptor, False)
            while offset < len(encoded):
                try:
                    written = os.write(descriptor, encoded[offset:])
                    if written == 0:
                        raise BrokenPipeError('process owner transport write returned zero')
                    offset += written
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        # A partial JSON frame cannot be reused by the next request.
                        if offset:
                            self._writer.close()
                            with self._condition:
                                self._closed = True
                                self._condition.notify_all()
                        raise TimeoutError('process owner transport send timed out')
                    try:
                        await asyncio.sleep(0.01)
                    except asyncio.CancelledError as error:
                        if offset == 0:
                            raise
                        # Finish only this already-started frame before releasing the lock.
                        cancelled = error
            if cancelled is not None:
                raise cancelled
        finally:
            self._write_lock.release()

    @classmethod
    def connect_unix(cls, **kwargs: Any) -> "ProcessOwnerClient":
        return asyncio.run(cls.connect_unix_async(**kwargs))

    def initialize(self, **kwargs: Any) -> dict[str, Any]:
        return asyncio.run(self.initialize_async(**kwargs))

    def request(self, method: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        return asyncio.run(self.request_async(method, params, **kwargs))

    def start(self, **kwargs: Any) -> dict[str, Any]:
        return asyncio.run(self.start_async(**kwargs))

    def read(self, **kwargs: Any) -> dict[str, Any]:
        return asyncio.run(self.read_async(**kwargs))

    def write(self, **kwargs: Any) -> dict[str, Any]:
        return asyncio.run(self.write_async(**kwargs))

    def signal(self, **kwargs: Any) -> dict[str, Any]:
        return asyncio.run(self.signal_async(**kwargs))

    def terminate(self, **kwargs: Any) -> dict[str, Any]:
        return asyncio.run(self.terminate_async(**kwargs))

    def release(self, **kwargs: Any) -> dict[str, Any]:
        return asyncio.run(self.release_async(**kwargs))

    def drain_and_shutdown(self, **kwargs: Any) -> dict[str, Any]:
        return asyncio.run(self.drain_and_shutdown_async(**kwargs))

    def close(self) -> None:
        asyncio.run(self.close_async())

    async def close_async(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        try:
            self._writer.close()
        except OSError:
            pass
        if self._transport_closer is not None:
            try:
                self._transport_closer()
            except OSError:
                pass
        if self._process is not None and self._process.poll() is None:
            deadline = time.monotonic() + 2.0
            while self._process.poll() is None and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            for action in (signal.SIGTERM, signal.SIGKILL):
                if self._process.poll() is not None:
                    break
                try:
                    if os.name == 'posix':
                        os.killpg(self._process.pid, action)
                    elif action == signal.SIGTERM:
                        self._process.terminate()
                    else:
                        self._process.kill()
                except ProcessLookupError:
                    pass
                deadline = time.monotonic() + 2.0
                while self._process.poll() is None and time.monotonic() < deadline:
                    await asyncio.sleep(0.01)
            if self._process.poll() is None:
                raise TimeoutError('process owner transport did not exit after termination')
        while self._reader_thread.is_alive():
            await asyncio.sleep(0.01)
        self._reader.close()

    def notifications(self) -> tuple[dict[str, Any], ...]:
        with self._condition:
            return tuple(self._notifications)

    def _response_lines(self) -> Iterator[bytes]:
        try:
            descriptor = self._reader.fileno()
        except (AttributeError, io.UnsupportedOperation):
            yield from self._reader
            return
        buffered = bytearray()
        while not self._closed:
            if not select.select([descriptor], [], [], 0.05)[0]:
                continue
            try:
                chunk = os.read(descriptor, 65536)
            except BlockingIOError:
                continue
            if not chunk:
                return
            buffered.extend(chunk)
            while b'\n' in buffered:
                line, _, remaining = buffered.partition(b'\n')
                buffered = bytearray(remaining)
                yield bytes(line)

    def _read_loop(self) -> None:
        try:
            for raw_line in self._response_lines():
                payload = require_mapping(
                    json.loads(raw_line.decode("utf-8")),
                    name="server message",
                )
                if "id" in payload:
                    reject_unknown_fields(
                        payload,
                        allowed={"id", "result", "error"},
                        name="response",
                    )
                    request_id = payload.get("id")
                    if not isinstance(request_id, int) or isinstance(request_id, bool):
                        raise ProcessOwnerProtocolError(
                            "response.id must be an integer"
                        )
                    if ("result" in payload) == ("error" in payload):
                        raise ProcessOwnerProtocolError(
                            "response must contain exactly one result or error"
                        )
                    with self._condition:
                        if request_id not in self._pending_request_ids:
                            continue
                        if request_id in self._responses:
                            raise ProcessOwnerProtocolError(
                                f"duplicate response id: {request_id}"
                            )
                        self._responses[request_id] = payload
                        self._condition.notify_all()
                    continue
                reject_unknown_fields(
                    payload,
                    allowed={"method", "params"},
                    name="notification",
                )
                method = require_non_empty_string(
                    payload.get("method"), name="notification.method"
                )
                params = require_mapping(
                    payload.get("params", {}), name="notification.params"
                )
                with self._condition:
                    self._notifications.append(
                        {"method": method, "params": dict(params)}
                    )
                if self._notification_handler is not None:
                    self._notification_handler(method, dict(params))
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            ProcessOwnerProtocolError,
        ):
            pass
        finally:
            with self._condition:
                self._closed = True
                self._responses.clear()
                self._condition.notify_all()

    def __enter__(self) -> "ProcessOwnerClient":
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        self.close()
