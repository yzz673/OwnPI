from __future__ import annotations

import asyncio
import hashlib
import json
from concurrent.futures import CancelledError
import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Literal

from ownpi.controller.state_store import WorkflowStateStore
from ownpi.controller.stages import Stage
from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.permissions.action_compiler import compile_canonical_action
from ownpi.runtime.permissions.auto_review import (
    AutoReviewContext,
    AutoReviewService,
)
from ownpi.runtime.permissions.models import (
    ApprovalReceipt,
    ApprovalsReviewer,
    EffectivePermissions,
    PermissionRequest,
)
from ownpi.runtime.permissions.orchestrator import (
    PermissionEvaluation,
    PermissionOrchestrator,
    additional_permissions_cover,
    approval_fingerprint,
)
from ownpi.runtime.permissions.session_identity import valid_session_identity
from ownpi.runtime.permissions.bash_policy import analyze_bash_command
from ownpi.runtime.environment import (
    EnvironmentExecutionReceipt,
    EnvironmentResolver,
)
from ownpi.runtime.events import now_utc_iso
from ownpi.runtime.execution.contracts import (
    ApprovalContract,
    ExecutionAttemptContract,
    build_execution_attempt_contract,
    execution_contract_hash,
)
from ownpi.runtime.execution.policy import (
    ExecPolicyDecision,
    compile_exec_policy,
    exec_policy_hash,
)
from ownpi.runtime.human.clarification import resolve_clarified_path_for_scope
from ownpi.runtime.limits.tool_output import (
    DEFAULT_TOOL_RESULT_TRUNCATION_POLICY,
    TruncationPolicy,
)
from ownpi.runtime.memory.receipts import (
    bind_memory_consumption_receipt,
    canonicalize_memory_consumption_effects,
    memory_consumption_receipt_from_metadata,
    with_memory_consumption_receipt,
)
from ownpi.runtime.memory.tool_results import memory_receipt_only_tool_result
from ownpi.skills.snapshot import SkillCatalogSnapshot
from ownpi.runtime.hooks.adapters import run_post_tool_hooks, run_pre_tool_hooks
from ownpi.runtime.hooks.builder import build_hook_engine
from ownpi.runtime.hooks.context_delivery import (
    merge_hook_delivery_batches,
    persist_hook_delivery_batch,
)
from ownpi.runtime.hooks.engine import HookEngine
from ownpi.runtime.hooks.registry import HookRegistry
from ownpi.runtime.hooks.types import (
    HookDeliveryBatch,
    PermissionOutcome,
    PermissionRequestHookRequest,
    outcome_metadata,
)
from ownpi.runtime.tools.planning import allow_human_question_from_runtime_state
from ownpi.runtime.tools.remote_state_projection import (
    project_remote_workflow_result,
)
from ownpi.runtime.tools.definitions import ToolDefinition, ToolExecutionContext, ToolExposure
from ownpi.runtime.tools.errors import ToolPreflightValidationError
from ownpi.runtime.tools.router import ToolRouter
from ownpi.runtime.tools.role_policy import tool_role_allowed
from ownpi.runtime.tools.concurrency_gate import ToolInvocationConcurrencyGate
from ownpi.runtime.tools.execution_engine import ToolExecutionEngine
from ownpi.runtime.tools.invocation_policy import (
    is_side_channel_control_tool_name,
)
from ownpi.runtime.tools.surface_utils import (
    summarize_tool_result,
)
from ownpi.runtime.tools.runtime_text_resource_mutation import (
    capture_runtime_text_resource_mutation,
    reconcile_runtime_text_resource_mutation,
)
from ownpi.runtime.tools.runtime_text_resources import (
    build_runtime_text_resource_records,
    runtime_text_resource_record_for_request,
)
from ownpi.schemas.runtime_human_clarification import (
    HumanClarificationQuestion,
    HumanClarificationSession,
    build_question_key,
    normalize_human_answer_payload,
    selected_preview_for_answer,
    slugify_question_identifier,
)
from ownpi.schemas.runtime_permission import (
    SessionPermissionGrant,
    ToolPermissionSession,
)
from ownpi.schemas.runtime import HookRegistryConfig
from ownpi.schemas.workflow_plan import ExecutionTier
from ownpi.schemas.runtime_tooling import (
    ToolCall,
    ToolInvocationRecord,
    ToolObservation,
    ToolPermissionDecision,
    ToolPermissionEvent,
    ToolProtocolViolationRecord,
)
from ownpi.tools.codex_patch import parse_patch, summarize_operations
from ownpi.runtime.interactions.live_approval import wait_for_approval


ToolExecutionProtocol = Literal["auto", "native_tool", "runtime_policy"]


@dataclass(frozen=True)
class ToolRuntimeContext:
    state_store: WorkflowStateStore
    raw_user_message: str
    task_spec_text: str
    project_root: Path
    run_dir: Path
    stage: Stage
    attempt: int
    tool_router: ToolRouter
    query_chain_id: str = ""
    query_chain_depth: int = 0
    continuation_index: int = 0
    requested_by: str = "runtime_policy"
    effective_permissions: EffectivePermissions | None = None
    actor_kind: str = "core"
    actor_id: str | None = None
    process_owner_actor_id: str | None = None
    tool_call_id: str = ""
    execution_handle: Any | None = None
    conversation_lane: str = ""
    conversation_exchange_id: str = ""
    rollout_conversation_id: str = ""
    rollout_parent_id: str = ""
    rollout_history: Any | None = None
    resume_read_session: Any | None = None
    output_sequence_index: int | None = None
    user_config: Any | None = None
    cancel_checker: Any | None = None
    before_tool_handler: Callable[[ToolCall], None] | None = None
    on_raw_tool_outcome: Callable[[ToolInvocationOutcome], None] | None = None
    on_tool_terminal: Callable[[], None] | None = None
    progress_callback: Any | None = None
    mcp_elicitation_handler: Any | None = None
    hook_engine: HookEngine | None = None
    app_paths: Any | None = None
    execution_runtime_service: Any | None = None
    process_owner_manager: Any | None = None
    subagent_owner: Any | None = None
    shared_resource_run_dir: Path | None = None
    skill_catalog_snapshot: SkillCatalogSnapshot | None = None
    tool_result_truncation_policy: TruncationPolicy = (
        DEFAULT_TOOL_RESULT_TRUNCATION_POLICY
    )

    @property
    def canonical_permissions(self) -> EffectivePermissions:
        return (
            self.effective_permissions
            or self.state_store.state.tool_state.effective_permissions
        )

    @property
    def conversation_stage(self) -> str:
        return self.conversation_lane.strip() or self.stage.value

    @property
    def run_id(self) -> str:
        return str(self.state_store.state.run_metadata.run_id or "")

    @property
    def managed_venv_path(self) -> str:
        return self.state_store.state.managed_venv_path

    @property
    def active_remote_profile(self) -> str:
        return str(self.state_store.state.remote_state.active_remote_profile or '').strip()

    @property
    def managed_non_python_dependency_root(self) -> str:
        return self.state_store.state.managed_non_python_dependency_root


def _allow_human_question(context: ToolRuntimeContext) -> bool:
    return allow_human_question_from_runtime_state(context.state_store.state)


@dataclass(frozen=True)
class ToolInvocationOutcome:
    request: ToolCall
    record: ToolInvocationRecord
    tool_result: ToolResult | None = None
    durable_result: ToolResult | None = None
    pending_interaction_session: HumanClarificationSession | None = None
    hook_delivery: HookDeliveryBatch = field(default_factory=HookDeliveryBatch)
    diagnostic: str = ''


@dataclass(frozen=True)
class DestructiveActionAssessment:
    is_destructive: bool = False
    effective_permission_class: str = "edit"
    reason: str = ""
    kinds: tuple[str, ...] = ()


