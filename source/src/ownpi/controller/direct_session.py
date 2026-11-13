"""Production ownership around the shared direct critic session."""

from contextlib import ExitStack, contextmanager

from ownpi.agents import load_agent_modules
from ownpi.controller.direct_critic import DirectCriticSession
from ownpi.controller.runtime_control_signal import RuntimeControlRequested, clear_consumed_runtime_control_request
from ownpi.controller.staged_runtime_support import build_runtime_control_poll
from ownpi.controller.stages import Stage
from ownpi.llm.provider_request_context import ProviderRequestContext, bind_provider_request_context
from ownpi.llm.telemetry import llm_trace_session
from ownpi.runtime.context.entry import RuntimeEntryContext
from ownpi.runtime.interactions.pause import (
    HumanClarificationPausedError, ToolPermissionPausedError, raise_pending_actor_interaction,
)
from ownpi.runtime.rollout.direct_resume import latest_direct_answer, resolve_direct_resume
from ownpi.runtime.rollout.resume_read_session import ResumeReadSession
from ownpi.schemas.artifacts import FinalPackage
from ownpi.schemas.workflow_plan import StageRuntimeProfile


class DirectSessionOwner:
    def __init__(self, engine, *, run_context=None, run_dir=None,
                 runtime_entry_context=None, facts=None, locked=False):
        self.engine = engine
        self.stack = ExitStack()
        self.closed = False
        self.session = None
        self.store = None
        self.saved_files = []
        self.entry_context = runtime_entry_context or RuntimeEntryContext(
            origin='resume' if run_context is None else 'live',
        )
        self.run_dir = run_context.run_dir if run_context else run_dir.resolve()
        try:
            if not locked:
                self.stack.enter_context(engine._fresh_runtime_lock(engine._runtime_lock_path(self.run_dir)))
                self.stack.enter_context(engine._bind_active_runtime_owner(
                    self.run_dir, run_id=self.run_dir.name, mode='fresh' if run_context else 'resume',
                ))
            engine._configure_review_policy(StageRuntimeProfile.TASK_LEVEL)
            restored = None
            if run_context is not None:
                from ownpi.controller.run_context import FreshStartRejectedError
                from ownpi.runtime.rollout.business_reader import read_business_events
                from ownpi.runtime.rollout.run_events import run_events_path

                if run_events_path(self.run_dir).exists():
                    events = read_business_events(run_events_path(self.run_dir)).events
                    if any(event.kind in {'stage_attempt_started', 'direct_session_bound'}
                           for event in events):
                        raise FreshStartRejectedError('Run already initialized; resume the existing direct session.')
                store, self.saved_files = engine._initialize_fresh_workflow_state(
                    run_context=run_context, run_dir=self.run_dir,
                    stage_runtime_profile=StageRuntimeProfile.TASK_LEVEL,
                    runtime_entry_context=self.entry_context,
                )
                store.state.workflow_state.scratchpad.update(run_context.scratchpad_payload())
                engine._apply_prepared_run_context(state_store=store, run_context=run_context)
                store.state.workflow_state.current_stage = Stage.DIRECT_QA
                store.enter_stage_attempt(Stage.DIRECT_QA)
            else:
                from ownpi.runtime.rollout.resume import read_run_resume
                from ownpi.runtime.rollout.resume_state import restore_workflow_state
                from ownpi.runtime.rollout.lifecycle import attach_run_events

                if facts is None:
                    reader = self.stack.enter_context(ResumeReadSession())
                    facts = read_run_resume(self.run_dir, read_session=reader)
                restored = resolve_direct_resume(facts)
                store = restore_workflow_state(
                    restored.entry, project_root=engine._project_root,
                    user_config=engine._user_config, app_paths=engine._app_paths,
                )
                store.bind_resume_conversation(
                    stage=Stage.DIRECT_QA.value, attempt=restored.entry.attempt,
                    actor_kind='core', conversation_id=restored.core_id,
                )
                attach_run_events(store, self.run_dir)
            self.store = store
            engine._run_event_state_store = store
            store.state.workflow_state.scratchpad['direct_session_production'] = True
            engine._attach_execution_runtime_service(state_store=store, run_dir=self.run_dir)
            load_agent_modules()
            agents = engine._build_agents()
            context = engine._build_agent_runtime_invocation_context(
                state_store=store, run_dir=self.run_dir, saved_files=self.saved_files,
                stage=Stage.DIRECT_QA, attempt=store.state.workflow_state.current_stage_attempt,
                operation='direct_critic_session',
            )
            # Building the session-level context must not consume Core's replay binding.
            if context.resume_conversation_id:
                store.bind_resume_conversation(
                    stage=Stage.DIRECT_QA.value, attempt=context.attempt, actor_kind='core',
                    conversation_id=context.resume_conversation_id,
                )
            self.session = DirectCriticSession(
                invocation_context=context, runtime_agents=agents,
                emit=engine._emit, operation_runner=self._run_operation,
                prepare_request=self._prepare_request,
            )
            if restored is not None:
                self.session.restore(message=restored.message, loop_index=restored.entry.loop_index,
                                     active=restored.active)
            if restored is not None or self.entry_context.session_start_hook_emitted:
                store.state.workflow_state.scratchpad['hook_session_started'] = True
            from ownpi.runtime.hooks.context_delivery import persist_hook_delivery_batch
            from ownpi.runtime.hooks.types import HookDeliveryBatch

            persist_hook_delivery_batch(store.state.context_state, HookDeliveryBatch(
                model_context_fragments=self.entry_context.hook_prefix_fragments,
            ))
            with self._request_context():
                engine._emit_session_start_hook(state_store=store, run_dir=self.run_dir,
                                                source='direct_session')
        except BaseException:
            self.close()
            raise

    @contextmanager
    def _request_context(self):
        metadata = self.store.state.run_metadata
        poll = build_runtime_control_poll(
            host=self.engine, state_store=self.store, run_dir=self.run_dir,
            saved_files=self.saved_files, current_stage=Stage.DIRECT_QA, allow_direct=True,
        )
        with (llm_trace_session(sink=lambda event: self.engine._record_llm_trace(
                state_store=self.store, event=event)),
              bind_provider_request_context(ProviderRequestContext(
                  session_id=metadata.session_id, task_id=metadata.task_id, run_id=metadata.run_id)),
              self.store.bind_runtime_entry_context(self.entry_context),
              self.store.bind_runtime_control_poll(poll)):
            yield

    def _prepare_request(self, *, current_request, continuation):
        config, agent = self.session.runtime_agents['core']
        context = self.engine._build_agent_runtime_invocation_context(
            state_store=self.store, run_dir=self.run_dir, saved_files=self.saved_files,
            stage=Stage.DIRECT_QA, attempt=self.store.state.workflow_state.current_stage_attempt,
            operation='direct_critic_core', native_loop_continuation=continuation,
        )
        previous = agent.set_runtime_invocation_context(context)
        try:
            agent._try_run_native_stage_core_round(self.store.build_agent_input(
                config, operation='direct_critic_core', current_request=current_request,
            ), prepare_only=True)
        finally:
            agent.set_runtime_invocation_context(previous)

    def _run_operation(self, *, operation, **kwargs):
        engine = self.engine
        args = dict(state_store=self.store, run_dir=self.run_dir, saved_files=self.saved_files,
                    stage=Stage.DIRECT_QA, attempt=self.store.state.workflow_state.current_stage_attempt,
                    label='direct critic', filename=f'{operation}.json', event_name=f'{operation}_completed',
                    operation=operation, **kwargs)
        result = engine._stream_agent_operation(**args)
        if result is None:
            result = engine._run_agent_operation(**args)
        if operation == 'direct_critic_reviewer':
            from ownpi.controller.stage_kernel import (
                StageContinuationTerminatedError, _native_loop_terminal_status_decision,
            )

            decision = _native_loop_terminal_status_decision(
                state_store=self.store, stage=Stage.DIRECT_QA,
                attempt=self.store.state.workflow_state.current_stage_attempt,
                round_index=int(self.store.state.workflow_state.scratchpad.get('continuation_index', 0)),
                result=result,
            )
            if decision is not None:
                raise StageContinuationTerminatedError(
                    stage=Stage.DIRECT_QA,
                    attempt=self.store.state.workflow_state.current_stage_attempt,
                    continuation_index=0, action='fail', reason=decision['reason'],
                    step_results=[], terminal_decision_hint=decision['terminal_decision_hint'],
                )
        raise_pending_actor_interaction(
            state_store=self.store, stage=Stage.DIRECT_QA,
            attempt=self.store.state.workflow_state.current_stage_attempt,
            continuation_index=int(self.store.state.workflow_state.scratchpad.get('continuation_index', 0)),
            step_results=[],
        )
        return result

    def admit(self, message):
        if self.closed:
            raise RuntimeError('Direct session is detached')
        with self._request_context():
            self.session.admit(message)

    def submit(self, message):
        return self._execute_request(lambda: (self.admit(message), self._continue())[1])

    def continue_request(self):
        return self._execute_request(self._continue)

    def _continue(self):
        if self.closed:
            raise RuntimeError('Direct session is detached')
        interaction = self.store.state.workflow_state.pending_human_clarification
        if interaction is not None and interaction.status == 'pending':
            return self.engine._build_paused_interaction_result(
                state_store=self.store, run_dir=self.run_dir, saved_files=self.saved_files,
                final_decision='paused_for_human',
            )
        with self._request_context():
            if self.session.active:
                self.session.continue_request()
        return self.package()

    def _execute_request(self, operation):
        from ownpi.controller.stage_kernel import StageContinuationTerminatedError
        from ownpi.llm.provider_retry import RetryCancelled

        self.store.state.workflow_state.scratchpad.pop('runtime_failure_details', None)
        try:
            return operation()
        except (HumanClarificationPausedError, ToolPermissionPausedError):
            return self.engine._build_paused_interaction_result(
                state_store=self.store, run_dir=self.run_dir, saved_files=self.saved_files,
                final_decision='paused_for_human',
            )
        except RuntimeControlRequested as error:
            clear_consumed_runtime_control_request(self.run_dir, request_id=error.request.request_id)
            return self.package(status='paused_for_human' if error.request.action == 'pause' else 'stopped')
        except RetryCancelled:
            try:
                self.store.poll_runtime_control('direct_provider_cancelled')
            except RuntimeControlRequested as error:
                clear_consumed_runtime_control_request(self.run_dir, request_id=error.request.request_id)
            return self.package(status='stopped')
        except StageContinuationTerminatedError:
            from uuid import uuid4

            self.store.run_event_store.emit('direct_request_failed', f'direct-failure:{uuid4().hex}', {
                'loop_index': self.session.loop_index,
                'failure': self.store.state.workflow_state.scratchpad.get('runtime_failure_details', {}),
            })
            return self.package(status='failed')

    def package(self, *, status='idle'):
        metadata = self.store.state.run_metadata
        return FinalPackage(
            status=status, task_id=metadata.task_id, current_stage=Stage.DIRECT_QA.value,
            final_decision='critic_complete' if status == 'idle' else 'fail' if status == 'failed' else status,
            run_summary={'run_id': metadata.run_id, 'status': status,
                         'execution_tier': 'direct', 'request_index': self.session.loop_index,
                         'request_status': 'active' if self.session.active else 'completed',
                         'direct_answer': latest_direct_answer(self.store, self.run_dir,
                                                             loop_index=self.session.loop_index)},
            stage_runs=[], run_artifacts={'run_dir': str(self.run_dir)},
            runtime_diagnostics=dict(self.store.state.workflow_state.scratchpad.get('runtime_failure_details', {})),
        )

    def close(self):
        if self.closed:
            return
        if self.session is not None and not self.session.close():
            raise OSError('Direct Core writer close is unconfirmed')
        try:
            if self.store is not None:
                try:
                    with self._request_context():
                        self.engine._emit_session_end_hook(state_store=self.store, run_dir=self.run_dir,
                                                           reason='direct_detach')
                finally:
                    self.engine._shutdown_execution_runtime(self.store, terminal=False)
        finally:
            self.stack.close()
            self.closed = True
