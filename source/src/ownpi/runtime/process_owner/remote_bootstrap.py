from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import json
import re
import shlex
import subprocess
from typing import Any, Awaitable, Callable, Sequence

from ownpi.runtime.environment.process_capture import capture_private_process
from ownpi.runtime.process_owner.protocol import PROTOCOL_MAJOR
from ownpi.runtime.limits.resources import (
    PROCESS_OWNER_DAEMON_IDLE_GRACE_SECONDS,
)
from ownpi.runtime.process_owner.remote import (
    OWNER_SERVER_MODULE,
    OWNER_PACKAGE_NAME,
    OWNER_PACKAGE_VERSION,
    RemoteEndpointRef,
    RemoteOwnerError,
    RemoteOwnerRunPaths,
    RemoteOwnerVersionMismatch,
    _profile_int,
    _profile_text,
    parse_endpoint_ref,
    resolve_endpoint_ref,
)


@dataclass(frozen=True)
class OwnerPackageProbe:
    package_name: str
    package_version: str
    protocol_major: int
    protocol_minor: int
    available: bool = True


@dataclass(frozen=True)
class RemoteBootstrapResult:
    endpoint_ref: RemoteEndpointRef
    paths: RemoteOwnerRunPaths
    package_probe: OwnerPackageProbe
    ssh_command: tuple[str, ...]
    bootstrap_script: str


CommandRunner = Callable[[Sequence[str]], Awaitable[subprocess.CompletedProcess[str]]]
_PACKAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,254}$")
_USER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def build_ssh_command(profile: Any, remote_command: str) -> list[str]:
    host = _profile_text(profile, "host")
    user = _profile_text(profile, "user")
    if not host:
        raise ValueError("SSH profile host is required")
    if not _HOST_RE.fullmatch(host) or (user and not _USER_RE.fullmatch(user)):
        raise ValueError("SSH profile target is invalid")
    if not remote_command or "\x00" in remote_command:
        raise ValueError("remote SSH command is invalid")
    target = f"{user}@{host}" if user else host
    if any(
        char.isspace() or char in {";", "&", "|", "`", "\n", "\r"} for char in target
    ):
        raise ValueError("SSH profile target contains unsafe characters")
    port = _profile_int(profile, "port", 22)
    timeout = _profile_int(profile, "connect_timeout_seconds", 10)
    strict = _profile_text(profile, "strict_host_key_checking", "accept-new")
    if strict not in {"yes", "no", "accept-new"}:
        raise ValueError("unsupported StrictHostKeyChecking policy")
    if port <= 0 or timeout <= 0:
        raise ValueError("SSH port and timeout must be positive")
    return [
        "ssh",
        "-p",
        str(port),
        "-o",
        f"ConnectTimeout={timeout}",
        "-o",
        f"StrictHostKeyChecking={strict}",
        target,
        remote_command,
    ]


def build_owner_version_probe_command(
    *,
    python_executable: str = "python3",
    package_name: str = OWNER_PACKAGE_NAME,
) -> str:
    _validate_package_name(package_name)
    code = (
        "import importlib.metadata as m, json; "
        "from ownpi.runtime.process_owner import protocol as p; "
        f"print(json.dumps({{'package_version': m.version({package_name!r}), "
        "'protocol_major': p.PROTOCOL_MAJOR, 'protocol_minor': p.PROTOCOL_MINOR}))"
    )
    return shlex.join([python_executable, "-c", code])


