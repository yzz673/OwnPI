from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ownpi.runtime.limits.workflow import (
    review_profile_selection_from_value,
)
from ownpi.product.resources import read_template_resource
from ownpi.utils.io import write_text_atomic


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = yaml.safe_load(file) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Expected mapping in {path}")
    return payload


def set_review_profile(config_path: Path, value: str) -> dict[str, Any]:
    normalized = review_profile_selection_from_value(value)
    payload = _read_yaml_mapping(config_path)
    runtime = payload.get("runtime") or {}
    if not isinstance(runtime, dict):
        raise ValueError("runtime section must be a mapping.")

    old_value = str(runtime.get("review_profile", "auto"))
    runtime["review_profile"] = normalized
    payload["runtime"] = runtime

    with config_path.open("w", encoding="utf-8") as file:
        yaml.safe_dump(payload, file, sort_keys=False, allow_unicode=True)

    return {
        "config_path": str(config_path),
        "updated": "runtime.review_profile",
        "old_value": old_value,
        "new_value": normalized,
    }


def configure_external_knowledge(
    config_path: Path,
    delivery_root: Path,
) -> None:
    root = delivery_root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("External Knowledge delivery root must be a directory")
    payload = (
        _read_yaml_mapping(config_path)
        if config_path.is_file()
        else _read_yaml_text_mapping(read_template_resource("user.config.yaml"))
    )
    runtime = payload.get("runtime") or {}
    if not isinstance(runtime, dict):
        raise ValueError("runtime section must be a mapping.")
    external = runtime.get("external_knowledge") or {}
    if not isinstance(external, dict):
        raise ValueError("runtime.external_knowledge section must be a mapping.")
    external["enabled"] = True
    external["delivery_root"] = str(root)
    runtime["external_knowledge"] = external
    payload["runtime"] = runtime
    _write_yaml_atomic(config_path, payload)


def disable_external_knowledge_if_managed(
    config_path: Path,
    managed_pack_root: Path,
) -> None:
    if not config_path.is_file():
        return
    payload = _read_yaml_mapping(config_path)
    runtime = payload.get("runtime") or {}
    if not isinstance(runtime, dict):
        raise ValueError("runtime section must be a mapping.")
    external = runtime.get("external_knowledge") or {}
    if not isinstance(external, dict):
        raise ValueError("runtime.external_knowledge section must be a mapping.")
    configured = str(external.get("delivery_root", "") or "").strip()
    if not configured:
        return
    try:
        Path(configured).expanduser().resolve(strict=False).relative_to(
            managed_pack_root.expanduser().resolve(strict=False)
        )
    except ValueError:
        return
    external["enabled"] = False
    external["delivery_root"] = ""
    runtime["external_knowledge"] = external
    payload["runtime"] = runtime
    _write_yaml_atomic(config_path, payload)


def _write_yaml_atomic(path: Path, payload: dict[str, Any]) -> None:
    content = yaml.safe_dump(
        payload,
        sort_keys=False,
        allow_unicode=True,
    )
    write_text_atomic(path, content)


def _read_yaml_text_mapping(content: str) -> dict[str, Any]:
    payload = yaml.safe_load(content) or {}
    if not isinstance(payload, dict):
        raise ValueError("Expected user config template to contain a mapping")
    return payload
