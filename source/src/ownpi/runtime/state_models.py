from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ownpi.controller.stages import ACTIVE_STAGE_ORDER, Stage
from ownpi.stage_ids import canonical_gate_decision_id, canonical_stage_id
from ownpi.runtime.contracts import (
    ArtifactRecord,
    ToolResult,
)
from ownpi.runtime.events import HistoryEvent
from ownpi.runtime.execution.registry import ExecutionRegistry
from ownpi.runtime.limits.projection import (
    RECENT_RUNTIME_EVENT_LIMIT,
    RECENT_WORKFLOW_EVENT_LIMIT,
)
from ownpi.runtime.process_owner.bindings import ProcessOwnerBindingRegistry
from ownpi.runtime.runtime_events import RuntimeEventRecord
from ownpi.runtime.stage_prep.models import StagePrepState
from ownpi.runtime.task_context import RuntimeTaskContext
from ownpi.schemas.runtime_human_clarification import HumanClarificationSession
from ownpi.schemas.runtime_permission import SessionPermissionGrant, ToolPermissionSession
from ownpi.runtime.permissions.models import EffectivePermissions
from ownpi.runtime.permissions.runtime_config import default_runtime_permissions
from ownpi.schemas.feedback import (
    ReviewTarget,
    decode_review_target,
    CoreFeedbackIntentReceipt,
    FeedbackRecordRef,
    NextStageReceipt,
)
from ownpi.schemas.runtime_tooling import (
    ToolInvocationRecord,
    LLMUsageReceipt,
    ToolCall,
    ToolObservation,
    ToolProtocolViolationRecord,
    ToolPermissionEvent,
)
from ownpi.schemas.workflow_plan import (
    ExecutionTier,
    RunWorkflowPlan,
    StageRuntimeProfile,
)

if TYPE_CHECKING:
    from ownpi.runtime.memory.external.contracts import ExternalKnowledgeReleasePin


def _canonicalize_revisit_mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result = dict(value)
    for key in ('target_stage', 'source_stage'):
        if result.get(key) is not None:
            result[key] = canonical_stage_id(result[key])
    if result.get('decision') is not None:
        result['decision'] = canonical_gate_decision_id(result['decision'])
    return result


def _canonicalize_stage_counters(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, int] = {}
    for key, raw_count in value.items():
        stage = canonical_stage_id(key)
        result[stage] = result.get(stage, 0) + max(0, int(raw_count or 0))
    return result


