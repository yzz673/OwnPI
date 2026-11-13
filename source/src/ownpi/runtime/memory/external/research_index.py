from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from ownpi.runtime.limits.resources import (
    MAX_EXTERNAL_KNOWLEDGE_PACKAGE_CONTROL_JSON_BYTES,
)
from ownpi.runtime.memory.external.contracts import (
    ExternalKnowledgeReleasePin,
    ExternalKnowledgeSourceId,
)
from ownpi.runtime.memory.external.release import SafePathResolver
from ownpi.runtime.memory.external.schema_registry import (
    load_bundled_schema_registry,
)


SOURCE_ID = ExternalKnowledgeSourceId.RESEARCH_STRATEGY


class ResearchStrategyAdapterError(ValueError):
    """Raised when a pinned Research package violates its declared contract."""


@dataclass(frozen=True)
class StrategyRecord:
    stable_id: str
    cell_id: str
    archetype: str
    dimension: str
    item: Mapping[str, Any]
    detail_locator: str
    relation_locator: str


@dataclass(frozen=True)
class DimensionRecord:
    cell_id: str
    archetype: str
    dimension: str
    context: Mapping[str, Any]
    card_locator: str
    supports_locator: str
    strategies: tuple[StrategyRecord, ...]
    strategy_digest: str


@dataclass(frozen=True)
class PaperRecord:
    stable_id: str
    support_id: str
    item: Mapping[str, Any]
    locator: str


