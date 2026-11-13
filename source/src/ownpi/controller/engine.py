from __future__ import annotations

import hashlib
import json
import logging
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator, Literal, cast

from ownpi.agents import load_agent_modules
from ownpi.agents.base import AgentRuntimeInvocationContext
from ownpi.agents.registry import build_agent
from ownpi.app.paths import AppPaths
from ownpi.chat.context_meter import build_context_meter_from_runtime_state
from ownpi.controller.final_package_builder import (
    FinalPackageBuilder,
    is_full_tier_runtime,
    llm_operation_key,
)
from ownpi.controller.finalization_service import FinalizationService, TERMINAL_RUN_STATUSES
from ownpi.controller.interaction_answer_service import (
    InteractionAnswerService,
)
from ownpi.controller.run_context import FreshStartRejectedError, PreparedRunContext
from ownpi.controller.staged_runtime_loop import StagedRuntimeLoop
from ownpi.controller.staged_runtime_host import StagedRuntimeHost
from ownpi.controller.stage_kernel import (
    StageKernel,
)
from ownpi.controller.state_store import WorkflowStateStore
from ownpi.controller.stages import (
    ACTIVE_STAGE_ORDER,
    GateDecision,
    Stage,
    TransitionOutcome,
    analysis_revisit_decision_for_target,
    analysis_revisit_target_for_decision,
    stage_from_value,
)
from ownpi.hooks.builtin import build_builtin_hook_registrations
from ownpi.llm.factory import build_llm_client
from ownpi.llm.base import LLMClient
from ownpi.llm.provider_request_context import (
    ProviderRequestContext,
    bind_provider_request_context,
)
from ownpi.llm.telemetry import llm_trace_session
from ownpi.runtime.events import now_utc_iso
from ownpi.runtime.task_context import (
    RuntimeTaskContext,
)
from ownpi.runtime.execution.monitor_dispatch import ExecutionToolDispatcher
from ownpi.runtime.execution.service import ExecutionRuntimeService
from ownpi.runtime.process_owner import ProcessOwnerManager
from ownpi.runtime.permissions.auto_review import (
    AutoReviewService,
    LLMAutoReviewBackend,
)
from ownpi.runtime.permissions.models import PermissionPreset
from ownpi.runtime.permissions.runtime_config import compile_runtime_permissions
from ownpi.runtime.context.prefetch import (
    consume_context_prefetch,
    start_context_prefetch,
)
from ownpi.runtime.context.entry import RuntimeEntryContext
from ownpi.runtime.context.provider_usage import read_provider_usage
from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.hooks.builder import build_hook_engine
from ownpi.runtime.hooks.context_delivery import persist_hook_delivery_batch
from ownpi.runtime.hooks.types import (
    HookDeliveryBatch,
    HookModelContextFragment,
    SessionEndRequest,
    SessionStartRequest,
)
from ownpi.runtime.continuation_directive import RuntimeContinuationDirective
from ownpi.controller.runtime_control_signal import (
    RuntimeControlRequested,
    clear_runtime_control_request,
    consume_runtime_control_request,
    read_runtime_control_request,
    runtime_interrupt_requested,
    runtime_control_lock,
)
from ownpi.controller.runtime_active_owner import bind_active_runtime_owner
from ownpi.runtime.limits.workflow import resolve_review_policy
from ownpi.runtime.limits.projection import MAX_LLM_TRACE_RECENT_EVENTS
from ownpi.runtime.research_state_resource import initialize_research_state

from ownpi.runtime.sessions import parse_session_run_dir
from ownpi.runtime.state_models import (
    FeedbackHandoffState,
    calculate_total_stage_attempts,
)
from ownpi.runtime.builders import apply_temporary_llm_overrides
from ownpi.runtime.tools.runtime import ToolRuntime
from ownpi.schemas.artifacts import (
    FinalPackage,
    GateTrace,
    StageRunSummary,
)
from ownpi.schemas.runtime import AgentInstanceConfig, LLMConfig, UserConfig
from ownpi.schemas.feedback import NextStageReceipt
from ownpi.schemas.runtime_human_clarification import (
    CONTROLLER_CLARIFICATION_HANDOFF_KEY,
    HumanClarificationQuestion,
    MAX_HUMAN_CLARIFICATION_QUESTIONS,
    HumanClarificationSession,
    build_question_key,
    active_controller_clarification_handoff,
    build_controller_clarification_handoff,
    human_answer_text,
    human_clarification_answer_summary,
    normalize_human_answer_payload,
)
from ownpi.schemas.runtime_tooling import LLMUsageReceipt
from ownpi.schemas.runtime_tooling import ToolObservation
from ownpi.schemas.workflow_plan import (
    ExecutionTier,
    StageRuntimeProfile,
    RunWorkflowPlan,
    execution_tier_for_profile,
)
from ownpi.utils.io import ensure_dir
from ownpi.utils.locking import FileLockUnavailableError, filesystem_write_lock

