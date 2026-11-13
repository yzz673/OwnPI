from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from ownpi.app.config_tools import (
    configure_external_knowledge as _configure_external_knowledge,
    disable_external_knowledge_if_managed as _disable_external_knowledge_if_managed,
)
from ownpi.app.paths import AppPaths
from ownpi.product.distribution_contracts import PackArtifactManifest
from ownpi.product.installation_contracts import InstallationMetadata, InstalledComponent
from ownpi.product.installation_transaction import (
    backup_paths as _backup_paths,
    discard_backups as _discard_backups,
    install_lock as _install_lock,
    read_link as _read_link,
    replace_link as _replace_link,
    restore_file as _restore_file,
    restore_link as _restore_link,
    restore_marker as _restore_marker,
    restore_paths as _restore_paths,
)
from ownpi.product.pack_contracts import PackManifest, validate_pack_root
from ownpi.product.pack_installation_support import (
    PACK_ID,
    activate_knowledge_delivery as _activate_knowledge_delivery,
    component_digest as _component_digest,
    extract_archive as _extract_archive,
    install_managed_tool_receipts as _install_managed_tool_receipts,
    installation_metadata as _installation_metadata,
    install_wheelhouse as _install_wheelhouse,
    obtain_pack_artifact as _obtain_pack_artifact,
    pack_receipt_path as _pack_receipt_path,
    read_pack_receipt as _read_pack_receipt,
    repair_entrypoint_shebangs as _repair_entrypoint_shebangs,
    replace_components as _replace_components,
    run as _run,
    validate_dependency_union as _validate_dependency_union,
    write_metadata as _write_metadata,
    write_pack_receipt as _write_pack_receipt,
)
from ownpi.product.packs import inspect_neuroscience_pack


def install_or_update_neuroscience_pack(app_paths: AppPaths) -> dict[str, object]:
    metadata = _installation_metadata(app_paths)
    with tempfile.TemporaryDirectory(prefix="ownpi-pack-download-") as raw_tmp:
        temporary = Path(raw_tmp)
        artifact, digest = _obtain_pack_artifact(metadata, temporary)
        artifact_root = _extract_archive(artifact, temporary / "extracted")
        return install_neuroscience_pack_artifact(
            artifact_root=artifact_root,
            artifact_digest=digest,
            app_paths=app_paths,
        )


def install_neuroscience_pack_artifact(
    *,
    artifact_root: Path,
    artifact_digest: str,
    app_paths: AppPaths,
) -> dict[str, object]:
    metadata = _installation_metadata(app_paths)
    artifact = PackArtifactManifest.from_file(artifact_root / "pack-artifact.json")
    if artifact.target != metadata.target:
        raise RuntimeError(f"Neuroscience Pack target {artifact.target} does not match {metadata.target}")
    if artifact.component.digest != _component_digest(artifact_root / artifact.component.path):
        raise RuntimeError("Neuroscience Pack component digest mismatch")
    pack_source = artifact_root / artifact.component.path
    manifest = PackManifest.from_file(pack_source / "pack.json")
    validate_pack_root(pack_source, manifest)
    if Version(metadata.base_version) not in SpecifierSet(manifest.core_requires):
        raise RuntimeError(
            f"Neuroscience Pack {manifest.version} requires OwnPI Core {manifest.core_requires}; "
            f"installed Core is {metadata.base_version}"
        )
    if not any(item.component_id == "core" and item.status == "ready" for item in metadata.components):
        raise RuntimeError("Neuroscience Pack installation requires a ready OwnPI Core")
    _validate_dependency_union(app_paths.app_home, manifest)

    home = app_paths.app_home
    with _install_lock(home / "state/install.lock"):
        return _install_transaction(
            home=home,
            metadata=metadata,
            artifact=artifact,
            artifact_digest=artifact_digest,
            pack_source=pack_source,
            manifest=manifest,
        )


def remove_neuroscience_pack(app_paths: AppPaths) -> dict[str, object]:
    metadata = _installation_metadata(app_paths)
    home = app_paths.app_home
    with _install_lock(home / "state/install.lock"):
        _disable_external_knowledge_if_managed(
            app_paths.config_path,
            home / "packs/neuroscience",
        )
        shutil.rmtree(home / "packs/neuroscience", ignore_errors=True)
        shutil.rmtree(home / "tools/brainlib", ignore_errors=True)
        delivery = home / "external_knowledge/delivery"
        if delivery.is_symlink():
            delivery.unlink()
        components = tuple(item for item in metadata.components if item.component_id != "neuroscience")
        updated = _replace_components(metadata, components)
        _write_metadata(home / "state/installation.json", updated)
    return {"pack_id": "neuroscience", "status": "not_installed"}


