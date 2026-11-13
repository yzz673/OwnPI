"""Single presentation-state owner; backend services own execution and persistence."""

from __future__ import annotations

import argparse
import json
import logging
import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from typing import Any, TextIO

from ownpi.app.paths import AppPaths
from ownpi.config.env_loader import load_dotenv
from ownpi.config.loader import load_user_config
from ownpi.controller.pending_interactions import pending_permission_interactions
from ownpi.controller.runtime_active_owner import read_active_runtime_owner
from ownpi.controller.runtime_control import RuntimeControlService
from ownpi.controller.session_manager import RuntimeSessionManager
from ownpi.runtime.rollout.business_reader import read_business_events
from ownpi.runtime.rollout.interaction_events import project_interactions
from ownpi.runtime.rollout.run_events import run_events_path
from ownpi.tui_bridge import panels
from ownpi.tui_bridge.approval_receipts import bind_approval_receipts
from ownpi.tui_bridge.commands import CATALOG, Configuration
from ownpi.tui_bridge.exit_display import unexpected_exit_item
from ownpi.tui_bridge.facts import outcome_payload, reply_activities, resume_command
from ownpi.tui_bridge.lifecycle import (
    recovery_availability,
    run_capabilities,
    uses_direct,
)
from ownpi.tui_bridge.outcome_display import merge_outcome
from ownpi.tui_bridge.projection import CanonicalProjector
from ownpi.tui_bridge.protocol import Command, EventWriter
from ownpi.tui_bridge.stream_delivery import StreamDelivery
from ownpi.tui_bridge.subagents import enrich_batches
from ownpi.tui_bridge.submissions import Submissions
from ownpi.tui_bridge.view_health import ViewHealth
from ownpi.tui_bridge.workspace import select_workspace
from ownpi.utils.io import write_text_atomic

LOG = logging.getLogger(__name__)


