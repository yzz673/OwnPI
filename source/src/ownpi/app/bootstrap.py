from __future__ import annotations

from pathlib import Path

from ownpi.app.paths import AppPaths
from ownpi.product.resources import read_template_resource


def _write_if_missing(path: Path, contents: str, *, force: bool) -> bool:
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    return True


def scaffold_app_home(paths: AppPaths, *, force: bool = False) -> list[Path]:
    paths.ensure_directories()
    created: list[Path] = []
    if _write_if_missing(paths.config_path, read_template_resource("user.config.yaml"), force=force):
        created.append(paths.config_path)
    if _write_if_missing(paths.env_example_path, read_template_resource("env.example"), force=force):
        created.append(paths.env_example_path)
    return created