@dataclass
class RevisitState:
    pending_target_stage: str | None = None
    pending_target_attempt: int | None = None
    pending_source_stage: str | None = None
    pending_source_attempt: int | None = None
    pending_source_stage_run_id: str | None = None
    pending_decision: str | None = None
    pending_reason: str | None = None
    pending_revisit_contract: dict[str, Any] | None = None
    latest_revisit_event: dict[str, Any] | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    total_revisits: int = 0
    stage_revisit_counters: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> 'RevisitState':
        payload = data or {}
        return cls(
            pending_target_stage=(
                canonical_stage_id(payload['pending_target_stage'])
                if payload.get('pending_target_stage') is not None
                else None
            ),
            pending_target_attempt=(
                int(payload['pending_target_attempt'])
                if payload.get('pending_target_attempt') is not None
                else None
            ),
            pending_source_stage=(
                canonical_stage_id(payload['pending_source_stage'])
                if payload.get('pending_source_stage') is not None
                else None
            ),
            pending_source_attempt=(
                int(payload['pending_source_attempt'])
                if payload.get('pending_source_attempt') is not None
                else None
            ),
            pending_source_stage_run_id=(
                str(payload['pending_source_stage_run_id'])
                if payload.get('pending_source_stage_run_id') is not None
                else None
            ),
            pending_decision=(
                canonical_gate_decision_id(payload['pending_decision'])
                if payload.get('pending_decision') is not None
                else None
            ),
            pending_reason=(
                str(payload['pending_reason'])
                if payload.get('pending_reason') is not None
                else None
            ),
            pending_revisit_contract=(
                _canonicalize_revisit_mapping(payload['pending_revisit_contract'])
                if isinstance(payload.get('pending_revisit_contract'), dict)
                else None
            ),
            latest_revisit_event=(
                _canonicalize_revisit_mapping(payload['latest_revisit_event'])
                if isinstance(payload.get('latest_revisit_event'), dict)
                else None
            ),
            history=[
                _canonicalize_revisit_mapping(item)
                for item in payload.get('history', [])
                if isinstance(item, dict)
            ],
            total_revisits=max(0, int(payload.get('total_revisits', 0) or 0)),
            stage_revisit_counters=_canonicalize_stage_counters(
                payload.get('stage_revisit_counters')
            ),
        )

    def current_context_for(self, *, stage: Stage, attempt: int) -> dict[str, Any] | None:
        if self.pending_target_stage != stage.value:
            return None
        if not isinstance(self.pending_revisit_contract, dict):
            raise RuntimeError('active revisit state requires a canonical revisit contract')
        pending_attempt = (
            int(self.pending_target_attempt or 0)
            if self.pending_target_attempt is not None
            else None
        )
        current_attempt = int(attempt or 0)
        if pending_attempt is not None and current_attempt < pending_attempt:
            return None
        return {
            'target_stage': self.pending_target_stage,
            'target_attempt': current_attempt if pending_attempt is not None else self.pending_target_attempt,
            'original_target_attempt': self.pending_target_attempt,
            'source_stage': self.pending_source_stage,
            'source_attempt': self.pending_source_attempt,
            'source_stage_run_id': self.pending_source_stage_run_id,
            'decision': self.pending_decision,
            'reason': self.pending_reason,
            'revisit_contract': dict(self.pending_revisit_contract),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            'pending_target_stage': self.pending_target_stage,
            'pending_target_attempt': self.pending_target_attempt,
            'pending_source_stage': self.pending_source_stage,
            'pending_source_attempt': self.pending_source_attempt,
            'pending_source_stage_run_id': self.pending_source_stage_run_id,
            'pending_decision': self.pending_decision,
            'pending_reason': self.pending_reason,
            'pending_revisit_contract': (
                dict(self.pending_revisit_contract)
                if isinstance(self.pending_revisit_contract, dict)
                else None
            ),
            'latest_revisit_event': (
                dict(self.latest_revisit_event)
                if isinstance(self.latest_revisit_event, dict)
                else None
            ),
            'history': [dict(item) for item in self.history if isinstance(item, dict)],
            'total_revisits': int(self.total_revisits or 0),
            'stage_revisit_counters': {
                str(key): int(value or 0)
                for key, value in self.stage_revisit_counters.items()
            },
        }


@dataclass
class WorkflowState:
    execution_tier: ExecutionTier = ExecutionTier.FULL
    stage_runtime_profile: StageRuntimeProfile = StageRuntimeProfile.RESEARCH_LEVEL
    current_stage: Stage | None = ACTIVE_STAGE_ORDER[0]
    current_stage_attempt: int = 0
    run_workflow_plan: RunWorkflowPlan | None = None
    stage_attempt_counters: dict[str, int] = field(default_factory=dict)
    revisit_state: RevisitState = field(default_factory=RevisitState)
    pause_reason: str | None = None
    pending_human_clarification: HumanClarificationSession | None = None
    human_clarification_history: list[HumanClarificationSession] = field(default_factory=list)
    clarification_resume_count: int = 0
    pending_tool_permission: ToolPermissionSession | None = None
    tool_permission_history: list[ToolPermissionSession] = field(default_factory=list)
    current_query_chain_id: str | None = None
    current_query_chain_depth: int = 0
    parent_query_chain_id: str | None = None
    query_chain_history: list[dict[str, Any]] = field(default_factory=list)
    current_branch_id: str = 'main'
    last_stage_run_id: str | None = None
    scratchpad: dict[str, Any] = field(default_factory=dict)

    @property
    def current_phase(self) -> str:
        return self.current_stage.value if self.current_stage is not None else ''


