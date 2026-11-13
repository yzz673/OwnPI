from __future__ import annotations

import os
import json
import subprocess
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from ownpi.app.external_knowledge_release import (
    default_external_knowledge_install_root,
    inspect_external_knowledge_installation,
)
from ownpi.app.paths import AppPaths
from ownpi.app.setup import inspect_llm_readiness
from ownpi.config.loader import load_user_config
from ownpi.product.resources import inspect_product_resources
from ownpi.product.deployment import inspect_deployment
from ownpi.product.installation_contracts import InstallationMetadata
from ownpi.product.managed_tools import inspect_brainlib
from ownpi.product.packs import active_neuroscience_pack, inspect_neuroscience_pack
from ownpi.tools.pdf_reader import inspect_pdf_tool_status


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    summary: str
    details: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "status": self.status,
            "summary": self.summary,
            "details": self.details,
        }


@dataclass(frozen=True)
class DoctorReport:
    ok: bool
    install_check: bool
    checks: list[DoctorCheck]

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "install_check": self.install_check,
            "checks": [check.to_dict() for check in self.checks],
        }


def run_doctor(app_paths: AppPaths, *, install_check: bool = False) -> DoctorReport:
    checks = [
        _check_python_version(),
        _check_package_version(),
        _check_deployment(app_paths),
        _check_tui(app_paths),
        _check_app_home(app_paths),
        _check_product_resources(),
        _check_config(app_paths, required=not install_check),
        _check_env_file(app_paths, required=not install_check),
        _check_llm(app_paths, required=not install_check),
        _check_pdf_status(),
        _check_external_knowledge(app_paths),
        _check_neuroscience_pack(app_paths),
        _check_dependency_bundle(app_paths),
        _check_managed_latex(app_paths),
        _check_brainlib(
            app_paths,
            required=(
                install_check
                and active_neuroscience_pack(app_paths) is not None
                and os.environ.get("OWNPI_INSTALL_WITH_BRAINLIB", "true") != "false"
            ),
        ),
    ]
    return DoctorReport(
        ok=all(check.status != "error" for check in checks),
        install_check=install_check,
        checks=checks,
    )


def render_doctor_report(report: DoctorReport) -> str:
    lines = [
        "# OwnPI Doctor",
        "",
        f"status: {'ok' if report.ok else 'error'}",
        f"install_check: {str(report.install_check).lower()}",
        "",
        "## Checks",
    ]
    for check in report.checks:
        lines.append(f"- {check.status}: {check.name} - {check.summary}")
    return "\n".join(lines)


def _check_python_version() -> DoctorCheck:
    version = sys.version_info
    ok = version >= (3, 11)
    return DoctorCheck(
        name="python_version",
        status="ok" if ok else "error",
        summary=f"{version.major}.{version.minor}.{version.micro}",
        details={
            "executable": sys.executable,
            "version": sys.version,
            "requires": ">=3.11",
        },
    )


def _check_package_version() -> DoctorCheck:
    try:
        version = metadata.version("ownpi")
    except metadata.PackageNotFoundError:
        from ownpi import __version__

        version = __version__
    return DoctorCheck(
        name="package_version",
        status="ok",
        summary=version,
        details={"package": "ownpi", "version": version},
    )


def _check_deployment(app_paths: AppPaths) -> DoctorCheck:
    inspection = inspect_deployment(app_paths.app_home)
    status = "ok" if inspection.status == "ready" else "error"
    return DoctorCheck(
        name="deployment",
        status=status,
        summary=inspection.status,
        details=inspection.to_dict(),
    )


