from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from ownpi.runtime.limits.resources import (
    MAX_EXTERNAL_KNOWLEDGE_PACKAGE_CONTROL_JSON_BYTES,
)
from ownpi.runtime.memory.external.contracts import (
    ExternalKnowledgeAcceleratorIdentity,
    ExternalKnowledgeAcceleratorStatus,
    ExternalKnowledgeReleasePin,
    ExternalKnowledgeSourceId,
    ExternalKnowledgeSourceIdentity,
)
from ownpi.runtime.memory.external.schema_registry import (
    BundledSchemaRegistry,
    SchemaRegistryError,
    load_bundled_schema_registry,
)


_RELEASE_CONTROL_EXCLUSIONS = frozenset(
    {"content_inventory.json", "release_snapshot.json", "INSTALL_COMPLETE"}
)


class ExternalKnowledgeReleaseStatus(str, Enum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class ExternalKnowledgeValidationMode(str, Enum):
    LIGHTWEIGHT = "lightweight"
    FULL = "full"


class SafePathError(ValueError):
    """Raised when a delivery locator crosses its pinned authority boundary."""


class _ReleaseError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message


@dataclass(frozen=True)
class ExternalKnowledgeReleaseDiagnostic:
    code: str
    message: str

    def __post_init__(self) -> None:
        if (
            not self.code
            or len(self.code) > 64
            or not self.code.replace("_", "").isalnum()
        ):
            raise ValueError("diagnostic code must be a compact identifier")
        if not self.message or len(self.message) > 240 or "\n" in self.message:
            raise ValueError("diagnostic message must be one compact line")


@dataclass(frozen=True)
class ExternalKnowledgeReleaseValidation:
    status: ExternalKnowledgeReleaseStatus
    pin: ExternalKnowledgeReleasePin | None
    diagnostics: tuple[ExternalKnowledgeReleaseDiagnostic, ...]
    mode: ExternalKnowledgeValidationMode

    def __post_init__(self) -> None:
        if not isinstance(self.status, ExternalKnowledgeReleaseStatus):
            raise TypeError("release status must use ExternalKnowledgeReleaseStatus")
        if not isinstance(self.mode, ExternalKnowledgeValidationMode):
            raise TypeError("validation mode must use ExternalKnowledgeValidationMode")
        if not isinstance(self.diagnostics, tuple) or len(self.diagnostics) > 8:
            raise ValueError("release diagnostics must be a bounded tuple")
        if (
            self.status == ExternalKnowledgeReleaseStatus.UNAVAILABLE
            and self.pin is not None
        ):
            raise ValueError("unavailable release cannot expose a pin")
        if (
            self.status != ExternalKnowledgeReleaseStatus.UNAVAILABLE
            and self.pin is None
        ):
            raise ValueError("available or degraded release requires a pin")


@dataclass(frozen=True)
class SafePathResolver:
    root: Path

    def __post_init__(self) -> None:
        supplied = Path(self.root)
        if supplied.is_symlink():
            raise SafePathError("resolver root must not be a symlink")
        try:
            resolved = supplied.resolve(strict=True)
        except OSError as exc:
            raise SafePathError("resolver root is unavailable") from exc
        if not resolved.is_dir():
            raise SafePathError("resolver root must be a directory")
        object.__setattr__(self, "root", resolved)

    def resolve(self, locator: str, *, kind: str = "any") -> Path:
        if not isinstance(locator, str) or not locator or len(locator) > 1_024:
            raise SafePathError("locator must be a bounded non-empty string")
        if "\\" in locator or locator.startswith("/"):
            raise SafePathError(
                "locator must be normalized package-root-relative POSIX"
            )
        parts = locator.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise SafePathError("locator contains an empty, dot, or traversal segment")
        candidate = self.root
        for part in parts:
            candidate = candidate / part
            if candidate.is_symlink():
                raise SafePathError("locator traverses a symlink")
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(self.root)
        except (OSError, ValueError) as exc:
            raise SafePathError(
                "locator is missing or escapes the pinned root"
            ) from exc
        if kind == "file" and not resolved.is_file():
            raise SafePathError("locator does not identify a regular file")
        if kind == "dir" and not resolved.is_dir():
            raise SafePathError("locator does not identify a directory")
        if kind not in {"any", "file", "dir"}:
            raise ValueError("unsupported resolver kind")
        return resolved

    def resolve_file(self, locator: str) -> Path:
        return self.resolve(locator, kind="file")

    def resolve_dir(self, locator: str) -> Path:
        return self.resolve(locator, kind="dir")


def create_fresh_release_pin(
    delivery_root: Path,
    *,
    mode: ExternalKnowledgeValidationMode = ExternalKnowledgeValidationMode.LIGHTWEIGHT,
) -> ExternalKnowledgeReleaseValidation:
    """Create a new pin from CURRENT; failures are compact unavailable outcomes."""
    try:
        delivery = SafePathResolver(delivery_root)
        selector = _load_json(delivery.resolve_file("CURRENT.json"))
        selector_schema = _load_json(delivery.resolve_file("CURRENT.schema.json"))
        _validate_json_schema(selector, selector_schema, label="CURRENT selector")
        release_root_relative = _required_string(selector, "release_root")
        pin, diagnostics = _validate_release(
            delivery=delivery,
            release_root_relative=release_root_relative,
            release_id=_required_string(selector, "release_id"),
            snapshot_sha256=_required_string(selector, "snapshot_sha256"),
            mode=mode,
        )
        return _success(pin, diagnostics=diagnostics, mode=mode)
    except (OSError, ValueError, SchemaRegistryError) as exc:
        return _unavailable(exc, mode=mode)


def verify_resume_release_pin(
    delivery_root: Path,
    pin: ExternalKnowledgeReleasePin,
    *,
    mode: ExternalKnowledgeValidationMode = ExternalKnowledgeValidationMode.LIGHTWEIGHT,
) -> ExternalKnowledgeReleaseValidation:
    """Verify an immutable checkpoint pin without consulting CURRENT.json."""
    try:
        if not isinstance(pin, ExternalKnowledgeReleasePin):
            raise _ReleaseError(
                "invalid_pin", "Checkpoint External release pin is invalid."
            )
        observed, diagnostics = _validate_release(
            delivery=SafePathResolver(delivery_root),
            release_root_relative=pin.release_root_relative,
            release_id=pin.release_id,
            snapshot_sha256=pin.snapshot_sha256,
            mode=mode,
        )
        if _pin_core(observed) != _pin_core(pin):
            raise _ReleaseError(
                "pin_mismatch", "Pinned release identity no longer verifies."
            )
        if observed.accelerators != pin.accelerators:
            diagnostics = diagnostics + (
                ExternalKnowledgeReleaseDiagnostic(
                    "accelerator_degraded",
                    "Pinned accelerator is unavailable; lexical fallback remains usable.",
                ),
            )
        return _success(pin, diagnostics=diagnostics, mode=mode)
    except (OSError, ValueError, SchemaRegistryError) as exc:
        return _unavailable(exc, mode=mode)


def validate_full_delivery(
    delivery_root: Path,
    *,
    pin: ExternalKnowledgeReleasePin | None = None,
) -> ExternalKnowledgeReleaseValidation:
    if pin is None:
        return create_fresh_release_pin(
            delivery_root, mode=ExternalKnowledgeValidationMode.FULL
        )
    return verify_resume_release_pin(
        delivery_root,
        pin,
        mode=ExternalKnowledgeValidationMode.FULL,
    )


create_external_knowledge_release_pin = create_fresh_release_pin
verify_external_knowledge_release_pin = verify_resume_release_pin


def _validate_release(
    *,
    delivery: SafePathResolver,
    release_root_relative: str,
    release_id: str,
    snapshot_sha256: str,
    mode: ExternalKnowledgeValidationMode,
) -> tuple[ExternalKnowledgeReleasePin, tuple[ExternalKnowledgeReleaseDiagnostic, ...]]:
    release_root = delivery.resolve_dir(release_root_relative)
    release = SafePathResolver(release_root)
    snapshot_path = release.resolve_file("release_snapshot.json")
    _require_digest(snapshot_path, snapshot_sha256, code="snapshot_digest_mismatch")
    snapshot = _load_json(snapshot_path)
    catalog_path = release.resolve_file("package_catalog.json")
    catalog_sha256 = _sha256_file(catalog_path)
    if catalog_sha256 != snapshot.get("package_catalog_sha256"):
        raise _ReleaseError(
            "catalog_digest_mismatch", "Package catalog digest does not match snapshot."
        )
    catalog = _load_json(catalog_path)
    registry_path = _required_string(catalog, "schema_registry_path")
    registry = load_bundled_schema_registry(
        resolve_release_path=release.resolve_file,
        registry_path=registry_path,
    )
    registry.validate_component(
        snapshot,
        component_type="nkf_installed_release_snapshot",
        component_version="1.0.0",
    )
    registry.validate_component(
        catalog,
        component_type="nkf_release_package_catalog",
        component_version="3.0.0",
    )
    _require_equal(snapshot.get("release_id"), release_id, code="release_id_mismatch")
    marker = _load_and_validate(
        release,
        registry,
        "INSTALL_COMPLETE",
        component_type="nkf_install_complete",
        component_version="1.0.0",
    )
    inventory = _load_and_validate(
        release,
        registry,
        "content_inventory.json",
        component_type="nkf_release_content_inventory",
        component_version="1.0.0",
    )
    release_digest = _required_string(snapshot, "release_content_digest")
    _require_equal(marker.get("release_id"), release_id)
    _require_equal(inventory.get("release_content_digest"), release_digest)
    _require_equal(marker.get("snapshot_sha256"), snapshot_sha256)
    _require_equal(marker.get("release_content_digest"), release_digest)
    package_items = _catalog_items(catalog, "packages")
    source_identities, source_manifests = _validate_sources(
        release=release,
        registry=registry,
        package_items=package_items,
    )
    accelerator_identities, diagnostics = _validate_accelerators(
        release=release,
        registry=registry,
        catalog=catalog,
        snapshot=snapshot,
        source_manifests=source_manifests,
    )
    if mode == ExternalKnowledgeValidationMode.FULL:
        _validate_full_inventory(release, inventory)
    return (
        ExternalKnowledgeReleasePin(
            release_id=release_id,
            release_root_relative=release_root_relative,
            snapshot_sha256=snapshot_sha256,
            release_content_digest=release_digest,
            catalog_sha256=catalog_sha256,
            sources=source_identities,
            accelerators=accelerator_identities,
        ),
        diagnostics,
    )


def _validate_sources(
    *,
    release: SafePathResolver,
    registry: BundledSchemaRegistry,
    package_items: list[dict[str, Any]],
) -> tuple[
    tuple[ExternalKnowledgeSourceIdentity, ...],
    dict[ExternalKnowledgeSourceId, dict[str, Any]],
]:
    by_source = {item.get("source_id"): item for item in package_items}
    expected_sources = {source_id.value for source_id in ExternalKnowledgeSourceId}
    if (
        len(package_items) != len(expected_sources)
        or set(by_source) != expected_sources
    ):
        raise _ReleaseError(
            "required_source_set_invalid",
            "External delivery must contain exactly the two required sources.",
        )
    identities: list[ExternalKnowledgeSourceIdentity] = []
    manifests: dict[ExternalKnowledgeSourceId, dict[str, Any]] = {}
    component_types = {
        ExternalKnowledgeSourceId.METHOD_KNOWLEDGE: "knowledge_package_manifest",
        ExternalKnowledgeSourceId.RESEARCH_STRATEGY: "research_strategy_package_manifest",
    }
    for source_id in ExternalKnowledgeSourceId:
        item = by_source.get(source_id.value)
        if item is None:
            raise _ReleaseError(
                "required_source_missing", "A required External source is missing."
            )
        package_root_locator = _required_string(item, "package_root")
        package = SafePathResolver(release.resolve_dir(package_root_locator))
        manifest_path = release.resolve_file(_required_string(item, "manifest_path"))
        inventory_path = release.resolve_file(
            _required_string(item, "content_inventory_path")
        )
        _require_digest(manifest_path, _required_string(item, "manifest_sha256"))
        _require_digest(
            inventory_path, _required_string(item, "content_inventory_sha256")
        )
        manifest = _load_json(manifest_path)
        version = _required_string(item, "schema_version")
        registry.validate_component(
            manifest,
            component_type=component_types[source_id],
            component_version=version,
        )
        package_inventory = _load_json(inventory_path)
        registry.validate_component(
            package_inventory,
            component_type="nkf_package_content_inventory",
            component_version="1.0.0",
        )
        for key in ("package_id",):
            _require_equal(manifest.get(key), item.get(key))
        _require_equal(manifest.get("version"), item.get("package_version"))
        _require_equal(manifest.get("schema_version"), version)
        _require_equal(
            manifest.get("content_inventory_sha256"),
            item.get("content_inventory_sha256"),
        )
        _require_equal(
            manifest.get("package_content_digest"),
            package_inventory.get("package_content_digest"),
        )
        context_path = release.resolve_file(
            _required_string(item, "agent_context_index_path")
        )
        context = _load_json(context_path)
        registry.validate_component(
            context,
            component_type="agent_context_index",
            component_version=version,
        )
        _validate_declared_locators(manifest, package)
        _validate_declared_locators(context, package)
        identities.append(
            ExternalKnowledgeSourceIdentity(
                source_id=source_id,
                package_id=_required_string(item, "package_id"),
                package_version=_required_string(item, "package_version"),
                schema_version=version,
                manifest_sha256=_required_string(item, "manifest_sha256"),
                content_inventory_sha256=_required_string(
                    item, "content_inventory_sha256"
                ),
                package_content_digest=_required_string(
                    manifest, "package_content_digest"
                ),
            )
        )
        manifests[source_id] = manifest
    return tuple(identities), manifests


def _validate_accelerators(
    *,
    release: SafePathResolver,
    registry: BundledSchemaRegistry,
    catalog: dict[str, Any],
    snapshot: dict[str, Any],
    source_manifests: Mapping[ExternalKnowledgeSourceId, dict[str, Any]],
) -> tuple[
    tuple[ExternalKnowledgeAcceleratorIdentity, ...],
    tuple[ExternalKnowledgeReleaseDiagnostic, ...],
]:
    items = _catalog_items(catalog, "accelerators")
    activations = {
        item.get("accelerator_id"): item
        for item in _catalog_items(snapshot, "accelerator_activations")
    }
    identities: list[ExternalKnowledgeAcceleratorIdentity] = []
    diagnostics: list[ExternalKnowledgeReleaseDiagnostic] = []
    for item in items:
        accelerator_id = _required_string(item, "accelerator_id")
        source_id = ExternalKnowledgeSourceId(
            _required_string(item, "accelerates_source_id")
        )
        activation = activations.get(accelerator_id, {})
        try:
            if (
                activation.get("status") != "active"
                or item.get("candidate_status") != "available"
            ):
                raise _ReleaseError(
                    "accelerator_inactive", "Optional accelerator is not active."
                )
            manifest_locator = _required_string(item, "manifest_path")
            manifest_path = release.resolve_file(manifest_locator)
            manifest_sha = _required_string(item, "manifest_sha256")
            _require_digest(manifest_path, manifest_sha)
            manifest = _load_json(manifest_path)
            registry.validate_component(
                manifest,
                component_type="knowledge_vector_index",
                component_version="2.0.0",
            )
            accelerator = SafePathResolver(
                release.resolve_dir(_required_string(item, "root"))
            )
            inventory_path = accelerator.resolve_file(
                _required_string(manifest, "content_inventory_path")
            )
            _require_digest(
                inventory_path, _required_string(manifest, "content_inventory_sha256")
            )
            inventory = _load_json(inventory_path)
            registry.validate_component(
                inventory,
                component_type="nkf_package_content_inventory",
                component_version="1.0.0",
            )
            source_manifest = source_manifests[source_id]
            _require_equal(
                manifest.get("source_package_id"), source_manifest.get("package_id")
            )
            _require_equal(
                manifest.get("source_package_version"), source_manifest.get("version")
            )
            _require_equal(
                manifest.get("package_content_digest"),
                inventory.get("package_content_digest"),
            )
            _validate_declared_locators(manifest, accelerator)
            identities.append(
                ExternalKnowledgeAcceleratorIdentity(
                    accelerator_id=accelerator_id,
                    accelerates_source_id=source_id,
                    accelerator_version=_required_string(manifest, "version"),
                    manifest_sha256=manifest_sha,
                    package_content_digest=_required_string(
                        manifest, "package_content_digest"
                    ),
                    status=ExternalKnowledgeAcceleratorStatus.ACTIVE,
                )
            )
        except (OSError, ValueError, SchemaRegistryError):
            identities.append(
                ExternalKnowledgeAcceleratorIdentity(
                    accelerator_id=accelerator_id,
                    accelerates_source_id=source_id,
                    accelerator_version="",
                    manifest_sha256=str(item.get("manifest_sha256") or ""),
                    package_content_digest="",
                    status=ExternalKnowledgeAcceleratorStatus.UNAVAILABLE,
                    fallback="lexical",
                )
            )
            diagnostics.append(
                ExternalKnowledgeReleaseDiagnostic(
                    "accelerator_unavailable",
                    "Optional Method accelerator is unavailable; lexical fallback remains usable.",
                )
            )
    return tuple(identities), tuple(diagnostics)


def _validate_declared_locators(payload: object, resolver: SafePathResolver) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            is_locator = key in {"path", "resource_path"} or key.endswith(
                ("_path", "_dir")
            )
            is_locator_list = key.endswith(("_paths", "_dirs"))
            if is_locator and isinstance(value, str):
                resolver.resolve(value)
            elif is_locator_list and isinstance(value, list):
                for locator in value:
                    if not isinstance(locator, str):
                        raise _ReleaseError(
                            "invalid_locator", "Declared locator is invalid."
                        )
                    resolver.resolve(locator)
            else:
                _validate_declared_locators(value, resolver)
    elif isinstance(payload, list):
        for item in payload:
            _validate_declared_locators(item, resolver)


def _validate_full_inventory(
    release: SafePathResolver, inventory: dict[str, Any]
) -> None:
    entries = inventory.get("entries")
    if not isinstance(entries, list):
        raise _ReleaseError(
            "inventory_invalid", "Release inventory entries are invalid."
        )
    declared: set[str] = set()
    total_bytes = 0
    for entry in entries:
        if not isinstance(entry, dict):
            raise _ReleaseError(
                "inventory_invalid", "Release inventory entry is invalid."
            )
        locator = _required_string(entry, "path")
        if locator in declared:
            raise _ReleaseError(
                "inventory_duplicate", "Release inventory contains duplicate paths."
            )
        declared.add(locator)
        path = release.resolve_file(locator)
        size = path.stat().st_size
        _require_equal(size, entry.get("size_bytes"), code="inventory_size_mismatch")
        _require_digest(
            path, _required_string(entry, "sha256"), code="inventory_digest_mismatch"
        )
        total_bytes += size
    _require_equal(
        len(declared), inventory.get("file_count"), code="inventory_count_mismatch"
    )
    _require_equal(
        total_bytes, inventory.get("total_size_bytes"), code="inventory_size_mismatch"
    )
    actual: set[str] = set()
    for path in release.root.rglob("*"):
        if path.is_symlink():
            raise _ReleaseError("inventory_symlink", "Release tree contains a symlink.")
        if path.is_file():
            actual.add(path.relative_to(release.root).as_posix())
    if actual != declared | _RELEASE_CONTROL_EXCLUSIONS:
        raise _ReleaseError(
            "inventory_membership_mismatch", "Release inventory membership is invalid."
        )


def _load_and_validate(
    resolver: SafePathResolver,
    registry: BundledSchemaRegistry,
    locator: str,
    *,
    component_type: str,
    component_version: str,
) -> dict[str, Any]:
    payload = _load_json(resolver.resolve_file(locator))
    registry.validate_component(
        payload,
        component_type=component_type,
        component_version=component_version,
    )
    return payload


def _load_json(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > MAX_EXTERNAL_KNOWLEDGE_PACKAGE_CONTROL_JSON_BYTES:
            raise _ReleaseError(
                "control_file_too_large", "Delivery control file exceeds size limit."
            )
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _ReleaseError(
            "control_file_unreadable", "Delivery control file is unreadable."
        ) from exc
    if not isinstance(payload, dict):
        raise _ReleaseError(
            "control_file_invalid", "Delivery control file must contain an object."
        )
    return payload


def _validate_json_schema(
    payload: object, schema: dict[str, Any], *, label: str
) -> None:
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(payload)
    except (SchemaError, ValidationError) as exc:
        raise _ReleaseError(
            "selector_schema_invalid", f"{label} failed JSON Schema validation."
        ) from exc


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise _ReleaseError(
            "control_identity_missing", "Required delivery identity is missing."
        )
    return value


def _require_digest(
    path: Path, expected: str, *, code: str = "control_digest_mismatch"
) -> None:
    if _sha256_file(path) != expected:
        raise _ReleaseError(code, "Delivery control digest does not match.")


def _require_equal(
    actual: object, expected: object, *, code: str = "control_identity_mismatch"
) -> None:
    if actual != expected:
        raise _ReleaseError(code, "Cross-file delivery identity does not match.")


def _catalog_items(payload: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    values = payload.get(key)
    if not isinstance(values, list) or any(
        not isinstance(item, dict) for item in values
    ):
        raise _ReleaseError(
            "control_collection_invalid", "Delivery control collection is invalid."
        )
    return values


def _pin_core(pin: ExternalKnowledgeReleasePin) -> tuple[object, ...]:
    return (
        pin.release_id,
        pin.release_root_relative,
        pin.snapshot_sha256,
        pin.release_content_digest,
        pin.catalog_sha256,
        pin.sources,
    )


def _success(
    pin: ExternalKnowledgeReleasePin,
    *,
    diagnostics: tuple[ExternalKnowledgeReleaseDiagnostic, ...],
    mode: ExternalKnowledgeValidationMode,
) -> ExternalKnowledgeReleaseValidation:
    status = (
        ExternalKnowledgeReleaseStatus.DEGRADED
        if diagnostics
        else ExternalKnowledgeReleaseStatus.AVAILABLE
    )
    return ExternalKnowledgeReleaseValidation(status, pin, diagnostics, mode)


def _unavailable(
    error: BaseException,
    *,
    mode: ExternalKnowledgeValidationMode,
) -> ExternalKnowledgeReleaseValidation:
    code = error.code if isinstance(error, _ReleaseError) else "delivery_invalid"
    message = (
        error.safe_message
        if isinstance(error, _ReleaseError)
        else "External knowledge delivery is unavailable or invalid."
    )
    diagnostic = ExternalKnowledgeReleaseDiagnostic(code, message)
    return ExternalKnowledgeReleaseValidation(
        ExternalKnowledgeReleaseStatus.UNAVAILABLE,
        None,
        (diagnostic,),
        mode,
    )