if TYPE_CHECKING:
    from ownpi.runtime.rollout.resume import ResumedConversation, RunResumeFacts
    from ownpi.runtime.rollout.resume_entry import StageResumeEntry


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class WorkflowEngine:
    def __init__(
        self,
        *,
        user_config: UserConfig,
        project_root: Path,
        app_paths: AppPaths | None = None,
        event_callback: Callable[[str], None] | None = None,
        runtime_status_callback: Callable[[dict[str, Any]], None] | None = None,
        artifact_callback: Callable[
            [str, AgentInstanceConfig, str, dict[str, Any], str], None
        ]
        | None = None,
        verbose_llm_progress: bool = False,
        subagent_owner: Any | None = None,
        subagent_adapter: Any | None = None,
        subagent_context_builder: Any | None = None,
    ) -> None:
        self._user_config = user_config
        self._project_root = project_root
        self._effective_review_policy = resolve_review_policy(
            user_config.runtime.review_profile
        )
        self._effective_permissions = compile_runtime_permissions(
            user_config.runtime.permissions,
            project_root=project_root,
        )
        self._app_paths = app_paths or AppPaths.discover(repo_root=project_root)
        self._app_paths.ensure_directories()
        self._event_callback = event_callback
        self._runtime_status_callback = runtime_status_callback
        self._artifact_callback = artifact_callback
        self._verbose_llm_progress = verbose_llm_progress
        if subagent_owner is not None:
            self._subagent_owner = subagent_owner
        else:
            from ownpi.subagent import SubagentOwner

            self._subagent_owner = SubagentOwner.from_runtime_config(
                self._user_config.runtime,
                adapter=subagent_adapter,
                context_builder=subagent_context_builder,
            )
        self._execution_runtime_service: ExecutionRuntimeService | None = None
        self._process_owner_manager: ProcessOwnerManager | None = None
        self._runtime_cancel_checker: Callable[[], bool] | None = None
        self._hook_engine = build_hook_engine(
            self._user_config.runtime.hooks,
            build_builtin_hook_registrations(self._user_config.runtime.builtin_hooks),
            effective_permissions=self._effective_permissions,
        )
        auto_review_service = None
        if (
            self._user_config.runtime.permissions.preset
            is PermissionPreset.APPROVE_FOR_ME
        ):
            reviewer_name = (
                "reviewer_model"
                if "reviewer_model" in self._user_config.llms
                else self._user_config.default_llm_name
            )
            auto_review_service = AutoReviewService(
                LLMAutoReviewBackend(
                    build_llm_client(self._user_config.get_llm_config(reviewer_name))
                )
            )
        self._tool_runtime = ToolRuntime(
            hook_engine=self._hook_engine,
            auto_review_service=auto_review_service,
        )
        self._final_package_builder = FinalPackageBuilder()
        self._finalization_service = FinalizationService(
            build_final_package=self._build_final_package,
            is_full_tier_runtime=self._is_full_tier_runtime,
            shutdown_execution_runtime=self._shutdown_execution_runtime,
            emit_session_end=self._emit_session_end_hook,
        )
        self._interaction_answer_service = InteractionAnswerService(
            user_config=self._user_config,
            project_root=self._project_root,
            app_paths=self._app_paths,
            build_paused_interaction_result=self._build_paused_interaction_result,
        )
        self._stage_kernel = StageKernel(
            run_agent_operation=self._run_agent_operation,
            stream_agent_operation=self._stream_agent_operation,
            runtime_gate=self._runtime_gate,
            emit=self._emit,
        )
        self._staged_runtime_loop = StagedRuntimeLoop(cast(StagedRuntimeHost, self))

    def close(self, *, wait: bool = True) -> None:
        direct = getattr(self, '_direct_session_owner', None)
        if direct is not None:
            direct.close()
        self._hook_engine.close(wait=wait)

    def open_direct_session(self, *, run_context=None, run_dir=None,
                            runtime_entry_context=None):
        from ownpi.controller.direct_session import DirectSessionOwner

        existing = getattr(self, '_direct_session_owner', None)
        if existing is not None and not existing.closed:
            raise RuntimeError('Engine already owns a direct session')
        owner = DirectSessionOwner(
            self, run_context=run_context, run_dir=run_dir,
            runtime_entry_context=runtime_entry_context,
        )
        self._direct_session_owner = owner
        return owner

    def execute_fresh_raw_message(
        self, *, raw_user_message: str, run_context: PreparedRunContext,
    ) -> FinalPackage:
        from ownpi.runtime.rollout.run_events import RunEventStore
        from ownpi.runtime.rollout.stage_business_events import emit_run_initialized
        from ownpi.schemas.workflow_plan import resolve_runtime_profile
        from ownpi.task_authoring import TaskSpecAuthoringRequest, TaskSpecAuthoringService

        run_dir = run_context.run_dir
        if resolve_runtime_profile(config_selection=self._user_config.runtime.default_profile).resolved is StageRuntimeProfile.TASK_LEVEL:
            owner = self.open_direct_session(run_context=run_context)
            try:
                return owner.submit(raw_user_message)
            finally:
                owner.close()
        with (run_dir / 'raw_user_message.txt').open(encoding='utf-8', newline='') as source:
            if source.read() != raw_user_message:
                raise FreshStartRejectedError('Fresh raw message does not match the canonical run artifact.')
        profile = resolve_runtime_profile(
            config_selection=self._user_config.runtime.default_profile,
        ).resolved
        with self._fresh_runtime_lock(self._runtime_lock_path(run_dir)):
            with self._bind_active_runtime_owner(run_dir, run_id=run_context.run_id, mode='fresh'):
                phase = 'task_spec_authoring_pending'
                client = None
                try:
                    self._check_fresh_start_control(run_dir)
                    with RunEventStore(run_dir, run_context.run_id) as events:
                        emit_run_initialized(
                            events, session_id=run_context.session_id, task_id=run_context.task_id,
                            execution_tier=execution_tier_for_profile(profile).value,
                            stage_runtime_profile=profile.value,
                        )
                    if profile == StageRuntimeProfile.RESEARCH_LEVEL:
                        self._check_fresh_start_control(run_dir)
                        config = self._user_config.get_llm_config(self._user_config.default_llm_name)
                        client = self._build_pre_stage_llm_client(config, run_dir=run_dir)
                        authored = TaskSpecAuthoringService(client).author(
                            request=TaskSpecAuthoringRequest(
                                request_id=f'{run_context.run_id}:task_authoring',
                                raw_user_message=raw_user_message, run_dir=run_dir,
                                project_root=self._project_root, run_id=run_context.run_id,
                                session_id=run_context.session_id, task_id=run_context.task_id,
                                provider=config.provider, model=config.model,
                            ),
                        )
                        self._check_fresh_start_control(run_dir)
                        if authored.status != 'completed':
                            raise RuntimeError(
                                f'Task Authoring failed: {authored.failure_class}: {authored.failure_message}'
                            )
                except Exception as error:
                    from ownpi.runtime.native_tools.loop_outcome import is_loop_cancellation

                    if is_loop_cancellation(error):
                        return self._pre_stage_cancelled_result(run_dir)
                    # Provider adapters may turn cancellation into a failed outcome.
                    try:
                        if isinstance(error, RuntimeControlRequested):
                            raise error
                        self._check_fresh_start_control(run_dir)
                    except RuntimeControlRequested as control:
                        request = control.request
                        status = 'paused_for_human' if request.action == 'pause' else 'stopped'
                        return FinalPackage(
                            status=status, task_id=run_context.task_id, current_stage=None,
                            final_decision=(GateDecision.CLARIFY_HUMAN.value
                                            if request.action == 'pause' else GateDecision.FAIL.value),
                            run_summary={'run_id': run_context.run_id, 'status': status,
                                         'pre_stage_phase': phase, 'control_action': request.action,
                                         'control_reason': request.reason,
                                         'control_request_id': request.request_id},
                            stage_runs=[], run_artifacts={'run_dir': str(run_dir)},
                        )
                    raise
                finally:
                    close = getattr(client, 'close', None)
                    if callable(close):
                        try:
                            close()
                        except Exception:
                            logging.getLogger(__name__).warning(
                                'Failed to close fresh pre-stage provider client', exc_info=True,
                            )
                from ownpi.llm.provider_retry import RetryCancelled

                try:
                    return self._execute_fresh_prepared_locked(
                        run_context=run_context,
                        stage_runtime_profile=profile, runtime_entry_context=RuntimeEntryContext(),
                    )
                except RetryCancelled:
                    return self._pre_stage_cancelled_result(run_dir)

    @staticmethod
    def _check_fresh_start_control(run_dir: Path) -> None:
        with runtime_control_lock(run_dir):
            request = read_runtime_control_request(run_dir)
            if request is not None:
                consumed = consume_runtime_control_request(run_dir, request)
                clear_runtime_control_request(run_dir, request_id=request.request_id)
                if consumed:
                    raise RuntimeControlRequested(request)

    def execute_fresh_prepared(
        self,
        *,
        raw_user_message: str,
        run_context: PreparedRunContext,
        stage_runtime_profile: StageRuntimeProfile,
        runtime_entry_context: RuntimeEntryContext | None = None,
    ) -> FinalPackage:
        if stage_runtime_profile is StageRuntimeProfile.TASK_LEVEL:
            owner = self.open_direct_session(
                run_context=run_context, runtime_entry_context=runtime_entry_context,
            )
            try:
                return owner.submit(raw_user_message)
            finally:
                owner.close()
        canonical_message = (run_context.run_dir / "raw_user_message.txt").read_text(
            encoding="utf-8"
        )
        if canonical_message != raw_user_message:
            raise FreshStartRejectedError(
                "Fresh admission raw message does not match the canonical run artifact."
            )
        run_dir = ensure_dir(run_context.run_dir)
        with self._fresh_runtime_lock(run_dir / ".runtime.lock"):
            with self._bind_active_runtime_owner(
                run_dir,
                run_id=run_context.run_id,
                mode="fresh",
            ):
                from ownpi.runtime.rollout.run_events import RunEventStore
                from ownpi.runtime.rollout.stage_business_events import emit_run_initialized

                with RunEventStore(run_dir, run_context.run_id) as events:
                    emit_run_initialized(
                        events, session_id=run_context.session_id, task_id=run_context.task_id,
                        execution_tier=execution_tier_for_profile(stage_runtime_profile).value,
                        stage_runtime_profile=stage_runtime_profile.value,
                    )
                locked_kwargs: dict[str, Any] = {
                    'run_context': run_context,
                    'stage_runtime_profile': stage_runtime_profile,
                    'runtime_entry_context': runtime_entry_context,
                }
                from ownpi.llm.provider_retry import RetryCancelled

                try:
                    return self._execute_fresh_prepared_locked(**locked_kwargs)
                except RetryCancelled:
                    return self._pre_stage_cancelled_result(run_dir)

    @staticmethod
    @contextmanager
    def _fresh_runtime_lock(path: Path) -> Iterator[None]:
        with ExitStack() as stack:
            try:
                stack.enter_context(filesystem_write_lock(path, blocking=False))
            except FileLockUnavailableError as error:
                raise FreshStartRejectedError(str(error)) from error
            yield

    def _pre_stage_cancelled_result(self, run_dir: Path) -> FinalPackage:
        from ownpi.controller.runtime_control_signal import RuntimeControlRequest

        identity = parse_session_run_dir(run_dir)
        with runtime_control_lock(run_dir):
            request = read_runtime_control_request(run_dir)
            if request is not None:
                consume_runtime_control_request(run_dir, request)
                clear_runtime_control_request(run_dir, request_id=request.request_id)
        request = request or RuntimeControlRequest(
            request_id=f'native-cancel:{run_dir.name}', action='interrupt',
            requested_at=now_utc_iso(), reason='native tool loop cancelled by user',
        )
        status = 'paused_for_human' if request.action == 'pause' else 'stopped'
        return FinalPackage(
            status=status, task_id=identity.task_id if identity is not None else '',
            current_stage=None,
            final_decision=GateDecision.CLARIFY_HUMAN.value if request.action == 'pause' else GateDecision.FAIL.value,
            run_summary={'run_id': run_dir.name, 'status': status, 'control_action': request.action,
                         'control_reason': request.reason, 'control_request_id': request.request_id},
            stage_runs=[], run_artifacts={'run_dir': str(run_dir)},
        )

    def _configure_review_policy(
        self,
        stage_runtime_profile: StageRuntimeProfile,
    ) -> None:
        policy = resolve_review_policy(
            self._user_config.runtime.review_profile,
            stage_runtime_profile=stage_runtime_profile,
        )
        self._effective_review_policy = policy

    def _execute_fresh_prepared_locked(
        self,
        *,
        run_context: PreparedRunContext,
        stage_runtime_profile: StageRuntimeProfile,
        runtime_entry_context: RuntimeEntryContext | None,
    ) -> FinalPackage:
        self._configure_review_policy(stage_runtime_profile)
        run_dir = ensure_dir(run_context.run_dir)
        entry_context = runtime_entry_context or RuntimeEntryContext()
        state_store, saved_files = self._initialize_fresh_workflow_state(
            run_context=run_context,
            run_dir=run_dir,
            stage_runtime_profile=stage_runtime_profile,
            runtime_entry_context=entry_context,
        )
        self._attach_execution_runtime_service(
            state_store=state_store,
            run_dir=run_dir,
        )
        state_store.state.workflow_state.scratchpad.update(
            run_context.scratchpad_payload()
        )
        if entry_context.session_start_hook_emitted:
            state_store.state.workflow_state.scratchpad["hook_session_started"] = True
        self._apply_prepared_run_context(
            state_store=state_store, run_context=run_context
        )
        persist_hook_delivery_batch(
            state_store.state.context_state,
            HookDeliveryBatch(
                model_context_fragments=entry_context.hook_prefix_fragments,
            ),
        )
        research_state = initialize_research_state(
            run_dir=run_dir,
            state=state_store.state,
        )
        if research_state is not None and str(research_state.path) not in saved_files:
            saved_files.append(str(research_state.path))
        workflow_state = state_store.state.workflow_state
        plan = workflow_state.run_workflow_plan
        if plan is None:
            raise RuntimeError("Fresh full-tier execution requires a workflow plan.")
        workflow_state.current_stage = plan.entry_stage
        return self._execute_fresh_staged(
            state_store=state_store,
            run_context=run_context,
            run_dir=run_dir,
            saved_files=saved_files,
            runtime_entry_context=entry_context,
        )

    def _initialize_fresh_workflow_state(
        self,
        *,
        run_context: PreparedRunContext,
        run_dir: Path,
        stage_runtime_profile: StageRuntimeProfile,
        runtime_entry_context: RuntimeEntryContext = RuntimeEntryContext(),
    ) -> tuple[WorkflowStateStore, list[str]]:
        direct_admission = execution_tier_for_profile(stage_runtime_profile) is ExecutionTier.DIRECT
        raw_message_path = run_dir / "raw_user_message.txt"
        task_spec_path = run_dir / "task_spec.md"
        run_workflow_plan: RunWorkflowPlan | None = None
        saved_files: list[str] = []
        if not direct_admission:
            if (
                not task_spec_path.is_file()
                or not task_spec_path.read_text(encoding="utf-8").strip()
            ):
                raise FileNotFoundError(
                    f"Completed full-tier task spec artifact not found: {task_spec_path}"
                )
            run_workflow_plan = RunWorkflowPlan((*ACTIVE_STAGE_ORDER, Stage.OUTPUT_SYNTHESIS))
        task_context = None if direct_admission else RuntimeTaskContext.from_paths(
            raw_user_message_path=raw_message_path,
            task_spec_path=(task_spec_path if task_spec_path.is_file() else None),
        )
        state_store = WorkflowStateStore.initialize(
            task_context=task_context,
            run_workflow_plan=run_workflow_plan,
            session_id=run_context.session_id,
            task_id=run_context.task_id,
            run_id=run_context.run_id,
            execution_tier=(
                ExecutionTier.DIRECT if direct_admission else ExecutionTier.FULL
            ),
            stage_runtime_profile=stage_runtime_profile,
            user_config=self._user_config,
            project_root=self._project_root,
            app_paths=self._app_paths,
        )
        from ownpi.runtime.rollout.lifecycle import attach_run_events
        from ownpi.runtime.rollout.stage_business_events import emit_run_initialized

        metadata = state_store.state.run_metadata
        workflow = state_store.state.workflow_state
        emit_run_initialized(
            attach_run_events(state_store, run_dir),
            session_id=str(metadata.session_id), task_id=str(metadata.task_id),
            execution_tier=workflow.execution_tier.value,
            stage_runtime_profile=workflow.stage_runtime_profile.value,
        )
        self._run_event_state_store = state_store
        return state_store, saved_files

    def _execute_fresh_staged(
        self,
        *,
        state_store: WorkflowStateStore,
        run_context: PreparedRunContext,
        run_dir: Path,
        saved_files: list[str],
        runtime_entry_context: RuntimeEntryContext,
    ) -> FinalPackage:
        workflow_state = state_store.state.workflow_state
        plan = workflow_state.run_workflow_plan
        if plan is None:
            raise RuntimeError("Fresh full-tier execution requires a workflow plan.")
        load_agent_modules()
        runtime_agents = self._build_agents()
        if workflow_state.current_stage != plan.entry_stage:
            raise RuntimeError("Fresh staged RuntimeState cursor is not at plan entry.")
        from ownpi.runtime.rollout.lifecycle import attach_run_events
        from ownpi.runtime.rollout.stage_business_events import emit_workflow_plan

        emit_workflow_plan(attach_run_events(state_store, run_dir), plan)
        self._run_event_state_store = state_store
        with (
            llm_trace_session(
                sink=lambda event: self._record_llm_trace(
                    state_store=state_store, event=event
                )
            ),
            bind_provider_request_context(
                ProviderRequestContext(
                    session_id=run_context.session_id,
                    task_id=run_context.task_id,
                    run_id=run_context.run_id,
                )
            ),
        ):
            self._emit_session_start_hook(
                state_store=state_store,
                run_dir=run_dir,
                source="workflow_fresh",
            )
            return self._execute_internal(
                runtime_agents=runtime_agents,
                run_dir=run_dir,
                state_store=state_store,
                saved_files=saved_files,
                runtime_entry_context=runtime_entry_context,
            )

    def _runtime_interrupt_requested(self, run_dir: Path) -> bool:
        checker = self._runtime_cancel_checker
        return bool(checker is not None and checker()) or runtime_interrupt_requested(
            run_dir
        )

    def _build_pre_stage_llm_client(
        self,
        config: LLMConfig,
        *,
        run_dir: Path,
    ) -> LLMClient:
        client = build_llm_client(config)
        client.set_cancel_checker(
            lambda: self._runtime_interrupt_requested(run_dir)
        )
        return client

    def _build_plan_mode_stage_entry_loop(
        self,
        request: Any,
    ) -> Any:
        from ownpi.runtime.native_tools.plan_mode.factory import (
            PlanModeStageEntryDependencies,
            build_plan_mode_stage_entry_loop,
        )

        return build_plan_mode_stage_entry_loop(
            request=request,
            dependencies=PlanModeStageEntryDependencies(
                user_config=self._user_config,
                tool_runtime=self._tool_runtime,
                hook_engine=self._hook_engine,
                app_paths=self._app_paths,
                process_owner_manager=self._process_owner_manager,
            ),
        )

    def resume_rollout(
        self, run_dir: Path, *, conversation_id: str | None = None,
        hook_prefix_fragments: tuple[HookModelContextFragment, ...] = (),
    ) -> FinalPackage:
        from ownpi.runtime.rollout.resume_read_session import ResumeReadSession

        resolved_run_dir = Path(run_dir).expanduser().resolve()
        if not resolved_run_dir.is_dir():
            raise FileNotFoundError(f'Run directory not found: {resolved_run_dir}')
        with filesystem_write_lock(self._runtime_lock_path(resolved_run_dir), blocking=False):
            parsed = parse_session_run_dir(resolved_run_dir)
            run_id = parsed.run_id if parsed is not None else resolved_run_dir.name
            with (self._bind_active_runtime_owner(
                      resolved_run_dir, run_id=run_id, mode='resume',
                      discard_pending_interrupt=True,
                  ),
                  ResumeReadSession() as reader):
                from ownpi.llm.provider_retry import RetryCancelled

                try:
                    return self._resume_rollout_locked(
                        resolved_run_dir, conversation_id=conversation_id,
                        hook_prefix_fragments=hook_prefix_fragments,
                        read_session=reader,
                    )
                except RetryCancelled:
                    return self._pre_stage_cancelled_result(resolved_run_dir)

    def resume_child_rollout(
        self, run_dir: Path, *, child_id: str, conversation_id: str,
        parent_conversation_id: str,
    ) -> Any:
        """Resume one admitted child without replaying the parent's batch invocation."""
        from ownpi.runtime.rollout.resume_read_session import ResumeReadSession

        root = Path(run_dir).expanduser().resolve()
        if not root.is_dir():
            raise FileNotFoundError(f'Run directory not found: {root}')
        with filesystem_write_lock(self._runtime_lock_path(root), blocking=False):
            parsed = parse_session_run_dir(root)
            run_id = parsed.run_id if parsed is not None else root.name
            with (self._bind_active_runtime_owner(root, run_id=run_id, mode='resume'),
                  ResumeReadSession() as reader):
                return self._resume_child_rollout_locked(
                    root, child_id=child_id, conversation_id=conversation_id,
                    parent_conversation_id=parent_conversation_id,
                    read_session=reader,
                )

    def _resume_child_rollout_locked(
        self, run_dir: Path, *, child_id: str, conversation_id: str,
        parent_conversation_id: str,
        read_session: Any = None,
    ) -> Any:
        from ownpi.runtime.rollout.lifecycle import attach_run_events
        from ownpi.runtime.rollout.resume import find_conversation_metadata, read_child_resume
        from ownpi.runtime.rollout.resume_state import WorkflowResumePaused, restore_child_parent_state
        from ownpi.subagent.resume import build_resumed_child_owner

        self._assert_session_run_dir(run_dir, operation='resume')
        facts = read_child_resume(
            run_dir, child_id=child_id, conversation_id=conversation_id,
            parent_conversation_id=parent_conversation_id,
            read_session=read_session,
        )
        self._require_supported_resume_identity(facts.stage_projection().run_identity)
        if self._subagent_owner is not None and facts.agent_relationships is not None:
            self._subagent_owner.install_historical_agents(facts.agent_relationships)
        if not self._user_config.runtime.subagent_enabled:
            raise WorkflowResumePaused('subagent execution is disabled by current configuration')
        state_store = restore_child_parent_state(
            facts, project_root=self._project_root,
            user_config=self._user_config, app_paths=self._app_paths,
        )
        parent = find_conversation_metadata(run_dir, conversation_id=parent_conversation_id, read_session=facts.read_session)
        actor_kind = parent.metadata.get('actor_kind')
        agent_config = next((agent for agent in self._user_config.agents.get_enabled_pipeline()
                             if agent.role == actor_kind), None)
        if agent_config is None:
            raise WorkflowResumePaused(f'current configuration has no {actor_kind} agent')
        llm_client = build_llm_client(self._user_config.get_llm_config(
            self._user_config.resolve_llm_name(agent_config.llm),
        ))
        attach_run_events(state_store, run_dir)
        self._run_event_state_store = state_store
        with bind_provider_request_context(ProviderRequestContext(
            session_id=state_store.state.run_metadata.session_id,
            task_id=state_store.state.run_metadata.task_id,
            run_id=state_store.state.run_metadata.run_id,
        )):
            self._attach_execution_runtime_service(state_store=state_store, run_dir=run_dir)
            owner = build_resumed_child_owner(
                facts, state_store=state_store, project_root=self._project_root,
                user_config=self._user_config, app_paths=self._app_paths,
                llm_client=llm_client, tool_runtime=self._tool_runtime,
                process_owner_manager=self._process_owner_manager,
                cancel_checker=self._runtime_cancel_checker,
            )
            self._subagent_owner = owner
            return owner.run_resumed_child(facts, cancel_checker=self._runtime_cancel_checker)

    def _resume_rollout_locked(
        self, run_dir: Path, *, conversation_id: str | None,
        hook_prefix_fragments: tuple[HookModelContextFragment, ...],
        read_session: Any = None,
    ) -> FinalPackage:
        from ownpi.runtime.rollout.lifecycle import attach_run_events
        from ownpi.runtime.rollout.resume import read_run_resume
        from ownpi.runtime.rollout.resume_state import WorkflowResumePaused, restore_workflow_state

        self._assert_session_run_dir(run_dir, operation='resume')
        facts = read_run_resume(run_dir, conversation_id=conversation_id, read_session=read_session)
        from ownpi.runtime.rollout.direct_resume import is_direct_session

        if is_direct_session(facts.events):
            from ownpi.controller.direct_session import DirectSessionOwner

            owner = DirectSessionOwner(self, run_dir=run_dir, facts=facts, locked=True)
            self._direct_session_owner = owner
            try:
                return owner.continue_request()
            finally:
                owner.close()
        projection = facts.stage_projection()
        identity = projection.run_identity
        self._require_supported_resume_identity(identity)
        if identity is not None and (identity['execution_tier'] == ExecutionTier.FULL.value
                                     and projection.workflow_plan is None):
            return self._resume_pre_stage(facts, identity, hook_prefix_fragments)
        from ownpi.runtime.rollout.resume_entry import resolve_stage_resume_entry

        resume_entry = resolve_stage_resume_entry(facts)
        facts = resume_entry.facts
        selected_conversation_id = (
            facts.conversation.conversation_id
            if facts.conversation is not None
            else facts.run_id
        )
        if self._subagent_owner is not None and facts.agent_relationships is not None:
            self._subagent_owner.install_historical_agents(facts.agent_relationships)
        cursor = resume_entry.cursor
        state_store = restore_workflow_state(
            resume_entry, project_root=self._project_root,
            user_config=self._user_config, app_paths=self._app_paths,
        )
        if cursor is not None and cursor.actor_kind not in {'core', 'reviewer'}:
            raise WorkflowResumePaused(
                f'{cursor.actor_kind} conversation requires its parent actor entry'
            )
        attach_run_events(state_store, run_dir)
        self._run_event_state_store = state_store
        interaction = state_store.state.workflow_state.pending_human_clarification
        if interaction is not None and interaction.status == 'pending':
            state_store.state.workflow_state.pause_reason = 'awaiting_user_interaction'
            return self._build_paused_interaction_result(
                state_store=state_store, run_dir=run_dir, saved_files=[],
                final_decision='paused_for_human',
            )
        workflow = state_store.state.workflow_state
        resumed_gate = resume_entry.gate
        if (workflow.execution_tier is ExecutionTier.FULL and interaction is None
                and resumed_gate is not None
                and resumed_gate.terminal_status in TERMINAL_RUN_STATUSES):
            # The restored gate already ends this attempt; no actor needs to resume.
            gate = resumed_gate
            return self._finalization_service.finalize_staged_run(
                state_store=state_store, run_dir=run_dir, saved_files=[],
                final_status=gate.terminal_status, final_decision=gate.decision,
                terminal_stage=workflow.current_stage,
            )
        self._configure_review_policy(workflow.stage_runtime_profile)
        execution_kind = 'stage_continuation'
        continuation = RuntimeContinuationDirective(
            execution_kind=execution_kind, source_record_id=selected_conversation_id,
            stage=(cursor.stage if cursor is not None
                   else state_store.state.workflow_state.current_stage.value),
            stage_attempt=(cursor.attempt if cursor is not None
                           else state_store.state.workflow_state.current_stage_attempt),
            continuation_index=(cursor.continuation_index if cursor is not None else 0),
            query_chain_id=(cursor.query_chain_id if cursor is not None
                            else state_store.state.workflow_state.current_query_chain_id or ''),
            query_chain_depth=(cursor.query_chain_depth if cursor is not None
                               else state_store.state.workflow_state.current_query_chain_depth),
        )
        if facts.conversation is None and state_store.state.workflow_state.execution_tier is ExecutionTier.FULL:
            continuation = None
        entry_context = RuntimeEntryContext(
            origin='resume', source_resume_id=selected_conversation_id,
            continuation_kind=execution_kind,
            hook_prefix_fragments=hook_prefix_fragments,
        )
        interaction_kwargs = {}
        if interaction is not None:
            continuation = None
            if interaction.actor_kind == 'plan_mode':
                interaction_kwargs['resume_clarification_session'] = interaction
            elif interaction.origin_kind == 'controller':
                self._activate_controller_clarification_handoff(
                    state_store=state_store, session=interaction, answer_artifact_path=None,
                )
                interaction_kwargs['resume_controller_clarification_session'] = interaction
            else:
                interaction_kwargs['resume_clarification_session'] = interaction
        load_agent_modules()
        runtime_agents = self._build_agents()
        with (
            llm_trace_session(
                sink=lambda event: self._record_llm_trace(state_store=state_store, event=event)
            ),
            bind_provider_request_context(ProviderRequestContext(
                session_id=state_store.state.run_metadata.session_id,
                task_id=state_store.state.run_metadata.task_id,
                run_id=state_store.state.run_metadata.run_id,
            )),
        ):
            self._attach_execution_runtime_service(state_store=state_store, run_dir=run_dir)
            return self._execute_internal(
                runtime_agents=runtime_agents, run_dir=run_dir, state_store=state_store,
                saved_files=[],
                runtime_continuation=continuation, runtime_entry_context=entry_context,
                stage_resume_entry=resume_entry,
                **interaction_kwargs,
            )

    @staticmethod
    def _require_supported_resume_identity(identity: dict[str, str] | None) -> None:
        from ownpi.runtime.rollout.resume_state import WorkflowResumePaused

        if identity is not None and identity['execution_tier'] == ExecutionTier.DIRECT.value:
            raise WorkflowResumePaused(
                'Legacy Direct execution is no longer supported; '
                'historical runs remain available for inspection and debug trace.'
            )
        if identity is not None and (
            identity['execution_tier'] == ExecutionTier.FULL.value
            and identity['stage_runtime_profile'] == StageRuntimeProfile.TASK_LEVEL.value
        ):
            raise WorkflowResumePaused(
                'Task Level staged execution is no longer supported; '
                'historical runs remain available for inspection and debug trace.'
            )

    def _resume_pre_stage(
        self, facts: 'RunResumeFacts', identity: dict[str, str],
        hook_prefix_fragments: tuple[HookModelContextFragment, ...],
    ) -> FinalPackage:
        from ownpi.runtime.rollout.resume import _run_conversation_inventory
        from ownpi.runtime.rollout.resume_state import WorkflowResumePaused
        from ownpi.task_authoring import TaskSpecAuthoringRequest, TaskSpecAuthoringService

        run_dir = facts.run_root
        raw_message = facts.read_canonical_text('raw_user_message.txt')
        run_context = PreparedRunContext(
            session_id=identity['session_id'], task_id=identity['task_id'],
            run_id=facts.run_id, run_dir=run_dir,
        )
        def actor_replay(kind: str) -> 'ResumedConversation | None':
            conversations, failures = _run_conversation_inventory(
                run_dir, read_session=facts.read_session,
            )
            matching = [item for item in conversations
                        if item.metadata.get('run_id') == facts.run_id
                        and item.metadata.get('actor_kind') == kind
                        and item.metadata.get('stage') == kind
                        and item.metadata.get('attempt') == 1]
            for path, metadata, reason in failures:
                if metadata is not None and isinstance(metadata.get('run_id'), str) \
                        and metadata['run_id'] and metadata['run_id'] != facts.run_id:
                    continue
                if metadata is not None and (
                    isinstance(metadata.get('actor_kind'), str)
                    and metadata['actor_kind'] != kind
                    or isinstance(metadata.get('stage'), str)
                    and metadata['stage'] != kind
                    or type(metadata.get('attempt')) is int and metadata['attempt'] != 1
                ):
                    continue
                raise WorkflowResumePaused(
                    f'{kind} rollout cannot be read: {path}: {reason}'
                )
            if len(matching) > 1:
                raise WorkflowResumePaused(f'{kind} has ambiguous rollout history')
            return facts.read_session.replay(matching[0].path) if matching else None

        task_spec = run_dir / 'task_spec.md'
        if not task_spec.is_file() or not task_spec.read_text(encoding='utf-8').strip():
            config = self._user_config.get_llm_config(self._user_config.default_llm_name)
            outcome = TaskSpecAuthoringService(
                self._build_pre_stage_llm_client(config, run_dir=run_dir)
            ).author(
                request=TaskSpecAuthoringRequest(
                    request_id=f'{facts.run_id}:task_authoring', raw_user_message=raw_message,
                    run_dir=run_dir, project_root=self._project_root, run_id=facts.run_id,
                    session_id=identity['session_id'], task_id=identity['task_id'],
                    provider=config.provider, model=config.model,
                    hook_prefix_fragments=hook_prefix_fragments,
                ),
                replay=actor_replay('task_authoring'),
            )
            if outcome.status != 'completed':
                raise WorkflowResumePaused(
                    f'Task Authoring could not continue: {outcome.failure_class}: {outcome.failure_message}'
                )
        return self._execute_fresh_prepared_locked(
            run_context=run_context,
            stage_runtime_profile=StageRuntimeProfile(identity['stage_runtime_profile']),
            runtime_entry_context=RuntimeEntryContext(
                origin='resume', hook_prefix_fragments=hook_prefix_fragments,
            ),
        )

    def _runtime_lock_path(self, run_dir: Path) -> Path:
        if parse_session_run_dir(run_dir) is not None:
            return run_dir / ".runtime.lock"
        identity = hashlib.sha256(str(run_dir).encode("utf-8")).hexdigest()
        return self._app_paths.app_home / "locks" / "runtime" / f"{identity}.lock"

    @contextmanager
    def _bind_active_runtime_owner(
        self,
        run_dir: Path,
        *,
        run_id: str,
        mode: Literal["fresh", "resume"],
        discard_pending_interrupt: bool = False,
    ) -> Iterator[None]:
        with ExitStack() as owner_context:
            if discard_pending_interrupt:
                # The caller holds the run lock; serialize cleanup with new stop requests.
                with runtime_control_lock(run_dir):
                    request = read_runtime_control_request(run_dir)
                    if request is not None and request.action == 'interrupt':
                        consume_runtime_control_request(run_dir, request)
                        clear_runtime_control_request(
                            run_dir, request_id=request.request_id,
                        )
                    lease = owner_context.enter_context(
                        bind_active_runtime_owner(run_dir, run_id=run_id, mode=mode),
                    )
            else:
                lease = owner_context.enter_context(
                    bind_active_runtime_owner(run_dir, run_id=run_id, mode=mode),
                )
            previous = self._runtime_cancel_checker
            self._runtime_cancel_checker = lease.cancel_requested
            try:
                yield
            finally:
                self._runtime_cancel_checker = previous
                event_state = getattr(self, '_run_event_state_store', None)
                event_store = getattr(event_state, 'run_event_store', None)
                if event_store is not None and not event_store.closed:
                    try:
                        self._shutdown_execution_runtime(event_state, terminal=False)
                    except Exception:
                        logging.getLogger(__name__).warning(
                            'Run event cleanup remains pending', exc_info=True,
                        )

    def answer_pending_interaction(
        self,
        run_dir: Path,
        *,
        answers: dict[str, Any],
        session_id: str | None = None,
    ) -> FinalPackage:
        resolved_run_dir = Path(run_dir).expanduser().resolve()
        self._assert_session_run_dir(resolved_run_dir, operation="answer interaction")
        with filesystem_write_lock(
            self._runtime_lock_path(resolved_run_dir),
            blocking=False,
        ):
            return self._interaction_answer_service.answer_pending_interaction(
                resolved_run_dir,
                answers=answers,
                session_id=session_id,
            )

    def answer_tool_permission(
        self,
        run_dir: Path,
        *,
        decision: str,
        scope: str = "once",
        session_id: str = "",
    ) -> FinalPackage:
        resolved_run_dir = Path(run_dir).expanduser().resolve()
        self._assert_session_run_dir(resolved_run_dir, operation="answer permission")
        from ownpi.runtime.interactions.live_approval import answer_live_approval

        session = answer_live_approval(
            resolved_run_dir, decision=decision, scope=scope, session_id=session_id,
        )
        if session is not None:
            return FinalPackage(
                status='in_progress', task_id='', current_stage=session.stage,
                final_decision='permission_answered',
                run_summary={'permission_session': session.session_id, 'decision': decision},
                stage_runs=[], run_artifacts={},
            )
        raise RuntimeError(
            "No live tool approval is waiting. Resume the run first; "
            "a stopped invocation cannot receive an approval answer."
        )

    def _build_paused_interaction_result(
        self,
        *,
        state_store: WorkflowStateStore,
        run_dir: Path,
        saved_files: list[str],
        stage_runs: list[StageRunSummary] | None = None,
        final_decision: GateDecision | str,
    ) -> FinalPackage:
        return self._finalization_service.build_paused_package(
            state_store=state_store,
            run_dir=run_dir,
            saved_files=saved_files,
            stage_runs=stage_runs,
            final_decision=final_decision,
            terminal_stage=state_store.state.workflow_state.current_stage,
        )

    def _execute_internal(
        self,
        *,
        runtime_agents: dict[str, tuple[AgentInstanceConfig, Any]],
        run_dir: Path,
        state_store: WorkflowStateStore,
        saved_files: list[str],
        runtime_continuation: RuntimeContinuationDirective | None = None,
        runtime_entry_context: RuntimeEntryContext | None = None,
        stage_resume_entry: StageResumeEntry | None = None,
        resume_clarification_session: HumanClarificationSession | None = None,
        resume_controller_clarification_session: HumanClarificationSession
        | None = None,
    ) -> FinalPackage:
        return self._staged_runtime_loop.execute(
            runtime_agents=runtime_agents,
            run_dir=run_dir,
            state_store=state_store,
            saved_files=saved_files,
            runtime_continuation=runtime_continuation,
            runtime_entry_context=runtime_entry_context,
            stage_resume_entry=stage_resume_entry,
            resume_clarification_session=resume_clarification_session,
            resume_controller_clarification_session=resume_controller_clarification_session,
        )

    def _pause_for_human_clarification(
        self,
        *,
        state_store: WorkflowStateStore,
        run_dir: Path,
        saved_files: list[str],
        stage: Stage,
        attempt: int,
        continuation_index: int,
        continuation_kind: str,
        source: str,
        reason: str,
        questions: list[HumanClarificationQuestion],
        actor_kind: str = 'core',
        actor_id: str | None = None,
        conversation_lane: str = '',
    ) -> HumanClarificationSession:
        existing = state_store.current_human_clarification_session()
        if existing is not None and existing.status == "pending":
            return existing
        if existing is not None and existing.status == "answered":
            state_store.mark_human_clarification_resumed(
                session_id=existing.session_id,
                resumed_at=now_utc_iso(),
            )
        session_id = f"{stage.value}-a{attempt}-{int(time.time() * 1000)}"
        session = HumanClarificationSession(
            session_id=session_id,
            stage=stage.value,
            attempt=attempt,
            query_chain_id=str(
                state_store.state.workflow_state.current_query_chain_id or ""
            ),
            query_chain_depth=int(
                state_store.state.workflow_state.current_query_chain_depth or 0
            ),
            continuation_index=continuation_index,
            continuation_kind=continuation_kind,
            status="pending",
            questions=tuple(questions[:MAX_HUMAN_CLARIFICATION_QUESTIONS]),
            answers={},
            reason=reason,
            source=source,
            origin_kind="controller",
            actor_kind=actor_kind,
            actor_id=actor_id,
            conversation_lane=conversation_lane,
            interaction_request_id=session_id,
            created_at=now_utc_iso(),
        )
        self._clear_controller_clarification_handoff(state_store=state_store)
        state_store.record_pending_human_clarification_session(session)
        return session

    @staticmethod
    def _materialize_consumed_resume_session(
        *,
        state_store: WorkflowStateStore,
        session: HumanClarificationSession | None,
    ) -> None:
        if session is None:
            return
        resumed_at = now_utc_iso()
        state_store.mark_human_clarification_resumed(
            session_id=session.session_id,
            resumed_at=resumed_at,
        )


    @staticmethod
    def _active_controller_clarification_handoff(
        *,
        state_store: WorkflowStateStore,
        stage: Stage | str | None = None,
        attempt: int | None = None,
    ) -> dict[str, Any] | None:
        stage_value = stage.value if isinstance(stage, Stage) else stage
        return active_controller_clarification_handoff(
            state_store.state.workflow_state.scratchpad.get(
                CONTROLLER_CLARIFICATION_HANDOFF_KEY
            ),
            stage=stage_value,
            attempt=attempt,
        )

    @staticmethod
    def _clear_controller_clarification_handoff(
        *,
        state_store: WorkflowStateStore,
        consumed_by_continuation_index: int | None = None,
    ) -> None:
        handoff = state_store.current_controller_clarification_handoff()
        if handoff is not None:
            state_store.mark_controller_clarification_handoff_result_consumed(
                session_id=str(handoff.get("session_id", "") or ""),
                consumed_by_continuation_index=(
                    int(consumed_by_continuation_index)
                    if consumed_by_continuation_index is not None
                    else int(
                        state_store.state.workflow_state.scratchpad.get(
                            "continuation_index", 0
                        )
                        or 0
                    )
                ),
            )
        state_store.clear_controller_clarification_handoff()

    def _activate_controller_clarification_handoff(
        self,
        *,
        state_store: WorkflowStateStore,
        session: HumanClarificationSession,
        answer_artifact_path: str | None,
    ) -> None:
        payload = build_controller_clarification_handoff(
            session,
            answer_artifact_path=answer_artifact_path,
        )
        state_store.set_controller_clarification_handoff(payload)
        state_store.record_tool_result(
            self._build_controller_clarification_tool_result(
                session=session,
                handoff_payload=payload,
                answer_artifact_path=answer_artifact_path,
            ),
            summary_override=(
                f"controller clarification answered: "
                f"{human_clarification_answer_summary(session) or session.reason}"
            ),
        )
        state_store.state.workflow_state.clarification_resume_count += 1
        summary = human_clarification_answer_summary(session)
        if summary:
            state_store.record_tool_observation(
                ToolObservation(
                    observation_id=f"controller_clarification_handoff:{session.session_id}",
                    source_type="runtime_guidance",
                    source_name="controller_clarification_handoff",
                    stage=session.stage,
                    attempt=session.attempt,
                    query_chain_id=session.query_chain_id,
                    query_chain_depth=session.query_chain_depth,
                    continuation_index=session.continuation_index,
                    summary=(
                        f"controller clarification answered: {summary}. "
                        "Use this answer first; if more input is still required, ask only for the remaining "
                        "missing field(s) and do not repeat the previous clarification verbatim."
                    ),
                    created_at=now_utc_iso(),
                )
            )
        self._emit(
            f"[{session.stage}:attempt{session.attempt}] controller_clarification_handoff "
            f"session={session.session_id} continuation={session.continuation_index}"
        )

    @staticmethod
    def _build_controller_clarification_tool_result(
        *,
        session: HumanClarificationSession,
        handoff_payload: dict[str, Any],
        answer_artifact_path: str | None,
    ) -> ToolResult:
        interaction_request_id = (
            session.interaction_request_id
            or session.tool_request_id
            or session.session_id
        )
        answers: list[dict[str, Any]] = []
        for question in session.questions:
            payload = normalize_human_answer_payload(
                session.answers.get(question.question_id)
            )
            answer_text = human_answer_text(payload)
            if not answer_text:
                continue
            answers.append(
                {
                    "interaction_request_id": interaction_request_id,
                    "question_id": question.question_id,
                    "question_key": build_question_key(
                        interaction_request_id, question.question_id
                    ),
                    "selected_labels": list(payload.get("selected_labels", [])),
                    "free_text": payload.get("free_text"),
                    "answer_text": answer_text,
                }
            )
        answer_summary = human_clarification_answer_summary(session)
        consumption_payload = {
            "consumption_status": "pending",
            "consumed_at": None,
            "consumed_by_continuation_index": None,
            "available_to_continuation_index": int(session.continuation_index or 0),
        }
        result_id = f"controller_clarification_handoff:{session.session_id}"
        return ToolResult(
            ok=True,
            tool_name="human_clarification",
            data={
                "kind": "human_clarification",
                "origin_kind": "controller",
                "session_id": session.session_id,
                "interaction_request_id": interaction_request_id,
                "request_id": interaction_request_id,
                "result_id": result_id,
                "reason": session.reason,
                "answer_text": answer_summary,
                "question_answer_pairs": list(
                    handoff_payload.get("question_answer_pairs", [])
                ),
                "questions": [question.to_dict() for question in session.questions],
                "answers": answers,
                "answer_artifact_path": answer_artifact_path,
                "instruction": str(handoff_payload.get("instruction", "") or ""),
                **consumption_payload,
            },
            stdout=(
                "User has answered controller clarification: "
                f"{answer_summary}. You can now continue with the user answers in mind."
            ),
            metadata={
                "stage": session.stage,
                "attempt": int(session.attempt or 0),
                "continuation_index": int(session.continuation_index or 0),
                "query_chain_id": session.query_chain_id,
                "query_chain_depth": int(session.query_chain_depth or 0),
                "kind": "human_clarification",
                "origin_kind": "controller",
                "same_boundary": True,
                "session_id": session.session_id,
                "interaction_request_id": interaction_request_id,
                "request_id": interaction_request_id,
                "result_id": result_id,
                "answer_artifact_path": answer_artifact_path,
                "instruction": str(handoff_payload.get("instruction", "") or ""),
                **consumption_payload,
            },
        )

    @staticmethod
    def _effective_clarified_path(
        *,
        state_store: WorkflowStateStore,
        stage: Stage,
        attempt: int,
        fallback_path: str,
        candidate_question_ids: tuple[str, ...],
        allow_previous_attempt: bool = False,
    ) -> str:
        pending = state_store.current_human_clarification_session()
        if (
            pending is not None
            and pending.stage == stage.value
            and pending.attempt == attempt
        ):
            for question_id in candidate_question_ids:
                value = human_answer_text(pending.answers.get(question_id))
                if value:
                    return value
        for session in reversed(
            state_store.state.workflow_state.human_clarification_history
        ):
            if session.stage != stage.value or session.attempt != attempt:
                continue
            for question_id in candidate_question_ids:
                value = human_answer_text(session.answers.get(question_id))
                if value:
                    return value
        if allow_previous_attempt:
            for session in reversed(
                state_store.state.workflow_state.human_clarification_history
            ):
                if session.stage != stage.value or int(session.attempt or 0) > attempt:
                    continue
                for question_id in candidate_question_ids:
                    value = human_answer_text(session.answers.get(question_id))
                    if value:
                        return value
        return fallback_path

    @staticmethod
    def _is_continuation_runtime_continuation_kind(continuation_kind: str | None) -> bool:
        return str(continuation_kind or "") in {
            "same_attempt_runtime",
            "continuation_runtime",
        }

    def _run_agent_streaming_turn(
        self,
        *,
        state_store: WorkflowStateStore,
        run_dir: Path | None = None,
        saved_files: list[str] | None = None,
        stage: Stage,
        attempt: int,
        agent_config: AgentInstanceConfig,
        agent: Any,
        operation: str,
        fallback_to_run: bool,
        label: str,
        native_loop_continuation: Any | None = None,
        current_request: str | None = None,
    ):
        streaming_started = False
        fallback_consumes_override = False
        try:
            attempt_agent = self._agent_with_runtime_llm_overrides(
                base_agent=agent,
                agent_config=agent_config,
                state_store=state_store,
            )
            stream_method = getattr(attempt_agent, "stream", None)
            if not callable(stream_method):
                if fallback_to_run:
                    fallback_consumes_override = True
                    invocation_context = self._build_agent_runtime_invocation_context(
                        state_store=state_store,
                        run_dir=run_dir or self._fallback_agent_run_dir(state_store),
                        saved_files=saved_files if saved_files is not None else [],
                        stage=stage,
                        attempt=attempt,
                        operation=operation,
                        actor_kind=agent_config.role,
                        native_loop_continuation=native_loop_continuation,
                    )
                    previous_context = self._set_agent_runtime_invocation_context(
                        attempt_agent,
                        invocation_context,
                    )
                    try:
                        return attempt_agent.run(
                            state_store.build_agent_input(
                                agent_config, operation=operation, current_request=current_request,
                            )
                        )
                    finally:
                        self._set_agent_runtime_invocation_context(
                            attempt_agent, previous_context
                        )
                return None
            start_context_prefetch(
                state_store.state,
                self._app_paths,
                self._project_root,
            )
            agent_input = state_store.build_agent_input(
                agent_config, operation=operation, current_request=current_request,
            )
            self._emit_runtime_status(
                state_store=state_store,
                event_kind="llm_stream_started",
                component=agent_config.id,
                operation=operation,
                provider_inflight=True,
            )
            streaming_started = True
            try:
                result = None
                invocation_context = self._build_agent_runtime_invocation_context(
                    state_store=state_store,
                    run_dir=run_dir or self._fallback_agent_run_dir(state_store),
                    saved_files=saved_files if saved_files is not None else [],
                    stage=stage,
                    attempt=attempt,
                    operation=operation,
                    actor_kind=agent_config.role,
                    native_loop_continuation=native_loop_continuation,
                )
                previous_context = self._set_agent_runtime_invocation_context(
                    attempt_agent,
                    invocation_context,
                )
                try:
                    for event in stream_method(agent_input):
                        self._handle_agent_stream_event(
                            state_store=state_store,
                            stage=stage,
                            attempt=attempt,
                            agent_config=agent_config,
                            operation=operation,
                            event=event,
                        )
                        candidate = getattr(event, "agent_output", None)
                        if candidate is not None:
                            result = candidate
                finally:
                    self._set_agent_runtime_invocation_context(
                        attempt_agent,
                        previous_context,
                    )
                if result is None:
                    raise RuntimeError(
                        f"streaming agent operation {operation!r} completed without AgentOutput."
                    )
                self._emit_runtime_status(
                    state_store=state_store,
                    event_kind="llm_stream_completed",
                    component=agent_config.id,
                    operation=operation,
                    provider_inflight=False,
                )
                return result
            except Exception:
                self._emit_runtime_status(
                    state_store=state_store,
                    event_kind="llm_stream_failed",
                    component=agent_config.id,
                    operation=operation,
                    provider_inflight=False,
                )
                raise
        finally:
            consume_context_prefetch(state_store.state, max_wait_ms=0)
            if streaming_started or fallback_consumes_override:
                self._consume_temporary_llm_overrides(state_store)

    def _build_agent_runtime_invocation_context(
        self,
        *,
        state_store: WorkflowStateStore,
        run_dir: Path,
        saved_files: list[str],
        stage: Stage,
        attempt: int,
        operation: str,
        actor_kind: str = 'core',
        native_loop_continuation: Any | None = None,
    ) -> AgentRuntimeInvocationContext:
        runtime_config = getattr(self._user_config, "runtime", self._user_config)
        subagent_owner = self._subagent_owner
        if bool(getattr(runtime_config, "subagent_enabled", False)):
            from ownpi.subagent.caller import owner_for_invocation

            subagent_owner = owner_for_invocation(
                subagent_owner, runtime_config=runtime_config, run_dir=run_dir,
            )
            self._subagent_owner = subagent_owner
        if subagent_owner is None:
            from ownpi.subagent import SubagentOwner

            subagent_owner = SubagentOwner.from_runtime_config(
                runtime_config,
                adapter=getattr(self, "_subagent_adapter", None),
            )
        return AgentRuntimeInvocationContext(
            state_store=state_store,
            run_dir=run_dir,
            saved_files=saved_files,
            stage=stage,
            attempt=attempt,
            operation=operation,
            tool_runtime=self._tool_runtime,
            user_config=self._user_config,
            app_paths=self._app_paths,
            hook_engine=self._hook_engine,
            execution_runtime_service=state_store.execution_runtime_service,
            process_owner_manager=self._process_owner_manager,
            cancel_checker=self._runtime_cancel_checker,
            subagent_owner=subagent_owner,
            native_loop_continuation=native_loop_continuation,
            resume_conversation_id=self._take_business_resume_conversation(
                state_store, stage=stage.value, attempt=attempt, actor_kind=actor_kind,
            ),
            resume_read_session=getattr(
                state_store.state.workflow_state.scratchpad.get('rollout_resume_facts'),
                'read_session', None,
            ),
        )

    @staticmethod
    def _take_business_resume_conversation(state_store, *, stage: str, attempt: int, actor_kind: str):
        selected = state_store.take_resume_conversation(stage=stage, attempt=attempt, actor_kind=actor_kind)
        return selected

    def _fallback_agent_run_dir(self, state_store: WorkflowStateStore) -> Path:
        scratchpad = state_store.state.workflow_state.scratchpad
        raw_run_dir = scratchpad.get("run_dir")
        if isinstance(raw_run_dir, str) and raw_run_dir.strip():
            return Path(raw_run_dir).expanduser()
        output_dir = self._resolve_runtime_path(self._user_config.runtime.output_dir)
        return output_dir

    def _attach_execution_runtime_service(
        self,
        *,
        state_store: WorkflowStateStore,
        run_dir: Path,
    ) -> ExecutionRuntimeService:
        from ownpi.runtime.rollout.lifecycle import attach_run_events

        bound = state_store.execution_runtime_service
        existing = self._execution_runtime_service
        if bound is not None:
            if existing is not None and existing is not bound:
                self._replace_execution_runtime(existing)
            attach_run_events(state_store, run_dir)
            self._run_event_state_store = state_store
            self._execution_runtime_service = bound
            return bound
        if existing is not None:
            self._replace_execution_runtime(existing)
        attach_run_events(state_store, run_dir)
        self._run_event_state_store = state_store
        self._attach_process_owner_manager(state_store)
        service = ExecutionRuntimeService(
            state_store=state_store,
            run_dir=run_dir,
            execution_dispatch=ExecutionToolDispatcher(
                tool_runtime=self._tool_runtime,
                state_store=state_store,
                run_dir=run_dir,
                project_root=self._project_root,
                user_config=self._user_config,
                hook_engine=self._hook_engine,
                app_paths=self._app_paths,
                process_owner_manager=self._process_owner_manager,
            ),
            process_release_port=self._process_owner_manager,
        )
        state_store.bind_execution_runtime_service(service)
        self._execution_runtime_service = service
        try:
            service.start()
        except Exception:
            self._shutdown_execution_service(
                service=service,
                state_store=state_store,
                mode='startup_failure',
            )
            raise
        return service

    def _replace_execution_runtime(
        self,
        service: ExecutionRuntimeService,
    ) -> None:
        self._shutdown_execution_service(
            service=service,
            state_store=None,
            mode='replacement',
        )
        if self._execution_runtime_service is service:
            raise BlockingIOError('Previous execution runtime persistence is still unconfirmed')

    def _shutdown_execution_runtime(
        self,
        state_store: WorkflowStateStore,
        terminal: bool,
    ) -> None:
        service = state_store.execution_runtime_service
        if service is None:
            from ownpi.runtime.rollout.lifecycle import close_run_events

            # Terminal finalization closes the writer after recording its outcome.
            if not terminal:
                close_run_events(state_store)
            return
        self._shutdown_execution_service(
            service=service,
            state_store=state_store,
            mode='terminal' if terminal else 'pause',
        )

    def _shutdown_execution_service(
        self,
        *,
        service: ExecutionRuntimeService,
        state_store: WorkflowStateStore | None,
        mode: Literal['pause', 'terminal', 'replacement', 'startup_failure'],
    ) -> None:
        from ownpi.runtime.execution.shutdown import stop_execution_for_cleanup

        if not stop_execution_for_cleanup(service):
            return
        if mode != 'pause':
            subagent_owner = getattr(self, '_subagent_owner', None)
            shutdown_subagents = getattr(subagent_owner, 'shutdown_report', None)
            if callable(shutdown_subagents):
                report = shutdown_subagents(timeout=5.0)
                if not report.ok:
                    logging.getLogger(__name__).warning('Child shutdown incomplete: %s', report.to_dict())
                if not report.batch_drained or report.timed_out or report.submit_failed:
                    return
        if mode == 'pause':
            if state_store is not None:
                from ownpi.runtime.rollout.lifecycle import close_run_events

                owner = getattr(self, '_subagent_owner', None)
                close_if_idle = getattr(owner, 'close_run_events_if_idle', None)
                confirmed = (close_if_idle(lambda: close_run_events(state_store))
                             if callable(close_if_idle) else close_run_events(state_store))
                if not confirmed:
                    logging.getLogger(__name__).warning('Run persistence pause remains unconfirmed')
            return
        manager = self._process_owner_manager
        if service.mark_closed() is False:
            return
        event_state_store = state_store or getattr(service, '_state_store', None)
        if event_state_store is not None and mode != 'terminal':
            from ownpi.runtime.rollout.lifecycle import close_run_events

            if not close_run_events(event_state_store):
                return
        if manager is not None:
            manager.close(drain=False)
        if state_store is not None:
            state_store.bind_execution_runtime_service(None)
        if self._execution_runtime_service is service:
            self._execution_runtime_service = None
        if self._process_owner_manager is manager:
            self._process_owner_manager = None

    def _attach_process_owner_manager(
        self,
        state_store: WorkflowStateStore,
    ) -> ProcessOwnerManager:
        bindings = state_store.state.process_owner_bindings
        current = self._process_owner_manager
        if current is not None and current.bindings is bindings:
            return current
        if current is not None:
            current.close(drain=False)
        configured = self._user_config.runtime.process_owner
        manager = ProcessOwnerManager(
            bindings=bindings,
            owner_daemon_idle_grace_seconds=(
                configured.owner_daemon_idle_grace_seconds
            ),
            notification_handler=self._handle_process_owner_notification,
        )
        self._process_owner_manager = manager
        return manager

    def _handle_process_owner_notification(
        self,
        method: str,
        params: dict[str, Any],
    ) -> None:
        if method not in {
            'process/exited',
            'process/closed',
            'session/expired',
            'transport/failed',
            'transport/recovered',
        }:
            return
        service = self._execution_runtime_service
        if service is None:
            return
        owner_id = str(params.get('owner_id', '') or '')
        process_id = str(params.get('process_id', '') or '')
        if not owner_id:
            return
        if method in {'session/expired', 'transport/failed'}:
            service.commit_owner_lost(owner_id=owner_id, process_id=process_id)
            return
        service.notify_owner_event(
            owner_id=owner_id,
            process_id=process_id,
            method=method,
        )

    @staticmethod
    def _set_agent_runtime_invocation_context(
        agent: Any,
        context: AgentRuntimeInvocationContext | None,
    ) -> AgentRuntimeInvocationContext | None:
        setter = getattr(agent, "set_runtime_invocation_context", None)
        if callable(setter):
            return setter(context)
        return None

    def _run_agent_operation(
        self,
        *,
        state_store: WorkflowStateStore,
        run_dir: Path,
        saved_files: list[str],
        stage: Stage,
        attempt: int,
        label: str,
        agent_config: AgentInstanceConfig,
        agent: Any,
        operation: str,
        filename: str,
        event_name: str,
        native_loop_continuation: Any | None = None,
        current_request: str | None = None,
    ):
        try:
            start_context_prefetch(
                state_store.state,
                self._app_paths,
                self._project_root,
            )
            agent_input = state_store.build_agent_input(
                agent_config, operation=operation, current_request=current_request,
            )
            self._emit_runtime_status(
                state_store=state_store,
                event_kind="context_usage",
                component=agent_config.id,
                operation=operation,
            )
            self._emit(
                f"[{label}] running agent={agent_config.id} op={operation}"
            )
            agent = self._agent_with_runtime_llm_overrides(
                base_agent=agent,
                agent_config=agent_config,
                state_store=state_store,
            )
            invocation_context = self._build_agent_runtime_invocation_context(
                state_store=state_store,
                run_dir=run_dir,
                saved_files=saved_files,
                stage=stage,
                attempt=attempt,
                operation=operation,
                actor_kind='reviewer' if operation in {'reviewer_review', 'direct_critic_reviewer'} else 'core',
                native_loop_continuation=native_loop_continuation,
            )
            previous_context = self._set_agent_runtime_invocation_context(
                agent,
                invocation_context,
            )
            try:
                result = agent.run(agent_input)
            finally:
                self._set_agent_runtime_invocation_context(
                    agent, previous_context
                )
        finally:
            consume_context_prefetch(state_store.state, max_wait_ms=0)
            self._consume_temporary_llm_overrides(state_store)
        return self._persist_agent_operation_result(
            state_store=state_store,
            run_dir=run_dir,
            saved_files=saved_files,
            stage=stage,
            attempt=attempt,
            label=label,
            agent_config=agent_config,
            operation=operation,
            filename=filename,
            event_name=event_name,
            result=result,
        )

    def _stream_agent_operation(
        self,
        *,
        state_store: WorkflowStateStore,
        run_dir: Path,
        saved_files: list[str],
        stage: Stage,
        attempt: int,
        label: str,
        agent_config: AgentInstanceConfig,
        agent: Any,
        operation: str,
        filename: str,
        event_name: str,
        native_loop_continuation: Any | None = None,
        current_request: str | None = None,
    ):
        if operation not in {"analysis_core_round", 'direct_critic_core'}:
            return None
        result = self._run_agent_streaming_turn(
            state_store=state_store,
            run_dir=run_dir,
            saved_files=saved_files,
            stage=stage,
            attempt=attempt,
            agent_config=agent_config,
            agent=agent,
            operation=operation,
            fallback_to_run=False,
            label=label,
            native_loop_continuation=native_loop_continuation,
            current_request=current_request,
        )
        if result is None:
            return None
        return self._persist_agent_operation_result(
            state_store=state_store,
            run_dir=run_dir,
            saved_files=saved_files,
            stage=stage,
            attempt=attempt,
            label=label,
            agent_config=agent_config,
            operation=operation,
            filename=filename,
            event_name=event_name,
            result=result,
        )

    def _handle_agent_stream_event(
        self,
        *,
        state_store: WorkflowStateStore,
        stage: Stage,
        attempt: int,
        agent_config: AgentInstanceConfig,
        operation: str,
        event: Any,
    ) -> None:
        event_type = str(getattr(event, "event_type", "") or "")
        delta = str(getattr(event, "delta", "") or "")
        payload = getattr(event, "payload", None)
        payload_dict = dict(payload) if isinstance(payload, dict) else {}
        if event_type == "llm_attempt_discarded":
            self._emit_runtime_status(
                state_store=state_store,
                event_kind="llm_stream_attempt_discarded",
                component=agent_config.id,
                operation=operation,
                provider_inflight=True,
                extra_payload={"stream_event_type": event_type, **payload_dict},
                stage_override=stage,
                attempt_override=attempt,
            )
            return
        presentation_class = str(payload_dict.get("presentation_class") or "")
        canonical_event_type = str(payload_dict.get("canonical_event_type") or "")
        if canonical_event_type and not delta:
            self._emit_runtime_status(
                state_store=state_store,
                event_kind=event_type,
                component=agent_config.id,
                operation=operation,
                provider_inflight=(canonical_event_type != "response_completed"),
                extra_payload={
                    "stream_event_type": event_type,
                    "display": presentation_class or "protocol",
                    **payload_dict,
                },
                stage_override=stage,
                attempt_override=attempt,
            )
            return
        if not delta:
            return
        is_text_delta = (
            presentation_class == "user_text" or event_type == "llm_text_delta"
        )
        self._emit_runtime_status(
            state_store=state_store,
            event_kind="llm_stream_delta",
            component=agent_config.id,
            operation=operation,
            provider_inflight=True,
        )
        if self._runtime_status_callback is None:
            return
        self._deliver_runtime_status(
            {
                "event_kind": (
                    "llm_text_delta"
                    if is_text_delta
                    else (event_type if canonical_event_type else "llm_protocol_delta")
                ),
                "stage": stage.value,
                "attempt": attempt,
                "continuation_index": int(
                    state_store.state.workflow_state.scratchpad.get(
                        "continuation_index", 0
                    )
                    or 0
                ),
                "component": agent_config.id,
                "operation": operation,
                "delta": delta,
                "stream_event_type": event_type,
                "display": presentation_class
                or ("text" if is_text_delta else "protocol"),
                "provider_inflight": True,
                **payload_dict,
            }
        )

    def _persist_agent_operation_result(
        self,
        *,
        state_store: WorkflowStateStore,
        run_dir: Path,
        saved_files: list[str],
        stage: Stage,
        attempt: int,
        label: str,
        agent_config: AgentInstanceConfig,
        operation: str,
        filename: str,
        event_name: str,
        result: Any,
    ):
        serialized = self._serialize_payload(result.payload)
        serialized = self._attach_native_tool_metadata(
            serialized=serialized,
            result=result,
        )
        context_update = state_store.record_agent_output(
            stage=stage,
            attempt=attempt,
            agent_config=agent_config,
            agent_output=result,
            file_path=None,
        )
        if isinstance(context_update, dict):
            messages = context_update.get("messages")
            if isinstance(messages, list):
                for message in messages:
                    if isinstance(message, str) and message.strip():
                        self._emit(message.strip())
            else:
                message = context_update.get("message")
                if isinstance(message, str) and message.strip():
                    self._emit(message.strip())
        state_store.record_stage_event(
            event_type=event_name,
            stage=stage,
            attempt=attempt,
            payload={
                "artifact_type": result.artifact_type,
            },
        )
        if self._artifact_callback is not None:
            try:
                self._artifact_callback(
                    label, agent_config, result.artifact_type, serialized, None
                )
            except RuntimeControlRequested:
                raise
            except Exception:
                logging.getLogger(__name__).warning(
                    'Failed to display agent artifact for %s', label, exc_info=True,
                )
        return result

    def _runtime_gate(
        self,
        *,
        state_store: WorkflowStateStore,
        stage: Stage,
        attempt: int,
    ) -> GateTrace:
        if stage == Stage.ANALYSIS_DECISION:
            return self._analysis_action_runtime_gate(
                state_store=state_store,
                stage=stage,
                attempt=attempt,
            )
        critic_loop = state_store.state.feedback_state.critic_loop
        if (
            critic_loop.source_stage != stage.value
            or critic_loop.attempt != attempt
            or critic_loop.phase != "completed"
        ):
            raise RuntimeError(
                "runtime_gate requires an explicit terminal Reviewer-loop state for the current stage attempt."
            )
        return self._reviewer_loop_runtime_gate(
            state_store=state_store,
            stage=stage,
            attempt=attempt,
        )

    def _analysis_action_runtime_gate(
        self,
        *,
        state_store: WorkflowStateStore,
        stage: Stage,
        attempt: int,
    ) -> GateTrace:
        from ownpi.runtime.stage_execution.loop_policy import is_analysis_route_loop

        workflow = state_store.state.workflow_state
        if not is_analysis_route_loop(
            stage, workflow.stage_runtime_profile,
            int(workflow.scratchpad.get('stage_loop_index', 1)),
        ):
            raise RuntimeError('Analysis gate requires the final substage')
        plan = state_store.state.workflow_state.run_workflow_plan
        if plan is None:
            raise RuntimeError("analysis action gate requires an active workflow plan")
        if not plan.contains(stage):
            raise RuntimeError(
                "analysis action gate stage is not present in the workflow plan"
            )
        feedback_state = state_store.state.feedback_state
        transition = feedback_state.analysis_transition
        receipt = transition.next_stage_receipt
        if (
            transition.source_stage != stage.value
            or transition.attempt != attempt
            or not transition.feedback_record_id
            or feedback_state.record_ref(transition.feedback_record_id) is None
            or receipt is None
            or receipt.source_stage != stage.value
            or receipt.attempt != attempt
            or receipt.feedback_record_id != transition.feedback_record_id
        ):
            raise RuntimeError(
                "analysis_protocol_failure: durable feedback and next-stage receipts are required"
            )
        if transition.gate_trace is not None:
            return GateTrace.from_dict(transition.gate_trace)
        try:
            target_stage = stage_from_value(receipt.target_stage)
        except ValueError as exc:
            raise RuntimeError(
                f"analysis_protocol_failure: invalid target stage {receipt.target_stage!r}"
            ) from exc
        planned_successor = plan.next_stage(stage)
        route_kind = (
            "advance"
            if target_stage == planned_successor
            else "revisit"
            if plan.is_prior_stage(stage, target_stage)
            else "invalid"
        )
        if route_kind == "invalid":
            raise RuntimeError(
                "analysis_protocol_failure: target stage is neither the planned successor "
                "nor a prior stage in the active workflow plan"
            )
        reason = f"analysis_route:{route_kind}:{target_stage.value}"
        revisit_metadata: dict[str, Any] = {
            "protocol_version": "unified_feedback:v1",
            "route_source": "next_stage",
            "route_tool_call_id": receipt.tool_call_id,
            "feedback_record_id": receipt.feedback_record_id,
            "route_kind": route_kind,
            "target_stage": target_stage.value,
        }
        if route_kind == "advance":
            outcome = self._accept_plan_transition(
                plan=plan,
                stage=stage,
                reason=reason,
            )
        else:
            outcome, typed_metadata = self._resolve_typed_analysis_revisit_transition(
                state_store=state_store,
                plan=plan,
                stage=stage,
                attempt=attempt,
                receipt=receipt,
            )
            revisit_metadata.update(typed_metadata)
        gate_trace = GateTrace(
            stage=stage,
            attempt=attempt,
            decision=outcome.decision,
            next_stage=outcome.next_stage,
            counter_snapshot={
                "total_stage_attempts": calculate_total_stage_attempts(
                    state_store.state.workflow_state
                ),
                "stage_attempts": state_store.state.workflow_state.stage_attempt_counters.get(
                    stage.value,
                    attempt,
                ),
                "review_rounds_completed": 0,
            },
            terminal_status=outcome.terminal_status,
            reason=reason,
            limit_exceeded_reason=outcome.limit_exceeded_reason,
            stage_close_kind="analysis_route",
            review_rounds_completed=0,
            latest_review_record_ref=receipt.feedback_record_id,
            revisit_metadata=revisit_metadata,
            evidence_lane="artifact",
        )
        if outcome.next_stage is None:
            feedback_state.active_handoff = FeedbackHandoffState()
        else:
            consumer_attempt = (
                int(
                    state_store.state.workflow_state.stage_attempt_counters.get(
                        outcome.next_stage.value,
                        0,
                    )
                    or 0
                )
                + 1
            )
            feedback_state.active_handoff = FeedbackHandoffState(
                record_id=receipt.feedback_record_id,
                consumer_stage=outcome.next_stage.value,
                consumer_attempt=consumer_attempt,
                status="pending",
            )
        transition.gate_trace = gate_trace.to_dict()
        transition.applied_at = now_utc_iso()
        state_store.poll_runtime_control("analysis_transition_applied")
        return gate_trace

    def _resolve_typed_analysis_revisit_transition(
        self,
        *,
        state_store: WorkflowStateStore,
        plan: RunWorkflowPlan,
        stage: Stage,
        attempt: int,
        receipt: NextStageReceipt,
    ) -> tuple[TransitionOutcome, dict[str, Any]]:
        target_value = str(receipt.target_stage or "").strip()
        decision = analysis_revisit_decision_for_target(target_value)
        target_stage = (
            analysis_revisit_target_for_decision(decision)
            if decision is not None
            else None
        )
        if decision is None or target_stage is None:
            raise RuntimeError(
                f"analysis_protocol_failure: illegal revisit target {target_value!r}"
            )
        try:
            plan.validate_revisit_target(stage, target_stage)
        except ValueError as exc:
            raise RuntimeError(
                "analysis_protocol_failure: target stage is not a prior stage in the active plan"
            ) from exc
        revisit_state = state_store.state.workflow_state.revisit_state
        next_attempt = (
            int(
                state_store.state.workflow_state.stage_attempt_counters.get(
                    target_stage.value,
                    0,
                )
                or 0
            )
            + 1
        )
        contract_id = (
            f"revisit:{stage.value}:a{attempt}:{target_stage.value}:a{next_attempt}"
        )
        existing_contract = revisit_state.pending_revisit_contract
        if (
            isinstance(existing_contract, dict)
            and existing_contract.get("contract_id") == contract_id
        ):
            if (
                existing_contract.get("route_call_id") != receipt.tool_call_id
                or existing_contract.get("feedback_record_id")
                != receipt.feedback_record_id
            ):
                raise RuntimeError(
                    "analysis_protocol_failure: revisit contract identity conflicts with "
                    "the durable action"
                )
            replay_metadata = {
                "allowed": True,
                "idempotent_replay": True,
                "source_stage": stage.value,
                "source_attempt": attempt,
                "target_stage": target_stage.value,
                "target_attempt": next_attempt,
                "decision": decision.value,
                "reason": f"analysis_route:revisit:{target_stage.value}",
                "revisit_contract": dict(existing_contract),
            }
            return (
                TransitionOutcome(
                    decision=decision,
                    current_stage=stage,
                    next_stage=target_stage,
                    terminal_status=None,
                    limit_exceeded_reason=(
                        f"analysis_route:revisit:{target_stage.value}"
                    ),
                ),
                replay_metadata,
            )
        budget_reason = self._analysis_revisit_budget_exceeded_reason(
            state_store=state_store,
            target_stage=target_stage,
        )
        if budget_reason is not None:
            max_total = self._effective_revisit_budget()
            outcome = self._accept_plan_transition(
                plan=plan,
                stage=stage,
                reason=f"{budget_reason}:force_forward",
            )
            return outcome, {
                "allowed": False,
                "budget_exceeded": True,
                "forced_forward": True,
                "reason": budget_reason,
                "requested_target_stage": target_stage.value,
                "budget_state": {
                    "max_total_revisits": max_total,
                    "used_total_revisits": revisit_state.total_revisits,
                },
            }
        revisit_contract = {
            "contract_id": contract_id,
            "protocol_version": "unified_feedback:v1",
            "source_stage": stage.value,
            "source_attempt": attempt,
            "source_analysis_attempt": attempt,
            "target_stage": target_stage.value,
            "target_attempt": next_attempt,
            "decision": decision.value,
            "route_call_id": receipt.tool_call_id,
            "feedback_record_id": receipt.feedback_record_id,
            "return_stage": Stage.ANALYSIS_DECISION.value,
            "status": "pending",
            "created_at": now_utc_iso(),
        }
        metadata = {
            "allowed": True,
            "source_stage": stage.value,
            "source_attempt": attempt,
            "target_stage": target_stage.value,
            "target_attempt": next_attempt,
            "decision": decision.value,
            "reason": f"analysis_route:revisit:{target_stage.value}",
            "revisit_contract": dict(revisit_contract),
        }
        state_store.record_revisit_transition(
            source_stage=stage,
            source_attempt=attempt,
            target_stage=target_stage,
            target_attempt=next_attempt,
            decision=decision,
            reason=f"analysis_route:revisit:{target_stage.value}",
            revisit_contract=dict(revisit_contract),
        )
        return (
            TransitionOutcome(
                decision=decision,
                current_stage=stage,
                next_stage=target_stage,
                terminal_status=None,
                limit_exceeded_reason=f"analysis_route:revisit:{target_stage.value}",
            ),
            metadata,
        )

    def _reviewer_loop_runtime_gate(
        self,
        *,
        state_store: WorkflowStateStore,
        stage: Stage,
        attempt: int,
    ) -> GateTrace:
        loop = state_store.state.feedback_state.critic_loop
        plan = state_store.state.workflow_state.run_workflow_plan
        if plan is None:
            raise RuntimeError("reviewer loop gate requires an active workflow plan.")
        if not plan.contains(stage):
            raise RuntimeError(
                "reviewer loop gate stage is not present in the workflow plan."
            )
        reason = "core_completed_feedback"
        revisit_metadata: dict[str, Any] = {}
        active_revisit_context = (
            state_store.state.workflow_state.revisit_state.current_context_for(
                stage=stage,
                attempt=attempt,
            )
        )
        return_to_analysis = isinstance(active_revisit_context, dict) and plan.contains(
            Stage.ANALYSIS_DECISION
        )
        if return_to_analysis:
            feedback_record_id = str(loop.feedback_record_id or "").strip()
            if (
                not feedback_record_id
                or state_store.state.feedback_state.record_ref(feedback_record_id)
                is None
            ):
                raise RuntimeError(
                    "revisit repair return requires a durable Reviewer feedback record."
                )
            consumer_attempt = (
                int(
                    state_store.state.workflow_state.stage_attempt_counters.get(
                        Stage.ANALYSIS_DECISION.value,
                        0,
                    )
                    or 0
                )
                + 1
            )
            active_handoff = state_store.state.feedback_state.active_handoff
            if not (
                active_handoff.record_id == feedback_record_id
                and active_handoff.consumer_stage == Stage.ANALYSIS_DECISION.value
                and active_handoff.consumer_attempt == consumer_attempt
                and active_handoff.status in {"pending", "consumed"}
            ):
                state_store.state.feedback_state.active_handoff = FeedbackHandoffState(
                    record_id=feedback_record_id,
                    consumer_stage=Stage.ANALYSIS_DECISION.value,
                    consumer_attempt=consumer_attempt,
                    status="pending",
                )
            outcome = TransitionOutcome(
                decision=GateDecision.ACCEPT,
                current_stage=stage,
                next_stage=Stage.ANALYSIS_DECISION,
                terminal_status=None,
                limit_exceeded_reason="revisit_repair_return_to_analysis",
            )
            reason = "revisit_repair_return_to_analysis"
        else:
            outcome = self._accept_plan_transition(
                plan=plan,
                stage=stage,
                reason=reason,
            )
        next_stage = outcome.next_stage
        if (
            outcome.decision
            in {
                GateDecision.ACCEPT,
                GateDecision.ADVANCE_WITH_RESIDUAL_FINDINGS,
            }
            and outcome.terminal_status is None
        ):
            if next_stage is None:
                raise RuntimeError("accept transition requires next stage resolution.")
        return GateTrace(
            stage=stage,
            attempt=attempt,
            decision=outcome.decision,
            next_stage=next_stage,
            counter_snapshot={
                "total_stage_attempts": calculate_total_stage_attempts(
                    state_store.state.workflow_state
                ),
                "stage_attempts": state_store.state.workflow_state.stage_attempt_counters.get(
                    stage.value,
                    attempt,
                ),
                "review_rounds_completed": loop.round,
            },
            terminal_status=outcome.terminal_status,
            reason=reason,
            stage_close_kind=loop.completion_reason or loop.phase,
            review_rounds_completed=loop.round,
            latest_review_record_ref=loop.feedback_record_id,
            revisit_metadata=revisit_metadata,
        )

    @staticmethod
    def _accept_plan_transition(
        *,
        plan: RunWorkflowPlan,
        stage: Stage,
        reason: str,
    ) -> TransitionOutcome:
        if not plan.contains(stage):
            return TransitionOutcome(
                decision=GateDecision.FAIL,
                current_stage=stage,
                next_stage=None,
                terminal_status="failed",
                limit_exceeded_reason="current_stage_not_in_plan",
            )
        if stage == plan.terminal_stage:
            return TransitionOutcome(
                decision=GateDecision.ACCEPT,
                current_stage=stage,
                next_stage=None,
                terminal_status="completed",
                limit_exceeded_reason=reason,
            )
        next_stage = plan.next_stage(stage)
        return TransitionOutcome(
            decision=GateDecision.ACCEPT,
            current_stage=stage,
            next_stage=next_stage,
            terminal_status=None,
            limit_exceeded_reason=reason,
        )

    def _analysis_revisit_budget_exceeded_reason(
        self,
        *,
        state_store: WorkflowStateStore,
        target_stage: Stage,
    ) -> str | None:
        del target_stage
        revisit_state = state_store.state.workflow_state.revisit_state
        max_total = self._effective_revisit_budget()
        if max_total is not None and int(revisit_state.total_revisits or 0) >= max_total:
            return "max_total_revisits_exceeded"
        return None

    def _effective_revisit_budget(self) -> int | None:
        return self._effective_review_policy.max_total_revisits

    def _build_final_package(
        self,
        *,
        state_store: WorkflowStateStore,
        run_dir: Path,
        saved_files: list[str],
        stage_runs: list[StageRunSummary] | None = None,
        final_status: str,
        final_decision: GateDecision | str,
        terminal_stage: Stage | None,
    ) -> FinalPackage:
        build_result = self._final_package_builder.build(
            state_store=state_store,
            run_dir=run_dir,
            saved_files=saved_files,
            stage_runs=stage_runs,
            final_status=final_status,
            final_decision=final_decision,
            terminal_stage=terminal_stage,
            effective_permissions=state_store.state.tool_state.effective_permissions,
        )
        return build_result.package


    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            raise ValueError(f"Expected JSON object in {path}")
        return data

    @staticmethod
    def _is_full_tier_runtime(workflow_state) -> bool:
        return is_full_tier_runtime(workflow_state)

    @staticmethod
    def _assert_session_run_dir(run_dir: Path, *, operation: str) -> None:
        if parse_session_run_dir(run_dir) is None:
            raise ValueError(
                f"{operation} requires a session-managed run path shaped as "
                "<app_home>/sessions/<session_id>/tasks/<task_id>/runs/<run_id>"
            )

    def _emit(self, message: str) -> None:
        if self._event_callback is not None:
            try:
                self._event_callback(message)
            except RuntimeControlRequested:
                raise
            except Exception:
                logging.getLogger(__name__).warning('Runtime display callback failed', exc_info=True)

    def _emit_runtime_status(
        self,
        *,
        state_store: WorkflowStateStore,
        event_kind: str,
        provider: str = "",
        component: str = "",
        operation: str = "",
        elapsed_ms: float = 0.0,
        heartbeat_count: int = 0,
        provider_inflight: bool = False,
        extra_payload: dict[str, Any] | None = None,
        stage_override: Stage | None = None,
        attempt_override: int | None = None,
    ) -> None:
        workflow_state = state_store.state.workflow_state
        context_meter_summary = build_context_meter_from_runtime_state(
            state_store.state,
            provider_operation=operation,
            provider_elapsed_ms=elapsed_ms,
            provider_inflight=provider_inflight,
            live_surface_capable=self._live_context_meter_capable(state_store),
        )
        payload = {
            "event_kind": event_kind,
            "stage": str(
                getattr(
                    stage_override or workflow_state.current_stage,
                    "value",
                    workflow_state.current_phase,
                )
                or ""
            ),
            "attempt": int(
                attempt_override
                if attempt_override is not None
                else workflow_state.current_stage_attempt or 0
            ),
            "query_chain_id": str(workflow_state.current_query_chain_id or ""),
            "continuation_index": int(
                workflow_state.scratchpad.get("continuation_index", 0) or 0
            ),
            "provider": provider,
            "component": component,
            "operation": operation,
            "elapsed_ms": float(elapsed_ms or 0.0),
            "heartbeat_count": int(heartbeat_count or 0),
            "provider_inflight": bool(provider_inflight),
            "pressure_level": str(
                context_meter_summary.get("pressure_level", "") or ""
            ),
            "context_meter_summary": context_meter_summary,
        }
        if extra_payload:
            payload.update(extra_payload)
        self._deliver_runtime_status(payload)

    def _deliver_runtime_status(self, payload: dict[str, Any]) -> None:
        if self._runtime_status_callback is not None:
            try:
                self._runtime_status_callback(payload)
            except RuntimeControlRequested:
                raise
            except Exception:
                logging.getLogger(__name__).warning('Runtime status callback failed', exc_info=True)

    @staticmethod
    def _live_context_meter_capable(state_store: WorkflowStateStore) -> bool:
        context_meter = state_store.state.runtime_preferences.context_meter
        if not isinstance(context_meter, dict):
            return False
        return (
            bool(context_meter.get("enabled", True))
            and str(context_meter.get("mode", "auto") or "auto") != "static_only"
        )

    @staticmethod
    def _empty_llm_trace_summary() -> dict[str, Any]:
        return {
            "llm_call_count": 0,
            "llm_success_count": 0,
            "llm_error_count": 0,
            "timeout_like_error_count": 0,
            "retried_call_count": 0,
            "total_elapsed_ms": 0.0,
            "max_prompt_char_count": 0,
            "operation_stats": {},
        }

    @classmethod
    def _update_llm_trace_summary(
        cls,
        *,
        summary: dict[str, Any],
        event: dict[str, Any],
        operation_key: str,
    ) -> None:
        summary["llm_call_count"] = int(summary.get("llm_call_count", 0) or 0) + 1
        if event.get("success") is True:
            summary["llm_success_count"] = (
                int(summary.get("llm_success_count", 0) or 0) + 1
            )
        elif event.get("success") is False:
            summary["llm_error_count"] = int(summary.get("llm_error_count", 0) or 0) + 1
        error_type = str(event.get("error_type", "") or "").lower()
        error_message = str(event.get("error_message", "") or "").lower()
        if "timeout" in error_type or "timed out" in error_message:
            summary["timeout_like_error_count"] = (
                int(summary.get("timeout_like_error_count", 0) or 0) + 1
            )
        if bool(event.get("retried", False)):
            summary["retried_call_count"] = (
                int(summary.get("retried_call_count", 0) or 0) + 1
            )
        elapsed_ms = float(event.get("elapsed_ms", 0.0) or 0.0)
        summary["total_elapsed_ms"] = round(
            float(summary.get("total_elapsed_ms", 0.0) or 0.0) + elapsed_ms, 2
        )
        prompt_chars = int(event.get("prompt_char_count", 0) or 0)
        summary["max_prompt_char_count"] = max(
            int(summary.get("max_prompt_char_count", 0) or 0), prompt_chars
        )

        operation_stats = summary.setdefault("operation_stats", {})
        if not isinstance(operation_stats, dict):
            operation_stats = {}
            summary["operation_stats"] = operation_stats
        bucket = operation_stats.setdefault(
            operation_key,
            {
                "operation_key": operation_key,
                "call_count": 0,
                "total_elapsed_ms": 0.0,
                "max_elapsed_ms": 0.0,
                "max_prompt_char_count": 0,
                "error_count": 0,
            },
        )
        bucket["call_count"] = int(bucket.get("call_count", 0) or 0) + 1
        bucket["total_elapsed_ms"] = round(
            float(bucket.get("total_elapsed_ms", 0.0) or 0.0) + elapsed_ms, 2
        )
        bucket["max_elapsed_ms"] = max(
            float(bucket.get("max_elapsed_ms", 0.0) or 0.0), elapsed_ms
        )
        bucket["max_prompt_char_count"] = max(
            int(bucket.get("max_prompt_char_count", 0) or 0), prompt_chars
        )
        if event.get("success") is False:
            bucket["error_count"] = int(bucket.get("error_count", 0) or 0) + 1

    def _record_llm_trace(
        self, *, state_store: WorkflowStateStore, event: dict[str, Any]
    ) -> None:
        scratchpad = state_store.state.workflow_state.scratchpad
        llm_trace = scratchpad.setdefault(
            "llm_trace",
            {
                "events": [],
                "event_count": 0,
                "dropped_event_count": 0,
                "latest_by_operation": {},
                "summary": self._empty_llm_trace_summary(),
            },
        )
        llm_trace["trace_scope"] = "summary_recent_only"
        events = llm_trace.setdefault("events", [])
        normalized_event = dict(event)
        if isinstance(events, list):
            events.append(normalized_event)
            overflow = max(0, len(events) - MAX_LLM_TRACE_RECENT_EVENTS)
            if overflow:
                del events[:overflow]
                llm_trace["dropped_event_count"] = (
                    int(llm_trace.get("dropped_event_count", 0) or 0) + overflow
                )
        llm_trace["event_count"] = int(llm_trace.get("event_count", 0) or 0) + 1
        latest_by_operation = llm_trace.setdefault("latest_by_operation", {})
        operation_key = self._llm_operation_key(event)
        if isinstance(latest_by_operation, dict):
            latest_by_operation[operation_key] = normalized_event
        summary = llm_trace.setdefault("summary", self._empty_llm_trace_summary())
        if isinstance(summary, dict):
            self._update_llm_trace_summary(
                summary=summary,
                event=normalized_event,
                operation_key=operation_key,
            )
        if (
            str(event.get("event_type", "") or "").strip() == "request_completed"
            and bool(event.get("success", False))
            and (
                isinstance(event.get("usage"), dict)
                or event.get("provider_prompt_tokens") is not None
                or event.get("provider_total_tokens") is not None
            )
        ):
            self._record_llm_usage_receipt_from_trace(
                state_store=state_store,
                operation_key=operation_key,
                event=event,
            )
        event_type = str(event.get("event_type", "") or "").strip()
        provider_inflight = event_type in {"request_started", "request_heartbeat"}
        heartbeat_count = int(event.get("heartbeat_count", 0) or 0)
        self._emit_runtime_status(
            state_store=state_store,
            event_kind="llm_status",
            provider=str(event.get("provider", "") or "").strip(),
            component=str(event.get("component", "") or "").strip(),
            operation=str(event.get("operation", "") or operation_key),
            elapsed_ms=float(event.get("elapsed_ms", 0.0) or 0.0),
            heartbeat_count=heartbeat_count,
            provider_inflight=provider_inflight,
        )
        llm_progress_message = self._llm_progress_message(
            operation_key=operation_key, event=event
        )
        if llm_progress_message is not None and self._verbose_llm_progress:
            self._emit(llm_progress_message)
        if event_type in {"request_started", "request_heartbeat"}:
            return
        if state_store.state.runtime_preferences.ui_mode != "developer":
            return
        elapsed_ms = float(event.get("elapsed_ms", 0.0) or 0.0)
        if not bool(event.get("success", False)) or elapsed_ms >= 10000:
            self._emit(
                "[llm] "
                f"{operation_key} success={bool(event.get('success', False))} "
                f"elapsed_ms={elapsed_ms:.0f} attempts={int(event.get('attempt_count', 0) or 0)} "
                f"prompt_chars={int(event.get('prompt_char_count', 0) or 0)}"
            )

    @staticmethod
    def _llm_operation_key(event: dict[str, Any]) -> str:
        return llm_operation_key(event)

    @staticmethod
    def _llm_progress_message(
        *, operation_key: str, event: dict[str, Any]
    ) -> str | None:
        event_type = str(event.get("event_type", "") or "").strip()
        provider = str(event.get("provider", "") or "").strip() or "unknown"
        stage = str(event.get("stage", "") or "").strip() or "unknown"
        component = str(event.get("component", "") or "").strip() or "unknown"
        operation = str(event.get("operation", "") or "").strip() or operation_key
        elapsed_ms = float(event.get("elapsed_ms", 0.0) or 0.0)
        if event_type == "request_started":
            return (
                "[llm] "
                f"start stage={stage} component={component} operation={operation} provider={provider}"
            )
        if event_type == "request_heartbeat":
            return (
                "[llm] "
                f"heartbeat stage={stage} component={component} operation={operation} provider={provider} "
                f"elapsed_ms={elapsed_ms:.0f}"
            )
        if event_type == "request_completed":
            return (
                "[llm] "
                f"done stage={stage} component={component} operation={operation} provider={provider} "
                f"success={bool(event.get('success', False))} elapsed_ms={elapsed_ms:.0f}"
            )
        return None

    def _record_llm_usage_receipt_from_trace(
        self,
        *,
        state_store: WorkflowStateStore,
        operation_key: str,
        event: dict[str, Any],
    ) -> None:
        usage = (
            dict(event.get("usage", {})) if isinstance(event.get("usage"), dict) else {}
        )
        for key in (
            "provider_prompt_tokens",
            "provider_completion_tokens",
            "provider_total_tokens",
            "provider_cache_read_tokens",
            "provider_cache_write_tokens",
        ):
            if key in event and key not in usage:
                usage[key] = event.get(key)
        if not usage:
            return
        context_state = state_store.state.context_state
        snapshot = context_state.token_budget_snapshot
        estimated = (
            int(getattr(snapshot, "estimated_input_tokens", 0) or 0)
            if snapshot is not None
            else 0
        )
        counters = read_provider_usage(usage)
        provider_prompt = counters["provider_prompt_tokens"]
        provider_total = counters["provider_total_tokens"]
        delta_base = provider_prompt if provider_prompt is not None else provider_total
        receipt = LLMUsageReceipt(
            receipt_id=f"llm-usage:{len(context_state.llm_usage_receipts) + 1}",
            scope={
                "stage": state_store.state.workflow_state.current_stage.value,
                "attempt": state_store.state.workflow_state.current_stage_attempt,
                "query_chain_id": str(
                    state_store.state.workflow_state.current_query_chain_id or ""
                ),
                "query_chain_depth": int(
                    state_store.state.workflow_state.current_query_chain_depth or 0
                ),
                "continuation_index": int(
                    state_store.state.workflow_state.scratchpad.get(
                        "continuation_index", 0
                    )
                    or 0
                ),
            },
            model=str(event.get("model", "") or ""),
            request_kind=str(event.get("request_kind", operation_key) or operation_key),
            estimated_rendered_input_tokens=estimated,
            provider_prompt_tokens=provider_prompt,
            provider_completion_tokens=counters["provider_completion_tokens"],
            provider_total_tokens=provider_total,
            provider_cache_read_tokens=counters["provider_cache_read_tokens"],
            provider_cache_write_tokens=counters["provider_cache_write_tokens"],
            estimate_vs_provider_delta=(
                int(delta_base) - int(estimated)
                if delta_base is not None and estimated > 0
                else None
            ),
            usage_source="provider_reported",
            created_at=str(event.get("timestamp", "") or now_utc_iso()),
        )
        state_store.record_llm_usage_receipt(receipt)

    def _build_agents(self) -> dict[str, tuple[AgentInstanceConfig, Any]]:
        llm_cache: dict[str, Any] = {}
        runtime_agents: dict[str, tuple[AgentInstanceConfig, Any]] = {}
        for agent_config in self._user_config.agents.get_enabled_pipeline():
            llm_profile_name = self._user_config.resolve_llm_name(agent_config.llm)
            if llm_profile_name not in llm_cache:
                llm_cache[llm_profile_name] = build_llm_client(
                    self._user_config.get_llm_config(llm_profile_name)
                )
            llm_client = llm_cache[llm_profile_name]
            runtime_agents[agent_config.role] = (
                agent_config,
                build_agent(
                    agent_config, llm_client, self._project_root, self._app_paths
                ),
            )
        if "core" not in runtime_agents or "reviewer" not in runtime_agents:
            raise ValueError(
                "Template-driven stage runtime requires both core and reviewer agents."
            )
        return runtime_agents

    def _agent_with_runtime_llm_overrides(
        self,
        *,
        base_agent: Any,
        agent_config: AgentInstanceConfig,
        state_store: WorkflowStateStore,
    ) -> Any:
        prefs = state_store.state.runtime_preferences
        if not (prefs.temporary_model_override or prefs.temporary_reasoning_override):
            return base_agent
        profile_name = self._user_config.resolve_llm_name(agent_config.llm)
        llm_config = apply_temporary_llm_overrides(
            self._user_config.get_llm_config(profile_name),
            temporary_model=prefs.temporary_model_override,
            temporary_reasoning=prefs.temporary_reasoning_override,
        )
        llm_client = build_llm_client(llm_config)
        return build_agent(
            agent_config, llm_client, self._project_root, self._app_paths
        )

    @staticmethod
    def _consume_temporary_llm_overrides(state_store: WorkflowStateStore) -> None:
        prefs = state_store.state.runtime_preferences
        if not (prefs.temporary_model_override or prefs.temporary_reasoning_override):
            return
        prefs.temporary_model_override = None
        prefs.temporary_reasoning_override = None

    @staticmethod
    def _apply_prepared_run_context(
        *,
        state_store: WorkflowStateStore,
        run_context: PreparedRunContext,
    ) -> None:
        run_metadata = state_store.state.run_metadata
        run_metadata.session_id = run_context.session_id
        run_metadata.task_id = run_context.task_id
        run_metadata.run_id = run_context.run_id
        run_metadata.updated_at = now_utc_iso()

    def _resolve_runtime_path(self, raw_path: str) -> Path:
        candidate = Path(raw_path).expanduser()
        if candidate.is_absolute():
            return candidate
        return self._project_root / candidate

    def _emit_session_start_hook(
        self,
        *,
        state_store: WorkflowStateStore,
        run_dir: Path,
        source: str,
    ) -> None:
        scratchpad = state_store.state.workflow_state.scratchpad
        if scratchpad.get("hook_session_started"):
            return
        outcome = self._hook_engine.run(
            SessionStartRequest(
                payload={
                    "project_root": str(self._project_root),
                    "cwd": str(self._project_root),
                    "run_dir": str(run_dir),
                    "run_id": str(state_store.state.run_metadata.run_id or ""),
                    "source": source,
                }
            ),
            effective_permissions=state_store.state.tool_state.effective_permissions,
        )
        scratchpad["hook_session_started"] = True
        persist_hook_delivery_batch(
            state_store.state.context_state,
            outcome.delivery,
        )
        for message in outcome.delivery.system_messages:
            self._emit(f"[hooks] {message}")

    def _emit_session_end_hook(
        self,
        *,
        state_store: WorkflowStateStore,
        run_dir: Path,
        reason: str,
    ) -> None:
        scratchpad = state_store.state.workflow_state.scratchpad
        if scratchpad.get("hook_session_ended"):
            return
        outcome = self._hook_engine.run(
            SessionEndRequest(
                payload={
                    "project_root": str(self._project_root),
                    "cwd": str(self._project_root),
                    "run_dir": str(run_dir),
                    "session_id": str(state_store.state.run_metadata.session_id or ""),
                    "run_id": str(state_store.state.run_metadata.run_id or ""),
                    "reason": reason,
                    "source": "workflow_finalization",
                }
            ),
            effective_permissions=state_store.state.tool_state.effective_permissions,
        )
        scratchpad["hook_session_ended"] = True
        for message in outcome.delivery.system_messages:
            self._emit(f"[hooks] {message}")

    @staticmethod
    def _serialize_payload(payload: Any) -> dict[str, Any]:
        if hasattr(payload, "to_dict"):
            return payload.to_dict()
        if isinstance(payload, dict):
            return payload
        raise TypeError(f"Unsupported agent payload type: {type(payload)!r}")

    @staticmethod
    def _attach_native_tool_metadata(
        *,
        serialized: dict[str, Any],
        result: Any,
    ) -> dict[str, Any]:
        if getattr(result, "artifact_type", None) != "native_tool_loop_result":
            return serialized
        metadata = getattr(result, "metadata", None)
        if not isinstance(metadata, dict):
            return serialized
        trace = {
            key: metadata[key]
            for key in ("native_tool_specs", "native_tool_loop")
            if key in metadata
        }
        if not trace:
            return serialized
        return {**dict(serialized), "_runtime_trace": trace}
