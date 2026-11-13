"""Small product-level discovery boundary for optional capability packs."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ownpi.app.config_tools import (
    configure_external_knowledge,
    disable_external_knowledge_if_managed,
)
from ownpi.app.paths import AppPaths
from ownpi.product.pack_contracts import (
    NEUROSCIENCE_PACK_ID,
    PackManifest,
    validate_pack_root,
)
from ownpi.product.installation_transaction import restore_file, restore_marker
from ownpi.skills.catalog import SkillCatalogResult, build_skill_catalog_result
from ownpi.schemas.context import SkillSummary


PACK_MANIFEST_FILENAME = "pack.json"
PACK_DISABLED_MARKER = ".disabled"
NEUROSCIENCE_SKILLS = frozenset(
    {
        "brain-data-intake-audit",
        "brain-signal-preprocessing-prior",
        "brainlib",
        "neuro-ai-design",
    }
)
NEUROSCIENCE_SKILL_URI_PREFIX = "bundled://"


@dataclass(frozen=True)
class PackInspection:
    pack_id: str
    status: str
    root: Path | None
    manifest: PackManifest | None = None
    reason: str = ""

    @property
    def active(self) -> bool:
        return self.status == "ready" and self.manifest is not None and self.manifest.enabled

    def to_dict(self) -> dict[str, Any]:
        return {
            "pack_id": self.pack_id,
            "status": self.status,
            "root": str(self.root) if self.root is not None else None,
            "manifest": self.manifest.to_dict() if self.manifest is not None else None,
            "reason": self.reason,
        }


def inspect_neuroscience_pack(app_paths: AppPaths) -> PackInspection:
    for root in _candidate_roots(app_paths):
        if not root.exists():
            continue
        return _inspect_pack_root(root)
    return PackInspection(NEUROSCIENCE_PACK_ID, "not_installed", None, reason="pack not found")


def active_neuroscience_pack(app_paths: AppPaths) -> PackInspection | None:
    inspection = inspect_neuroscience_pack(app_paths)
    return inspection if inspection.active else None


def neuroscience_pack_candidate_roots(app_paths: AppPaths) -> tuple[Path, ...]:
    return _candidate_roots(app_paths)


def set_neuroscience_pack_enabled(app_paths: AppPaths, enabled: bool) -> PackInspection:
    base = app_paths.app_home / "packs" / NEUROSCIENCE_PACK_ID
    roots = (base / "current", base)
    installed_root = next((root for root in roots if root.exists()), None)
    if installed_root is None:
        return PackInspection(NEUROSCIENCE_PACK_ID, "not_installed", None, reason="pack not installed in app home")
    inspection = _inspect_pack_root(installed_root)
    if inspection.root is None or inspection.manifest is None:
        return inspection
    marker = inspection.root.parent / PACK_DISABLED_MARKER
    marker_before = marker.exists()
    config_before = (
        app_paths.config_path.read_bytes() if app_paths.config_path.is_file() else None
    )
    try:
        if enabled:
            marker.unlink(missing_ok=True)
            if inspection.manifest.knowledge_deliveries:
                delivery = inspection.manifest.knowledge_deliveries[0]
                configure_external_knowledge(
                    app_paths.config_path,
                    inspection.root.resolve(strict=True) / delivery.path,
                )
        else:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
            disable_external_knowledge_if_managed(
                app_paths.config_path,
                app_paths.app_home / "packs" / NEUROSCIENCE_PACK_ID,
            )
    except BaseException:
        restore_marker(marker, marker_before)
        restore_file(app_paths.config_path, config_before)
        raise
    return inspect_neuroscience_pack(app_paths)


def neuroscience_skill_summaries(app_paths: AppPaths) -> list[SkillSummary]:
    inspection = active_neuroscience_pack(app_paths)
    if inspection is None or inspection.root is None or inspection.manifest is None:
        return []
    declared_skills = {Path(path).name.casefold() for path in inspection.manifest.skills}
    result: SkillCatalogResult = build_skill_catalog_result(
        [(f"pack:{NEUROSCIENCE_PACK_ID}", inspection.root / "skills")]
    )
    return [
        SkillSummary(
            name=item.name,
            description=item.description,
            path=f"{NEUROSCIENCE_SKILL_URI_PREFIX}{_skill_slug(item)}",
            source=f"pack:{NEUROSCIENCE_PACK_ID}",
            tags=item.tags,
            priority=item.priority,
            when_to_use=item.when_to_use,
            aliases=item.aliases,
        )
        for item in result.skills
        if _skill_slug(item) in declared_skills
    ]


def resolve_neuroscience_skill_root(app_paths: AppPaths, logical_path: str) -> Path | None:
    inspection = active_neuroscience_pack(app_paths)
    slug = str(logical_path or "").removeprefix(NEUROSCIENCE_SKILL_URI_PREFIX).strip().casefold()
    if inspection is None or inspection.root is None or inspection.manifest is None:
        return None
    declared_skills = {Path(path).name.casefold() for path in inspection.manifest.skills}
    if slug not in declared_skills:
        return None
    root = (inspection.root / "skills" / slug).resolve()
    try:
        root.relative_to((inspection.root / "skills").resolve())
    except ValueError:
        return None
    return root if root.is_dir() else None


def brainlib_module_resource(package: str) -> str | None:
    return BRAINLIB_MODULE_RESOURCES.get(str(package or "").strip().casefold())


BRAINLIB_MODULE_RESOURCES = {
    "brainlib-overview": "references/modules/brainlib-overview/SKILL.md",
    "brainlib-cli-workflows": "references/modules/brainlib-cli-workflows/SKILL.md",
    "brainlib-config-cookbook": "references/modules/brainlib-config-cookbook/SKILL.md",
    "brainlib-data-and-artifacts": "references/modules/brainlib-data-and-artifacts/SKILL.md",
    "brainlib-live-testing-and-debugging": "references/modules/brainlib-live-testing-and-debugging/SKILL.md",
    "brainlib-model-training-checkpoint": "references/modules/brainlib-model-training-checkpoint/SKILL.md",
    "brainlib-preprocess-paradigm-split": "references/modules/brainlib-preprocess-paradigm-split/SKILL.md",
    "brainlib-python-api": "references/modules/brainlib-python-api/SKILL.md",
}


def _candidate_roots(app_paths: AppPaths) -> tuple[Path, ...]:
    override = os.environ.get("OWNPI_NEUROSCIENCE_PACK", "").strip()
    roots: list[Path] = [Path(override).expanduser().resolve()] if override else []
    roots.append(app_paths.app_home / "packs" / NEUROSCIENCE_PACK_ID / "current")
    return tuple(dict.fromkeys(roots))


def _inspect_pack_root(root: Path) -> PackInspection:
    manifest_path = root / PACK_MANIFEST_FILENAME
    if not root.exists():
        return PackInspection(NEUROSCIENCE_PACK_ID, "not_installed", None, reason="pack not found")
    if not manifest_path.is_file():
        return PackInspection(NEUROSCIENCE_PACK_ID, "error", root, reason="missing pack.json")
    try:
        manifest = PackManifest.from_file(manifest_path)
        validate_pack_root(root, manifest)
    except (OSError, TypeError, ValueError) as exc:
        return PackInspection(NEUROSCIENCE_PACK_ID, "error", root, reason=str(exc))
    if not manifest.enabled:
        return PackInspection(NEUROSCIENCE_PACK_ID, "installed_disabled", root, manifest)
    if (root.parent / PACK_DISABLED_MARKER).exists() or (root / PACK_DISABLED_MARKER).exists():
        return PackInspection(NEUROSCIENCE_PACK_ID, "installed_disabled", root, manifest)
    if not (root / "skills").is_dir():
        return PackInspection(NEUROSCIENCE_PACK_ID, "incomplete", root, manifest, "missing skills directory")
    missing_skills = [path for path in manifest.skills if not (root / path).is_dir()]
    if missing_skills:
        return PackInspection(
            NEUROSCIENCE_PACK_ID,
            "incomplete",
            root,
            manifest,
            f"missing declared skill directories: {', '.join(missing_skills)}",
        )
    return PackInspection(NEUROSCIENCE_PACK_ID, "ready", root, manifest)


def _skill_slug(summary: SkillSummary) -> str:
    return Path(summary.path).parent.name.casefold() if summary.path else ""


__all__ = [
    "BRAINLIB_MODULE_RESOURCES",
    "NEUROSCIENCE_PACK_ID",
    "NEUROSCIENCE_SKILLS",
    "PackInspection",
    "PackManifest",
    "active_neuroscience_pack",
    "brainlib_module_resource",
    "inspect_neuroscience_pack",
    "neuroscience_skill_summaries",
    "neuroscience_pack_candidate_roots",
    "resolve_neuroscience_skill_root",
    "set_neuroscience_pack_enabled",
]
