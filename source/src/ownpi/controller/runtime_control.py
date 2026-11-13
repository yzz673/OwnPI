from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Literal

from ownpi.app.paths import AppPaths
from ownpi.diagnostics import diagnostic_session
from ownpi.controller.run_context import FreshStartRejectedError, PreparedRunContext
from ownpi.controller.session_inspection import SessionInspectionService
from ownpi.controller.session_manager import RuntimeSessionManager
from ownpi.runtime.sessions import SessionTaskStatus
from ownpi.controller.runtime_control_signal import (
    RuntimeControlAction,
    clear_runtime_control_request,
    consume_runtime_control_request,
    read_runtime_control_request,
    runtime_control_lock,
    submit_runtime_control_with_disposition,
)
from ownpi.controller.runtime_active_owner import (
    notify_active_runtime_owner,
    runtime_owner_is_active,
)
from ownpi.schemas.runtime import UserConfig
from ownpi.utils.locking import FileLockUnavailableError, filesystem_write_lock


@dataclass(frozen=True)
class RuntimeControlResult:
    """Client-facing result projected from existing Runtime owners."""

    action: str
    accepted: bool
    run_id: str
    run_dir: str
    message: str = ""
    request_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    disposition: Literal["accepted", "pending", "rejected"] = "accepted"

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "accepted": self.accepted,
            "message": self.message,
            "request_id": self.request_id,
            "run_id": self.run_id,
            "run_dir": self.run_dir,
            "payload": dict(self.payload),
            "disposition": self.disposition,
        }


