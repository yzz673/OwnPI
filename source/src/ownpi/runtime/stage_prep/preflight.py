from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from ownpi.controller.state_store import WorkflowStateStore
from ownpi.runtime.stage_prep.models import StagePrepRequest
from ownpi.runtime.stage_prep.paths import StagePrepLayout
from ownpi.static_semantic_contracts import resolve_stage_contract
from ownpi.utils.io import ensure_dir


@dataclass(frozen=True)
class StagePrepInputs:
    raw_message_sha256: str
    task_spec_sha256: str


def validate_stage_prep_preflight(
    *,
    request: StagePrepRequest,
    state_store: WorkflowStateStore,
    layout: StagePrepLayout,
) -> StagePrepInputs:
    state = state_store.state
    metadata = state.run_metadata
    workflow = state.workflow_state
    run_dir = request.run_dir.expanduser().resolve()
    if layout.run_dir != run_dir:
        raise ValueError('stage prep layout does not belong to request run_dir')
    if not metadata.session_id or not metadata.task_id or not metadata.run_id:
        raise ValueError('stage prep requires complete session/task/run identity')
    plan = workflow.run_workflow_plan
    if plan is None or not plan.contains(request.stage):
        raise ValueError(f'stage is not selected for this run: {request.stage.value}')
    if workflow.stage_runtime_profile != request.level:
        raise ValueError('stage prep level does not match RuntimeState')
    if workflow.current_stage not in {None, request.stage}:
        raise ValueError('stage prep stage does not match RuntimeState current stage')
    resolve_stage_contract(request.stage, request.level)

    task_context = state.task_context
    raw_path = Path(task_context.raw_user_message_ref.path).resolve()
    task_ref = task_context.task_spec_ref
    if task_ref is None:
        raise ValueError('ordinary stage prep requires a canonical task specification')
    task_path = Path(task_ref.path).resolve()
    if not raw_path.is_relative_to(run_dir) or not task_path.is_relative_to(run_dir):
        raise ValueError('canonical task context paths must remain inside run_dir')
    raw_text = task_context.read_raw_user_message().strip()
    task_text = task_context.read_task_spec().strip()
    if layout.stage_dir.exists() and not layout.stage_dir.is_dir():
        raise ValueError(f'stage prep path is not a directory: {layout.stage_dir}')
    ensure_dir(layout.stage_dir)
    if request.has_approved_stage_plan:
        plan_path = Path(request.approved_stage_plan_path).expanduser().resolve()
        if not plan_path.is_relative_to(run_dir):
            raise ValueError('approved stage blueprint must remain inside run_dir')
        if not plan_path.is_file():
            raise FileNotFoundError(f'approved stage blueprint is missing: {plan_path}')
        if sha256(plan_path.read_bytes()).hexdigest() != request.approved_stage_plan_sha256:
            raise ValueError('approved stage blueprint hash mismatch')
    return StagePrepInputs(
        raw_message_sha256=_sha256_text(raw_text),
        task_spec_sha256=_sha256_text(task_text),
    )


def _sha256_text(value: str) -> str:
    return sha256(value.encode('utf-8')).hexdigest()


__all__ = ['StagePrepInputs', 'validate_stage_prep_preflight']
