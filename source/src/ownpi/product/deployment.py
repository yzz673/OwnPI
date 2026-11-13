"""Product installation inspection and update operations.

This module consumes only the canonical APP-home installation contract. It does
not select or mutate Runtime stages, provider behavior, or user workspaces.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping
from urllib.request import Request, urlopen

from packaging.requirements import Requirement

from ownpi.app.paths import AppPaths
from ownpi.product.distribution_contracts import DistributionManifest
from ownpi.product.installation_contracts import InstallationMetadata


@dataclass(frozen=True)
class DependencyRequirement:
    name: str
    constraints: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "constraints": list(self.constraints),
            "sources": list(self.sources),
        }


@dataclass(frozen=True)
class DependencyResolution:
    requirements: tuple[DependencyRequirement, ...]
    reused: tuple[str, ...]
    to_install: tuple[str, ...]
    conflicts: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.conflicts

    def to_dict(self) -> dict[str, object]:
        return {
            "requirements": [item.to_dict() for item in self.requirements],
            "reused": list(self.reused),
            "to_install": list(self.to_install),
            "conflicts": list(self.conflicts),
            "ready": self.ready,
            "policy": "single-managed-environment",
        }


@dataclass(frozen=True)
class DeploymentInspection:
    status: str
    base_version: str | None
    channel: str
    target: str | None
    app_home: Path
    managed_python: Path | None
    metadata_path: Path
    components: tuple[dict[str, str], ...] = ()
    reason: str = ""

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    @property
    def version(self) -> str | None:
        return self.base_version

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "ready": self.ready,
            "base_version": self.base_version,
            "channel": self.channel,
            "target": self.target,
            "app_home": str(self.app_home),
            "managed_python": str(self.managed_python) if self.managed_python else None,
            "metadata_path": str(self.metadata_path),
            "components": list(self.components),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class UpdateAvailability:
    status: str
    current_version: str | None
    available_version: str | None
    channel: str
    reason: str = ""

    @property
    def update_available(self) -> bool:
        return self.status == "available"

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "update_available": self.update_available,
            "current_version": self.current_version,
            "available_version": self.available_version,
            "channel": self.channel,
            "reason": self.reason,
        }


_EXACT_VERSION = re.compile(r"==\s*([A-Za-z0-9][A-Za-z0-9.+!-]*)")


def normalize_distribution_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", str(name).strip()).casefold()


def parse_requirement(raw_requirement: str, *, source: str = "") -> DependencyRequirement:
    parsed = Requirement(str(raw_requirement or "").strip())
    suffix = str(parsed.specifier)
    if parsed.extras:
        suffix = f"[{','.join(sorted(parsed.extras))}]{suffix}"
    if parsed.marker:
        suffix = f"{suffix}; {parsed.marker}"
    return DependencyRequirement(
        name=normalize_distribution_name(parsed.name),
        constraints=(suffix,) if suffix else (),
        sources=(source,) if source else (),
    )


def build_dependency_union(
    requirements: Iterable[DependencyRequirement | str],
    *,
    installed: Mapping[str, str] | Iterable[str] = (),
) -> DependencyResolution:
    merged: dict[str, DependencyRequirement] = {}
    exact_versions: dict[str, set[str]] = {}
    for item in requirements:
        requirement = parse_requirement(item) if isinstance(item, str) else item
        name = normalize_distribution_name(requirement.name)
        existing = merged.get(name)
        constraints = tuple(dict.fromkeys((*(existing.constraints if existing else ()), *requirement.constraints)))
        sources = tuple(dict.fromkeys((*(existing.sources if existing else ()), *requirement.sources)))
        merged[name] = DependencyRequirement(name, constraints, sources)
        for constraint in requirement.constraints:
            exact_versions.setdefault(name, set()).update(_EXACT_VERSION.findall(constraint))
    installed_names = _installed_names(installed)
    conflicts = tuple(
        f"{name}: incompatible exact versions {', '.join(sorted(versions))}"
        for name, versions in sorted(exact_versions.items())
        if len(versions) > 1
    )
    ordered = tuple(merged[name] for name in sorted(merged))
    return DependencyResolution(
        requirements=ordered,
        reused=tuple(item.name for item in ordered if item.name in installed_names),
        to_install=tuple(item.name for item in ordered if item.name not in installed_names),
        conflicts=conflicts,
    )


def inspect_deployment(app_home: Path | None = None) -> DeploymentInspection:
    home = (app_home or AppPaths.discover().app_home).expanduser().resolve()
    metadata_path = home / "state/installation.json"
    if not metadata_path.is_file():
        return DeploymentInspection(
            "not_installed", None, "", None, home, None, metadata_path,
            reason="canonical installation metadata is missing",
        )
    try:
        metadata = InstallationMetadata.from_file(metadata_path)
        _validate_managed_paths(home, metadata)
    except (OSError, TypeError, ValueError) as exc:
        return DeploymentInspection("failed", None, "", None, home, None, metadata_path, reason=str(exc))
    python = Path(metadata.managed_venv_path) / "bin/python"
    components = tuple(item.to_dict() for item in metadata.components)
    core = next((item for item in metadata.components if item.component_id == "core"), None)
    status = "ready" if core is not None and core.status == "ready" and python.is_file() else "failed"
    reason = "" if status == "ready" else "Core component or managed Python is not ready"
    return DeploymentInspection(
        status, metadata.base_version, metadata.channel, metadata.target, home,
        python, metadata_path, components, reason,
    )


def check_for_update(
    *,
    current_version: str,
    channel: str,
    metadata_source: str,
    timeout_seconds: float = 5.0,
) -> UpdateAvailability:
    try:
        manifest = _load_distribution_manifest(metadata_source, timeout_seconds=timeout_seconds)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return UpdateAvailability("unavailable", current_version, None, channel, str(exc))
    if manifest.kind != "ownpi-base" or manifest.channel != channel:
        return UpdateAvailability(
            "unavailable", current_version, manifest.version, channel, "release channel or kind mismatch"
        )
    status = "available" if _version_key(manifest.version) > _version_key(current_version) else "current"
    return UpdateAvailability(status, current_version, manifest.version, channel)


def update_installation(*, app_home: Path) -> int:
    home = app_home.expanduser().resolve()
    metadata = InstallationMetadata.from_file(home / "state/installation.json")
    installer = home / "components/base/current/resources/install.sh"
    if not installer.is_file():
        raise RuntimeError("installed base updater is missing")
    endpoint = metadata.update_manifest_url.rstrip("/")
    if not endpoint:
        raise RuntimeError("installation metadata does not configure an update endpoint")
    suffix = f"/{metadata.channel}"
    base_url = endpoint[: -len(suffix)] if endpoint.endswith(suffix) else endpoint
    environment = {
        **os.environ,
        "OWNPI_HOME": str(home),
        "OWNPI_RELEASE_CHANNEL": metadata.channel,
        "OWNPI_DISTRIBUTION_BASE_URL": base_url,
    }
    return subprocess.run(["bash", str(installer)], check=False, env=environment).returncode


def read_update_check_cache(path: Path, *, max_age_seconds: float) -> UpdateAvailability | None:
    try:
        if max_age_seconds >= 0 and path.stat().st_mtime + max_age_seconds < time.time():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        return UpdateAvailability(
            str(payload["status"]),
            _optional_text(payload.get("current_version")),
            _optional_text(payload.get("available_version")),
            str(payload["channel"]),
            str(payload.get("reason") or ""),
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def write_update_check_cache(path: Path, result: UpdateAvailability) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(".json.staging")
    staging.write_text(json.dumps(result.to_dict(), indent=2) + "\n", encoding="utf-8")
    staging.replace(path)


def _load_distribution_manifest(source: str, *, timeout_seconds: float) -> DistributionManifest:
    if source.startswith(("https://", "http://")):
        headers: dict[str, str] = {}
        token = os.environ.get("OWNPI_RELEASE_AUTH_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        with urlopen(Request(source, headers=headers), timeout=timeout_seconds) as response:
            return DistributionManifest.from_dict(json.loads(response.read().decode("utf-8")))
    return DistributionManifest.from_file(Path(source).expanduser())


def _validate_managed_paths(home: Path, metadata: InstallationMetadata) -> None:
    paths = {
        "python_runtime_path": Path(metadata.python_runtime_path),
        "managed_venv_path": Path(metadata.managed_venv_path),
        "managed_tools_path": Path(metadata.managed_tools_path),
    }
    for label, path in paths.items():
        try:
            path.relative_to(home)
        except ValueError as exc:
            raise ValueError(f"{label} escapes APP home") from exc
    if Path(metadata.managed_venv_path) != home / "env/ownpi":
        raise ValueError("managed_venv_path must be APP_HOME/env/ownpi")


def _installed_names(installed: Mapping[str, str] | Iterable[str]) -> set[str]:
    values = installed.keys() if isinstance(installed, Mapping) else installed
    return {normalize_distribution_name(str(value).split("==", 1)[0]) for value in values}


def _version_key(version: str) -> tuple[tuple[int, object], ...]:
    return tuple((0, int(part)) if part.isdigit() else (1, part) for part in re.split(r"[._+-]", version))


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


__all__ = [
    "DependencyRequirement",
    "DependencyResolution",
    "DeploymentInspection",
    "UpdateAvailability",
    "build_dependency_union",
    "check_for_update",
    "inspect_deployment",
    "normalize_distribution_name",
    "parse_requirement",
    "read_update_check_cache",
    "update_installation",
    "write_update_check_cache",
]