class RuntimeControlService:
    """Coordinates user actions through the existing Runtime owners."""

    def __init__(
        self,
        *,
        user_config: UserConfig,
        project_root: Path,
        app_paths: AppPaths | None = None,
    ) -> None:
        self._user_config = user_config
        self._project_root = Path(project_root).expanduser().resolve()
        self._app_paths = app_paths or AppPaths.discover(repo_root=self._project_root)
        self._session_manager = RuntimeSessionManager(
            app_paths=self._app_paths,
            project_root=self._project_root,
        )
        self._inspection = SessionInspectionService(
            store=self._session_manager.store,
        )

    def status(self, run_dir: Path | str) -> RuntimeControlResult:
        resolved = self.resolve_run_target(run_dir)
        return self._result(
            action='status', run_dir=resolved, accepted=True, message='',
            payload=self._inspection.run_status(resolved),
        )

    def pause(self, run_dir: Path | str, *, reason: str = "") -> RuntimeControlResult:
        return self._request_control(run_dir, action="pause", reason=reason)

    def interrupt(self, run_dir: Path | str, *, reason: str = "") -> RuntimeControlResult:
        return self._request_control(run_dir, action="interrupt", reason=reason)

    def stop(self, run_dir: Path | str, *, reason: str = "") -> RuntimeControlResult:
        """Immediately cancel active work while retaining its resumable rollout."""
        result = self._request_control(run_dir, action="interrupt", reason=reason)
        return replace(
            result,
            action="stop",
            message=(
                "stop request accepted and delivered to the active Runtime"
                if result.accepted
                else result.message
            ),
        )

    def start(
        self,
        *,
        raw_user_message: str,
        run_context: PreparedRunContext,
        event_callback: Callable[[str], None] | None = None,
        runtime_status_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> RuntimeControlResult:
        try:
            from ownpi.controller.workflow import run_raw_message_start

            with diagnostic_session(run_context.run_dir, operation='start'):
                package = run_raw_message_start(
                    raw_user_message=raw_user_message,
                    user_config=self._user_config,
                    project_root=self._project_root,
                    run_context=run_context,
                    app_paths=self._app_paths,
                    event_callback=event_callback,
                    runtime_status_callback=runtime_status_callback,
                )
        except FreshStartRejectedError:
            raise
        except Exception as exc:
            self.record_start_failure(run_context, error=exc)
            raise
        return self._result(
            action="start",
            run_dir=run_context.run_dir,
            accepted=True,
            message="fresh Runtime execution completed or reached a durable boundary",
            payload=package.to_dict(),
        )

    def prepare_direct_start(self, *, raw_user_message: str,
                             run_context: PreparedRunContext) -> None:
        """Confirm rollout admission before handing ownership to a worker."""
        from ownpi.controller.engine import WorkflowEngine

        engine = WorkflowEngine(user_config=self._user_config,
                                project_root=self._project_root, app_paths=self._app_paths)
        try:
            with diagnostic_session(run_context.run_dir, operation='admit'):
                engine.open_direct_session(run_context=run_context).admit(raw_user_message)
        finally:
            engine.close(wait=True)

    def record_start_failure(
        self,
        run_context: PreparedRunContext,
        *,
        error: BaseException,
    ) -> None:
        """Project a CLI/process startup failure into the session index."""
        try:
            self._session_manager.store.update_run_status(
                run_context.session_id, run_context.task_id, run_context.run_id,
                status=SessionTaskStatus.FAILED.value,
                metadata={
                    "failure": "runtime_start_failure",
                    "failure_class": type(error).__name__,
                    "failure_message": str(error),
                },
            )
        except Exception:
            logging.getLogger(__name__).warning('Failed to record start failure for run', exc_info=True)
        try:
            self._session_manager.store.update_task_status(
                run_context.session_id, run_context.task_id,
                status=SessionTaskStatus.FAILED.value, active_run_id=run_context.run_id,
                metadata={
                    "last_run_status": SessionTaskStatus.FAILED.value,
                    "failure_class": type(error).__name__,
                },
            )
        except Exception:
            logging.getLogger(__name__).warning('Failed to record start failure for task', exc_info=True)

    def answer(
        self,
        run_dir: Path,
        *,
        answers: dict[str, Any],
        session_id: str | None = None,
    ) -> RuntimeControlResult:
        from ownpi.controller.engine import WorkflowEngine

        resolved = self._resolve_run_dir(run_dir)
        engine = WorkflowEngine(
            user_config=self._user_config,
            project_root=self._project_root,
            app_paths=self._app_paths,
        )
        try:
            with diagnostic_session(resolved, operation='answer'):
                package = engine.answer_pending_interaction(resolved, answers=answers, session_id=session_id)
        finally:
            engine.close(wait=True)
        self._sync_session_index(resolved, package.status)
        return self._result(
            action="answer",
            run_dir=resolved,
            accepted=True,
            message="interaction answer delegated to InteractionController",
            payload=package.to_dict(),
        )

    def resume(
        self, run_dir: Path | str, *,
        event_callback: Callable[[str], None] | None = None,
        runtime_status_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> RuntimeControlResult:
        from ownpi.controller.engine import WorkflowEngine

        resolved = self.resolve_run_target(run_dir)
        session_id, _, _ = self.run_identity(resolved)
        session = self._session_manager.store.load_session(session_id)
        project_root = Path(session.project_root).expanduser().resolve() if session.project_root else self._project_root
        engine = WorkflowEngine(
            user_config=self._user_config,
            project_root=project_root,
            app_paths=self._app_paths,
            event_callback=event_callback,
            runtime_status_callback=runtime_status_callback,
        )
        try:
            with diagnostic_session(resolved, operation='resume'):
                package = engine.resume_rollout(resolved)
        finally:
            engine.close(wait=True)
        self._sync_session_index(resolved, package.status)
        return self._result(
            action="resume", run_dir=resolved, accepted=True,
            message="run resumed from its latest durable recovery point",
            payload=package.to_dict(),
        )

    def answer_tool_permission(
        self,
        run_dir: Path,
        *,
        decision: str,
        scope: str = "once",
        session_id: str = "",
    ) -> RuntimeControlResult:
        from ownpi.controller.engine import WorkflowEngine

        resolved = self._resolve_run_dir(run_dir)
        engine = WorkflowEngine(
            user_config=self._user_config,
            project_root=self._project_root,
            app_paths=self._app_paths,
        )
        try:
            with diagnostic_session(resolved, operation='answer_tool_permission'):
                package = engine.answer_tool_permission(
                    resolved,
                    decision=decision,
                    scope=scope,
                    session_id=session_id,
                )
        finally:
            engine.close(wait=True)
        self._sync_session_index(resolved, package.status)
        return self._result(
            action="answer_tool_permission",
            run_dir=resolved,
            accepted=True,
            message="tool permission answer delegated to InteractionController",
            payload=package.to_dict(),
        )

    def list(self) -> list[dict[str, Any]]:
        return self._inspection.list_sessions()

    def list_runs(self) -> list[dict[str, Any]]:
        """List run identities without reconstructing Runtime state."""
        runs: list[dict[str, Any]] = []
        for session in self._session_manager.store.list_sessions():
            for task in session.tasks:
                for run in task.runs:
                    run_dir = Path(run.run_dir).expanduser().resolve()
                    runs.append(
                        {
                            "session_id": session.session_id,
                            "task_id": task.task_id,
                            "run_id": run.run_id,
                            "run_dir": str(run_dir),
                            "created_at": run.created_at,
                            "updated_at": run.updated_at,
                        }
                    )
        return runs

    def resolve_run_target(self, target: Path | str) -> Path:
        """Resolve either a canonical run directory or an unambiguous run ID."""
        candidate = Path(str(target)).expanduser()
        if candidate.exists():
            return self._resolve_run_dir(candidate)
        run_id = str(target or '').strip()
        if not run_id:
            raise ValueError('run ID or run directory is required')
        matches: list[Path] = []
        for session in self._session_manager.store.list_sessions():
            for task in session.tasks:
                for run in task.runs:
                    if run.run_id == run_id:
                        matches.append(Path(run.run_dir).expanduser().resolve())
        if not matches:
            raise FileNotFoundError(f'Runtime run not found: {target}')
        if len(matches) > 1:
            raise ValueError(f'run ID is ambiguous across sessions: {run_id}')
        return self._resolve_run_dir(matches[0])

    @staticmethod
    def run_identity(run_dir: Path) -> tuple[str, str, str]:
        from ownpi.runtime.sessions import parse_session_run_dir

        parsed = parse_session_run_dir(run_dir)
        if parsed is None:
            raise ValueError('run directory is not session-managed')
        return parsed.session_id, parsed.task_id, parsed.run_id

    def _request_control(
        self,
        run_dir: Path | str,
        *,
        action: RuntimeControlAction,
        reason: str,
    ) -> RuntimeControlResult:
        resolved = self.resolve_run_target(run_dir)
        if not runtime_owner_is_active(resolved):
            return self._result(
                action=action,
                run_dir=resolved,
                accepted=False,
                disposition="rejected",
                message=f"cannot request {action}: Runtime has no active owner",
            )
        submission = submit_runtime_control_with_disposition(
            resolved,
            action=action,
            reason=reason,
        )
        request = submission.request
        payload: dict[str, Any] = {}
        if action == "interrupt":
            notification = notify_active_runtime_owner(resolved)
            if notification.owner_exited:
                try:
                    # Exclude a new runtime before consuming the old owner's stop.
                    with filesystem_write_lock(resolved / ".runtime.lock", blocking=False):
                        with runtime_control_lock(resolved):
                            current = read_runtime_control_request(resolved)
                            if current is not None and current.request_id == request.request_id:
                                consume_runtime_control_request(resolved, current)
                                clear_runtime_control_request(resolved, request_id=current.request_id)
                except FileLockUnavailableError:
                    pass
            payload = {
                "owner_notified": notification.notified,
                "notification_reason": notification.reason,
                "owner_pid": (
                    notification.owner.pid
                    if notification.owner is not None
                    else None
                ),
                "owner_exited": notification.owner_exited,
                "owner_escalated": notification.escalated,
                "owner_escalation_signal": notification.escalation_signal,
            }
        return self._result(
            action=action,
            run_dir=resolved,
            accepted=True,
            disposition=submission.disposition,
            request_id=request.request_id,
            payload=payload,
            message=(
                f"interrupt request {submission.disposition} and delivered to the active Runtime"
                if action == "interrupt"
                else f"pause request {submission.disposition} and will be consumed at a safe point"
            ),
        )

    def _result(
        self,
        *,
        action: str,
        run_dir: Path,
        accepted: bool,
        message: str,
        request_id: str = "",
        payload: dict[str, Any] | None = None,
        disposition: Literal["accepted", "pending", "rejected"] = "accepted",
    ) -> RuntimeControlResult:
        return RuntimeControlResult(
            action=action,
            accepted=accepted,
            run_id=run_dir.name,
            run_dir=str(run_dir),
            message=message,
            request_id=request_id,
            payload=dict(payload or {}),
            disposition=disposition,
        )

    @staticmethod
    def _resolve_run_dir(run_dir: Path | str) -> Path:
        resolved = Path(run_dir).expanduser().resolve()
        if not resolved.is_dir():
            raise FileNotFoundError(f"Run directory not found: {resolved}")
        return resolved

    def _sync_session_index(self, run_dir: Path, status: str) -> None:
        from ownpi.runtime.sessions import parse_session_run_dir

        parsed = parse_session_run_dir(run_dir)
        if parsed is None:
            return
        try:
            self._session_manager.store.update_run_status(
                parsed.session_id,
                parsed.task_id,
                parsed.run_id,
                status=str(status or "failed"),
            )
        except Exception:
            logging.getLogger(__name__).warning(
                'Failed to synchronize run result', exc_info=True,
            )
        task_status = {
            "paused_for_human": SessionTaskStatus.BLOCKED.value,
            "interrupted": SessionTaskStatus.BLOCKED.value,
            "stopped": SessionTaskStatus.BLOCKED.value,
        }.get(str(status or ""), str(status or "failed"))
        try:
            self._session_manager.store.update_task_status(
                parsed.session_id,
                parsed.task_id,
                status=task_status,
                active_run_id=parsed.run_id,
            )
        except Exception:
            logging.getLogger(__name__).warning(
                'Failed to synchronize task result', exc_info=True,
            )


__all__ = ["RuntimeControlResult", "RuntimeControlService"]
