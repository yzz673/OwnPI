from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BUNDLE_SCHEMA_VERSION = 1
DEPENDENCY_BUNDLE_ID = "dependency-bundle"


@dataclass(frozen=True)
class ManagedToolManifest:
    tool_id: str
    version: str
    source_path: str
    executables: tuple[str, ...]
    inventory: str = ""

    @classmethod
    def from_dict(cls, payload: object) -> "ManagedToolManifest":
        data = _object(payload, "managed tool")
        _only_fields(data, {"tool_id", "version", "source_path", "executables", "inventory"}, "managed tool")
        return cls(
            tool_id=_required_text(data, "tool_id"),
            version=_required_text(data, "version"),
            source_path=_relative_path(data, "source_path"),
            executables=_text_tuple(data.get("executables")),
            inventory=_optional_relative_path(data, "inventory"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "tool_id": self.tool_id,
            "version": self.version,
            "source_path": self.source_path,
            "executables": list(self.executables),
            "inventory": self.inventory,
        }


@dataclass(frozen=True)
class DependencyBundleManifest:
    version: str
    core_requires: str
    python_dependencies: tuple[str, ...]
    capabilities: tuple[str, ...]
    managed_tools: tuple[ManagedToolManifest, ...]
    wheel_lock: tuple[dict[str, str], ...] = ()

    @classmethod
    def from_dict(cls, payload: object) -> "DependencyBundleManifest":
        data = _object(payload, "Dependency Bundle manifest")
        _only_fields(
            data,
            {
                "schema_version",
                "bundle_id",
                "version",
                "core_requires",
                "python_dependencies",
                "capabilities",
                "managed_tools",
                "wheel_lock",
            },
            "Dependency Bundle manifest",
        )
        if int(data.get("schema_version", 0)) != BUNDLE_SCHEMA_VERSION:
            raise ValueError(f"unsupported Dependency Bundle schema_version: {data.get('schema_version')}")
        if _required_text(data, "bundle_id") != DEPENDENCY_BUNDLE_ID:
            raise ValueError("unsupported Dependency Bundle ID")
        raw_tools = data.get("managed_tools")
        if not isinstance(raw_tools, list):
            raise ValueError("managed_tools must be a list")
        return cls(
            version=_required_text(data, "version"),
            core_requires=_required_text(data, "core_requires"),
            python_dependencies=_text_tuple(data.get("python_dependencies")),
            capabilities=_text_tuple(data.get("capabilities")),
            managed_tools=tuple(ManagedToolManifest.from_dict(item) for item in raw_tools),
            wheel_lock=_wheel_lock(data.get("wheel_lock")),
        )

    @classmethod
    def from_file(cls, path: Path) -> "DependencyBundleManifest":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "bundle_id": DEPENDENCY_BUNDLE_ID,
            "version": self.version,
            "core_requires": self.core_requires,
            "python_dependencies": list(self.python_dependencies),
            "capabilities": list(self.capabilities),
            "managed_tools": [tool.to_dict() for tool in self.managed_tools],
            "wheel_lock": [dict(item) for item in self.wheel_lock],
        }


@dataclass(frozen=True)
class DependencyBundleInspection:
    status: str
    version: str | None
    root: Path | None
    capabilities: dict[str, bool]
    missing_executables: tuple[str, ...] = ()
    reason: str = ""

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    def to_dict(self) -> dict[str, object]:
        return {
            "component_id": DEPENDENCY_BUNDLE_ID,
            "status": self.status,
            "ready": self.ready,
            "version": self.version,
            "root": str(self.root) if self.root else None,
            "capabilities": dict(self.capabilities),
            "missing_executables": list(self.missing_executables),
            "reason": self.reason,
        }


def dependency_bundle_root(app_home: Path) -> Path:
    return app_home.expanduser().resolve() / "components" / DEPENDENCY_BUNDLE_ID / "current"


def inspect_dependency_bundle(app_home: Path) -> DependencyBundleInspection:
    root = dependency_bundle_root(app_home)
    if not root.is_dir():
        return DependencyBundleInspection("not_installed", None, None, {})
    try:
        manifest = DependencyBundleManifest.from_file(root / "bundle.json")
        missing: list[str] = []
        for tool in manifest.managed_tools:
            tool_root = app_home.expanduser().resolve() / "tools" / tool.tool_id / tool.version
            for executable in tool.executables:
                if _find_executable(tool_root, executable) is None:
                    missing.append(executable)
        capabilities = {name: True for name in manifest.capabilities}
        if missing:
            capabilities["latex-pdf-build"] = False
            return DependencyBundleInspection(
                "incomplete",
                manifest.version,
                root,
                capabilities,
                tuple(sorted(set(missing))),
                "managed tools are incomplete",
            )
        return DependencyBundleInspection("ready", manifest.version, root, capabilities)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return DependencyBundleInspection("incomplete", None, root, {}, reason=str(exc))


def managed_tools_path(app_home: Path) -> str:
    tools_root = app_home.expanduser().resolve() / "tools"
    entries: list[str] = []
    if tools_root.is_dir():
        for tool_root in sorted(tools_root.glob("*/*")):
            for candidate in (tool_root / "bin", tool_root):
                if candidate.is_dir():
                    entries.append(str(candidate))
    return ":".join(dict.fromkeys(entries))


def _find_executable(root: Path, executable: str) -> Path | None:
    for candidate in (root / "bin" / executable, root / executable):
        if candidate.is_file() and candidate.stat().st_mode & 0o111:
            return candidate
    return None


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
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError("expected a list of non-empty strings")
    return tuple(item.strip() for item in value)


def _relative_path(data: dict[str, Any], field: str) -> str:
    value = _required_text(data, field)
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{field} must stay inside its component: {value}")
    return path.as_posix()


def _optional_relative_path(data: dict[str, Any], field: str) -> str:
    value = str(data.get(field) or "").strip()
    return _relative_path({field: value}, field) if value else ""


def _wheel_lock(value: object) -> tuple[dict[str, str], ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("wheel_lock must be a list")
    result: list[dict[str, str]] = []
    for item in value:
        data = _object(item, "wheel lock entry")
        _only_fields(data, {"name", "version", "filename"}, "wheel lock entry")
        result.append(
            {
                "name": _required_text(data, "name"),
                "version": _required_text(data, "version"),
                "filename": _relative_path(data, "filename"),
            }
        )
    return tuple(result)