def _check_tui(app_paths: AppPaths) -> DoctorCheck:
    metadata_path = app_paths.app_home / "state/installation.json"
    if metadata_path.is_file():
        try:
            installation = InstallationMetadata.from_file(metadata_path)
            core = json.loads((app_paths.app_home / "components/core/current/component.json").read_text())
            if not isinstance(core, dict) or "ownpi-tui" not in core.get("capabilities", []):
                raise ValueError("installed Core does not declare TUI; update OwnPI")
            python = Path(installation.managed_venv_path) / "bin/python"
        except (OSError, ValueError, TypeError) as error:
            return DoctorCheck("tui", "error", str(error), {})
    else:
        python = Path(sys.executable)
    probe = (
        "import json\nfrom pathlib import Path\nfrom ownpi.tui_bridge import binary\n"
        "source = (Path(binary.__file__).resolve().parents[1] / 'ui/Cargo.toml').is_file()\n"
        "try:\n result = {'status': 'ok', 'details': binary.check_installed()}\n"
        "except Exception as error:\n result = {'status': 'warning' if source else 'error', 'error': str(error)}\n"
        "result.setdefault('details', {})['source_checkout'] = source\n"
        "print(json.dumps(result))\n"
    )
    try:
        result = subprocess.run([str(python), "-I", "-c", probe], check=False, capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError(f"TUI check failed: {result.stderr[-1000:]}")
        payload = json.loads(result.stdout)
        return DoctorCheck("tui", payload["status"], payload.get("error", "ready"), payload.get("details", {}))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        return DoctorCheck("tui", "error", str(error), {})


def _check_app_home(app_paths: AppPaths) -> DoctorCheck:
    exists = app_paths.app_home.exists()
    writable = _is_writable(app_paths.app_home if exists else app_paths.app_home.parent)
    status = "ok" if exists and writable else "warning" if writable else "error"
    return DoctorCheck(
        name="app_home",
        status=status,
        summary=str(app_paths.app_home),
        details={
            "app_home": str(app_paths.app_home),
            "exists": exists,
            "writable": writable,
            "config_path": str(app_paths.config_path),
            "env_path": str(app_paths.env_path),
        },
    )


def _check_product_resources() -> DoctorCheck:
    inspection = inspect_product_resources()
    counts = {
        "templates": len(inspection.templates),
        "always_on_instructions": len(inspection.always_on_instructions),
        "bundled_skills": len(inspection.bundled_skills),
    }
    return DoctorCheck(
        name="product_resources",
        status="ok" if inspection.ok else "error",
        summary=", ".join(f"{key}={value}" for key, value in counts.items()),
        details=inspection.to_dict(),
    )


def _check_config(app_paths: AppPaths, *, required: bool) -> DoctorCheck:
    if not app_paths.config_path.exists():
        return DoctorCheck(
            name="config",
            status="error" if required else "warning",
            summary="config.yaml is missing",
            details={"config_path": str(app_paths.config_path), "exists": False},
        )
    try:
        user_config = load_user_config(app_paths.config_path)
    except (OSError, ValueError) as exc:
        return DoctorCheck(
            name="config",
            status="error",
            summary="config.yaml is not readable or invalid",
            details={"config_path": str(app_paths.config_path), "error": str(exc)},
        )
    return DoctorCheck(
        name="config",
        status="ok",
        summary=str(app_paths.config_path),
        details={
            "config_path": str(app_paths.config_path),
            "runtime": {
                "workspace_dir": user_config.runtime.workspace_dir,
                "output_dir": user_config.runtime.output_dir,
            },
            "llm_profile": {
                "active": user_config.selected_llm_profile or user_config.default_llm_name,
                "available": list(user_config.selectable_llm_profiles),
            },
        },
    )


def _check_env_file(app_paths: AppPaths, *, required: bool) -> DoctorCheck:
    exists = app_paths.env_path.exists()
    status = "ok" if exists else "error" if required else "warning"
    mode = ""
    if exists:
        try:
            mode = oct(app_paths.env_path.stat().st_mode & 0o777)
        except OSError:
            mode = "<unavailable>"
    return DoctorCheck(
        name="env_file",
        status=status,
        summary=str(app_paths.env_path) if exists else ".env is missing",
        details={"env_path": str(app_paths.env_path), "exists": exists, "mode": mode},
    )


def _check_llm(app_paths: AppPaths, *, required: bool) -> DoctorCheck:
    if not app_paths.config_path.exists():
        return DoctorCheck(
            name="llm",
            status="error" if required else "warning",
            summary="config.yaml is missing",
            details={},
        )
    try:
        user_config = load_user_config(app_paths.config_path)
        active_llm = user_config.get_llm_config()
        readiness = inspect_llm_readiness(active_llm)
        from ownpi.runtime.builders import build_effective_llm_snapshot

        llm_snapshot = build_effective_llm_snapshot(active_llm)
    except (OSError, ValueError) as exc:
        return DoctorCheck(name="llm", status="error", summary="LLM config invalid", details={"error": str(exc)})
    status = "ok" if readiness.ready_for_real_llm or readiness.is_mock else "error" if required else "warning"
    return DoctorCheck(
        name="llm",
        status=status,
        summary=f"{readiness.provider}/{readiness.model}",
        details={
            "provider": readiness.provider,
            "model": readiness.model,
            "is_mock": readiness.is_mock,
            "has_api_key": readiness.has_api_key,
            "has_base_url": readiness.has_base_url,
            "ready_for_real_llm": readiness.ready_for_real_llm,
            "web_search_execution_mode": llm_snapshot["web_search_execution_mode"],
            "hosted_web_search_spec_digest": llm_snapshot[
                "hosted_web_search_spec_digest"
            ],
        },
    )


def _check_pdf_status() -> DoctorCheck:
    try:
        status = inspect_pdf_tool_status().to_dict()
    except ModuleNotFoundError as exc:
        status = {"available": False, "error": f"missing optional runtime dependency: {exc.name}"}
    available = bool(status.get("available_extractors"))
    return DoctorCheck(
        name="pdf_tooling",
        status="ok" if available else "warning",
        summary="available" if available else "not available",
        details=status,
    )


def _check_external_knowledge(app_paths: AppPaths) -> DoctorCheck:
    install_root = default_external_knowledge_install_root(app_paths)
    try:
        payload = inspect_external_knowledge_installation(install_root)
    except (OSError, ValueError) as exc:
        payload: dict[str, object] = {"install_root": str(install_root), "error": str(exc)}
    installed = bool(payload.get("ready"))
    return DoctorCheck(
        name="external_knowledge",
        status="ok" if installed else "warning",
        summary="installed" if installed else "not installed",
        details=payload,
    )


def _check_brainlib(app_paths: AppPaths, *, required: bool) -> DoctorCheck:
    inspection = inspect_brainlib(app_paths)
    status = inspection.status
    if required and status == "warning":
        status = "error"
    return DoctorCheck(
        name="brainlib",
        status=status,
        summary=inspection.summary,
        details=inspection.details,
    )


def _check_neuroscience_pack(app_paths: AppPaths) -> DoctorCheck:
    inspection = inspect_neuroscience_pack(app_paths)
    return DoctorCheck(
        name="neuroscience_pack",
        status="ok" if inspection.status in {"ready", "installed_disabled", "not_installed"} else "warning",
        summary=inspection.status,
        details=inspection.to_dict(),
    )


def _check_dependency_bundle(app_paths: AppPaths) -> DoctorCheck:
    metadata_path = app_paths.app_home / "state/installation.json"
    try:
        metadata = InstallationMetadata.from_file(metadata_path)
        component = next(item for item in metadata.components if item.component_id == "dependency-bundle")
    except (OSError, StopIteration, TypeError, ValueError) as exc:
        return DoctorCheck("dependency_bundle", "warning", "not_installed", {"reason": str(exc)})
    return DoctorCheck(
        name="dependency_bundle",
        status="ok" if component.status == "ready" else "warning",
        summary=component.status,
        details=component.to_dict(),
    )


def _check_managed_latex(app_paths: AppPaths) -> DoctorCheck:
    roots = sorted((app_paths.app_home / "tools/latex").glob("*/bin"))
    required = ("latexmk", "pdflatex", "bibtex", "biber", "perl")
    selected = roots[-1] if roots else None
    missing = [name for name in required if selected is None or not (selected / name).is_file()]
    unsupported = [name for name in ("xelatex", "lualatex") if selected is None or not (selected / name).is_file()]
    return DoctorCheck(
        name="managed_latex",
        status="ok" if not missing else "warning",
        summary="ready" if not missing else "incomplete",
        details={
            "bin_dir": str(selected) if selected else None,
            "required": list(required),
            "missing": missing,
            "unsupported_optional_engines": unsupported,
        },
    )


def _is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return os.access(path, os.W_OK)
