from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
from typing import Any, TextIO

from ownpi.app.paths import AppPaths
from ownpi.controller.run_context import PreparedRunContext
from ownpi.controller.runtime_control import RuntimeControlResult, RuntimeControlService
from ownpi.controller.session_manager import RuntimeSessionManager
from ownpi.utils.io import write_text_atomic


def add_runtime_parsers(
    subparsers: argparse._SubParsersAction,
    app_paths: AppPaths,
) -> None:
    """Register the Runtime command surface owned by this module."""
    run_parser = subparsers.add_parser(
        "run",
        help="Start and control an OwnPI Runtime run",
    )
    run_subparsers = run_parser.add_subparsers(dest="run_command", required=True)

    list_parser = run_subparsers.add_parser("list", help="List Runtime runs")
    list_parser.add_argument("--json", action="store_true")
    list_parser.add_argument("--config", default=str(app_paths.config_path))
    _add_project_root_argument(list_parser)

    status_parser = run_subparsers.add_parser('status', help='Show run state and Runtime activity')
    status_parser.add_argument('target', help='Run ID or session-managed run directory')
    status_parser.add_argument('--json', action='store_true')
    status_parser.add_argument('--config', default=str(app_paths.config_path))
    _add_project_root_argument(status_parser)

    for command, help_text in (
        ("resume", "Resume a run from its latest durable recovery point"),
    ):
        command_parser = run_subparsers.add_parser(command, help=help_text)
        command_parser.add_argument("target", help="Run ID or session-managed run directory")
        command_parser.add_argument(
            "--background", action="store_true",
            help="Resume the run in a detached process and return immediately",
        )
        command_parser.add_argument("--json", action="store_true")
        command_parser.add_argument("--config", default=str(app_paths.config_path))
        _add_project_root_argument(command_parser)

    start_parser = run_subparsers.add_parser(
        "start",
        help="Start a run from a raw message",
    )
    start_parser.add_argument("message", nargs="?", help="Raw task message")
    start_parser.add_argument(
        "--message-file",
        help="Read the raw message from a UTF-8 file; use - for stdin",
    )
    start_parser.add_argument(
        "--workspace",
        default=None,
        help="Workspace directory; defaults to the launch directory",
    )
    start_parser.add_argument("--json", action="store_true")
    start_parser.add_argument(
        "--background",
        action="store_true",
        help="Start the run in a detached process and return immediately",
    )
    start_parser.add_argument("--config", default=str(app_paths.config_path))
    _add_project_root_argument(start_parser)

    for action in ("stop",):
        control_parser = run_subparsers.add_parser(
            action,
            help=f"Request Runtime {action}",
        )
        control_parser.add_argument("target", help="Run ID or session-managed run directory")
        control_parser.add_argument("--reason", default="")
        control_parser.add_argument("--json", action="store_true")
        control_parser.add_argument("--config", default=str(app_paths.config_path))
        _add_project_root_argument(control_parser)


def _add_project_root_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--project-root",
        default=None,
        help=argparse.SUPPRESS,
    )


def read_raw_message(
    positional_message: str | None,
    message_file: str | None,
    *,
    stdin: TextIO | None = None,
) -> str:
    """Read exactly one user message without rewriting its content."""
    if positional_message is not None and message_file is not None:
        raise ValueError("provide either a positional message or --message-file, not both")
    if positional_message is not None:
        message = str(positional_message)
    elif message_file is not None:
        if message_file == "-":
            message = (stdin or sys.stdin).read()
        else:
            path = Path(message_file).expanduser()
            if not path.is_file():
                raise FileNotFoundError(f"message file not found: {path.resolve()}")
            with path.open("r", encoding="utf-8", newline="") as handle:
                message = handle.read()
    else:
        raise ValueError("a raw message is required; provide MESSAGE or --message-file")
    if not message.strip():
        raise ValueError("raw message must not be empty")
    return message


