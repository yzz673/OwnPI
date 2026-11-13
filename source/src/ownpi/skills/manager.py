from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from ownpi.app.paths import AppPaths
from ownpi.extensions.discovery import discover_extensions
from ownpi.schemas.context import SkillSummary
from ownpi.skills.bundled import get_bundled_skill_summaries
from ownpi.skills.catalog import SkippedSkill, build_skill_catalog, build_skill_catalog_result
from ownpi.skills.installer import (
    AvailableSkillInfo,
    SkillInstallerError,
    install_github_skill,
    install_local_skill,
    list_available_skills,
)

_SKILL_STATE_FILENAME = "skills_state.json"
_DEFAULT_HIDDEN_SKILLS = frozenset(
    {
        'mcp-integration',
        'feedback-authoring',
        'frontend-design',
        'ui-ux-pro-max',
        'webapp-testing',
        'post-acceptance',
        'session-wrap-up',
        'checkpoint-manager',
        'uv-package-manager',
    }
)


@dataclass(frozen=True)
class SkillInstallSpec:
    source_type: str
    source: str
    ref: str = "main"
    path: str | None = None
    name: str | None = None
    method: str = "auto"


@dataclass(frozen=True)
class InstalledSkillInfo:
    name: str
    path: str
    source: str
    description: str
    enabled: bool = True

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "name": self.name,
            "path": self.path,
            "source": self.source,
            "description": self.description,
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class RemovedSkillInfo:
    name: str
    removed_path: str

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "removed_path": self.removed_path,
        }


@dataclass(frozen=True)
class SkillCatalogStatus:
    installed: list[InstalledSkillInfo]
    skipped: list[SkippedSkill]


@dataclass
class EnabledSkillCatalogCache:
    fingerprint: str = ''
    catalog: tuple[SkillSummary, ...] = ()

    def resolve(
        self,
        app_paths: AppPaths,
        *,
        project_root: Path | None = None,
    ) -> tuple[SkillSummary, ...]:
        current_fingerprint = build_skill_catalog_fingerprint(
            app_paths,
            project_root=project_root,
        )
        if self.fingerprint != current_fingerprint:
            self.catalog = tuple(
                build_enabled_skill_catalog(
                    app_paths,
                    project_root=project_root,
                )
            )
            self.fingerprint = current_fingerprint
        return self.catalog


def list_installed_skills(app_paths: AppPaths, *, project_root: Path | None = None) -> list[InstalledSkillInfo]:
    return inspect_installed_skills(app_paths, project_root=project_root).installed


def inspect_installed_skills(app_paths: AppPaths, *, project_root: Path | None = None) -> SkillCatalogStatus:
    extension_snapshot = discover_extensions(app_paths, project_root=project_root)
    catalog_result = build_skill_catalog_result(app_paths.get_skill_roots())
    disabled = _load_disabled_skills(app_paths)
    skills = _all_skill_summaries(
        app_paths,
        catalog_result.skills,
        extension_snapshot.skills,
    )
    skipped = [
        *list(catalog_result.skipped),
        *[
            SkippedSkill(path=item.path, source=item.source, reason=item.reason)
            for item in extension_snapshot.skipped
            if item.kind == 'skill'
        ],
    ]
    pack = _pack_inspection(app_paths)
    if pack.status in {'error', 'incomplete'} and pack.root is not None:
        skipped.append(
            SkippedSkill(
                path=str(pack.root),
                source='pack:neuroscience',
                reason=pack.reason or f'pack status is {pack.status}',
            )
        )
    return SkillCatalogStatus(
        installed=[
            InstalledSkillInfo(
                name=item.name,
                path=item.path,
                source=item.source,
                description=item.description,
                enabled=item.name.casefold() not in disabled,
            )
            for item in skills
        ],
        skipped=skipped,
    )


def build_enabled_skill_catalog(app_paths: AppPaths, *, project_root: Path | None = None):
    disabled = _load_disabled_skills(app_paths)
    extension_snapshot = discover_extensions(app_paths, project_root=project_root)
    skills = _all_skill_summaries(
        app_paths,
        build_skill_catalog(app_paths.get_skill_roots()),
        extension_snapshot.skills,
    )
    return [
        item
        for item in skills
        if item.name.casefold() not in disabled
    ]


def _all_skill_summaries(
    app_paths: AppPaths,
    filesystem_skills: list[SkillSummary],
    extension_skills: tuple[SkillSummary, ...] | list[SkillSummary],
) -> list[SkillSummary]:
    from ownpi.product.packs import NEUROSCIENCE_SKILLS, neuroscience_skill_summaries

    bundled = [
        item
        for item in get_bundled_skill_summaries()
        if item.path.removeprefix('bundled://').casefold() not in NEUROSCIENCE_SKILLS
    ]
    pack_skills = {
        item.name.casefold(): item for item in neuroscience_skill_summaries(app_paths)
    }
    ordered_bundled: list[SkillSummary] = []
    for item in bundled:
        ordered_bundled.append(item)
        if item.name.casefold() == 'academic-paper-writing':
            ordered_bundled.extend(
                pack_skills.pop(name)
                for name in (
                    'brain-data-intake-audit',
                    'brain-signal-preprocessing-prior',
                    'brainlib',
                )
                if name in pack_skills
            )
        elif item.name.casefold() == 'mcp-integration':
            neuro_design = pack_skills.pop('neuro-ai-design', None)
            if neuro_design is not None:
                ordered_bundled.append(neuro_design)
    ordered_bundled.extend(pack_skills.values())
    return _dedupe_skill_summaries([*filesystem_skills, *extension_skills, *ordered_bundled])