def calculate_total_stage_attempts(workflow_state: WorkflowState) -> int:
    """Calculate the attempt total from the canonical per-stage counters."""

    return sum(
        int(value or 0) for value in workflow_state.stage_attempt_counters.values()
    )

@dataclass
class CriticLoopState:
    critic_role: str = 'none'
    source_stage: str = ''
    attempt: int = 0
    phase: str = 'idle'
    round: int = 0
    review_target: ReviewTarget | None = None
    feedback_record_id: str | None = None
    core_intent_receipt: CoreFeedbackIntentReceipt | None = None
    completion_reason: str | None = None

    @classmethod
    def from_dict(cls, data: Any) -> 'CriticLoopState':
        if not isinstance(data, dict):
            return cls()
        allowed_fields = {
            'critic_role',
            'source_stage',
            'attempt',
            'phase',
            'round',
            'review_target',
            'feedback_record_id',
            'core_intent_receipt',
            'completion_reason',
        }
        unknown = sorted(str(key) for key in data if str(key) not in allowed_fields)
        if unknown:
            raise ValueError(
                f'critic loop contains unsupported fields: {", ".join(unknown)}'
            )
        critic_role = str(data.get('critic_role', 'none') or 'none')
        if critic_role not in {'none', 'reviewer'}:
            raise ValueError(f'invalid critic role: {critic_role}')
        phase = str(data.get('phase', 'idle') or 'idle')
        allowed_phases = {
            'idle',
            'critic_pending',
            'feedback_submitted',
            'core_post_feedback',
            'review_requested',
            'completed',
        }
        if phase not in allowed_phases:
            raise ValueError(f'invalid critic loop phase: {phase}')
        if phase == 'idle' and critic_role != 'none':
            raise ValueError('idle critic loop must use critic_role=none')
        if phase != 'idle' and critic_role == 'none':
            raise ValueError('active critic loop requires a critic role')
        target_payload = data.get('review_target')
        intent_payload = data.get('core_intent_receipt')
        return cls(
            critic_role=critic_role,
            source_stage=str(data.get('source_stage', '') or ''),
            attempt=int(data.get('attempt', 0) or 0),
            phase=phase,
            round=int(data.get('round', 0) or 0),
            review_target=(
                decode_review_target(target_payload)
                if isinstance(target_payload, dict)
                else None
            ),
            feedback_record_id=_optional_text(data.get('feedback_record_id')),
            core_intent_receipt=(
                CoreFeedbackIntentReceipt.from_dict(intent_payload)
                if isinstance(intent_payload, dict)
                else None
            ),
            completion_reason=_optional_text(data.get('completion_reason')),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            'critic_role': self.critic_role,
            'source_stage': self.source_stage,
            'attempt': self.attempt,
            'phase': self.phase,
            'round': self.round,
            'review_target': (
                self.review_target.to_dict()
                if self.review_target is not None
                else None
            ),
            'feedback_record_id': self.feedback_record_id,
            'core_intent_receipt': (
                self.core_intent_receipt.to_dict()
                if self.core_intent_receipt is not None
                else None
            ),
            'completion_reason': self.completion_reason,
        }


def _optional_text(value: Any) -> str | None:
    text = str(value or '').strip()
    return text or None


@dataclass
class FeedbackHandoffState:
    record_id: str = ''
    consumer_stage: str | None = None
    consumer_attempt: int | None = None
    status: str = 'none'
    consumed_at: str | None = None

    @classmethod
    def from_dict(cls, data: Any) -> 'FeedbackHandoffState':
        if not isinstance(data, dict):
            return cls()
        status = str(data.get('status', 'none') or 'none')
        if status not in {'none', 'pending', 'consumed'}:
            raise ValueError(f'invalid feedback handoff status: {status}')
        attempt = data.get('consumer_attempt')
        return cls(
            record_id=str(data.get('record_id', '') or '').strip(),
            consumer_stage=_optional_text(data.get('consumer_stage')),
            consumer_attempt=int(attempt) if attempt is not None else None,
            status=status,
            consumed_at=_optional_text(data.get('consumed_at')),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            'record_id': self.record_id,
            'consumer_stage': self.consumer_stage,
            'consumer_attempt': self.consumer_attempt,
            'status': self.status,
            'consumed_at': self.consumed_at,
        }