class ToolRuntime:
    def __init__(
        self,
        *,
        hook_engine: HookEngine | None = None,
        auto_review_service: AutoReviewService | None = None,
    ) -> None:
        self._hook_engine = hook_engine
        self._auto_review_service = auto_review_service
        self._concurrency_gate = ToolInvocationConcurrencyGate()

    def fork_for_agent(self) -> "ToolRuntime":
        """Keep tool services while giving a child its own invocation gate."""
        return ToolRuntime(
            hook_engine=self._hook_engine,
            auto_review_service=self._auto_review_service,
        )

    @staticmethod
    def _normalize_human_question_tool_call_ids(request: ToolCall) -> ToolCall:
        raw_questions = request.input.get("questions")
        if not isinstance(raw_questions, list) or not raw_questions:
            return request
        if not all(isinstance(item, dict) for item in raw_questions):
            return request
        normalized_raw_questions: list[dict[str, Any]] = []
        for question_index, raw_item in enumerate(raw_questions, start=1):
            updated_item = dict(raw_item)
            if isinstance(updated_item.get("question_id"), str):
                try:
                    updated_item["question_id"] = (
                        HumanClarificationQuestion.from_wire_strict(
                            updated_item,
                            question_index=question_index,
                            source="core",
                        ).question_id
                    )
                except ValueError:
                    updated_item["question_id"] = slugify_question_identifier(
                        updated_item["question_id"]
                    )
            normalized_raw_questions.append(updated_item)
        return replace(
            request,
            input={
                **dict(request.input),
                "questions": normalized_raw_questions,
            },
        )

    async def run_tool_use(
        self,
        *,
        context: ToolRuntimeContext,
        request: ToolCall,
        record_request: bool = True,
        execution_protocol: ToolExecutionProtocol = "auto",
    ) -> ToolInvocationOutcome:
        async with self._concurrency_gate.claim(
            concurrency_safe=self._request_is_concurrency_safe(
                context=context,
                request=request,
            ),
            cancel_checker=context.cancel_checker,
        ):
            return await self.invoke_tool(
                context=context,
                request=request,
                record_request=record_request,
                execution_protocol=execution_protocol,
            )

    def run_tools(
        self,
        *,
        context: ToolRuntimeContext,
        requests: list[ToolCall] | list[ToolCall],
        execution_protocol: ToolExecutionProtocol = "auto",
    ) -> list[ToolInvocationOutcome]:
        terminal_requests: set[int] = set()

        def mark_terminal(request: ToolCall) -> None:
            terminal_requests.add(id(request))
            if context.on_tool_terminal is not None:
                context.on_tool_terminal()

        engine: ToolExecutionEngine[ToolInvocationOutcome] = ToolExecutionEngine(
            invoke_tool=lambda request: self.run_tool_use(
                context=replace(context, on_tool_terminal=lambda: mark_terminal(request)),
                request=request,
                execution_protocol=execution_protocol,
            ),
            cancel_checker=context.cancel_checker,
            terminal_checker=lambda request: id(request) in terminal_requests,
        )
        return engine.execute(
            requests=requests,
        )

    def _record_tool_call_outcome(
        self, *, context: ToolRuntimeContext, record: ToolInvocationRecord
    ) -> None:
        if context.conversation_exchange_id and context.output_sequence_index is not None:
            record = replace(record, context_effects={
                **record.context_effects,
                'conversation_exchange_id': context.conversation_exchange_id,
                'output_sequence_index': context.output_sequence_index,
            })
        context.state_store.record_tool_call_record(record)

    async def invoke_tool(
        self,
        *,
        context: ToolRuntimeContext,
        request: ToolCall,
        record_request: bool = True,
        tool_call_recovery_attempted: bool = False,
        execution_protocol: ToolExecutionProtocol = "auto",
    ) -> ToolInvocationOutcome:
        materialized_call = self._materialize_tool_call(
            context=context, request=request
        )
        materialized_request = materialized_call
        outcome_request = materialized_call
        resolved_execution_protocol = _resolve_tool_execution_protocol(
            request=materialized_request,
            execution_protocol=execution_protocol,
        )
        if (
            resolved_execution_protocol != "runtime_policy"
            and materialized_request.tool_name != "human_question"
            and is_side_channel_control_tool_name(materialized_request.tool_name)
        ):
            if record_request:
                context.state_store.record_tool_call_request(materialized_request)
            return self._protocol_violation_outcome(
                context=context,
                request=outcome_request,
                violation_kind="tool_schema_not_loaded",
                message=(
                    f"{materialized_request.tool_name} is a controller side-channel, not a callable tool; "
                    "use the controller-side clarification flow or answer the live permission request"
                ),
            )
        requested_definition = self._requested_definition(
            context,
            materialized_request.tool_name,
            requested_by=materialized_request.requested_by,
        )
        dataset_path_override = self._dataset_path_override(
            context=context,
            tool_name=materialized_request.tool_name,
        )
        fallback_live_definition = requested_definition
        hook_engine = _hook_engine_for_context(context, self._hook_engine)
        if record_request:
            context.state_store.record_tool_call_request(
                _redacted_persistent_tool_call(
                    definition=fallback_live_definition,
                    request=materialized_request,
                )
            )
        if requested_definition is None:
            record = self._build_invocation_record(
                context=context,
                request_id=materialized_request.request_id,
                tool_name=materialized_request.tool_name,
                status="tool_runtime_failure",
                granted=False,
                input_digest=_persistent_input_digest(
                    definition=None,
                    input_payload=materialized_request.input,
                ),
                error=f"unknown tool: {materialized_request.tool_name}",
                created_at=now_utc_iso(),
                finished_at=now_utc_iso(),
            )
            self._record_tool_call_outcome(context=context, record=record)
            return ToolInvocationOutcome(request=materialized_request, record=record)
        definition = requested_definition
        if not tool_role_allowed(definition.name, context.actor_kind):
            return self._protocol_violation_outcome(
                context=context,
                request=outcome_request,
                violation_kind='tool_role_not_allowed',
                message=f'{definition.name} is not available to {context.actor_kind}',
            )
        validation_feedback = definition.validate_input(materialized_request.input)
        if validation_feedback is not None:
            return self._protocol_violation_outcome(
                context=context,
                request=outcome_request,
                violation_kind=str(
                    validation_feedback.get("violation_kind", "shape_mismatch")
                    or "shape_mismatch"
                ),
                message=validation_feedback["error"],
                warning=validation_feedback["compact_hint"],
                input_payload=materialized_call.input,
                context_effects={
                    "allowed_input_keys": list(
                        validation_feedback.get("allowed_input_keys", [])
                    ),
                    "required_input_keys": list(
                        validation_feedback.get("required_input_keys", [])
                    ),
                    "invalid_input_keys": list(
                        validation_feedback.get("invalid_input_keys", [])
                    ),
                    "missing_required_input_keys": list(
                        validation_feedback.get("missing_required_input_keys", [])
                    ),
                    "default_input_examples": list(
                        validation_feedback.get("default_input_examples", [])
                    ),
                    "schema_validation_hint": str(
                        validation_feedback.get("compact_hint", "") or ""
                    ),
                },
            )
        destructive_assessment = _assess_destructive_action(
            definition=definition,
            input=materialized_request.input,
            project_root=context.project_root,
        )
        environment_selection = self._resolve_execution_environment(
            context=context,
            definition=definition,
            input=materialized_request.input,
            destructive_action=destructive_assessment.is_destructive,
        )
        execution_context = self._build_execution_context(
            context=context,
            dataset_path_override=dataset_path_override,
            environment_selection=environment_selection,
            tool_call_id=materialized_request.request_id,
        )
        request_context_effects = _build_request_context_effects(
            definition=definition,
            input=materialized_request.input,
        )
        validation_feedback = _run_custom_input_validator(
            definition=definition,
            execution_context=execution_context,
            input=materialized_request.input,
            request=materialized_request,
        )
        if validation_feedback is not None:
            record = self._build_invocation_record(
                context=context,
                request_id=materialized_request.request_id,
                tool_name=definition.name,
                status="tool_validation_error",
                granted=False,
                input_digest=_persistent_input_digest(
                    definition=(
                        definition or requested_definition or fallback_live_definition
                    ),
                    input_payload=materialized_request.input,
                ),
                result_summary=validation_feedback["compact_hint"],
                warning=validation_feedback["compact_hint"],
                error=validation_feedback["error"],
                validation_code=(
                    str(validation_feedback.get("validation_code")).strip()
                    if validation_feedback.get("validation_code") is not None
                    else None
                ),
                context_effects={
                    "schema_validation_hint": validation_feedback["compact_hint"],
                    "validation_source": str(
                        validation_feedback.get("validation_source") or "schema"
                    ),
                    "validation_code": (
                        str(validation_feedback.get("validation_code")).strip()
                        if validation_feedback.get("validation_code") is not None
                        else ""
                    ),
                    "allowed_input_keys": list(
                        validation_feedback.get("allowed_input_keys", [])
                    ),
                    "required_input_keys": list(
                        validation_feedback.get("required_input_keys", [])
                    ),
                    "invalid_input_keys": list(
                        validation_feedback.get("invalid_input_keys", [])
                    ),
                    "missing_required_input_keys": list(
                        validation_feedback.get("missing_required_input_keys", [])
                    ),
                    "default_input_examples": list(
                        validation_feedback.get("default_input_examples", [])
                    ),
                    **_custom_validation_context_effects(validation_feedback),
                    **request_context_effects,
                },
                created_at=now_utc_iso(),
                finished_at=now_utc_iso(),
            )
            self._record_tool_call_outcome(context=context, record=record)
            context.state_store.record_tool_observation(
                ToolObservation(
                    observation_id=f"tool_schema_validation:{materialized_request.request_id}",
                    source_type="schema_validation",
                    source_name=definition.name,
                    stage=context.stage.value,
                    attempt=context.attempt,
                    query_chain_id=context.query_chain_id,
                    query_chain_depth=context.query_chain_depth,
                    continuation_index=context.continuation_index,
                    summary=str(
                        validation_feedback.get("observation_summary")
                        or validation_feedback["compact_hint"]
                    ),
                    created_at=now_utc_iso(),
                )
            )
            recovery_outcome = await self._recover_after_tool_validation_error(
                context=context,
                original_request=materialized_request,
                original_definition=definition,
                validation_feedback=validation_feedback,
                record_request=record_request,
                recovery_attempted=tool_call_recovery_attempted,
                execution_protocol=resolved_execution_protocol,
            )
            if recovery_outcome is not None:
                return recovery_outcome
            return ToolInvocationOutcome(request=materialized_request, record=record)
        if definition.name == "human_question":
            materialized_request = self._normalize_human_question_tool_call_ids(
                materialized_request
            )
            outcome_request = materialized_request
            request_context_effects = _build_request_context_effects(
                definition=definition,
                input=materialized_request.input,
            )
        hook_decision = await run_pre_tool_hooks(
            hook_engine,
            context=context,
            definition=definition,
            input_payload=dict(materialized_request.input),
            tool_call_id=materialized_request.request_id,
        )
        hook_delivery = hook_decision.delivery
        persist_hook_delivery_batch(
            context.state_store.state.context_state,
            hook_delivery,
        )
        if hook_decision.blocked:
            return self._protocol_violation_outcome(
                context=context,
                request=outcome_request,
                violation_kind="hook_validation_rejected",
                message=(
                    hook_decision.reason
                    or f"PreToolUse hook rejected {definition.name} during validation"
                ),
            )
        if hook_decision.updated_input is not None:
            materialized_request = replace(
                materialized_request, input=dict(hook_decision.updated_input)
            )
            outcome_request = materialized_request
            request_context_effects = {
                **_build_request_context_effects(
                    definition=definition,
                    input=materialized_request.input,
                ),
                "hook_stage": "before_tool_use",
                "hook_input_overridden": True,
                "hook_metadata": outcome_metadata(hook_decision),
            }
            validation_feedback = definition.validate_input(materialized_request.input)
            if validation_feedback is not None:
                return self._protocol_violation_outcome(
                    context=context,
                    request=outcome_request,
                    violation_kind=str(
                        validation_feedback.get("violation_kind", "shape_mismatch")
                        or "shape_mismatch"
                    ),
                    message=validation_feedback["error"],
                    warning=validation_feedback["compact_hint"],
                    input_payload=materialized_request.input,
                    context_effects={
                        "allowed_input_keys": list(
                            validation_feedback.get("allowed_input_keys", [])
                        ),
                        "required_input_keys": list(
                            validation_feedback.get("required_input_keys", [])
                        ),
                        "invalid_input_keys": list(
                            validation_feedback.get("invalid_input_keys", [])
                        ),
                        "missing_required_input_keys": list(
                            validation_feedback.get("missing_required_input_keys", [])
                        ),
                        "default_input_examples": list(
                            validation_feedback.get("default_input_examples", [])
                        ),
                        "schema_validation_hint": str(
                            validation_feedback.get("compact_hint", "") or ""
                        ),
                        **_custom_validation_context_effects(validation_feedback),
                        **request_context_effects,
                    },
                )
            destructive_assessment = _assess_destructive_action(
                definition=definition,
                input=materialized_request.input,
                project_root=context.project_root,
            )
            environment_selection = self._resolve_execution_environment(
                context=context,
                definition=definition,
                input=materialized_request.input,
                destructive_action=destructive_assessment.is_destructive,
            )
            execution_context = self._build_execution_context(
                context=context,
                dataset_path_override=dataset_path_override,
                environment_selection=environment_selection,
                tool_call_id=materialized_request.request_id,
            )
            validation_feedback = _run_custom_input_validator(
                definition=definition,
                execution_context=execution_context,
                input=materialized_request.input,
                request=materialized_request,
            )
            if validation_feedback is not None:
                record = self._build_invocation_record(
                    context=context,
                    request_id=materialized_request.request_id,
                    tool_name=definition.name,
                    status="tool_validation_error",
                    granted=False,
                    input_digest=_persistent_input_digest(
                        definition=(
                            definition
                            or requested_definition
                            or fallback_live_definition
                        ),
                        input_payload=materialized_request.input,
                    ),
                    result_summary=validation_feedback["compact_hint"],
                    warning=validation_feedback["compact_hint"],
                    error=validation_feedback["error"],
                    validation_code=(
                        str(validation_feedback.get("validation_code")).strip()
                        if validation_feedback.get("validation_code") is not None
                        else None
                    ),
                    context_effects={
                        "schema_validation_hint": validation_feedback["compact_hint"],
                        "validation_source": str(
                            validation_feedback.get("validation_source") or "schema"
                        ),
                        "validation_code": (
                            str(validation_feedback.get("validation_code")).strip()
                            if validation_feedback.get("validation_code") is not None
                            else ""
                        ),
                        "allowed_input_keys": list(
                            validation_feedback.get("allowed_input_keys", [])
                        ),
                        "required_input_keys": list(
                            validation_feedback.get("required_input_keys", [])
                        ),
                        "invalid_input_keys": list(
                            validation_feedback.get("invalid_input_keys", [])
                        ),
                        "missing_required_input_keys": list(
                            validation_feedback.get("missing_required_input_keys", [])
                        ),
                        "default_input_examples": list(
                            validation_feedback.get("default_input_examples", [])
                        ),
                        **_custom_validation_context_effects(validation_feedback),
                        **request_context_effects,
                    },
                    created_at=now_utc_iso(),
                    finished_at=now_utc_iso(),
                )
                self._record_tool_call_outcome(context=context, record=record)
                context.state_store.record_tool_observation(
                    ToolObservation(
                        observation_id=f"tool_schema_validation:{materialized_request.request_id}",
                        source_type="schema_validation",
                        source_name=definition.name,
                        stage=context.stage.value,
                        attempt=context.attempt,
                        query_chain_id=context.query_chain_id,
                        query_chain_depth=context.query_chain_depth,
                        continuation_index=context.continuation_index,
                        summary=str(
                            validation_feedback.get("observation_summary")
                            or validation_feedback["compact_hint"]
                        ),
                        created_at=now_utc_iso(),
                    )
                )
                recovery_outcome = await self._recover_after_tool_validation_error(
                    context=context,
                    original_request=materialized_request,
                    original_definition=definition,
                    validation_feedback=validation_feedback,
                    record_request=record_request,
                    recovery_attempted=tool_call_recovery_attempted,
                    execution_protocol=resolved_execution_protocol,
                )
                if recovery_outcome is not None:
                    return recovery_outcome
                return ToolInvocationOutcome(
                    request=materialized_request, record=record
                )
        runtime_resource_grant = runtime_text_resource_record_for_request(
            context=context,
            tool_name=definition.name,
            input_payload=materialized_request.input,
        )
        if definition.name == "human_question":
            permission_decision = self._tool_permission_decision(
                context=context,
                definition=definition,
                requested_by=materialized_request.requested_by,
                destructive_assessment=destructive_assessment,
                input_payload=materialized_request.input,
                request_id=materialized_request.request_id,
            )
            context.state_store.record_tool_permission_event(
                ToolPermissionEvent(
                    event_id=f"permission:{materialized_request.request_id}",
                    request_id=materialized_request.request_id,
                    tool_name=definition.name,
                    permission_class=(
                        permission_decision.permission_class
                        or definition.summary.permission_class
                    ),
                    behavior=permission_decision.behavior,
                    granted=permission_decision.granted,
                    reason=permission_decision.reason,
                    base_permission_class=(
                        permission_decision.base_permission_class
                        or definition.summary.permission_class
                    ),
                    destructive_action=permission_decision.destructive_action,
                    destructive_reason=permission_decision.destructive_reason,
                    destructive_kinds=permission_decision.destructive_kinds,
                    stage=context.stage.value,
                    attempt=context.attempt,
                    query_chain_id=context.query_chain_id,
                    query_chain_depth=context.query_chain_depth,
                    continuation_index=context.continuation_index,
                    requested_by=materialized_request.requested_by,
                    **_permission_decision_event_fields(permission_decision),
                    created_at=now_utc_iso(),
                )
            )
            if not permission_decision.granted:
                denial = permission_decision.reason or f"tool denied: {definition.name}"
                record = self._build_invocation_record(
                    context=context,
                    request_id=materialized_request.request_id,
                    tool_name=definition.name,
                    status="denied",
                    granted=False,
                    input_digest=_persistent_input_digest(
                        definition=(
                            definition
                            or requested_definition
                            or fallback_live_definition
                        ),
                        input_payload=materialized_request.input,
                    ),
                    permission_class=(
                        permission_decision.permission_class
                        or definition.summary.permission_class
                    ),
                    base_permission_class=(
                        permission_decision.base_permission_class
                        or definition.summary.permission_class
                    ),
                    destructive_action=permission_decision.destructive_action,
                    destructive_reason=permission_decision.destructive_reason,
                    destructive_kinds=permission_decision.destructive_kinds,
                    warning=denial,
                    context_effects=request_context_effects,
                    created_at=now_utc_iso(),
                    finished_at=now_utc_iso(),
                )
                self._record_tool_call_outcome(context=context, record=record)
                context.state_store.record_tool_observation(
                    ToolObservation(
                        observation_id=f"tool_permission_denial:{materialized_request.request_id}",
                        source_type="permission_denial",
                        source_name=definition.name,
                        stage=context.stage.value,
                        attempt=context.attempt,
                        query_chain_id=context.query_chain_id,
                        query_chain_depth=context.query_chain_depth,
                        continuation_index=context.continuation_index,
                        summary=denial,
                        created_at=now_utc_iso(),
                    )
                )
                return ToolInvocationOutcome(
                    request=materialized_request, record=record
                )
            clarification_denial = _deny_redundant_bound_dataset_confirmation(
                context=context,
                request=materialized_request,
            )
            if clarification_denial is not None:
                record = self._build_invocation_record(
                    context=context,
                    request_id=materialized_request.request_id,
                    tool_name=definition.name,
                    status="tool_validation_error",
                    granted=False,
                    input_digest=_persistent_input_digest(
                        definition=(
                            definition
                            or requested_definition
                            or fallback_live_definition
                        ),
                        input_payload=materialized_request.input,
                    ),
                    permission_class=(
                        permission_decision.permission_class
                        or definition.summary.permission_class
                    ),
                    base_permission_class=(
                        permission_decision.base_permission_class
                        or definition.summary.permission_class
                    ),
                    result_summary=clarification_denial["compact_hint"],
                    warning=clarification_denial["compact_hint"],
                    error=clarification_denial["error"],
                    validation_code=clarification_denial["validation_code"],
                    context_effects={
                        "validation_source": "runtime_boundary_policy",
                        "validation_code": clarification_denial["validation_code"],
                        "recovery_hint": clarification_denial["recovery_hint"],
                        "authoritative_paths": clarification_denial[
                            "authoritative_paths"
                        ],
                        **request_context_effects,
                    },
                    created_at=now_utc_iso(),
                    finished_at=now_utc_iso(),
                )
                self._record_tool_call_outcome(context=context, record=record)
                context.state_store.record_tool_observation(
                    ToolObservation(
                        observation_id=f"human_question_denied:{materialized_request.request_id}",
                        source_type="human_question_policy_denial",
                        source_name=definition.name,
                        stage=context.stage.value,
                        attempt=context.attempt,
                        query_chain_id=context.query_chain_id,
                        query_chain_depth=context.query_chain_depth,
                        continuation_index=context.continuation_index,
                        summary=clarification_denial["compact_hint"],
                        created_at=now_utc_iso(),
                    )
                )
                return ToolInvocationOutcome(
                    request=materialized_request, record=record
                )
            return self._await_human_question_interaction(
                context=context,
                request=materialized_request,
                definition=definition,
                request_context_effects=request_context_effects,
            )
        try:
            permission_decision, canonical_evaluation = (
                self._canonical_permission_decision(
                    context=context,
                    definition=definition,
                    requested_by=materialized_request.requested_by,
                    destructive_assessment=destructive_assessment,
                    input_payload=materialized_request.input,
                    environment_selection=environment_selection,
                    request_id=materialized_request.request_id,
                )
            )
        except ToolPreflightValidationError as exc:
            return self._preflight_validation_outcome(
                context=context,
                request=outcome_request,
                definition=definition,
                error=exc,
                request_context_effects=request_context_effects,
            )
        effective_permissions_for_execution = canonical_evaluation.effective_permissions
        approved_permission_session_id = None
        approved_permission_session = None
        if permission_decision.behavior == "ask" and hook_engine is not None:
            permission_outcome = await hook_engine.run_async(
                PermissionRequestHookRequest(
                    payload={
                        "tool_name": definition.name,
                        "tool_input": dict(materialized_request.input),
                        "permission": permission_decision.to_dict(),
                        "cwd": str(context.project_root),
                        "run_id": context.run_id,
                        "stage": context.stage.value,
                        "attempt": context.attempt,
                        "actor_kind": context.actor_kind,
                        "tool_call_id": materialized_request.request_id,
                    }
                ),
                effective_permissions=context.canonical_permissions,
            )
            if not isinstance(permission_outcome, PermissionOutcome):
                raise TypeError("PermissionRequest adapter received an incompatible outcome")
            persist_hook_delivery_batch(
                context.state_store.state.context_state, permission_outcome.delivery,
            )
            hook_delivery = merge_hook_delivery_batches(hook_delivery, permission_outcome.delivery)
            if permission_outcome.decision in {"allow", "deny"}:
                approval_request = canonical_evaluation.approval_request
                assert approval_request is not None
                receipt = ApprovalReceipt(
                    request_id=approval_request.request_id,
                    approved=permission_outcome.decision == "allow",
                    reviewer=context.canonical_permissions.approvals_reviewer,
                    fingerprint=approval_fingerprint(approval_request),
                    approved_overlay=approval_request.requested_overlay,
                    reason=permission_outcome.reason or f"PermissionRequest hook: {permission_outcome.decision}",
                )
                reviewed = PermissionOrchestrator().apply_approval(canonical_evaluation, receipt)
                effective_permissions_for_execution = reviewed.effective_permissions
                permission_decision = replace(
                    permission_decision,
                    behavior="allow" if reviewed.allowed else "deny",
                    granted=reviewed.allowed,
                    reason=receipt.reason if reviewed.allowed else (reviewed.denial_reason or receipt.reason),
                    decision_steps=[
                        *permission_decision.decision_steps,
                        _canonical_approval_step("permission_hook", receipt),
                    ],
                )
                request_context_effects = {
                    **request_context_effects,
                    "permission_hook": outcome_metadata(permission_outcome),
                }
        if (
            permission_decision.behavior == "ask"
            and approved_permission_session is None
        ):
            approval_request = canonical_evaluation.approval_request
            assert approval_request is not None
            session_grant = self._matching_session_grant(
                context=context,
                request=approval_request,
            )
            if session_grant is not None:
                approval_receipt = ApprovalReceipt(
                    request_id=approval_request.request_id,
                    approved=True,
                    reviewer=ApprovalsReviewer(session_grant.reviewer),
                    fingerprint=approval_fingerprint(approval_request),
                    approved_overlay=approval_request.requested_overlay,
                    reason=f"covered by bounded session grant {session_grant.grant_id}",
                )
                approved_evaluation = PermissionOrchestrator().apply_approval(
                    canonical_evaluation,
                    approval_receipt,
                )
                if approved_evaluation.allowed:
                    effective_permissions_for_execution = (
                        approved_evaluation.effective_permissions
                    )
                    permission_decision = replace(
                        permission_decision,
                        behavior="allow",
                        granted=True,
                        reason=f"allowed by bounded session grant: {session_grant.grant_id}",
                        decision_steps=[
                            *permission_decision.decision_steps,
                            _canonical_approval_step(
                                "session_approval_grant",
                                approval_receipt,
                                grant_id=session_grant.grant_id,
                            ),
                        ],
                    )
        if (
            permission_decision.behavior == "ask"
            and context.canonical_permissions.approvals_reviewer
            is ApprovalsReviewer.AUTO_REVIEW
        ):
            approval_request = canonical_evaluation.approval_request
            assert approval_request is not None
            if self._auto_review_service is None:
                permission_decision = replace(
                    permission_decision,
                    behavior="deny",
                    granted=False,
                    reason="auto-review is configured but no independent reviewer is available",
                )
            else:
                receipt = await self._auto_review_service.review(
                    AutoReviewContext(
                        request=approval_request,
                        user_authorization_text=context.raw_user_message,
                        sanitized_action=dict(
                            canonical_evaluation.operation.input_payload
                        ),
                        evidence=(
                            {
                                "stage": context.stage.value,
                                "attempt": context.attempt,
                                "requested_by": materialized_request.requested_by,
                            },
                        ),
                    ),
                    cancel_checker=context.cancel_checker,
                )
                reviewed_evaluation = PermissionOrchestrator().apply_approval(
                    canonical_evaluation,
                    receipt,
                )
                if reviewed_evaluation.allowed:
                    effective_permissions_for_execution = (
                        reviewed_evaluation.effective_permissions
                    )
                    permission_decision = replace(
                        permission_decision,
                        behavior="allow",
                        granted=True,
                        reason=receipt.reason or "allowed by independent auto-review",
                        decision_steps=[
                            *permission_decision.decision_steps,
                            _canonical_approval_step("auto_review", receipt),
                        ],
                    )
                else:
                    permission_decision = replace(
                        permission_decision,
                        behavior="deny",
                        granted=False,
                        reason=(
                            reviewed_evaluation.denial_reason
                            or receipt.reason
                            or "denied by independent auto-review"
                        ),
                    )
        if permission_decision.behavior == "ask":
            permission_session = self._build_permission_session(
                context=context,
                request=materialized_request,
                definition=definition,
                permission_decision=permission_decision,
                approval_request=canonical_evaluation.approval_request,
            )
            approved_permission_session = await wait_for_approval(context, permission_session)
            approved_permission_session_id = permission_session.session_id
            approval_request = canonical_evaluation.approval_request
            assert approval_request is not None
            receipt = ApprovalReceipt(
                request_id=approval_request.request_id,
                approved=approved_permission_session.decision == "approve",
                reviewer=ApprovalsReviewer(approved_permission_session.reviewer),
                fingerprint=approved_permission_session.approval_fingerprint,
                approved_overlay=approval_request.requested_overlay,
                reason="approved by user" if approved_permission_session.decision == "approve" else "denied by user",
            )
            reviewed = PermissionOrchestrator().apply_approval(canonical_evaluation, receipt)
            effective_permissions_for_execution = reviewed.effective_permissions
            permission_decision = replace(
                permission_decision, behavior="allow" if reviewed.allowed else "deny",
                granted=reviewed.allowed,
                reason=receipt.reason if reviewed.allowed else (reviewed.denial_reason or receipt.reason),
                decision_steps=[
                    *permission_decision.decision_steps,
                    _canonical_approval_step("user_approval", receipt),
                ],
            )
        if permission_decision.behavior == "allow":
            operation = canonical_evaluation.operation
            if operation.session_identity:
                current_effects = definition.resolve_declared_effects(
                    context, materialized_request.input,
                )
                current_action = compile_canonical_action(
                    request_id=operation.request_id, tool_name=operation.tool_name,
                    permission_class=permission_decision.permission_class,
                    input_payload=operation.input_payload, environment=operation.environment,
                    destructive=destructive_assessment.is_destructive,
                    destructive_reason=destructive_assessment.reason,
                    declared_effects=current_effects,
                )
                if current_action.operation.effect_hash != operation.effect_hash:
                    permission_decision = replace(
                        permission_decision, behavior="deny", granted=False,
                        reason="MCP server identity or effects changed before execution",
                    )
        if permission_decision.behavior == "allow":
            execution_recheck = PermissionOrchestrator().revalidate_for_execution(
                canonical_evaluation,
                effective_permissions=effective_permissions_for_execution,
            )
            if not execution_recheck.allowed:
                permission_decision = replace(
                    permission_decision,
                    behavior="deny",
                    granted=False,
                    reason=(
                        execution_recheck.denial_reason
                        or "canonical permissions changed before execution"
                    ),
                )
            elif (
                effective_permissions_for_execution.policy_hash
                != canonical_evaluation.effective_permissions.policy_hash
            ):
                environment_selection = self._resolve_execution_environment(
                    context=context,
                    definition=definition,
                    input=materialized_request.input,
                    destructive_action=destructive_assessment.is_destructive,
                    effective_permissions=effective_permissions_for_execution,
                )
        context.state_store.record_tool_permission_event(
            ToolPermissionEvent(
                event_id=f"permission:{materialized_request.request_id}",
                request_id=materialized_request.request_id,
                tool_name=definition.name,
                permission_class=(
                    permission_decision.permission_class
                    or definition.summary.permission_class
                ),
                behavior=permission_decision.behavior,
                granted=permission_decision.granted,
                reason=permission_decision.reason,
                base_permission_class=(
                    permission_decision.base_permission_class
                    or definition.summary.permission_class
                ),
                destructive_action=permission_decision.destructive_action,
                destructive_reason=permission_decision.destructive_reason,
                destructive_kinds=permission_decision.destructive_kinds,
                stage=context.stage.value,
                attempt=context.attempt,
                query_chain_id=context.query_chain_id,
                query_chain_depth=context.query_chain_depth,
                continuation_index=context.continuation_index,
                requested_by=materialized_request.requested_by,
                session_id=(
                    approved_permission_session.session_id
                    if approved_permission_session is not None
                    else None
                ),
                resolution=(
                    approved_permission_session.decision if approved_permission_session is not None else None
                ),
                answered_at=(
                    approved_permission_session.answered_at
                    if approved_permission_session is not None
                    else None
                ),
                **_permission_decision_event_fields(permission_decision),
                created_at=now_utc_iso(),
            )
        )
        if not permission_decision.granted:
            denial = permission_decision.reason or f"tool denied: {definition.name}"
            denied_contract, denied_policy_decision, denied_policy_hash = (
                self._compile_execution_contract(
                    context=context,
                    definition=definition,
                    request=materialized_request,
                    permission_decision=permission_decision,
                    approved_permission_session=approved_permission_session,
                    environment_selection=environment_selection,
                    requested_at=now_utc_iso(),
                )
            )
            denied_contract_hash = execution_contract_hash(denied_contract)
            context.state_store.record_execution_contract_compiled(
                denied_contract,
                status=denied_contract.status,
                stage=context.stage.value,
                attempt=context.attempt,
                query_chain_id=context.query_chain_id,
                query_chain_depth=context.query_chain_depth,
                continuation_index=context.continuation_index,
                request_id=materialized_request.request_id,
                tool_name=definition.name,
                contract_hash=denied_contract_hash,
                exec_policy_decision=denied_policy_decision,
                exec_policy_hash_value=denied_policy_hash,
            )
            record = self._build_invocation_record(
                context=context,
                request_id=materialized_request.request_id,
                tool_name=definition.name,
                status="denied",
                granted=False,
                permission_session_id=approved_permission_session_id,
                input_digest=_persistent_input_digest(
                    definition=(
                        definition or requested_definition or fallback_live_definition
                    ),
                    input_payload=materialized_request.input,
                ),
                warning=denial,
                context_effects={
                    "execution_contract_id": denied_contract.contract_id,
                    "execution_contract_hash": denied_contract_hash,
                    "exec_policy_decision_id": denied_policy_decision.decision_id,
                    "exec_policy_hash": denied_policy_hash,
                },
                created_at=now_utc_iso(),
                finished_at=now_utc_iso(),
            )
            self._record_tool_call_outcome(context=context, record=record)
            context.state_store.record_tool_observation(
                ToolObservation(
                    observation_id=f"tool_permission_denial:{materialized_request.request_id}",
                    source_type="permission_denial",
                    source_name=definition.name,
                    stage=context.stage.value,
                    attempt=context.attempt,
                    query_chain_id=context.query_chain_id,
                    query_chain_depth=context.query_chain_depth,
                    continuation_index=context.continuation_index,
                    summary=denial,
                    created_at=now_utc_iso(),
                )
            )
            return ToolInvocationOutcome(request=materialized_request, record=record)
        if definition.handler is None:
            skipped_contract, skipped_policy_decision, skipped_policy_hash = (
                self._compile_terminal_failure_contract(
                    context=context,
                    request=materialized_request,
                    definition=definition,
                    permission_class=(
                        permission_decision.permission_class
                        or definition.summary.permission_class
                    ),
                    behavior="allow",
                    granted=True,
                    reason="tool has no executable handler",
                    destructive_assessment=destructive_assessment,
                    environment_selection=environment_selection,
                    requested_at=now_utc_iso(),
                    approved_permission_session=approved_permission_session,
                )
            )
            skipped_contract_hash = execution_contract_hash(skipped_contract)
            context.state_store.record_execution_contract_compiled(
                skipped_contract,
                status=skipped_contract.status,
                stage=context.stage.value,
                attempt=context.attempt,
                query_chain_id=context.query_chain_id,
                query_chain_depth=context.query_chain_depth,
                continuation_index=context.continuation_index,
                request_id=materialized_request.request_id,
                tool_name=definition.name,
                contract_hash=skipped_contract_hash,
                exec_policy_decision=skipped_policy_decision,
                exec_policy_hash_value=skipped_policy_hash,
            )
            record = self._build_invocation_record(
                context=context,
                request_id=materialized_request.request_id,
                tool_name=definition.name,
                status="skipped",
                granted=False,
                permission_session_id=approved_permission_session_id,
                input_digest=_persistent_input_digest(
                    definition=(
                        definition or requested_definition or fallback_live_definition
                    ),
                    input_payload=materialized_request.input,
                ),
                permission_class=(
                    permission_decision.permission_class
                    or definition.summary.permission_class
                ),
                base_permission_class=(
                    permission_decision.base_permission_class
                    or definition.summary.permission_class
                ),
                destructive_action=permission_decision.destructive_action,
                destructive_reason=permission_decision.destructive_reason,
                destructive_kinds=permission_decision.destructive_kinds,
                warning="tool has no executable handler",
                context_effects={
                    "execution_contract_id": skipped_contract.contract_id,
                    "execution_contract_hash": skipped_contract_hash,
                    "exec_policy_decision_id": skipped_policy_decision.decision_id,
                    "exec_policy_hash": skipped_policy_hash,
                },
                created_at=now_utc_iso(),
                finished_at=now_utc_iso(),
            )
            self._record_tool_call_outcome(context=context, record=record)
            return ToolInvocationOutcome(request=materialized_request, record=record)
        created_at = now_utc_iso()
        execution_contract, exec_policy_decision, exec_policy_hash_value = (
            self._compile_execution_contract(
                context=context,
                definition=definition,
                request=materialized_request,
                permission_decision=permission_decision,
                approved_permission_session=approved_permission_session,
                environment_selection=environment_selection,
                requested_at=created_at,
                effective_permissions=effective_permissions_for_execution,
            )
        )
        execution_contract_hash_value = execution_contract_hash(execution_contract)
        context.state_store.record_execution_contract_compiled(
            execution_contract,
            status=execution_contract.status,
            stage=context.stage.value,
            attempt=context.attempt,
            query_chain_id=context.query_chain_id,
            query_chain_depth=context.query_chain_depth,
            continuation_index=context.continuation_index,
            request_id=materialized_request.request_id,
            tool_name=definition.name,
            contract_hash=execution_contract_hash_value,
            exec_policy_decision=exec_policy_decision,
            exec_policy_hash_value=exec_policy_hash_value,
            effective_permissions=effective_permissions_for_execution,
        )
        execution_context = self._build_execution_context(
            context=context,
            dataset_path_override=dataset_path_override,
            environment_selection=environment_selection,
            tool_call_id=materialized_request.request_id,
            execution_contract=execution_contract,
            exec_policy_decision=exec_policy_decision,
            exec_policy_hash_value=exec_policy_hash_value,
            effective_permissions=effective_permissions_for_execution,
        )
        request_context_effects = {
            **request_context_effects,
            "execution_contract_id": execution_contract.contract_id,
            "execution_contract_hash": execution_contract_hash_value,
            "exec_policy_decision_id": exec_policy_decision.decision_id,
            "exec_policy_hash": exec_policy_hash_value,
        }
        try:
            runtime_resource_mutation = capture_runtime_text_resource_mutation(
                context=context,
                tool_name=definition.name,
                input_payload=materialized_request.input,
            )
        except (OSError, ValueError) as exc:
            return self._protocol_violation_outcome(
                context=context,
                request=outcome_request,
                violation_kind="runtime_text_resource_integrity_failed",
                message=str(exc),
                input_payload=materialized_request.input,
            )
        if callable(context.cancel_checker) and context.cancel_checker():
            raise CancelledError("Tool invocation cancelled before handler execution")
        if context.before_tool_handler is not None:
            context.before_tool_handler(materialized_request)
        try:
            tool_result = await definition.handler(
                execution_context,
                dict(materialized_request.input),
            )
        except (Exception, asyncio.CancelledError) as exc:
            from ownpi.runtime.interactions.pause import HumanClarificationPausedError

            if isinstance(exc, HumanClarificationPausedError):
                if exc.pending_interactions:
                    context.state_store.state.workflow_state.pending_human_clarification = exc.pending_interactions[0]
                raise
            if isinstance(exc, (CancelledError, asyncio.CancelledError)):
                request_context_effects = {
                    **request_context_effects,
                    "failure_class": "tool_execution_cancelled",
                    "execution_started": True,
                }
            record = self._build_invocation_record(
                context=context,
                request_id=materialized_request.request_id,
                tool_name=definition.name,
                status="tool_runtime_failure",
                granted=True,
                permission_session_id=approved_permission_session_id,
                input_digest=_persistent_input_digest(
                    definition=(
                        definition or requested_definition or fallback_live_definition
                    ),
                    input_payload=materialized_request.input,
                ),
                permission_class=(
                    permission_decision.permission_class
                    or definition.summary.permission_class
                ),
                base_permission_class=(
                    permission_decision.base_permission_class
                    or definition.summary.permission_class
                ),
                destructive_action=permission_decision.destructive_action,
                destructive_reason=permission_decision.destructive_reason,
                destructive_kinds=permission_decision.destructive_kinds,
                error=str(exc),
                environment_provider_kind=environment_selection.provider_kind
                if "environment_selection" in locals()
                else None,
                environment_selection_reason=environment_selection.selection_reason
                if "environment_selection" in locals()
                else "",
                environment_receipt_id=None,
                context_effects=request_context_effects,
                created_at=created_at,
                finished_at=now_utc_iso(),
            )
            failure = ToolInvocationOutcome(
                request=outcome_request, record=record, hook_delivery=hook_delivery,
            )
            diagnostics: list[str] = []
            from ownpi.controller.runtime_control_signal import RuntimeControlRequested

            control_error: BaseException | None = (
                exc if isinstance(exc, (CancelledError, asyncio.CancelledError, RuntimeControlRequested))
                else None
            )

            def report_secondary(error: BaseException) -> None:
                nonlocal control_error
                if not isinstance(error, Exception) or isinstance(error, (CancelledError, RuntimeControlRequested)):
                    # Defer control until independent handoffs finish; fatal
                    # exceptions take precedence over cancellation requests.
                    fatal = not isinstance(error, (Exception, asyncio.CancelledError))
                    if control_error is None:
                        control_error = error
                    elif fatal and isinstance(control_error, (Exception, asyncio.CancelledError)):
                        error.add_note(f"Earlier cleanup control: {type(control_error).__name__}: {control_error}")
                        control_error = error
                    elif error is not control_error:
                        control_error.add_note(f"Additional cleanup control: {type(error).__name__}: {error}")
                    return
                diagnostics.append(f"{type(error).__name__}: {error}")
                logging.getLogger(__name__).warning(
                    "Tool failure cleanup failed: request=%s", materialized_request.request_id,
                    exc_info=(type(error), error, error.__traceback__),
                )

            # Preserve the handler failure before any independent cleanup runs.
            if context.on_raw_tool_outcome is not None:
                try:
                    context.on_raw_tool_outcome(failure)
                except BaseException as error:
                    report_secondary(error)
            try:
                runtime_resource_receipt = reconcile_runtime_text_resource_mutation(
                    context=context, snapshot=runtime_resource_mutation,
                    tool_name=definition.name,
                    request_id=materialized_request.request_id, tool_succeeded=False,
                )
                if runtime_resource_receipt is not None:
                    record = replace(record, context_effects={
                        **record.context_effects,
                        "runtime_text_resource_receipt": runtime_resource_receipt,
                    })
            except BaseException as error:
                report_secondary(error)
            try:
                self._record_tool_call_outcome(context=context, record=record)
            except BaseException as error:
                report_secondary(error)
            failure = replace(failure, record=record, diagnostic="; ".join(diagnostics))
            if context.on_raw_tool_outcome is not None:
                try:
                    context.on_raw_tool_outcome(failure)
                except BaseException as error:
                    report_secondary(error)
                    failure = replace(failure, diagnostic="; ".join(diagnostics))
            if not isinstance(exc, (CancelledError, asyncio.CancelledError, RuntimeControlRequested)) and context.on_tool_terminal is not None:
                try:
                    context.on_tool_terminal()
                except BaseException as error:
                    report_secondary(error)
            if control_error is not None:
                raise control_error
            return failure
        if context.on_raw_tool_outcome is not None:
            raw_outcome = ToolInvocationOutcome(
                request=outcome_request,
                record=self._build_invocation_record(
                    context=context, request_id=materialized_request.request_id,
                    tool_name=definition.name,
                    status="completed" if tool_result.ok else "tool_runtime_failure",
                    granted=True, error=tool_result.error, duration=tool_result.duration,
                    result_summary=f"{definition.name}: {'completed' if tool_result.ok else 'tool_runtime_failure'}",
                    permission_decision_id=permission_decision.decision_id or None,
                    permission_class=permission_decision.permission_class or definition.summary.permission_class,
                    base_permission_class=permission_decision.base_permission_class or definition.summary.permission_class,
                    destructive_action=permission_decision.destructive_action,
                    destructive_reason=permission_decision.destructive_reason,
                    destructive_kinds=permission_decision.destructive_kinds,
                    permission_session_id=approved_permission_session_id,
                    context_effects=dict(request_context_effects),
                    created_at=created_at, finished_at=now_utc_iso(),
                ),
                tool_result=tool_result,
                hook_delivery=hook_delivery,
            )
            context.on_raw_tool_outcome(raw_outcome)
        handler_memory_receipt = memory_consumption_receipt_from_metadata(
            tool_result.metadata
        )
        hook_result = await run_post_tool_hooks(
            hook_engine,
            context=context,
            definition=definition,
            input_payload=dict(materialized_request.input),
            result=tool_result,
            tool_call_id=materialized_request.request_id,
        )
        persist_hook_delivery_batch(
            context.state_store.state.context_state,
            hook_result.delivery,
        )
        hook_delivery = merge_hook_delivery_batches(
            hook_delivery,
            hook_result.delivery,
        )
        hook_metadata = outcome_metadata(hook_result)
        if hook_result.receipts:
            request_context_effects = {
                **request_context_effects,
                "hook_stage": "after_tool_use",
                "hook_metadata": hook_metadata,
            }
        if hook_result.updated_mcp_tool_output is not None and not hook_result.blocked:
            rewritten_result = _tool_result_with_updated_mcp_output(
                tool_result,
                hook_result.updated_mcp_tool_output,
            )
            if rewritten_result is not tool_result:
                tool_result = rewritten_result
                request_context_effects = {
                    **request_context_effects,
                    "hook_stage": "after_tool_use",
                    "hook_mcp_tool_output_overridden": True,
                    "hook_metadata": hook_metadata,
                }
        if hook_result.blocked:
            request_context_effects = {
                **request_context_effects,
                "post_tool_use_blocked": True,
                "post_tool_use_block_reason": hook_result.reason or "PostToolUse hook blocked the tool result",
            }
        # Preserve the hook decision before auxiliary receipts can fail.
        if context.on_raw_tool_outcome is not None and (
            raw_outcome.record.context_effects != request_context_effects
            or raw_outcome.tool_result is not tool_result
            or raw_outcome.hook_delivery != hook_delivery
        ):
            context.on_raw_tool_outcome(replace(
                raw_outcome,
                record=replace(raw_outcome.record, context_effects=dict(request_context_effects)),
                tool_result=tool_result,
                hook_delivery=hook_delivery,
            ))
        if context.on_tool_terminal is not None:
            context.on_tool_terminal()
        runtime_resource_receipt = reconcile_runtime_text_resource_mutation(
            context=context,
            snapshot=runtime_resource_mutation,
            tool_name=definition.name,
            request_id=materialized_request.request_id,
            tool_succeeded=tool_result.ok,
        )
        if runtime_resource_receipt is not None:
            request_context_effects = {
                **request_context_effects,
                "runtime_text_resource_receipt": runtime_resource_receipt,
            }
            tool_result = replace(
                tool_result,
                metadata={
                    **dict(tool_result.metadata or {}),
                    "runtime_text_resource_receipt": runtime_resource_receipt,
                },
            )
        tool_result_context_effects = _tool_result_context_effects(tool_result)
        if tool_result_context_effects:
            request_context_effects = {
                **request_context_effects,
                **tool_result_context_effects,
            }
        if handler_memory_receipt is not None:
            handler_memory_receipt = bind_memory_consumption_receipt(
                handler_memory_receipt,
                request_id=materialized_request.request_id,
            )
            request_context_effects = canonicalize_memory_consumption_effects(
                request_context_effects,
                receipt=handler_memory_receipt,
            )
            tool_result = replace(
                tool_result,
                metadata=with_memory_consumption_receipt(
                    tool_result.metadata,
                    handler_memory_receipt,
                ),
            )
        result_id = self._result_id_for_request(materialized_request.request_id)
        finalized_result = self._finalize_tool_result(
            tool_result=tool_result,
            request_id=materialized_request.request_id,
            result_id=result_id,
            stage=context.stage,
            attempt=context.attempt,
            query_chain_id=context.query_chain_id,
            query_chain_depth=context.query_chain_depth,
            continuation_index=context.continuation_index,
        )
        environment_result = (
            _runtime_text_resource_state_result(
                tool_result=finalized_result,
                grant=runtime_resource_grant,
                receipt=runtime_resource_receipt,
            )
            if runtime_resource_grant is not None
            else finalized_result
        )
        project_remote_workflow_result(
            remote_state=context.state_store.state.remote_state,
            tool_name=definition.name,
            input_payload=materialized_request.input,
            tool_result=finalized_result,
        )
        environment_receipt = self._materialize_environment_receipt(
            context=context,
            definition=definition,
            tool_result=environment_result,
            environment_selection=environment_selection,
            execution_contract=execution_contract,
            execution_contract_hash_value=execution_contract_hash_value,
        )
        environment_receipt_view = context.state_store.record_environment_receipt(
            environment_receipt,
            stage=context.stage.value,
            attempt=context.attempt,
            query_chain_id=context.query_chain_id,
            continuation_index=context.continuation_index,
        )
        finalized_result = self._attach_environment_receipt(
            tool_result=finalized_result,
            receipt=environment_receipt,
            receipt_view=environment_receipt_view,
        )
        is_memory_result = (
            handler_memory_receipt is not None
            or definition.result_family
            in {
                "internal_memory",
                "external_knowledge",
            }
        )
        is_receipt_only_result = is_memory_result or runtime_resource_grant is not None
        durable_result = (
            _runtime_text_resource_state_result(
                tool_result=finalized_result,
                grant=runtime_resource_grant,
                receipt=runtime_resource_receipt,
            )
            if runtime_resource_grant is not None
            else memory_receipt_only_tool_result(finalized_result)
            if is_memory_result
            else finalized_result
        )
        raw_result_hash = _stable_hash(durable_result.to_dict())
        observable_input = _redacted_observable_input(
            definition=definition, input_payload=materialized_request.input,
        )
        custom_result_summary = (
            None if is_receipt_only_result else _build_custom_result_summary(
                definition=definition, tool_result=finalized_result,
            )
        )
        execution_record = self._build_invocation_record(
            context=context,
            request_id=materialized_request.request_id,
            tool_name=definition.name,
            status="completed" if finalized_result.ok else "tool_runtime_failure",
            granted=True,
            permission_session_id=approved_permission_session_id,
            input_digest=_persistent_input_digest(
                definition=definition, input_payload=materialized_request.input,
            ),
            permission_class=permission_decision.permission_class or definition.summary.permission_class,
            base_permission_class=permission_decision.base_permission_class or definition.summary.permission_class,
            destructive_action=permission_decision.destructive_action,
            destructive_reason=permission_decision.destructive_reason,
            destructive_kinds=permission_decision.destructive_kinds,
            result_summary=custom_result_summary or (
                f"{definition.name}: {'completed' if finalized_result.ok else 'tool_runtime_failure'}"
            ),
            result_ref=None,
            environment_provider_kind=environment_receipt.provider_kind,
            environment_selection_reason=environment_receipt.selection_reason,
            environment_receipt_id=environment_receipt.receipt_id,
            warning="; ".join(finalized_result.warnings) or None,
            error=finalized_result.error,
            duration=finalized_result.duration,
            context_effects={
                "result_family": definition.result_family,
                "raw_result_hash": raw_result_hash,
                "permission_decision_id": permission_decision.decision_id,
                **({"custom_result_summary": custom_result_summary} if custom_result_summary else {}),
                **_remote_pull_artifact_context_effects(definition=definition, tool_result=finalized_result),
                **request_context_effects,
            },
            created_at=created_at,
            finished_at=now_utc_iso(),
            permission_decision_id=permission_decision.decision_id or None,
            raw_result_hash=raw_result_hash,
            observable_input=observable_input,
        )
        # Hand off the raw in-memory outcome before record storage can fail.
        if context.on_raw_tool_outcome is not None:
            context.on_raw_tool_outcome(ToolInvocationOutcome(
                request=outcome_request, record=execution_record, tool_result=finalized_result,
                hook_delivery=hook_delivery, durable_result=durable_result,
            ))
        self._record_tool_call_outcome(context=context, record=execution_record)
        return ToolInvocationOutcome(
            request=outcome_request, record=execution_record, tool_result=finalized_result,
            hook_delivery=hook_delivery, durable_result=durable_result,
        )

    def invoke_runtime_policy_tools(
        self,
        *,
        context: ToolRuntimeContext,
        requests: list[ToolCall],
    ) -> list[ToolInvocationOutcome]:
        return self.run_tools(
            context=context,
            requests=requests,
        )

    def complete_human_question_interaction(
        self,
        *,
        context: ToolRuntimeContext,
        session: HumanClarificationSession,
        answered_session: HumanClarificationSession,
    ) -> ToolInvocationOutcome:
        if session.origin_kind != "tool" or not session.tool_request_id:
            raise RuntimeError(
                "complete_human_question_interaction requires a tool-origin interaction session"
            )
        definition = context.tool_router.resolve("human_question")
        if definition is None:
            raise RuntimeError("human_question tool definition is unavailable")
        request_id = str(session.tool_request_id).strip()
        request = self._lookup_recorded_tool_call(
            context=context,
            request_id=request_id,
        )
        if request is None:
            request = ToolCall(
                request_id=request_id,
                tool_name="human_question",
                input={"questions": [item.to_dict() for item in session.questions]},
                reason=session.reason,
                stage=session.stage,
                attempt=session.attempt,
                query_chain_id=session.query_chain_id,
                query_chain_depth=session.query_chain_depth,
                continuation_index=session.continuation_index,
                requested_by="agent",
                created_at=session.created_at,
            )
        answer_entries: list[dict[str, Any]] = []
        for question in session.questions:
            answer_payload = normalize_human_answer_payload(
                answered_session.answers.get(question.question_id)
            )
            answer_entries.append(
                {
                    "interaction_request_id": answered_session.interaction_request_id,
                    "question_id": question.question_id,
                    "question_key": build_question_key(
                        answered_session.interaction_request_id, question.question_id
                    ),
                    "selected_labels": list(answer_payload.get("selected_labels", [])),
                    "free_text": answer_payload.get("free_text"),
                    "selected_preview": selected_preview_for_answer(
                        question=question,
                        answer_payload=answer_payload,
                    ),
                }
            )
        tool_result = ToolResult(
            ok=True,
            tool_name="human_question",
            data={
                "origin_kind": "tool",
                "interaction_request_id": answered_session.interaction_request_id,
                "questions": [item.to_dict() for item in session.questions],
                "answers": answer_entries,
            },
            metadata={
                "observation_id": f"tool_result:human_question:{request_id}",
                "interaction_session_id": answered_session.session_id,
                "origin_kind": "tool",
            },
            duration=0.0,
        )
        result_id = self._result_id_for_request(request_id)
        finalized_result = self._finalize_tool_result(
            tool_result=tool_result,
            request_id=request_id,
            result_id=result_id,
            stage=context.stage,
            attempt=context.attempt,
            query_chain_id=context.query_chain_id,
            query_chain_depth=context.query_chain_depth,
            continuation_index=context.continuation_index,
        )
        custom_result_summary = f"answered {len(answer_entries)} human_question item(s)"
        raw_result_hash = _stable_hash(finalized_result.to_dict())
        observable_input = _redacted_observable_input(
            definition=definition, input_payload=request.input
        )
        record = self._build_invocation_record(
            context=context,
            request_id=request_id,
            tool_name="human_question",
            status="completed",
            granted=True,
            interaction_session_id=answered_session.session_id,
            input_digest=_persistent_input_digest(
                definition=definition,
                input_payload=request.input,
            ),
            result_summary=custom_result_summary,
            result_ref=None,
            context_effects={
                "origin_kind": "tool",
                "interaction_request_id": answered_session.interaction_request_id,
                "question_keys": [item["question_key"] for item in answer_entries],
                "stage": context.stage.value,
                "attempt": context.attempt,
                "continuation_index": context.continuation_index,
                "custom_result_summary": custom_result_summary,
                "raw_result_hash": raw_result_hash,
            },
            created_at=answered_session.answered_at or now_utc_iso(),
            finished_at=answered_session.answered_at or now_utc_iso(),
            raw_result_hash=raw_result_hash,
            observable_input=observable_input,
        )
        self._record_tool_call_outcome(context=context, record=record)
        return ToolInvocationOutcome(
            request=request,
            record=record,
            tool_result=finalized_result,
            durable_result=finalized_result,
        )

    @staticmethod
    def _lookup_recorded_tool_call(
        *,
        context: ToolRuntimeContext,
        request_id: str,
    ) -> ToolCall | None:
        for item in reversed(context.state_store.state.tool_state.tool_calls):
            if item.request_id == request_id:
                return ToolCall.from_dict(item.to_dict())
        return None

    def _await_human_question_interaction(
        self,
        *,
        context: ToolRuntimeContext,
        request: ToolCall,
        definition: ToolDefinition,
        request_context_effects: dict[str, Any],
    ) -> ToolInvocationOutcome:
        execution_tier = context.state_store.state.workflow_state.execution_tier
        continuation_kind = (
            "direct_runtime"
            if execution_tier == ExecutionTier.DIRECT
            else "continuation_runtime"
        )
        critic_loop = context.state_store.state.feedback_state.critic_loop
        is_critic = context.actor_kind in {"reviewer"}
        created_at = now_utc_iso()
        session = HumanClarificationSession(
            session_id=f"human-question:{request.request_id}",
            stage=context.stage.value,
            attempt=context.attempt,
            query_chain_id=request.query_chain_id or context.query_chain_id,
            query_chain_depth=(
                request.query_chain_depth
                if int(request.query_chain_depth or 0) > 0
                else int(context.query_chain_depth or 0)
            ),
            continuation_index=context.continuation_index,
            actor_kind=context.actor_kind,
            actor_id=context.actor_id,
            conversation_lane=context.conversation_lane,
            critic_kind=critic_loop.critic_role if is_critic else "none",
            critic_round=critic_loop.round if is_critic else 0,
            critic_phase=critic_loop.phase if is_critic else "idle",
            continuation_kind=continuation_kind,
            status="pending",
            questions=tuple(
                HumanClarificationQuestion.from_wire_strict(
                    item,
                    question_index=index,
                    source=context.actor_kind,
                )
                for index, item in enumerate(
                    request.input.get("questions", []), start=1
                )
                if isinstance(item, dict)
            ),
            answers={},
            reason=request.reason or "human_question",
            source=context.actor_kind,
            origin_kind="tool",
            interaction_request_id=request.request_id,
            tool_name=definition.name,
            tool_request_id=request.request_id,
            created_at=created_at,
        )
        context.state_store.record_pending_human_clarification_session(session)
        summary = request.reason or f"awaiting user interaction for {definition.name}"
        record = self._build_invocation_record(
            context=context,
            request_id=request.request_id,
            tool_name=definition.name,
            status="awaiting_user_interaction",
            granted=True,
            interaction_session_id=session.session_id,
            awaiting_user_interaction=True,
            input_digest=_persistent_input_digest(
                definition=definition,
                input_payload=request.input,
            ),
            result_summary=summary,
            warning=summary,
            context_effects={
                "origin_kind": "tool",
                "interaction_request_id": session.interaction_request_id,
                **request_context_effects,
            },
            created_at=created_at,
            finished_at=created_at,
        )
        self._record_tool_call_outcome(context=context, record=record)
        context.state_store.record_tool_observation(
            ToolObservation(
                observation_id=f"human_question_request:{request.request_id}",
                source_type="human_question_request",
                source_name=definition.name,
                stage=context.stage.value,
                attempt=context.attempt,
                query_chain_id=context.query_chain_id,
                query_chain_depth=context.query_chain_depth,
                continuation_index=context.continuation_index,
                summary=summary,
                created_at=created_at,
            )
        )
        return ToolInvocationOutcome(
            request=request,
            record=record,
            pending_interaction_session=session,
        )


    def _build_execution_context(
        self,
        *,
        context: ToolRuntimeContext,
        dataset_path_override: str | None,
        environment_selection,
        execution_contract: ExecutionAttemptContract | None = None,
        exec_policy_decision: ExecPolicyDecision | None = None,
        exec_policy_hash_value: str = "",
        effective_permissions: EffectivePermissions | None = None,
        tool_call_id: str = "",
    ) -> ToolExecutionContext:
        contract_hash = (
            execution_contract_hash(execution_contract)
            if execution_contract is not None
            else ""
        )
        policy_hash = exec_policy_hash_value or (
            exec_policy_hash(exec_policy_decision)
            if exec_policy_decision is not None
            else ""
        )
        return ToolExecutionContext(
            raw_user_message=context.raw_user_message,
            task_spec_text=context.task_spec_text,
            project_root=context.project_root,
            stage=context.stage,
            attempt=context.attempt,
            continuation_index=context.continuation_index,
            run_id=str(
                getattr(context.state_store.state.run_metadata, "run_id", "") or ""
            ),
            tool_call_id=context.tool_call_id or tool_call_id,
            rollout_history=context.rollout_history,
            dataset_path_override=dataset_path_override,
            run_dir=context.run_dir,
            state_store=context.state_store,
            execution_runtime_service=context.execution_runtime_service,
            environment_config_snapshot=context.state_store.state.environment_state.config_snapshot,
            environment_descriptor=environment_selection.descriptor.to_dict(),
            sandbox_policy=environment_selection.sandbox_policy.to_dict(),
            environment_provider_kind=environment_selection.provider_kind,
            environment_selection_reason=environment_selection.selection_reason,
            environment_affinity=environment_selection.environment_affinity,
            execution_class=environment_selection.execution_class,
            user_config=context.user_config,
            actor_kind=context.actor_kind,
            actor_id=context.actor_id,
            process_owner_actor_id=context.process_owner_actor_id,
            execution_handle=context.execution_handle,
            active_remote_profile=context.active_remote_profile,
            cancel_checker=context.cancel_checker
            if callable(context.cancel_checker)
            else None,
            progress_callback=context.progress_callback
            if callable(context.progress_callback)
            else None,
            mcp_elicitation_handler=context.mcp_elicitation_handler
            if callable(context.mcp_elicitation_handler)
            else None,
            hook_engine=context.hook_engine or self._hook_engine,
            app_paths=getattr(context, "app_paths", None),
            skill_catalog_snapshot=context.skill_catalog_snapshot,
            process_owner_manager=context.process_owner_manager,
            subagent_owner=context.subagent_owner,
            execution_contract_id=execution_contract.contract_id
            if execution_contract is not None
            else "",
            execution_contract_hash=contract_hash,
            exec_policy_decision_id=exec_policy_decision.decision_id
            if exec_policy_decision is not None
            else "",
            exec_policy_hash=policy_hash,
            canonical_action_hash=(
                execution_contract.canonical_action_hash
                if execution_contract is not None
                else ""
            ),
            canonical_effect_hash=(
                execution_contract.canonical_effect_hash
                if execution_contract is not None
                else ""
            ),
            effective_permissions=effective_permissions
            or context.canonical_permissions,
            runtime_text_resource_records=build_runtime_text_resource_records(context),
        )

    @staticmethod
    def _resolve_execution_environment(
        *,
        context: ToolRuntimeContext,
        definition: ToolDefinition,
        input: dict[str, Any],
        destructive_action: bool,
        effective_permissions: EffectivePermissions | None = None,
    ):
        config_snapshot = context.state_store.state.environment_state.config_snapshot
        resolver = EnvironmentResolver(
            config_snapshot=config_snapshot
            if isinstance(config_snapshot, dict)
            else None
        )
        return resolver.resolve_tool_environment(
            project_root=context.project_root,
            run_dir=context.run_dir,
            stage=context.stage.value,
            tool_summary=definition.to_summary(),
            input=input,
            effective_permissions=effective_permissions
            or context.canonical_permissions,
            destructive_action=destructive_action,
            extra_readable_roots=_active_skill_resource_roots(context),
        )

    @staticmethod
    def _materialize_environment_receipt(
        *,
        context: ToolRuntimeContext,
        definition: ToolDefinition,
        tool_result: ToolResult,
        environment_selection,
        execution_contract: ExecutionAttemptContract | None = None,
        execution_contract_hash_value: str = "",
    ) -> EnvironmentExecutionReceipt:
        metadata = (
            tool_result.metadata if isinstance(tool_result.metadata, dict) else {}
        )
        contract_metadata = _execution_contract_receipt_metadata(
            execution_contract=execution_contract,
            execution_contract_hash_value=execution_contract_hash_value,
        )
        data = tool_result.data if isinstance(tool_result.data, dict) else {}
        receipt_id = str(
            data.get("environment_receipt_id")
            or metadata.get("environment_receipt_id")
            or ""
        ).strip()
        if receipt_id:
            for current in reversed(
                context.state_store.state.environment_state.latest_environment_receipts
            ):
                if str(current.get("receipt_id", "") or "").strip() == receipt_id:
                    receipt = EnvironmentExecutionReceipt.from_dict(current)
                    return replace(
                        receipt,
                        provider_metadata={
                            **dict(receipt.provider_metadata),
                            **contract_metadata,
                        },
                    )
        request_id = str(metadata.get("request_id") or "").strip()
        if not request_id:
            raise RuntimeError(
                "Environment receipt materialization requires a tool request_id"
            )
        duration_ms = int((tool_result.duration or 0.0) * 1000)
        status = "completed" if tool_result.ok else "failed"
        invocation_identity = (
            f'{context.conversation_exchange_id}:{context.output_sequence_index}'
            if context.conversation_exchange_id and context.output_sequence_index is not None
            else request_id
        )
        provider_metadata = {
            "credential_mode": definition.to_summary().credential_mode,
            "backend_kind": definition.to_summary().backend_kind,
            "receipt_only": True,
            **contract_metadata,
        }
        return EnvironmentExecutionReceipt(
            receipt_id=(
                f"environment:{definition.name}:{context.stage.value}:"
                f"{context.attempt}:{context.continuation_index}:{invocation_identity}"
            ),
            provider_kind=environment_selection.provider_kind,
            request_kind="host_managed"
            if definition.to_summary().backend_kind in {"browser", "mcp"}
            else "tool_runtime",
            tool_name=definition.name,
            status=status,
            descriptor=environment_selection.descriptor,
            sandbox_policy=environment_selection.sandbox_policy,
            selection_reason=environment_selection.selection_reason,
            environment_affinity=environment_selection.environment_affinity,
            execution_class=environment_selection.execution_class,
            session_id=None,
            command_display="",
            started_at=metadata.get("created_at")
            if isinstance(metadata.get("created_at"), str)
            else None,
            heartbeat_at=metadata.get("created_at")
            if isinstance(metadata.get("created_at"), str)
            else None,
            completed_at=metadata.get("created_at")
            if isinstance(metadata.get("created_at"), str)
            else now_utc_iso(),
            elapsed_ms=max(0, duration_ms),
            exit_code=tool_result.exit_code,
            heartbeat_count=1 if tool_result.ok or tool_result.error else 0,
            stdout_preview=str(tool_result.stdout or ""),
            stderr_preview=str(tool_result.stderr or ""),
            stdout_truncated=False,
            stderr_truncated=False,
            timeout_class=None,
            pty_mode="pipe",
            network_mode=(
                "full" if environment_selection.sandbox_policy.allow_network else "none"
            ),
            writable_roots=environment_selection.sandbox_policy.writable_host_roots,
            artifact_refs=tuple(
                str(item.file_path)
                for item in tool_result.artifacts
                if getattr(item, "file_path", None)
            ),
            provider_metadata=provider_metadata,
        )

    @staticmethod
    def _compile_execution_contract(
        *,
        context: ToolRuntimeContext,
        definition: ToolDefinition,
        request: ToolCall,
        permission_decision: ToolPermissionDecision,
        approved_permission_session: ToolPermissionSession | None,
        environment_selection,
        requested_at: str,
        effective_permissions: EffectivePermissions | None = None,
    ) -> tuple[ExecutionAttemptContract, ExecPolicyDecision, str]:
        approval = _approval_contract_from_decision(
            permission_decision=permission_decision,
            approved_permission_session=approved_permission_session,
        )
        preliminary_contract = build_execution_attempt_contract(
            request_id=request.request_id,
            tool_name=definition.name,
            input_payload=dict(request.input),
            requested_at=requested_at,
            tool_summary=definition.to_summary(),
            permission_decision=permission_decision,
            approval=approval,
            environment_selection=environment_selection,
            provider_config=context.state_store.state.environment_state.config_snapshot
            if isinstance(
                context.state_store.state.environment_state.config_snapshot, dict
            )
            else {},
            effective_permissions=effective_permissions
            or context.canonical_permissions,
        )
        policy_decision = compile_exec_policy(
            request_id=request.request_id,
            tool_name=definition.name,
            permission_decision=permission_decision,
            approval=approval,
            environment_selection=environment_selection,
            enforcement_expectations=preliminary_contract.enforcement_expectations,
            blocked_policy_reasons=preliminary_contract.blocked_policy_reasons,
        )
        policy_hash_value = exec_policy_hash(policy_decision)
        contract = build_execution_attempt_contract(
            request_id=request.request_id,
            tool_name=definition.name,
            input_payload=dict(request.input),
            requested_at=requested_at,
            tool_summary=definition.to_summary(),
            permission_decision=permission_decision,
            approval=approval,
            environment_selection=environment_selection,
            provider_config=context.state_store.state.environment_state.config_snapshot
            if isinstance(
                context.state_store.state.environment_state.config_snapshot, dict
            )
            else {},
            exec_policy_decision=policy_decision,
            exec_policy_hash_value=policy_hash_value,
            effective_permissions=effective_permissions
            or context.canonical_permissions,
        )
        return contract, policy_decision, policy_hash_value

    @staticmethod
    def _attach_environment_receipt(
        *,
        tool_result: ToolResult,
        receipt: EnvironmentExecutionReceipt,
        receipt_view: dict[str, Any],
    ) -> ToolResult:
        metadata = (
            dict(tool_result.metadata) if isinstance(tool_result.metadata, dict) else {}
        )
        metadata.pop("environment_receipt", None)
        metadata["environment_receipt_id"] = receipt.receipt_id
        receipt_ref = receipt_view.get("durable_receipt_ref")
        if isinstance(receipt_ref, dict) and receipt_ref:
            metadata["environment_receipt_ref"] = dict(receipt_ref)
        else:
            metadata.pop("environment_receipt_ref", None)
        metadata["environment_provider_kind"] = receipt.provider_kind
        metadata["environment_selection_reason"] = receipt.selection_reason
        data = dict(tool_result.data) if isinstance(tool_result.data, dict) else {}
        data.pop("environment_receipt", None)
        if memory_consumption_receipt_from_metadata(metadata) is None:
            data.setdefault("provider_kind", receipt.provider_kind)
            data.setdefault("environment_receipt_id", receipt.receipt_id)
            data.setdefault("environment_selection_reason", receipt.selection_reason)
        return replace(tool_result, metadata=metadata, data=data)

    @staticmethod
    def _materialize_tool_call(
        *,
        context: ToolRuntimeContext,
        request: ToolCall,
    ) -> ToolCall:
        materialized = request
        if materialized.request_id:
            return materialized
        return replace(
            materialized,
            request_id=ToolRuntime._next_request_id(
                context.state_store, prefix="invoke"
            ),
            stage=materialized.stage or context.stage.value,
            attempt=materialized.attempt or context.attempt,
            query_chain_id=materialized.query_chain_id or context.query_chain_id,
            query_chain_depth=(
                materialized.query_chain_depth
                if int(materialized.query_chain_depth or 0) > 0
                else int(context.query_chain_depth or 0)
            ),
            continuation_index=materialized.continuation_index
            or context.continuation_index,
            requested_by=materialized.requested_by or context.requested_by,
            created_at=materialized.created_at or now_utc_iso(),
        )

    async def _recover_after_tool_validation_error(
        self,
        *,
        context: ToolRuntimeContext,
        original_request: ToolCall,
        original_definition: ToolDefinition,
        validation_feedback: dict[str, Any],
        record_request: bool,
        recovery_attempted: bool,
        execution_protocol: ToolExecutionProtocol,
    ) -> ToolInvocationOutcome | None:
        if recovery_attempted:
            return None
        payload = validation_feedback.get("tool_call_recovery")
        if not isinstance(payload, dict):
            return None
        if (
            str(payload.get("policy", "") or "").strip()
            != "auto_correct_single_safe_candidate"
        ):
            return None
        target_tool = str(payload.get("tool_name", "") or "").strip()
        if (
            not target_tool
            or target_tool.casefold() != original_definition.name.casefold()
        ):
            return None
        target_definition = self._resolve_eligible_definition(context, target_tool)
        if target_definition is None:
            return None
        original_summary = original_definition.to_summary()
        target_summary = target_definition.to_summary()
        if (
            str(original_summary.toolset_family or "").strip()
            != str(target_summary.toolset_family or "").strip()
        ):
            return None
        if str(payload.get("confidence", "") or "").strip() not in {
            "high",
            "deterministic",
        }:
            return None
        target_input = _build_recovered_tool_input(
            original_input=original_request.input,
            recovery_payload=payload,
        )
        if target_input is None:
            return None
        target_assessment = _assess_destructive_action(
            definition=target_definition,
            input=target_input,
            project_root=context.project_root,
        )
        if (
            target_assessment.is_destructive
            or _recovered_input_requests_destructive_behavior(target_input)
        ):
            return None
        recovered_request = replace(
            original_request,
            request_id=f"{original_request.request_id}:recovered:{target_definition.name}",
            tool_name=target_definition.name,
            input=target_input,
            reason=(
                str(payload.get("reason", "") or "").strip()
                or f"Runtime corrected {original_definition.name} to {target_definition.name} after validation."
            ),
            expected_output=(
                str(payload.get("expected_output", "") or "").strip()
                or original_request.expected_output
            ),
        )
        context.state_store.record_tool_observation(
            ToolObservation(
                observation_id=f"tool_call_recovery:{original_request.request_id}",
                source_type="tool_call_recovery",
                source_name=target_definition.name,
                stage=context.stage.value,
                attempt=context.attempt,
                query_chain_id=context.query_chain_id,
                query_chain_depth=context.query_chain_depth,
                continuation_index=context.continuation_index,
                summary=(
                    f"Runtime corrected tool call {original_definition.name} -> {target_definition.name} "
                    f"after validation_code={validation_feedback.get('validation_code') or '<none>'}; "
                    f"policy={payload.get('policy') or '<none>'}; confidence={payload.get('confidence') or '<none>'}."
                ),
                created_at=now_utc_iso(),
            )
        )
        return await self.invoke_tool(
            context=context,
            request=recovered_request,
            record_request=record_request,
            tool_call_recovery_attempted=True,
            execution_protocol=execution_protocol,
        )

    def _preflight_validation_outcome(
        self,
        *,
        context: ToolRuntimeContext,
        request: ToolCall,
        definition: ToolDefinition,
        error: ToolPreflightValidationError,
        request_context_effects: dict[str, Any],
    ) -> ToolInvocationOutcome:
        compact_hint = error.compact_hint or str(error)
        context_effects = {
            "schema_validation_hint": compact_hint,
            "validation_source": "effect_resolution",
            "validation_code": error.validation_code,
            **(
                {"validation_field": error.field_name}
                if error.field_name
                else {}
            ),
            **request_context_effects,
        }
        record = self._build_invocation_record(
            context=context,
            request_id=request.request_id,
            tool_name=definition.name,
            status="tool_validation_error",
            granted=False,
            input_digest=_persistent_input_digest(
                definition=definition,
                input_payload=request.input,
            ),
            validation_code=error.validation_code,
            result_summary=compact_hint,
            warning=compact_hint,
            error=str(error),
            context_effects=context_effects,
            created_at=now_utc_iso(),
            finished_at=now_utc_iso(),
        )
        self._record_tool_call_outcome(context=context, record=record)
        context.state_store.record_tool_observation(
            ToolObservation(
                observation_id=f"tool_preflight_validation:{request.request_id}",
                source_type="tool_preflight_validation",
                source_name=definition.name,
                stage=context.stage.value,
                attempt=context.attempt,
                query_chain_id=context.query_chain_id,
                query_chain_depth=context.query_chain_depth,
                continuation_index=context.continuation_index,
                summary=compact_hint,
                created_at=now_utc_iso(),
            )
        )
        return ToolInvocationOutcome(request=request, record=record)

    def _protocol_violation_outcome(
        self,
        *,
        context: ToolRuntimeContext,
        request: ToolCall,
        violation_kind: str,
        message: str,
        warning: str | None = None,
        input_payload: dict[str, Any] | None = None,
        context_effects: dict[str, Any] | None = None,
    ) -> ToolInvocationOutcome:
        requested_definition = self._requested_definition(
            context,
            request.tool_name,
            requested_by=request.requested_by,
        )
        persistent_input = _persistent_input_payload(
            definition=requested_definition,
            input_payload=(
                dict(input_payload)
                if isinstance(input_payload, dict)
                else dict(request.input)
            ),
        )
        violation_definition = ToolDefinition(
            exposure=ToolExposure.HIDDEN,
            name=request.tool_name,
                                   description="protocol violation placeholder",
                                   permission_class="protocol",
                                   backend_kind="generic",
                                   execution_class="read_only",

            handler=None,
        )
        violation_contract, violation_policy_decision, violation_policy_hash = (
            self._compile_terminal_failure_contract(
                context=context,
                request=request,
                definition=violation_definition,
                permission_class="protocol",
                behavior="deny",
                granted=False,
                reason=message,
                destructive_assessment=None,
                environment_selection=self._resolve_execution_environment(
                    context=context,
                    definition=violation_definition,
                    input=request.input,
                    destructive_action=False,
                ),
                requested_at=now_utc_iso(),
            )
        )
        violation_contract_hash = execution_contract_hash(violation_contract)
        context.state_store.record_execution_contract_compiled(
            violation_contract,
            status=violation_contract.status,
            stage=context.stage.value,
            attempt=context.attempt,
            query_chain_id=context.query_chain_id,
            query_chain_depth=context.query_chain_depth,
            continuation_index=context.continuation_index,
            request_id=request.request_id,
            tool_name=request.tool_name,
            contract_hash=violation_contract_hash,
            exec_policy_decision=violation_policy_decision,
            exec_policy_hash_value=violation_policy_hash,
        )
        violation = ToolProtocolViolationRecord(
            violation_id=f"protocol:{request.request_id}:{violation_kind}",
            violation_kind=violation_kind,
            message=message,
            tool_name=request.tool_name,
            request_id=request.request_id,
            stage=context.stage.value,
            attempt=context.attempt,
            query_chain_id=context.query_chain_id,
            query_chain_depth=context.query_chain_depth,
            continuation_index=context.continuation_index,
            input_payload=persistent_input,
            created_at=now_utc_iso(),
        )
        context.state_store.record_tool_protocol_violation(violation)
        record = self._build_invocation_record(
            context=context,
            request_id=request.request_id,
            tool_name=request.tool_name,
            status="tool_protocol_violation",
            granted=False,
            input_digest=_input_digest(persistent_input),
            warning=warning or message,
            error=message,
            created_at=violation.created_at,
            finished_at=violation.created_at,
            context_effects={
                "violation_kind": violation_kind,
                "protocol_only": True,
                "execution_contract_id": violation_contract.contract_id,
                "execution_contract_hash": violation_contract_hash,
                "exec_policy_decision_id": violation_policy_decision.decision_id,
                "exec_policy_hash": violation_policy_hash,
                **(dict(context_effects) if isinstance(context_effects, dict) else {}),
            },
        )
        self._record_tool_call_outcome(context=context, record=record)
        context.state_store.record_tool_observation(
            ToolObservation(
                observation_id=f"tool_protocol_violation:{request.request_id}",
                source_type="tool_protocol_violation",
                source_name=request.tool_name,
                stage=context.stage.value,
                attempt=context.attempt,
                query_chain_id=context.query_chain_id,
                query_chain_depth=context.query_chain_depth,
                continuation_index=context.continuation_index,
                summary=warning or message,
                created_at=violation.created_at,
            )
        )
        return ToolInvocationOutcome(request=request, record=record)

    def _compile_terminal_failure_contract(
        self,
        *,
        context: ToolRuntimeContext,
        request: ToolCall,
        definition: ToolDefinition,
        permission_class: str,
        behavior: str,
        granted: bool,
        reason: str,
        destructive_assessment: DestructiveActionAssessment | None,
        environment_selection,
        requested_at: str,
        approved_permission_session: ToolPermissionSession | None = None,
    ) -> tuple[ExecutionAttemptContract, ExecPolicyDecision, str]:
        assessment = destructive_assessment or DestructiveActionAssessment(
            is_destructive=False,
            effective_permission_class=permission_class,
        )
        permission_decision = ToolPermissionDecision(
            behavior=behavior,
            granted=granted,
            reason=reason,
            tool_name=definition.name,
            permission_class=permission_class,
            base_permission_class=permission_class,
            destructive_action=assessment.is_destructive,
            destructive_reason=assessment.reason,
            destructive_kinds=assessment.kinds,
            permission_matcher_kind="default",
        )
        return self._compile_execution_contract(
            context=context,
            definition=definition,
            request=request,
            permission_decision=permission_decision,
            approved_permission_session=approved_permission_session,
            environment_selection=environment_selection,
            requested_at=requested_at,
        )

    @staticmethod
    def _next_request_id(state_store: WorkflowStateStore, *, prefix: str) -> str:
        state_store.state.tool_state.tool_round_counter += 1
        return f"{prefix}:{state_store.state.tool_state.tool_round_counter}"

    def _request_is_concurrency_safe(
        self,
        *,
        context: ToolRuntimeContext,
        request: ToolCall,
    ) -> bool:
        definition = self._resolve_eligible_definition(context, request.tool_name)
        return bool(definition is not None and definition.is_concurrency_safe)

    @staticmethod
    def _finalize_tool_result(
        *,
        tool_result: ToolResult,
        request_id: str,
        result_id: str,
        stage: Stage,
        attempt: int,
        query_chain_id: str,
        query_chain_depth: int,
        continuation_index: int,
    ) -> ToolResult:
        metadata = {
            **tool_result.metadata,
            "request_id": request_id,
            "result_id": result_id,
            "stage": stage.value,
            "attempt": attempt,
            "query_chain_id": str(query_chain_id or "").strip(),
            "query_chain_depth": int(query_chain_depth or 0),
            "continuation_index": continuation_index,
        }
        artifacts = list(tool_result.artifacts)
        return replace(
            tool_result,
            metadata=metadata,
            artifacts=artifacts,
        )

    @staticmethod
    def _result_id_for_request(request_id: str) -> str:
        return f"result:{request_id}"

    def _requested_definition(
        self,
        context: ToolRuntimeContext,
        tool_name: str,
        *,
        requested_by: str | None = None,
    ) -> ToolDefinition | None:
        del requested_by
        return context.tool_router.resolve(tool_name)

    def _resolve_eligible_definition(
        self,
        context: ToolRuntimeContext,
        tool_name: str,
    ) -> ToolDefinition | None:
        return context.tool_router.resolve(tool_name)

    def _tool_permission_decision(
        self,
        *,
        context: ToolRuntimeContext,
        definition: ToolDefinition,
        requested_by: str,
        destructive_assessment: DestructiveActionAssessment | None = None,
        input_payload: dict[str, Any] | None = None,
        environment_selection: Any | None = None,
        request_id: str = "",
    ) -> ToolPermissionDecision:
        if input_payload is None:
            return ToolPermissionDecision(
                behavior="allow",
                granted=True,
                reason="tool visibility does not grant execution permission",
                tool_name=definition.name,
                permission_class=definition.summary.permission_class,
                base_permission_class=definition.summary.permission_class,
            )
        decision, _evaluation = self._canonical_permission_decision(
            context=context,
            definition=definition,
            requested_by=requested_by,
            destructive_assessment=destructive_assessment,
            input_payload=input_payload,
            environment_selection=environment_selection,
            request_id=request_id,
        )
        return decision

    def _canonical_permission_decision(
        self,
        *,
        context: ToolRuntimeContext,
        definition: ToolDefinition,
        requested_by: str,
        destructive_assessment: DestructiveActionAssessment | None,
        input_payload: dict[str, Any],
        environment_selection: Any | None,
        request_id: str,
    ) -> tuple[ToolPermissionDecision, PermissionEvaluation]:
        del requested_by
        assessment = destructive_assessment or DestructiveActionAssessment(
            effective_permission_class=definition.summary.permission_class
        )
        effective_permission_class = (
            assessment.effective_permission_class or definition.summary.permission_class
        )
        environment_payload = (
            environment_selection.to_dict()
            if environment_selection is not None
            and hasattr(environment_selection, "to_dict")
            else {}
        )
        action = compile_canonical_action(
            request_id=request_id or f"permission:{definition.name}",
            tool_name=definition.name,
            permission_class=effective_permission_class,
            input_payload=input_payload,
            environment=environment_payload,
            destructive=assessment.is_destructive,
            destructive_reason=assessment.reason,
            declared_effects=definition.resolve_declared_effects(
                context, input_payload
            ),
        )
        evaluation = PermissionOrchestrator().evaluate(
            action.operation,
            effective_permissions=context.canonical_permissions,
        )
        behavior = (
            "allow"
            if evaluation.allowed
            else ("ask" if evaluation.approval_request else "deny")
        )
        reason = (
            "allowed by canonical permission profile"
            if evaluation.allowed
            else (
                evaluation.approval_request.reason
                if evaluation.approval_request is not None
                else evaluation.denial_reason
            )
        )
        return (
            ToolPermissionDecision(
                behavior=behavior,
                granted=evaluation.allowed,
                reason=reason,
                tool_name=definition.name,
                permission_class=effective_permission_class,
                base_permission_class=definition.summary.permission_class,
                destructive_action=assessment.is_destructive,
                destructive_reason=assessment.reason,
                destructive_kinds=assessment.kinds,
                human_prompt=(
                    f"Allow {definition.name}: {reason}?"
                    if evaluation.approval_request is not None
                    else ""
                ),
                permission_matcher_kind="canonical_profile",
                canonical_action_hash=action.action_hash,
                canonical_effect_hash=action.effects.effect_hash,
                base_policy_hash=evaluation.effective_permissions.policy_hash,
            ),
            evaluation,
        )

    @staticmethod
    def _build_permission_session(
        *,
        context: ToolRuntimeContext,
        request: ToolCall,
        definition: ToolDefinition,
        permission_decision: ToolPermissionDecision,
        approval_request: PermissionRequest | None,
    ) -> ToolPermissionSession:
        if approval_request is None:
            raise ValueError("approval session requires a canonical permission request")
        execution_tier = context.state_store.state.workflow_state.execution_tier
        default_continuation_kind = (
            "direct_runtime"
            if execution_tier == ExecutionTier.DIRECT
            else "continuation_runtime"
        )
        continuation_kind = str(permission_decision.continuation_kind or "").strip()
        if (
            execution_tier == ExecutionTier.DIRECT
            and continuation_kind == "continuation_runtime"
        ):
            continuation_kind = "direct_runtime"
        if not continuation_kind:
            continuation_kind = default_continuation_kind
        critic_loop = context.state_store.state.feedback_state.critic_loop
        is_critic = context.actor_kind in {"reviewer"}
        return ToolPermissionSession(
            session_id=(
                f"tool-permission:{context.conversation_exchange_id}:{context.output_sequence_index}"
                if context.conversation_exchange_id and context.output_sequence_index is not None
                else f"tool-permission:{request.request_id}"
            ),
            request_id=request.request_id,
            tool_name=definition.name,
            permission_class=(
                permission_decision.permission_class
                or definition.summary.permission_class
            ),
            stage=context.stage.value,
            attempt=context.attempt,
            query_chain_id=(request.query_chain_id or context.query_chain_id),
            query_chain_depth=(
                request.query_chain_depth
                if int(request.query_chain_depth or 0) > 0
                else int(context.query_chain_depth or 0)
            ),
            continuation_index=context.continuation_index,
            actor_kind=context.actor_kind,
            actor_id=context.actor_id,
            conversation_lane=context.conversation_lane,
            conversation_exchange_id=context.conversation_exchange_id,
            output_sequence_index=context.output_sequence_index,
            critic_kind=critic_loop.critic_role if is_critic else "none",
            critic_round=critic_loop.round if is_critic else 0,
            critic_phase=critic_loop.phase if is_critic else "idle",
            base_permission_class=(
                permission_decision.base_permission_class
                or definition.summary.permission_class
            ),
            destructive_action=permission_decision.destructive_action,
            destructive_reason=permission_decision.destructive_reason,
            destructive_kinds=permission_decision.destructive_kinds,
            requested_by=request.requested_by,
            continuation_kind=continuation_kind,
            status="pending",
            decision=None,
            human_prompt=permission_decision.human_prompt
            or f"Allow tool {definition.name}?",
            reason=permission_decision.reason,
            input_digest=_persistent_input_digest(
                definition=definition,
                input_payload=request.input,
            ),
            approval_fingerprint=approval_fingerprint(approval_request),
            base_policy_hash=approval_request.base_policy_hash,
            reviewer=context.canonical_permissions.approvals_reviewer.value,
            approval_request=approval_request.to_dict(),
            request_payload=_redacted_persistent_tool_call(
                definition=definition,
                request=request,
            ).to_dict(),
            created_at=now_utc_iso(),
        )


    @staticmethod
    def _matching_session_grant(
        *,
        context: ToolRuntimeContext,
        request: PermissionRequest,
    ) -> SessionPermissionGrant | None:
        run_metadata = context.state_store.state.run_metadata
        task_id = str(run_metadata.task_id or "task")
        patch_covered: set[str] = set()
        patch_entries = []
        patch_network_enabled = None
        for grant in reversed(
            context.state_store.state.tool_state.session_permission_grants
        ):
            if grant.run_id != str(run_metadata.run_id or ""):
                continue
            if grant.runtime_session_id != str(run_metadata.session_id or ""):
                continue
            if grant.task_id != task_id:
                continue
            if grant.tool_name != request.tool_name or grant.action != request.action:
                continue
            if grant.environment_hash != request.environment_hash:
                continue
            if grant.base_policy_hash != request.base_policy_hash:
                continue
            if grant.reviewer != context.canonical_permissions.approvals_reviewer.value:
                continue
            if request.tool_name == 'patch_apply':
                if (
                    not valid_session_identity(grant) or not valid_session_identity(request)
                    or grant.session_match_kind != 'patch_path_exact'
                    or request.session_match_kind != 'patch_path_exact'
                    or not request.session_match_components
                    or grant.session_match_digest not in request.session_match_components
                ):
                    continue
            elif request.tool_name == "exec_command":
                if not valid_session_identity(grant) or not valid_session_identity(request):
                    continue
                if (
                    grant.session_match_kind != request.session_match_kind
                    or grant.session_match_version != request.session_match_version
                    or grant.session_match_digest != request.session_match_digest
                ):
                    continue
                if grant.session_match_kind == "exec_command_prefix":
                    if not grant.command_prefix or grant.command_prefix != request.command_prefix:
                        continue
                elif grant.command_prefix:
                    continue
            elif request.tool_name == 'mcp_tool_invoke' or request.tool_name.startswith('mcp__'):
                if not valid_session_identity(grant) or not valid_session_identity(request):
                    continue
                if (
                    grant.session_match_kind != request.session_match_kind
                    or grant.session_match_version != request.session_match_version
                    or grant.session_match_digest != request.session_match_digest
                ):
                    continue
            elif grant.session_match_kind:
                continue
            if (
                grant.exact_input_hash is not None
                and grant.exact_input_hash != request.normalized_input_hash
            ):
                continue
            if grant.command_prefix and grant.command_prefix != request.command_prefix:
                continue
            granted_overlay = PermissionRequest.from_dict(
                {
                    **request.to_dict(),
                    "requested_overlay": grant.approved_overlay,
                }
            ).requested_overlay
            if request.tool_name == 'patch_apply':
                patch_covered.add(grant.session_match_digest)
                patch_entries.extend(granted_overlay.file_system_entries)
                if granted_overlay.network_enabled is True:
                    patch_network_enabled = True
                combined = replace(
                    granted_overlay,
                    file_system_entries=tuple(dict.fromkeys(patch_entries)),
                    network_enabled=patch_network_enabled,
                )
                if (
                    set(request.session_match_components) <= patch_covered
                    and additional_permissions_cover(combined, request.requested_overlay)
                ):
                    # Return only the overlay for this request, not the union of
                    # historical grants. The caller still issues a fresh receipt.
                    return replace(grant, approved_overlay=request.to_dict()['requested_overlay'])
                continue
            if additional_permissions_cover(granted_overlay, request.requested_overlay):
                return grant
        return None

    @staticmethod
    def _dataset_path_override(
        *,
        context: ToolRuntimeContext,
        tool_name: str,
    ) -> str | None:
        if tool_name != "pdf_reader":
            return None
        return ToolRuntime._effective_dataset_path_override(context)

    @staticmethod
    def _effective_dataset_path_override(context: ToolRuntimeContext) -> str | None:
        fallback = ""
        effective = resolve_clarified_path_for_scope(
            pending_session=context.state_store.current_human_clarification_session(),
            history=context.state_store.state.workflow_state.human_clarification_history,
            stage=context.stage.value,
            attempt=context.attempt,
            fallback_path=fallback,
            candidate_question_ids=(
                "dataset_path",
                "file_path",
                "pdf_path",
                "confirm_or_provide_demo_dataset",
                "dataset_path_or_retarget",
            ),
            allow_previous_attempt=True,
        )
        if not effective:
            return None
        return effective

    @staticmethod
    def _build_invocation_record(
        *,
        context: ToolRuntimeContext,
        request_id: str,
        tool_name: str,
        status: str,
        granted: bool,
        permission_session_id: str | None = None,
        interaction_session_id: str | None = None,
        awaiting_user_interaction: bool = False,
        input_digest: str = "",
        validation_code: str | None = None,
        permission_class: str | None = None,
        base_permission_class: str | None = None,
        destructive_action: bool = False,
        destructive_reason: str = "",
        destructive_kinds: tuple[str, ...] = (),
        result_summary: str = "",
        result_ref: str | None = None,
        environment_provider_kind: str | None = None,
        environment_selection_reason: str = "",
        environment_receipt_id: str | None = None,
        warning: str | None = None,
        error: str | None = None,
        duration: float | None = None,
        context_effects: dict[str, Any] | None = None,
        created_at: str | None = None,
        finished_at: str | None = None,
        invocation_receipt_id: str | None = None,
        permission_decision_id: str | None = None,
        projection_id: str | None = None,
        raw_result_hash: str = "",
        output_hash: str = "",
        observable_input: dict[str, Any] | None = None,
    ) -> ToolInvocationRecord:
        return ToolInvocationRecord(
            request_id=request_id,
            tool_name=tool_name,
            status=status,
            granted=granted,
            stage=context.stage.value,
            attempt=context.attempt,
            query_chain_id=context.query_chain_id,
            query_chain_depth=context.query_chain_depth,
            continuation_index=context.continuation_index,
            permission_session_id=permission_session_id,
            interaction_session_id=interaction_session_id,
            awaiting_user_interaction=awaiting_user_interaction,
            input_digest=input_digest,
            validation_code=validation_code,
            permission_class=permission_class,
            base_permission_class=base_permission_class,
            destructive_action=destructive_action,
            destructive_reason=destructive_reason,
            destructive_kinds=destructive_kinds,
            result_summary=result_summary,
            result_ref=result_ref,
            environment_provider_kind=environment_provider_kind,
            environment_selection_reason=environment_selection_reason,
            environment_receipt_id=environment_receipt_id,
            warning=warning,
            error=error,
            duration=duration,
            context_effects=dict(context_effects)
            if isinstance(context_effects, dict)
            else {},
            created_at=created_at,
            finished_at=finished_at,
            invocation_receipt_id=invocation_receipt_id,
            permission_decision_id=permission_decision_id,
            projection_id=projection_id,
            raw_result_hash=raw_result_hash,
            output_hash=output_hash,
            observable_input=dict(observable_input)
            if isinstance(observable_input, dict)
            else {},
        )