def resolve_workspace(raw_workspace: str | None, *, cwd: Path | None = None) -> Path:
    """Resolve, create, and validate the user-facing workspace binding."""
    base = Path(cwd or Path.cwd()).expanduser().resolve()
    candidate = base if raw_workspace is None else Path(raw_workspace).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    candidate = candidate.resolve()
    try:
        candidate.mkdir(parents=True, exist_ok=True)
    except FileExistsError as exc:
        raise ValueError(f"workspace is not a directory: {candidate}") from exc
    except OSError as exc:
        raise PermissionError(f"workspace cannot be created: {candidate}") from exc
    resolved = candidate.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError(f"workspace is not a directory: {resolved}")
    if not os.access(resolved, os.R_OK | os.X_OK):
        raise PermissionError(f"workspace is not accessible: {resolved}")
    return resolved


class RuntimeProgressReporter:
    """Render only coarse stage observations from existing Runtime callbacks."""

    def __init__(self, *, stderr: TextIO | None = None) -> None:
        self.stderr = stderr or sys.stderr
        self._last_stage: str = ""

    def on_runtime_status(self, payload: dict[str, Any]) -> None:
        if str(payload.get("event_kind") or "") != "stage_status":
            return
        operation = str(payload.get("operation") or "")
        if operation not in {"stage_started", "stage_resumed"}:
            return
        stage = str(payload.get("stage") or "unknown")
        if stage == self._last_stage and operation == "stage_started":
            return
        self._last_stage = stage
        print(f"[run] {operation}: {stage}", file=self.stderr, flush=True)


class RuntimeSignalAdapter:
    """Translate foreground Ctrl-C into the canonical stop service."""

    def __init__(
        self,
        *,
        service: RuntimeControlService,
        run_dir: Path,
        stderr: TextIO | None = None,
    ) -> None:
        self.service = service
        self.run_dir = run_dir
        self.stderr = stderr or sys.stderr
        self.stop_count = 0
        self._interrupt_event: threading.Event | None = None
        self._shutdown_event: threading.Event | None = None
        self._worker: threading.Thread | None = None

    def start(self) -> None:
        if self._worker is not None:
            return
        self._interrupt_event = threading.Event()
        self._shutdown_event = threading.Event()
        self._worker = threading.Thread(
            target=self._run,
            name="ownpi-runtime-interrupt",
            daemon=True,
        )
        self._worker.start()

    def close(self) -> None:
        if (
            self._worker is None
            or self._interrupt_event is None
            or self._shutdown_event is None
        ):
            return
        if self._interrupt_event.is_set() and self.stop_count == 0:
            self._worker.join(timeout=5.0)
        self._shutdown_event.set()
        self._interrupt_event.set()
        self._worker.join(timeout=5.0)
        self._worker = None

    def handle_interrupt(self, _signum: int | None = None, _frame: Any = None) -> None:
        if self._interrupt_event is None:
            return
        self._interrupt_event.set()

    def _run(self) -> None:
        interrupt_event = self._interrupt_event
        shutdown_event = self._shutdown_event
        if interrupt_event is None or shutdown_event is None:
            return
        interrupt_event.wait()
        if shutdown_event.is_set():
            return
        self.stop_count = 1
        try:
            result = self.service.stop(
                self.run_dir,
                reason="foreground Ctrl-C",
            )
        except (FileNotFoundError, PermissionError, RuntimeError, ValueError) as exc:
            print(
                f"[run] stop request was not accepted: {exc}",
                file=self.stderr,
                flush=True,
            )
            return
        print(
            f"[run] stop requested: {result.message}",
            file=self.stderr,
            flush=True,
        )


@contextmanager
def _foreground_runtime_signals(
    *, service: RuntimeControlService, run_dir: Path, stderr: TextIO,
) -> Iterator[None]:
    adapter = RuntimeSignalAdapter(service=service, run_dir=run_dir, stderr=stderr)
    can_install = threading_main_thread()
    previous_handler = signal.getsignal(signal.SIGINT) if can_install else None
    adapter.start()
    try:
        if can_install:
            signal.signal(signal.SIGINT, adapter.handle_interrupt)
        yield
    finally:
        if can_install:
            signal.signal(signal.SIGINT, previous_handler)
        adapter.close()


