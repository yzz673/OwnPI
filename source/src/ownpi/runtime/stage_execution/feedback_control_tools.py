from __future__ import annotations

from typing import Any

from ownpi.controller.stages import (
    Stage,
    stage_from_value,
)
from ownpi.runtime.call_continuation.critic_loop import (
    record_core_feedback_intent,
    record_next_stage_receipt,
)
from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.events import now_utc_iso
from ownpi.runtime.rollout.stage_business_events import emit_critic_loop_transition
from ownpi.runtime.stage_execution.loop_policy import is_analysis_route_loop
from ownpi.runtime.tools.definitions import ToolDefinition, ToolExecutionContext, ToolExposure
from ownpi.runtime.tools.role_policy import tool_role_allowed
from ownpi.schemas.feedback import CoreFeedbackIntentReceipt, NextStageReceipt
from ownpi.schemas.runtime_tooling import ToolCallBatchPolicy
from ownpi.schemas.workflow_plan import RunWorkflowPlan, StageRuntimeProfile
from ownpi.tools.common import metadata_for_context


REQUEST_REVIEW_TOOL_NAME = 'request_review'
COMPLETE_FEEDBACK_TOOL_NAME = 'complete_feedback'
NEXT_STAGE_TOOL_NAME = 'next_stage'


def build_request_review_definition(*, direct: bool = False) -> ToolDefinition:
    return _core_intent_definition(
        name=REQUEST_REVIEW_TOOL_NAME,
        description='Request Reviewer inspection after completing and checking the current work.',
        detail=(
            'Finish and check the current user request, then call request_review(). '
            'On every submission, including revisions, put only the complete current answer '
            'to the user in answer.text; it is displayed verbatim after approval, '
            'including any necessary delivery summary and work-product locations. Put revision '
            'notes, change lists, process commentary, and Reviewer-directed instructions in the '
            'optional review_context; never put them in answer.text. Reviewer inspects that '
            'answer and referenced work products. '
            'After feedback, address each finding before requesting another review.'
        ) if direct else (
            'Before the first request, check every current-stage task and contract requirement '
            'against actual artifacts and evidence. Before later requests, check every actionable '
            'Reviewer finding individually and verify that all corrections are complete. Call only '
            'after all required work is finished; partial work or a plan to fix it is insufficient. '
            'If answer is provided, it must contain only the complete user-facing answer or delivery '
            'summary; put Reviewer-directed notes in review_context. '
            'Runtime records the submission and starts Reviewer inspection of the current workspace.'
        ),
    )


def build_complete_feedback_definition() -> ToolDefinition:
    return _core_intent_definition(
        name=COMPLETE_FEEDBACK_TOOL_NAME,
        description='Close the active feedback loop when no critic issue remains.',
        detail=(
            'Call this only when the critic feedback has no remaining issue and no further critic '
            'verification is needed. The canonical Reviewer feedback must '
            'be "No findings."; otherwise address all findings and call request_review().'
        ),
    )