def _input_digest(input_payload: dict[str, Any]) -> str:
    if not input_payload:
        return "{}"
    parts = [
        f"{key}={value}"
        for key, value in sorted(input_payload.items(), key=lambda item: item[0])
    ]
    return ", ".join(parts)


def _stable_hash(payload: Any) -> str:
    try:
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
    except TypeError:
        serialized = str(payload)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _runtime_text_resource_state_result(
    *,
    tool_result: ToolResult,
    grant: Any,
    receipt: dict[str, Any] | None,
) -> ToolResult:
    data: dict[str, Any] = {
        "resource_kind": str(grant.resource_kind),
        "relative_path": grant.path.name,
        "content_persisted": False,
    }
    if receipt is not None:
        data["runtime_text_resource_receipt"] = dict(receipt)
    status = "updated" if receipt is not None and receipt.get("changed") else "observed"
    return replace(
        tool_result,
        data=data,
        stdout=f"runtime-owned resource {grant.resource_kind} {status}",
        stderr="",
        content_items=(),
    )


def _scope_payload(context: ToolRuntimeContext) -> dict[str, Any]:
    return {
        "stage": context.stage.value,
        "attempt": context.attempt,
        "actor_kind": context.actor_kind,
        "actor_id": context.actor_id,
        "query_chain_id": context.query_chain_id,
        "query_chain_depth": context.query_chain_depth,
        "continuation_index": context.continuation_index,
    }


