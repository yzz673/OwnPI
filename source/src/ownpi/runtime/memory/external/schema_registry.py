from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from ownpi.runtime.limits.resources import MAX_EXTERNAL_KNOWLEDGE_SCHEMA_BYTES

SCHEMA_REGISTRY_COMPONENT = ("nkf_consumer_schema_registry", "1.0.0")
_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_RANGE_PATTERN = re.compile(r"^>=([0-9]+\.[0-9]+\.[0-9]+) <([0-9]+\.[0-9]+\.[0-9]+)$")


class SchemaRegistryError(ValueError):
    """Raised when the bundled executable schema contract is invalid."""


@dataclass(frozen=True)
class SchemaRegistryEntry:
    schema_id: str
    component_type: str
    component_version: str
    path: str
    nullable_policy: str
    compatible_reader_range: str
    path_base: str

    def __post_init__(self) -> None:
        for name in ("schema_id", "component_type", "path"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or len(value) > 240:
                raise SchemaRegistryError(f"invalid registry {name}")
        if _VERSION_PATTERN.fullmatch(self.component_version) is None:
            raise SchemaRegistryError("invalid registry component version")
        if self.nullable_policy != "explicit_only":
            raise SchemaRegistryError("unsupported nullable policy")
        if self.path_base not in {
            "artifact_root",
            "release_root",
            "package_root",
            "delivery_root",
        }:
            raise SchemaRegistryError("unsupported registry path base")
        match = _RANGE_PATTERN.fullmatch(self.compatible_reader_range)
        if match is None or not _version_in_range(
            self.component_version,
            lower=match.group(1),
            upper=match.group(2),
        ):
            raise SchemaRegistryError("component version is outside its reader range")


@dataclass(frozen=True)
class BundledSchemaRegistry:
    entries: tuple[SchemaRegistryEntry, ...]
    _by_component: Mapping[tuple[str, str], SchemaRegistryEntry]
    _schemas_by_id: Mapping[str, Mapping[str, Any]]

    @classmethod
    def load(
        cls,
        *,
        resolve_release_path: Callable[[str], Path],
        registry_path: str = "schemas/schema_registry.json",
    ) -> BundledSchemaRegistry:
        registry_file = resolve_release_path(registry_path)
        payload = _load_json_object(registry_file)
        raw_entries = payload.get("schemas")
        if not isinstance(raw_entries, list):
            raise SchemaRegistryError("schema registry entries are missing")
        entries = tuple(_entry_from_payload(item) for item in raw_entries)
        by_component: dict[tuple[str, str], SchemaRegistryEntry] = {}
        raw_schemas_by_id: dict[str, Mapping[str, Any]] = {}
        path_by_schema_id: dict[str, str] = {}
        schemas_by_path: dict[str, Mapping[str, Any]] = {}
        for entry in entries:
            key = (entry.component_type, entry.component_version)
            if key in by_component:
                raise SchemaRegistryError("duplicate component schema registration")
            by_component[key] = entry
            if entry.path not in schemas_by_path:
                schema = _load_json_object(resolve_release_path(entry.path))
                try:
                    Draft202012Validator.check_schema(schema)
                except SchemaError as exc:
                    raise SchemaRegistryError(
                        "bundled schema is not valid Draft 2020-12"
                    ) from exc
                schemas_by_path[entry.path] = schema
            schema = schemas_by_path[entry.path]
            if schema.get("$id") != entry.schema_id:
                raise SchemaRegistryError(
                    "bundled schema id does not match registry entry"
                )
            previous_path = path_by_schema_id.get(entry.schema_id)
            if previous_path is not None and previous_path != entry.path:
                raise SchemaRegistryError(
                    "one schema id resolves to multiple schema files"
                )
            path_by_schema_id[entry.schema_id] = entry.path
            raw_schemas_by_id[entry.schema_id] = schema
        own_entry = by_component.get(SCHEMA_REGISTRY_COMPONENT)
        if own_entry is None:
            raise SchemaRegistryError("registry self-schema is not registered")
        _validate(payload, schemas_by_path[own_entry.path], label="schema registry")
        return cls(
            entries=entries,
            _by_component=MappingProxyType(by_component),
            _schemas_by_id=MappingProxyType(raw_schemas_by_id),
        )

    def entry_for(
        self, component_type: str, component_version: str
    ) -> SchemaRegistryEntry:
        try:
            return self._by_component[(component_type, component_version)]
        except KeyError as exc:
            raise SchemaRegistryError("component schema is not registered") from exc

    def schema_for_id(self, schema_id: str) -> Mapping[str, Any]:
        try:
            return self._schemas_by_id[schema_id]
        except KeyError as exc:
            raise SchemaRegistryError("schema id is not registered") from exc

    def validate_component(
        self,
        payload: object,
        *,
        component_type: str,
        component_version: str,
    ) -> None:
        entry = self.entry_for(component_type, component_version)
        _validate(payload, self.schema_for_id(entry.schema_id), label=component_type)

    def validate_schema_id(self, payload: object, *, schema_id: str) -> None:
        _validate(payload, self.schema_for_id(schema_id), label="schema object")


def load_bundled_schema_registry(
    *,
    resolve_release_path: Callable[[str], Path],
    registry_path: str = "schemas/schema_registry.json",
) -> BundledSchemaRegistry:
    return BundledSchemaRegistry.load(
        resolve_release_path=resolve_release_path,
        registry_path=registry_path,
    )


def _entry_from_payload(payload: object) -> SchemaRegistryEntry:
    if not isinstance(payload, dict):
        raise SchemaRegistryError("schema registry entry must be an object")
    try:
        return SchemaRegistryEntry(
            schema_id=payload["schema_id"],
            component_type=payload["component_type"],
            component_version=payload["component_version"],
            path=payload["path"],
            nullable_policy=payload["nullable_policy"],
            compatible_reader_range=payload["compatible_reader_range"],
            path_base=payload["path_base"],
        )
    except KeyError as exc:
        raise SchemaRegistryError("schema registry entry is incomplete") from exc


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        size = path.stat().st_size
        if size > MAX_EXTERNAL_KNOWLEDGE_SCHEMA_BYTES:
            raise SchemaRegistryError("schema control file exceeds size limit")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SchemaRegistryError("schema control file is unreadable") from exc
    if not isinstance(payload, dict):
        raise SchemaRegistryError("schema control file must contain an object")
    return payload


def _validate(payload: object, schema: Mapping[str, Any], *, label: str) -> None:
    try:
        Draft202012Validator(schema).validate(payload)
    except ValidationError as exc:
        location = "/".join(str(part) for part in exc.absolute_path)
        suffix = f" at {location[:120]}" if location else ""
        raise SchemaRegistryError(
            f"{label} failed JSON Schema validation{suffix}"
        ) from exc


def _version_in_range(version: str, *, lower: str, upper: str) -> bool:
    parsed = tuple(int(part) for part in version.split("."))
    return (
        tuple(int(part) for part in lower.split("."))
        <= parsed
        < tuple(int(part) for part in upper.split("."))
    )
