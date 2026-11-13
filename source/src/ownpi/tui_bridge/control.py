"""One-shot backend stop when an execution worker loses its Bridge pipe."""

from __future__ import annotations

import argparse
from pathlib import Path

from ownpi.app.paths import AppPaths
from ownpi.config.env_loader import load_dotenv
from ownpi.config.loader import load_user_config
from ownpi.controller.runtime_control import RuntimeControlService


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("config", "app-home", "project-root", "run-dir"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    paths = AppPaths.discover(app_home=args.app_home, repo_root=args.project_root)
    load_dotenv(paths.env_path)
    service = RuntimeControlService(
        user_config=load_user_config(args.config),
        project_root=args.project_root,
        app_paths=paths,
    )
    # This process never acquires execution ownership. Backend cancellation and
    # escalation remain entirely inside the existing stop service.
    service.stop(args.run_dir, reason="TUI Bridge disconnected")


if __name__ == "__main__":
    main()
