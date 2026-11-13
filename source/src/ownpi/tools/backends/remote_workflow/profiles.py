from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Any

from ownpi.runtime.permissions.models import EffectivePermissions, FileSystemAccessMode
from ownpi.runtime.permissions.policy import lexical_absolute_path, require_file_access

DEFAULT_CONFIG_DIR = Path("~/.config/ownpi/remote-workflow").expanduser()


@dataclass(frozen=True)
class RemoteProfile:
    name: str
    host: str
    user: str
    local_root: Path
    remote_root: str
    port: int = 22
    conda_env: str = ""
    strict_host_key_checking: str = "accept-new"
    connect_timeout_seconds: int = 10

    @property
    def target(self) -> str:
        return f"{self.user}@{self.host}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "host": self.host,
            "user": self.user,
            "port": self.port,
            "local_root": str(self.local_root),
            "remote_root": self.remote_root,
            "conda_env": self.conda_env,
            "strict_host_key_checking": self.strict_host_key_checking,
            "connect_timeout_seconds": self.connect_timeout_seconds,
        }


def resolve_config_dir(raw_config_dir: str | None = None) -> Path:
    value = raw_config_dir or os.environ.get("OWNPI_REMOTE_WORKFLOW_CONFIG_DIR")
    return lexical_absolute_path(value or DEFAULT_CONFIG_DIR)


def profiles_dir(config_dir: Path) -> Path:
    return config_dir / "profiles"


def profile_path(config_dir: Path, name: str) -> Path:
    safe_name = _safe_profile_name(name)
    return profiles_dir(config_dir) / f"{safe_name}.toml"


def list_profiles(config_dir: Path, *, effective_permissions: EffectivePermissions | None = None) -> list[RemoteProfile]:
    directory = profiles_dir(config_dir)
    if effective_permissions is not None:
        require_file_access(effective_permissions, directory, FileSystemAccessMode.READ)
    if not directory.exists():
        return []
    profiles: list[RemoteProfile] = []
    for path in sorted(directory.glob("*.toml")):
        profiles.append(load_profile(config_dir, path.stem, effective_permissions=effective_permissions))
    return profiles


def load_profile(config_dir: Path, name: str, *, effective_permissions: EffectivePermissions | None = None) -> RemoteProfile:
    path = profile_path(config_dir, name)
    if effective_permissions is not None:
        require_file_access(effective_permissions, path, FileSystemAccessMode.READ)
    if not path.exists():
        raise FileNotFoundError(f"profile not found: {name}")
    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    return profile_from_dict(payload, fallback_name=path.stem)


def profile_from_dict(payload: dict[str, Any], *, fallback_name: str = "") -> RemoteProfile:
    required = ("host", "user", "local_root", "remote_root")
    missing = [field for field in required if not str(payload.get(field, "")).strip()]
    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"profile is missing required field(s): {joined}")
    return RemoteProfile(
        name=str(payload.get("name") or fallback_name).strip() or fallback_name,
        host=str(payload["host"]).strip(),
        user=str(payload["user"]).strip(),
        port=int(payload.get("port", 22) or 22),
        local_root=lexical_absolute_path(str(payload["local_root"])),
        remote_root=str(payload["remote_root"]).strip().rstrip("/"),
        conda_env=str(payload.get("conda_env", "") or "").strip(),
        strict_host_key_checking=str(payload.get("strict_host_key_checking", "accept-new") or "accept-new"),
        connect_timeout_seconds=int(payload.get("connect_timeout_seconds", 10) or 10),
    )


def write_profile(config_dir: Path, profile: RemoteProfile, *, overwrite: bool = False) -> Path:
    path = profile_path(config_dir, profile.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(f"profile already exists: {profile.name}")
    path.write_text(_profile_toml(profile), encoding="utf-8")
    return path


def remove_profile(config_dir: Path, name: str) -> Path:
    path = profile_path(config_dir, name)
    if not path.exists():
        raise FileNotFoundError(f"profile not found: {name}")
    path.unlink()
    return path


def _profile_toml(profile: RemoteProfile) -> str:
    lines = [
        f'name = "{_escape_toml_string(profile.name)}"',
        f'host = "{_escape_toml_string(profile.host)}"',
        f'user = "{_escape_toml_string(profile.user)}"',
        f"port = {profile.port}",
        f'local_root = "{_escape_toml_string(str(profile.local_root))}"',
        f'remote_root = "{_escape_toml_string(profile.remote_root)}"',
        f'conda_env = "{_escape_toml_string(profile.conda_env)}"',
        f'strict_host_key_checking = "{_escape_toml_string(profile.strict_host_key_checking)}"',
        f"connect_timeout_seconds = {profile.connect_timeout_seconds}",
        "",
    ]
    return "\n".join(lines)


def _escape_toml_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _safe_profile_name(name: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in name.strip())
    cleaned = cleaned.strip("_-")
    if not cleaned:
        raise ValueError("profile name is empty")
    return cleaned