def rollback_neuroscience_pack(app_paths: AppPaths) -> dict[str, object]:
    metadata = _installation_metadata(app_paths)
    home = app_paths.app_home
    with _install_lock(home / "state/install.lock"):
        pack_base = home / "packs/neuroscience"
        current = pack_base / "current"
        if not current.is_symlink():
            raise RuntimeError("Neuroscience Pack rollback requires an installed current release")
        current_release = current.resolve()
        candidates = [
            path
            for path in (pack_base / "releases").glob("*")
            if path.is_dir()
            and not path.name.startswith(".")
            and Version(path.name) < Version(current_release.name)
        ]
        if not candidates:
            raise RuntimeError("Neuroscience Pack has no previous release to roll back to")
        previous_release = max(candidates, key=lambda path: Version(path.name))
        manifest = PackManifest.from_file(previous_release / "pack.json")
        validate_pack_root(previous_release, manifest)
        _validate_dependency_union(home, manifest)
        receipt = _read_pack_receipt(pack_base, manifest.version)
        return _activate_existing_release(
            home=home,
            metadata=metadata,
            release=previous_release,
            manifest=manifest,
            component_digest=str(receipt["component_digest"]),
            artifact_digest=str(receipt["artifact_digest"]),
        )


def _install_transaction(
    *,
    home: Path,
    metadata: InstallationMetadata,
    artifact: PackArtifactManifest,
    artifact_digest: str,
    pack_source: Path,
    manifest: PackManifest,
) -> dict[str, object]:
    pack_base = home / "packs/neuroscience"
    release = pack_base / "releases" / manifest.version
    release_staging = pack_base / "releases" / f".{manifest.version}.staging.{os.getpid()}"
    venv = Path(metadata.managed_venv_path)
    venv_staging = venv.with_name(f".{venv.name}.pack-staging.{os.getpid()}")
    shutil.rmtree(release_staging, ignore_errors=True)
    shutil.rmtree(venv_staging, ignore_errors=True)
    release_staging.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(pack_source, release_staging, symlinks=True)
    shutil.copytree(venv, venv_staging, symlinks=True)
    try:
        staging_python = venv_staging / "bin/python"
        _install_wheelhouse(staging_python, release_staging / manifest.wheelhouse)
        _run([str(staging_python), "-c", "import brainlib"], "Brainlib import smoke")
        for tool in manifest.managed_tools:
            for entrypoint in tool.entrypoints:
                if not (venv_staging / "bin" / entrypoint).is_file():
                    raise RuntimeError(f"Pack entrypoint was not installed: {entrypoint}")
    except BaseException:
        shutil.rmtree(release_staging, ignore_errors=True)
        shutil.rmtree(venv_staging, ignore_errors=True)
        raise

    previous_venv = venv.with_name(f".{venv.name}.previous.{os.getpid()}")
    previous_release = release.with_name(f".{release.name}.previous.{os.getpid()}")
    shutil.rmtree(previous_venv, ignore_errors=True)
    shutil.rmtree(previous_release, ignore_errors=True)
    metadata_path = home / "state/installation.json"
    metadata_before = metadata_path.read_bytes()
    config_path = home / "config.yaml"
    config_before = config_path.read_bytes() if config_path.is_file() else None
    current_before = _read_link(pack_base / "current")
    knowledge_before = _read_link(home / "external_knowledge/delivery")
    disabled_before = (pack_base / ".disabled").exists()
    receipt_path = _pack_receipt_path(pack_base, manifest.version)
    receipt_before = receipt_path.read_bytes() if receipt_path.is_file() else None
    tool_destinations = tuple(home / "tools" / tool.tool_id / tool.version for tool in manifest.managed_tools)
    tool_backups = _backup_paths(tool_destinations)
    try:
        venv.rename(previous_venv)
        if release.exists() or release.is_symlink():
            release.rename(previous_release)
        release_staging.rename(release)
        venv_staging.rename(venv)
        _repair_entrypoint_shebangs(venv, manifest)
        _replace_link(pack_base / "current", Path("releases") / manifest.version)
        _install_managed_tool_receipts(home, release, venv, manifest)
        knowledge_root = _activate_knowledge_delivery(home, manifest)
        if disabled_before:
            _disable_external_knowledge_if_managed(config_path, pack_base)
        elif knowledge_root is not None:
            _configure_external_knowledge(config_path, knowledge_root)
        _write_pack_receipt(
            pack_base,
            version=manifest.version,
            component_digest=artifact.component.digest,
            artifact_digest=artifact_digest,
        )
        component = InstalledComponent(
            "neuroscience",
            artifact.version,
            "ready",
            artifact.component.digest,
            artifact_digest,
        )
        components = tuple(item for item in metadata.components if item.component_id != "neuroscience") + (
            component,
        )
        _write_metadata(metadata_path, _replace_components(metadata, components))
    except BaseException:
        shutil.rmtree(release, ignore_errors=True)
        shutil.rmtree(venv, ignore_errors=True)
        if previous_release.exists():
            previous_release.rename(release)
        if previous_venv.exists():
            previous_venv.rename(venv)
        _restore_paths(tool_backups)
        _restore_link(pack_base / "current", current_before)
        _restore_link(home / "external_knowledge/delivery", knowledge_before)
        _restore_marker(pack_base / ".disabled", disabled_before)
        _restore_file(receipt_path, receipt_before)
        _restore_file(metadata_path, metadata_before)
        _restore_file(config_path, config_before)
        raise
    _discard_backups(tool_backups)
    shutil.rmtree(previous_release, ignore_errors=True)
    shutil.rmtree(previous_venv, ignore_errors=True)
    inspection = inspect_neuroscience_pack(AppPaths.discover(app_home=home, repo_root=None))
    return inspection.to_dict()