class ResearchPackageIndex:
    """Validated, bounded lookup state for one immutable Research package."""

    def __init__(self, delivery_root: Path, pin: ExternalKnowledgeReleasePin) -> None:
        if not isinstance(pin, ExternalKnowledgeReleasePin):
            raise TypeError("pin must use ExternalKnowledgeReleasePin")
        self.pin = pin
        delivery = SafePathResolver(delivery_root)
        self.release = SafePathResolver(delivery.resolve_dir(pin.release_root_relative))
        catalog_path = self.release.resolve_file("package_catalog.json")
        require_digest(catalog_path, pin.catalog_sha256, "catalog")
        catalog = load_json(catalog_path)
        self.registry = load_bundled_schema_registry(
            resolve_release_path=self.release.resolve_file,
            registry_path=required_string(catalog, "schema_registry_path"),
        )
        self.registry.validate_component(
            catalog,
            component_type="nkf_release_package_catalog",
            component_version="3.0.0",
        )
        package_item = self._package_item(catalog)
        self.package = SafePathResolver(
            self.release.resolve_dir(required_string(package_item, "package_root"))
        )
        self.source = next(item for item in pin.sources if item.source_id == SOURCE_ID)
        manifest_path = self.release.resolve_file(
            required_string(package_item, "manifest_path")
        )
        require_under_root(manifest_path, self.package.root, "manifest")
        require_digest(manifest_path, self.source.manifest_sha256, "manifest")
        self.manifest = load_json(manifest_path)
        self.registry.validate_component(
            self.manifest,
            component_type="research_strategy_package_manifest",
            component_version=self.source.schema_version,
        )
        self._validate_identity(package_item)
        self._validate_inventory(package_item)
        archetypes = self._load_archetypes()
        dimension_entries = self._load_index("dimension_index_path", "dimensions")
        contexts = self._load_contexts(package_item)
        self.papers_by_support, self.papers_by_stable = self._load_papers()
        self.dimensions, self.strategies = self._load_hierarchy(
            archetypes, dimension_entries, contexts
        )

    def component(self, locator: str) -> dict[str, Any]:
        payload = load_json(self.package.resolve_file(locator))
        self.validate_component(payload)
        return payload

    def validate_component(self, payload: Mapping[str, Any]) -> None:
        self.registry.validate_component(
            payload,
            component_type=required_string(payload, "package_type"),
            component_version=required_string(payload, "schema_version"),
        )

    def _package_item(self, catalog: Mapping[str, Any]) -> Mapping[str, Any]:
        matches = [
            item
            for item in required_list(catalog, "packages")
            if isinstance(item, dict) and item.get("source_id") == SOURCE_ID.value
        ]
        if len(matches) != 1:
            raise ResearchStrategyAdapterError("catalog Research source is not unique")
        return matches[0]

    def _validate_identity(self, item: Mapping[str, Any]) -> None:
        expected = {
            "package_id": self.source.package_id,
            "package_version": self.source.package_version,
            "schema_version": self.source.schema_version,
            "manifest_sha256": self.source.manifest_sha256,
            "content_inventory_sha256": self.source.content_inventory_sha256,
        }
        if any(item.get(key) != value for key, value in expected.items()):
            raise ResearchStrategyAdapterError("catalog package identity mismatch")
        manifest = self.manifest
        if (
            manifest.get("package_id") != self.source.package_id
            or manifest.get("version") != self.source.package_version
            or manifest.get("schema_version") != self.source.schema_version
            or manifest.get("package_content_digest")
            != self.source.package_content_digest
        ):
            raise ResearchStrategyAdapterError("manifest package identity mismatch")

    def _validate_inventory(self, item: Mapping[str, Any]) -> None:
        path = self.package.resolve_file(
            required_string(self.manifest, "content_inventory_path")
        )
        if path != self.release.resolve_file(
            required_string(item, "content_inventory_path")
        ):
            raise ResearchStrategyAdapterError("inventory locator identity mismatch")
        require_digest(path, self.source.content_inventory_sha256, "inventory")
        inventory = load_json(path)
        self.registry.validate_component(
            inventory,
            component_type="nkf_package_content_inventory",
            component_version="1.0.0",
        )
        if (
            inventory.get("package_content_digest")
            != self.source.package_content_digest
        ):
            raise ResearchStrategyAdapterError("inventory package digest mismatch")

    def _load_contexts(self, item: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
        path = self.package.resolve_file(
            required_string(self.manifest, "agent_context_index_path")
        )
        if path != self.release.resolve_file(
            required_string(item, "agent_context_index_path")
        ):
            raise ResearchStrategyAdapterError("context locator identity mismatch")
        payload = load_json(path)
        self.registry.validate_component(
            payload,
            component_type="agent_context_index",
            component_version=self.source.schema_version,
        )
        contexts = required_list(payload, "contexts")
        result = {
            required_string(value, "source_ref"): value
            for value in contexts
            if isinstance(value, dict)
        }
        if len(result) != len(contexts):
            raise ResearchStrategyAdapterError("context identities must be unique")
        return result

    def _load_archetypes(self) -> dict[str, Mapping[str, Any]]:
        entries = self._load_index("archetype_index_path", "archetypes")
        result: dict[str, Mapping[str, Any]] = {}
        for entry in entries.values():
            archetype = required_string(entry, "primary_strategy_archetype")
            card = self.component(required_string(entry, "archetype_card_path"))
            local_index = self.component(required_string(entry, "dimension_index_path"))
            if (
                card.get("primary_strategy_archetype") != archetype
                or local_index.get("primary_strategy_archetype") != archetype
                or archetype in result
            ):
                raise ResearchStrategyAdapterError("archetype identity mismatch")
            result[archetype] = local_index
        return result

    def _load_index(
        self, manifest_key: str, items_key: str
    ) -> dict[str, Mapping[str, Any]]:
        payload = self.component(required_string(self.manifest, manifest_key))
        entries = required_list(payload, items_key)
        identity = {
            "archetypes": "primary_strategy_archetype",
            "dimensions": "cell_id",
        }[items_key]
        result = {
            required_string(value, identity): value
            for value in entries
            if isinstance(value, dict)
        }
        if len(result) != len(entries):
            raise ResearchStrategyAdapterError(f"{items_key} identities must be unique")
        return result

    def _load_papers(self) -> tuple[dict[str, PaperRecord], dict[str, PaperRecord]]:
        payload = self.component(
            required_string(self.manifest, "paper_support_index_path")
        )
        by_support: dict[str, PaperRecord] = {}
        by_stable: dict[str, PaperRecord] = {}
        for item in required_list(payload, "papers"):
            if not isinstance(item, dict):
                raise ResearchStrategyAdapterError("paper index entry is invalid")
            support_id = required_string(item, "support_id")
            stable_id = f"paper-{hashlib.sha256(support_id.encode()).hexdigest()[:32]}"
            paper = PaperRecord(
                stable_id,
                support_id,
                item,
                required_string(item, "paper_support_path"),
            )
            if support_id in by_support or stable_id in by_stable:
                raise ResearchStrategyAdapterError("duplicate paper support identity")
            by_support[support_id], by_stable[stable_id] = paper, paper
        return by_support, by_stable

    def _load_hierarchy(
        self,
        archetypes: Mapping[str, Mapping[str, Any]],
        entries: Mapping[str, Mapping[str, Any]],
        contexts: Mapping[str, Mapping[str, Any]],
    ) -> tuple[dict[str, DimensionRecord], dict[str, StrategyRecord]]:
        local_cells = {
            required_string(value, "cell_id")
            for local in archetypes.values()
            for value in required_list(local, "dimensions")
            if isinstance(value, dict)
        }
        if local_cells != set(entries):
            raise ResearchStrategyAdapterError("dimension indexes diverge")
        dimensions: dict[str, DimensionRecord] = {}
        strategies: dict[str, StrategyRecord] = {}
        for cell_id, entry in entries.items():
            context = contexts.get(cell_id)
            if context is None:
                raise ResearchStrategyAdapterError("dimension context is missing")
            archetype = required_string(entry, "primary_strategy_archetype")
            dimension = required_string(entry, "construction_dimension")
            if archetype not in archetypes:
                raise ResearchStrategyAdapterError("dimension archetype is undeclared")
            card = required_string(entry, "dimension_card_path")
            strategy_locator = required_string(entry, "strategy_index_path")
            supports = required_string(entry, "dimension_support_manifest_path")
            self._validate_context_paths(context, card, strategy_locator, supports)
            if self.component(card).get("cell_id") != cell_id:
                raise ResearchStrategyAdapterError("dimension card identity mismatch")
            path = self.package.resolve_file(strategy_locator)
            strategy_index = load_json(path)
            self.validate_component(strategy_index)
            records: list[StrategyRecord] = []
            for item in required_list(strategy_index, "strategies"):
                if not isinstance(item, dict):
                    raise ResearchStrategyAdapterError(
                        "strategy index entry is invalid"
                    )
                stable_id = f"{cell_id}+{required_string(item, 'strategy_id')}"
                record = StrategyRecord(
                    stable_id,
                    cell_id,
                    archetype,
                    dimension,
                    item,
                    required_string(item, "detail_path"),
                    required_string(item, "synthesis_relation_manifest_path"),
                )
                if stable_id in strategies:
                    raise ResearchStrategyAdapterError("duplicate strategy identity")
                records.append(record)
                strategies[stable_id] = record
            dimensions[cell_id] = DimensionRecord(
                cell_id,
                archetype,
                dimension,
                context,
                card,
                supports,
                tuple(records),
                sha256_file(path),
            )
        if set(contexts) != set(dimensions):
            raise ResearchStrategyAdapterError("context and dimension indexes diverge")
        return dimensions, strategies

    @staticmethod
    def _validate_context_paths(context: Mapping[str, Any], *required: str) -> None:
        declared = {
            item["path"]
            for key in ("recommended_initial_resources", "resources")
            for item in optional_list(context, key)
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        }
        if not set(required).issubset(declared):
            raise ResearchStrategyAdapterError("context omits a hierarchy locator")


def load_json(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > MAX_EXTERNAL_KNOWLEDGE_PACKAGE_CONTROL_JSON_BYTES:
            raise ResearchStrategyAdapterError("package JSON exceeds size limit")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ResearchStrategyAdapterError("package JSON is unreadable") from exc
    if not isinstance(payload, dict):
        raise ResearchStrategyAdapterError("package JSON must contain an object")
    return payload


def required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ResearchStrategyAdapterError(f"required {key} is missing")
    return value


def required_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ResearchStrategyAdapterError(f"required {key} is invalid")
    return value


def required_list(payload: Mapping[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise ResearchStrategyAdapterError(f"required {key} is missing")
    return value


def optional_list(payload: Mapping[str, Any], key: str) -> list[Any]:
    value = payload.get(key, [])
    if not isinstance(value, list):
        raise ResearchStrategyAdapterError(f"optional {key} is invalid")
    return value


def require_digest(path: Path, expected: str, label: str) -> None:
    if sha256_file(path) != expected:
        raise ResearchStrategyAdapterError(f"{label} digest mismatch")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_under_root(path: Path, root: Path, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ResearchStrategyAdapterError(f"{label} is outside package root") from exc


def bounded(value: str, maximum: int) -> str:
    return value if len(value) <= maximum else value[: maximum - 3].rstrip() + "..."


def bounded_body(payload: Mapping[str, Any], keys: Sequence[str]) -> str:
    selected = {key: payload[key] for key in keys if key in payload}
    return json.dumps(selected, ensure_ascii=False, sort_keys=True)
