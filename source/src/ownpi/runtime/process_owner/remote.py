from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import stat
from typing import Any, Mapping
from uuid import uuid4


OWNER_PACKAGE_NAME = "ownpi"
OWNER_PACKAGE_VERSION = "0.1.0"
OWNER_SERVER_MODULE = "ownpi.runtime.process_owner.server"
OWNER_RELAY_MODULE = "ownpi.runtime.process_owner.relay"
_PROFILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_BOOTSTRAP_EXPORTS = frozenset(
    {
        "CommandRunner",
        "OwnerPackageProbe",
        "RemoteBootstrapResult",
        "bootstrap_remote_owner",
        "build_owner_install_command",
        "build_owner_version_probe_command",
        "build_remote_bootstrap_script",
        "build_remote_stale_probe_command",
        "build_ssh_command",
        "cleanup_remote_stale_endpoint",
        "probe_owner_package",
    }
)


class RemoteOwnerError(RuntimeError):
    """Base error for remote owner bootstrap and endpoint discovery."""


class RemoteOwnerVersionMismatch(RemoteOwnerError):
    """Raised when the remote owner package is not the requested version."""


class StaleEndpointError(RemoteOwnerError):
    """Raised when an endpoint manifest no longer points at a live owner."""


@dataclass(frozen=True)
class RemoteEndpointRef:
    """Non-secret durable reference: local profile identity plus remote manifest path."""

    profile_name: str
    manifest_path: PurePosixPath

    @classmethod
    def parse(cls, value: str) -> "RemoteEndpointRef":
        raw = str(value or "").strip()
        if raw.count(":") != 1:
            raise ValueError(
                "endpoint_ref must have the form profile:/absolute/manifest.json"
            )
        profile_name, raw_path = raw.split(":", 1)
        if not _PROFILE_RE.fullmatch(profile_name):
            raise ValueError("endpoint_ref profile name is invalid")
        if not raw_path.startswith("/") or any(char.isspace() for char in raw_path):
            raise ValueError(
                "endpoint_ref manifest path must be absolute and whitespace-free"
            )
        if "\x00" in raw_path:
            raise ValueError("endpoint_ref manifest path contains NUL")
        path = PurePosixPath(raw_path)
        if any(part in {".", ".."} for part in raw_path.split("/")):
            raise ValueError("endpoint_ref manifest path must not contain dot segments")
        if path.suffix != ".json":
            raise ValueError("endpoint_ref manifest path must end with .json")
        if path.parent == PurePosixPath("/") or not _RUN_ID_RE.fullmatch(
            path.parent.name
        ):
            raise ValueError("endpoint_ref must identify a run-scoped directory")
        return cls(profile_name=profile_name, manifest_path=path)

    @property
    def profile(self) -> str:
        return self.profile_name

    @property
    def run_dir(self) -> PurePosixPath:
        return self.manifest_path.parent

    def __str__(self) -> str:
        return f"{self.profile_name}:{self.manifest_path}"

    def to_dict(self) -> dict[str, str]:
        return {
            "profile_name": self.profile_name,
            "manifest_path": str(self.manifest_path),
        }


@dataclass(frozen=True)
class RemoteEndpointManifest:
    socket_path: PurePosixPath
    protocol_major: int
    protocol_minor: int
    pid: int
    package_version: str = ""
    run_dir: PurePosixPath | None = None

    @classmethod
    def from_value(cls, value: Mapping[str, Any]) -> "RemoteEndpointManifest":
        allowed = {
            "socket_path",
            "protocol_major",
            "protocol_minor",
            "pid",
            "package_version",
            "run_dir",
        }
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError(f"endpoint manifest contains unknown fields: {unknown}")
        socket_path = _absolute_posix_path(value.get("socket_path"), "socket_path")
        major = _non_negative_int(value.get("protocol_major"), "protocol_major")
        minor = _non_negative_int(value.get("protocol_minor"), "protocol_minor")
        pid = _non_negative_int(value.get("pid"), "pid")
        package_version = str(value.get("package_version", "") or "").strip()
        if any(char.isspace() for char in package_version):
            raise ValueError(
                "endpoint manifest package_version must be whitespace-free"
            )
        raw_run_dir = value.get("run_dir")
        run_dir = _absolute_posix_path(raw_run_dir, "run_dir") if raw_run_dir else None
        return cls(socket_path, major, minor, pid, package_version, run_dir)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "socket_path": str(self.socket_path),
            "protocol_major": self.protocol_major,
            "protocol_minor": self.protocol_minor,
            "pid": self.pid,
        }
        if self.package_version:
            payload["package_version"] = self.package_version
        if self.run_dir is not None:
            payload["run_dir"] = str(self.run_dir)
        return payload


@dataclass(frozen=True)
class RemoteOwnerRunPaths:
    run_dir: PurePosixPath
    socket_path: PurePosixPath
    manifest_path: PurePosixPath
    log_path: PurePosixPath


def parse_endpoint_ref(value: str) -> RemoteEndpointRef:
    return RemoteEndpointRef.parse(value)


def endpoint_ref_for(
    *, profile_name: str, manifest_path: str | PurePosixPath
) -> RemoteEndpointRef:
    return RemoteEndpointRef.parse(f"{profile_name}:{manifest_path}")


def resolve_endpoint_ref(
    profile: Any, value: str | RemoteEndpointRef
) -> RemoteEndpointRef:
    ref = value if isinstance(value, RemoteEndpointRef) else parse_endpoint_ref(value)
    profile_name = _profile_text(profile, "name")
    if not profile_name or profile_name != ref.profile_name:
        raise ValueError("endpoint_ref profile does not match SSH profile")
    return ref


