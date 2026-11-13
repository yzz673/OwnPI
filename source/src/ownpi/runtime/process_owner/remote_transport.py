from __future__ import annotations

import asyncio
from enum import Enum
import subprocess
import threading
from typing import Any, BinaryIO, Callable, cast

from ownpi.runtime.process_owner.client import (
    NotificationHandler,
    ProcessOwnerClient,
    complete_on_cancel,
)
from ownpi.runtime.process_owner.protocol import ProcessOwnerProtocolError
from ownpi.runtime.process_owner.relay import start_ssh_relay
from ownpi.runtime.process_owner.remote import RemoteEndpointRef


class RemoteTransportState(str, Enum):
    CONNECTED = "Connected"
    RECOVERING = "Recovering"
    FAILED = "Failed"


class RemoteTransportError(RuntimeError):
    """Raised when a remote owner transport cannot be recovered."""


class RemoteOwnerLostError(RemoteTransportError):
    """Raised when the remote owner session is definitively no longer resumable."""


PopenFactory = Callable[..., subprocess.Popen[bytes]]


class RemoteProcessOwnerTransport:
    """Recoverable SSH relay around the shared process-owner protocol client."""

    def __init__(
        self,
        *,
        profile: Any,
        endpoint_ref: str | RemoteEndpointRef,
        python_executable: str = "python3",
        popen_factory: PopenFactory = subprocess.Popen,
        notification_handler: NotificationHandler | None = None,
    ) -> None:
        self._profile = profile
        self._endpoint_ref = (
            endpoint_ref
            if isinstance(endpoint_ref, RemoteEndpointRef)
            else RemoteEndpointRef.parse(endpoint_ref)
        )
        self._python_executable = python_executable
        self._popen_factory = popen_factory
        self._notification_handler = notification_handler
        self._client: ProcessOwnerClient | None = None
        self._resume_session_id = ""
        self._closed = False
        self._condition = threading.Condition(threading.RLock())
        self._reconnect_in_progress = False
        self._last_reconnect_error: RemoteTransportError | None = None
        self.state = RemoteTransportState.FAILED
        self.negotiated_protocol: dict[str, int] = {}
        self.negotiated_capabilities: tuple[str, ...] = ()

    @property
    def endpoint_ref(self) -> RemoteEndpointRef:
        return self._endpoint_ref

    @property
    def session_id(self) -> str:
        with self._condition:
            return self._resume_session_id

    @property
    def client(self) -> ProcessOwnerClient:
        with self._condition:
            if self._client is None or self.state is not RemoteTransportState.CONNECTED:
                raise RemoteTransportError(
                    f"remote owner transport is {self.state.value}"
                )
            return self._client

    async def connect_async(self, *, resume_session_id: str = "") -> dict[str, Any]:
        with self._condition:
            if self._closed:
                raise RemoteTransportError("remote owner transport is closed")
            if self._client is not None or self._reconnect_in_progress:
                raise RemoteTransportError(
                    "remote owner transport is already connected"
                )
            self._reconnect_in_progress = True
            self.state = (
                RemoteTransportState.RECOVERING
                if resume_session_id
                else RemoteTransportState.FAILED
            )
        try:
            client, result = await self._open_client_async(resume_session_id=resume_session_id)
        except BaseException as exc:
            error = _connection_error(exc, resuming=bool(resume_session_id))
            with self._condition:
                self.state = RemoteTransportState.FAILED
                self._last_reconnect_error = error
                self._reconnect_in_progress = False
                self._condition.notify_all()
            if not isinstance(exc, Exception):
                raise
            raise error from exc
        with self._condition:
            closed = self._closed
            if not closed:
                self._install_client_locked(client, result)
            self._reconnect_in_progress = False
            self._condition.notify_all()
        if closed:
            await complete_on_cancel(client.close_async())
            raise RemoteTransportError("remote owner transport is closed")
        return result

    async def reconnect_async(self) -> dict[str, Any]:
        return await self._recover_async(failed_client=None, force=True)

    async def detach_for_recovery_async(self) -> None:
        with self._condition:
            if self._closed:
                raise RemoteTransportError("remote owner transport is closed")
            if not self._resume_session_id:
                raise RemoteTransportError("remote owner has no session to detach")
            client = self._client
            self._client = None
            self.state = RemoteTransportState.RECOVERING
        if client is not None:
            await client.close_async()

    async def request_async(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return await self.client.request_async(method, params)

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
        params = {
            "process_id": process_id,
            "argv": list(argv),
            "cwd": cwd,
            "env": dict(env or {}),
            "tty": tty,
            "pipe_stdin": pipe_stdin,
            "pty_columns": pty_columns,
            "pty_rows": pty_rows,
            "timeout_seconds": max(0, int(timeout_seconds)),
            "stdout_path": str(stdout_path or ""),
            "stderr_path": str(stderr_path or ""),
            "sandbox_backend": sandbox_backend,
        }
        try:
            return await self._request_with_recovery_async("process/start", params)
        except ProcessOwnerProtocolError as exc:
            if exc.start_outcome == 'not_created' or "duplicate process_id" not in str(exc):
                raise
            await self.read_async(process_id=process_id, wait_ms=0, max_bytes=1)
            return {"process_id": process_id, "recovered_start": True}

    async def read_async(
        self,
        *,
        process_id: str,
        after_seq: int = 0,
        max_bytes: int = 65536,
        wait_ms: int = 0,
        cancel_checker: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        return await self._request_with_recovery_async(
            "process/read",
            {
                "process_id": process_id,
                "after_seq": after_seq,
                "max_bytes": max_bytes,
                "wait_ms": wait_ms,
            },
            cancel_checker=cancel_checker,
        )

    async def write_async(self, *, process_id: str, chunk: str, write_id: str) -> dict[str, Any]:
        return await self._request_with_recovery_async(
            "process/write",
            {"process_id": process_id, "chunk": chunk, "write_id": write_id},
        )

    async def signal_async(self, *, process_id: str, signal_number: int) -> dict[str, Any]:
        return await self._request_with_recovery_async(
            "process/signal",
            {"process_id": process_id, "signal": signal_number},
        )

    async def terminate_async(self, *, process_id: str, grace_ms: int = 2000) -> dict[str, Any]:
        return await self._request_with_recovery_async(
            "process/terminate",
            {"process_id": process_id, "grace_ms": grace_ms},
        )

    async def release_async(self, *, process_id: str, final_seq: int) -> dict[str, Any]:
        return await self._request_with_recovery_async(
            "process/release",
            {"process_id": process_id, "final_seq": final_seq},
        )

    async def drain_and_shutdown_async(self, *, grace_ms: int = 2000) -> dict[str, Any]:
        return await self._request_with_recovery_async(
            "session/drain_and_shutdown",
            {"grace_ms": grace_ms},
        )

    async def close_async(self) -> None:
        with self._condition:
            self._closed = True
            client = self._client
            self._client = None
            self.state = RemoteTransportState.FAILED
            self._condition.notify_all()
        if client is not None:
            await client.close_async()

    def connect(self, **kwargs: Any) -> Any:
        return asyncio.run(self.connect_async(**kwargs))

    def reconnect(self, **kwargs: Any) -> Any:
        return asyncio.run(self.reconnect_async(**kwargs))

    def detach_for_recovery(self, **kwargs: Any) -> Any:
        return asyncio.run(self.detach_for_recovery_async(**kwargs))

    def start(self, **kwargs: Any) -> Any:
        return asyncio.run(self.start_async(**kwargs))

    def read(self, **kwargs: Any) -> Any:
        return asyncio.run(self.read_async(**kwargs))

    def write(self, **kwargs: Any) -> Any:
        return asyncio.run(self.write_async(**kwargs))

    def signal(self, **kwargs: Any) -> Any:
        return asyncio.run(self.signal_async(**kwargs))

    def terminate(self, **kwargs: Any) -> Any:
        return asyncio.run(self.terminate_async(**kwargs))

    def release(self, **kwargs: Any) -> Any:
        return asyncio.run(self.release_async(**kwargs))

    def drain_and_shutdown(self, **kwargs: Any) -> Any:
        return asyncio.run(self.drain_and_shutdown_async(**kwargs))

    def close(self, **kwargs: Any) -> Any:
        return asyncio.run(self.close_async(**kwargs))

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return asyncio.run(self.request_async(method, params))

    def __enter__(self) -> "RemoteProcessOwnerTransport":
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        self.close()

    async def _request_with_recovery_async(
        self, method: str, params: dict[str, Any],
        *, cancel_checker: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        with self._condition:
            connected = (
                self.state is RemoteTransportState.CONNECTED
                and self._client is not None
            )
        if not connected:
            await self._recover_async(failed_client=None, force=False)
        client = self.client
        try:
            return await client.request_async(method, params, **(
                {'cancel_checker': cancel_checker} if cancel_checker is not None else {}
            ))
        except Exception as exc:
            if not _is_recoverable_transport_error(exc):
                raise
            await self._recover_async(failed_client=client, force=False)
            return await self.client.request_async(method, params, **(
                {'cancel_checker': cancel_checker} if cancel_checker is not None else {}
            ))

    async def _recover_async(
        self,
        *,
        failed_client: ProcessOwnerClient | None,
        force: bool,
    ) -> dict[str, Any]:
        waited = False
        while True:
            with self._condition:
                if not self._reconnect_in_progress or self._closed:
                    break
            waited = True
            await asyncio.sleep(0.01)
        with self._condition:
            if self._closed:
                raise RemoteTransportError("remote owner transport is closed")
            if waited:
                if (
                    self.state is RemoteTransportState.CONNECTED
                    and self._client is not None
                ):
                    return self._negotiation_result_locked()
                if self._last_reconnect_error is not None:
                    raise self._last_reconnect_error
            if not self._resume_session_id:
                raise RemoteTransportError("remote owner has no session to resume")
            if (
                not force
                and failed_client is not None
                and self._client is not failed_client
                and self.state is RemoteTransportState.CONNECTED
            ):
                return self._negotiation_result_locked()
            old_client = self._client
            self._client = None
            resume_session_id = self._resume_session_id
            self._reconnect_in_progress = True
            self._last_reconnect_error = None
            self.state = RemoteTransportState.RECOVERING
        try:
            if old_client is not None:
                await old_client.close_async()
            client, result = await self._open_client_after_detach_race_async(
                resume_session_id=resume_session_id,
            )
        except BaseException as exc:
            error = _connection_error(exc, resuming=True)
            with self._condition:
                self.state = RemoteTransportState.FAILED
                self._last_reconnect_error = error
                self._reconnect_in_progress = False
                self._condition.notify_all()
            if not isinstance(exc, Exception):
                raise
            raise error from exc
        with self._condition:
            closed = self._closed
            if not closed:
                self._install_client_locked(client, result)
            self._reconnect_in_progress = False
            self._condition.notify_all()
        if closed:
            await complete_on_cancel(client.close_async())
            raise RemoteTransportError("remote owner transport is closed")
        return result

    async def _open_client_async(
        self,
        *,
        resume_session_id: str,
    ) -> tuple[ProcessOwnerClient, dict[str, Any]]:
        process = start_ssh_relay(
            self._profile,
            self._endpoint_ref,
            python_executable=self._python_executable,
            popen_factory=self._popen_factory,
        )
        client = ProcessOwnerClient(
            reader=cast(BinaryIO, process.stdout),
            writer=cast(BinaryIO, process.stdin),
            process=process,
            notification_handler=self._notification_handler,
        )
        try:
            result = await client.initialize_async(resume_session_id=resume_session_id)
        except BaseException:
            await complete_on_cancel(client.close_async())
            raise
        return client, result

    async def _open_client_after_detach_race_async(
        self,
        *,
        resume_session_id: str,
    ) -> tuple[ProcessOwnerClient, dict[str, Any]]:
        for attempt in range(20):
            try:
                return await self._open_client_async(resume_session_id=resume_session_id)
            except ProcessOwnerProtocolError as exc:
                if (
                    not resume_session_id
                    or "session is already attached" not in str(exc).casefold()
                    or attempt == 19
                ):
                    raise
                await asyncio.sleep(0.05)
        raise RemoteTransportError("remote owner resume retry exhausted")

    def _install_client_locked(
        self,
        client: ProcessOwnerClient,
        result: dict[str, Any],
    ) -> None:
        self._client = client
        self._resume_session_id = client.session_id
        version = result.get("protocol_version")
        self.negotiated_protocol = (
            dict(version) if isinstance(version, dict) else {}
        )
        capabilities = result.get("capabilities")
        self.negotiated_capabilities = (
            tuple(str(item) for item in capabilities)
            if isinstance(capabilities, list)
            else ()
        )
        self._last_reconnect_error = None
        self.state = RemoteTransportState.CONNECTED

    def _negotiation_result_locked(self) -> dict[str, Any]:
        return {
            "session_id": self._resume_session_id,
            "protocol_version": dict(self.negotiated_protocol),
            "capabilities": list(self.negotiated_capabilities),
        }


def _is_recoverable_transport_error(error: Exception) -> bool:
    if isinstance(error, ProcessOwnerProtocolError) and error.start_outcome == 'not_created':
        return False
    if isinstance(error, (BrokenPipeError, ConnectionError, EOFError, TimeoutError)):
        return True
    if isinstance(error, ProcessOwnerProtocolError):
        message = str(error).lower()
        return any(
            marker in message for marker in ("connection closed", "transport", "eof")
        )
    return False


def _connection_error(error: Exception, *, resuming: bool) -> RemoteTransportError:
    if resuming and _is_definitive_resume_loss(error):
        return RemoteOwnerLostError(
            "remote owner session is unknown or expired"
        )
    message = (
        "remote owner session could not be resumed"
        if resuming
        else "remote owner relay failed during initialize"
    )
    return RemoteTransportError(message)


def _is_definitive_resume_loss(error: BaseException) -> bool:
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if isinstance(current, ProcessOwnerProtocolError):
            message = str(current).casefold()
            if "unknown or expired resume_session_id" in message:
                return True
        current = current.__cause__ or current.__context__
    return False


async def connect_remote_owner_async(
    *,
    profile: Any,
    endpoint_ref: str | RemoteEndpointRef,
    resume_session_id: str = "",
    python_executable: str = "python3",
    popen_factory: PopenFactory = subprocess.Popen,
    notification_handler: NotificationHandler | None = None,
) -> RemoteProcessOwnerTransport:
    transport = RemoteProcessOwnerTransport(
        profile=profile,
        endpoint_ref=endpoint_ref,
        python_executable=python_executable,
        popen_factory=popen_factory,
        notification_handler=notification_handler,
    )
    await transport.connect_async(resume_session_id=resume_session_id)
    return transport


def connect_remote_owner(**kwargs: Any) -> RemoteProcessOwnerTransport:
    return asyncio.run(connect_remote_owner_async(**kwargs))
