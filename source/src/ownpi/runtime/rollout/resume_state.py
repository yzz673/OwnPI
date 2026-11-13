"""Install business state from current facts; never load a resume snapshot."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ownpi.controller.stages import Stage
from ownpi.controller.state_store import WorkflowStateStore
from ownpi.runtime.rollout.business_scope import business_stage
from ownpi.runtime.task_context import RuntimeTaskContext
from ownpi.schemas.workflow_plan import ExecutionTier, StageRuntimeProfile

if TYPE_CHECKING:
    from ownpi.app.paths import AppPaths
    from ownpi.runtime.rollout.resume import RunResumeFacts
    from ownpi.runtime.rollout.resume_entry import StageResumeEntry
    from ownpi.schemas.runtime import UserConfig


class WorkflowResumePaused(ValueError):
    """A business action lacks evidence, without rejecting its conversation."""


def _initialize(facts: RunResumeFacts, *, project_root: Path,
                user_config: UserConfig | None, app_paths: AppPaths | None) -> WorkflowStateStore:
    identity = facts.stage_projection().run_identity
    if identity is None:
        raise ValueError('run initialization fact is absent')
    metadata = facts.conversation.metadata if facts.conversation is not None else {}
    if metadata.get('session_id') and metadata['session_id'] != identity['session_id']:
        raise ValueError('conversation and run session identities differ')
    if not facts.run_id:
        raise ValueError('conversation lacks a run identity')
    tier = ExecutionTier(identity['execution_tier'])
    plan = facts.stage_projection().workflow_plan
    if tier is ExecutionTier.FULL and plan is None:
        raise ValueError('complete workflow plan fact is absent')
    from ownpi.runtime.rollout.direct_resume import is_direct_session

    direct_session = is_direct_session(facts.events)
    if not direct_session:
        facts.read_canonical_text('raw_user_message.txt')
    task_spec = facts.run_root / 'task_spec.md'
    if tier is ExecutionTier.FULL or task_spec.is_file():
        facts.read_canonical_text(task_spec)
    return WorkflowStateStore.initialize(
        task_context=None if direct_session else RuntimeTaskContext.from_paths(
            raw_user_message_path=facts.run_root / 'raw_user_message.txt',
            task_spec_path=task_spec if task_spec.is_file() else None),
        run_workflow_plan=plan, session_id=identity['session_id'], task_id=identity['task_id'],
        run_id=facts.run_id, execution_tier=tier, project_root=project_root,
        stage_runtime_profile=StageRuntimeProfile(identity['stage_runtime_profile']),
        user_config=user_config, app_paths=app_paths, initialize_external_knowledge=False,
    )


def restore_child_parent_state(
    facts: RunResumeFacts, *, project_root: Path,
    user_config: UserConfig | None = None, app_paths: AppPaths | None = None,
) -> WorkflowStateStore:
    """Rebuild a child's business services without starting its parent workflow."""
    try:
        metadata = facts.conversation.metadata
        if metadata.get('actor_kind') != 'subagent':
            raise ValueError('child resume requires a subagent conversation')
        admitted = facts.admitted_child(str(metadata.get('actor_id') or ''))
        if admitted is None:
            raise ValueError('child task admission fact is absent')
        store = _initialize(facts, project_root=project_root, user_config=user_config, app_paths=app_paths)
        workflow = store.state.workflow_state
        workflow.current_stage = Stage(business_stage(metadata['stage']))
        workflow.current_stage_attempt = metadata['attempt']
        workflow.scratchpad.update(run_dir=str(facts.run_root), rollout_resume_child=admitted,
                                   rollout_resume_facts=facts)
        _install_resources(facts, store)
        return store
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise WorkflowResumePaused(f'child runtime facts cannot be installed: {error}') from error


def _install_resources(facts: RunResumeFacts, store: WorkflowStateStore) -> None:
    store.state.execution_registry = facts.execution_projection().registry
    from ownpi.runtime.rollout.resource_projection import project_resources

    resources = project_resources(facts.events)
    for binding in resources.installable_bindings():
        store.state.process_owner_bindings.upsert(binding)
    store.state.workflow_state.scratchpad['rollout_resources'] = resources


def restore_workflow_state(
    entry: StageResumeEntry, *, project_root: Path,
    user_config: UserConfig | None = None, app_paths: AppPaths | None = None,
) -> WorkflowStateStore:
    """Install an entry already resolved by the caller, without selecting a boundary."""
    facts = entry.facts
    try:
        store = _initialize(facts, project_root=project_root, user_config=user_config, app_paths=app_paths)
        workflow = store.state.workflow_state
        workflow.current_stage = entry.stage
        workflow.current_stage_attempt = entry.attempt
        workflow.stage_attempt_counters = dict(entry.counters)
        workflow.current_query_chain_id = entry.query_chain_id or None
        workflow.current_query_chain_depth = entry.query_chain_depth
        workflow.scratchpad.update(
            run_dir=str(facts.run_root), stage_loop_index=entry.loop_index,
            continuation_index=entry.continuation_index, rollout_resume_facts=facts,
        )
        workflow.last_stage_run_id = entry.stage_run_id or None
        store.state.feedback_state = entry.feedback
        workflow.revisit_state = entry.revisit
        if entry.revisit.pending_target_stage == entry.stage.value:
            context = entry.revisit.current_context_for(stage=entry.stage, attempt=entry.attempt)
            if context is not None:
                workflow.scratchpad['active_revisit_context'] = context
        workflow.pending_human_clarification = entry.interaction
        _install_resources(facts, store)
        if facts.conversation is not None:
            store.bind_resume_conversation(
                stage=entry.stage.value, attempt=entry.attempt,
                actor_kind=facts.conversation.metadata['actor_kind'],
                conversation_id=facts.conversation.conversation_id,
            )
        if entry.prep is not None:
            from ownpi.runtime.stage_prep.handoff import build_stage_prep_handoff
            from ownpi.runtime.stage_prep.lifecycle import activate_stage_prep_result
            from ownpi.runtime.stage_prep.models import StagePrepResult

            activate_stage_prep_result(
                result=StagePrepResult(
                    status='completed', record=entry.prep,
                    handoff=build_stage_prep_handoff(record=entry.prep, loop_index=entry.loop_index),
                    reused=True,
                ), state_store=store,
            )
        return store
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise WorkflowResumePaused(f'workflow facts cannot be installed: {error}') from error