def run_scoped_paths(
    *, base_dir: str | PurePosixPath, run_id: str
) -> RemoteOwnerRunPaths:
    base = PurePosixPath(str(base_dir))
    if not base.is_absolute():
        raise ValueError("remote owner base_dir must be absolute")
    if not _RUN_ID_RE.fullmatch(str(run_id or "")):
        raise ValueError("remote owner run_id is invalid")
    run_dir = base / str(run_id)
    return RemoteOwnerRunPaths(
        run_dir=run_dir,
        socket_path=run_dir / "owner.sock",
        manifest_path=run_dir / "endpoint.json",
        log_path=run_dir / "owner.log",
    )


def ensure_run_scoped_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise RemoteOwnerError(f"run-scoped directory must not be a symlink: {path}")
    os.chmod(path, 0o700)
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise RemoteOwnerError(f"run-scoped directory is not private: {path}")
    return path


def read_endpoint_manifest(
    path: Path, *, require_private: bool = True
) -> RemoteEndpointManifest:
    try:
        path_stat = path.lstat()
    except FileNotFoundError as exc:
        raise StaleEndpointError(f"endpoint manifest is missing: {path}") from exc
    mode = stat.S_IMODE(path_stat.st_mode)
    if not stat.S_ISREG(path_stat.st_mode):
        raise RemoteOwnerError(f"endpoint manifest is not a regular file: {path}")
    if require_private and mode != 0o600:
        raise RemoteOwnerError(f"endpoint manifest must have mode 0600: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StaleEndpointError(f"endpoint manifest is unreadable: {path}") from exc
    if not isinstance(payload, Mapping):
        raise RemoteOwnerError("endpoint manifest must contain an object")
    manifest = RemoteEndpointManifest.from_value(payload)
    manifest_dir = PurePosixPath(str(path.parent))
    if manifest.socket_path.parent != manifest_dir:
        raise RemoteOwnerError(
            "endpoint manifest socket_path must remain inside the run directory"
        )
    if manifest.run_dir is not None and manifest.run_dir != manifest_dir:
        raise RemoteOwnerError("endpoint manifest run_dir does not match its parent")
    return manifest


def write_endpoint_manifest(path: Path, manifest: RemoteEndpointManifest) -> None:
    _validate_manifest_location(path, manifest)
    ensure_run_scoped_directory(path.parent)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid4().hex}")
    payload = (
        json.dumps(manifest.to_dict(), sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def endpoint_is_stale(
    manifest_path: Path,
    *,
    connect_timeout_seconds: float = 0.2,
) -> bool:
    try:
        manifest = read_endpoint_manifest(manifest_path)
    except (StaleEndpointError, RemoteOwnerError):
        return True
    socket_path = Path(str(manifest.socket_path))
    if (
        socket_path.is_symlink()
        or not socket_path.exists()
        or not stat.S_ISSOCK(socket_path.stat().st_mode)
    ):
        return True
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(max(0.01, connect_timeout_seconds))
    try:
        connection.connect(str(socket_path))
    except (ConnectionError, OSError):
        return True
    finally:
        connection.close()
    return False


def cleanup_stale_endpoint(manifest_path: Path) -> bool:
    stale = endpoint_is_stale(manifest_path)
    if not stale:
        raise RemoteOwnerError(f"refusing to remove live endpoint: {manifest_path}")
    try:
        manifest = read_endpoint_manifest(manifest_path)
    except RemoteOwnerError:
        manifest = None
    if manifest is not None:
        if not endpoint_is_stale(manifest_path):
            raise RemoteOwnerError(
                f"refusing to remove revived endpoint: {manifest_path}"
            )
        Path(str(manifest.socket_path)).unlink(missing_ok=True)
    manifest_path.unlink(missing_ok=True)
    return True


def _absolute_posix_path(value: Any, name: str) -> PurePosixPath:
    text = str(value or "")
    path = PurePosixPath(text)
    if not text.startswith("/") or path == PurePosixPath("/"):
        raise ValueError(f"endpoint manifest {name} must be an absolute path")
    if any(char.isspace() for char in text) or "\x00" in text:
        raise ValueError(f"endpoint manifest {name} contains unsafe characters")
    return path


def _non_negative_int(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"endpoint manifest {name} must be a non-negative integer")
    return value


def _validate_manifest_location(path: Path, manifest: RemoteEndpointManifest) -> None:
    manifest_dir = PurePosixPath(str(path.parent))
    if PurePosixPath(str(manifest.socket_path)).parent != manifest_dir:
        raise ValueError(
            "endpoint manifest socket_path must remain inside the run directory"
        )
    if (
        manifest.run_dir is not None
        and PurePosixPath(str(manifest.run_dir)) != manifest_dir
    ):
        raise ValueError("endpoint manifest run_dir does not match its parent")


def _profile_text(profile: Any, name: str, default: str = "") -> str:
    return str(getattr(profile, name, default) or default).strip()


def _profile_int(profile: Any, name: str, default: int) -> int:
    return int(getattr(profile, name, default) or default)


def __getattr__(name: str) -> Any:
    if name not in _BOOTSTRAP_EXPORTS:
        raise AttributeError(name)
    from ownpi.runtime.process_owner import remote_bootstrap

    return getattr(remote_bootstrap, name)
