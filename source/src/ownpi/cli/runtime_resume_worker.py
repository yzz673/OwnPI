"""Resume an existing run through the normal recovery entry in the background."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from ownpi.app.paths import AppPaths
from ownpi.config.loader import load_user_config
from ownpi.controller.runtime_control import RuntimeControlService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-dir', required=True, type=Path)
    parser.add_argument('--project-root', required=True, type=Path)
    parser.add_argument('--app-home', required=True, type=Path)
    parser.add_argument('--config', required=True, type=Path)
    args = parser.parse_args(argv)
    os.environ['OWNPI_HOME'] = str(args.app_home.resolve())
    paths = AppPaths.discover()
    from ownpi.cli.main import _load_runtime_env

    _load_runtime_env(paths)
    config = load_user_config(args.config)
    result = RuntimeControlService(
        user_config=config, project_root=args.project_root, app_paths=paths,
    ).resume(args.run_dir)
    return 0 if result.accepted else 1


if __name__ == '__main__':
    raise SystemExit(main())