def _core_intent_definition(*, name: str, description: str, detail: str) -> ToolDefinition:
    async def handler(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
        return run_core_feedback_intent(context=context, arguments=arguments, intent=name)

    return ToolDefinition(
        exposure=ToolExposure.DIRECT_MODEL_ONLY,
        **_control_summary(
            name=name,
            description=description,
            detail=detail,
            schema_cache_variant=f'{name}:v4' if name == REQUEST_REVIEW_TOOL_NAME else f'{name}:v2',
        ),
        input_schema_builder=_request_review_schema if name == REQUEST_REVIEW_TOOL_NAME else _empty_public_schema,
        handler=handler,
        observation_family='core_feedback_intent',
        result_family='core_feedback_intent',
    )


def run_core_feedback_intent(
    *,
    context: ToolExecutionContext,
    arguments: dict[str, Any],
    intent: str,
) -> ToolResult:
    if intent not in {REQUEST_REVIEW_TOOL_NAME, COMPLETE_FEEDBACK_TOOL_NAME}:
        return _failure(context, intent, 'unsupported Core feedback intent')
    if not tool_role_allowed(intent, str(context.actor_kind or '').strip()):
        return _failure(context, intent, f'{intent} is only available to Core')
    unknown = sorted(set(arguments) - ({'review_context', 'answer'} if intent == REQUEST_REVIEW_TOOL_NAME else set()))
    if unknown:
        return _failure(context, intent, f'unexpected fields: {", ".join(unknown)}')
    tool_call_id = context.tool_call_id
    if not isinstance(tool_call_id, str) or not tool_call_id.strip():
        return _failure(context, intent, 'Runtime-owned tool call identity is missing')
    state_store = context.state_store
    feedback_state = getattr(getattr(state_store, 'state', None), 'feedback_state', None)
    if feedback_state is None:
        return _failure(context, intent, 'feedback state is unavailable')
    loop = feedback_state.critic_loop
    stage = str(getattr(context.stage, 'value', context.stage) or '').strip()
    initial_request = loop.phase == 'idle' and intent == REQUEST_REVIEW_TOOL_NAME
    workflow = state_store.state.workflow_state
    if intent == REQUEST_REVIEW_TOOL_NAME:
        from ownpi.runtime.rollout.answer_publication import answer_protocol_enabled, answer_text

        required = (stage == Stage.DIRECT_QA.value
                    and workflow.stage_runtime_profile == StageRuntimeProfile.TASK_LEVEL
                    and answer_protocol_enabled(
                        state_store.run_event_store.events, attempt=context.attempt,
                        loop_index=int(workflow.scratchpad.get('stage_loop_index', 1)),
                    ))
        try:
            answer_text(arguments, required=required)
        except ValueError as exc:
            return _failure(context, intent, str(exc))
    if stage == Stage.ANALYSIS_DECISION.value and is_analysis_route_loop(
        Stage.ANALYSIS_DECISION, workflow.stage_runtime_profile,
        int(workflow.scratchpad.get('stage_loop_index', 1)),
    ):
        return _failure(context, intent, 'Analysis uses next_stage instead of review control')
    existing_receipt = loop.core_intent_receipt
    if existing_receipt is not None:
        if (
            existing_receipt.intent == intent
            and existing_receipt.source_stage == stage
            and existing_receipt.attempt == context.attempt
            and existing_receipt.critic_role == ('reviewer' if initial_request else loop.critic_role)
            and existing_receipt.round == loop.round
            and existing_receipt.feedback_record_id == (loop.feedback_record_id or '')
            and existing_receipt.tool_call_id == tool_call_id.strip()
        ):
            receipt = existing_receipt
        else:
            return _failure(
                context,
                intent,
                'a different Core feedback intent is already committed',
            )
    else:
        receipt = None
    if (
        receipt is None
        and not initial_request
        and (
            loop.phase != 'core_post_feedback'
            or loop.source_stage != stage
            or loop.attempt != context.attempt
            or not loop.feedback_record_id
        )
    ):
        return _failure(context, intent, 'no active feedback is bound to this Core attempt')
    if receipt is None:
        receipt = CoreFeedbackIntentReceipt(
            intent=intent,
            source_stage=stage,
            attempt=context.attempt,
            critic_role='reviewer',
            round=loop.round,
            feedback_record_id=loop.feedback_record_id or '',
            tool_call_id=tool_call_id.strip(),
            created_at=now_utc_iso(),
        )
    try:
        from ownpi.runtime.rollout.call_source import confirm_call_source

        source = confirm_call_source(context, intent)
        resolved = record_core_feedback_intent(feedback_state, receipt=receipt)
        emit_critic_loop_transition(
            state_store.run_event_store, feedback_state=feedback_state,
            stage=stage, attempt=context.attempt,
            stage_loop_index=int(state_store.state.workflow_state.scratchpad.get('stage_loop_index', 1)),
            action='core_intent_accepted', source=source,
        )
    except (OSError, ValueError) as exc:
        feedback_state.critic_loop = loop
        return _failure(context, intent, str(exc))
    return ToolResult(
        ok=True,
        tool_name=intent,
        data={
            'intent': intent,
            'feedback_record_id': receipt.feedback_record_id,
            'critic_role': receipt.critic_role,
            'round': receipt.round,
        },
        stdout='Core feedback intent recorded',
        metadata=metadata_for_context(
            context,
            feedback_record_id=receipt.feedback_record_id,
            critic_role=receipt.critic_role,
            feedback_round=receipt.round,
            critic_phase=resolved.phase,
            tool_call_id=tool_call_id,
        ),
    )


def build_next_stage_definition(*, workflow_plan: RunWorkflowPlan) -> ToolDefinition:
    target_stages = _analysis_target_stages(workflow_plan)
    target_values = tuple(stage.value for stage in target_stages)

    async def handler(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
        return run_next_stage(context, arguments)

    return ToolDefinition(
        exposure=ToolExposure.DIRECT_MODEL_ONLY,
        **_control_summary(
            name=NEXT_STAGE_TOOL_NAME,
            description='Choose the next stage after completing Analysis.',
            detail=(
                'Analysis-only terminal control tool. Complete the Analysis Memo and submit '
                'feedback before calling it. Runtime validates target_stage against the active '
                'workflow plan; choosing its planned successor advances, while choosing a prior '
                'selected stage revisits that stage.'
            ),
            schema_cache_variant=f'next_stage:v2:{",".join(target_values)}',
        ),
        detail_builder=lambda: {'result_highlights': ['target_stage']},
        input_schema_builder=lambda: {
            'type': 'object',
            'properties': {
                'target_stage': {
                    'type': 'string',
                    'enum': list(target_values),
                },
            },
            'required': ['target_stage'],
            'additionalProperties': False,
        },
        handler=handler,
        observation_family='next_stage',
        result_family='next_stage',
    )


def run_next_stage(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
    if (
        not tool_role_allowed(NEXT_STAGE_TOOL_NAME, str(context.actor_kind or '').strip())
        or str(getattr(context.stage, 'value', context.stage) or '').strip()
        != Stage.ANALYSIS_DECISION.value
    ):
        return _failure(
            context,
            NEXT_STAGE_TOOL_NAME,
            'next_stage is only available to Analysis Core',
        )
    unknown = sorted(set(arguments) - {'target_stage'})
    if unknown:
        return _failure(
            context,
            NEXT_STAGE_TOOL_NAME,
            f'unexpected fields: {", ".join(unknown)}',
        )
    target_value = str(arguments.get('target_stage', '') or '').strip()
    tool_call_id = context.tool_call_id
    if not target_value:
        return _failure(context, NEXT_STAGE_TOOL_NAME, 'target_stage is required')
    if not isinstance(tool_call_id, str) or not tool_call_id.strip():
        return _failure(
            context,
            NEXT_STAGE_TOOL_NAME,
            'Runtime-owned tool call identity is missing',
        )
    state_store = context.state_store
    feedback_state = getattr(getattr(state_store, 'state', None), 'feedback_state', None)
    if feedback_state is None:
        return _failure(context, NEXT_STAGE_TOOL_NAME, 'feedback state is unavailable')
    workflow_state = getattr(getattr(state_store, 'state', None), 'workflow_state', None)
    if workflow_state is None:
        return _failure(context, NEXT_STAGE_TOOL_NAME, 'workflow state is unavailable')
    if not is_analysis_route_loop(
        Stage.ANALYSIS_DECISION, workflow_state.stage_runtime_profile,
        int(workflow_state.scratchpad.get('stage_loop_index', 1)),
    ):
        return _failure(context, NEXT_STAGE_TOOL_NAME, 'next_stage requires the final Analysis substage')
    plan = getattr(workflow_state, 'run_workflow_plan', None)
    if plan is None:
        return _failure(
            context,
            NEXT_STAGE_TOOL_NAME,
            'next_stage requires an active workflow plan',
        )
    try:
        target_stage = stage_from_value(target_value)
    except ValueError:
        return _failure(context, NEXT_STAGE_TOOL_NAME, 'target_stage is invalid')
    legal_targets = _analysis_target_stages(plan)
    if target_stage not in legal_targets:
        return _failure(
            context,
            NEXT_STAGE_TOOL_NAME,
            'target_stage must be a prior selected stage or the planned successor of Analysis',
        )
    target_value = target_stage.value
    transition = feedback_state.analysis_transition
    if (
        transition.source_stage != Stage.ANALYSIS_DECISION.value
        or transition.attempt != context.attempt
        or not transition.feedback_record_id
    ):
        return _failure(
            context,
            NEXT_STAGE_TOOL_NAME,
            'Submit feedback with submit_feedback() before calling next_stage().',
        )
    existing_receipt = transition.next_stage_receipt
    if existing_receipt is not None:
        if (
            existing_receipt.source_stage == Stage.ANALYSIS_DECISION.value
            and existing_receipt.attempt == context.attempt
            and existing_receipt.feedback_record_id == transition.feedback_record_id
            and existing_receipt.target_stage == target_value
            and existing_receipt.tool_call_id == tool_call_id.strip()
        ):
            receipt = existing_receipt
        else:
            return _failure(
                context,
                NEXT_STAGE_TOOL_NAME,
                'a different next-stage receipt is already committed',
            )
    else:
        receipt = NextStageReceipt(
            source_stage=Stage.ANALYSIS_DECISION.value,
            attempt=context.attempt,
            feedback_record_id=transition.feedback_record_id,
            target_stage=target_value,
            tool_call_id=tool_call_id.strip(),
            created_at=now_utc_iso(),
        )
    try:
        resolved = record_next_stage_receipt(feedback_state, receipt=receipt)
    except ValueError as exc:
        return _failure(context, NEXT_STAGE_TOOL_NAME, str(exc))
    return ToolResult(
        ok=True,
        tool_name=NEXT_STAGE_TOOL_NAME,
        data={
            'target_stage': receipt.target_stage,
            'feedback_record_id': receipt.feedback_record_id,
        },
        stdout='Next-stage route recorded',
        metadata=metadata_for_context(
            context,
            target_stage=receipt.target_stage,
            feedback_record_id=receipt.feedback_record_id,
            analysis_route_ready=resolved.next_stage_receipt is not None,
            tool_call_id=tool_call_id,
        ),
    )


def _analysis_target_stages(workflow_plan: RunWorkflowPlan) -> tuple[Stage, ...]:
    if not workflow_plan.contains(Stage.ANALYSIS_DECISION):
        raise ValueError('active workflow plan does not contain Analysis')
    successor = workflow_plan.next_stage(Stage.ANALYSIS_DECISION)
    if successor is None:
        raise ValueError('Analysis must have a planned successor')
    return (*workflow_plan.prior_stages(Stage.ANALYSIS_DECISION), successor)


def _control_summary(
    *,
    name: str,
    description: str,
    detail: str,
    schema_cache_variant: str,
) -> dict[str, Any]:
    return {
        'name': name,
        'kind': 'runtime_control',
        'description': description,
        'detail': detail,
        'read_only': False,
        'concurrency_safe': False,
        'batch_policy': ToolCallBatchPolicy.TURN_EXCLUSIVE,
        'permission_class': 'task',
        'invocation_mode': 'agent_requested',
        'working_set_priority': 'high',
        'toolset_family': 'control',
        'backend_kind': 'synthetic',
        'resume_strategy': 'stateful',
        'schema_cache_variant': schema_cache_variant,
    }


def _request_review_schema() -> dict[str, Any]:
    return {
        'type': 'object',
        'properties': {
            'answer': {
                'type': 'object',
                'description': (
                    'Only the complete answer to show the user, including any necessary delivery '
                    'summary and work-product locations; never put revision notes or Reviewer-directed '
                    'content here. Required for task-level requests.'
                ),
                'properties': {'text': {'type': 'string'}},
                'required': ['text'],
                'additionalProperties': False,
            },
            'review_context': {
                'type': 'string',
                'description': (
                    'Optional message to Reviewer before inspection: describe completed work, '
                    'revision notes, artifact locations, or anything Reviewer should know. Put only '
                    'the complete user-facing answer in answer.text.'
                ),
            },
        },
        'required': [],
        'additionalProperties': False,
    }


def _empty_public_schema() -> dict[str, Any]:
    return {'type': 'object', 'properties': {}, 'additionalProperties': False}


def _failure(context: ToolExecutionContext, tool_name: str, error: str) -> ToolResult:
    return ToolResult(
        ok=False,
        tool_name=tool_name,
        error=error,
        metadata=metadata_for_context(context),
    )


__all__ = [
    'COMPLETE_FEEDBACK_TOOL_NAME',
    'NEXT_STAGE_TOOL_NAME',
    'REQUEST_REVIEW_TOOL_NAME',
    'build_complete_feedback_definition',
    'build_next_stage_definition',
    'build_request_review_definition',
    'run_core_feedback_intent',
    'run_next_stage',
]