@dataclass
class AnalysisTransitionState:
    source_stage: str = ''
    attempt: int = 0
    feedback_record_id: str | None = None
    next_stage_receipt: NextStageReceipt | None = None
    gate_trace: dict[str, Any] | None = None
    applied_at: str | None = None

    @classmethod
    def from_dict(cls, data: Any) -> 'AnalysisTransitionState':
        if not isinstance(data, dict):
            return cls()
        receipt_payload = data.get('next_stage_receipt')
        gate_trace = data.get('gate_trace')
        return cls(
            source_stage=str(data.get('source_stage', '') or '').strip(),
            attempt=int(data.get('attempt', 0) or 0),
            feedback_record_id=_optional_text(data.get('feedback_record_id')),
            next_stage_receipt=(
                NextStageReceipt.from_dict(receipt_payload)
                if isinstance(receipt_payload, dict)
                else None
            ),
            gate_trace=dict(gate_trace) if isinstance(gate_trace, dict) else None,
            applied_at=_optional_text(data.get('applied_at')),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            'source_stage': self.source_stage,
            'attempt': self.attempt,
            'feedback_record_id': self.feedback_record_id,
            'next_stage_receipt': (
                self.next_stage_receipt.to_dict()
                if self.next_stage_receipt is not None
                else None
            ),
            'gate_trace': dict(self.gate_trace) if self.gate_trace is not None else None,
            'applied_at': self.applied_at,
        }


@dataclass
class FeedbackState:
    record_refs: list[FeedbackRecordRef] = field(default_factory=list)
    history_evidence_gaps: list[str] = field(default_factory=list)
    active_handoff: FeedbackHandoffState = field(default_factory=FeedbackHandoffState)
    critic_loop: CriticLoopState = field(default_factory=CriticLoopState)
    analysis_transition: AnalysisTransitionState = field(default_factory=AnalysisTransitionState)

    def record_ref(self, record_id: str | None) -> FeedbackRecordRef | None:
        expected = str(record_id or '').strip()
        if not expected:
            return None
        return next(
            (item for item in reversed(self.record_refs) if item.record_id == expected),
            None,
        )


@dataclass
class TokenBudgetSnapshot:
    estimated_input_tokens: int = 0
    estimated_output_reserve: int = 0
    context_window_limit: int = 128000
    raw_context_window_limit: int | None = None
    auto_compact_token_limit: int | None = None
    safety_margin: int = 4000
    pressure_level: str = 'low'
    estimation_strategy: str = 'heuristic'
    provider_hint: str | None = None
    category_token_estimates: dict[str, int] = field(default_factory=dict)
    rendered_char_counts: dict[str, int] = field(default_factory=dict)
    remaining_input_tokens: int = 0
    usage_ratio: float = 0.0
    budget_source: str = 'runtime_state'
    budget_limit_source: str = 'default'
    provider_prompt_tokens: int | None = None
    provider_completion_tokens: int | None = None
    provider_total_tokens: int | None = None
    provider_cache_read_tokens: int | None = None
    provider_cache_write_tokens: int | None = None
    estimate_vs_provider_delta: int | None = None
    usage_source: str = 'estimated_only'