def build_skill_catalog_fingerprint(
    app_paths: AppPaths,
    *,
    project_root: Path | None = None,
) -> str:
    paths: set[Path] = {_skill_state_path(app_paths)}
    for _, root in app_paths.get_skill_roots():
        paths.add(root)
        if root.is_dir():
            paths.update(_safe_rglob(root, 'SKILL.md'))
    for _, root in app_paths.get_extension_roots(project_root=project_root):
        paths.add(root)
        for relative_root in ('skills', 'plugins'):
            candidate = root / relative_root
            paths.add(candidate)
            if not candidate.is_dir():
                continue
            paths.update(_safe_rglob(candidate, 'SKILL.md'))
            paths.update(_safe_rglob(candidate, 'plugin.json'))
    from ownpi.product.packs import neuroscience_pack_candidate_roots

    for root in neuroscience_pack_candidate_roots(app_paths):
        paths.add(root)
        if root.is_dir():
            paths.update(_safe_rglob(root, 'pack.json'))
            paths.update(_safe_rglob(root, 'SKILL.md'))
    bundled_root = Path(__file__).resolve().parent / 'bundled' / 'content'
    paths.add(bundled_root)
    if bundled_root.is_dir():
        paths.update(_safe_rglob(bundled_root, 'SKILL.md'))
    payload = {
        'app_home': str(app_paths.app_home),
        'repo_root': str(app_paths.repo_root or ''),
        'project_root': str(project_root.expanduser().resolve())
        if project_root is not None
        else '',
        'paths': [
            _skill_catalog_path_metadata(path)
            for path in sorted(paths, key=lambda item: str(item))
        ],
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=False,
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _pack_inspection(app_paths: AppPaths):
    from ownpi.product.packs import inspect_neuroscience_pack

    return inspect_neuroscience_pack(app_paths)


def _skill_catalog_path_metadata(path: Path) -> dict[str, str | int | bool]:
    try:
        stat = path.stat()
    except OSError:
        return {'path': str(path), 'exists': False}
    return {
        'path': str(path),
        'exists': True,
        'is_dir': path.is_dir(),
        'size': int(stat.st_size),
        'mtime_ns': int(stat.st_mtime_ns),
    }


def _safe_rglob(root: Path, pattern: str) -> tuple[Path, ...]:
    try:
        return tuple(root.rglob(pattern))
    except OSError:
        return ()


def set_skill_enabled(
    app_paths: AppPaths,
    name: str,
    enabled: bool,
    *,
    project_root: Path | None = None,
) -> InstalledSkillInfo:
    requested = name.strip()
    if not requested:
        raise ValueError("Skill name cannot be empty.")

    installed = list_installed_skills(app_paths, project_root=project_root)
    matched: InstalledSkillInfo | None = None
    for item in installed:
        if item.name.casefold() == requested.casefold():
            matched = item
            break
    if matched is None:
        raise FileNotFoundError(f"Visible skill not found: {requested}")

    disabled, explicitly_enabled = _load_skill_state(app_paths)
    key = matched.name.casefold()
    if enabled:
        disabled.discard(key)
        explicitly_enabled.add(key)
    else:
        disabled.add(key)
        explicitly_enabled.discard(key)
    _save_skill_state(app_paths, disabled, explicitly_enabled)

    return InstalledSkillInfo(
        name=matched.name,
        path=matched.path,
        source=matched.source,
        description=matched.description,
        enabled=enabled,
    )


def toggle_skill_enabled(app_paths: AppPaths, name: str, *, project_root: Path | None = None) -> InstalledSkillInfo:
    installed = list_installed_skills(app_paths, project_root=project_root)
    for item in installed:
        if item.name.casefold() == name.strip().casefold():
            return set_skill_enabled(app_paths, item.name, not item.enabled, project_root=project_root)
    raise FileNotFoundError(f"Visible skill not found: {name}")


def install_skill(app_paths: AppPaths, spec: SkillInstallSpec) -> InstalledSkillInfo:
    app_paths.ensure_directories()
    if spec.source_type == "local":
        destination = install_local_skill(app_paths, Path(spec.source), name=spec.name)
        source = f"local:{Path(spec.source).expanduser().resolve()}"
    elif spec.source_type == "github":
        if not spec.path:
            raise ValueError("GitHub install requires a path inside the repository.")
        destination = install_github_skill(
            app_paths,
            repo=spec.source,
            ref=spec.ref,
            path=spec.path,
            name=spec.name,
            method=spec.method,
        )
        source = f"github:{spec.source}@{spec.ref}/{spec.path}"
    else:
        raise ValueError(f"Unsupported skill source_type: {spec.source_type!r}")
    skill_file = destination / "SKILL.md"
    return InstalledSkillInfo(
        name=destination.name,
        path=str(skill_file),
        source=source,
        description=_extract_description(skill_file.read_text(encoding="utf-8")),
        enabled=True,
    )


def remove_skill(app_paths: AppPaths, name: str) -> RemovedSkillInfo:
    requested = name.strip()
    if not requested:
        raise ValueError("Skill name cannot be empty.")

    target_dir = app_paths.skills_dir / requested
    if target_dir.exists():
        skill_root = target_dir.resolve()
        if not (skill_root / "SKILL.md").is_file():
            raise ValueError(f"Skill directory must contain SKILL.md: {skill_root}")
        shutil.rmtree(skill_root)
        disabled, explicitly_enabled = _load_skill_state(app_paths)
        key = requested.casefold()
        disabled.discard(key)
        explicitly_enabled.discard(key)
        _save_skill_state(app_paths, disabled, explicitly_enabled)
        return RemovedSkillInfo(name=requested, removed_path=str(skill_root))

    installed = list_installed_skills(app_paths)
    for item in installed:
        if item.name.casefold() == requested.casefold() and item.source != "app_home":
            raise FileNotFoundError(
                f"Skill {requested!r} is not installed in ~/.ownpi/skills; current visible source is {item.source!r}."
            )
    raise FileNotFoundError(f"Skill not found in ~/.ownpi/skills: {requested}")


def parse_github_url(url: str) -> SkillInstallSpec:
    cleaned = url.strip().rstrip("/")
    marker = "github.com/"
    if marker not in cleaned or "/tree/" not in cleaned:
        raise ValueError("GitHub skill URL must look like https://github.com/<owner>/<repo>/tree/<ref>/<path>.")
    after_host = cleaned.split(marker, 1)[1]
    repo_part, tree_part = after_host.split("/tree/", 1)
    repo_part = repo_part.strip("/")
    tree_segments = [segment for segment in tree_part.split("/") if segment]
    if len(tree_segments) < 2:
        raise ValueError("GitHub skill URL must include both <ref> and <path>.")
    ref = tree_segments[0]
    path = "/".join(tree_segments[1:])
    return SkillInstallSpec(source_type="github", source=repo_part, ref=ref, path=path)


def _extract_description(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    for index, line in enumerate(lines):
        if line == "---":
            continue
        if line.startswith("description:"):
            return line.split(":", 1)[1].strip() or "No description provided."
        if line.startswith("#"):
            break
    for line in lines:
        if line.startswith("#"):
            continue
        if line:
            return line
    return "No description provided."


def _skill_state_path(app_paths: AppPaths) -> Path:
    return app_paths.app_home / _SKILL_STATE_FILENAME


def _load_disabled_skills(app_paths: AppPaths) -> set[str]:
    disabled, explicitly_enabled = _load_skill_state(app_paths)
    return (_DEFAULT_HIDDEN_SKILLS | disabled) - explicitly_enabled


def _load_skill_state(app_paths: AppPaths) -> tuple[set[str], set[str]]:
    path = _skill_state_path(app_paths)
    if not path.exists():
        return set(), set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set(), set()
    raw_items = payload.get("disabled_skills", [])
    raw_enabled = payload.get("enabled_skills", [])
    disabled = (
        {str(item).strip().casefold() for item in raw_items if str(item).strip()}
        if isinstance(raw_items, list)
        else set()
    )
    explicitly_enabled = (
        {str(item).strip().casefold() for item in raw_enabled if str(item).strip()}
        if isinstance(raw_enabled, list)
        else set()
    )
    return disabled, explicitly_enabled


def _save_skill_state(
    app_paths: AppPaths,
    disabled: set[str],
    explicitly_enabled: set[str],
) -> None:
    app_paths.ensure_directories()
    payload = {
        "disabled_skills": sorted(disabled),
        "enabled_skills": sorted(explicitly_enabled),
    }
    _skill_state_path(app_paths).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _dedupe_skill_summaries(items):
    seen: set[str] = set()
    deduped = []
    for item in items:
        key = item.name.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


__all__ = [
    "AvailableSkillInfo",
    "EnabledSkillCatalogCache",
    "InstalledSkillInfo",
    "RemovedSkillInfo",
    "SkillCatalogStatus",
    "SkillInstallSpec",
    "SkillInstallerError",
    "build_enabled_skill_catalog",
    "build_skill_catalog_fingerprint",
    "inspect_installed_skills",
    "install_skill",
    "list_available_skills",
    "list_installed_skills",
    "parse_github_url",
    "remove_skill",
    "set_skill_enabled",
    "toggle_skill_enabled",
]
