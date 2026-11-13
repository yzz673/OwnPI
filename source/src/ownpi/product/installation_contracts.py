from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

try:
    from .distribution_contracts import SUPPORTED_TARGETS
except ImportError:  # Standalone artifact installer imports resources as top-level modules.
    from distribution_contracts import SUPPORTED_TARGETS


INSTALLATION_SCHEMA_VERSION = 2
ComponentStatus = Literal[
    "ready",
    "incomplete",
    "not_installed",
    "unsupported",
    "failed",
]


@dataclass(frozen=True)
class InstalledComponent:
    component_id: str
    version: str
    status: ComponentStatus
    digest: str
    artifact_digest: str
    reason: str = ""

    @classmethod
    def from_dict(cls, payload: object) -> "InstalledComponent":
        data = _object(payload, "installed component")
        _only_fields(
            data,
            {"component_id", "version", "status", "digest", "artifact_digest", "reason"},
            "installed component",
        )
        status = _required_text(data, "status")
        if status not in {"ready", "incomplete", "not_installed", "unsupported", "failed"}:
            raise ValueError(f"unsupported component status: {status}")
        return cls(
            component_id=_required_text(data, "component_id"),
            version=_required_text(data, "version"),
            status=status,  # type: ignore[arg-type]
            digest=_sha256(data, "digest"),
            artifact_digest=_sha256(data, "artifact_digest"),
            reason=str(data.get("reason") or "").strip(),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "component_id": self.component_id,
            "version": self.version,
            "status": self.status,
            "digest": self.digest,
            "artifact_digest": self.artifact_digest,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class InstallationMetadata:
    base_version: str
    target: str
    channel: str
    python_runtime_version: str
    python_runtime_path: str
    managed_venv_path: str
    managed_tools_path: str
    components: tuple[InstalledComponent, ...]
    update_manifest_url: str = ""

    @classmethod
    def from_dict(cls, payload: object) -> "InstallationMetadata":
        data = _object(payload, "installation metadata")
        _only_fields(
            data,
            {
                "schema_version",
                "base_version",
                "target",
                "channel",
                "python_runtime_version",
                "python_runtime_path",
                "managed_venv_path",
                "managed_tools_path",
                "components",
                "update_manifest_url",
            },
            "installation metadata",
        )
        if int(data.get("schema_version", 0)) != INSTALLATION_SCHEMA_VERSION:
            raise ValueError(f"unsupported installation schema_version: {data.get('schema_version')}")
        target = _required_text(data, "target")
        if target not in SUPPORTED_TARGETS:
            raise ValueError(f"unsupported OwnPI installation target: {target}")
        raw_components = data.get("components")
        if not isinstance(raw_components, list):
            raise ValueError("installation metadata components must be a list")
        components = tuple(InstalledComponent.from_dict(item) for item in raw_components)
        if {item.component_id for item in components} < {"core", "dependency-bundle"}:
            raise ValueError("installation metadata requires core and dependency-bundle components")
        return cls(
            base_version=_required_text(data, "base_version"),
            target=target,
            channel=_required_text(data, "channel"),
            python_runtime_version=_required_text(data, "python_runtime_version"),
            python_runtime_path=_absolute_path(data, "python_runtime_path"),
            managed_venv_path=_absolute_path(data, "managed_venv_path"),
            managed_tools_path=_absolute_path(data, "managed_tools_path"),
            components=components,
            update_manifest_url=str(data.get("update_manifest_url") or "").strip(),
        )

    @classmethod
    def from_file(cls, path: Path) -> "InstallationMetadata":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": INSTALLATION_SCHEMA_VERSION,
            "base_version": self.base_version,
            "target": self.target,
            "channel": self.channel,
            "python_runtime_version": self.python_runtime_version,
            "python_runtime_path": self.python_runtime_path,
            "managed_venv_path": self.managed_venv_path,
            "managed_tools_path": self.managed_tools_path,
            "components": [component.to_dict() for component in self.components],
            "update_manifest_url": self.update_manifest_url,
        }


def _object(payload: object, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be an object")
    return payload


def _only_fields(data: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"unknown {label} fields: {', '.join(sorted(unknown))}")


def _required_text(data: dict[str, Any], field: str) -> str:
    value = str(data.get(field) or "").strip()
    if not value:
        raise ValueError(f"{field} is required")
    return value


def _absolute_path(data: dict[str, Any], field: str) -> str:
    value = _required_text(data, field)
    if not Path(value).is_absolute():
        raise ValueError(f"{field} must be absolute")
    return value


def _sha256(data: dict[str, Any], field: str) -> str:
    value = _required_text(data, field).casefold()
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{field} must be a SHA-256 digest")
    return value
