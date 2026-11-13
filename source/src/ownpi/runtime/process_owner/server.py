from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import socket
import threading
import time
from typing import Any, BinaryIO
from uuid import uuid4

from ownpi.runtime.process_owner.protocol import (
    DEFAULT_CAPABILITIES,
    PACKAGE_VERSION,
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    InitializeParams,
    ProtocolVersion,
    ProcessOwnerProtocolError,
    REQUIRED_CAPABILITIES,
    SANDBOX_DIAGNOSTICS_CAPABILITY,
    error_response,
    notification,
    reject_unknown_fields,
    require_bool,
    require_identifier,
    require_non_empty_string,
    require_non_negative_int,
    optional_string,
    require_positive_int,
    require_string,
    require_string_list,
    require_string_mapping,
    success_response,
    validate_request,
)
from ownpi.runtime.process_owner.session_registry import (
    SessionEntry,
    SessionRegistry,
)
from ownpi.runtime.limits.resources import (
    PROCESS_OWNER_DAEMON_IDLE_GRACE_SECONDS,
)


def _validate_start_params(params: dict[str, Any]) -> dict[str, Any]:
    reject_unknown_fields(
        params,
        allowed={
            "process_id",
            "argv",
            "cwd",
            "env",
            "tty",
            "pipe_stdin",
            "pty_columns",
            "pty_rows",
            "timeout_seconds",
            "stdout_path",
            "stderr_path",
            "sandbox_backend",
            "arg0",
        },
        name="process/start params",
    )
    argv = require_string_list(
        params.get("argv"),
        name="argv",
        max_items=4096,
        min_items=1,
    )
    arg0_value = params.get("arg0", "")
    arg0 = (
        ""
        if arg0_value in (None, "")
        else require_string(arg0_value, name="arg0")
    )
    if arg0:
        argv[0] = arg0
    return dict(
        process_id=require_identifier(
            params.get("process_id"), name="process_id"
        ),
        argv=argv,
        cwd=require_non_empty_string(params.get("cwd"), name="cwd"),
        env=require_string_mapping(params.get("env", {}), name="env"),
        tty_mode=require_bool(params.get("tty", False), name="tty"),
        pipe_stdin=require_bool(
            params.get("pipe_stdin", False), name="pipe_stdin"
        ),
        pty_columns=_optional_positive_int(
            params.get("pty_columns"), name="pty_columns"
        ),
        pty_rows=_optional_positive_int(
            params.get("pty_rows"), name="pty_rows"
        ),
        timeout_seconds=require_non_negative_int(
            params.get("timeout_seconds", 0), name="timeout_seconds"
        ),
        stdout_path=optional_string(
            params.get("stdout_path"), name="stdout_path", max_length=4096
        ),
        stderr_path=optional_string(
            params.get("stderr_path"), name="stderr_path", max_length=4096
        ),
        sandbox_backend=optional_string(
            params.get("sandbox_backend"), name="sandbox_backend"
        ) or "unknown",
    )


def _optional_positive_int(value: Any, *, name: str) -> int | None:
    if value is None:
        return None
    return require_positive_int(value, name=name, maximum=10_000)


