from __future__ import annotations

from pathlib import Path

from ownpi.app.paths import AppPaths
from ownpi.controller.run_context import PreparedRunContext
from ownpi.runtime.sessions import SessionStore, SessionTaskType


class RuntimeSessionManager:
    def __init__(self, *, app_paths: AppPaths, project_root: Path) -> None:
        self.app_paths = app_paths
        self.project_root = Path(project_root).expanduser().resolve()
        self.store = SessionStore(self.app_paths.app_home / 'sessions')

    def prepare_workflow_run(
        self,
        *,
        session_id: str | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
        entry: str = 'workflow',
    ) -> PreparedRunContext:
        session = self.store.get_or_create_session(
            session_id=session_id,
            project_root=str(self.project_root),
            metadata={'entry': entry},
        )
        task = self.store.get_or_create_task(
            session.session_id,
            task_id=task_id,
            task_type=SessionTaskType.WORKFLOW,
            metadata={'entry': entry},
        )
        run = self.store.allocate_run(
            session.session_id,
            task.task_id,
            run_id=run_id,
            metadata={'entry': entry},
        )
        return PreparedRunContext(
            session_id=session.session_id,
            task_id=task.task_id,
            run_id=run.run_id,
            run_dir=Path(run.run_dir),
        )
