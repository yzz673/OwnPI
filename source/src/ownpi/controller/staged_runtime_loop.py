from __future__ import annotations

from pathlib import Path
from typing import Any

from ownpi.controller.staged_runtime_host import StagedRuntimeHost
from ownpi.controller.staged_runtime_support import (
    build_runtime_control_poll,
    resolve_stage_entry_context,
)
from ownpi.controller.runtime_control_signal import (
    RuntimeControlRequest,
    RuntimeControlRequested,
    clear_consumed_runtime_control_request,
)
from ownpi.controller.stage_kernel import (
    StageContinuationTerminatedError,
    build_analysis_stage_program,
)
from ownpi.controller.state_store import WorkflowStateStore
from ownpi.controller.stages import GateDecision, Stage
from ownpi.runtime.events import now_utc_iso
from ownpi.runtime.context.entry import RuntimeEntryContext
from ownpi.runtime.interactions.pause import (
    HumanClarificationPausedError,
    ToolPermissionPausedError,
)
from ownpi.runtime.continuation_directive import RuntimeContinuationDirective
from ownpi.runtime.rollout.resume_entry import StageResumeEntry
from ownpi.runtime.stage_prep import StagePrepRequest, StagePrepService
from ownpi.runtime.stage_prep.models import StagePrepResult
from ownpi.schemas.artifacts import FinalPackage
from ownpi.schemas.runtime import AgentInstanceConfig
from ownpi.schemas.runtime_human_clarification import HumanClarificationSession

