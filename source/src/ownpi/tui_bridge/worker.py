"""Run the existing backend on the main thread of an isolated execution process."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import sys
import subprocess
import traceback
from threading import Event, Lock, Thread
from typing import Any, Callable

from ownpi.app.paths import AppPaths
from ownpi.config.env_loader import load_dotenv
from ownpi.config.loader import load_user_config
from ownpi.controller.run_context import PreparedRunContext
from ownpi.controller.runtime_control import RuntimeControlService
from ownpi.controller.runtime_active_owner import read_active_runtime_owner
from ownpi.controller.session_manager import RuntimeSessionManager
from ownpi.diagnostics import diagnostic_session
from ownpi.schemas.runtime import UserConfig


def submit_direct(
    *, service: RuntimeControlService, config: UserConfig, paths: AppPaths,
    run_dir: Path, project_root: Path, message: str,
    callback: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    from ownpi.controller.engine import WorkflowEngine

    engine = WorkflowEngine(user_config=config, project_root=project_root,
                            app_paths=paths, runtime_status_callback=callback)
    try:
        with diagnostic_session(run_dir, operation="tui_direct_submit"):
            owner = engine.open_direct_session(run_dir=run_dir)
            package = owner.submit(message)
    finally:
        engine.close(wait=True)
    # There is no service.submit for a subsequent Direct request. These public
    # store calls publish its result; they do not implement recovery.
    session, task, run = service.run_identity(run_dir)
    store = RuntimeSessionManager(app_paths=paths, project_root=project_root).store
    store.update_run_status(session, task, run, status=package.status)
    task_status = "blocked" if package.status in {"paused_for_human", "interrupted", "stopped"} else package.status
    store.update_task_status(session, task, status=task_status, active_run_id=run)
    return package.to_dict()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--app-home", required=True, type=Path)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--action", choices=("start", "resume", "direct_submit"), required=True)
    args = parser.parse_args()
    wire = sys.stdout
    wire_lock = Lock()
    execution_finished = Event()

    def emit(kind: str, data: dict[str, Any]) -> None:
        with wire_lock:
            wire.write(
                json.dumps(
                    {"type": kind, "data": data}, ensure_ascii=False, allow_nan=False
                )
                + "\n"
            )
            wire.flush()

    try:
        with redirect_stdout(sys.stderr):
            paths = AppPaths.discover(
                app_home=args.app_home, repo_root=args.project_root
            )
            load_dotenv(paths.env_path)
            config = load_user_config(args.config)
            emit("execution_config", {"permissions": config.runtime.permissions.preset.value})
            service = RuntimeControlService(
                user_config=config, project_root=args.project_root, app_paths=paths
            )

            message = ""
            if args.action == "direct_submit":
                request = json.loads(sys.stdin.readline())
                message = request.get("text")
                if not isinstance(message, str) or not message.strip():
                    raise ValueError("A nonempty Direct request is required")

            def callback(event: dict[str, Any]) -> None:
                owner = read_active_runtime_owner(args.run_dir)
                owned = owner.to_dict() if owner and owner.pid == os.getpid() else {}
                emit(
                    "stream",
                    {
                        **event,
                        "submission_owner": owned,
                        "stream_key": [
                            args.run_dir.name,
                            event.get("component", ""),
                            event.get("attempt", 0),
                            event.get("item_id", ""),
                        ],
                    },
                )

            def parent_disconnected() -> None:
                # Loss of the client pipe requests the same backend stop as a user exit.
                sys.stdin.read()
                # EOF can precede Runtime lease acquisition. Wait for its actual
                # owner instead of losing cancellation in that startup window.
                while not execution_finished.is_set():
                    try:
                        if service.status(args.run_dir).payload.get("owner_active"):
                            subprocess.run(
                                [
                                    sys.executable,
                                    *(["-I"] if sys.flags.isolated else []),
                                    "-m",
                                    "ownpi.tui_bridge.control",
                                    "--config",
                                    str(args.config),
                                    "--app-home",
                                    str(args.app_home),
                                    "--project-root",
                                    str(args.project_root),
                                    "--run-dir",
                                    str(args.run_dir),
                                ],
                                stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL,
                                stderr=sys.stderr,
                                start_new_session=True,
                                check=False,
                            )
                            return
                    except Exception:
                        traceback.print_exc(file=sys.stderr)
                    execution_finished.wait(0.05)

            Thread(
                target=parent_disconnected, daemon=True, name="tui-client-lifetime"
            ).start()
            if args.action == "start":
                session_id, task_id, run_id = service.run_identity(args.run_dir)
                context = PreparedRunContext(session_id, task_id, run_id, args.run_dir)
                raw_message = (args.run_dir / "raw_user_message.txt").read_text(
                    encoding="utf-8"
                )
                result = service.start(
                    raw_user_message=raw_message,
                    run_context=context,
                    runtime_status_callback=callback,
                ).payload
            elif args.action == "direct_submit":
                result = submit_direct(service=service, config=config, paths=paths,
                    run_dir=args.run_dir, project_root=args.project_root,
                    message=message, callback=callback)
            else:
                result = service.resume(args.run_dir, runtime_status_callback=callback).payload
        emit("result", result)
        return 0
    except Exception as error:
        traceback.print_exc(file=sys.stderr)
        emit("error", {"message": str(error), "error_class": type(error).__name__})
        return 1
    finally:
        execution_finished.set()


if __name__ == "__main__":
    raise SystemExit(main())