def dispatch_runtime_command(
    args: argparse.Namespace,
    *,
    user_config: Any,
    project_root: Path,
    app_paths: AppPaths,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Dispatch one Runtime command through RuntimeControlService."""
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    service = RuntimeControlService(
        user_config=user_config,
        project_root=project_root,
        app_paths=app_paths,
    )
    command = str(args.run_command)
    if command == "list":
        payload = service.list_runs()
        if bool(args.json):
            _write_json(out, payload)
        else:
            _render_run_list(payload, out=out)
        return 0
    if command == "start":
        return _dispatch_start(
            args,
            service=service,
            user_config=user_config,
            project_root=project_root,
            app_paths=app_paths,
            stdout=out,
            stderr=err,
        )

    target = service.resolve_run_target(args.target)
    if command == 'status':
        result = service.status(target)
        if bool(args.json):
            _write_json(out, result.payload)
        else:
            _render_run_status(result.payload, out=out)
        return 0
    if command == "resume":
        if bool(getattr(args, "background", False)):
            return _dispatch_background_resume(
                args, run_dir=target, project_root=project_root,
                app_paths=app_paths, stdout=out,
            )
        with _foreground_runtime_signals(service=service, run_dir=target, stderr=err):
            result = service.resume(target)
        _render_result(result, as_json=bool(args.json), stdout=out)
        return 0 if result.accepted else 1
    if command == "stop":
        result = service.stop(target, reason=str(args.reason or ""))
        _render_result(result, as_json=bool(args.json), stdout=out)
        return 0 if result.accepted else 1
    raise ValueError(f"unsupported Runtime command: {command}")


def _dispatch_start(
    args: argparse.Namespace,
    *,
    service: RuntimeControlService,
    user_config: Any,
    project_root: Path,
    app_paths: AppPaths,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    background = bool(getattr(args, "background", False))
    raw_message = read_raw_message(args.message, args.message_file)
    project_root = resolve_workspace(args.workspace)
    service = RuntimeControlService(
        user_config=user_config, project_root=project_root, app_paths=app_paths,
    )
    run_context = RuntimeSessionManager(
        app_paths=app_paths, project_root=project_root,
    ).prepare_workflow_run(entry="cli_run")
    run_dir = run_context.run_dir
    from ownpi.schemas.workflow_plan import StageRuntimeProfile, resolve_runtime_profile

    direct = resolve_runtime_profile(
        config_selection=user_config.runtime.default_profile,
    ).resolved is StageRuntimeProfile.TASK_LEVEL
    if direct:
        service.prepare_direct_start(raw_user_message=raw_message, run_context=run_context)
    else:
        write_text_atomic(run_dir / "raw_user_message.txt", raw_message)

    if background:
        return _dispatch_background_start(
            args,
            service=service,
            run_context=run_context,
            app_paths=app_paths,
            project_root=project_root,
            stdout=stdout,
            stderr=stderr,
        )

    reporter = RuntimeProgressReporter(stderr=stderr)
    if bool(args.json):
        _write_json_line(
            stdout,
            {
                "type": "run_started",
                "run_id": run_context.run_id,
                "workspace": str(project_root),
            },
        )
    else:
        print(f"Run started: {run_context.run_id}", file=stdout, flush=True)
        print(f"Workspace: {project_root}", file=stdout, flush=True)
    with _foreground_runtime_signals(service=service, run_dir=run_dir, stderr=stderr):
        result = service.resume(
            run_dir, runtime_status_callback=reporter.on_runtime_status,
        ) if direct else service.start(
            raw_user_message=raw_message,
            run_context=run_context,
            runtime_status_callback=reporter.on_runtime_status,
        )
    if bool(args.json):
        _write_json_line(stdout, result.to_dict())
    else:
        _render_result(result, as_json=False, stdout=stdout)
    return 0


def _dispatch_background_start(
    args: argparse.Namespace,
    *,
    service: RuntimeControlService,
    run_context: PreparedRunContext,
    app_paths: AppPaths,
    project_root: Path,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Start the existing Runtime path in a detached CLI child process."""
    run_dir = run_context.run_dir
    command = [
        sys.executable,
        "-m",
        "ownpi.cli.runtime_start_worker",
        "--run-dir",
        str(run_dir),
        "--project-root",
        str(project_root),
        "--config",
        str(Path(args.config).expanduser().resolve()),
        "--app-home",
        str(app_paths.app_home.resolve()),
    ]
    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"
    try:
        process = _spawn_detached_runtime(
            command=command,
            cwd=project_root,
            environment=environment,
        )
    except OSError as exc:
        service.record_start_failure(run_context, error=exc)
        raise RuntimeError(f"could not start background Runtime process: {exc}") from exc

    payload = {
        "type": "run_started",
        "run_id": run_context.run_id,
        "workspace": str(project_root),
        "background": True,
        "pid": process.pid,
    }
    if bool(getattr(args, "json", False)):
        _write_json_line(stdout, payload)
    else:
        print(f"Run started in background: {run_context.run_id}", file=stdout, flush=True)
        print(f"PID: {process.pid}", file=stdout, flush=True)
        print(f"Workspace: {project_root}", file=stdout, flush=True)
    return 0


def _dispatch_background_resume(
    args: argparse.Namespace,
    *,
    run_dir: Path,
    project_root: Path,
    app_paths: AppPaths,
    stdout: TextIO,
) -> int:
    command = [
        sys.executable, "-m", "ownpi.cli.runtime_resume_worker",
        "--run-dir", str(run_dir),
        "--project-root", str(project_root),
        "--config", str(Path(args.config).expanduser().resolve()),
        "--app-home", str(app_paths.app_home.resolve()),
    ]
    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"
    try:
        process = _spawn_detached_runtime(
            command=command, cwd=project_root, environment=environment,
        )
    except OSError as exc:
        raise RuntimeError(f"could not start background resume process: {exc}") from exc
    payload = {
        "type": "run_resume_started", "run_id": run_dir.name,
        "background": True, "pid": process.pid,
    }
    if bool(args.json):
        _write_json_line(stdout, payload)
    else:
        print(f"Run resume started in background: {run_dir.name}", file=stdout, flush=True)
        print(f"PID: {process.pid}", file=stdout, flush=True)
    return 0


def _spawn_detached_runtime(
    *,
    command: list[str],
    cwd: Path,
    environment: dict[str, str],
) -> subprocess.Popen[Any]:
    return subprocess.Popen(
        command,
        cwd=str(cwd),
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )


def threading_main_thread() -> bool:
    """Keep signal installation testable."""
    return threading.current_thread() is threading.main_thread()


def _render_result(
    result: RuntimeControlResult,
    *,
    as_json: bool,
    stdout: TextIO,
) -> None:
    if as_json:
        _write_json(stdout, result.to_dict())
        return
    print(f"Action: {result.action}", file=stdout)
    print(f"Run: {result.run_id}", file=stdout)
    print(f"Disposition: {result.disposition}", file=stdout)
    if result.payload.get("status"):
        print(f"State: {result.payload['status']}", file=stdout)
    if result.message:
        print(f"Message: {result.message}", file=stdout)
    answer = result.payload.get('run_summary', {}).get('direct_answer')
    if answer:
        print(answer, file=stdout)


def _render_run_status(payload: dict[str, Any], *, out: TextIO) -> None:
    print(f"Run: {payload['run_id']}", file=out)
    print(f"State: {payload['status']}", file=out)
    print(f"Recorded state: {payload['recorded_status']}", file=out)
    print(f"Stage: {payload['current_stage'] or 'not started'}", file=out)
    if payload['stage_attempt'] is not None:
        print(f"Stage attempt: {payload['stage_attempt']}", file=out)
    print(f"Runtime active: {'yes' if payload['owner_active'] else 'no'}", file=out)
    if payload['owner_pid'] is not None:
        print(f"Runtime PID: {payload['owner_pid']}", file=out)
    print(f"Last recorded activity: {payload['last_recorded_at'] or 'none'}", file=out)
    print(f"Run directory: {payload['run_dir']}", file=out)
    if payload['message']:
        print(payload['message'], file=out)
    for warning in payload['warnings']:
        print(f'Warning: {warning}', file=out)


def _render_run_list(payload: list[dict[str, Any]], *, out: TextIO) -> None:
    if not payload:
        print("No Runtime runs.", file=out)
        return
    for run in payload:
        print(f"Run {run.get('run_id', '<unknown>')}", file=out)
        print(f"  run_dir: {run.get('run_dir', '<unknown>')}", file=out)


def _write_json(stream: TextIO, payload: Any) -> None:
    stream.write(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n")
    stream.flush()


def _write_json_line(stream: TextIO, payload: Any) -> None:
    stream.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    stream.flush()


__all__ = [
    "RuntimeProgressReporter",
    "RuntimeSignalAdapter",
    "add_runtime_parsers",
    "dispatch_runtime_command",
    "read_raw_message",
    "resolve_workspace",
]
