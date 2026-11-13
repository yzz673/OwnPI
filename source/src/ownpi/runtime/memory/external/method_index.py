from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ownpi.runtime.limits.resources import (
    MAX_EXTERNAL_KNOWLEDGE_METHOD_JSON_BYTES,
)
from ownpi.runtime.memory.external.contracts import (
    ExternalKnowledgeContentKind,
    ExternalKnowledgeReleasePin,
    ExternalKnowledgeSourceId,
    derive_relation_stable_id,
)
from ownpi.runtime.memory.external.release import SafePathResolver
from ownpi.runtime.memory.external.schema_registry import (
    load_bundled_schema_registry,
)

METHOD_SOURCE = ExternalKnowledgeSourceId.METHOD_KNOWLEDGE
METHOD_VERSION = "2.0.0"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class PatternRecord:
    context: Mapping[str, Any]
    item: Mapping[str, Any]


class MethodPackageIndex:
    def __init__(
        self, *, delivery_root: Path, pin: ExternalKnowledgeReleasePin
    ) -> None:
        self.pin = pin
        delivery = SafePathResolver(delivery_root)
        self.release = SafePathResolver(delivery.resolve_dir(pin.release_root_relative))
        self.catalog = load_json(self.release.resolve_file("package_catalog.json"))
        self.snapshot = load_json(self.release.resolve_file("release_snapshot.json"))
        if (
            sha256_file(self.release.resolve_file("package_catalog.json"))
            != pin.catalog_sha256
        ):
            raise ValueError("pinned catalog identity mismatch")
        source_item = self._source_catalog_item()
        self.source = next(
            source for source in pin.sources if source.source_id == METHOD_SOURCE
        )
        self.package = SafePathResolver(
            self.release.resolve_dir(required(source_item, "package_root"))
        )
        self.registry = load_bundled_schema_registry(
            resolve_release_path=self.release.resolve_file,
            registry_path=required(self.catalog, "schema_registry_path"),
        )
        manifest_path = self.release.resolve_file(
            required(source_item, "manifest_path")
        )
        inventory_path = self.release.resolve_file(
            required(source_item, "content_inventory_path")
        )
        for path in (manifest_path, inventory_path):
            try:
                path.relative_to(self.package.root)
            except ValueError as exc:
                raise ValueError("Method control locator crosses package root") from exc
        self._require_digest(manifest_path, self.source.manifest_sha256)
        self._require_digest(inventory_path, self.source.content_inventory_sha256)
        self.manifest = load_json(manifest_path)
        self._validate(self.manifest, "knowledge_package_manifest")
        inventory = load_json(inventory_path)
        self._validate(
            inventory,
            "nkf_package_content_inventory",
            version="1.0.0",
        )
        if (
            self.package.resolve_file(required(self.manifest, "content_inventory_path"))
            != inventory_path
            or inventory.get("package_content_digest")
            != self.source.package_content_digest
        ):
            raise ValueError("Method inventory declaration mismatch")
        self._validate_source_identity()
        context_path = self.package.resolve_file(
            required(self.manifest, "agent_context_index_path")
        )
        if context_path != self.release.resolve_file(
            required(source_item, "agent_context_index_path")
        ):
            raise ValueError("Method context locator mismatch")
        context_index = load_json(context_path)
        self._validate(context_index, "agent_context_index")
        source_package = context_index.get("source_package")
        if not isinstance(source_package, dict) or (
            source_package.get("package_id"),
            source_package.get("version"),
            source_package.get("manifest_path"),
            source_package.get("subtarget_index_path"),
        ) != (
            self.source.package_id,
            self.source.package_version,
            "package_manifest.json",
            required(self.manifest, "subtarget_index_path"),
        ):
            raise ValueError("Method context source identity mismatch")
        self._validate(
            load_json(
                self.package.resolve_file(
                    required(self.manifest, "subtarget_index_path")
                )
            ),
            "knowledge_subtarget_index",
        )
        contexts = context_index.get("contexts")
        if not isinstance(contexts, list) or not contexts:
            raise ValueError("Method contexts must be a non-empty list")
        if context_index.get("context_count") != len(contexts):
            raise ValueError("Method context count mismatch")
        for context in contexts:
            self._validate_context(context)
        self.contexts: tuple[Mapping[str, Any], ...] = tuple(contexts)
        self.contexts_by_subtarget = {
            required(context, "source_ref"): context for context in contexts
        }
        self.contexts_by_knowledge = {
            required(context, "knowledge_id"): context for context in contexts
        }
        self._patterns: dict[str, PatternRecord] = {}
        self._relation_patterns: dict[str, PatternRecord] = {}
        self._case_locators: dict[str, tuple[str, Mapping[str, Any]]] = {}

    def pattern_records(self, context: Mapping[str, Any]) -> tuple[PatternRecord, ...]:
        payload = self.pattern_index(context)
        patterns = payload.get("patterns")
        if not isinstance(patterns, list):
            raise ValueError("Method pattern index must contain a patterns list")
        records: list[PatternRecord] = []
        for item in patterns:
            if not isinstance(item, dict):
                raise ValueError("Method pattern item is invalid")
            pattern_id = required(item, "pattern_id")
            self.package.resolve_file(required(item, "detail_path"))
            record = PatternRecord(context, item)
            self._patterns[pattern_id] = record
            records.append(record)
        return tuple(records)

    def find_pattern(self, pattern_id: str) -> PatternRecord | None:
        if pattern_id in self._patterns:
            return self._patterns[pattern_id]
        for context in self.contexts:
            self.pattern_records(context)
            if pattern_id in self._patterns:
                return self._patterns[pattern_id]
        return None

    def find_pattern_for_relation(self, relation_stable_id: str) -> PatternRecord | None:
        """Resolve a relation ref from pinned package contents without prior reads."""
        cached = self._relation_patterns.get(relation_stable_id)
        if cached is not None:
            return cached
        for context in self.contexts:
            for record in self.pattern_records(context):
                relation = derive_relation_stable_id(
                    source_id=METHOD_SOURCE,
                    content_kind=ExternalKnowledgeContentKind.PATTERN_CASE_RELEVANCE,
                    endpoints=(
                        ("pattern", str(record.item["pattern_id"])),
                        ("subtarget", str(context["source_ref"])),
                    ),
                )
                if relation == relation_stable_id:
                    self._relation_patterns[relation_stable_id] = record
                    return record
        return None

    def find_case(self, case_id: str) -> tuple[str, Mapping[str, Any]] | None:
        """Resolve a canonical case ref from pinned edge resources independently."""
        cached = self._case_locators.get(case_id)
        if cached is not None:
            return cached
        for context in self.contexts:
            for record in self.pattern_records(context):
                detail = self.pattern_detail(record)
                edge_path = self.package.resolve_file(
                    required(detail, "pattern_case_edges_path")
                )
                with edge_path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        edge = self.validate_edge(json.loads(line), str(record.item["pattern_id"]))
                        if edge.get("case_id") != case_id:
                            continue
                        locator = required(edge, "canonical_case_path")
                        self.package.resolve_file(locator)
                        resolved = (locator, context)
                        self._case_locators[case_id] = resolved
                        return resolved
        return None

    def pattern_index(self, context: Mapping[str, Any]) -> dict[str, Any]:
        return self.read_context_resource(
            context,
            collection="resources",
            content_kind="method_pattern_index",
            component_type="knowledge_pattern_index",
        )

    def pattern_detail(self, record: PatternRecord) -> dict[str, Any]:
        payload = load_json(
            self.package.resolve_file(required(record.item, "detail_path"))
        )
        self._validate(payload, "method_pattern_detail")
        if payload.get("pattern_id") != record.item.get("pattern_id"):
            raise ValueError("Method pattern detail identity mismatch")
        self.package.resolve_file(required(payload, "pattern_case_edges_path"))
        return payload

    def read_context_resource(
        self,
        context: Mapping[str, Any],
        *,
        collection: str,
        content_kind: str,
        component_type: str,
    ) -> dict[str, Any]:
        values = context.get(collection)
        if not isinstance(values, list):
            raise ValueError("Method resource declaration is invalid")
        item = next(
            (
                value
                for value in values
                if isinstance(value, dict) and value.get("content_kind") == content_kind
            ),
            None,
        )
        if item is None:
            raise ValueError("Method resource is not declared")
        payload = load_json(self.package.resolve_file(required(item, "path")))
        self._validate(payload, component_type)
        return payload

    def read_case(self, locator: str, case_id: str) -> dict[str, Any]:
        payload = load_json(self.package.resolve_file(locator))
        self._validate(payload, "method_canonical_case")
        if payload.get("case_id") != case_id:
            raise ValueError("canonical case identity mismatch")
        return payload

    def validate_edge(self, payload: object, pattern_id: str) -> dict[str, Any]:
        self._validate(payload, "pattern_case_relevance")
        if not isinstance(payload, dict) or payload.get("pattern_id") != pattern_id:
            raise ValueError("pattern relation endpoint mismatch")
        return payload

    def validate_vector(self, accelerator_id: str) -> tuple[Path, dict[str, Any]]:
        item = _find_item(
            self.catalog.get("accelerators"), "accelerator_id", accelerator_id
        )
        activation = _find_item(
            self.snapshot.get("accelerator_activations"),
            "accelerator_id",
            accelerator_id,
        )
        if (
            item.get("candidate_status") != "available"
            or activation.get("status") != "active"
        ):
            raise ValueError("vector activation is not active")
        identity = next(
            value
            for value in self.pin.accelerators
            if value.accelerator_id == accelerator_id
        )
        manifest_path = self.release.resolve_file(required(item, "manifest_path"))
        self._require_digest(manifest_path, identity.manifest_sha256)
        root = SafePathResolver(self.release.resolve_dir(required(item, "root")))
        manifest = load_json(manifest_path)
        self._validate(manifest, "knowledge_vector_index")
        expected = {
            "version": identity.accelerator_version,
            "package_content_digest": identity.package_content_digest,
            "source_package_id": self.source.package_id,
            "source_package_version": self.source.package_version,
            "source_package_manifest_digest": self.source.manifest_sha256,
        }
        if any(manifest.get(key) != value for key, value in expected.items()):
            raise ValueError("vector source identity mismatch")
        for key in (
            "embedding_document_set_digest",
            "row_mapping_digest",
            "vector_file_digest",
            "retrieval_semantics_identity",
        ):
            if _SHA256.fullmatch(str(manifest.get(key, ""))) is None:
                raise ValueError("vector retrieval identity is invalid")
        count = manifest.get("document_count")
        if count != manifest.get("expected_document_count") or count != manifest.get(
            "vector_count"
        ):
            raise ValueError("vector row counts do not match")
        inventory_path = root.resolve_file(required(manifest, "content_inventory_path"))
        self._require_digest(
            inventory_path, str(manifest.get("content_inventory_sha256", ""))
        )
        inventory = load_json(inventory_path)
        self._validate(
            inventory,
            "nkf_package_content_inventory",
            version="1.0.0",
        )
        entries = inventory.get("entries")
        if not isinstance(entries, list):
            raise ValueError("vector inventory is invalid")
        by_path = {
            entry.get("path"): entry for entry in entries if isinstance(entry, dict)
        }
        for field in ("documents_path", "id_to_source_path", "embeddings_path"):
            locator = required(manifest, field)
            path = root.resolve_file(locator)
            entry = by_path.get(locator)
            if (
                not isinstance(entry, dict)
                or entry.get("size_bytes") != path.stat().st_size
            ):
                raise ValueError("vector artifact inventory mismatch")
        vector_entry = by_path[required(manifest, "embeddings_path")]
        if vector_entry.get("sha256") != manifest.get("vector_file_digest"):
            raise ValueError("vector file digest declaration mismatch")
        return root.root, manifest

    def _source_catalog_item(self) -> dict[str, Any]:
        return _find_item(
            self.catalog.get("packages"), "source_id", METHOD_SOURCE.value
        )

    def _validate_source_identity(self) -> None:
        expected = {
            "package_id": self.source.package_id,
            "version": self.source.package_version,
            "schema_version": self.source.schema_version,
            "package_content_digest": self.source.package_content_digest,
        }
        if any(self.manifest.get(key) != value for key, value in expected.items()):
            raise ValueError("Method package identity mismatch")

    def _validate_context(self, context: object) -> None:
        if not isinstance(context, dict):
            raise ValueError("Method context must be an object")
        for key in ("source_ref", "knowledge_id", "title", "summary"):
            required(context, key)
        if context.get("content_kind") != "method_subtarget_context":
            raise ValueError("Method context kind is invalid")
        expected = {
            "recommended_initial_resources": {
                "method_subtarget_card",
                "method_knowledge",
            },
            "resources": {
                "method_pattern_index",
                "method_pattern_case_edges",
                "method_canonical_cases",
            },
        }
        for collection in ("recommended_initial_resources", "resources", "relations"):
            values = context.get(collection)
            if not isinstance(values, list):
                raise ValueError("Method context resources are invalid")
            for item in values:
                if not isinstance(item, dict):
                    raise ValueError("Method context resource is invalid")
                locator = item.get("path", item.get("resource_path"))
                if not isinstance(locator, str):
                    raise ValueError("Method context locator is missing")
                self.package.resolve(locator)
            if (
                collection in expected
                and {str(item.get("content_kind", "")) for item in values}
                != expected[collection]
            ):
                raise ValueError("Method context required resources are incomplete")
        relations = context["relations"]
        if {str(item.get("relation_type", "")) for item in relations} != {
            "pattern_case_relevance"
        }:
            raise ValueError("Method context relation declaration is invalid")

    def _validate(
        self, payload: object, component_type: str, *, version: str = METHOD_VERSION
    ) -> None:
        self.registry.validate_component(
            payload,
            component_type=component_type,
            component_version=version,
        )

    @staticmethod
    def _require_digest(path: Path, expected: str) -> None:
        if sha256_file(path) != expected:
            raise ValueError("Method content identity mismatch")


def load_json(path: Path) -> dict[str, Any]:
    if path.stat().st_size > MAX_EXTERNAL_KNOWLEDGE_METHOD_JSON_BYTES:
        raise ValueError("Method control object exceeds size limit")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Method object must be a JSON object")
    return payload


def required(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Method object requires {key}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _find_item(payload: object, key: str, value: str) -> dict[str, Any]:
    if not isinstance(payload, list):
        raise ValueError("Method catalog collection is invalid")
    item = next(
        (
            candidate
            for candidate in payload
            if isinstance(candidate, dict) and candidate.get(key) == value
        ),
        None,
    )
    if item is None:
        raise ValueError("Method catalog item is unavailable")
    return item
