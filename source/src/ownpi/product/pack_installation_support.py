from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from ownpi.app.paths import AppPaths
from ownpi.product.dependency_bundle import DependencyBundleManifest
from ownpi.product.deployment import build_dependency_union, parse_requirement
from ownpi.product.distribution_contracts import DistributionManifest
from ownpi.product.installation_contracts import InstallationMetadata, InstalledComponent
from ownpi.product.installation_transaction import replace_link
from ownpi.product.pack_contracts import PackManifest


PACK_ID = "neuroscience"
PACK_RECEIPT_SCHEMA_VERSION = 1


def obtain_pack_artifact(metadata: InstallationMetadata, temporary: Path) -> tuple[Path, str]:
    test_artifact = os.environ.get("OWNPI_TEST_NEUROSCIENCE_ARTIFACT", "").strip()
    test_checksum = os.environ.get("OWNPI_TEST_NEUROSCIENCE_CHECKSUM", "").strip()
    if test_artifact:
        if not test_checksum:
            raise RuntimeError("Pack test artifact requires OWNPI_TEST_NEUROSCIENCE_CHECKSUM")
        artifact = Path(test_artifact).expanduser().resolve()
        expected = Path(test_checksum).read_text(encoding="utf-8").split()[0]
    else:
        if not metadata.update_manifest_url:
            raise RuntimeError("installation metadata does not configure a Pack distribution endpoint")
        manifest_url = (
            f"{metadata.update_manifest_url.rstrip('/')}/"
            f"ownpi-neuroscience-pack-{metadata.target}.json"
        )
        distribution_path = temporary / "distribution.json"
        download(manifest_url, distribution_path)
        distribution = DistributionManifest.from_file(distribution_path)
        if distribution.kind != "ownpi-neuroscience-pack" or distribution.target != metadata.target:
            raise RuntimeError("Pack distribution manifest does not match this installation")
        artifact = temporary / Path(distribution.artifact).name
        download(urljoin(manifest_url, distribution.artifact), artifact)
        expected = distribution.checksum
    actual = sha256_file(artifact)
    if actual != expected.casefold():
        raise RuntimeError("Neuroscience Pack artifact checksum mismatch")
    return artifact, actual


def extract_archive(archive: Path, destination: Path) -> Path:
    destination.mkdir(parents=True)
    with tarfile.open(archive, "r:gz") as handle:
        for member in handle.getmembers():
            path = Path(member.name)
            link = Path(member.linkname) if member.linkname else None
            if path.is_absolute() or ".." in path.parts:
                raise RuntimeError(f"unsafe Pack archive member: {member.name}")
            if link is not None and (link.is_absolute() or ".." in link.parts):
                raise RuntimeError(f"unsafe Pack archive link: {member.name}")
        handle.extractall(destination, filter="data")
    roots = [item for item in destination.iterdir() if item.is_dir()]
    if len(roots) != 1:
        raise RuntimeError("Neuroscience Pack archive must contain exactly one root directory")
    return roots[0]


def install_wheelhouse(python: Path, wheelhouse: Path) -> None:
    wheels = tuple(sorted(wheelhouse.glob("*.whl")))
    if not wheels:
        raise RuntimeError(f"Neuroscience Pack wheelhouse has no wheels: {wheelhouse}")
    command = [
        str(python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-index",
        "--no-deps",
        *(str(path) for path in wheels),
    ]
    run(command, "Neuroscience Pack package installation", timeout=1800)


def repair_entrypoint_shebangs(venv: Path, manifest: PackManifest) -> None:
    python = str(venv / "bin/python")
    for tool in manifest.managed_tools:
        for entrypoint in tool.entrypoints:
            path = venv / "bin" / entrypoint
            lines = path.read_text(encoding="utf-8").splitlines()
            if lines and lines[0].startswith("#!"):
                path.write_text("\n".join((f"#!{python}", *lines[1:])) + "\n", encoding="utf-8")


def install_managed_tool_receipts(
    home: Path,
    release: Path,
    venv: Path,
    manifest: PackManifest,
) -> None:
    for tool in manifest.managed_tools:
        destination = home / "tools" / tool.tool_id / tool.version
        staging = destination.with_name(f".{destination.name}.staging")
        shutil.rmtree(staging, ignore_errors=True)
        shutil.copytree(release / tool.path, staging)
        bin_dir = staging / "bin"
        bin_dir.mkdir(exist_ok=True)
        for entrypoint in tool.entrypoints:
            launcher = bin_dir / entrypoint
            launcher.write_text(
                "#!/usr/bin/env bash\nset -euo pipefail\n"
                f'exec "{venv / "bin" / entrypoint}" "$@"\n',
                encoding="utf-8",
            )
            launcher.chmod(0o755)
        shutil.rmtree(destination, ignore_errors=True)
        staging.rename(destination)


def activate_knowledge_delivery(home: Path, manifest: PackManifest) -> Path | None:
    if not manifest.knowledge_deliveries:
        return None
    delivery = manifest.knowledge_deliveries[0]
    link = home / "external_knowledge/delivery"
    target = home / "packs/neuroscience/current" / delivery.path
    physical_root = (
        home
        / "packs/neuroscience/releases"
        / manifest.version
        / delivery.path
    ).resolve(strict=True)
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.exists() and not link.is_symlink():
        raise RuntimeError(f"external knowledge path is not managed by OwnPI: {link}")
    replace_link(link, target)
    return physical_root


def pack_receipt_path(pack_base: Path, version: str) -> Path:
    return pack_base / "receipts" / f"{version}.json"


def write_pack_receipt(
    pack_base: Path,
    *,
    version: str,
    component_digest: str,
    artifact_digest: str,
) -> None:
    path = pack_receipt_path(pack_base, version)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": PACK_RECEIPT_SCHEMA_VERSION,
        "pack_id": PACK_ID,
        "version": version,
        "component_digest": component_digest,
        "artifact_digest": artifact_digest,
    }
    staging = path.with_suffix(".json.staging")
    staging.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    staging.replace(path)