class ConnectionHandler:
    def __init__(
        self,
        *,
        reader: BinaryIO,
        writer: BinaryIO,
        session_registry: SessionRegistry,
        recoverable: bool,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._session_registry = session_registry
        self._recoverable = recoverable
        self._connection_id = f"owner-connection-{uuid4().hex}"
        self._session: SessionEntry | None = None
        self._ready = False
        self._session_recoverable = True
        self._request_ids: set[str | int] = set()
        self._write_lock = threading.Lock()

    def run(self) -> None:
        try:
            for raw_line in self._reader:
                payload: Any = None
                if len(raw_line) > 8 * 1024 * 1024:
                    self._send(
                        error_response(
                            None,
                            code="payload_too_large",
                            message="request exceeds limit",
                        )
                    )
                    continue
                try:
                    payload = json.loads(raw_line.decode("utf-8"))
                    request_id, method, params = validate_request(payload)
                    self._remember_request_id(request_id)
                    result = self._dispatch(method, params)
                    self._send(success_response(request_id, result))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    self._send(
                        error_response(
                            None,
                            code="invalid_request",
                            message=f"malformed JSON request: {type(exc).__name__}",
                        )
                    )
                except ProcessOwnerProtocolError as exc:
                    request_id = (
                        payload.get("id") if isinstance(payload, dict) else None
                    )
                    self._send(
                        error_response(
                            request_id, code="invalid_request", message=str(exc),
                            start_outcome=exc.start_outcome,
                        )
                    )
                except Exception:
                    request_id = (
                        payload.get("id") if isinstance(payload, dict) else None
                    )
                    self._send(
                        error_response(
                            request_id,
                            code="internal_error",
                            message="process owner request failed",
                        )
                    )
        finally:
            if self._session is not None:
                self._session_registry.detach(
                    session_id=self._session.session_id,
                    connection_id=self._connection_id,
                    recoverable=(
                        self._recoverable and self._session_recoverable
                    ),
                )

    def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "initialize":
            return self._initialize(params)
        if method == "initialized":
            session = self._require_session()
            reject_unknown_fields(params, allowed=set(), name="initialized params")
            if self._ready:
                raise ProcessOwnerProtocolError("connection is already ready")
            self._ready = True
            return {"initialized": True}
        session = self._require_ready_session()
        registry = session.process_registry
        if method == "process/start":
            try:
                start_params = _validate_start_params(params)
            except ProcessOwnerProtocolError as exc:
                raise ProcessOwnerProtocolError(str(exc), start_outcome='not_created') from exc
            return registry.start(**start_params)
        if method == "process/read":
            reject_unknown_fields(
                params,
                allowed={"process_id", "after_seq", "max_bytes", "wait_ms"},
                name="process/read params",
            )
            return registry.read(
                process_id=require_identifier(
                    params.get("process_id"), name="process_id"
                ),
                after_seq=require_non_negative_int(
                    params.get("after_seq", 0), name="after_seq"
                ),
                max_bytes=require_positive_int(
                    params.get("max_bytes", 65536),
                    name="max_bytes",
                    maximum=4 * 1024 * 1024,
                ),
                wait_ms=require_non_negative_int(
                    params.get("wait_ms", 0), name="wait_ms"
                ),
            )
        if method == "process/write":
            reject_unknown_fields(
                params,
                allowed={"process_id", "chunk", "write_id"},
                name="process/write params",
            )
            return registry.write(
                process_id=require_identifier(
                    params.get("process_id"), name="process_id"
                ),
                chunk=require_string(
                    params.get("chunk", ""), name="chunk", max_length=4 * 1024 * 1024
                ),
                write_id=require_identifier(params.get("write_id"), name="write_id"),
            )
        if method == "process/signal":
            reject_unknown_fields(
                params, allowed={"process_id", "signal"}, name="process/signal params"
            )
            return registry.signal(
                process_id=require_identifier(
                    params.get("process_id"), name="process_id"
                ),
                signal_number=require_positive_int(
                    params.get("signal"), name="signal", maximum=signal.NSIG
                ),
            )
        if method == "process/terminate":
            reject_unknown_fields(
                params,
                allowed={"process_id", "grace_ms"},
                name="process/terminate params",
            )
            return registry.terminate(
                process_id=require_identifier(
                    params.get("process_id"), name="process_id"
                ),
                grace_seconds=require_non_negative_int(
                    params.get("grace_ms", 2000), name="grace_ms"
                )
                / 1000.0,
            )
        if method == "process/release":
            reject_unknown_fields(
                params,
                allowed={"process_id", "final_seq"},
                name="process/release params",
            )
            return registry.release(
                process_id=require_identifier(
                    params.get("process_id"), name="process_id"
                ),
                final_seq=require_positive_int(
                    params.get("final_seq"), name="final_seq"
                ),
            )
        if method == "session/drain_and_shutdown":
            reject_unknown_fields(
                params, allowed={"grace_ms"}, name="session/drain_and_shutdown params"
            )
            result = registry.drain_and_shutdown(
                grace_seconds=require_non_negative_int(
                    params.get("grace_ms", 2000), name="grace_ms"
                )
                / 1000.0
            )
            self._session_recoverable = False
            return result
        raise ProcessOwnerProtocolError(f"unknown method: {method}")
    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._session is not None or self._ready:
            raise ProcessOwnerProtocolError("connection is already initialized")
        parsed = InitializeParams.from_value(params)
        negotiated = parsed.protocol_version.negotiate(
            ProtocolVersion(major=PROTOCOL_MAJOR, minor=PROTOCOL_MINOR)
        )
        if parsed.package_version and parsed.package_version != PACKAGE_VERSION:
            raise ProcessOwnerProtocolError(
                f"package version mismatch: client={parsed.package_version} server={PACKAGE_VERSION}"
            )
        requested = set(parsed.capabilities)
        unsupported = sorted(requested - DEFAULT_CAPABILITIES)
        if unsupported:
            raise ProcessOwnerProtocolError(
                f"unsupported required capabilities: {unsupported}"
            )
        missing = sorted(REQUIRED_CAPABILITIES - requested)
        if missing:
            raise ProcessOwnerProtocolError(f"missing required capabilities: {missing}")
        self._session = self._session_registry.attach(
            connection_id=self._connection_id,
            notification_sink=self._notify,
            resume_session_id=parsed.resume_session_id,
        )
        return {
            "session_id": self._session.session_id,
            "protocol_version": negotiated.to_dict(),
            "capabilities": sorted((requested & DEFAULT_CAPABILITIES) | {SANDBOX_DIAGNOSTICS_CAPABILITY}),
            "package_version": PACKAGE_VERSION,
        }

    def _require_session(self) -> SessionEntry:
        if self._session is None:
            raise ProcessOwnerProtocolError("initialize must be called first")
        return self._session

    def _require_ready_session(self) -> SessionEntry:
        if not self._ready:
            raise ProcessOwnerProtocolError(
                "initialized must be called before process requests"
            )
        return self._require_session()

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._send(notification(method, params))

    def _send(self, payload: dict[str, Any]) -> None:
        encoded = (
            json.dumps(payload, separators=(",", ":"), ensure_ascii=True) + "\n"
        ).encode("utf-8")
        with self._write_lock:
            self._writer.write(encoded)
            self._writer.flush()

    def _remember_request_id(self, request_id: str | int) -> None:
        if request_id in self._request_ids:
            raise ProcessOwnerProtocolError(
                f"duplicate request id: {request_id}"
            )
        self._request_ids.add(request_id)


def run_unix_server(
    *,
    socket_path: Path,
    endpoint_manifest: Path,
    idle_grace: float,
) -> int:
    if len(os.fsencode(socket_path)) >= 100:
        raise ValueError(
            "process owner Unix socket path must be shorter than 100 bytes"
        )
    socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    socket_path.unlink(missing_ok=True)
    registry = SessionRegistry()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    os.chmod(socket_path, 0o600)
    server.listen(16)
    server.settimeout(1.0)
    _write_endpoint_manifest(endpoint_manifest, socket_path=socket_path)
    lifecycle = {"last_connection_at": time.monotonic(), "active_connections": 0}
    lock = threading.Lock()
    try:
        while True:
            registry.maintenance()
            with lock:
                idle = (
                    lifecycle["active_connections"] == 0
                    and not registry.has_sessions()
                    and time.monotonic() - lifecycle["last_connection_at"] >= idle_grace
                )
            if idle:
                break
            try:
                connection, _ = server.accept()
            except socket.timeout:
                continue
            with lock:
                lifecycle["active_connections"] += 1
                lifecycle["last_connection_at"] = time.monotonic()
            threading.Thread(
                target=_serve_socket_connection,
                args=(connection, registry, lock, lifecycle),
                daemon=True,
            ).start()
    finally:
        registry.shutdown()
        server.close()
        socket_path.unlink(missing_ok=True)
        endpoint_manifest.unlink(missing_ok=True)
    return 0


def _serve_socket_connection(
    connection: socket.socket,
    registry: SessionRegistry,
    lock: threading.Lock,
    lifecycle: dict[str, float | int],
) -> None:
    try:
        with connection:
            reader = connection.makefile("rb")
            writer = connection.makefile("wb")
            ConnectionHandler(
                reader=reader,
                writer=writer,
                session_registry=registry,
                recoverable=True,
            ).run()
    finally:
        with lock:
            lifecycle["active_connections"] = max(
                0,
                int(lifecycle["active_connections"]) - 1,
            )
            lifecycle["last_connection_at"] = time.monotonic()


def _write_endpoint_manifest(path: Path, *, socket_path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(f"{path.suffix}.tmp-{uuid4().hex}")
    temporary.write_text(
        json.dumps(
            {
                "socket_path": str(socket_path),
                "protocol_major": PROTOCOL_MAJOR,
                "protocol_minor": PROTOCOL_MINOR,
                "pid": os.getpid(),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ownpi-process-owner")
    parser.add_argument("--unix-socket", type=Path)
    parser.add_argument("--endpoint-manifest", type=Path)
    parser.add_argument(
        "--idle-grace-seconds",
        type=float,
        default=PROCESS_OWNER_DAEMON_IDLE_GRACE_SECONDS,
    )
    parser.add_argument("--version", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.version:
        print(f"{PROTOCOL_MAJOR}.{PROTOCOL_MINOR}")
        return 0
    if args.unix_socket and args.endpoint_manifest:
        return run_unix_server(
            socket_path=args.unix_socket,
            endpoint_manifest=args.endpoint_manifest,
            idle_grace=args.idle_grace_seconds,
        )
    raise SystemExit("provide --unix-socket and --endpoint-manifest")


if __name__ == "__main__":
    raise SystemExit(main())