@dataclass
class ContextState:
    workflow_event_count: int = 0
    recent_workflow_events: list[HistoryEvent] = field(default_factory=list)
    runtime_event_count: int = 0
    recent_runtime_events: list[RuntimeEventRecord] = field(default_factory=list)
    token_budget_snapshot: TokenBudgetSnapshot | None = None
    llm_usage_receipts: list[LLMUsageReceipt] = field(default_factory=list)
    hook_delivery_records: dict[str, dict[str, Any]] = field(default_factory=dict)
    hook_delivery_consumptions: list[dict[str, Any]] = field(default_factory=list)
    context_prefetch_state: Any = field(default_factory=dict)

    def record_workflow_event(self, event: HistoryEvent) -> None:
        self.workflow_event_count += 1
        self.recent_workflow_events.append(event)
        del self.recent_workflow_events[:-RECENT_WORKFLOW_EVENT_LIMIT]

    def record_runtime_event(self, event: RuntimeEventRecord) -> None:
        self.runtime_event_count = max(
            self.runtime_event_count + 1,
            int(event.sequence or 0),
        )
        self.recent_runtime_events.append(event)
        del self.recent_runtime_events[:-RECENT_RUNTIME_EVENT_LIMIT]


@dataclass
class ToolState:
    native_loaded_tool_refs: list[dict[str, str]] = field(default_factory=list)
    native_tool_search_receipts: list[dict[str, Any]] = field(default_factory=list)
    native_loaded_tool_scope_stage: str = ''
    native_loaded_tool_scope_attempt: int = 0
    native_loaded_tool_refs_verified: bool = False
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_history: list[ToolInvocationRecord] = field(default_factory=list)
    tool_protocol_violations: list[ToolProtocolViolationRecord] = field(default_factory=list)
    last_tool_results: list[ToolResult] = field(default_factory=list)
    read_file_state: dict[str, dict[str, Any]] = field(default_factory=dict)
    read_file_state_history: list[dict[str, Any]] = field(default_factory=list)
    recent_observations: list[ToolObservation] = field(default_factory=list)
    inflight_tool_requests: list[str] = field(default_factory=list)
    effective_permissions: EffectivePermissions = field(
        default_factory=default_runtime_permissions
    )
    session_permission_grants: list[SessionPermissionGrant] = field(default_factory=list)
    tool_permission_events: list[ToolPermissionEvent] = field(default_factory=list)
    tool_warnings: list[str] = field(default_factory=list)
    tool_round_counter: int = 0

@dataclass
class RemoteState:
    active_remote_host: str | None = None
    active_remote_profile: str | None = None
    remote_workdir: str | None = None
    remote_status_summary: str | None = None


