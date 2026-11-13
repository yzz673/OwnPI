from __future__ import annotations

import argparse
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import threading
from typing import Any, BinaryIO

from ownpi.runtime.process_owner.remote import (
    OWNER_RELAY_MODULE,
    RemoteEndpointRef,
    StaleEndpointError,
    build_ssh_command,
    parse_endpoint_ref,
    read_endpoint_manifest,
    resolve_endpoint_ref,
)


def relay_endpoint(
    *, endpoint_manifest: Path, reader: BinaryIO, writer: BinaryIO
) -> None:
    manifest = read_endpoint_manifest(endpoint_manifest)
    socket_path = str(manifest.socket_path)
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.connect(socket_path)
    except OSError as exc:
        connection.close()
        raise StaleEndpointError(
            f"process owner socket is unavailable: {socket_path}"
        ) from exc
    socket_reader = connection.makefile("rb")
    socket_writer = connection.makefile("wb")
    upload = threading.Thread(
        target=_copy_lines_and_shutdown,
        args=(reader, socket_writer, connection),
        daemon=True,
    )
    upload.start()
    try:
        _copy_lines(socket_reader, writer)
    finally:
        try:
            connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        socket_reader.close()
        socket_writer.close()
        connection.close()
        upload.join(timeout=1.0)


def _copy_lines(source: BinaryIO, destination: BinaryIO) -> None:
    for line in iter(source.readline, b""):
        destination.write(line)
        destination.flush()


def _copy_lines_and_shutdown(
    source: BinaryIO, destination: BinaryIO, connection: socket.socket
) -> None:
    try:
        _copy_lines(source, destination)
    finally:
        try:
            destination.close()
        except OSError:
            pass
        try:
            connection.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def build_ssh_relay_command(
    profile: Any,
    endpoint_ref: str | RemoteEndpointRef,
    *,
    python_executable: str = "python3",
) -> list[str]:
    """Build an SSH-authenticated stdio relay without embedding credentials."""
    ref = resolve_endpoint_ref(profile, endpoint_ref)
    remote_command = shlex.join(
        [
            python_executable,
            "-m",
            OWNER_RELAY_MODULE,
            "--endpoint-manifest",
            str(ref.manifest_path),
        ]
    )
    return build_ssh_command(profile, remote_command)


def start_ssh_relay(
    profile: Any,
    endpoint_ref: str | RemoteEndpointRef,
    *,
    python_executable: str = "python3",
    popen_factory: Any = subprocess.Popen,
) -> subprocess.Popen[bytes]:
    command = build_ssh_relay_command(
        profile,
        endpoint_ref,
        python_executable=python_executable,
    )
    process = popen_factory(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        start_new_session=(sys.platform != "win32"),
    )
    if process.stdin is None or process.stdout is None:
        process.kill()
        raise RuntimeError("SSH relay stdio pipes are unavailable")
    return process


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ownpi-process-owner-relay")
    endpoint_group = parser.add_mutually_exclusive_group(required=True)
    endpoint_group.add_argument("--endpoint-manifest", type=Path)
    endpoint_group.add_argument("--endpoint-ref")
    args = parser.parse_args(argv)
    manifest_path = (
        args.endpoint_manifest
        if args.endpoint_manifest is not None
        else Path(str(parse_endpoint_ref(args.endpoint_ref).manifest_path))
    )
    relay_endpoint(
        endpoint_manifest=manifest_path,
        reader=sys.stdin.buffer,
        writer=sys.stdout.buffer,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