_SECRET_INPUT_NAME_FRAGMENTS = (
    "token",
    "secret",
    "password",
    "credential",
    "api_key",
    "apikey",
    "private_key",
)
_REDACTED_INPUT_PREFIX = "[REDACTED sha256="


def _redacted_observable_input(
    *, definition: ToolDefinition, input_payload: dict[str, Any]
) -> dict[str, Any]:
    summary = definition.to_summary()
    secret_fields = {item.casefold() for item in summary.secret_input_fields}
    observable_fields = {
        str(item) for item in summary.observable_input_fields if str(item).strip()
    }
    if observable_fields:
        source_items = {
            key: input_payload.get(key)
            for key in observable_fields
            if key in input_payload
        }
    else:
        source_items = {
            str(key): value
            for key, value in input_payload.items()
            if isinstance(value, (str, int, float, bool)) or value is None
        }
    redacted: dict[str, Any] = {}
    for key, value in source_items.items():
        lowered = str(key).casefold()
        if lowered in secret_fields or any(
            fragment in lowered for fragment in _SECRET_INPUT_NAME_FRAGMENTS
        ):
            redacted[str(key)] = "[REDACTED]"
        else:
            redacted[str(key)] = value
    return redacted


def _redacted_persistent_tool_call(
    *,
    definition: ToolDefinition | None,
    request: ToolCall,
) -> ToolCall:
    return replace(
        request,
        input=_persistent_input_payload(
            definition=definition,
            input_payload=request.input,
        ),
    )


