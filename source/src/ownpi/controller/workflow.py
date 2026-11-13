from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from ownpi.app.paths import AppPaths
from ownpi.controller.engine import WorkflowEngine
from ownpi.controller.run_context import FreshStartRejectedError, PreparedRunContext
from ownpi.controller.session_manager import RuntimeSessionManager
from ownpi.runtime.context.entry import RuntimeEntryContext
from ownpi.runtime.hooks.types import HookModelContextFragment
from ownpi.runtime.sessions import SessionTaskStatus
from ownpi.schemas.artifacts import FinalPackage
from ownpi.schemas.runtime import AgentInstanceConfig, UserConfig
from ownpi.schemas.workflow_plan import StageRuntimeProfile, resolve_runtime_profile


ArtifactCallback = Callable[[str, AgentInstanceConfig, str, dict[str, Any], str], None]


def run_fresh_admission(
    *,
    raw_user_message: str,
    user_config: UserConfig,
    project_root: Path,
    run_context: PreparedRunContext,
    app_paths: AppPaths | None = None,
    event_callback: Callable[[str], None] | None = None,
    runtime_status_callback: Callable[[dict[str, Any]], None] | None = None,
    artifact_callback: ArtifactCallback | None = None,
    verbose_llm_progress: bool = False,
    hook_prefix_fragments: tuple[HookModelContextFragment, ...] = (),
    session_start_hook_emitted: bool = False,
    stage_runtime_profile: StageRuntimeProfile | None = None,
    runtime_profile_selection: str | None = None,
) -> FinalPackage:
    runtime_profile = stage_runtime_profile or resolve_runtime_profile(
        invocation_selection=runtime_profile_selection,
        config_selection=user_config.runtime.default_profile,
    ).resolved
    return _run_fresh_entry(
        user_config=user_config, project_root=project_root, run_context=run_context,
        app_paths=app_paths, event_callback=event_callback,
        runtime_status_callback=runtime_status_callback, artifact_callback=artifact_callback,
        verbose_llm_progress=verbose_llm_progress,
        execute=lambda engine: engine.execute_fresh_prepared(
            raw_user_message=raw_user_message, run_context=run_context,
            stage_runtime_profile=runtime_profile,
            runtime_entry_context=RuntimeEntryContext(
                hook_prefix_fragments=hook_prefix_fragments,
                session_start_hook_emitted=session_start_hook_emitted,
            ),
        ),
    )


def run_raw_message_start(
    *, raw_user_message: str, user_config: UserConfig, project_root: Path,
    run_context: PreparedRunContext, app_paths: AppPaths,
    event_callback: Callable[[str], None] | None = None,
    runtime_status_callback: Callable[[dict[str, Any]], None] | None = None,
) -> FinalPackage:
    return _run_fresh_entry(
        user_config=user_config, project_root=project_root, run_context=run_context,
        app_paths=app_paths, event_callback=event_callback,
        runtime_status_callback=runtime_status_callback,
        execute=lambda engine: engine.execute_fresh_raw_message(
            raw_user_message=raw_user_message, run_context=run_context,
        ),
    )


def _run_fresh_entry(
    *, user_config: UserConfig, project_root: Path, run_context: PreparedRunContext,
    app_paths: AppPaths | None, execute: Callable[[WorkflowEngine], FinalPackage],
    event_callback: Callable[[str], None] | None,
    runtime_status_callback: Callable[[dict[str, Any]], None] | None,
    artifact_callback: ArtifactCallback | None = None,
    verbose_llm_progress: bool = False,
) -> FinalPackage:
    resolved_app_paths = app_paths or AppPaths.discover(repo_root=project_root)
    engine = WorkflowEngine(
        user_config=user_config,
        project_root=project_root,
        app_paths=resolved_app_paths,
        event_callback=event_callback,
        runtime_status_callback=runtime_status_callback,
        artifact_callback=artifact_callback,
        verbose_llm_progress=verbose_llm_progress,
    )
    session_manager = RuntimeSessionManager(
        app_paths=resolved_app_paths,
        project_root=project_root,
    )
    try:
        result = execute(engine)
    except FreshStartRejectedError:
        raise
    except Exception:
        try:
            session_manager.store.update_run_status(
                run_context.session_id, run_context.task_id, run_context.run_id,
                status=SessionTaskStatus.FAILED.value,
            )
        except Exception:
            logging.getLogger(__name__).warning('Failed to synchronize fresh run failure', exc_info=True)
        try:
            session_manager.store.update_task_status(
                run_context.session_id, run_context.task_id,
                status=SessionTaskStatus.FAILED.value, active_run_id=run_context.run_id,
            )
        except Exception:
            logging.getLogger(__name__).warning('Failed to synchronize fresh task failure', exc_info=True)
        raise
    finally:
        engine.close(wait=True)
    try:
        session_manager.store.update_run_status(
            run_context.session_id, run_context.task_id, run_context.run_id,
            status=result.status,
        )
    except Exception:
        logging.getLogger(__name__).warning('Failed to synchronize fresh run result', exc_info=True)
    task_status = {
        'paused_for_human': SessionTaskStatus.BLOCKED.value,
    }.get(result.status, result.status)
    try:
        session_manager.store.update_task_status(
            run_context.session_id, run_context.task_id,
            status=task_status, active_run_id=run_context.run_id,
        )
    except Exception:
        logging.getLogger(__name__).warning('Failed to synchronize fresh task result', exc_info=True)
    return result
