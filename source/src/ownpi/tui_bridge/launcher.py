"""The independent ownpi-tui entry point, without PATH-based self discovery."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

from ownpi.app.paths import AppPaths
from ownpi.tui_bridge.binary import binary_path
from ownpi.tui_bridge.workspace import select_workspace
from ownpi.tui_bridge.onboarding import (
    ConfigurationProblem,
    SetupCancelled,
    prepare_configuration,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="OwnPI — your own principal investigator"
    )
    parser.add_argument("--config", type=Path)
    project = parser.add_mutually_exclusive_group()
    project.add_argument(
        "--project-root",
        "--workspace",
        type=Path,
        help="Open an existing project (default: current directory)",
    )
    project.add_argument(
        "--new-project", type=Path, help="Create a new, empty project directory"
    )
    parser.add_argument("--app-home", type=Path)
    parser.add_argument("--resume", metavar="RUN_ID")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.new_project and args.resume:
        parser.error("--new-project cannot be combined with --resume")
    paths = AppPaths.discover(app_home=args.app_home)
    config_path = (args.config or paths.config_path).expanduser().resolve()
    try:
        binary = binary_path()
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    try:
        project_root = select_workspace(
            args.new_project or args.project_root, create=args.new_project is not None
        )
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    try:
        configured_env = prepare_configuration(
            config_path,
            paths,
            project_root,
            interactive=bool(
                getattr(sys, "stdin", None)
                and sys.stdin.isatty()
                and sys.stdout.isatty()
            ),
        )
    except (SetupCancelled, KeyboardInterrupt, EOFError):
        print("\nSetup cancelled. Configuration unchanged.")
        return 130
    except ConfigurationProblem as error:
        parser.exit(1, f"{error}\n")
    except (OSError, UnicodeError):
        parser.exit(
            1,
            "Cannot read or save configuration. Check the configuration directory permissions and retry.\n",
        )
    command = [
        str(binary),
        "--bridge-python",
        sys.executable,
        "--config",
        str(config_path),
        "--project-root",
        str(project_root),
        "--app-home",
        str(paths.app_home),
    ]
    if args.resume:
        command.extend(["--resume", args.resume])
    if sys.flags.isolated:
        command.append("--bridge-python-isolated")
    environment = {**os.environ, **configured_env}
    if sys.stdout.isatty():
        # Interactive OwnPI uses its configured theme even when the invoking
        # shell/agent disables colors for ordinary command output. Keep this
        # override local to the TUI child; redirected launches retain NO_COLOR.
        environment.pop("NO_COLOR", None)
    try:
        return subprocess.call(command, env=environment)
    except OSError as error:
        parser.exit(1, f"Cannot start the OwnPI terminal: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