def _persistent_input_digest(
    *,
    definition: ToolDefinition | None,
    input_payload: dict[str, Any],
) -> str:
    return _input_digest(
        _persistent_input_payload(
            definition=definition,
            input_payload=input_payload,
        )
    )


def _persistent_input_payload(
    *,
    definition: ToolDefinition | None,
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    secret_fields = (
        {
            str(item).casefold()
            for item in definition.to_summary().secret_input_fields
            if str(item).strip()
        }
        if definition is not None
        else set()
    )
    return {
        str(key): '[REDACTED]' if str(key).casefold() == 'env_overrides' else _persistent_input_value(
            key=str(key),
            value=value,
            force_redact=str(key).casefold() in secret_fields,
        )
        for key, value in input_payload.items()
    }


def _persistent_input_value(
    *,
    key: str,
    value: Any,
    force_redact: bool = False,
) -> Any:
    lowered = key.casefold()
    if force_redact or any(
        fragment in lowered for fragment in _SECRET_INPUT_NAME_FRAGMENTS
    ):
        return f"{_REDACTED_INPUT_PREFIX}{_stable_hash(value)}]"
    if isinstance(value, dict):
        return {
            str(nested_key): _persistent_input_value(
                key=str(nested_key),
                value=nested_value,
            )
            for nested_key, nested_value in value.items()
        }
    if isinstance(value, list):
        return [_persistent_input_value(key=key, value=item) for item in value]
    if isinstance(value, tuple):
        return tuple(_persistent_input_value(key=key, value=item) for item in value)
    return value


def _permission_decision_event_fields(
    decision: ToolPermissionDecision,
) -> dict[str, Any]:
    return {
        "decision_id": str(decision.decision_id or ""),
        "decision_steps": [dict(item) for item in decision.decision_steps],
        "safety_check_kind": str(decision.safety_check_kind or ""),
        "permission_matcher_kind": str(decision.permission_matcher_kind or "default"),
    }


def _canonical_approval_step(
    source: str,
    receipt: ApprovalReceipt,
    *,
    grant_id: str = "",
) -> dict[str, Any]:
    return {
        "step": "canonical_approval",
        "source": source,
        "reviewer": receipt.reviewer.value,
        "approval_fingerprint": receipt.fingerprint,
        "request_id": receipt.request_id,
        "grant_id": grant_id,
    }


def _approval_contract_from_decision(
    *,
    permission_decision: ToolPermissionDecision,
    approved_permission_session: ToolPermissionSession | None,
) -> ApprovalContract:
    if approved_permission_session is not None:
        return ApprovalContract(
            behavior=permission_decision.behavior,
            granted=permission_decision.granted,
            source="user_approval",
            session_id=approved_permission_session.session_id,
            decision_scope=approved_permission_session.decision_scope or "",
            reason=permission_decision.reason,
        )
    approval_steps = [
        item
        for item in permission_decision.decision_steps
        if isinstance(item, dict) and item.get("step") == "canonical_approval"
    ]
    source = (
        str(approval_steps[-1].get("source", "canonical_profile"))
        if approval_steps
        else "canonical_profile"
    )
    return ApprovalContract(
        behavior=permission_decision.behavior,
        granted=permission_decision.granted,
        source=source,
        decision_scope=permission_decision.continuation_kind,
        reason=permission_decision.reason,
    )


def _execution_contract_receipt_metadata(
    *,
    execution_contract: ExecutionAttemptContract | None,
    execution_contract_hash_value: str,
) -> dict[str, Any]:
    if execution_contract is None:
        return {}
    payload = {
        "execution_contract_id": execution_contract.contract_id,
        "execution_contract_hash": execution_contract_hash_value
        or execution_contract_hash(execution_contract),
        "exec_policy_decision_id": execution_contract.exec_policy_decision_id,
        "exec_policy_hash": execution_contract.exec_policy_hash,
    }
    expectations = execution_contract.enforcement_expectations
    for key in (
        "sandbox_enforcement_status",
        "effective_read_roots",
        "effective_write_roots",
    ):
        if key in expectations:
            payload.setdefault(key, expectations[key])
    if execution_contract.blocked_policy_reasons:
        payload["blocked_policy_reasons"] = list(
            execution_contract.blocked_policy_reasons
        )
    return payload


def _hook_engine_for_context(
    context: ToolRuntimeContext,
    default_engine: HookEngine | None,
) -> HookEngine | None:
    scratchpad = getattr(context.state_store.state.workflow_state, "scratchpad", {})
    raw_modifiers = (
        scratchpad.get("skill_runtime_hook_modifiers")
        if isinstance(scratchpad, dict)
        else None
    )
    if not isinstance(raw_modifiers, list) or not raw_modifiers:
        return context.hook_engine or default_engine
    configs: list[HookRegistryConfig] = []
    for item in raw_modifiers:
        if not isinstance(item, dict):
            continue
        hooks_config = item.get("hooks_config")
        if not isinstance(hooks_config, dict) or not hooks_config:
            continue
        source = f"skill:{item.get('skill_name', 'unknown')}"
        if isinstance(hooks_config.get("events"), dict):
            config = HookRegistryConfig.from_dict(hooks_config, source=source)
        else:
            config = HookRegistryConfig.from_events_dict(hooks_config, source=source)
        if config.enabled:
            configs.append(config)
    if not configs:
        return context.hook_engine or default_engine
    overlay = HookRegistry.from_config(HookRegistryConfig.merge(*configs))
    base = context.hook_engine or default_engine
    if base is None:
        return build_hook_engine(
            HookRegistryConfig.merge(*configs),
            effective_permissions=context.effective_permissions,
        )
    return base.with_registrations(overlay.snapshot)


def _active_skill_resource_roots(context: ToolRuntimeContext) -> tuple[str, ...]:
    state = getattr(getattr(context, "state_store", None), "state", None)
    workflow_state = getattr(state, "workflow_state", None)
    scratchpad = getattr(workflow_state, "scratchpad", None)
    entries = (
        scratchpad.get("active_skill_resource_roots", [])
        if isinstance(scratchpad, dict)
        else []
    )
    if not isinstance(entries, list):
        return ()
    roots: list[str] = []
    current_stage = str(getattr(context.stage, "value", context.stage) or "")
    current_attempt = int(context.attempt or 0)
    current_continuation = int(context.continuation_index or 0)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("stage", "") or "") != current_stage:
            continue
        if int(entry.get("attempt", 0) or 0) != current_attempt:
            continue
        if int(entry.get("continuation_index", 0) or 0) > current_continuation:
            continue
        root = str(entry.get("root", "") or "").strip()
        if root and root not in roots:
            roots.append(root)
    return tuple(roots)


