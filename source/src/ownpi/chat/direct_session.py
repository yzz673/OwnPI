"""REPL attachment to the production direct owner."""

from ownpi.controller.engine import WorkflowEngine
from ownpi.controller.run_context import PreparedRunContext
from ownpi.controller.runtime_control import RuntimeControlService
from ownpi.diagnostics import diagnostic_session
from ownpi.runtime.context.entry import RuntimeEntryContext
from ownpi.runtime.hooks.context_delivery import persist_hook_delivery_batch
from ownpi.runtime.hooks.types import HookDeliveryBatch
from ownpi.runtime.sessions import parse_session_run_dir


class DirectChatSession:
    def __init__(self):
        self.engine = None
        self.owner = None
        self.run_context = None
        self.config = None

    def detach(self):
        if self.engine is not None:
            self.engine.close(wait=True)
        self.engine = self.owner = None

    def _attach(self, *, user_config, project_root, app_paths, run_context,
                entry_context, event_callback=None, runtime_status_callback=None,
                artifact_callback=None, verbose_llm_progress=False, resume=False):
        if self.engine is not None and user_config != self.config:
            self.detach()
        if self.engine is None:
            self.engine = WorkflowEngine(
                user_config=user_config, project_root=project_root, app_paths=app_paths,
                event_callback=event_callback, runtime_status_callback=runtime_status_callback,
                artifact_callback=artifact_callback, verbose_llm_progress=verbose_llm_progress,
            )
            try:
                self.owner = self.engine.open_direct_session(
                    run_context=None if resume or self.run_context else run_context,
                    run_dir=run_context.run_dir, runtime_entry_context=entry_context,
                )
            except BaseException:
                self.detach()
                raise
            self.config = user_config
            self.run_context = run_context
        else:
            self.engine._event_callback = event_callback
            self.engine._runtime_status_callback = runtime_status_callback
            self.engine._artifact_callback = artifact_callback
            self.owner.entry_context = entry_context

    def execute(self, *, raw_user_message, user_config, project_root, run_context,
                app_paths, event_callback=None, runtime_status_callback=None,
                artifact_callback=None, verbose_llm_progress=False,
                hook_prefix_fragments=(), session_start_hook_emitted=False,
                stage_runtime_profile=None):
        self._attach(
            user_config=user_config, project_root=project_root, app_paths=app_paths,
            run_context=run_context, entry_context=RuntimeEntryContext(
                hook_prefix_fragments=hook_prefix_fragments,
                session_start_hook_emitted=session_start_hook_emitted,
            ), event_callback=event_callback, runtime_status_callback=runtime_status_callback,
            artifact_callback=artifact_callback, verbose_llm_progress=verbose_llm_progress,
        )
        if self.owner.session.active:
            raise RuntimeError('Current request is unfinished; answer its interaction or use /resume-run first')
        persist_hook_delivery_batch(self.owner.store.state.context_state,
                                    HookDeliveryBatch(model_context_fragments=hook_prefix_fragments))
        try:
            with diagnostic_session(run_context.run_dir, operation='direct_submit'):
                result = self.owner.submit(raw_user_message)
        except BaseException:
            self.detach()
            raise
        RuntimeControlService(user_config=user_config, project_root=project_root,
                              app_paths=app_paths)._sync_session_index(run_context.run_dir, result.status)
        if result.status != 'idle':
            self.detach()
        return result

    def resume(self, run_dir, *, user_config, project_root, app_paths):
        self.detach()
        identity = parse_session_run_dir(run_dir)
        if identity is None:
            raise ValueError('Direct attachment requires a session-managed run')
        self.run_context = PreparedRunContext(
            session_id=identity.session_id, task_id=identity.task_id,
            run_id=identity.run_id, run_dir=run_dir,
        )
        self._attach(user_config=user_config, project_root=project_root, app_paths=app_paths,
                     run_context=self.run_context,
                     entry_context=RuntimeEntryContext(origin='resume', session_start_hook_emitted=True),
                     resume=True)
        try:
            with diagnostic_session(run_dir, operation='direct_resume'):
                result = self.owner.continue_request()
        except BaseException:
            self.detach()
            raise
        RuntimeControlService(user_config=user_config, project_root=project_root,
                              app_paths=app_paths)._sync_session_index(run_dir, result.status)
        if result.status != 'idle':
            self.detach()
        return result