class StagedRuntimeLoop:
    """Owns the staged workflow execution loop while reusing engine primitives."""

    def __init__(self, host: StagedRuntimeHost) -> None:
        self._host = host

    def _handle_runtime_control(
        self,
        *,
        exception: RuntimeControlRequested,
        state_store: WorkflowStateStore,
        run_dir: Path,
        saved_files: list[str],
        current_stage: Stage,
        attempt: int,
        stage_label: str,
    ) -> tuple[str, GateDecision, Stage]:
        request = exception.request
        final_status = 'paused_for_human' if request.action == 'pause' else 'stopped'
        final_decision = (
            GateDecision.CLARIFY_HUMAN
            if request.action == 'pause'
            else GateDecision.FAIL
        )
        state_store.record_stage_event(
            event_type=f'user_{request.action}_requested',
            stage=current_stage,
            attempt=attempt,
            payload={
                'request_id': request.request_id,
                'reason': request.reason,
                'control_action': request.action,
            },
            status=final_status,
        )
        self._host._emit(
            f'[{stage_label}] user_{request.action}_accepted '
            f'request_id={request.request_id}'
        )
        return final_status, final_decision, current_stage

    def execute(
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
        resume_controller_clarification_session: HumanClarificationSession | None = None,
    ) -> FinalPackage:
        host = self._host
        core_config, _ = runtime_agents['core']
        workflow_state = state_store.state.workflow_state
        plan = workflow_state.run_workflow_plan
        if plan is None:
            raise RuntimeError('Full staged runtime requires a canonical RunWorkflowPlan.')

        host._attach_execution_runtime_service(
            state_store=state_store,
            run_dir=run_dir,
        )
        current_stage = workflow_state.current_stage or plan.entry_stage
        if not plan.contains(current_stage):
            raise RuntimeError('Staged runtime current stage is not present in the workflow plan.')
        state_store.set_current_stage(current_stage)

        final_status = 'failed'
        final_decision = GateDecision.FAIL
        terminal_stage: Stage | None = None
        runtime_control_request: RuntimeControlRequest | None = None
        active_resume_session: HumanClarificationSession | None = (
            resume_clarification_session or resume_controller_clarification_session
        )
        if runtime_continuation is not None and active_resume_session is not None:
            raise RuntimeError('A generic resume directive and an interaction session cannot both be active.')
        pending_stage_entry = stage_resume_entry
        pending_continuation = runtime_continuation
        pending_entry_context = runtime_entry_context or RuntimeEntryContext()

        while True:
            plan = state_store.state.workflow_state.run_workflow_plan or plan
            current_stage = state_store.state.workflow_state.current_stage or current_stage
            if not plan.contains(current_stage):
                raise RuntimeError('Staged runtime lost its current stage cursor.')
            resuming_continuation = bool(
                active_resume_session is not None
                and active_resume_session.stage == current_stage.value
                and int(active_resume_session.attempt or 0) > 0
            )
            matched_continuation = pending_continuation
            if matched_continuation is not None:
                if matched_continuation.stage != current_stage.value:
                    raise RuntimeError(
                        'Active resume directive does not match the current stage: '
                        f'{matched_continuation.stage!r} != {current_stage.value!r}.'
                    )
            resuming_runtime_continuation = matched_continuation is not None
            resume_entry = pending_stage_entry
            pending_stage_entry = None
            stage_entry_resume = resume_entry is not None
            unstarted_stage_entry = resume_entry is not None and not resume_entry.attempt_started
            if resume_entry is not None:
                attempt = resume_entry.attempt
                pending_continuation = None
            elif resuming_continuation:
                if active_resume_session is None:
                    raise RuntimeError('Continuation resume is missing its interaction session.')
                attempt = int(active_resume_session.attempt or 0)
            elif matched_continuation is not None:
                attempt = int(matched_continuation.stage_attempt)
                if resuming_runtime_continuation:
                    pending_continuation = None
            else:
                attempt = int(workflow_state.stage_attempt_counters.get(current_stage.value, 0) + 1)
            stage_is_resuming = resuming_continuation or resuming_runtime_continuation or stage_entry_resume
            resumed_gate = resume_entry.gate if resume_entry is not None else None
            if resumed_gate is not None:
                state_store.take_resume_conversation(
                    stage=current_stage.value, attempt=attempt, actor_kind='core',
                )
                gate_trace = resumed_gate
                pending_continuation = None
                pending_entry_context = RuntimeEntryContext()
                if gate_trace.terminal_status is not None:
                    final_status = gate_trace.terminal_status
                    final_decision = gate_trace.decision
                    terminal_stage = current_stage
                    if final_decision == GateDecision.CLARIFY_HUMAN:
                        host._ensure_gate_human_clarification_session(
                            state_store=state_store, run_dir=run_dir, saved_files=saved_files,
                            stage=current_stage, attempt=attempt, report=None,
                        )
                    break
                if gate_trace.next_stage is None:
                    final_status = 'failed'
                    final_decision = GateDecision.FAIL
                    terminal_stage = current_stage
                    break
                state_store.set_current_stage(gate_trace.next_stage)
                current_stage = gate_trace.next_stage
                active_resume_session = None
                continue
            stage_label = f'{current_stage.value}:attempt{attempt}'
            entry_context = resolve_stage_entry_context(
                pending=pending_entry_context,
                stage_is_resuming=stage_is_resuming,
                matched_directive=matched_continuation,
                active_session=active_resume_session,
                resuming_continuation=resuming_continuation,
            )
            poll_runtime_control = build_runtime_control_poll(
                host=host,
                state_store=state_store,
                run_dir=run_dir,
                saved_files=saved_files,
                current_stage=current_stage,
            )

            approved_stage_plan_path = ''
            approved_stage_plan_sha256 = ''
            if resume_entry is not None:
                approved_stage_plan_path = resume_entry.approved_plan_path
                approved_stage_plan_sha256 = resume_entry.approved_plan_sha256
            plan_mode_enabled = bool(
                getattr(
                    getattr(getattr(host._user_config, 'runtime', None), 'plan_mode', None),
                    'enabled',
                    False,
                )
            )
            if plan_mode_enabled and not approved_stage_plan_path and (resume_entry is None or resume_entry.prep is None):
                from ownpi.runtime.native_tools.plan_mode.placement import (
                    PlanModeStageEntryPlacement,
                )

                placement = PlanModeStageEntryPlacement(host=host).execute(
                    run_dir=run_dir,
                    state_store=state_store,
                    saved_files=saved_files,
                    stage=current_stage,
                    attempt=attempt,
                    entry_context=entry_context,
                    active_resume_session=active_resume_session,
                    resume_entry=resume_entry,
                )
                if placement.status != 'approved':
                    if placement.status == 'user_cancelled':
                        final_status, final_decision, terminal_stage = self._handle_runtime_control(
                            exception=RuntimeControlRequested(RuntimeControlRequest(
                                request_id=f'plan-cancel:{current_stage.value}:{attempt}',
                                action='interrupt', requested_at=now_utc_iso(),
                                reason='Plan Mode cancelled by user',
                            )), state_store=state_store, run_dir=run_dir,
                            saved_files=saved_files,
                            current_stage=current_stage, attempt=attempt, stage_label=stage_label,
                        )
                        break
                    paused = placement.status == 'paused'
                    final_status = 'paused_for_human' if paused else 'failed'
                    final_decision = (
                        GateDecision.CLARIFY_HUMAN
                        if paused
                        else GateDecision.FAIL
                    )
                    terminal_stage = current_stage
                    break
                approved_stage_plan_path = placement.approved_stage_plan_path
                approved_stage_plan_sha256 = placement.approved_stage_plan_sha256
                if placement.consumed_resume_session:
                    active_resume_session = None

            prep_request = StagePrepRequest(
                run_dir=run_dir,
                stage=current_stage,
                level=workflow_state.stage_runtime_profile,
                entry_context=entry_context,
                attempt=attempt if stage_is_resuming and not unstarted_stage_entry else None,
                stage_loop_index=(
                    int(workflow_state.scratchpad.get('stage_loop_index', 1))
                    if stage_is_resuming else 1
                ),
                continuation_index=(
                    resume_entry.continuation_index
                    if resume_entry is not None else active_resume_session.continuation_index
                    if resuming_continuation and active_resume_session is not None
                    else (
                        matched_continuation.continuation_index
                        if matched_continuation is not None
                        else 0
                    )
                ),
                approved_stage_plan_path=approved_stage_plan_path,
                approved_stage_plan_sha256=approved_stage_plan_sha256,
            )
            try:
                if resume_entry is not None and resume_entry.prep is not None:
                    prep_result = StagePrepResult(
                        status='completed', record=resume_entry.prep,
                        handoff=state_store.state.stage_prep_state.active_handoff, reused=True,
                    )
                    state_store.poll_runtime_control('stage_prep_reused')
                else:
                    prep_result = StagePrepService().prepare(
                        request=prep_request, state_store=state_store,
                    )
            except RuntimeControlRequested as exc:
                runtime_control_request = exc.request
                final_status, final_decision, terminal_stage = (
                    self._handle_runtime_control(
                        exception=exc,
                        state_store=state_store,
                        run_dir=run_dir,
                        saved_files=saved_files,
                        current_stage=current_stage,
                        attempt=attempt,
                        stage_label=stage_label,
                    )
                )
                break
            if prep_result.status != 'completed' or prep_result.handoff is None:
                failure_reason = (
                    'stage_prep_failed: '
                    f'{prep_result.record.failure_class}: '
                    f'{prep_result.record.failure_message}'
                )
                failure_details = {
                    'failure_class': prep_result.record.failure_class,
                    'error_message': prep_result.record.failure_message,
                    'resumable': True,
                    'auto_resume_safe': False,
                    'recommended_recovery': 'resume_from_latest_valid_checkpoint',
                    'stage': current_stage.value,
                    'attempt': attempt,
                    'continuation_index': prep_request.continuation_index,
                    'terminal_decision': GateDecision.FAIL.value,
                    'terminal_reason': 'stage_prep_failed',
                    'stage_prep_record': prep_result.record.to_dict(),
                }
                workflow_state.scratchpad['runtime_failure_reason'] = failure_reason
                workflow_state.scratchpad['runtime_failure_details'] = failure_details
                state_store.record_error(
                    message=failure_reason,
                    stage=current_stage,
                    attempt=attempt,
                    agent_id='stage_prep',
                )
                host._emit(f'[{stage_label}] {failure_reason}')
                final_status = 'failed'
                final_decision = GateDecision.FAIL
                terminal_stage = current_stage
                break
            program = None
            if current_stage == Stage.ANALYSIS_DECISION:
                program = build_analysis_stage_program(current_stage)
            revisit_context = state_store.state.workflow_state.revisit_state.current_context_for(
                stage=current_stage,
                attempt=attempt,
            )
            state_store.record_stage_event(
                event_type=('stage_resumed' if stage_is_resuming else 'stage_started'),
                stage=current_stage,
                attempt=attempt,
                payload={
                    'stage_prep_handoff': prep_result.handoff.to_dict(),
                    'revisit_context': revisit_context,
                    'resume_session_id': (
                        active_resume_session.session_id
                        if resuming_continuation and active_resume_session is not None
                        else None
                    ),
                    'resume_source': (
                        'interaction'
                        if resuming_continuation
                        else (
                            matched_continuation.execution_kind
                            if matched_continuation is not None
                            else None
                        )
                    ),
                    'resume_source_id': (
                        matched_continuation.source_record_id
                        if matched_continuation is not None
                        else None
                    ),
                },
            )
            host._emit(
                f'[{stage_label}] {"stage_resumed" if stage_is_resuming else "stage_started"} '
                f'prep_id={prep_result.handoff.prep_id}'
            )
            host._emit_runtime_status(
                state_store=state_store,
                event_kind='stage_status',
                component='controller',
                operation=('stage_resumed' if stage_is_resuming else 'stage_started'),
            )
            try:
                with (
                    state_store.bind_runtime_entry_context(entry_context),
                    state_store.bind_runtime_control_poll(poll_runtime_control),
                ):
                    continuation_index = (
                        resume_entry.continuation_index
                        if resume_entry is not None else active_resume_session.continuation_index
                        if resuming_continuation
                        and active_resume_session is not None
                        and host._is_continuation_runtime_continuation_kind(active_resume_session.continuation_kind)
                        else (
                            matched_continuation.continuation_index
                            if matched_continuation is not None
                            else 0
                        )
                    )
                    if current_stage == Stage.ANALYSIS_DECISION:
                        if program is None:
                            raise RuntimeError('Analysis stage program is missing')
                        attempt_result = host._stage_kernel.execute_analysis_stage(
                            program=program,
                            stage_prep_handoff=prep_result.handoff,
                            state_store=state_store,
                            run_dir=run_dir,
                            saved_files=saved_files,
                            stage=current_stage,
                            attempt=attempt,
                            label=stage_label,
                            runtime_agents=runtime_agents,
                            initial_continuation_index=continuation_index,
                            resume_action=resume_entry.action if resume_entry is not None else None,
                        )
                    else:
                        attempt_result = host._stage_kernel.execute_ordinary_stage(
                            stage_prep_handoff=prep_result.handoff,
                            state_store=state_store,
                            run_dir=run_dir,
                            saved_files=saved_files,
                            stage=current_stage,
                            attempt=attempt,
                            label=stage_label,
                            runtime_agents=runtime_agents,
                            initial_continuation_index=continuation_index,
                            resume_action=resume_entry.action if resume_entry is not None else None,
                        )
                pending_entry_context = RuntimeEntryContext()
                if resuming_continuation and active_resume_session is not None:
                    if active_resume_session is resume_controller_clarification_session:
                        host._clear_controller_clarification_handoff(
                            state_store=state_store,
                            consumed_by_continuation_index=(
                                active_resume_session.continuation_index
                                if active_resume_session is not None
                                else None
                            ),
                        )
                    else:
                        state_store.mark_human_clarification_resumed(
                            session_id=active_resume_session.session_id,
                            resumed_at=now_utc_iso(),
                        )
                    active_resume_session = None
            except RuntimeControlRequested as exc:
                runtime_control_request = exc.request
                active_resume_session = None
                final_status, final_decision, terminal_stage = self._handle_runtime_control(
                    exception=exc,
                    state_store=state_store,
                    run_dir=run_dir,
                    saved_files=saved_files,
                    current_stage=current_stage,
                    attempt=attempt,
                    stage_label=stage_label,
                )
                break
            except HumanClarificationPausedError as exc:
                if exc.pending_interactions:
                    state_store.state.workflow_state.pending_human_clarification = exc.pending_interactions[0]
                if active_resume_session is resume_controller_clarification_session:
                    host._clear_controller_clarification_handoff(
                        state_store=state_store,
                        consumed_by_continuation_index=exc.continuation_index,
                    )
                else:
                    host._materialize_consumed_resume_session(
                        state_store=state_store,
                        session=active_resume_session,
                    )
                active_resume_session = None
                final_status = 'paused_for_human'
                final_decision = GateDecision.CLARIFY_HUMAN
                terminal_stage = current_stage
                state_store.record_stage_event(
                    event_type='human_clarification_paused',
                    stage=current_stage,
                    attempt=attempt,
                    payload={
                        'reason': str(exc),
                        'session_id': exc.session_id,
                        'continuation_kind': exc.continuation_kind,
                        'continuation_index': exc.continuation_index,
                        'step_results': [dict(item) for item in exc.step_results if isinstance(item, dict)],
                    },
                    status='paused_for_human',
                )
                host._emit(
                    f'[{stage_label}] human_clarification_paused session={exc.session_id} '
                    f'resume={exc.continuation_kind}'
                )
                break
            except ToolPermissionPausedError as exc:
                if active_resume_session is resume_controller_clarification_session:
                    host._clear_controller_clarification_handoff(
                        state_store=state_store,
                        consumed_by_continuation_index=exc.continuation_index,
                    )
                else:
                    host._materialize_consumed_resume_session(
                        state_store=state_store,
                        session=active_resume_session,
                    )
                active_resume_session = None
                final_status = 'paused_for_human'
                final_decision = 'await_permission'
                terminal_stage = current_stage
                state_store.record_stage_event(
                    event_type='tool_permission_paused',
                    stage=current_stage,
                    attempt=attempt,
                    payload={
                        'reason': str(exc),
                        'session_id': exc.session_id,
                        'continuation_kind': exc.continuation_kind,
                        'tool_name': exc.tool_name,
                        'continuation_index': exc.continuation_index,
                        'step_results': [dict(item) for item in exc.step_results if isinstance(item, dict)],
                    },
                    status='paused_for_human',
                )
                host._emit(
                    f'[{stage_label}] tool_permission_paused session={exc.session_id} '
                    f'tool={exc.tool_name} resume={exc.continuation_kind}'
                )
                break
            except StageContinuationTerminatedError as exc:
                if active_resume_session is resume_controller_clarification_session:
                    host._clear_controller_clarification_handoff(
                        state_store=state_store,
                        consumed_by_continuation_index=exc.continuation_index,
                    )
                else:
                    host._materialize_consumed_resume_session(
                        state_store=state_store,
                        session=active_resume_session,
                    )
                active_resume_session = None
                failure_reason = str(exc)
                terminal_decision = GateDecision.FAIL
                terminal_reason = (
                    str(exc.terminal_decision_hint or '').strip()
                    or f'continuation_policy:{exc.action}'
                )
                native_failure_details = (
                    state_store.state.workflow_state.scratchpad.get(
                        'runtime_failure_details'
                    )
                )
                failure_details = {
                    **(
                        dict(native_failure_details)
                        if isinstance(native_failure_details, dict)
                        else {}
                    ),
                    'stage': current_stage.value,
                    'attempt': attempt,
                    'continuation_index': exc.continuation_index,
                    'continuation_action': exc.action,
                    'terminal_decision': terminal_decision.value,
                    'terminal_reason': terminal_reason,
                    'step_results': [dict(item) for item in exc.step_results if isinstance(item, dict)],
                }
                state_store.state.workflow_state.scratchpad['runtime_failure_reason'] = failure_reason
                state_store.state.workflow_state.scratchpad['runtime_failure_details'] = failure_details
                state_store.record_stage_event(
                    event_type='runtime_failure',
                    stage=current_stage,
                    attempt=attempt,
                    payload={
                        'reason': failure_reason,
                        'terminal_decision': terminal_decision.value,
                        'terminal_reason': terminal_reason,
                        **failure_details,
                    },
                    status='failed',
                )
                state_store.record_error(
                    message=failure_reason,
                    stage=current_stage,
                    attempt=attempt,
                    agent_id='stage_continuation_loop',
                )
                host._emit(f'[{stage_label}] runtime_failure reason={failure_reason}')
                final_status = 'failed'
                final_decision = terminal_decision
                terminal_stage = current_stage
                break
            except Exception as exc:
                workflow_state = state_store.state.workflow_state
                continuation_index = int(
                    workflow_state.scratchpad.get('continuation_index', 0)
                    or 0
                )
                failure_reason = f'{type(exc).__name__}: {exc}'
                failure_details = {
                    'failure_class': 'runtime_protocol_failure',
                    'error_type': type(exc).__name__,
                    'error_message': str(exc),
                    # An execution exception ends this attempt, but does not
                    # prove that the durable run boundary is unusable. Resume
                    # admission performs the authoritative checkpoint check.
                    'resumable': True,
                    'auto_resume_safe': False,
                    'recommended_recovery': 'resume_from_latest_valid_checkpoint',
                    'stage': current_stage.value,
                    'attempt': attempt,
                    'continuation_index': continuation_index,
                    'terminal_decision': GateDecision.FAIL.value,
                    'terminal_reason': 'runtime_protocol_failure',
                }
                workflow_state.scratchpad['runtime_failure_reason'] = failure_reason
                workflow_state.scratchpad['runtime_failure_details'] = failure_details
                state_store.record_stage_event(
                    event_type='runtime_failure',
                    stage=current_stage,
                    attempt=attempt,
                    payload={'reason': failure_reason, **failure_details},
                    status='failed',
                )
                state_store.record_error(
                    message=failure_reason,
                    stage=current_stage,
                    attempt=attempt,
                    agent_id='staged_runtime_loop',
                )
                host._emit(f'[{stage_label}] runtime_failure reason={failure_reason}')
                final_status = 'failed'
                final_decision = GateDecision.FAIL
                terminal_stage = current_stage
                break
            if attempt_result.gate_trace.terminal_status is not None:
                final_status = attempt_result.gate_trace.terminal_status
                final_decision = attempt_result.gate_trace.decision
                terminal_stage = current_stage
                if final_decision == GateDecision.CLARIFY_HUMAN:
                    host._ensure_gate_human_clarification_session(
                        state_store=state_store,
                        run_dir=run_dir,
                        saved_files=saved_files,
                        stage=current_stage,
                        attempt=attempt,
                        report=None,
                    )
                break
            if attempt_result.gate_trace.next_stage is None:
                final_status = 'failed'
                final_decision = GateDecision.FAIL
                terminal_stage = current_stage
                break
            next_stage = attempt_result.gate_trace.next_stage
            state_store.set_current_stage(next_stage)
            current_stage = next_stage
            active_resume_session = None


        if final_status == 'paused_for_human':
            package = host._finalization_service.build_paused_package(
                state_store=state_store,
                run_dir=run_dir,
                saved_files=saved_files,
                final_decision=final_decision,
                terminal_stage=terminal_stage,
            )
            if runtime_control_request is not None:
                clear_consumed_runtime_control_request(
                    run_dir, request_id=runtime_control_request.request_id,
                )
            return package

        package = (
            host._finalization_service.build_paused_package(
                state_store=state_store,
                run_dir=run_dir,
                saved_files=saved_files,
                final_decision=final_decision,
                terminal_stage=terminal_stage,
                final_status='stopped',
            )
            if final_status == 'stopped'
            else host._finalization_service.finalize_staged_run(
                state_store=state_store,
                run_dir=run_dir,
                saved_files=saved_files,
                final_status=final_status,
                final_decision=final_decision,
                terminal_stage=terminal_stage,
            )
        )
        if runtime_control_request is not None:
            clear_consumed_runtime_control_request(
                run_dir, request_id=runtime_control_request.request_id,
            )
        return package