class Bridge:
    def __init__(
        self,
        *,
        config_path: Path,
        project_root: Path,
        paths: AppPaths,
        output: TextIO,
        queue: Queue | None = None,
    ) -> None:
        self.paths = paths
        self.config_path = config_path.resolve()
        self.project_root = project_root.resolve()
        self.settings = Configuration(self.config_path, paths, self.project_root)
        self.manager = RuntimeSessionManager(
            app_paths=paths, project_root=self.project_root
        )
        self.writer = EventWriter(output)
        self.projector = CanonicalProjector()
        self.queue: Queue = queue if queue is not None else Queue(maxsize=1024)
        self.executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="tui-control"
        )
        self.worker: subprocess.Popen | None = None
        self.worker_request = ""
        self.worker_log: TextIO | None = None
        self.run_dir: Path | None = None
        self.session_id: str | None = None
        self.requests: dict[str, tuple[str, dict[str, Any] | None]] = {}
        self.last_snapshot = ""
        self.view_health = ViewHealth()
        self.next_refresh_at = time.monotonic()
        self.backend_replies: dict[str, dict[str, Any]] = {}
        # Transient presentation of accepted control replies, never a persisted ledger.
        self.approval_receipts: dict[str, dict[str, dict[str, Any]]] = {}
        self.shutdown_request: str | None = None
        self.shutdown_deadline = 0.0
        self.shutdown_announced = False
        self.stop_inflight = False
        self.stopped_for_shutdown = False
        self.closed = False
        self.disconnected = False
        self.initialized = False
        self.controls_inflight: set[str] = set()
        self.allow_unrecoverable_exit = False
        self.menu_contexts: dict[str, dict[str, Any]] = {}
        self.submissions = Submissions()
        self.execution_permissions: str | None = None
        self.stream_delivery = StreamDelivery()

    def service(self) -> RuntimeControlService:
        return RuntimeControlService(
            user_config=self.settings.config,
            project_root=self.project_root,
            app_paths=self.paths,
        )

    def identity(self) -> dict[str, str]:
        if self.run_dir is None:
            return {"session_id": self.session_id or ""}
        session, task, run = RuntimeControlService.run_identity(self.run_dir)
        return {
            "session_id": session,
            "task_id": task,
            "run_id": run,
            "run_dir": str(self.run_dir),
        }

    def emit(self, kind: str, data: dict[str, Any], request_id: str = "") -> None:
        if self.disconnected:
            return
        if kind == "panel" and request_id in self.menu_contexts:
            context = self.menu_contexts[request_id]
            data = {
                "navigation": context.get("menu_navigation", "root"),
                "source_view": context.get("source_view", ""),
                "source_epoch": context.get("source_epoch"),
                **data,
            }
        try:
            self.writer.emit(
                kind, data, request_id=request_id, identity=self.identity()
            )
        except (BrokenPipeError, OSError):
            self.disconnected = True
            self.begin_shutdown("disconnect")

    def reply(
        self,
        request: str,
        *,
        ok: bool = True,
        disposition: str = "completed",
        **data: Any,
    ) -> None:
        result = {"ok": ok, "disposition": disposition, **data}
        if not ok and disposition == "rejected":
            self.submissions.reject(request)
        if request in self.requests:
            fingerprint, _ = self.requests[request]
            self.requests[request] = fingerprint, result
        self.emit("result", result, request)
        if disposition != "pending":
            self.menu_contexts.pop(request, None)

    def dispatch(self, command: Command) -> None:
        previous = self.requests.get(command.request_id)
        if previous:
            if previous[0] != command.fingerprint():
                self.emit(
                    "result",
                    {
                        "ok": False,
                        "disposition": "rejected",
                        "message": "request_id reused with different content",
                    },
                    command.request_id,
                )
            elif previous[1] is not None:
                self.emit("result", previous[1], command.request_id)
            else:
                self.emit(
                    "result", {"ok": True, "disposition": "pending"}, command.request_id
                )
            return
        if len(self.requests) >= 10000:
            self.emit(
                "result",
                {
                    "ok": False,
                    "disposition": "rejected",
                    "message": "Reconnect to continue",
                },
                command.request_id,
            )
            return
        self.requests[command.request_id] = command.fingerprint(), None
        try:
            if not self.initialized and command.method != "initialize":
                raise ValueError("Initialize the TUI protocol first")
            if self.shutdown_request and command.method not in {
                "inspect",
                "stop",
                "shutdown",
            }:
                raise ValueError("Stopping before exit; new work is disabled")
            self._dispatch(command)
        except Exception as error:
            LOG.exception("TUI command %s failed", command.method)
            if command.method == "submit":
                self.refresh(force=True)
            self.reply(
                command.request_id,
                ok=False,
                disposition="rejected",
                message=str(error),
                error_class=type(error).__name__,
            )

    def _dispatch(self, command: Command) -> None:
        method, params, request = command.method, command.params, command.request_id
        if method in {"command", "configure"}:
            self.menu_contexts[request] = params
        if method == "initialize":
            self.initialized = True
            self.emit(
                "bootstrap",
                {
                    "config": self.settings.public(),
                    "skills": self.settings.skills(),
                    "commands": [
                        {"name": name, "description": description}
                        for name, description in CATALOG
                    ],
                },
            )
            self.reply(request)
            self.refresh(force=True)
        elif method == "submit":
            self.require_idle()
            text = params.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("A nonempty request is required")
            self.settings.config = load_user_config(self.config_path)
            direct = uses_direct(self.settings.config)
            capability = run_capabilities(self.run_dir) if self.run_dir else {}
            if capability.get("mode") == "direct":
                if not capability.get("can_submit"):
                    raise ValueError("This request is unfinished. Answer its question, resume it, or use /clear for a new session.")
                self.submissions.begin(request, self.run_dir.name, self.projector.read_run(self.run_dir).items)
                self.start_worker("direct_submit", request, text=text)
            else:
                context = self.manager.prepare_workflow_run(session_id=self.session_id, entry="tui")
                self.run_dir, self.session_id = context.run_dir, context.session_id
                self.submissions.begin(request, self.run_dir.name, [], owned=True)
                if direct:
                    self.service().prepare_direct_start(raw_user_message=text, run_context=context)
                    self.start_worker("resume", request)
                else:
                    write_text_atomic(context.run_dir / "raw_user_message.txt", text)
                    self.start_worker("start", request)
        elif method == "resume":
            self.require_idle()
            target = self.service().resolve_run_target(str(params.get("target") or ""))
            status = self.service().status(target).payload
            if status.get("owner_active"):
                raise ValueError("This run already has an active execution owner. Open history to observe it.")
            recovery = run_capabilities(target)
            if not recovery["available"] and status["status"] != "completed":
                raise ValueError("Backend recovery unavailable: " + recovery["reason"])
            self.select_run(str(params.get("target") or ""))
            if recovery.get("can_submit"):
                self.reply(request, message="Conversation restored. Enter your next message; no execution started.")
            elif status["status"] == "completed":
                self.reply(request, message="Opened completed run history; no execution started")
            else:
                self.start_worker("resume", request)
        elif method == "inspect":
            target = params.get("target")
            if target:
                self.require_idle()
                self.select_run(str(target))
            self.refresh(force=True)
            self.reply(request)
        elif method == "new_session":
            self.require_idle()
            self.run_dir, self.session_id = None, None
            self.last_snapshot = ""
            self.refresh(force=True)
            self.reply(request)
        elif method == "stop":
            if self.run_dir is None:
                raise ValueError("There is no active run")
            self.async_call(
                request,
                "stop",
                lambda: self.service().stop(self.run_dir, reason="TUI stop").to_dict(),
            )
        elif method == "answer":
            self.require_idle()
            if self.run_dir is None or not params.get("interaction_id"):
                raise ValueError("A current question identity is required")
            self.async_call(
                request,
                "answer",
                lambda: (
                    self.service()
                    .answer(
                        self.run_dir,
                        answers=params.get("answers"),
                        session_id=params["interaction_id"],
                    )
                    .to_dict()
                ),
                params=params,
            )
        elif method == "permission":
            if self.run_dir is None or not params.get("interaction_id"):
                raise ValueError("A current permission identity is required")
            pending = next((value for value in pending_permission_interactions(self.run_dir)
                            if value["session_id"] == params["interaction_id"]), None)
            if pending is None:
                raise ValueError("Request is no longer pending; local draft retained.")
            params = {**params, "approval_request": pending, "run_id": self.run_dir.name}
            self.async_call(
                request,
                "permission",
                lambda: (
                    self.service()
                    .answer_tool_permission(
                        self.run_dir,
                        decision=str(params.get("decision", "")),
                        scope=str(params.get("scope", "once")),
                        session_id=str(params["interaction_id"]),
                    )
                    .to_dict()
                ),
                params=params,
            )
        elif method == "configure":
            # Running engines keep their current immutable config; the next execution reloads.
            result = self.settings.update(
                str(params.get("field", "")), params.get("value")
            )
            self.emit("configuration", result)
            self.reply(request, **result)
        elif method == "command":
            native = params.get("skill_action")
            if native is not None:
                self.require_idle()
                self.async_call(
                    request,
                    "native_skill",
                    lambda: self.settings.skill_action(native),
                    params=params,
                )
            else:
                self.command(
                    str(params.get("text", "")),
                    request,
                    context=params.get("menu_context"),
                )
        elif method == "shutdown":
            self.allow_unrecoverable_exit = params.get("allow_unrecoverable") is True
            self.begin_shutdown(request)

    def require_idle(self) -> None:
        if self.controls_inflight:
            raise ValueError("Wait for the pending backend action before changing runs")
        if self.worker is not None:
            raise ValueError(
                "A run is active. Stop it before starting or resuming another run."
            )
        if self.run_dir is not None and self.service().status(self.run_dir).payload.get(
            "owner_active"
        ):
            raise ValueError("The backend has an active owner for this run")

    def select_run(self, target: str) -> None:
        run_dir = self.service().resolve_run_target(target)
        session_id, _, _ = self.service().run_identity(run_dir)
        session = self.manager.store.load_session(session_id)
        root = Path(session.project_root).expanduser().resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"The run's workspace is unavailable: {root}")
        self.run_dir, self.session_id, self.project_root = run_dir, session_id, root
        self.settings.project_root = root
        self.manager = RuntimeSessionManager(app_paths=self.paths, project_root=root)
        self.emit("configuration", {"config": self.settings.public()})
        self.refresh(force=True)

    def start_worker(self, action: str, request: str, *, text: str | None = None) -> None:
        assert self.run_dir is not None
        self.settings.config = load_user_config(self.config_path)
        log_dir = self.paths.app_home / "diagnostics" / "tui"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.worker_log = (log_dir / f"{self.run_dir.name}.log").open(
            "a", encoding="utf-8"
        )
        command = [
            sys.executable,
            *(["-I"] if sys.flags.isolated else []),
            "-m",
            "ownpi.tui_bridge.worker",
            "--action",
            action,
            "--config",
            str(self.config_path),
            "--app-home",
            str(self.paths.app_home),
            "--project-root",
            str(self.project_root),
            "--run-dir",
            str(self.run_dir),
        ]
        try:
            self.worker = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self.worker_log,
                text=True,
                encoding="utf-8",
                cwd=self.project_root,
                start_new_session=True,
            )
        except OSError as error:
            self.worker_log.close()
            self.worker_log = None
            if action == "start":
                from ownpi.controller.run_context import PreparedRunContext

                identity = self.identity()
                self.service().record_start_failure(
                    PreparedRunContext(
                        identity["session_id"],
                        identity["task_id"],
                        identity["run_id"],
                        self.run_dir,
                    ),
                    error=error,
                )
            raise
        self.worker_request = request
        self.execution_permissions = None
        process = self.worker
        if action == "direct_submit":
            assert process.stdin is not None
            process.stdin.write(json.dumps({"text": text}, ensure_ascii=False) + "\n")
            process.stdin.flush()

        def read_worker() -> None:
            assert process.stdout is not None
            try:
                for line in process.stdout:
                    value = json.loads(line)
                    if not isinstance(value, dict) or value.get("type") not in {
                        "stream",
                        "execution_config",
                        "result",
                        "error",
                    }:
                        raise ValueError("Invalid execution worker event")
                    self.queue.put(("worker", value))
            except Exception as error:
                self.queue.put(
                    ("worker", {"type": "error", "data": {"message": str(error)}})
                )
            finally:
                process.stdout.close()
                self.queue.put(("worker_exit", process.wait()))

        Thread(target=read_worker, daemon=True, name="tui-worker-reader").start()
        self.emit("execution", {"state": "starting", "action": action}, request)
        self.refresh(force=True)

    def async_call(
        self,
        request: str,
        action: str,
        function: Any,
        *,
        params: dict[str, Any] | None = None,
    ) -> None:
        self.controls_inflight.add(request or action)
        self.emit(
            "result", {"ok": True, "disposition": "pending", "action": action}, request
        )

        def execute() -> None:
            try:
                result = function()
                self.queue.put(
                    ("control", (request, action, params or {}, result, None))
                )
            except Exception as error:
                self.queue.put(
                    ("control", (request, action, params or {}, None, error))
                )

        self.executor.submit(execute)

    def control_result(self, value: tuple) -> None:
        request, action, params, result, error = value
        self.controls_inflight.discard(request or action)
        if action == "shutdown_stop":
            self.stop_inflight = False
            self.stopped_for_shutdown = error is None and bool(result.get("accepted"))
            if error:
                LOG.error("Backend stop failed during exit: %s", error)
            return
        if error is not None:
            # An accepted answer is durable even when presentation failed afterwards.
            accepted = bool(getattr(error, "accepted", False))
            if action == "answer" and self.run_dir:
                try:
                    facts = project_interactions(
                        read_business_events(run_events_path(self.run_dir)).events
                    )
                    accepted = accepted or bool(
                        facts.get(params.get("interaction_id"))
                        and facts[params["interaction_id"]].answer_event_id
                    )
                except (OSError, ValueError):
                    pass
            if not accepted:
                self.reply(
                    request,
                    ok=False,
                    disposition="rejected",
                    message=str(error),
                    action=action,
                )
                self.refresh(force=True)
                return
            self.emit(
                "notice", {"message": str(error), "answer_accepted": True}, request
            )
            # Preserve the accepted state, but do not contend with an unreleased writer.
            self.reply(
                request,
                action=action,
                answer_accepted=True,
                message="Answer accepted. Inspect the run before resuming.",
            )
            self.refresh(force=True)
            return
        if action == "native_skill":
            self.emit("skills", {"skills": self.settings.skills()})
            self.reply(request, message="Skill catalog updated.")
            return
        if action == "skill":
            self.emit("panel", result, request)
            self.emit("skills", {"skills": self.settings.skills()})
            self.reply(request)
            return
        if action == "permission" and result.get("accepted"):
            summary = (result.get("payload") or {}).get("run_summary") or {}
            original = params.get("approval_request")
            if (original and summary.get("permission_session") == original["session_id"]
                    and summary.get("decision") == params.get("decision")):
                self.approval_receipts.setdefault(params["run_id"], {})[original["session_id"]] = {
                    **original, "decision": summary["decision"],
                    "decision_scope": params.get("scope", "once"),
                }
            self.emit(
                "interaction_accepted",
                {"interaction_id": params["interaction_id"]},
                request,
            )
        self.refresh(force=True)
        if action == "answer":
            if not result.get("accepted"):
                self.reply(
                    request,
                    ok=False,
                    disposition="rejected",
                    backend=result,
                    message=result.get("message", "Answer was not accepted"),
                )
                return
            self.emit(
                "interaction_accepted",
                {"interaction_id": params["interaction_id"]},
                request,
            )
            try:
                self.start_worker("resume", request)
            except Exception as error:
                self.reply(
                    request,
                    ok=False,
                    answer_accepted=True,
                    message=f"Answer accepted, but execution could not start: {error}",
                )
        else:
            accepted = bool(result.get("accepted", True))
            message = result.get("message", "")
            if action == "stop" and accepted:
                message += " Background commands may still run; inspect /ps."
            self.reply(
                request,
                action=action,
                backend=result,
                ok=accepted,
                disposition="completed" if accepted else "rejected",
                message=message,
            )

    def command(
        self,
        text: str,
        request: str,
        *,
        context: dict | None = None,
        presentation: dict | None = None,
        reply: bool = True,
    ) -> None:
        tokens = shlex.split(text)
        if not tokens:
            raise ValueError("A command is required")
        name = tokens[0].removeprefix("/")

        def show(panel: dict) -> None:
            self.emit("panel", {**panel, **(presentation or {})}, request)

        if name in {"direct", "research"}:
            if len(tokens) != 1:
                raise ValueError(f"Use /{name} without arguments; /runtime opens the level picker.")
            selected = "task" if name == "direct" else "research"
            result = self.settings.update("default_profile", selected)
            self.emit("configuration", result)
            self.emit("notice", {"message": f"{name.title()} selected for new tasks. Existing runs keep their original level."})
            self.command("/runtime", request, reply=False)
        elif name == "project":
            if len(tokens) == 1:
                show(panels.document("Project workspace", f"Current directory: `{self.project_root}`\n\n"
                    "New chats share this project's files.\n\n"
                    '- Open existing: `/project open "/path/to/project"`\n'
                    '- Create empty: `/project new "/path/to/new-project"`\n'
                    "\nStop active work before switching projects."))
            elif len(tokens) == 3 and tokens[1] in {"open", "new"}:
                self.require_idle()
                candidate = Path(tokens[2]).expanduser()
                if not candidate.is_absolute():
                    candidate = self.project_root / candidate
                root = select_workspace(candidate, create=tokens[1] == "new")
                self.project_root = root
                self.settings.project_root = root
                self.manager = RuntimeSessionManager(app_paths=self.paths, project_root=root)
                self.run_dir, self.session_id = None, None
                self.last_snapshot = ""
                self.emit("configuration", {"config": self.settings.public()})
                self.refresh(force=True)
                self.emit("notice", {"message": f"Project opened: {root}"})
            else:
                raise ValueError('Use /project, /project open "PATH", or /project new "PATH"')
        elif name in {"runtime", "runtime_profile"}:
            if len(tokens) != 1:
                raise ValueError("Use /runtime to view and change the execution level.")
            self.settings.config = load_user_config(self.config_path)
            current = run_capabilities(self.run_dir).get("mode", "unknown") if self.run_dir else "none"
            show(self.settings.panel("runtime", {"current_mode": current}))
        elif name in {"subagents", "requests"}:
            self.emit("panel", {"presenter": name, "title": name.title()}, request)
        elif name in {
            "settings",
            "model",
            "model_all",
            "model_profiles",
            "model_reasoning",
            "model_advanced",
            "reasoning",
            "permissions_confirm",
            "permissions",
            "appearance",
            "compaction",
            "review_profile",
        }:
            show(
                self.settings.panel("theme" if name == "appearance" else name, context)
            )
        elif name == "compaction_field" and len(tokens) == 2:
            show(self.settings.panel("compaction_field:" + tokens[1]))
        elif name == "skills":
            if len(tokens) > 1:
                self.require_idle()
                self.async_call(
                    request, "skill", lambda: self.settings.skill_command(tokens)
                )
                return
            self.emit("panel", self.settings.skill_command(tokens), request)
        elif name == "resume" and len(tokens) > 1:
            self._dispatch(Command(request, "resume", {"target": tokens[1]}))
            return
        elif name in {"inspect_run", "resolve_run"} and len(tokens) == 2:
            self._dispatch(Command(request, "inspect", {"target": tokens[1]}))
            return
        elif name in {"runs", "resume", "run_actions", "run_inspect", "run_context"}:
            self.emit("panel", panels.run_panel(self, tokens), request)
        elif name == "status":
            status = (
                self.service().status(self.run_dir).payload
                if self.run_dir
                else {"status": "idle"}
            )
            config = self.settings.public()
            body = "\n".join(
                f"- **{key.replace('_', ' ').title()}:** {config[key]}"
                for key in (
                    "model",
                    "provider",
                    "reasoning_effort",
                    "permissions",
                    "runtime_profile",
                    "review_profile",
                    "project_root",
                )
            )
            body += "\n\n## Runtime\n```json\n" + json.dumps(status, indent=2) + "\n```"
            self.emit("panel", panels.document("Status", body), request)
        elif name == "audit":
            self.emit("panel", panels.audit_panel(self), request)
        elif name == "ps":
            self.emit("panel", panels.process_panel(self, tokens), request)
        elif name in {"stop", "stop_run"}:
            self._dispatch(Command(request, "stop", {}))
            return
        elif name == "memory":
            self.emit("panel", panels.memory_panel(self, tokens), request)
        elif name == "extensions":
            show(panels.extensions_panel(self, tokens))
        elif name == "commands":
            self.emit(
                "panel",
                {
                    "presenter": "commands",
                    "commands": [
                        {"name": name, "description": description}
                        for name, description in CATALOG
                    ],
                },
                request,
            )
        else:
            raise ValueError(f"Unknown or unsupported command: /{name}")
        if reply:
            self.reply(request)

    def refresh(self, *, force: bool = False) -> None:
        try:
            snapshot = self.build_snapshot()
            encoded = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, allow_nan=False)
        except (OSError, ValueError, KeyError, TypeError) as error:
            observation = self.view_health.failed(error)
            if observation:
                self.emit("view_health", observation)
            return
        recovered = self.view_health.succeeded()
        if recovered:
            self.emit("view_health", recovered)
        if force or recovered or encoded != self.last_snapshot:
            self.emit("snapshot", snapshot)
            self.last_snapshot = encoded

    def build_snapshot(self) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        warnings: list[str] = []
        usage: dict[str, Any] = {}
        pending: list[dict[str, Any]] = []
        subagents: list[dict[str, Any]] = []
        activity: dict[str, Any] = {}
        status: dict[str, Any] = {"status": "idle", "owner_active": False}
        if self.run_dir:
            status = self.service().status(self.run_dir).payload
            if self.worker is not None and status.get("owner_active"):
                owner = read_active_runtime_owner(self.run_dir)
                if owner is not None and owner.run_id == self.run_dir.name and owner.pid == getattr(self.worker, "pid", None):
                    self.submissions.confirm_owner(self.worker_request, owner.started_at)
            session = self.manager.store.load_session(self.session_id)
            for task in sorted(
                session.tasks, key=lambda task: (task.created_at, task.task_id)
            ):
                for run in sorted(
                    task.runs, key=lambda run: (run.created_at, run.run_id)
                ):
                    projected = self.projector.read_run(Path(run.run_dir))
                    activities = (
                        reply_activities(
                            run.run_id, self.backend_replies[run.run_id]
                        )
                        if run.run_id in self.backend_replies
                        else []
                    )
                    run_items, approval_warnings = bind_approval_receipts(
                        projected.items,
                        list(self.approval_receipts.get(run.run_id, {}).values()),
                        Path(run.run_dir), read_session=self.projector.reader,
                    )
                    warnings.extend(approval_warnings)
                    has_terminal = False
                    for item in run_items:
                        if item["kind"] == "task_outcome":
                            items.extend(activities)
                            has_terminal = True
                        if (
                            item["kind"] == "task_outcome"
                            and run.run_id in self.backend_replies
                            and (not item["payload"].get("request_index") or
                                 item["payload"]["request_index"] == self.backend_replies[run.run_id].get("run_summary", {}).get("request_index"))
                        ):
                            item = {
                                **item,
                                "payload": merge_outcome(
                                    item["payload"],
                                    outcome_payload(
                                        item["payload"]["outcome"],
                                        self.backend_replies[run.run_id],
                                    ),
                                ),
                            }
                        items.append(item)
                    run_path = Path(run.run_dir)
                    run_pending = self.projector.interactions(run_path)
                    run_state = (
                        status
                        if run_path.resolve() == self.run_dir.resolve()
                        else self.service().status(run_path).payload
                    )
                    current_run = run_path.resolve() == self.run_dir.resolve()
                    if not (current_run and self.worker is not None):
                        observed_exit = unexpected_exit_item(run_path, run_state)
                        if observed_exit is not None:
                            items.append(observed_exit)
                            if current_run:
                                # Copy status for presentation only; never rewrite
                                # Runtime state or its recovery decisions.
                                status = {**status, "message": observed_exit["payload"]["failure_summary"]}
                    subagents.extend(
                        enrich_batches(
                            projected.subagents,
                            run_pending,
                            bool(run_state.get("owner_active")),
                            str(run_state.get("status") or "unknown"),
                        )
                    )
                    warnings.extend(projected.warnings)
                    if not has_terminal:
                        items.extend(activities)
                    if Path(run.run_dir).resolve() == self.run_dir.resolve():
                        usage = projected.usage
                        activity = projected.activity
            pending = self.projector.interactions(self.run_dir)
        active_executions = [
            record
            for record in panels.executions(self)
            if record.status not in {"terminal_success", "terminal_failure"}
        ]
        terminal = next((item for item in reversed(items)
                         if item["kind"] == "task_outcome"
                         and item["payload"].get("task_id") == status.get("run_id")
                         and item["payload"].get("outcome") == status.get("status")), None)
        return {
            "background_terminal_count": len(active_executions),
            "submission_receipts": self.submissions.receipts(items),
            "execution_permissions": self.execution_permissions if self.worker is not None else None,
            "items": items,
            "subagents": subagents,
            "status": status,
            "activity": activity,
            "capabilities": run_capabilities(self.run_dir) if self.run_dir else {},
            "interactions": pending,
            "warnings": warnings,
            "usage": usage,
            "worker_active": self.worker is not None,
            "timing": ({"run_id": status["run_id"], "elapsed_ms": terminal["payload"].get("elapsed_ms")}
                       if terminal and not status.get("owner_active") and self.worker is None else {}),
        }

    def begin_shutdown(self, request: str) -> None:
        if self.shutdown_request is None:
            self.shutdown_request = request
            self.shutdown_deadline = time.monotonic() + 30
            self.shutdown_announced = False

    def check_shutdown(self) -> None:
        if self.shutdown_request is None:
            return
        status = (
            self.service().status(self.run_dir).payload
            if self.run_dir
            else {"status": "idle", "owner_active": False}
        )
        if (self.worker is not None or status["owner_active"]) and not self.shutdown_announced:
            self.shutdown_announced = True
            self.emit(
                "execution",
                {"state": "stopping", "message": "Stopping active work before exit…"},
                self.shutdown_request,
            )
        if (
            status["owner_active"]
            and not self.stop_inflight
            and not self.stopped_for_shutdown
        ):
            self.stop_inflight = True
            self.async_call(
                "",
                "shutdown_stop",
                lambda: self.service().stop(self.run_dir, reason="TUI exit").to_dict(),
            )
        elif self.worker is None and not status["owner_active"]:
            if self.stop_inflight:
                return  # The backend may still be recording its confirmed stop.
            if (
                status["status"] == "unexpected_exit"
                and not self.allow_unrecoverable_exit
                and not self.disconnected
            ):
                self.reply(
                    self.shutdown_request,
                    ok=False,
                    disposition="rejected",
                    status=status,
                    can_exit_without_resume=True,
                    recovery_unconfirmed=True,
                    message="Execution ended unexpectedly. Records remain, but completion is unconfirmed.",
                )
                self.shutdown_request = None
                return
            if status["status"] in {
                "idle",
                "completed",
                "failed",
                "stopped",
                "paused_for_human",
                "cancelled",
                "unexpected_exit",
            }:
                recovery = (
                    recovery_availability(self.run_dir)
                    if self.run_dir and status["status"] != "completed"
                    else {"available": False, "reason": ""}
                )
                if (
                    self.run_dir
                    and status["status"] != "completed"
                    and not recovery["available"]
                    and not self.allow_unrecoverable_exit
                    and not self.disconnected
                ):
                    self.reply(
                        self.shutdown_request,
                        ok=False,
                        disposition="rejected",
                        status=status,
                        can_exit_without_resume=True,
                        message="The run stopped, but this backend phase has no recovery entry: "
                        + recovery["reason"],
                    )
                    self.shutdown_request = None
                    return
                self.reply(
                    self.shutdown_request,
                    status=status,
                    shutdown=True,
                    recovery_reason=recovery["reason"],
                    resume_command=(
                        resume_command(
                            self.config_path,
                            self.paths.app_home,
                            self.project_root,
                            self.run_dir.name,
                        )
                        if self.run_dir
                        and (recovery["available"] or status["status"] == "completed")
                        else ""
                    ),
                )
                self.closed = True
                return
        if time.monotonic() > self.shutdown_deadline:
            self.reply(
                self.shutdown_request,
                ok=False,
                disposition="rejected",
                message="Backend stop or recovery boundary is unconfirmed; the UI remains open.",
                status=status,
            )
            self.shutdown_request = None
            if self.disconnected:
                self.closed = True

    def handle(self, kind: str, value: Any) -> None:
        if kind == "command":
            self.dispatch(value)
        elif kind == "invalid":
            self.emit("notice", {"message": value})
        elif kind == "eof":
            self.disconnected = True
            self.begin_shutdown("disconnect")
        elif kind == "control":
            self.control_result(value)
        elif kind == "worker":
            if value["type"] == "execution_config":
                self.execution_permissions = value["data"].get("permissions")
                self.refresh(force=True)
            elif value["type"] == "stream":
                data = value["data"]
                owner = data.get("submission_owner") or {}
                if (self.run_dir and owner.get("run_id") == self.run_dir.name
                        and owner.get("pid") == getattr(self.worker, "pid", None)
                        and owner.get("started_at")):
                    self.submissions.confirm_owner(self.worker_request, owner["started_at"])
                self.stream_delivery.select_run(self.run_dir)
                self.stream_delivery.offer(data, self.worker_request, self.projector.present_stream, self.emit)
            elif value["type"] == "error":
                self.reply(
                    self.worker_request,
                    ok=False,
                    message=value["data"].get("message", "Execution failed"),
                )
            elif value["type"] == "result":
                if self.run_dir:
                    self.backend_replies[self.run_dir.name] = value["data"]
                self.emit("execution_result", value["data"], self.worker_request)
        elif kind == "worker_exit":
            if self.worker and self.worker.stdin:
                self.worker.stdin.close()
            self.worker = None
            if self.worker_log:
                self.worker_log.close()
                self.worker_log = None
            self.refresh(force=True)
            previous = self.requests.get(self.worker_request)
            if previous and previous[1] is None:
                self.reply(self.worker_request, ok=value == 0, returncode=value)
            self.submissions.reject(self.worker_request)
            self.worker_request = ""

    def poll(self) -> None:
        """Keep canonical state current even while worker callbacks fill the queue."""
        timeout = max(0.0, min(0.15, self.next_refresh_at - time.monotonic()))
        try:
            kind, value = self.queue.get(timeout=timeout)
            self.handle(kind, value)
        except Empty:
            pass
        if not self.closed and time.monotonic() >= self.next_refresh_at:
            self.refresh()
            self.stream_delivery.select_run(self.run_dir)
            self.stream_delivery.pump(self.projector.present_stream, self.emit)
            self.next_refresh_at = time.monotonic() + 0.15
        self.check_shutdown()

    def close(self) -> None:
        self.executor.shutdown(wait=True)
        self.projector.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--app-home", required=True, type=Path)
    args = parser.parse_args()
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    paths = AppPaths.discover(app_home=args.app_home, repo_root=args.project_root)
    load_dotenv(paths.env_path)
    bridge = Bridge(
        config_path=args.config,
        project_root=args.project_root,
        paths=paths,
        output=sys.stdout,
    )

    def read_input() -> None:
        for line in sys.stdin:
            try:
                bridge.queue.put(("command", Command.parse(line)))
            except (ValueError, TypeError) as error:
                bridge.queue.put(("invalid", str(error)))
        bridge.queue.put(("eof", None))

    Thread(target=read_input, daemon=True, name="tui-input").start()
    try:
        while not bridge.closed:
            bridge.poll()
    finally:
        bridge.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