def _assess_destructive_action(
    *,
    definition: ToolDefinition,
    input: dict[str, Any],
    project_root: Path,
) -> DestructiveActionAssessment:
    base_permission_class = definition.summary.permission_class
    if definition.name == "exec_command":
        command = str(input.get("cmd", "") or "").strip()
        if command:
            analysis = analyze_bash_command(command)
            if analysis.kind == "too_complex":
                return DestructiveActionAssessment(
                    is_destructive=True,
                    effective_permission_class="destructive",
                    reason=f"command requires conservative policy review: {analysis.reason}",
                    kinds=("unresolved_shell_semantics",),
                )
            mutating = {"rm", "rmdir", "mv", "unlink", "shred", "truncate"}
            if any(
                segment.effective_argv
                and Path(segment.effective_argv[0]).name in mutating
                for segment in analysis.commands
            ):
                return DestructiveActionAssessment(
                    is_destructive=True,
                    effective_permission_class="destructive",
                    reason="command contains a destructive filesystem operation",
                    kinds=("destructive_command",),
                )
        else:
            program = str(input.get("program", "") or "").strip()
            if Path(program).name in {"rm", "rmdir", "mv", "unlink", "shred", "truncate"}:
                return DestructiveActionAssessment(
                    is_destructive=True,
                    effective_permission_class="destructive",
                    reason="structured command contains a destructive filesystem operation",
                    kinds=("destructive_command",),
                )
    if not definition.to_summary().destructive_capable:
        return DestructiveActionAssessment(
            effective_permission_class=base_permission_class
        )

    tool_name = definition.name
    kinds: list[str] = []
    reason = ""
    try:
        if tool_name == "patch_apply":
            patch_text = str(input.get("patch", "") or "")
            summary = summarize_operations(parse_patch(patch_text))
            kinds.extend(summary.destructive_kinds)
            if kinds:
                reason = "patch operations request destructive or potentially overwriting changes"
    except Exception:
        return DestructiveActionAssessment(
            effective_permission_class=base_permission_class
        )

    unique_kinds = tuple(dict.fromkeys(kinds))
    if not unique_kinds:
        return DestructiveActionAssessment(
            effective_permission_class=base_permission_class
        )
    return DestructiveActionAssessment(
        is_destructive=True,
        effective_permission_class="destructive",
        reason=reason or "destructive workspace mutation requested",
        kinds=unique_kinds,
    )


