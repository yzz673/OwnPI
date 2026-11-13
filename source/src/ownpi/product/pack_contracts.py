from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PACK_SCHEMA_VERSION = 2
NEUROSCIENCE_PACK_ID = "neuroscience"


@dataclass(frozen=True)
class PackPythonDependency:
    requirement: str
    source: str

    @classmethod
    def from_dict(cls, payload: object) -> "PackPythonDependency":
        data = _object(payload, "Pack Python dependency")
        _only_fields(data, {"requirement", "source"}, "Pack Python dependency")
        return cls(
            requirement=_required_text(data, "requirement"),
            source=_required_text(data, "source"),
        )

    def to_dict(self) -> dict[str, str]:
        return {"requirement": self.requirement, "source": self.source}


@dataclass(frozen=True)
class PackManagedTool:
    tool_id: str
    version: str
    path: str
    entrypoints: tuple[str, ...]
    digest: str

    @classmethod
    def from_dict(cls, payload: object) -> "PackManagedTool":
        data = _object(payload, "Pack managed tool")
        _only_fields(data, {"tool_id", "version", "path", "entrypoints", "digest"}, "Pack managed tool")
        return cls(
            tool_id=_required_text(data, "tool_id"),
            version=_required_text(data, "version"),
            path=_relative_path(data, "path"),
            entrypoints=_text_tuple(data.get("entrypoints"), "entrypoints"),
            digest=_sha256(data, "digest"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "tool_id": self.tool_id,
            "version": self.version,
            "path": self.path,
            "entrypoints": list(self.entrypoints),
            "digest": self.digest,
        }


@dataclass(frozen=True)
class KnowledgeDelivery:
    delivery_id: str
    version: str
    path: str
    digest: str

    @classmethod
    def from_dict(cls, payload: object) -> "KnowledgeDelivery":
        data = _object(payload, "knowledge delivery")
        _only_fields(data, {"delivery_id", "version", "path", "digest"}, "knowledge delivery")
        return cls(
            delivery_id=_required_text(data, "delivery_id"),
            version=_required_text(data, "version"),
            path=_relative_path(data, "path"),
            digest=_sha256(data, "digest"),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "delivery_id": self.delivery_id,
            "version": self.version,
            "path": self.path,
            "digest": self.digest,
        }


@dataclass(frozen=True)
class PackManifest:
    pack_id: str
    name: str
    version: str
    core_requires: str
    enabled: bool
    skills: tuple[str, ...]
    wheelhouse: str
    python_dependencies: tuple[PackPythonDependency, ...]
    managed_tools: tuple[PackManagedTool, ...]
    knowledge_deliveries: tuple[KnowledgeDelivery, ...]
    native_tool_contributions: tuple[str, ...]
    integrity_sha256: str

    @classmethod
    def from_dict(cls, payload: object) -> "PackManifest":
        data = _object(payload, "Pack manifest")
        _only_fields(
            data,
            {
                "schema_version",
                "pack_id",
                "name",
                "version",
                "core_requires",
                "enabled",
                "skills",
                "wheelhouse",
                "python_dependencies",
                "managed_tools",
                "knowledge_deliveries",
                "native_tool_contributions",
                "integrity_sha256",
            },
            "Pack manifest",
        )
        if int(data.get("schema_version", 0)) != PACK_SCHEMA_VERSION:
            raise ValueError(f"unsupported Pack schema_version: {data.get('schema_version')}")
        pack_id = _required_text(data, "pack_id")
        if pack_id != NEUROSCIENCE_PACK_ID:
            raise ValueError(f"unsupported pack_id: {pack_id}")
        raw_dependencies = _object_list(data.get("python_dependencies"), "python_dependencies")
        raw_tools = _object_list(data.get("managed_tools"), "managed_tools")
        raw_deliveries = _object_list(data.get("knowledge_deliveries"), "knowledge_deliveries")
        manifest = cls(
            pack_id=pack_id,
            name=_required_text(data, "name"),
            version=_required_text(data, "version"),
            core_requires=_required_text(data, "core_requires"),
            enabled=_required_bool(data, "enabled"),
            skills=_text_tuple(data.get("skills"), "skills"),
            wheelhouse=_relative_path(data, "wheelhouse"),
            python_dependencies=tuple(PackPythonDependency.from_dict(item) for item in raw_dependencies),
            managed_tools=tuple(PackManagedTool.from_dict(item) for item in raw_tools),
            knowledge_deliveries=tuple(KnowledgeDelivery.from_dict(item) for item in raw_deliveries),
            native_tool_contributions=_text_tuple(
                data.get("native_tool_contributions"), "native_tool_contributions"
            ),
            integrity_sha256=_sha256(data, "integrity_sha256"),
        )
        _validate_unique_ids(manifest)
        return manifest

    @classmethod
    def from_file(cls, path: Path) -> "PackManifest":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": PACK_SCHEMA_VERSION,
            "pack_id": self.pack_id,
            "name": self.name,
            "version": self.version,
            "core_requires": self.core_requires,
            "enabled": self.enabled,
            "skills": list(self.skills),
            "wheelhouse": self.wheelhouse,
            "python_dependencies": [item.to_dict() for item in self.python_dependencies],
            "managed_tools": [item.to_dict() for item in self.managed_tools],
            "knowledge_deliveries": [item.to_dict() for item in self.knowledge_deliveries],
            "native_tool_contributions": list(self.native_tool_contributions),
            "integrity_sha256": self.integrity_sha256,
        }

    def component_paths(self) -> tuple[str, ...]:
        return (
            *self.skills,
            self.wheelhouse,
            *(item.path for item in self.managed_tools),
            *(item.path for item in self.knowledge_deliveries),
            *self.native_tool_contributions,
        )


