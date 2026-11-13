from __future__ import annotations

from pathlib import Path
import logging
from typing import Any, Callable

from ownpi.controller.state_store import WorkflowStateStore
from ownpi.controller.stages import GateDecision, Stage
from ownpi.schemas.artifacts import FinalPackage, StageRunSummary
from ownpi.controller.final_package_builder import stage_summaries_from_gates


TERMINAL_RUN_STATUSES = {'completed', 'failed', 'cancelled', 'interrupted'}


class FinalizationService:
    """Builds terminal in-memory results and dispatches lifecycle hooks."""

    def __init__(
        self,
        *,
        build_final_package: Callable[..., FinalPackage],
        is_full_tier_runtime: Callable[[Any], bool],
        shutdown_execution_runtime: Callable[[WorkflowStateStore, bool], None] | None = None,
        emit_session_end: Callable[..., None] | None = None,
    ) -> None:
        self._build_final_package = build_final_package
        self._is_full_tier_runtime = is_full_tier_runtime
        self._shutdown_execution_runtime = shutdown_execution_runtime
        self._emit_session_end = emit_session_end

    def build_paused_package(
        self,
        *,
        state_store: WorkflowStateStore,
        run_dir: Path,
        saved_files: list[str],
        stage_runs: list[StageRunSummary] | None = None,
        final_decision: GateDecision | str,
        terminal_stage: Stage | None,
        final_status: str = 'paused_for_human',
    ) -> FinalPackage:
        self._shutdown_execution(state_store, terminal=False)
        return self._build_final_package(
            state_store=state_store,
            run_dir=run_dir,
            saved_files=saved_files,
            stage_runs=stage_runs,
            final_status=final_status,
            final_decision=final_decision,
            terminal_stage=terminal_stage,
        )

    def finalize_staged_run(
        self,
        *,
        state_store: WorkflowStateStore,
        run_dir: Path,
        saved_files: list[str],
        final_status: str,
        final_decision: GateDecision,
        terminal_stage: Stage | None,
    ) -> FinalPackage:
        failure: BaseException | None = None
        try:
            self._shutdown_execution(state_store, terminal=True)
            self._record_run_finalized_event(
                state_store=state_store,
                final_status=final_status,
                final_decision=final_decision,
                terminal_stage=terminal_stage,
                extra_payload={'stage_run_count': len(stage_summaries_from_gates(state_store))},
            )
            final_package = self._build_final_package(
                state_store=state_store,
                run_dir=run_dir,
                saved_files=saved_files,
                final_status=final_status,
                final_decision=final_decision,
                terminal_stage=terminal_stage,
            )
            self._dispatch_session_end(
                state_store=state_store,
                run_dir=run_dir,
                reason=final_status,
            )
            return final_package
        except BaseException as exc:
            failure = exc
            raise
        finally:
            self._close_run_events(state_store, failure=failure)

    def _dispatch_session_end(
        self,
        *,
        state_store: WorkflowStateStore,
        run_dir: Path,
        reason: str,
    ) -> None:
        if self._emit_session_end is None:
            return
        self._emit_session_end(
            state_store=state_store,
            run_dir=run_dir,
            reason=reason,
        )

    def _shutdown_execution(
        self,
        state_store: WorkflowStateStore,
        *,
        terminal: bool,
    ) -> None:
        if self._shutdown_execution_runtime is not None:
            self._shutdown_execution_runtime(state_store, terminal)
            return
        service = state_store.execution_runtime_service
        if service is not None:
            from ownpi.runtime.execution.shutdown import stop_execution_for_cleanup

            stop_execution_for_cleanup(service)

    @staticmethod
    def _close_run_events(
        state_store: WorkflowStateStore, *, failure: BaseException | None = None,
    ) -> None:
        from ownpi.runtime.rollout.lifecycle import close_run_events
        from ownpi.runtime.rollout.run_events import ClosedRunEventStoreError

        try:
            if not close_run_events(state_store):
                logging.getLogger(__name__).warning('Run event persistence remains unconfirmed during finalization')
                if failure is not None:
                    failure.add_note('Run event close also failed: persistence remains unconfirmed')
        except ClosedRunEventStoreError as close_error:
            if failure is None:
                raise
            failure.add_note(f'Run event close also failed: {type(close_error).__name__}: {close_error}')
        except OSError as close_error:
            logging.getLogger(__name__).warning('Run event close unconfirmed during finalization', exc_info=True)
            if failure is not None:
                failure.add_note(f'Run event close also failed: {type(close_error).__name__}: {close_error}')
        except Exception as close_error:
            if failure is None:
                raise
            # Cleanup must not replace the error that interrupted finalization.
            failure.add_note(f'Run event close also failed: {type(close_error).__name__}: {close_error}')

    @staticmethod
    def _record_run_finalized_event(
        *,
        state_store: WorkflowStateStore,
        final_status: str,
        final_decision: GateDecision | str,
        terminal_stage: Stage | None,
        extra_payload: dict[str, Any] | None = None,
    ) -> None:
        normalized_status = str(final_status or '').strip()
        if normalized_status not in TERMINAL_RUN_STATUSES:
            return
        decision_value = (
            final_decision.value
            if isinstance(final_decision, GateDecision)
            else str(final_decision or '').strip()
        )
        workflow_state = state_store.state.workflow_state
        stage = terminal_stage or workflow_state.current_stage
        stage_value = stage.value if isinstance(stage, Stage) else str(stage or '').strip()
        attempt = int(workflow_state.current_stage_attempt or 0)
        already_recorded = False
        for event in reversed(
            state_store.state.context_state.recent_runtime_events[-8:]
        ):
            if event.event_name != 'run_finalized':
                continue
            if event.status != normalized_status:
                continue
            payload = dict(event.payload)
            if str(payload.get('final_decision', '') or '') == decision_value:
                already_recorded = True
                break
        payload = {
            'final_status': normalized_status,
            'final_decision': decision_value,
            'terminal_stage': stage_value,
            'execution_tier': workflow_state.execution_tier.value,
            'stage_runtime_profile': workflow_state.stage_runtime_profile.value,
        }
        if isinstance(extra_payload, dict):
            payload.update(extra_payload)
        if not already_recorded:
            state_store.record_runtime_mutation(
                event_name='run_finalized',
                status=normalized_status,
                mutation_kind='result_recorded',
                stage=stage_value,
                attempt=attempt,
                replay_policy='do_not_replay',
                payload=payload,
                inspect_summary=f'run finalized: status={normalized_status}; decision={decision_value}',
            )
        event_store = state_store.run_event_store
        if event_store is not None:
            from ownpi.runtime.rollout.run_events import ClosedRunEventStoreError

            identity = f'run-finalized:{normalized_status}:{decision_value}'
            if already_recorded and event_store.closed:
                saved = next((event for event in event_store.events if event.event_id == identity), None)
                if saved is not None:
                    if saved.kind != 'run_finalized' or saved.data.to_object() != {
                        'status': normalized_status, 'decision': decision_value,
                    }:
                        raise ValueError(f'Conflicting business event: {identity}')
                    return
            try:
                event_store.emit('run_finalized', identity, {
                    'status': normalized_status, 'decision': decision_value,
                })
            except ClosedRunEventStoreError:
                raise
            except OSError:
                logging.getLogger(__name__).warning('Run finalized persistence unconfirmed; pending retained', exc_info=True)