async def probe_owner_package_async(
    profile: Any,
    *,
    python_executable: str = "python3",
    package_name: str = OWNER_PACKAGE_NAME,
    runner: CommandRunner | None = None,
) -> OwnerPackageProbe:
    command = build_ssh_command(
        profile,
        build_owner_version_probe_command(
            python_executable=python_executable,
            package_name=package_name,
        ),
    )
    result = await (runner or _run_command)(command)
    if result.returncode != 0:
        return OwnerPackageProbe(package_name, "", -1, -1, available=False)
    output = str(result.stdout or "").strip().splitlines()
    if not output:
        return OwnerPackageProbe(package_name, "", -1, -1, available=False)
    try:
        payload = json.loads(output[-1])
        return OwnerPackageProbe(
            package_name=package_name,
            package_version=str(payload["package_version"]),
            protocol_major=int(payload["protocol_major"]),
            protocol_minor=int(payload["protocol_minor"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RemoteOwnerError(
            "remote owner version probe returned malformed output"
        ) from exc


def build_owner_install_command(
    *,
    python_executable: str = "python3",
    package_name: str = OWNER_PACKAGE_NAME,
    package_version: str = OWNER_PACKAGE_VERSION,
) -> str:
    _validate_package_name(package_name)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}", package_version):
        raise ValueError("owner package version is invalid")
    return shlex.join(
        [
            python_executable,
            "-m",
            "pip",
            "install",
            "--user",
            "--upgrade",
            f"{package_name}=={package_version}",
        ]
    )


def build_remote_stale_probe_command(
    endpoint_ref: str | RemoteEndpointRef,
    *,
    python_executable: str = "python3",
) -> str:
    ref = (
        endpoint_ref
        if isinstance(endpoint_ref, RemoteEndpointRef)
        else parse_endpoint_ref(endpoint_ref)
    )
    code = (
        "from pathlib import Path; "
        "from ownpi.runtime.process_owner.remote import endpoint_is_stale; "
        f"raise SystemExit(0 if endpoint_is_stale(Path({str(ref.manifest_path)!r})) else 3)"
    )
    return shlex.join([python_executable, "-c", code])


async def cleanup_remote_stale_endpoint_async(
    profile: Any,
    endpoint_ref: str | RemoteEndpointRef,
    *,
    python_executable: str = "python3",
    runner: CommandRunner | None = None,
) -> bool:
    ref = resolve_endpoint_ref(profile, endpoint_ref)
    run = runner or _run_command
    probe = await run(
        build_ssh_command(
            profile,
            build_remote_stale_probe_command(ref, python_executable=python_executable),
        )
    )
    if probe.returncode == 3:
        raise RemoteOwnerError(f"refusing to remove live remote endpoint: {ref}")
    if probe.returncode != 0:
        raise RemoteOwnerError("remote stale endpoint probe failed")
    code = (
        "from pathlib import Path; "
        "from ownpi.runtime.process_owner.remote import cleanup_stale_endpoint; "
        f"cleanup_stale_endpoint(Path({str(ref.manifest_path)!r}))"
    )
    result = await run(
        build_ssh_command(
            profile,
            shlex.join([python_executable, "-c", code]),
        )
    )
    if result.returncode != 0:
        raise RemoteOwnerError("remote stale endpoint cleanup failed")
    return True


def build_remote_bootstrap_script(
    paths: RemoteOwnerRunPaths,
    *,
    python_executable: str = "python3",
    package_version: str = OWNER_PACKAGE_VERSION,
    idle_grace_seconds: float = PROCESS_OWNER_DAEMON_IDLE_GRACE_SECONDS,
) -> str:
    _validate_package_version(package_version)
    _validate_non_negative("idle_grace_seconds", idle_grace_seconds)

    def quote(value: object) -> str:
        return shlex.quote(str(value))

    daemon = shlex.join(
        [
            python_executable,
            "-m",
            OWNER_SERVER_MODULE,
            "--unix-socket",
            str(paths.socket_path),
            "--endpoint-manifest",
            str(paths.manifest_path),
            "--idle-grace-seconds",
            str(idle_grace_seconds),
        ]
    )
    manifest_check = shlex.join(
        [
            python_executable,
            "-c",
            (
                "import json, os, stat, sys; "
                'path=sys.argv[1]; payload=json.load(open(path, encoding="utf-8")); '
                "mode=stat.S_IMODE(os.stat(path).st_mode); "
                'valid=(mode == 0o600 and payload.get("socket_path") == sys.argv[2] '
                f'and payload.get("protocol_major") == {PROTOCOL_MAJOR}); '
                "raise SystemExit(0 if valid else 75)"
            ),
            str(paths.manifest_path),
            str(paths.socket_path),
        ]
    )
    return "\n".join(
        [
            "set -eu",
            f"run_dir={quote(paths.run_dir)}",
            f"manifest={quote(paths.manifest_path)}",
            f"socket_path={quote(paths.socket_path)}",
            f"log_path={quote(paths.log_path)}",
            'mkdir -p -- "$run_dir"',
            'test ! -L "$run_dir" || { echo symlink_run_directory >&2; exit 76; }',
            'chmod 700 -- "$run_dir"',
            'test ! -e "$manifest" || { echo stale_or_live_endpoint >&2; exit 73; }',
            'test ! -e "$socket_path" || { echo stale_or_live_socket >&2; exit 73; }',
            f'nohup setsid -- {daemon} </dev/null >"$log_path" 2>&1 &',
            "daemon_pid=$!",
            "i=0",
            'while [ ! -f "$manifest" ] && [ "$i" -lt 100 ]; do',
            '  kill -0 "$daemon_pid" 2>/dev/null || { cat "$log_path" >&2 || true; exit 74; }',
            "  sleep 0.05",
            "  i=$((i + 1))",
            "done",
            'test -f "$manifest" || { cat "$log_path" >&2 || true; exit 74; }',
            manifest_check,
            'chmod 700 -- "$run_dir"',
            'printf "ready pid=%s manifest=%s\\n" "$daemon_pid" "$manifest"',
            "",
        ]
    )


async def bootstrap_remote_owner_async(
    profile: Any,
    endpoint_ref: str | RemoteEndpointRef,
    *,
    package_version: str = OWNER_PACKAGE_VERSION,
    python_executable: str = "python3",
    runner: CommandRunner | None = None,
    install_if_missing: bool = True,
    idle_grace_seconds: float = PROCESS_OWNER_DAEMON_IDLE_GRACE_SECONDS,
) -> RemoteBootstrapResult:
    ref = resolve_endpoint_ref(profile, endpoint_ref)
    paths = RemoteOwnerRunPaths(
        ref.run_dir,
        ref.run_dir / "owner.sock",
        ref.manifest_path,
        ref.run_dir / "owner.log",
    )
    run = runner or _run_command
    probe = await probe_owner_package_async(
        profile, python_executable=python_executable, runner=run
    )
    if (
        probe.package_version != package_version
        or probe.protocol_major != PROTOCOL_MAJOR
    ):
        if not install_if_missing:
            raise RemoteOwnerVersionMismatch(
                f"remote owner mismatch: expected {package_version}/{PROTOCOL_MAJOR}, "
                f"got {probe.package_version or 'missing'}/{probe.protocol_major}"
            )
        install_result = await run(
            build_ssh_command(
                profile,
                build_owner_install_command(
                    python_executable=python_executable,
                    package_version=package_version,
                ),
            )
        )
        if install_result.returncode != 0:
            raise RemoteOwnerError("remote owner package installation failed")
        probe = await probe_owner_package_async(
            profile, python_executable=python_executable, runner=run
        )
        if (
            probe.package_version != package_version
            or probe.protocol_major != PROTOCOL_MAJOR
        ):
            raise RemoteOwnerVersionMismatch(
                "remote owner package remained incompatible after installation"
            )
    script = build_remote_bootstrap_script(
        paths,
        python_executable=python_executable,
        package_version=package_version,
        idle_grace_seconds=idle_grace_seconds,
    )
    command = build_ssh_command(profile, f"/bin/sh -lc {shlex.quote(script)}")
    result = await run(command)
    if result.returncode != 0:
        raise RemoteOwnerError(
            f"remote owner bootstrap failed: {str(result.stderr or '').strip()}"
        )
    if not any(
        line.startswith("ready ") for line in str(result.stdout or "").splitlines()
    ):
        raise RemoteOwnerError(
            "remote owner bootstrap did not return a ready handshake"
        )
    return RemoteBootstrapResult(ref, paths, probe, tuple(command), script)


def _validate_package_name(package_name: str) -> None:
    if not _PACKAGE_RE.fullmatch(package_name):
        raise ValueError("owner package name is invalid")


def _validate_package_version(package_version: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}", package_version):
        raise ValueError("owner package version is invalid")


def _validate_non_negative(name: str, value: float) -> None:
    if float(value) < 0:
        raise ValueError(f"{name} must be non-negative")


async def _run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return await capture_private_process(
        list(command), cwd=Path.cwd(), env=dict(os.environ),
        input_text=None, timeout_seconds=None,
    )


def bootstrap_remote_owner(
    profile: Any, endpoint_ref: str | RemoteEndpointRef, *,
    package_version: str = OWNER_PACKAGE_VERSION, python_executable: str = "python3",
    runner: CommandRunner | None = None, install_if_missing: bool = True,
    idle_grace_seconds: float = PROCESS_OWNER_DAEMON_IDLE_GRACE_SECONDS,
) -> RemoteBootstrapResult:
    return asyncio.run(bootstrap_remote_owner_async(
        profile, endpoint_ref, package_version=package_version,
        python_executable=python_executable, runner=runner,
        install_if_missing=install_if_missing, idle_grace_seconds=idle_grace_seconds,
    ))


def probe_owner_package(
    profile: Any, *, python_executable: str = "python3",
    package_name: str = OWNER_PACKAGE_NAME, runner: CommandRunner | None = None,
) -> OwnerPackageProbe:
    return asyncio.run(probe_owner_package_async(
        profile, python_executable=python_executable, package_name=package_name, runner=runner,
    ))


def cleanup_remote_stale_endpoint(
    profile: Any, endpoint_ref: str | RemoteEndpointRef, *,
    python_executable: str = "python3", runner: CommandRunner | None = None,
) -> bool:
    return asyncio.run(cleanup_remote_stale_endpoint_async(
        profile, endpoint_ref, python_executable=python_executable, runner=runner,
    ))