def tree_digest(root: Path, *, excluded_names: frozenset[str] = frozenset({"pack.json"})) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if not path.is_file() or path.is_symlink() or path.name in excluded_names:
            continue
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def validate_pack_root(root: Path, manifest: PackManifest) -> None:
    resolved_root = root.resolve()
    for relative in manifest.component_paths():
        candidate = root / relative
        try:
            candidate.resolve().relative_to(resolved_root)
        except (OSError, ValueError) as exc:
            raise ValueError(f"Pack manifest path escapes Pack root: {relative}") from exc
        if not candidate.exists():
            raise ValueError(f"missing declared Pack component: {relative}")
    for tool in manifest.managed_tools:
        if tree_digest(root / tool.path, excluded_names=frozenset()) != tool.digest:
            raise ValueError(f"managed tool digest mismatch: {tool.tool_id}")
    for delivery in manifest.knowledge_deliveries:
        if tree_digest(root / delivery.path, excluded_names=frozenset()) != delivery.digest:
            raise ValueError(f"knowledge delivery digest mismatch: {delivery.delivery_id}")
    if tree_digest(root) != manifest.integrity_sha256:
        raise ValueError("Pack integrity_sha256 does not match Pack contents")


def _validate_unique_ids(manifest: PackManifest) -> None:
    for label, values in (
        ("managed tool", [item.tool_id for item in manifest.managed_tools]),
        ("knowledge delivery", [item.delivery_id for item in manifest.knowledge_deliveries]),
    ):
        if len(values) != len(set(values)):
            raise ValueError(f"duplicate {label} id in Pack manifest")


def _object(payload: object, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be an object")
    return payload


def _object_list(payload: object, label: str) -> list[dict[str, Any]]:
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise ValueError(f"{label} must be a list of objects")
    return payload


def _only_fields(data: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"unknown {label} fields: {', '.join(sorted(unknown))}")


def _required_text(data: dict[str, Any], field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()


def _required_bool(data: dict[str, Any], field: str) -> bool:
    value = data.get(field)
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _text_tuple(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{field} must be a list of non-empty strings")
    result = tuple(_relative_path({field: item}, field) for item in value)
    if len(result) != len(set(result)):
        raise ValueError(f"{field} contains duplicate paths")
    return result


def _relative_path(data: dict[str, Any], field: str) -> str:
    value = _required_text(data, field)
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{field} must stay inside the Pack: {value}")
    return path.as_posix()


def _sha256(data: dict[str, Any], field: str) -> str:
    value = _required_text(data, field).casefold()
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{field} must be a SHA-256 digest")
    return value


__all__ = [
    "KnowledgeDelivery",
    "NEUROSCIENCE_PACK_ID",
    "PACK_SCHEMA_VERSION",
    "PackManagedTool",
    "PackManifest",
    "PackPythonDependency",
    "tree_digest",
    "validate_pack_root",
]
