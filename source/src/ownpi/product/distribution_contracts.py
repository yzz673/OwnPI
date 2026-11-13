from __future__ import annotations

import json
import platform
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


DISTRIBUTION_SCHEMA_VERSION = 2
SUPPORTED_TARGETS = frozenset(
    {
        "darwin-arm64",
        "darwin-x86_64",
        "linux-arm64",
        "linux-x86_64",
    }
)


def canonical_target(*, system: str | None = None, machine: str | None = None) -> str:
    raw_system = (system or platform.system()).strip().casefold()
    raw_machine = (machine or platform.machine()).strip().casefold()
    systems = {"darwin": "darwin", "macos": "darwin", "linux": "linux"}
    machines = {
        "aarch64": "arm64",
        "arm64": "arm64",
        "amd64": "x86_64",
        "x64": "x86_64",
        "x86_64": "x86_64",
    }
    target = f"{systems.get(raw_system, raw_system)}-{machines.get(raw_machine, raw_machine)}"
    if target not in SUPPORTED_TARGETS:
        raise ValueError(f"unsupported OwnPI distribution target: {target}")
    return target


@dataclass(frozen=True)
class ComponentManifest:
    component_id: str
    version: str
    path: str
    requirement: str = ""
    wheelhouse: str = ""
    python_dependencies: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    digest: str = ""

    @classmethod
    def from_dict(cls, payload: object) -> "ComponentManifest":
        data = _object(payload, "component manifest")
        _only_fields(
            data,
            {
                "component_id",
                "version",
                "path",
                "requirement",
                "wheelhouse",
                "python_dependencies",
                "capabilities",
                "digest",
            },
            "component manifest",
        )
        path = _relative_path(data, "path")
        wheelhouse = _optional_relative_path(data, "wheelhouse")
        return cls(
            component_id=_required_text(data, "component_id"),
            version=_required_text(data, "version"),
            path=path,
            requirement=str(data.get("requirement") or "").strip(),
            wheelhouse=wheelhouse,
            python_dependencies=_text_tuple(data.get("python_dependencies")),
            capabilities=_text_tuple(data.get("capabilities")),
            digest=_sha256(data, "digest"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "component_id": self.component_id,
            "version": self.version,
            "path": self.path,
            "requirement": self.requirement,
            "wheelhouse": self.wheelhouse,
            "python_dependencies": list(self.python_dependencies),
            "capabilities": list(self.capabilities),
            "digest": self.digest,
        }


@dataclass(frozen=True)
class BaseArtifactManifest:
    version: str
    channel: str
    target: str
    python_runtime: PythonRuntimeManifest
    components: tuple[ComponentManifest, ...]

    @classmethod
    def from_dict(cls, payload: object) -> "BaseArtifactManifest":
        data = _object(payload, "base artifact manifest")
        _only_fields(
            data,
            {"schema_version", "kind", "version", "channel", "target", "python_runtime", "components"},
            "base artifact manifest",
        )
        if int(data.get("schema_version", 0)) != DISTRIBUTION_SCHEMA_VERSION:
            raise ValueError(f"unsupported base artifact schema_version: {data.get('schema_version')}")
        if _required_text(data, "kind") != "ownpi-base":
            raise ValueError("base artifact kind must be ownpi-base")
        target = _required_text(data, "target")
        if target not in SUPPORTED_TARGETS:
            raise ValueError(f"unsupported OwnPI distribution target: {target}")
        raw_components = data.get("components")
        if not isinstance(raw_components, list):
            raise ValueError("base artifact components must be a list")
        components = tuple(ComponentManifest.from_dict(item) for item in raw_components)
        if {item.component_id for item in components} != {"core", "dependency-bundle"}:
            raise ValueError("base artifact requires core and dependency-bundle components")
        return cls(
            version=_required_text(data, "version"),
            channel=_required_text(data, "channel"),
            target=target,
            python_runtime=PythonRuntimeManifest.from_dict(data.get("python_runtime")),
            components=components,
        )

    @classmethod
    def from_file(cls, path: Path) -> "BaseArtifactManifest":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": DISTRIBUTION_SCHEMA_VERSION,
            "kind": "ownpi-base",
            "version": self.version,
            "channel": self.channel,
            "target": self.target,
            "python_runtime": self.python_runtime.to_dict(),
            "components": [component.to_dict() for component in self.components],
        }


