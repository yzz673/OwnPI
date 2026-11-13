from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ownpi.schemas.runtime import UserConfig


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in {path}")
    return data


def load_user_config(path: Path, *, llm_profile: str | None = None) -> UserConfig:
    return UserConfig.from_dict(_read_yaml(path), llm_profile=llm_profile)