def read_pack_receipt(pack_base: Path, version: str) -> dict[str, object]:
    path = pack_receipt_path(pack_base, version)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Neuroscience Pack rollback receipt is invalid: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Neuroscience Pack rollback receipt is invalid: {path}")
    expected = {"schema_version", "pack_id", "version", "component_digest", "artifact_digest"}
    if set(payload) != expected:
        raise RuntimeError(f"Neuroscience Pack rollback receipt has unexpected fields: {path}")
    if (
        payload.get("schema_version") != PACK_RECEIPT_SCHEMA_VERSION
        or payload.get("pack_id") != PACK_ID
        or payload.get("version") != version
    ):
        raise RuntimeError(f"Neuroscience Pack rollback receipt does not match release {version}")
    for field in ("component_digest", "artifact_digest"):
        value = str(payload.get(field) or "").casefold()
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise RuntimeError(f"Neuroscience Pack rollback receipt has invalid {field}")
        payload[field] = value
    return payload


def installation_metadata(app_paths: AppPaths) -> InstallationMetadata:
    path = app_paths.app_home / "state/installation.json"
    if not path.is_file():
        raise RuntimeError("OwnPI base installation metadata is missing")
    return InstallationMetadata.from_file(path)


def validate_dependency_union(home: Path, manifest: PackManifest) -> None:
    core_path = home / "components/core/current/component.json"
    bundle_path = home / "components/dependency-bundle/current/bundle.json"
    core = json.loads(core_path.read_text(encoding="utf-8"))
    if not isinstance(core, dict) or not isinstance(core.get("python_dependencies"), list):
        raise RuntimeError("installed Core dependency manifest is invalid")
    bundle = DependencyBundleManifest.from_file(bundle_path)
    union = build_dependency_union(
        [
            *(parse_requirement(str(item), source="core") for item in core["python_dependencies"]),
            *(parse_requirement(item, source="dependency-bundle") for item in bundle.python_dependencies),
            *(
                parse_requirement(item.requirement, source=f"neuroscience:{item.source}")
                for item in manifest.python_dependencies
            ),
        ]
    )
    if not union.ready:
        raise RuntimeError("Neuroscience Pack dependency conflict: " + "; ".join(union.conflicts))


def replace_components(
    metadata: InstallationMetadata,
    components: tuple[InstalledComponent, ...],
) -> InstallationMetadata:
    return InstallationMetadata(
        base_version=metadata.base_version,
        target=metadata.target,
        channel=metadata.channel,
        python_runtime_version=metadata.python_runtime_version,
        python_runtime_path=metadata.python_runtime_path,
        managed_venv_path=metadata.managed_venv_path,
        managed_tools_path=metadata.managed_tools_path,
        components=components,
        update_manifest_url=metadata.update_manifest_url,
    )


def component_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if not path.is_file() or path.is_symlink() or path.name == "component.json":
            continue
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def download(url: str, destination: Path) -> None:
    headers: dict[str, str] = {}
    token = os.environ.get("OWNPI_RELEASE_AUTH_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urlopen(Request(url, headers=headers), timeout=30) as response:
        destination.write_bytes(response.read())


def run(command: list[str], label: str, *, timeout: int = 300) -> None:
    completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-2000:]
        raise RuntimeError(f"{label} failed: {detail}")


def write_metadata(path: Path, metadata: InstallationMetadata) -> None:
    staging = path.with_suffix(".json.staging")
    staging.write_text(json.dumps(metadata.to_dict(), indent=2) + "\n", encoding="utf-8")
    staging.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
