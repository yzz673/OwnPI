from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ownpi.controller.stages import Stage
from ownpi.schemas.feedback import WorkspaceReviewTarget
from ownpi.static_semantic_contracts import resolve_stage_contract
from ownpi.runtime.call_continuation.critic_loop import (
    reset_critic_loop,
)
from ownpi.runtime.call_continuation.critic_policy import (
    CriticLoopPolicy,
    reviewer_policy,
)
from ownpi.runtime.call_continuation.state import (
    CALL_CONTINUATION_OPERATION_SCRATCHPAD_KEY,
    OP_NATIVE_TOOL_LOOP,
    CallContinuationOperation,
)
from ownpi.runtime.events import now_utc_iso
from ownpi.runtime.interactions.pause import raise_pending_actor_interaction
from ownpi.runtime.native_tools.role_continuation import (
    RoleLoopReceiptSnapshot,
    analysis_continuation_policy,
    core_feedback_continuation_policy,
    evaluate_role_loop_continuation,
)
from ownpi.runtime.native_tools.core_agent_loop import CoreAgentLoopContinuation
from ownpi.runtime.stage_prep.models import StagePrepHandoff
from ownpi.runtime.stage_execution.loop_policy import is_analysis_route_loop
from ownpi.runtime.state_models import FeedbackHandoffState
from ownpi.schemas.artifacts import GateTrace
from ownpi.runtime.native_tools.action_families import (
    ACTION_EDIT,
    ACTION_EXECUTE_LOCAL,
    ACTION_EXECUTE_REMOTE,
    ACTION_INSPECT,
    ACTION_VERIFY_EVIDENCE,
)
from ownpi.schemas.runtime import AgentInstanceConfig
from ownpi.schemas.runtime_human_clarification import (
    CONTROLLER_CLARIFICATION_HANDOFF_KEY,
    active_controller_clarification_handoff,
)
from ownpi.schemas.runtime_tooling import ToolObservation

_NATIVE_LOOP_TERMINAL_FAILURES = {
    "local_runtime_failure": "native_tool_local_runtime_failure",
    "tool_result_projection_failure": "native_tool_result_projection_failure",
    "provider_or_protocol_failure": "native_tool_provider_or_protocol_failure",
}


def _workspace_review_target(state_store: Any, stage: Stage, attempt: int) -> WorkspaceReviewTarget:
    state = state_store.state
    receipt = state.feedback_state.critic_loop.core_intent_receipt
    if (
        receipt is None or receipt.intent != "request_review"
        or receipt.source_stage != stage.value or receipt.attempt != attempt
    ):
        raise ValueError("review requires a matching request_review receipt")
    index = int(state.workflow_state.scratchpad.get("stage_loop_index", 1))
    bundle = resolve_stage_contract(stage, state.workflow_state.stage_runtime_profile)
    contract = bundle.loop_contracts[index - 1]
    return WorkspaceReviewTarget(
        run_id=state.run_metadata.run_id, stage=stage.value,
        attempt=attempt, loop_index=index,
        contract_sha256=contract.contract_sha256, request_id=receipt.tool_call_id,
    )


@dataclass(frozen=True)
class StageAttemptResult:
    gate_trace: GateTrace
    stage_review_artifact_type: str
    step_results: list[dict[str, Any]]


@dataclass(frozen=True)
class CoreRoundSpec:
    agent_role: str
    operation: str
    event_name: str
    filename_pattern: str
    result_kind: str = 'core_round'

    def render_filename(self, *, stage_value: str, attempt: int) -> str:
        return self.filename_pattern.format(stage=stage_value, attempt=attempt)


@dataclass(frozen=True)
class AnalysisStageProgram:
    stage: Stage
    core_round: CoreRoundSpec

    def __post_init__(self) -> None:
        if self.stage != Stage.ANALYSIS_DECISION:
            raise ValueError('AnalysisStageProgram only accepts analysis_decision')


@dataclass
class StageContinuationTerminatedError(RuntimeError):
    stage: Stage
    attempt: int
    continuation_index: int
    action: str
    reason: str
    step_results: list[dict[str, Any]]
    terminal_decision_hint: str | None = None

    def __str__(self) -> str:
        return self.reason


