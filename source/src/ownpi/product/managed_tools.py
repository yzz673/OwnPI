"""Read-only inspection of Pack-provided managed tool entrypoints."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ownpi.app.paths import AppPaths
from ownpi.product.packs import active_neuroscience_pack


@dataclass(frozen=True)
class ManagedToolInspection:
    name: str
    status: str
    summary: str
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "summary": self.summary,
            "details": self.details,
        }


def resolve_brainlib_command(app_paths: AppPaths) -> Path | None:
    inspection = active_neuroscience_pack(app_paths)
    if inspection is None or inspection.manifest is None:
        return None
    tool = next((item for item in inspection.manifest.managed_tools if item.tool_id == "brainlib"), None)
    if tool is None:
        return None
    command = app_paths.app_home / "tools/brainlib" / tool.version / "bin/brainlib"
    return command if command.is_file() else None


def inspect_brainlib(app_paths: AppPaths) -> ManagedToolInspection:
    pack = active_neuroscience_pack(app_paths)
    if pack is None or pack.manifest is None:
        return ManagedToolInspection(
            "brainlib",
            "warning",
            "neuroscience Pack is not installed",
            {"pack": "neuroscience", "command": None},
        )
    command = resolve_brainlib_command(app_paths)
    details: dict[str, Any] = {
        "pack_version": pack.manifest.version,
        "managed_venv": str(app_paths.app_home / "env/ownpi"),
        "command": str(command) if command else None,
    }
    if command is None:
        return ManagedToolInspection("brainlib", "error", "managed entrypoint is missing", details)
    completed = subprocess.run(
        [str(command), "--quiet", "catalog", "show", "--domain", "model", "--format", "json"],
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    details["smoke"] = {
        "returncode": completed.returncode,
        "stdout": completed.stdout[-2000:],
        "stderr": completed.stderr[-2000:],
    }
    if completed.returncode != 0:
        return ManagedToolInspection("brainlib", "error", "managed entrypoint smoke failed", details)
    return ManagedToolInspection("brainlib", "ok", "ready", details)


__all__ = ["ManagedToolInspection", "inspect_brainlib", "resolve_brainlib_command"]