@dataclass
class RuntimeTaskState:
    task_id: str
    task_type: str
    status: str
    description: str
    output_file: str | None = None
    output_offset: int = 0
    notified: bool = False
    provider_kind: str | None = None
    run_id: str | None = None
    stage: str | None = None
    attempt: int | None = None
    session_id: str | None = None
    started_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'RuntimeTaskState':
        raw_task_id = data.get('task_id', data.get('id', ''))
        raw_task_type = data.get('task_type', data.get('type', 'unknown'))
        return cls(
            task_id=str(raw_task_id).strip(),
            task_type=str(raw_task_type or 'unknown').strip() or 'unknown',
            status=str(data.get('status', 'pending') or 'pending').strip() or 'pending',
            description=str(data.get('description', '') or ''),
            output_file=(str(data['output_file']) if data.get('output_file') is not None else None),
            output_offset=int(data.get('output_offset', 0) or 0),
            notified=bool(data.get('notified', False)),
            provider_kind=(str(data['provider_kind']) if data.get('provider_kind') is not None else None),
            run_id=(str(data['run_id']) if data.get('run_id') is not None else None),
            stage=(str(data['stage']) if data.get('stage') is not None else None),
            attempt=(int(data['attempt']) if data.get('attempt') is not None else None),
            session_id=(str(data['session_id']) if data.get('session_id') is not None else None),
            started_at=(str(data['started_at']) if data.get('started_at') is not None else None),
            updated_at=(str(data['updated_at']) if data.get('updated_at') is not None else None),
            completed_at=(str(data['completed_at']) if data.get('completed_at') is not None else None),
            metadata=dict(data.get('metadata', {})) if isinstance(data.get('metadata'), dict) else {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            'task_id': self.task_id,
            'task_type': self.task_type,
            'status': self.status,
            'description': self.description,
            'output_file': self.output_file,
            'output_offset': int(self.output_offset or 0),
            'notified': bool(self.notified),
            'provider_kind': self.provider_kind,
            'run_id': self.run_id,
            'stage': self.stage,
            'attempt': self.attempt,
            'session_id': self.session_id,
            'started_at': self.started_at,
            'updated_at': self.updated_at,
            'completed_at': self.completed_at,
            'metadata': dict(self.metadata),
        }


@dataclass
class RuntimeTaskNotification:
    notification_id: str
    task_id: str
    target_actor: str | None = None
    priority: str = 'later'
    status: str | None = None
    consumed: bool = False
    created_at: str | None = None
    consumed_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'RuntimeTaskNotification':
        return cls(
            notification_id=str(data.get('notification_id', '') or '').strip(),
            task_id=str(data.get('task_id', '') or '').strip(),
            target_actor=(str(data['target_actor']) if data.get('target_actor') is not None else None),
            priority=str(data.get('priority', 'later') or 'later').strip() or 'later',
            status=(str(data['status']) if data.get('status') is not None else None),
            consumed=bool(data.get('consumed', False)),
            created_at=(str(data['created_at']) if data.get('created_at') is not None else None),
            consumed_at=(str(data['consumed_at']) if data.get('consumed_at') is not None else None),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            'notification_id': self.notification_id,
            'task_id': self.task_id,
            'target_actor': self.target_actor,
            'priority': self.priority,
            'status': self.status,
            'consumed': bool(self.consumed),
            'created_at': self.created_at,
            'consumed_at': self.consumed_at,
        }


@dataclass
class MCPRuntimeState:
    supported_transports: list[str] = field(default_factory=lambda: ['http', 'sse', 'stdio', 'ws'])
    declared_transports: list[str] = field(default_factory=lambda: ['stdio', 'http', 'sse', 'ws'])
    transport_contract: dict[str, Any] = field(
        default_factory=lambda: {
            'supported': ['http', 'sse', 'stdio', 'ws'],
            'declared': ['stdio', 'http', 'sse', 'ws'],
            'unsupported_policy': 'record_status_and_skip',
            'connection_scope': 'run_scoped_for_persistent_stdio',
        }
    )
    configured_servers: list[dict[str, Any]] = field(default_factory=list)
    resource_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    tool_cache: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    prompt_cache: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    connection_status: dict[str, dict[str, Any]] = field(default_factory=dict)
    server_capabilities: dict[str, dict[str, Any]] = field(default_factory=dict)
    stale_capabilities: dict[str, list[str]] = field(default_factory=dict)
    server_lifecycle: dict[str, dict[str, Any]] = field(default_factory=dict)
    capability_generations: dict[str, int] = field(default_factory=dict)
    capability_refresh_requests: list[dict[str, Any]] = field(default_factory=list)
    capability_refresh_results: list[dict[str, Any]] = field(default_factory=list)
    refresh_generation: int = 0
    live_refresh_events: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class MemoryState:
    internal_memory_status: str = 'unavailable'
    internal_memory_repository_identity: str = ''
    internal_memory_repository_identity_history: list[str] = field(default_factory=list)
    internal_memory_diagnostic: str = ''
    external_knowledge_pin: ExternalKnowledgeReleasePin | None = None
    external_knowledge_status: str = 'unavailable'
    external_knowledge_diagnostic: str = ''


@dataclass
class ArtifactState:
    recent_artifacts: list[ArtifactRecord] = field(default_factory=list)
    artifact_index: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class EnvironmentFact:
    fact_kind: str
    scope_identity: str
    subject: str
    value: bool | str | int | float | None
    evidence_ref: str
    observed_at: str
    validation_token: str

    @property
    def stable_key(self) -> tuple[str, str, str]:
        return (self.fact_kind, self.scope_identity, self.subject)

    def to_dict(self) -> dict[str, Any]:
        return {
            'fact_kind': self.fact_kind,
            'scope_identity': self.scope_identity,
            'subject': self.subject,
            'value': self.value,
            'evidence_ref': self.evidence_ref,
            'observed_at': self.observed_at,
            'validation_token': self.validation_token,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> 'EnvironmentFact':
        value = payload.get('value')
        if value is not None and not isinstance(value, (bool, str, int, float)):
            value = str(value)
        return cls(
            fact_kind=str(payload.get('fact_kind', '') or ''),
            scope_identity=str(payload.get('scope_identity', '') or ''),
            subject=str(payload.get('subject', '') or ''),
            value=value,
            evidence_ref=str(payload.get('evidence_ref', '') or ''),
            observed_at=str(payload.get('observed_at', '') or ''),
            validation_token=str(payload.get('validation_token', '') or ''),
        )


@dataclass
class EnvironmentState:
    config_snapshot: dict[str, Any] | None = None
    default_provider: str = 'host_local'
    latest_environment_receipts: list[dict[str, Any]] = field(default_factory=list)
    environment_receipt_count: int = 0
    latest_execution_contract: dict[str, Any] | None = None
    latest_exec_policy_decision: dict[str, Any] | None = None
    provider_counts: dict[str, int] = field(default_factory=dict)
    latest_lsp_status: list[dict[str, Any]] = field(default_factory=list)
    environment_facts: list[EnvironmentFact] = field(default_factory=list)


@dataclass
class RuntimePreferences:
    response_language: str
    ui_mode: str = 'user'
    review_profile: str = 'standard'
    temporary_model_override: str | None = None
    temporary_reasoning_override: str | None = None
    developer_verbosity: str = 'normal'
    timeout_policy: dict[str, Any] = field(default_factory=dict)
    interaction: dict[str, Any] = field(default_factory=dict)
    native_conversation: dict[str, Any] = field(default_factory=dict)
    context_meter: dict[str, Any] = field(default_factory=dict)
    internal_memory: dict[str, Any] = field(default_factory=dict)
    external_knowledge: dict[str, Any] = field(default_factory=dict)
    llm_profiles: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class RunMetadata:
    run_id: str
    created_at: str
    updated_at: str
    engine_version: str
    session_id: str | None = None
    task_id: str | None = None
    entry_mode: str = 'workflow'

    def to_dict(self) -> dict[str, Any]:
        payload = {
            'run_id': self.run_id,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'engine_version': self.engine_version,
            'session_id': self.session_id,
            'task_id': self.task_id,
            'entry_mode': self.entry_mode,
        }
        return payload


@dataclass
class RuntimeState:
    task_context: RuntimeTaskContext | None
    project_workspace_root: str
    managed_venv_path: str
    managed_non_python_dependency_root: str
    workflow_state: WorkflowState
    feedback_state: FeedbackState
    context_state: ContextState
    tool_state: ToolState
    remote_state: RemoteState
    memory_state: MemoryState
    artifact_state: ArtifactState
    environment_state: EnvironmentState
    runtime_preferences: RuntimePreferences
    run_metadata: RunMetadata
    execution_registry: ExecutionRegistry = field(default_factory=ExecutionRegistry)
    process_owner_bindings: ProcessOwnerBindingRegistry = field(
        default_factory=ProcessOwnerBindingRegistry
    )
    stage_prep_state: StagePrepState = field(default_factory=StagePrepState)
    mcp_runtime_state: MCPRuntimeState = field(default_factory=MCPRuntimeState)
    runtime_tasks: dict[str, RuntimeTaskState] = field(default_factory=dict)
    runtime_task_notifications: list[RuntimeTaskNotification] = field(default_factory=list)
    research_state_sha256: str | None = None