@dataclass(frozen=True)
class PackArtifactManifest:
    version: str
    channel: str
    target: str
    component: ComponentManifest

    @classmethod
    def from_dict(cls, payload: object) -> "PackArtifactManifest":
        data = _object(payload, "Pack artifact manifest")
        _only_fields(
            data,
            {"schema_version", "kind", "version", "channel", "target", "component"},
            "Pack artifact manifest",
        )
        if int(data.get("schema_version", 0)) != DISTRIBUTION_SCHEMA_VERSION:
            raise ValueError(f"unsupported Pack artifact schema_version: {data.get('schema_version')}")
        if _required_text(data, "kind") != "ownpi-neuroscience-pack":
            raise ValueError("Pack artifact kind must be ownpi-neuroscience-pack")
        target = _required_text(data, "target")
        if target not in SUPPORTED_TARGETS:
            raise ValueError(f"unsupported OwnPI distribution target: {target}")
        component = ComponentManifest.from_dict(data.get("component"))
        if component.component_id != "neuroscience":
            raise ValueError("Pack artifact component must be neuroscience")
        return cls(
            version=_required_text(data, "version"),
            channel=_required_text(data, "channel"),
            target=target,
            component=component,
        )

    @classmethod
    def from_file(cls, path: Path) -> "PackArtifactManifest":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": DISTRIBUTION_SCHEMA_VERSION,
            "kind": "ownpi-neuroscience-pack",
            "version": self.version,
            "channel": self.channel,
            "target": self.target,
            "component": self.component.to_dict(),
        }


@dataclass(frozen=True)
class PythonRuntimeManifest:
    version: str
    path: str
    executable: str

    @classmethod
    def from_dict(cls, payload: object) -> "PythonRuntimeManifest":
        data = _object(payload, "python runtime manifest")
        _only_fields(data, {"version", "path", "executable"}, "python runtime manifest")
        return cls(
            version=_required_text(data, "version"),
            path=_relative_path(data, "path"),
            executable=_relative_path(data, "executable"),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "version": self.version,
            "path": self.path,
            "executable": self.executable,
        }


@dataclass(frozen=True)
class DistributionManifest:
    kind: Literal["ownpi-base", "ownpi-neuroscience-pack"]
    version: str
    channel: str
    target: str
    artifact: str
    checksum: str
    components: tuple[ComponentManifest, ...]
    python_runtime: PythonRuntimeManifest | None = None

    @classmethod
    def from_dict(cls, payload: object) -> "DistributionManifest":
        data = _object(payload, "distribution manifest")
        _only_fields(
            data,
            {
                "schema_version",
                "kind",
                "version",
                "channel",
                "target",
                "artifact",
                "checksum",
                "components",
                "python_runtime",
            },
            "distribution manifest",
        )
        if int(data.get("schema_version", 0)) != DISTRIBUTION_SCHEMA_VERSION:
            raise ValueError(f"unsupported distribution schema_version: {data.get('schema_version')}")
        kind = _required_text(data, "kind")
        if kind not in {"ownpi-base", "ownpi-neuroscience-pack"}:
            raise ValueError(f"unsupported distribution kind: {kind}")
        target = _required_text(data, "target")
        if target not in SUPPORTED_TARGETS:
            raise ValueError(f"unsupported OwnPI distribution target: {target}")
        raw_components = data.get("components")
        if not isinstance(raw_components, list) or not raw_components:
            raise ValueError("distribution manifest requires components")
        components = tuple(ComponentManifest.from_dict(item) for item in raw_components)
        expected = (
            {"core", "dependency-bundle"}
            if kind == "ownpi-base"
            else {"neuroscience"}
        )
        actual = {component.component_id for component in components}
        if actual != expected:
            raise ValueError(
                f"{kind} components must be {', '.join(sorted(expected))}; got {', '.join(sorted(actual))}"
            )
        raw_runtime = data.get("python_runtime")
        runtime = PythonRuntimeManifest.from_dict(raw_runtime) if raw_runtime is not None else None
        if kind == "ownpi-base" and runtime is None:
            raise ValueError("ownpi-base manifest requires python_runtime")
        if kind != "ownpi-base" and runtime is not None:
            raise ValueError("Neuroscience Pack manifest must not include python_runtime")
        return cls(
            kind=kind,  # type: ignore[arg-type]
            version=_required_text(data, "version"),
            channel=_required_text(data, "channel"),
            target=target,
            artifact=_required_text(data, "artifact"),
            checksum=_sha256(data, "checksum"),
            components=components,
            python_runtime=runtime,
        )

    @classmethod
    def from_file(cls, path: Path) -> "DistributionManifest":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": DISTRIBUTION_SCHEMA_VERSION,
            "kind": self.kind,
            "version": self.version,
            "channel": self.channel,
            "target": self.target,
            "artifact": self.artifact,
            "checksum": self.checksum,
            "components": [component.to_dict() for component in self.components],
            "python_runtime": self.python_runtime.to_dict() if self.python_runtime else None,
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


def _text_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError("expected a list of non-empty strings")
    return tuple(item.strip() for item in value)


def _relative_path(data: dict[str, Any], field: str) -> str:
    value = _required_text(data, field)
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{field} must stay inside the artifact: {value}")
    return path.as_posix()


def _optional_relative_path(data: dict[str, Any], field: str) -> str:
    value = str(data.get(field) or "").strip()
    if not value:
        return ""
    return _relative_path({field: value}, field)


def _sha256(data: dict[str, Any], field: str) -> str:
    value = _required_text(data, field).casefold()
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{field} must be a SHA-256 digest")
    return value