def _tool_result_summary(tool_result: ToolResult) -> str:
    return summarize_tool_result(tool_result, max_len=240)


def _build_request_context_effects(
    *,
    definition: ToolDefinition,
    input: dict[str, Any],
) -> dict[str, Any]:
    effects: dict[str, Any] = {}
    target_paths: list[str] = []
    for key in (
        "path",
        "file_path",
        "paths",
        "source_path",
        "destination_path",
        "cwd",
        "target",
    ):
        value = input.get(key)
        if isinstance(value, str) and value.strip():
            target_paths.append(value.strip())
        elif isinstance(value, list):
            target_paths.extend(
                str(item).strip() for item in value if str(item).strip()
            )
    if target_paths:
        effects["target_paths"] = target_paths
    if definition.activity_builder is not None:
        try:
            activity_summary = definition.activity_builder(dict(input))
        except Exception:
            activity_summary = None
        if isinstance(activity_summary, str) and activity_summary.strip():
            effects["activity_summary"] = activity_summary.strip()
    if definition.classifier_input_builder is not None:
        try:
            classifier_input = definition.classifier_input_builder(dict(input))
        except Exception:
            classifier_input = None
        if isinstance(classifier_input, str):
            stripped = classifier_input.strip()
            if stripped:
                effects["classifier_input"] = stripped
        elif isinstance(classifier_input, dict) and classifier_input:
            effects["classifier_input"] = dict(classifier_input)
    return effects


