from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ownpi.runtime.limits.projection import MAX_REMOTE_WORKFLOW_PREVIEW_BYTES, MAX_REMOTE_WORKFLOW_PREVIEW_FILES
from ownpi.runtime.permissions.models import EffectivePermissions, FileSystemAccessMode
from ownpi.runtime.permissions.policy import require_file_access

_PREVIEW_EXTENSIONS = {
    ".csv",
    ".json",
    ".jsonl",
    ".log",
    ".md",
    ".out",
    ".tsv",
    ".txt",
    ".yaml",
    ".yml",
}

def _pulled_artifact_preview_data(local_destinations: Any, permissions: EffectivePermissions) -> dict[str, Any]:
    if not isinstance(local_destinations, list):
        return {}

    manifest: list[dict[str, Any]] = []
    previews: list[dict[str, Any]] = []
    for raw_destination in local_destinations:
        destination = Path(str(raw_destination)).expanduser()
        require_file_access(permissions, destination, FileSystemAccessMode.READ)
        if not destination.exists():
            manifest.append({"path": str(destination), "exists": False})
            continue
        candidates = [destination] if destination.is_file() else _preview_candidates(destination, permissions)
        manifest.append(
            {
                "path": str(destination),
                "exists": True,
                "kind": "file" if destination.is_file() else "directory",
                "preview_candidate_count": len(candidates),
            }
        )
        for candidate in candidates:
            if len(previews) >= MAX_REMOTE_WORKFLOW_PREVIEW_FILES:
                break
            preview = _preview_text_file(candidate, permissions)
            if preview:
                previews.append(preview)
        if len(previews) >= MAX_REMOTE_WORKFLOW_PREVIEW_FILES:
            break

    payload: dict[str, Any] = {"pulled_artifact_manifest": manifest}
    if previews:
        payload["pulled_artifact_previews"] = previews
    return payload


def _preview_candidates(root: Path, permissions: EffectivePermissions) -> list[Path]:
    candidates: list[Path] = []
    require_file_access(permissions, root, FileSystemAccessMode.READ)
    for path in sorted(root.iterdir()):
        require_file_access(permissions, path, FileSystemAccessMode.READ)
        if path.is_dir() and not path.is_symlink():
            candidates.extend(_preview_candidates(path, permissions))
            if len(candidates) >= MAX_REMOTE_WORKFLOW_PREVIEW_FILES:
                break
            continue
        if not path.is_file():
            continue
        if path.suffix.casefold() not in _PREVIEW_EXTENSIONS:
            continue
        candidates.append(path)
        if len(candidates) >= MAX_REMOTE_WORKFLOW_PREVIEW_FILES:
            break
    return candidates


def _preview_text_file(path: Path, permissions: EffectivePermissions) -> dict[str, Any] | None:
    require_file_access(permissions, path, FileSystemAccessMode.READ)
    try:
        size_bytes = path.stat().st_size
    except OSError:
        return None
    if size_bytes <= 0:
        return None
    try:
        with path.open('rb') as handle:
            raw = handle.read(MAX_REMOTE_WORKFLOW_PREVIEW_BYTES)
    except OSError:
        return None
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    if path.suffix.casefold() == ".json":
        text = _compact_json_preview(text)
    return {
        "path": str(path),
        "size_bytes": size_bytes,
        "truncated": size_bytes > MAX_REMOTE_WORKFLOW_PREVIEW_BYTES,
        "preview": text[:MAX_REMOTE_WORKFLOW_PREVIEW_BYTES],
    }


def _compact_json_preview(text: str) -> str:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text
    return json.dumps(parsed, ensure_ascii=False, indent=2)[
        :MAX_REMOTE_WORKFLOW_PREVIEW_BYTES
    ]
