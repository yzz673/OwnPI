from __future__ import annotations
from ownpi.runtime.tools.definitions import ToolExposure
from ownpi.runtime.tools.role_policy import tool_role_allowed

from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path

from ownpi.runtime.canonical_documents import (
    EXACT_TEXT_POLICY,
    prepare_canonical_text,
)
from ownpi.runtime.call_continuation.critic_loop import (
    record_analysis_feedback,
    record_critic_feedback,
)
from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.events import now_utc_iso
from ownpi.runtime.rollout.stage_business_events import emit_feedback_submitted
from ownpi.runtime.rollout.call_source import confirm_call_source
from ownpi.runtime.stage_execution.feedback_store import (
    persist_feedback_record,
)
from ownpi.controller.stages import Stage
from ownpi.runtime.stage_execution.loop_policy import is_analysis_route_loop
from ownpi.runtime.tools.definitions import ToolDefinition, ToolExecutionContext
from ownpi.schemas.feedback import FeedbackRecord
from ownpi.schemas.runtime_tooling import ToolCallBatchPolicy
from ownpi.tools.common import metadata_for_context


SUBMIT_FEEDBACK_TOOL_NAME = 'submit_feedback'


@dataclass(frozen=True)
class FeedbackBinding:
    author_role: str
    source_stage: str
    attempt: int
    round: int
    review_target: str
    feedback_path: Path
    stage_loop_index: int = 1

    def __post_init__(self) -> None:
        if self.author_role not in {'reviewer', 'analysis'}:
            raise ValueError(f'invalid feedback author role: {self.author_role}')
        if self.attempt < 1 or self.round < 1:
            raise ValueError('feedback attempt and round must be positive')
        if self.author_role == 'analysis' and self.round != 1:
            raise ValueError('Analysis feedback must use protocol round 1')
        if self.stage_loop_index < 1:
            raise ValueError('stage loop index must be positive')
        if not self.source_stage.strip() or not self.review_target.strip():
            raise ValueError('feedback source stage and review target are required')


def build_submit_feedback_definition(binding: FeedbackBinding) -> ToolDefinition:
    async def handler(context: ToolExecutionContext, arguments: dict[str, object]) -> ToolResult:
        return run_submit_feedback(context=context, arguments=arguments, binding=binding)

    return ToolDefinition(
        exposure=ToolExposure.DIRECT_MODEL_ONLY,
        name=SUBMIT_FEEDBACK_TOOL_NAME,
               kind='runtime_control',
               description='Persist the complete lightweight feedback for this review role.',
               detail=(
                'Submit the exact findings-first feedback text after inspecting the review target. '
                'Runtime owns author identity, stage, attempt, round, target, path, hashes, and time.'
            ),






               read_only=False,
               concurrency_safe=False,
               batch_policy=ToolCallBatchPolicy.TURN_EXCLUSIVE,
               permission_class='task',
               invocation_mode='agent_requested',
               working_set_priority='high',
               toolset_family='control',
               backend_kind='synthetic',
               resume_strategy='stateful',
               schema_cache_variant='feedback:v1',
        detail_builder=lambda: {
            'result_highlights': [
                'author_role',
                'record_id',
                'feedback_path',
                'content_hash',
            ],
        },
        input_schema_builder=lambda: {
            'type': 'object',
            'properties': {
                'content': {
                    'type': 'string',
                    'description': 'The complete feedback text, or `No findings.`.',
                },
            },
            'required': ['content'],
            'additionalProperties': False,
        },
        handler=handler,
        observation_family='feedback',
        result_family='feedback',
        secret_input_fields=('content',),
    )


