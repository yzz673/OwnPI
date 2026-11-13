from __future__ import annotations

from dataclasses import replace

from ownpi.controller.state_store import WorkflowStateStore
from ownpi.controller.runtime_control_signal import RuntimeControlRequested
from ownpi.runtime.events import now_utc_iso
from ownpi.runtime.stage_prep.identity import stage_prep_id
from ownpi.runtime.stage_prep.handoff import build_stage_prep_handoff
from ownpi.runtime.stage_prep.lifecycle import (
    activate_stage_prep_result,
    append_stage_prep_history_once,
    build_stage_prep_record,
    record_stage_prep_event,
)
from ownpi.runtime.stage_prep.models import (
    StagePrepRecord,
    StagePrepRequest,
    StagePrepResult,
)
from ownpi.runtime.stage_prep.paths import resolve_stage_prep_layout
from ownpi.runtime.stage_prep.preflight import (
    StagePrepInputs,
    validate_stage_prep_preflight,
)


class StagePrepService:
    def prepare(
        self,
        *,
        request: StagePrepRequest,
        state_store: WorkflowStateStore,
    ) -> StagePrepResult:
        attempt = self._establish_entry_identity(request=request, state_store=state_store)
        layout = resolve_stage_prep_layout(
            run_dir=request.run_dir,
            stage=request.stage,
            attempt=attempt,
        )
        inputs: StagePrepInputs | None = None
        created_at = now_utc_iso()
        try:
            inputs = validate_stage_prep_preflight(
                request=request,
                state_store=state_store,
                layout=layout,
            )
            prep_id = stage_prep_id(
                run_id=state_store.state.run_metadata.run_id,
                stage=request.stage.value,
                level=request.level.value,
                attempt=attempt,
                raw_hash=inputs.raw_message_sha256,
                task_hash=inputs.task_spec_sha256,
                approved_stage_plan_hash=request.approved_stage_plan_sha256,
            )
            started = build_stage_prep_record(
                status='started',
                prep_id=prep_id,
                request=request,
                state_store=state_store,
                attempt=attempt,
                created_at=created_at,
            )
            completed = replace(
                started,
                status='completed',
                updated_at=now_utc_iso(),
            )
            handoff = build_stage_prep_handoff(
                record=completed, loop_index=request.stage_loop_index
            )
            result = StagePrepResult(status='completed', record=completed, handoff=handoff)
            state_store.state.workflow_state.scratchpad['stage_loop_index'] = request.stage_loop_index
            activate_stage_prep_result(
                result=result,
                state_store=state_store,
            )
            record_stage_prep_event(
                state_store=state_store,
                event_type='stage_prep_completed',
                record=completed,
            )
            state_store.poll_runtime_control('stage_prep_completed')
            return result
        except RuntimeControlRequested:
            raise
        except Exception as error:
            failed = build_stage_prep_record(
                status='failed',
                prep_id=(
                    stage_prep_id(
                        run_id=state_store.state.run_metadata.run_id,
                        stage=request.stage.value,
                        level=request.level.value,
                        attempt=attempt,
                        raw_hash=inputs.raw_message_sha256 if inputs is not None else '',
                        task_hash=inputs.task_spec_sha256 if inputs is not None else '',
                        approved_stage_plan_hash=request.approved_stage_plan_sha256,
                    )
                ),
                request=request,
                state_store=state_store,
                attempt=attempt,
                created_at=created_at,
                failure_class=f'stage_prep_{type(error).__name__.lower()}',
                failure_message=str(error),
            )
            return self._fail(
                record=failed,
                state_store=state_store,
            )

    @staticmethod
    def _establish_entry_identity(
        *,
        request: StagePrepRequest,
        state_store: WorkflowStateStore,
    ) -> int:
        attempt = (request.attempt if request.attempt is not None
                   else state_store.enter_stage_attempt(request.stage))
        state_store.state.workflow_state.scratchpad['continuation_index'] = int(
            request.continuation_index
        )
        return attempt

    def _fail(
        self,
        *,
        record: StagePrepRecord,
        state_store: WorkflowStateStore,
    ) -> StagePrepResult:
        state = state_store.state.stage_prep_state
        state.active_handoff = None
        append_stage_prep_history_once(state.history, record)
        record_stage_prep_event(
            state_store=state_store,
            event_type='stage_prep_failed',
            record=record,
        )
        return StagePrepResult(status='failed', record=record)


__all__ = ['StagePrepService']