def _activate_existing_release(
    *,
    home: Path,
    metadata: InstallationMetadata,
    release: Path,
    manifest: PackManifest,
    component_digest: str,
    artifact_digest: str,
) -> dict[str, object]:
    pack_base = home / "packs/neuroscience"
    venv = Path(metadata.managed_venv_path)
    venv_staging = venv.with_name(f".{venv.name}.pack-staging.{os.getpid()}")
    previous_venv = venv.with_name(f".{venv.name}.previous.{os.getpid()}")
    shutil.rmtree(venv_staging, ignore_errors=True)
    shutil.rmtree(previous_venv, ignore_errors=True)
    shutil.copytree(venv, venv_staging, symlinks=True)
    try:
        staging_python = venv_staging / "bin/python"
        _install_wheelhouse(staging_python, release / manifest.wheelhouse)
        _run([str(staging_python), "-c", "import brainlib"], "Brainlib rollback import smoke")
    except BaseException:
        shutil.rmtree(venv_staging, ignore_errors=True)
        raise

    metadata_path = home / "state/installation.json"
    metadata_before = metadata_path.read_bytes()
    config_path = home / "config.yaml"
    config_before = config_path.read_bytes() if config_path.is_file() else None
    current_before = _read_link(pack_base / "current")
    knowledge_before = _read_link(home / "external_knowledge/delivery")
    disabled_before = (pack_base / ".disabled").exists()
    tool_destinations = tuple(home / "tools" / tool.tool_id / tool.version for tool in manifest.managed_tools)
    tool_backups = _backup_paths(tool_destinations)
    venv.rename(previous_venv)
    try:
        venv_staging.rename(venv)
        _repair_entrypoint_shebangs(venv, manifest)
        _replace_link(pack_base / "current", Path("releases") / manifest.version)
        _install_managed_tool_receipts(home, release, venv, manifest)
        knowledge_root = _activate_knowledge_delivery(home, manifest)
        if disabled_before:
            _disable_external_knowledge_if_managed(config_path, pack_base)
        elif knowledge_root is not None:
            _configure_external_knowledge(config_path, knowledge_root)
        component = InstalledComponent(
            PACK_ID,
            manifest.version,
            "ready",
            component_digest,
            artifact_digest,
        )
        components = tuple(item for item in metadata.components if item.component_id != PACK_ID) + (
            component,
        )
        _write_metadata(metadata_path, _replace_components(metadata, components))
    except BaseException:
        shutil.rmtree(venv, ignore_errors=True)
        if previous_venv.exists():
            previous_venv.rename(venv)
        _restore_paths(tool_backups)
        _restore_link(pack_base / "current", current_before)
        _restore_link(home / "external_knowledge/delivery", knowledge_before)
        _restore_marker(pack_base / ".disabled", disabled_before)
        _restore_file(metadata_path, metadata_before)
        _restore_file(config_path, config_before)
        raise
    _discard_backups(tool_backups)
    shutil.rmtree(previous_venv, ignore_errors=True)
    return inspect_neuroscience_pack(AppPaths.discover(app_home=home, repo_root=None)).to_dict()


__all__ = [
    "install_neuroscience_pack_artifact",
    "install_or_update_neuroscience_pack",
    "remove_neuroscience_pack",
    "rollback_neuroscience_pack",
]
