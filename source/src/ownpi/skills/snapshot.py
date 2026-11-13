from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ownpi.app.paths import AppPaths
from ownpi.schemas.context import SkillSummary
from ownpi.skills.manager import (
    build_enabled_skill_catalog,
    build_skill_catalog_fingerprint,
)


@dataclass(frozen=True)
class SkillCatalogSnapshot:
    skills: tuple[SkillSummary, ...]
    source_fingerprint: str
    digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.skills, tuple):
            raise TypeError("skill catalog snapshot must use an immutable tuple")
        if not all(isinstance(item, SkillSummary) for item in self.skills):
            raise TypeError("skill catalog snapshot requires SkillSummary entries")
        if not self.source_fingerprint.strip():
            raise ValueError("skill catalog snapshot requires a source fingerprint")
        if self.digest != _catalog_digest(self.skills):
            raise ValueError("skill catalog snapshot digest does not match its skills")


def build_skill_catalog_snapshot(
    *,
    app_paths: AppPaths,
    project_root: Path | None = None,
) -> SkillCatalogSnapshot:
    """Wrap the existing enabled-catalog production without changing its behavior."""
    skills = tuple(
        build_enabled_skill_catalog(
            app_paths,
            project_root=project_root,
        )
    )
    return SkillCatalogSnapshot(
        skills=skills,
        source_fingerprint=build_skill_catalog_fingerprint(
            app_paths,
            project_root=project_root,
        ),
        digest=_catalog_digest(skills),
    )


def _catalog_digest(skills: tuple[SkillSummary, ...]) -> str:
    encoded = json.dumps(
        [item.to_dict() for item in skills],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "SkillCatalogSnapshot",
    "build_skill_catalog_snapshot",
]