def run_submit_feedback(
    *,
    context: ToolExecutionContext,
    arguments: dict[str, object],
    binding: FeedbackBinding,
) -> ToolResult:
    actor_role = str(context.actor_kind or '').strip()
    source_stage = str(getattr(context.stage, 'value', context.stage) or '').strip()
    expected_actor = 'core' if binding.author_role == 'analysis' else binding.author_role
    if not tool_role_allowed(SUBMIT_FEEDBACK_TOOL_NAME, actor_role) or actor_role != expected_actor:
        return _failure(
            context,
            f'{SUBMIT_FEEDBACK_TOOL_NAME} is not available to {actor_role or "unknown"}',
        )
    if source_stage != binding.source_stage or context.attempt != binding.attempt:
        return _failure(context, 'feedback binding belongs to a different stage attempt')
    unknown = sorted(set(arguments) - {'content'})
    if unknown:
        return _failure(context, f'unexpected fields: {", ".join(unknown)}')
    content = arguments.get('content')
    tool_call_id = context.tool_call_id
    if not isinstance(content, str) or not content.strip():
        return _failure(context, 'content must be a non-empty string')
    if not isinstance(tool_call_id, str) or not tool_call_id.strip():
        return _failure(context, 'Runtime-owned tool call identity is missing')
    run_dir = context.run_dir
    if not isinstance(run_dir, Path):
        return _failure(context, 'Runtime run directory is unavailable')
    state_store = context.state_store
    if state_store is None:
        return _failure(context, 'Runtime state store is unavailable')
    if binding.author_role == 'analysis':
        workflow = state_store.state.workflow_state
        loop_index = int(workflow.scratchpad.get('stage_loop_index', 1))
        if (binding.stage_loop_index != loop_index or not is_analysis_route_loop(
            Stage(source_stage), workflow.stage_runtime_profile, loop_index,
        )):
            return _failure(context, 'Analysis feedback requires the current final substage')
    canonical_path = binding.feedback_path.expanduser().resolve()
    if not canonical_path.is_relative_to(run_dir.expanduser().resolve()):
        return _failure(context, 'feedback path is outside the Runtime run directory')
    prepared = prepare_canonical_text(content, policy=EXACT_TEXT_POLICY)
    content_hash = prepared.content_sha256
    record_id = _record_id(binding)
    record = FeedbackRecord(
        record_id=record_id,
        author_role=binding.author_role,
        source_stage=binding.source_stage,
        attempt=binding.attempt,
        round=binding.round,
        review_target=binding.review_target,
        provider_operation_id=f'native_tool:{tool_call_id.strip()}',
        feedback_path=str(canonical_path),
        content_hash=content_hash,
        content_chars=prepared.content_chars,
        created_at=now_utc_iso(),
        stage_loop_index=binding.stage_loop_index,
    )
    existing_ref = state_store.state.feedback_state.record_ref(record_id)
    if existing_ref is not None:
        # A retry keeps the original in-process binding, including its submission time.
        record = replace(record, created_at=existing_ref.record.created_at)
    try:
        source = confirm_call_source(context, SUBMIT_FEEDBACK_TOOL_NAME)
        record_ref, idempotent = persist_feedback_record(
            path=canonical_path,
            content=content,
            record=record,
        )
    except (OSError, TypeError, ValueError) as exc:
        return _failure(context, f'feedback persistence failed: {exc}')
    feedback_state = state_store.state.feedback_state
    previous_critic = feedback_state.critic_loop
    previous_analysis = feedback_state.analysis_transition
    previous_refs = list(feedback_state.record_refs)
    try:
        if binding.author_role == 'analysis':
            record_analysis_feedback(
                state_store.state.feedback_state,
                record=record,
                record_ref=record_ref,
            )
        else:
            record_critic_feedback(
                state_store.state.feedback_state,
                record=record,
                record_ref=record_ref,
            )
        emit_feedback_submitted(
            state_store.run_event_store, record=record, source=source,
            run_dir=state_store.run_event_store.path.parent.parent,
        )
    except (AttributeError, OSError, ValueError) as exc:
        feedback_state.critic_loop = previous_critic
        feedback_state.analysis_transition = previous_analysis
        feedback_state.record_refs[:] = previous_refs
        return _failure(context, str(exc))
    return ToolResult(
        ok=True,
        tool_name=SUBMIT_FEEDBACK_TOOL_NAME,
        data={
            'author_role': record.author_role,
            'record_id': record.record_id,
            'feedback_path': record.feedback_path,
            'content_hash': record.content_hash,
            'review_target': record.review_target,
            'idempotent': idempotent,
        },
        stdout='Feedback persisted',
        metadata=metadata_for_context(
            context,
            author_role=record.author_role,
            feedback_round=record.round,
            feedback_record_id=record.record_id,
            feedback_path=record.feedback_path,
            tool_call_id=tool_call_id,
        ),
    )


def _record_id(binding: FeedbackBinding) -> str:
    target_hash = sha256(binding.review_target.encode('utf-8')).hexdigest()[:12]
    return (
        f'feedback:{binding.source_stage}:a{binding.attempt}:{binding.author_role}:'
        f'r{binding.round}:{target_hash}'
    )


def _failure(context: ToolExecutionContext, error: str) -> ToolResult:
    return ToolResult(
        ok=False,
        tool_name=SUBMIT_FEEDBACK_TOOL_NAME,
        error=error,
        metadata=metadata_for_context(context),
    )


__all__ = [
    'SUBMIT_FEEDBACK_TOOL_NAME',
    'FeedbackBinding',
    'build_submit_feedback_definition',
    'run_submit_feedback',
]