class StageKernel:
    def __init__(
        self,
        *,
        run_agent_operation: Callable[..., Any],
        stream_agent_operation: Callable[..., Any] | None = None,
        runtime_gate: Callable[..., GateTrace],
        emit: Callable[[str], None],
    ) -> None:
        self._run_agent_operation = run_agent_operation
        self._stream_agent_operation = stream_agent_operation
        self._runtime_gate = runtime_gate
        self._emit = emit

    def execute_ordinary_stage(
        self,
        *,
        stage_prep_handoff: StagePrepHandoff,
        state_store: Any,
        run_dir: Path,
        saved_files: list[str],
        stage: Stage,
        attempt: int,
        label: str,
        runtime_agents: dict[str, tuple[AgentInstanceConfig, Any]],
        initial_continuation_index: int = 0,
        resume_action: str | None = None,
    ) -> StageAttemptResult:
        if stage == Stage.ANALYSIS_DECISION:
            raise ValueError('Analysis must use the Analysis program entry')
        _validate_stage_prep_handoff(
            handoff=stage_prep_handoff,
            state_store=state_store,
            stage=stage,
            attempt=attempt,
        )
        core_round = CoreRoundSpec(
            agent_role='core',
            operation='core_stage_round',
            event_name='core_round_completed',
            filename_pattern='stage_{stage}_core_round_attempt_{attempt}.json',
        )
        step_results: list[dict[str, Any]] = []
        restored_loop = state_store.state.feedback_state.critic_loop
        if (state_store.state.workflow_state.scratchpad.get('rollout_resume_facts') is not None
                and restored_loop.feedback_record_id):
            from ownpi.runtime.rollout.resume_delivery import deliver_completed_review

            deliver_completed_review(
                state_store, record_id=restored_loop.feedback_record_id, run_root=run_dir,
                read_session=state_store.state.workflow_state.scratchpad['rollout_resume_facts'].read_session,
            )
        if resume_action != 'reviewer':
            state_store.state.workflow_state.scratchpad['continuation_index'] = initial_continuation_index

        stage_review_artifact_type, reviewer_results = self._run_reviewer_loop(
            core_round=core_round,
            stage_prep_handoff=stage_prep_handoff,
            state_store=state_store,
            run_dir=run_dir,
            saved_files=saved_files,
            stage=stage,
            attempt=attempt,
            label=label,
            runtime_agents=runtime_agents,
        )
        step_results.extend(reviewer_results)
        return self._complete_ordinary_stage(
            stage_prep_handoff=stage_prep_handoff,
            state_store=state_store,
            run_dir=run_dir,
            saved_files=saved_files,
            stage=stage,
            attempt=attempt,
            label=label,
            step_results=step_results,
            stage_review_artifact_type=stage_review_artifact_type,
        )

    def execute_analysis_stage(
        self,
        *,
        program: AnalysisStageProgram,
        stage_prep_handoff: StagePrepHandoff,
        state_store,
        run_dir: Path,
        saved_files: list[str],
        stage: Stage,
        attempt: int,
        label: str,
        runtime_agents: dict[str, tuple[AgentInstanceConfig, Any]],
        initial_continuation_index: int = 0,
        resume_action: str | None = None,
    ) -> StageAttemptResult:
        if stage != Stage.ANALYSIS_DECISION:
            raise ValueError('execute_analysis_stage only accepts analysis_decision')
        if program.stage != stage:
            raise ValueError('Analysis program does not match the current stage')
        _validate_stage_prep_handoff(
            handoff=stage_prep_handoff,
            state_store=state_store,
            stage=stage,
            attempt=attempt,
        )
        workflow = state_store.state.workflow_state
        index = int(workflow.scratchpad.get('stage_loop_index', 1))
        bundle = resolve_stage_contract(stage, workflow.stage_runtime_profile)
        if stage_prep_handoff.loop_count != len(bundle.loop_contracts):
            raise ValueError('Analysis handoff loop count differs from the current contract bundle')
        route_loop = is_analysis_route_loop(stage, workflow.stage_runtime_profile, index)
        step_results: list[dict[str, Any]] = []
        if not route_loop:
            restored = state_store.state.feedback_state.critic_loop
            facts = workflow.scratchpad.get('rollout_resume_facts')
            if facts is not None and restored.feedback_record_id:
                from ownpi.runtime.rollout.resume_delivery import deliver_completed_review

                deliver_completed_review(
                    state_store, record_id=restored.feedback_record_id, run_root=run_dir,
                    read_session=facts.read_session,
                )
            if resume_action != 'reviewer':
                workflow.scratchpad['continuation_index'] = initial_continuation_index
            _, prefix_results = self._run_reviewer_loop(
                core_round=CoreRoundSpec(
                    agent_role='core', operation='core_stage_round',
                    event_name='core_round_completed',
                    filename_pattern='stage_{stage}_core_round_attempt_{attempt}.json',
                ),
                stage_prep_handoff=stage_prep_handoff, state_store=state_store,
                run_dir=run_dir, saved_files=saved_files, stage=stage, attempt=attempt,
                label=label, runtime_agents=runtime_agents,
                stop_before_index=stage_prep_handoff.loop_count,
            )
            step_results.extend(prefix_results)
            initial_continuation_index = 0
            resume_action = None
        restored_critic_loop = state_store.state.feedback_state.critic_loop
        if (
            restored_critic_loop.source_stage == stage.value
            and restored_critic_loop.attempt == attempt
            and restored_critic_loop.phase != "idle"
        ):
            raise RuntimeError(
                "analysis_protocol_incompatible_checkpoint: active critic state is not valid "
                "for single-core Analysis"
            )
        stage_review_artifact_type = "analysis_feedback"
        core_round = program.core_round
        if resume_action == 'analysis_route':
            result = None
            produce_step_results = []
        else:
            result, produce_step_results = self._run_produce_with_continuation_loop(
                core_round=core_round,
                state_store=state_store,
                run_dir=run_dir,
                saved_files=saved_files,
                stage=stage,
                attempt=attempt,
                label=label,
                runtime_agents=runtime_agents,
                start_round_index=initial_continuation_index,
                return_on_initial_core_complete=True,
            )
        step_results.extend(produce_step_results)
        if result is not None and result.artifact_type != 'native_tool_loop_result':
            native_status = _native_loop_status_from_result(result)
            raise RuntimeError(
                'analysis_protocol_failure: Analysis Core must finish with successful '
                'submit_feedback and next_stage calls; '
                f'native_status={native_status or "missing"}'
            )
        self._emit(f'[{label}] {core_round.event_name}')

        gate_trace = self._runtime_gate(
            state_store=state_store,
            stage=stage,
            attempt=attempt,
        )
        step_results.append(
            {
                "kind": "gate",
                "operation": "runtime_gate",
                "agent_role": "runtime_gate",
                "artifact_type": "gate_trace",
                "status": gate_trace.terminal_status or "success",
                "summary": gate_trace.decision.value,
            }
        )
        state_store.record_gate_outcome(
            stage=stage,
            attempt=attempt,
            gate_trace=gate_trace,
        )
        self._emit(f"[{label}] gate_decision={gate_trace.decision.value}")
        state_store.poll_runtime_control('stage_gate_outcome_recorded')
        return StageAttemptResult(
            gate_trace=gate_trace,
            stage_review_artifact_type=stage_review_artifact_type,
            step_results=step_results,
        )

    def _complete_ordinary_stage(
        self,
        *,
        stage_prep_handoff: StagePrepHandoff,
        state_store: Any,
        run_dir: Path,
        saved_files: list[str],
        stage: Stage,
        attempt: int,
        label: str,
        step_results: list[dict[str, Any]],
        stage_review_artifact_type: str,
    ) -> StageAttemptResult:
        gate_trace = self._runtime_gate(
            state_store=state_store,
            stage=stage,
            attempt=attempt,
        )
        step_results.append(
            {
                'kind': 'gate',
                'operation': 'runtime_gate',
                'agent_role': 'runtime_gate',
                'artifact_type': 'gate_trace',
                'status': gate_trace.terminal_status or 'success',
                'summary': gate_trace.decision.value,
            }
        )
        state_store.record_gate_outcome(
            stage=stage,
            attempt=attempt,
            gate_trace=gate_trace,
        )
        self._emit(f'[{label}] gate_decision={gate_trace.decision.value}')
        state_store.poll_runtime_control('stage_gate_outcome_recorded')
        return StageAttemptResult(
            gate_trace=gate_trace,
            stage_review_artifact_type=stage_review_artifact_type,
            step_results=step_results,
        )

    def _run_reviewer_loop(
        self,
        *,
        core_round: CoreRoundSpec,
        stage_prep_handoff: StagePrepHandoff,
        state_store: Any,
        run_dir: Path,
        saved_files: list[str],
        stage: Stage,
        attempt: int,
        label: str,
        runtime_agents: dict[str, tuple[AgentInstanceConfig, Any]],
        stop_before_index: int | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        policy = reviewer_policy()
        results: list[dict[str, Any]] = []
        while True:
            transition, loop_results = self._run_critic_loop(
                core_round=core_round,
                stage_prep_handoff=stage_prep_handoff,
                policy=policy,
                critic_role='reviewer',
                critic_operation='reviewer_review',
                critic_event_name='stage_review_written',
                state_store=state_store,
                run_dir=run_dir,
                saved_files=saved_files,
                stage=stage,
                attempt=attempt,
                label=label,
                runtime_agents=runtime_agents,
            )
            results.extend(loop_results)
            current_index = int(
                state_store.state.workflow_state.scratchpad.get('stage_loop_index', 1)
            )
            if transition != 'stage_advance' or current_index >= stage_prep_handoff.loop_count:
                if transition != 'stage_advance':
                    raise RuntimeError(
                        f'Reviewer critic loop returned unsupported transition: {transition}'
                    )
                break
            from ownpi.runtime.rollout.stage_business_events import emit_stage_loop_advanced

            emit_stage_loop_advanced(
                state_store.run_event_store, stage=stage.value, attempt=attempt,
                loop_index=current_index + 1,
            )
            state_store.state.workflow_state.scratchpad['stage_loop_index'] = current_index + 1
            reset_critic_loop(state_store.state.feedback_state)
            state_store.state.workflow_state.scratchpad['continuation_index'] = 0
            if stop_before_index is not None and current_index + 1 >= stop_before_index:
                state_store.state.feedback_state.active_handoff = FeedbackHandoffState()
                state_store.poll_runtime_control('stage_critic_loop_advanced')
                break
        return "stage_review", results

    def _run_critic_loop(
        self,
        *,
        core_round: CoreRoundSpec,
        stage_prep_handoff: StagePrepHandoff,
        policy: CriticLoopPolicy,
        critic_role: str,
        critic_operation: str,
        critic_event_name: str,
        state_store: Any,
        run_dir: Path,
        saved_files: list[str],
        stage: Stage,
        attempt: int,
        label: str,
        runtime_agents: dict[str, tuple[AgentInstanceConfig, Any]],
    ) -> tuple[str, list[dict[str, Any]]]:
        from ownpi.controller.critic_loop import CriticLoopBindings, execute_critic_loop

        critic_config, critic = runtime_agents[critic_role]

        def run_core(initial: bool, on_complete: Callable[[], None] | None) -> list[dict[str, Any]]:
            _, results = self._run_produce_with_continuation_loop(
                core_round=core_round, state_store=state_store, run_dir=run_dir,
                saved_files=saved_files, stage=stage, attempt=attempt, label=label,
                runtime_agents=runtime_agents,
                start_round_index=int(state_store.state.workflow_state.scratchpad.get('continuation_index', 0) or 0),
                return_on_initial_core_complete=initial,
                on_initial_core_complete=on_complete,
            )
            if initial:
                if int(state_store.state.workflow_state.scratchpad.get('stage_loop_index', 1)) > 1:
                    state_store.poll_runtime_control('stage_critic_loop_advanced')
                else:
                    self._emit(f'[{label}] core_round_completed')
            return results

        def run_reviewer() -> Any:
            loop = state_store.state.feedback_state.critic_loop
            return self._run_agent_operation_streaming_if_available(
                state_store=state_store, run_dir=run_dir, saved_files=saved_files,
                stage=stage, attempt=attempt, label=label,
                agent_config=critic_config, agent=critic, operation=critic_operation,
                filename=f'stage_{stage.value}_{policy.critic_kind}_round_{loop.round:03d}_attempt_{attempt}.json',
                event_name=critic_event_name,
            )

        route, results = execute_critic_loop(CriticLoopBindings(
            state_store=state_store, run_dir=run_dir, stage=stage.value, attempt=attempt,
            review_target=lambda: _workspace_review_target(state_store, stage, attempt),
            run_core=run_core, run_reviewer=run_reviewer, saved_files=saved_files,
            completion_safe_point='critic_loop_stage_advance',
        ))
        return ('stage_advance' if route == 'critic_complete' else route), results

    def _run_produce_with_continuation_loop(
        self,
        *,
        core_round: CoreRoundSpec,
        state_store,
        run_dir: Path,
        saved_files: list[str],
        stage: Stage,
        attempt: int,
        label: str,
        runtime_agents: dict[str, tuple[AgentInstanceConfig, Any]],
        start_round_index: int = 0,
        return_on_initial_core_complete: bool = False,
        on_initial_core_complete: Callable[[], None] | None = None,
        native_loop_continuation: CoreAgentLoopContinuation | None = None,
    ) -> tuple[Any, list[dict[str, Any]]]:
        agent_config, agent = runtime_agents[core_round.agent_role]
        round_index = start_round_index
        step_results: list[dict[str, Any]] = []
        metadata = state_store.state.run_metadata
        owns_continuation = native_loop_continuation is None
        native_loop_continuation = native_loop_continuation or CoreAgentLoopContinuation(
            identity=(
                str(metadata.run_id or ''), stage.value, str(attempt),
                core_round.agent_role,
            )
        )
        try:
            while True:
                active_phase = _current_call_continuation_phase(
                    state_store=state_store,
                    stage=stage,
                    attempt=attempt,
                    run_dir=run_dir,
                )
                state_store.state.workflow_state.scratchpad[
                    "native_tool_progress_check_mode"
                ] = True
                state_store.state.workflow_state.scratchpad["continuation_index"] = (
                    round_index
                )
                state_store.state.workflow_state.scratchpad["query_chain_id"] = str(
                    state_store.state.workflow_state.current_query_chain_id or ""
                )
                state_store.state.workflow_state.scratchpad["query_chain_depth"] = int(
                    state_store.state.workflow_state.current_query_chain_depth or 0
                )
                state_store.poll_runtime_control('core_continuation_started')
                step_path = run_dir / core_round.render_filename(
                    stage_value=stage.value, attempt=attempt
                )
                result = self._run_agent_operation_streaming_if_available(
                    state_store=state_store,
                    run_dir=run_dir,
                    saved_files=saved_files,
                    stage=stage,
                    attempt=attempt,
                    label=label,
                    agent_config=agent_config,
                    agent=agent,
                    operation=core_round.operation,
                    filename=step_path.name,
                    event_name=core_round.event_name,
                    native_loop_continuation=native_loop_continuation,
                )
                self._consume_controller_clarification_handoff(
                    state_store=state_store,
                    stage=stage,
                    attempt=attempt,
                )
                raise_pending_actor_interaction(
                    state_store=state_store,
                    stage=stage,
                    attempt=attempt,
                    continuation_index=round_index,
                    step_results=step_results,
                )
                native_loop_decision = self._handle_native_tool_loop_result(
                    state_store=state_store,
                    stage=stage,
                    attempt=attempt,
                    round_index=round_index,
                    label=label,
                    result=result,
                    run_dir=run_dir,
                )
                if native_loop_decision is not None:
                    step_result = {
                        "kind": "runtime_nudge",
                        "operation": native_loop_decision["operation"],
                        "agent_role": "stage_kernel",
                        "artifact_type": result.artifact_type,
                        "status": native_loop_decision["status"],
                        "file_path": str(
                            result.metadata.get("artifact_path", step_path)
                        ),
                        "summary": native_loop_decision["reason"],
                        "continuation_index": round_index,
                    }
                    step_results.append(step_result)
                    if native_loop_decision["status"] == "failed":
                        raise StageContinuationTerminatedError(
                            stage=stage,
                            attempt=attempt,
                            continuation_index=round_index,
                            action=str(native_loop_decision.get("action") or "fail"),
                            reason=native_loop_decision["reason"],
                            terminal_decision_hint=str(
                                native_loop_decision.get("terminal_decision_hint")
                                or "fail"
                            ),
                            step_results=list(step_results),
                        )
                    if native_loop_decision["operation"] in {
                        'core_feedback_intent_complete',
                        'analysis_route_complete',
                    }:
                        return result, step_results
                    if (
                        native_loop_decision["operation"] == "initial_core_complete"
                        and return_on_initial_core_complete
                    ):
                        if on_initial_core_complete is not None:
                            on_initial_core_complete()
                        return result, step_results
                    if (
                        str(active_phase.get("operation", "") or "")
                        == OP_NATIVE_TOOL_LOOP
                        and native_loop_decision["status"] == "nudged"
                        and native_loop_decision["operation"] == OP_NATIVE_TOOL_LOOP
                    ):
                        round_index += 1
                    continue
                step_result = {
                    "kind": core_round.result_kind,
                    "operation": core_round.operation,
                    "agent_role": core_round.agent_role,
                    "artifact_type": result.artifact_type,
                    "status": result.status,
                    "file_path": str(result.metadata.get("artifact_path", step_path)),
                    "summary": result.summary,
                    "continuation_index": round_index,
                }
                step_results.append(step_result)
                return result, step_results
        finally:
            state_store.state.workflow_state.scratchpad.pop("continuation_index", None)
            state_store.state.workflow_state.scratchpad.pop("query_chain_id", None)
            state_store.state.workflow_state.scratchpad.pop("query_chain_depth", None)
            state_store.state.workflow_state.scratchpad.pop(
                "native_tool_progress_check_mode", None
            )
            state_store.clear_controller_clarification_handoff()
            if owns_continuation:
                native_loop_continuation.close()


    @staticmethod
    def _consume_controller_clarification_handoff(
        *,
        state_store,
        stage: Stage,
        attempt: int,
    ) -> None:
        scratchpad = state_store.state.workflow_state.scratchpad
        handoff = active_controller_clarification_handoff(
            scratchpad.get(CONTROLLER_CLARIFICATION_HANDOFF_KEY),
            stage=stage.value,
            attempt=attempt,
        )
        if handoff is None:
            return
        state_store.mark_controller_clarification_handoff_result_consumed(
            session_id=str(handoff.get("session_id", "") or ""),
            consumed_by_continuation_index=int(
                scratchpad.get("continuation_index", 0) or 0
            ),
        )
        state_store.clear_controller_clarification_handoff()

    def _handle_native_tool_loop_result(
        self,
        *,
        state_store,
        stage: Stage,
        attempt: int,
        round_index: int,
        label: str,
        result: Any,
        run_dir: Path | None = None,
    ) -> dict[str, Any] | None:
        if getattr(result, "artifact_type", None) != "native_tool_loop_result":
            return None
        terminal_decision = _native_loop_terminal_status_decision(
            state_store=state_store,
            stage=stage,
            attempt=attempt,
            round_index=round_index,
            result=result,
        )
        if terminal_decision is not None:
            return terminal_decision
        decision = _route_after_core_tool_loop(
            state_store=state_store,
            stage=stage,
            attempt=attempt,
            round_index=round_index,
            result=result,
            reason="physical Core tool loop ended; resolve the canonical packet lifecycle route",
        )
        self._emit(
            f"[{label}] native_tool_loop_route continuation={round_index} status={decision['status']}"
        )
        return decision

    def _run_agent_operation_streaming_if_available(self, **kwargs):
        if self._stream_agent_operation is None:
            return self._run_agent_operation(**kwargs)
        result = self._stream_agent_operation(**kwargs)
        if result is not None:
            return result
        return self._run_agent_operation(**kwargs)


def build_analysis_stage_program(stage: Stage) -> AnalysisStageProgram:
    if stage != Stage.ANALYSIS_DECISION:
        raise ValueError('build_analysis_stage_program requires analysis_decision')
    return AnalysisStageProgram(
        stage=stage,
        core_round=CoreRoundSpec(
            agent_role='core',
            operation='analysis_core_round',
            event_name='analysis_core_round_completed',
            filename_pattern='stage_{stage}_analysis_core_round_attempt_{attempt}.json',
            result_kind='produce',
        ),
    )


def _validate_stage_prep_handoff(
    *,
    handoff: StagePrepHandoff,
    state_store: Any,
    stage: Stage,
    attempt: int,
) -> None:
    active_record = state_store.state.stage_prep_state.active_record()
    if (
        active_record is None
        or active_record.prep_id != handoff.prep_id
        or active_record.stage != stage.value
        or active_record.attempt != attempt
    ):
        raise ValueError('Stage Prep handoff identity does not match stage attempt')


def _native_loop_status_from_result(result: Any) -> str:
    payload = getattr(result, "payload", None)
    payload = payload if isinstance(payload, dict) else {}
    metadata = getattr(result, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    native_loop_value = metadata.get("native_tool_loop")
    native_loop: dict[str, Any] = (
        native_loop_value if isinstance(native_loop_value, dict) else {}
    )
    return str(payload.get("status") or native_loop.get("status") or "").strip()


def _native_loop_terminal_status_decision(
    *,
    state_store: Any,
    stage: Stage,
    attempt: int,
    round_index: int,
    result: Any,
) -> dict[str, str] | None:
    status = _native_loop_status_from_result(result)
    from ownpi.runtime.native_tools.loop_outcome import classify_loop_outcome
    from ownpi.runtime.interactions.pause import raise_loop_cancellation

    boundary = classify_loop_outcome(status)
    if boundary.kind == 'cancelled':
        raise_loop_cancellation(state_store, stage=stage, attempt=attempt, index=round_index)
    if boundary.kind != 'failed':
        return None
    operation = _NATIVE_LOOP_TERMINAL_FAILURES.get(status)
    if operation is None:
        return None
    metadata = getattr(result, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    native_loop_value = metadata.get("native_tool_loop")
    native_loop: dict[str, Any] = (
        native_loop_value if isinstance(native_loop_value, dict) else {}
    )
    failure_diagnostics_value = native_loop.get("failure_diagnostics")
    failure_diagnostics = (
        {
            str(key): value
            for key, value in failure_diagnostics_value.items()
        }
        if isinstance(failure_diagnostics_value, dict)
        else {}
    )
    reason = (
        f"native tool loop returned terminal failure status={status}; stage continuation is not allowed. "
        f"stage={stage.value}; attempt={attempt}; continuation={round_index}."
    )
    state_store.record_tool_observation(
        ToolObservation(
            observation_id=f"runtime_guidance:native_terminal_failure:{stage.value}:{attempt}:{round_index}",
            source_type="runtime_guidance",
            source_name="stage_kernel",
            stage=stage.value,
            attempt=attempt,
            continuation_index=round_index,
            summary=reason,
            created_at=now_utc_iso(),
        )
    )
    record = {
        "stage": stage.value,
        "attempt": attempt,
        "continuation_index": round_index,
        "native_status": status,
        "operation": operation,
        "status": "failed",
        "reason": reason,
        "failure_class": str(native_loop.get("failure_class") or ""),
        "failure_diagnostics": failure_diagnostics,
        "resumable": True,
        "auto_resume_safe": bool(native_loop.get("auto_resume_safe", False)),
        "recommended_recovery": str(
            native_loop.get("recommended_recovery")
            or "resume_from_latest_valid_checkpoint"
        ),
        "created_at": now_utc_iso(),
    }
    scratchpad = state_store.state.workflow_state.scratchpad
    scratchpad["native_tool_loop_last_terminal_failure"] = record
    scratchpad["runtime_failure_details"] = {
        **dict(record),
        "terminal_reason": operation,
    }
    history = scratchpad.setdefault("native_tool_loop_terminal_failure_history", [])
    if isinstance(history, list):
        history.append(dict(record))
        del history[:-64]
    scratchpad.pop(CALL_CONTINUATION_OPERATION_SCRATCHPAD_KEY, None)
    return {
        "operation": operation,
        "status": "failed",
        "action": "fail",
        "terminal_decision_hint": operation,
        "reason": reason,
        "failure_class": str(record["failure_class"]),
        "resumable": "true",
        "auto_resume_safe": str(bool(record["auto_resume_safe"])).lower(),
        "recommended_recovery": str(record["recommended_recovery"]),
    }


def _route_after_core_tool_loop(
    *,
    state_store: Any,
    stage: Stage,
    attempt: int,
    round_index: int,
    result: Any,
    reason: str,
) -> dict[str, str]:
    scratchpad = state_store.state.workflow_state.scratchpad
    feedback_state = state_store.state.feedback_state
    if stage == Stage.ANALYSIS_DECISION and is_analysis_route_loop(
        stage, state_store.state.workflow_state.stage_runtime_profile,
        int(scratchpad.get('stage_loop_index', 1)),
    ):
        transition = feedback_state.analysis_transition
        decision = evaluate_role_loop_continuation(
            policy=analysis_continuation_policy(),
            receipts=RoleLoopReceiptSnapshot(
                feedback_submitted=bool(
                    transition.source_stage == stage.value
                    and transition.attempt == attempt
                    and transition.feedback_record_id
                ),
                next_stage=bool(
                    transition.next_stage_receipt is not None
                    and transition.next_stage_receipt.attempt == attempt
                ),
            ),
        )
        if decision.complete:
            scratchpad.pop('role_loop_continuation_nudge', None)
            return {
                'operation': 'analysis_route_complete',
                'status': 'completed',
                'action': 'runtime_gate',
                'reason': 'Analysis feedback and next-stage receipts are durable.',
            }
        scratchpad['role_loop_continuation_nudge'] = decision.nudge
        return {
            'operation': OP_NATIVE_TOOL_LOOP,
            'status': 'nudged',
            'action': 'continue',
            'reason': decision.nudge,
        }
    critic_loop = feedback_state.critic_loop
    if (
        critic_loop.phase == 'idle' or (
        critic_loop.phase
        in {'core_post_feedback', 'review_requested', 'completed'}
        and critic_loop.source_stage == stage.value
        and critic_loop.attempt == attempt)
    ):
        receipt = critic_loop.core_intent_receipt
        decision = evaluate_role_loop_continuation(
            policy=core_feedback_continuation_policy(direct=stage == Stage.DIRECT_QA),
            receipts=RoleLoopReceiptSnapshot(
                request_review=bool(
                    receipt is not None and receipt.intent == 'request_review'
                    and receipt.source_stage == stage.value and receipt.attempt == attempt
                ),
                complete_feedback=bool(
                    receipt is not None and receipt.intent == 'complete_feedback'
                ),
            ),
        )
        if decision.complete:
            scratchpad.pop('role_loop_continuation_nudge', None)
            return {
                'operation': ('initial_core_complete' if critic_loop.phase == 'idle'
                              else 'core_feedback_intent_complete'),
                'status': 'completed',
                'action': receipt.intent if receipt is not None else '',
                'reason': 'Core feedback terminal intent is durable.',
            }
        scratchpad['role_loop_continuation_nudge'] = decision.nudge
        return {
            'operation': OP_NATIVE_TOOL_LOOP,
            'status': 'nudged',
            'action': 'continue',
            'reason': decision.nudge,
        }
    raise ValueError('Core exited with an unexpected critic phase')


def _current_call_continuation_phase(
    *,
    state_store: Any,
    stage: Stage,
    attempt: int,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    scratchpad = state_store.state.workflow_state.scratchpad
    marker = CallContinuationOperation.from_dict(
        scratchpad.get(CALL_CONTINUATION_OPERATION_SCRATCHPAD_KEY)
    )
    if (
        marker is not None
        and marker.status == "pending"
        and marker.stage == stage.value
        and int(marker.attempt) == int(attempt)
    ):
        return {
            "operation": marker.operation,
            "reason": marker.reason,
            "marker_status": marker.status,
        }
    return {
        "operation": OP_NATIVE_TOOL_LOOP,
        "reason": "default call continuation phase is provider-native tool loop",
    }


def _action_families_for_tool(*, tool_name: str, spec: dict[str, Any]) -> set[str]:
    execution_class = str(spec.get("execution_class", "") or "").casefold()
    permission_class = str(spec.get("permission_class", "") or "").casefold()
    read_only = bool(spec.get("read_only", False))
    is_remote_tool = (
        "remote" in execution_class
        or "remote" in permission_class
        or tool_name.startswith("remote_")
    )
    families: set[str] = set()
    if read_only or execution_class == "read_only":
        families.add("inspect")
    if is_remote_tool:
        if tool_name in {"remote_monitor", "remote_logs"}:
            families.add("monitor")
        elif tool_name == "remote_pull":
            families.add("retrieve_artifacts")
        elif not read_only:
            families.add(ACTION_EXECUTE_REMOTE)
    if (
        not read_only
        and not is_remote_tool
        and any(token in execution_class for token in ("command", "execution", "local"))
    ):
        families.add(ACTION_EXECUTE_LOCAL)
    if not read_only and any(token in permission_class for token in ("edit", "file")):
        families.add(ACTION_EDIT)
    if tool_name in {"patch_apply"}:
        families.add(ACTION_EDIT)
    if tool_name in {"exec_command"}:
        families.add(ACTION_EXECUTE_LOCAL)
    if tool_name in {
        "web_search",
        "web_fetch",
        "paper_fetch",
    }:
        families.add(ACTION_INSPECT)
        families.add(ACTION_VERIFY_EVIDENCE)
    if not families:
        families.add("inspect" if read_only else ACTION_EXECUTE_LOCAL)
    return families