def _tool_result_context_effects(tool_result: ToolResult) -> dict[str, Any]:
    metadata = tool_result.metadata if isinstance(tool_result.metadata, dict) else {}
    context_effects = metadata.get("context_effects")
    effects = dict(context_effects) if isinstance(context_effects, dict) else {}
    for key in (
        'failure_class', 'failure_kind', 'retryable', 'recommended_next_action',
        'sandbox_backend', 'denial_evidence',
    ):
        if key in metadata:
            effects[key] = metadata[key]
    return effects


def _tool_result_with_updated_mcp_output(
    tool_result: ToolResult, updated_output: dict[str, Any]
) -> ToolResult:
    if not _is_mcp_tool_result(tool_result):
        return tool_result
    data = dict(tool_result.data or {})
    original_content = data.get("content")
    raw_result = (
        dict(data.get("raw_result", {}))
        if isinstance(data.get("raw_result"), dict)
        else {}
    )
    standard_types = {"content": list, "structuredContent": dict,
                      "isError": bool, "_meta": dict}
    nested = updated_output.get("raw_result", {})
    invalid = not isinstance(nested, dict)
    for fields in (updated_output, nested if isinstance(nested, dict) else {}):
        invalid = invalid or any(
            key in fields and not isinstance(fields[key], expected)
            for key, expected in standard_types.items()
        )
    if invalid:
        return replace(tool_result, warnings=[*tool_result.warnings,
            "PostToolUse MCP output rewrite ignored: invalid MCP field type"])
    # Keep one canonical payload; explicit top-level fields win over the wrapper.
    for key in standard_types:
        if key in data and key not in raw_result:
            raw_result[key] = data[key]
    raw_result.update(nested)
    raw_result.update({key: updated_output[key] for key in standard_types
                       if key in updated_output})
    if not any(key in updated_output for key in (*standard_types, "raw_result")):
        raw_result["content"] = dict(updated_output)
    for key in standard_types:
        if key in raw_result and (key == "content" or key in data):
            data[key] = raw_result[key]
    data["raw_result"] = raw_result
    metadata = dict(tool_result.metadata)
    context_effects = (
        dict(metadata.get("context_effects", {}))
        if isinstance(metadata.get("context_effects"), dict)
        else {}
    )
    context_effects.update(
        {
            "hook_updated_mcp_tool_output": True,
            "original_mcp_content_summary": _truncate_inline(
                str(original_content), 240
            ),
        }
    )
    metadata.update(
        {
            "mcp_tool_output_updated_by_hook": True,
            "context_effects": context_effects,
        }
    )
    return replace(
        tool_result,
        data=data,
        stdout=_mcp_tool_stdout(data),
        metadata=metadata,
    )


def _is_mcp_tool_result(tool_result: ToolResult) -> bool:
    data = tool_result.data if isinstance(tool_result.data, dict) else {}
    metadata = tool_result.metadata if isinstance(tool_result.metadata, dict) else {}
    return bool(
        tool_result.tool_name == "mcp_tool_invoke"
        or str(tool_result.tool_name).startswith("mcp__")
        or metadata.get("mcp_dynamic_tool") is True
        or ("server_name" in data and "tool_name" in data and "raw_result" in data)
    )


def _mcp_tool_stdout(data: dict[str, Any]) -> str:
    return (
        f"server_name={data.get('server_name', '')}\n"
        f"tool_name={data.get('tool_name', '')}\n"
        f"content={data.get('content')}"
    )


def _truncate_inline(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _custom_validation_context_effects(feedback: dict[str, Any]) -> dict[str, Any]:
    effects: dict[str, Any] = {}
    for key in (
        "recoverable_validation_error",
        "recovery_hint",
        "suggested_tool_name",
        "corrective_action",
        "missing_targets",
        "suggested_targets",
        "invalid_root",
        "suggested_roots",
    ):
        if key in feedback:
            effects[key] = feedback[key]
    recovery_payload = feedback.get("tool_call_recovery")
    if isinstance(recovery_payload, dict):
        suggested_tool_name = str(recovery_payload.get("tool_name", "") or "").strip()
        if suggested_tool_name and "suggested_tool_name" not in effects:
            effects["suggested_tool_name"] = suggested_tool_name
        policy = str(recovery_payload.get("policy", "") or "").strip()
        if policy:
            effects["tool_call_recovery_policy"] = policy
    return effects


def _build_recovered_tool_input(
    *,
    original_input: dict[str, Any],
    recovery_payload: dict[str, Any],
) -> dict[str, Any] | None:
    input_patch = recovery_payload.get("input_patch")
    if input_patch is not None and not isinstance(input_patch, dict):
        return None
    inherit_keys = recovery_payload.get("inherit_input_keys")
    if inherit_keys is None:
        inherited = dict(original_input)
    elif isinstance(inherit_keys, list):
        inherited = {
            str(key): original_input[str(key)]
            for key in inherit_keys
            if str(key) in original_input
        }
    else:
        return None
    blocked_keys = {
        str(key)
        for key in recovery_payload.get("blocked_input_keys", [])
        if str(key).strip()
    }
    for key in blocked_keys:
        inherited.pop(key, None)
    if isinstance(input_patch, dict):
        for key, value in input_patch.items():
            key_text = str(key).strip()
            if key_text and key_text not in blocked_keys:
                inherited[key_text] = value
    return inherited


def _recovered_input_requests_destructive_behavior(
    input_payload: dict[str, Any],
) -> bool:
    return any(bool(input_payload.get(key)) for key in ("delete", "overwrite", "force"))


def _run_custom_input_validator(
    *, definition: ToolDefinition, execution_context: ToolExecutionContext,
    input: dict[str, Any], request: ToolCall | None = None,
) -> dict[str, Any] | None:
    intent_feedback = _validate_tool_call_intent(definition=definition, request=request)
    if intent_feedback is not None:
        return intent_feedback
    return definition.validate_business_input(execution_context, input)


_READ_INTENT_PREFIXES = (
    "read",
    "inspect",
    "search",
    "find",
    "discover",
    "list",
    "locate",
    "view",
    "preview",
    "check",
)
_WRITE_INTENT_VERBS = (
    "write",
    "create",
    "save",
    "materialize",
    "author",
    "generate",
    "produce",
    "draft",
    "overwrite",
)
_WRITE_TARGET_NOUNS = (
    "file",
    "script",
    "config",
    "configuration",
    "report",
    "notebook",
    "code",
    "module",
    "python",
    "yaml",
    "json",
    "markdown",
    "md",
)


def _validate_tool_call_intent(
    *,
    definition: ToolDefinition,
    request: ToolCall | None,
) -> dict[str, Any] | None:
    if request is None:
        return None
    summary = definition.to_summary()
    if not summary.read_only or summary.writes_workspace or definition.writes_workspace:
        return None
    if not _tool_call_requests_workspace_mutation(request):
        return None
    hint = (
        f"{definition.name} is read-only, but this tool call appears to be a write/create/save action. "
        "On the next loop, choose a tool that can perform the requested workspace mutation."
    )
    return {
        "error": f"{definition.name} cannot satisfy a write/create/save intent because it is read-only.",
        "compact_hint": hint,
        "observation_summary": (
            f"{definition.name} rejected a write-like intent before execution; choose a file mutation tool next."
        ),
        "validation_source": "intent_guard",
        "validation_code": "read_only_tool_selected_for_write_intent",
        "recoverable_validation_error": True,
        "recovery_hint": hint,
        "suggested_tool_name": "patch_apply",
        "corrective_action": "Use patch_apply or exec_command for the requested workspace mutation.",
        "default_input_examples": [
            {
                "file_path": "scripts/qc_intake.py",
                "content": '#!/usr/bin/env python3\nprint("ok")\n',
            },
            {"file_path": "configs/qc.yaml", "content": "dataset: demo\n"},
        ],
    }


def _tool_call_requests_workspace_mutation(request: ToolCall) -> bool:
    request_id = str(request.request_id or "").strip()
    if _request_id_has_write_intent(request_id):
        return True
    for text in (request.reason, request.expected_output):
        if _text_has_write_intent(str(text or "")):
            return True
    return False


def _request_id_has_write_intent(value: str) -> bool:
    normalized = _normalize_intent_text(value)
    if not normalized:
        return False
    tokens = [token for token in normalized.split() if token]
    if not tokens:
        return False
    return tokens[0] in _WRITE_INTENT_VERBS or (
        any(token in _WRITE_INTENT_VERBS for token in tokens)
        and any(token in _WRITE_TARGET_NOUNS for token in tokens)
    )


def _text_has_write_intent(value: str) -> bool:
    normalized = _normalize_intent_text(value)
    if not normalized:
        return False
    if any(
        marker in normalized
        for marker in (
            "later file write",
            "later use file write",
            "later use of file write",
        )
    ):
        return False
    if _starts_with_read_intent(normalized) and any(
        marker in normalized for marker in (" before ", " prior to ")
    ):
        return False
    words = normalized.split()
    for index, word in enumerate(words):
        if word not in _WRITE_INTENT_VERBS:
            continue
        window = words[index + 1 : index + 12]
        if any(token in _WRITE_TARGET_NOUNS for token in window):
            return True
    for index, word in enumerate(words):
        if word not in _WRITE_TARGET_NOUNS:
            continue
        window = words[index + 1 : index + 8]
        if any(token in _WRITE_INTENT_VERBS for token in window):
            return True
    return False


def _starts_with_read_intent(value: str) -> bool:
    return any(
        value == prefix or value.startswith(f"{prefix} ")
        for prefix in _READ_INTENT_PREFIXES
    )


def _normalize_intent_text(value: str) -> str:
    text = str(value or "").strip().casefold()
    if not text:
        return ""
    chars = [char if char.isalnum() else " " for char in text]
    return " ".join("".join(chars).split())


def _build_custom_result_summary(
    *,
    definition: ToolDefinition,
    tool_result: ToolResult,
) -> str | None:
    if definition.summary_builder is None:
        return None
    try:
        summary = definition.summary_builder(tool_result)
    except Exception:
        return None
    if isinstance(summary, str):
        cleaned = summary.strip()
        if cleaned:
            return cleaned
    return None


def _supports_finalization(
    *, definition, stage: Stage, tool_result: ToolResult | None = None
) -> bool:
    if stage != Stage.IMPLEMENTATION_EXECUTION:
        return False
    if definition.name == "remote_pull":
        if tool_result is None:
            return True
        data = tool_result.data if isinstance(tool_result.data, dict) else {}
        return bool(
            tool_result.ok
            and data.get("local_destinations")
            and _has_pulled_result_report_preview(data)
        )
    if definition.name == "remote_monitor":
        if tool_result is None:
            return True
        data = tool_result.data if isinstance(tool_result.data, dict) else {}
        monitor = data.get("monitor") if isinstance(data.get("monitor"), dict) else {}
        terminal_states = {"completed", "failed", "contract_violation"}
        return bool(
            tool_result.ok
            and str(monitor.get("job_state", "") or "").strip() in terminal_states
        )
    return definition.observation_family in {"repo_layout", "python_env"}


def _remote_pull_artifact_context_effects(
    *, definition: ToolDefinition, tool_result: ToolResult
) -> dict[str, Any]:
    if definition.name != "remote_pull" or not tool_result.ok:
        return {}
    pulled_paths = _pulled_artifact_paths_from_data(
        tool_result.data if isinstance(tool_result.data, dict) else {}
    )
    if not pulled_paths:
        return {}
    return {"pulled_artifact_paths": pulled_paths}


def _pulled_artifact_paths_from_data(data: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for raw_path in data.get("local_destinations") or ():
        text = str(raw_path or "").strip()
        if text and text not in paths:
            paths.append(text)
    previews = data.get("pulled_artifact_previews")
    if isinstance(previews, list):
        for item in previews:
            if not isinstance(item, dict):
                continue
            text = str(item.get("path") or "").strip()
            if text and text not in paths:
                paths.append(text)
    return paths


def _has_pulled_result_report_preview(data: dict[str, object]) -> bool:
    previews = data.get("pulled_artifact_previews")
    if not isinstance(previews, list):
        return False
    result_markers = ("summary", "report", "result", "metric", "metrics", "qc")
    control_markers = ("status", "log", "probe")
    for item in previews:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path", "") or "").strip().casefold()
        preview = str(item.get("preview", "") or "").strip()
        if not path or not preview:
            continue
        name = Path(path).name.casefold()
        has_result_marker = any(marker in name for marker in result_markers)
        has_only_control_marker = (
            any(marker in name for marker in control_markers) and not has_result_marker
        )
        if has_result_marker and not has_only_control_marker:
            return True
    return False


def _resolve_tool_execution_protocol(
    *,
    request: ToolCall,
    execution_protocol: ToolExecutionProtocol,
) -> str:
    if execution_protocol != "auto":
        return execution_protocol
    requested_by = str(request.requested_by or "").strip().casefold()
    if requested_by == "runtime_policy":
        return "runtime_policy"
    return "native_tool"


def _deny_redundant_bound_dataset_confirmation(
    *,
    context: ToolRuntimeContext,
    request: ToolCall,
) -> dict[str, Any] | None:
    bound_paths = _bound_runtime_dataset_paths(context)
    if not bound_paths:
        return None
    questions = request.input.get("questions")
    if not isinstance(questions, list) or not questions:
        return None
    combined_text = " ".join(
        _question_text_for_policy(item) for item in questions if isinstance(item, dict)
    )
    if not combined_text.strip():
        return None
    lowered = combined_text.casefold()
    if not _asks_to_confirm_existing_paths(lowered):
        return None
    mentioned_paths = [path for path in bound_paths if path in combined_text]
    if not mentioned_paths:
        return None
    if _asks_for_missing_or_replacement_value(lowered):
        return None
    hint = (
        "Current runtime artifact evidence already provides authoritative dataset paths for this task. "
        "Do not ask the user to confirm them. If access is uncertain, verify with eligible tools "
        "or continue with the bound paths and report concrete tool failures."
    )
    return {
        "error": "human_question cannot ask the user to confirm already known runtime dataset paths",
        "compact_hint": hint,
        "recovery_hint": hint,
        "validation_code": "redundant_authoritative_dataset_confirmation",
        "authoritative_paths": mentioned_paths,
    }


def _bound_runtime_dataset_paths(context: ToolRuntimeContext) -> list[str]:
    paths: list[str] = []
    for record in context.state_store.state.artifact_state.recent_artifacts:
        paths.extend(str(path) for path in record.source_paths if str(path).strip())
    return paths


def _question_text_for_policy(question: dict[str, Any]) -> str:
    parts = [
        str(question.get("question_id", "") or ""),
        str(question.get("header", "") or ""),
        str(question.get("question", "") or ""),
    ]
    options = question.get("options")
    if isinstance(options, list):
        for option in options:
            if not isinstance(option, dict):
                continue
            parts.append(str(option.get("label", "") or ""))
            parts.append(str(option.get("description", "") or ""))
    return " ".join(part for part in parts if part)


def _asks_to_confirm_existing_paths(text: str) -> bool:
    confirmation_terms = (
        "confirm",
        "verify",
        "verification",
        "correct",
        "accessible",
        "available",
        "exist",
        "exists",
        "路径确认",
        "确认",
        "可访问",
        "是否正确",
        "是否存在",
    )
    path_terms = ("path", "paths", "dataset", "datasets", "目录", "数据集")
    return any(term in text for term in confirmation_terms) and any(
        term in text for term in path_terms
    )


def _asks_for_missing_or_replacement_value(text: str) -> bool:
    missing_value_terms = (
        "provide",
        "give me",
        "what is the path",
        "which path",
        "replacement",
        "corrected path",
        "new path",
        "please provide",
        "请提供",
        "给出",
        "替代路径",
        "新的路径",
    )
    return any(term in text for term in missing_value_terms)
