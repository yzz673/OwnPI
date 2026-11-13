from __future__ import annotations

import hashlib
import json
import logging
import copy
from dataclasses import MISSING, fields
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Iterator

from ownpi.app.paths import AppPaths
from ownpi.controller import execution_contract_writer
from ownpi.controller import interaction_state_writer
from ownpi.runtime.runtime_events import RuntimeEventRecord
from ownpi.controller.stages import (
    GateDecision,
    Stage,
    is_completed_gate_decision,
)
from ownpi.runtime.builders import (
    build_agent_input,
    build_initial_runtime_state,
)
from ownpi.runtime.call_continuation.critic_loop import reset_critic_loop
from ownpi.runtime.contracts import (
    AgentInput,
    AgentOutput,
    ArtifactRecord,
    ArtifactRef,
    ToolResult,
)
from ownpi.runtime.environment import EnvironmentExecutionReceipt
from ownpi.runtime.environment.receipt_state import (
    current_environment_receipt_view,
)
from ownpi.runtime.events import HistoryEvent, now_utc_iso
from ownpi.runtime.memory.map_contracts import MemoryMapSection
from ownpi.runtime.memory.external.bootstrap import (
    initialize_external_knowledge_for_new_run,
)
from ownpi.runtime.project_workspace import canonical_project_workspace_root
from ownpi.runtime.query_chain import (
    build_query_chain_record,
    make_query_chain_id,
    query_chain_depth_for_parent,
)
from ownpi.runtime.context.entry import RuntimeEntryContext
from ownpi.runtime.context.prefetch import compact_context_prefetch_state
from ownpi.runtime.task_context import (
    RuntimeTaskContext,
)
from ownpi.runtime.tools.result_state import (
    current_tool_result_projection,
)
from ownpi.runtime.turn_spine import (
    build_turn_event_id,
    compact_mutation_ref,
    next_runtime_event_sequence,
    normalize_mutation_kind,
    normalize_state_domain,
    stable_payload_hash,
    summarize_mutation_event,
)
from ownpi.runtime.state_models import (
    AnalysisTransitionState,
    FeedbackHandoffState,
    RuntimeState,
)
from ownpi.runtime.tools.surface_utils import summarize_tool_result
from ownpi.schemas.runtime_human_clarification import (
    CONTROLLER_CLARIFICATION_HANDOFF_KEY,
    human_answer_text,
)
from ownpi.schemas.artifacts import GateTrace
from ownpi.schemas.context import AgentContextTrace, ToolSelectionTrace
from ownpi.schemas.runtime import AgentInstanceConfig, UserConfig
from ownpi.schemas.feedback import FeedbackRecord
from ownpi.schemas.runtime_human_clarification import (
    HumanClarificationSession,
    active_controller_clarification_handoff,
)
from ownpi.runtime.rollout.interaction_events import (
    InteractionEventStore,
    accept_answer,
    close_interaction,
    project_interactions,
    publish_request,
)
from ownpi.schemas.runtime_permission import (
    SessionPermissionGrant,
    ToolPermissionSession,
)
from ownpi.schemas.runtime_tooling import (
    LLMUsageReceipt,
    ToolInvocationRecord,
    ToolCall,
    ToolObservation,
    ToolProtocolViolationRecord,
    ToolPermissionEvent,
)
from ownpi.schemas.workflow_plan import (
    ExecutionTier,
    StageRuntimeProfile,
    RunWorkflowPlan,
)


_TOOL_PERMISSION_SCOPES = {"once", "session"}
def _normalize_tool_permission_scope(scope: Any) -> str:
    normalized = str(scope or "once").strip().lower().replace("-", "_")
    if normalized in {"bounded_session", "session_rule"}:
        normalized = "session"
    if normalized not in _TOOL_PERMISSION_SCOPES:
        raise RuntimeError(f"Unsupported tool permission scope: {scope}")
    return normalized


def _tool_permission_answer_reason(decision: str) -> str:
    if decision == "approve":
        return "human approved tool permission request"
    if decision == "deny_command":
        return "human denied the current tool command but allowed alternate execution"
    return "human denied tool permission request"


def _tool_permission_answer_verb(decision: str) -> str:
    if decision == "approve":
        return "approved"
    if decision == "deny_command":
        return "denied current command for"
    return "denied"


def _normalized_tool_permission_decision(decision: Any) -> str:
    normalized = str(decision or "").strip().lower().replace("-", "_")
    if normalized in {
        "deny_current_command",
        "reject_command",
        "reject_current_command",
    }:
        return "deny_command"
    return normalized


def _optional_string(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _read_json_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    return dict(payload) if isinstance(payload, dict) else {}



def _runtime_domain_from_category(category: str) -> str:
    normalized = str(category or '').strip()
    if normalized == 'tool_runtime':
        return 'tool'
    if normalized == 'interaction':
        return 'permission'
    if normalized == 'llm':
        return 'context'
    return normalize_state_domain(normalized)


def _mutation_kind_from_event_name(event_name: str, *, status: str = '') -> str:
    normalized = str(event_name or '').strip()
    if normalized.endswith(('_requested', '_started')):
        return 'request_created'
    if normalized.endswith(('_answered', '_decision')) or 'permission' in normalized:
        return 'decision_recorded'
    if 'receipt' in normalized:
        return 'receipt_recorded'
    if normalized.endswith(('_completed', '_failed', '_result')):
        return 'result_recorded'
    if 'cache' in normalized or 'refresh' in normalized:
        return 'cache_refreshed'
    return 'state_delta'




@dataclass
class WorkflowStateStore:
    state: RuntimeState
    app_paths: AppPaths | None = None
    run_event_store: InteractionEventStore | None = field(
        default=None, init=False, repr=False, compare=False,
    )
    _clarification_lock: RLock = field(
        default_factory=RLock, init=False, repr=False, compare=False,
    )
    _live_tool_approvals: dict[str, ToolPermissionSession] = field(
        default_factory=dict, init=False, repr=False, compare=False,
    )
    _approval_lock: RLock = field(
        default_factory=RLock, init=False, repr=False, compare=False,
    )
    _runtime_entry_context: RuntimeEntryContext = field(
        default_factory=RuntimeEntryContext,
        init=False,
        repr=False,
    )
    _runtime_control_poll: Callable[[str], None] | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _execution_runtime_service: Any | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _resume_conversation_binding: dict[tuple[str, int, str], str] = field(
        default_factory=dict, init=False, repr=False, compare=False,
    )

    @property
    def runtime_entry_context(self) -> RuntimeEntryContext:
        return self._runtime_entry_context

    @property
    def project_root(self) -> Path:
        return Path(self.state.project_workspace_root)

    @property
    def execution_runtime_service(self) -> Any | None:
        return self._execution_runtime_service

    def bind_execution_runtime_service(self, service: Any | None) -> None:
        self._execution_runtime_service = service

    def bind_resume_conversation(
        self, *, stage: str, attempt: int, actor_kind: str, conversation_id: str,
    ) -> None:
        self._resume_conversation_binding[(stage, attempt, actor_kind)] = conversation_id

    def take_resume_conversation(
        self, *, stage: str, attempt: int, actor_kind: str,
    ) -> str | None:
        return self._resume_conversation_binding.pop((stage, attempt, actor_kind), None)

    def execution_registry_snapshot(self) -> Any:
        service = self._execution_runtime_service
        if service is not None:
            return service.registry_snapshot()
        return self.state.execution_registry


    @contextmanager
    def bind_runtime_entry_context(
        self,
        context: RuntimeEntryContext,
    ) -> Iterator[None]:
        previous = self._runtime_entry_context
        self._runtime_entry_context = context
        try:
            yield
        finally:
            self._runtime_entry_context = previous

    @contextmanager
    def bind_runtime_control_poll(
        self,
        poll: Callable[[str], None] | None,
    ) -> Iterator[None]:
        previous = self._runtime_control_poll
        self._runtime_control_poll = poll
        try:
            yield
        finally:
            self._runtime_control_poll = previous

    def poll_runtime_control(self, reason: str) -> bool:
        poll = self._runtime_control_poll
        if poll is None:
            return False
        poll(str(reason or "").strip())
        return True

    @classmethod
    def initialize(
        cls,
        *,
        task_context: RuntimeTaskContext | None,
        run_workflow_plan: RunWorkflowPlan | None,
        session_id: str,
        task_id: str,
        run_id: str,
        execution_tier: ExecutionTier = ExecutionTier.FULL,
        project_root: Path,
        stage_runtime_profile: StageRuntimeProfile = StageRuntimeProfile.RESEARCH_LEVEL,
        user_config: UserConfig | None = None,
        app_paths: AppPaths | None = None,
        ui_mode: str = "user",
        entry_mode: str = "workflow",
        initialize_external_knowledge: bool = True,
    ) -> "WorkflowStateStore":
        config = user_config or _build_default_user_config()
        project_workspace_root = canonical_project_workspace_root(
            project_root,
            label="project workspace root",
        )
        store = cls(
            state=build_initial_runtime_state(
                task_context=task_context,
                project_workspace_root=project_workspace_root,
                run_workflow_plan=run_workflow_plan,
                user_config=config,
                session_id=session_id,
                task_id=task_id,
                run_id=run_id,
                execution_tier=execution_tier,
                stage_runtime_profile=stage_runtime_profile,
                ui_mode=ui_mode,
                entry_mode=entry_mode,
                app_paths=app_paths,
            ),
            app_paths=app_paths,
        )
        if initialize_external_knowledge:
            initialize_external_knowledge_for_new_run(
                state=store.state,
                config=config.runtime.external_knowledge,
            )
        return store

    def fork_child(
        self,
        *,
        run_id: str,
        task_id: str,
        session_id: str | None = None,
        app_paths: AppPaths | None = None,
    ) -> "WorkflowStateStore":
        """Create an isolated child store from the parent's durable state.

        Runtime-only callbacks, indexes, and execution services are intentionally
        not copied because they belong to the parent runtime scope.
        """
        if not str(run_id or '').strip() or not str(task_id or '').strip():
            raise ValueError("child run_id and task_id are required")
        # A child receives its own resume facts through its execution bundle.
        # Never copy the parent's recovery-scoped indexes (or their locks).
        resume_facts = self.state.workflow_state.scratchpad.get('rollout_resume_facts')
        memo = {id(resume_facts): None} if resume_facts is not None else {}
        try:
            state = copy.deepcopy(self.state, memo.copy())
        except (TypeError, ValueError) as exc:
            # Live runtime state may contain an ephemeral synchronization object
            # installed by a tool execution. Rebuild the dataclass field by
            # field and reset only the field that cannot cross a child boundary.
            state = copy.copy(self.state)
            for field_info in fields(self.state):
                value = getattr(self.state, field_info.name)
                if field_info.name == "context_state":
                    copied = copy.copy(value)
                    for context_field in fields(value):
                        context_value = getattr(value, context_field.name)
                        if context_field.name == "context_prefetch_state":
                            setattr(
                                copied,
                                context_field.name,
                                compact_context_prefetch_state(context_value),
                            )
                            continue
                        setattr(copied, context_field.name, copy.deepcopy(context_value))
                    setattr(state, field_info.name, copied)
                    continue
                try:
                    copied = copy.deepcopy(value, memo.copy())
                except (TypeError, ValueError):
                    factory = field_info.default_factory
                    if factory is not MISSING:
                        copied = factory()
                    elif field_info.default is not MISSING:
                        copied = copy.deepcopy(field_info.default)
                    else:
                        raise TypeError(
                            f"cannot fork child state field {field_info.name!r}"
                        ) from exc
                setattr(state, field_info.name, copied)
        metadata = state.run_metadata
        metadata.run_id = str(run_id).strip()
        metadata.task_id = str(task_id).strip()
        metadata.session_id = session_id or metadata.session_id
        metadata.entry_mode = 'subagent'
        state.execution_registry = type(state.execution_registry)()
        state.process_owner_bindings = type(state.process_owner_bindings)()
        return WorkflowStateStore(state=state, app_paths=app_paths or self.app_paths)

    def record_workflow_event(self, event: HistoryEvent) -> None:
        self.state.context_state.record_workflow_event(event)
        self._touch()

    @property
    def current_stage(self) -> Stage:
        return self.state.workflow_state.current_stage

    def set_run_workflow_plan(self, plan: RunWorkflowPlan) -> None:
        workflow_state = self.state.workflow_state
        workflow_state.run_workflow_plan = plan
        workflow_state.current_stage = plan.entry_stage
        self._touch()

    def clear_run_workflow_plan(self) -> None:
        workflow_state = self.state.workflow_state
        workflow_state.run_workflow_plan = None
        workflow_state.current_stage = None
        self._touch()

    def set_current_stage(self, stage: Stage) -> None:
        workflow_state = self.state.workflow_state
        plan = workflow_state.run_workflow_plan
        if plan is None or not plan.contains(stage):
            raise ValueError(
                f"current stage is not present in the active workflow plan: {stage.value}"
            )
        workflow_state.current_stage = stage
        workflow_state.current_stage_attempt = int(
            workflow_state.stage_attempt_counters.get(stage.value, 0) or 0
        )
        self._touch()

    def enter_stage_attempt(self, stage: Stage) -> int:
        previous_query_chain_id = (
            str(self.state.workflow_state.current_query_chain_id or "").strip() or None
        )
        if previous_query_chain_id is None:
            for item in reversed(self.state.workflow_state.query_chain_history):
                if not isinstance(item, dict):
                    continue
                candidate_id = str(item.get("query_chain_id", "") or "").strip()
                if candidate_id:
                    previous_query_chain_id = candidate_id
                    break
        counters = self.state.workflow_state.stage_attempt_counters
        attempt = counters.get(stage.value, 0) + 1
        counters[stage.value] = attempt
        self.state.workflow_state.current_stage = stage
        self.state.workflow_state.current_stage_attempt = attempt
        active_handoff = self.state.feedback_state.active_handoff
        if not (
            active_handoff.status == "pending"
            and active_handoff.consumer_stage == stage.value
            and active_handoff.consumer_attempt == attempt
        ):
            self.state.feedback_state.active_handoff = FeedbackHandoffState()
        if stage == Stage.ANALYSIS_DECISION:
            self.state.feedback_state.analysis_transition = AnalysisTransitionState()
        reset_critic_loop(self.state.feedback_state)
        self._activate_revisit_context_for_current_attempt(stage=stage, attempt=attempt)
        self.retain_live_tool_results_for_scope(
            stage=stage.value,
            attempt=attempt,
        )
        self.start_query_chain(
            stage=stage,
            attempt=attempt,
            parent_query_chain_id=previous_query_chain_id,
            reason="stage_attempt_start",
            source="state_store.enter_stage_attempt",
        )
        from ownpi.runtime.rollout.stage_business_events import emit_stage_attempt_started

        emit_stage_attempt_started(
            self.run_event_store,
            run_id=str(self.state.run_metadata.run_id),
            stage=stage.value,
            attempt=attempt,
            query_chain_id=str(self.state.workflow_state.current_query_chain_id or ''),
            query_chain_depth=int(self.state.workflow_state.current_query_chain_depth or 0),
        )
        self._touch()
        return attempt

    def resume_stage_attempt(
        self,
        stage: Stage,
        *,
        attempt: int,
        query_chain_id: str | None = None,
        query_chain_depth: int | None = None,
        continuation_index: int = 0,
    ) -> None:
        self.state.workflow_state.current_stage = stage
        self.state.workflow_state.current_stage_attempt = attempt
        self.retain_live_tool_results_for_scope(
            stage=stage.value,
            attempt=attempt,
        )
        self._activate_revisit_context_for_current_attempt(stage=stage, attempt=attempt)
        explicit_query_chain_id = str(query_chain_id or "").strip()
        if explicit_query_chain_id:
            workflow_state = self.state.workflow_state
            workflow_state.current_query_chain_id = explicit_query_chain_id
            workflow_state.current_query_chain_depth = int(query_chain_depth or 0)
            workflow_state.parent_query_chain_id = None
        else:
            self.start_query_chain(
                stage=stage,
                attempt=attempt,
                reason="stage_attempt_resume_without_explicit_query_identity",
                source="state_store.resume_stage_attempt",
            )
        self._touch()

    def record_revisit_transition(
        self,
        *,
        source_stage: Stage,
        source_attempt: int,
        target_stage: Stage,
        target_attempt: int,
        decision: GateDecision,
        reason: str | None,
        revisit_contract: dict[str, Any],
    ) -> None:
        contract_payload = dict(revisit_contract)
        workflow_state = self.state.workflow_state
        existing_contract = workflow_state.revisit_state.pending_revisit_contract
        contract_id = str(contract_payload.get("contract_id", "") or "")
        if not contract_id:
            raise ValueError("revisit_contract.contract_id is required")
        if (
            contract_id
            and isinstance(existing_contract, dict)
            and existing_contract.get("contract_id") == contract_id
        ):
            return
        event = {
            "source_stage": source_stage.value,
            "source_attempt": int(source_attempt or 0),
            "source_stage_run_id": workflow_state.last_stage_run_id,
            "target_stage": target_stage.value,
            "target_attempt": int(target_attempt or 0),
            "decision": decision.value,
            "reason": reason,
            "revisit_contract": contract_payload,
            "created_at": now_utc_iso(),
        }
        revisit_state = workflow_state.revisit_state
        revisit_state.pending_target_stage = target_stage.value
        revisit_state.pending_target_attempt = int(target_attempt or 0)
        revisit_state.pending_source_stage = source_stage.value
        revisit_state.pending_source_attempt = int(source_attempt or 0)
        revisit_state.pending_source_stage_run_id = workflow_state.last_stage_run_id
        revisit_state.pending_decision = decision.value
        revisit_state.pending_reason = reason
        revisit_state.pending_revisit_contract = contract_payload
        revisit_state.latest_revisit_event = dict(event)
        revisit_state.history.append(dict(event))
        del revisit_state.history[:-32]
        revisit_state.total_revisits += 1
        revisit_state.stage_revisit_counters[target_stage.value] = (
            revisit_state.stage_revisit_counters.get(target_stage.value, 0) + 1
        )
        workflow_state.scratchpad["latest_revisit_transition"] = dict(event)
        from ownpi.runtime.rollout.stage_business_events import emit_revisit

        emit_revisit(
            self.run_event_store,
            contract_id=contract_id,
            payload=event,
        )
        self.record_stage_event(
            event_type="revisit_transition",
            stage=source_stage,
            attempt=source_attempt,
            payload=event,
        )
        self._touch()

    def _activate_revisit_context_for_current_attempt(
        self, *, stage: Stage, attempt: int
    ) -> None:
        context = self.state.workflow_state.revisit_state.current_context_for(
            stage=stage, attempt=attempt
        )
        if context is None:
            self.state.workflow_state.scratchpad.pop("active_revisit_context", None)
            return
        self.state.workflow_state.scratchpad["active_revisit_context"] = context

    def build_agent_input(
        self,
        agent_config: AgentInstanceConfig,
        *,
        operation: str,
        current_request: str | None = None,
        internal_memory_map_contribution: MemoryMapSection | None = None,
        external_memory_map_contribution: MemoryMapSection | None = None,
    ) -> AgentInput:
        return build_agent_input(
            self.state,
            agent_config,
            operation=operation,
            current_request=current_request,
            project_root=self.project_root,
            state_store=self,
            app_paths=self.app_paths,
            internal_memory_map_contribution=internal_memory_map_contribution,
            external_memory_map_contribution=external_memory_map_contribution,
            runtime_entry_context=self.runtime_entry_context,
        )

    def current_human_clarification_session(self) -> HumanClarificationSession | None:
        return self.state.workflow_state.pending_human_clarification

    def current_controller_clarification_handoff(self) -> dict[str, Any] | None:
        return active_controller_clarification_handoff(
            self.state.workflow_state.scratchpad.get(
                CONTROLLER_CLARIFICATION_HANDOFF_KEY
            )
        )

    def set_controller_clarification_handoff(self, payload: dict[str, Any]) -> None:
        active = active_controller_clarification_handoff(payload)
        if active is None:
            self.state.workflow_state.scratchpad.pop(
                CONTROLLER_CLARIFICATION_HANDOFF_KEY, None
            )
        else:
            self.state.workflow_state.scratchpad[
                CONTROLLER_CLARIFICATION_HANDOFF_KEY
            ] = active
        self._touch()

    def clear_controller_clarification_handoff(self) -> None:
        handoff = self.current_controller_clarification_handoff()
        if self.run_event_store is not None and handoff is not None:
            session_id = str(handoff.get('session_id') or '')
            current = project_interactions(self.run_event_store.events).get(session_id)
            if current is not None and current.closed_event_id is None:
                try:
                    close_interaction(
                        self.run_event_store, session_id=session_id,
                        reason='controller_handoff_cleared',
                    )
                except OSError:
                    # The run writer retains the same closure for storage retry.
                    # Clearing workflow bookkeeping is not model delivery evidence.
                    logging.getLogger(__name__).warning(
                        'Controller handoff closure persistence unconfirmed', exc_info=True,
                    )
                    if getattr(self.run_event_store, 'closed', False):
                        return
        if (
            self.state.workflow_state.scratchpad.pop(
                CONTROLLER_CLARIFICATION_HANDOFF_KEY, None
            )
            is not None
        ):
            self._touch()

    def mark_controller_clarification_handoff_result_consumed(
        self,
        *,
        session_id: str,
        consumed_by_continuation_index: int,
        consumed_at: str | None = None,
    ) -> None:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return
        resolved_consumed_at = consumed_at or now_utc_iso()
        for index in range(len(self.state.tool_state.last_tool_results) - 1, -1, -1):
            result = self.state.tool_state.last_tool_results[index]
            if str(result.tool_name or "").strip() != "human_clarification":
                continue
            metadata = dict(result.metadata)
            if str(metadata.get("origin_kind", "") or "") != "controller":
                continue
            if str(metadata.get("session_id", "") or "") != normalized_session_id:
                continue
            if str(
                metadata.get("consumption_status", "") or ""
            ) == "consumed" and metadata.get("consumed_at"):
                return
            data = dict(result.data or {})
            consumed_payload = {
                "consumption_status": "consumed",
                "consumed_at": resolved_consumed_at,
                "consumed_by_continuation_index": int(
                    consumed_by_continuation_index or 0
                ),
            }
            self.state.tool_state.last_tool_results[index] = replace(
                result,
                data={
                    **data,
                    **consumed_payload,
                },
                metadata={
                    **metadata,
                    **consumed_payload,
                },
            )
            self._touch()
            return

    def current_tool_permission_session(self) -> ToolPermissionSession | None:
        with self._approval_lock:
            return next(iter(self._live_tool_approvals.values()), None)

    @contextmanager
    def live_tool_approval(self, session: ToolPermissionSession) -> Iterator[None]:
        try:
            with self._approval_lock:
                self._live_tool_approvals[session.session_id] = session
                self.record_pending_tool_permission_session(session)
            yield
        finally:
            with self._approval_lock:
                finished = self._live_tool_approvals.pop(session.session_id)
                if finished.status == 'pending':
                    finished = replace(finished, status='cancelled')
                self.state.workflow_state.tool_permission_history.append(finished)
                self.state.workflow_state.pending_tool_permission = next(
                    iter(self._live_tool_approvals.values()), None,
                )
                if not self._live_tool_approvals and self.state.workflow_state.pending_human_clarification is None:
                    self.state.workflow_state.pause_reason = None

    def live_tool_approval_answer(self, session_id: str) -> ToolPermissionSession | None:
        with self._approval_lock:
            session = self._live_tool_approvals.get(session_id)
            return session if session is not None and session.status != 'pending' else None

    def record_runtime_event(
        self,
        *,
        event_name: str,
        category: str,
        status: str,
        stage: str = "",
        attempt: int = 0,
        state_domain: str = "",
        mutation_kind: str = "",
        mutation_refs: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
        caused_by_event_id: str | None = None,
        before_payload: Any | None = None,
        after_payload: Any | None = None,
        replay_policy: str = "replay_as_observation",
        inspect_summary: str = "",
        query_chain_id: str = "",
        query_chain_depth: int = 0,
        continuation_index: int = 0,
        tool_name: str | None = None,
        request_id: str | None = None,
        session_id: str | None = None,
        payload: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> None:
        context_state = self.state.context_state
        effective_query_chain_id = str(
            query_chain_id
            or self.state.workflow_state.current_query_chain_id
            or ''
        ).strip()
        effective_query_chain_depth = (
            int(query_chain_depth or 0)
            if query_chain_depth
            else int(self.state.workflow_state.current_query_chain_depth or 0)
        )
        sequence = max(
            context_state.runtime_event_count + 1,
            next_runtime_event_sequence(context_state.recent_runtime_events),
        )
        effective_state_domain = normalize_state_domain(
            state_domain or _runtime_domain_from_category(category)
        )
        effective_mutation_kind = normalize_mutation_kind(
            mutation_kind
            or _mutation_kind_from_event_name(event_name, status=status)
        )
        effective_turn_id = effective_query_chain_id or str(
            self.state.run_metadata.run_id or ''
        ).strip()
        refs = tuple(
            dict(item) for item in mutation_refs if isinstance(item, dict)
        )
        event_payload = dict(payload or {})
        effective_created_at = created_at or now_utc_iso()
        event = RuntimeEventRecord(
            event_id=build_turn_event_id(
                sequence=sequence,
                event_name=event_name,
                request_id=request_id or '',
                session_id=session_id or '',
                tool_name=tool_name or effective_created_at,
                scope_id=(
                    f'{stage or self.state.workflow_state.current_phase}:'
                    f'a{int(attempt or 0)}:{effective_query_chain_id}:'
                    f'c{int(continuation_index or 0)}'
                ),
            ),
            event_name=event_name,
            category=category,
            status=status,
            stage=stage,
            attempt=int(attempt or 0),
            turn_id=effective_turn_id,
            sequence=sequence,
            state_domain=effective_state_domain,
            mutation_kind=effective_mutation_kind,
            mutation_refs=refs,
            caused_by_event_id=(
                str(caused_by_event_id).strip()
                if caused_by_event_id is not None
                else None
            ),
            before_hash=stable_payload_hash(before_payload),
            after_hash=stable_payload_hash(after_payload),
            replay_policy=str(replay_policy or 'replay_as_observation'),
            inspect_summary=(
                str(inspect_summary).strip()
                if str(inspect_summary or '').strip()
                else summarize_mutation_event(event_payload)
            ),
            query_chain_id=effective_query_chain_id,
            query_chain_depth=effective_query_chain_depth,
            continuation_index=int(continuation_index or 0),
            tool_name=tool_name,
            request_id=request_id,
            session_id=session_id,
            payload=event_payload,
            created_at=effective_created_at,
        )
        context_state.record_runtime_event(event)
        self._touch()

    def record_state_mutation(
        self,
        *,
        state_domain: str,
        event_name: str,
        status: str,
        mutation_kind: str = "state_delta",
        category: str | None = None,
        **kwargs: Any,
    ) -> None:
        self.record_runtime_event(
            event_name=event_name,
            category=category or state_domain,
            status=status,
            state_domain=state_domain,
            mutation_kind=mutation_kind,
            **kwargs,
        )

    def record_environment_mutation(
        self,
        *,
        event_name: str,
        status: str,
        mutation_kind: str = "state_delta",
        **kwargs: Any,
    ) -> None:
        self.record_state_mutation(
            state_domain="environment",
            event_name=event_name,
            status=status,
            mutation_kind=mutation_kind,
            **kwargs,
        )

    def record_execution_contract_compiled(
        self,
        contract: dict[str, Any] | Any,
        *,
        status: str,
        stage: str = "",
        attempt: int = 0,
        query_chain_id: str = "",
        query_chain_depth: int = 0,
        continuation_index: int = 0,
        request_id: str = "",
        tool_name: str = "",
        contract_hash: str = "",
        exec_policy_decision: dict[str, Any] | Any | None = None,
        exec_policy_hash_value: str = "",
        effective_permissions: Any | None = None,
    ) -> None:
        execution_contract_writer.record_execution_contract_compiled(
            self,
            contract,
            status=status,
            stage=stage,
            attempt=attempt,
            query_chain_id=query_chain_id,
            query_chain_depth=query_chain_depth,
            continuation_index=continuation_index,
            request_id=request_id,
            tool_name=tool_name,
            contract_hash=contract_hash,
            exec_policy_decision=exec_policy_decision,
            exec_policy_hash_value=exec_policy_hash_value,
            effective_permissions=effective_permissions,
        )

    def record_exec_policy_decision(
        self,
        decision: dict[str, Any] | Any | None,
        *,
        status: str,
        stage: str = "",
        attempt: int = 0,
        query_chain_id: str = "",
        query_chain_depth: int = 0,
        continuation_index: int = 0,
        request_id: str = "",
        tool_name: str = "",
        policy_hash: str = "",
    ) -> None:
        execution_contract_writer.record_exec_policy_decision(
            self,
            decision,
            status=status,
            stage=stage,
            attempt=attempt,
            query_chain_id=query_chain_id,
            query_chain_depth=query_chain_depth,
            continuation_index=continuation_index,
            request_id=request_id,
            tool_name=tool_name,
            policy_hash=policy_hash,
        )


    def record_mcp_mutation(
        self,
        *,
        event_name: str,
        status: str,
        mutation_kind: str = "state_delta",
        **kwargs: Any,
    ) -> None:
        self.record_state_mutation(
            state_domain="mcp",
            event_name=event_name,
            status=status,
            mutation_kind=mutation_kind,
            **kwargs,
        )

    def record_runtime_mutation(
        self,
        *,
        event_name: str,
        status: str,
        mutation_kind: str = "state_delta",
        **kwargs: Any,
    ) -> None:
        self.record_state_mutation(
            state_domain="runtime",
            event_name=event_name,
            status=status,
            mutation_kind=mutation_kind,
            **kwargs,
        )

    def start_query_chain(
        self,
        *,
        stage: Stage,
        attempt: int,
        parent_query_chain_id: str | None = None,
        reason: str,
        source: str,
    ) -> dict[str, Any]:
        workflow_state = self.state.workflow_state
        siblings = [
            item
            for item in workflow_state.query_chain_history
            if str(item.get("stage", "")) == stage.value
            and int(item.get("attempt", 0) or 0) == int(attempt or 0)
        ]
        query_chain_id = make_query_chain_id(
            stage=stage, attempt=attempt, ordinal=len(siblings) + 1
        )
        query_chain_depth = query_chain_depth_for_parent(
            workflow_state.query_chain_history,
            parent_query_chain_id,
        )
        record = build_query_chain_record(
            query_chain_id=query_chain_id,
            stage=stage,
            attempt=attempt,
            query_chain_depth=query_chain_depth,
            parent_query_chain_id=parent_query_chain_id,
            reason=reason,
            source=source,
        )
        workflow_state.current_query_chain_id = query_chain_id
        workflow_state.current_query_chain_depth = query_chain_depth
        workflow_state.parent_query_chain_id = parent_query_chain_id
        workflow_state.query_chain_history.append(record)
        self.record_runtime_event(
            event_name="query_chain_started",
            category="context",
            status="started",
            state_domain="context",
            mutation_kind="request_created",
            stage=stage.value,
            attempt=attempt,
            query_chain_id=query_chain_id,
            query_chain_depth=query_chain_depth,
            continuation_index=0,
            payload=dict(record),
        )
        self._touch()
        return record

    def record_pending_human_clarification_session(
        self, session: HumanClarificationSession
    ) -> None:
        if self.run_event_store is not None:
            with self._clarification_lock:
                current = self.state.workflow_state.pending_human_clarification
                publish_request(self.run_event_store, session)
                self.state.workflow_state.pending_human_clarification = session
                self.state.workflow_state.pause_reason = 'human_clarification'
                if current is None or current.session_id != session.session_id:
                    self.record_workflow_event(HistoryEvent(
                        event_type='human_clarification_requested', phase=session.stage,
                        status=session.status, timestamp=session.created_at or now_utc_iso(),
                        round_index=session.attempt or None,
                        artifact_type='human_clarification_session', payload=session.to_dict(),
                    ))
                self._touch()
            return
        raise RuntimeError('Human clarification requires a rollout event store')

    def record_pending_tool_permission_session(
        self, session: ToolPermissionSession
    ) -> None:
        self.state.workflow_state.pending_tool_permission = session
        self.state.workflow_state.pause_reason = "tool_permission"
        self.record_workflow_event(
            HistoryEvent(
                event_type="tool_permission_requested",
                phase=session.stage or self.state.workflow_state.current_phase,
                status=session.status,
                timestamp=session.created_at or now_utc_iso(),
                round_index=session.attempt or None,
                artifact_type="tool_permission_session",
                payload=session.to_dict(),
            )
        )
        self.record_runtime_event(
            event_name="tool_permission_requested",
            category="interaction",
            status=session.status,
            state_domain="permission",
            mutation_kind="request_created",
            stage=session.stage,
            attempt=session.attempt,
            query_chain_id=session.query_chain_id,
            query_chain_depth=session.query_chain_depth,
            continuation_index=session.continuation_index,
            tool_name=session.tool_name,
            request_id=session.request_id,
            session_id=session.session_id,
            payload=session.to_dict(),
            created_at=session.created_at,
        )
        self._touch()

    def submit_human_clarification_answers(
        self,
        *,
        session_id: str,
        answers: dict[str, Any],
        answered_at: str,
    ) -> HumanClarificationSession:
        if self.run_event_store is not None:
            with self._clarification_lock:
                current = self.state.workflow_state.pending_human_clarification
                if current is None or current.session_id != session_id:
                    raise ValueError(f'Active human clarification session not found: {session_id}')
                updated = accept_answer(
                    self.run_event_store, session_id=session_id,
                    answers=answers, answered_at=answered_at,
                )
                self.state.workflow_state.pending_human_clarification = updated
                if current.status == 'pending':
                    self.record_workflow_event(HistoryEvent(
                        event_type='human_clarification_answered', phase=updated.stage,
                        status='answered', timestamp=updated.answered_at,
                        round_index=updated.attempt or None,
                        artifact_type='human_clarification_answers', payload=updated.to_dict(),
                    ))
                    summary = '; '.join(
                        f'{question.question} => {human_answer_text(updated.answers.get(question.question_id))}'
                        for question in updated.questions
                        if human_answer_text(updated.answers.get(question.question_id, {}))
                    )
                    if summary:
                        self.state.tool_state.recent_observations.append(ToolObservation(
                            observation_id=f'human_clarification_answer:{session_id}',
                            source_type='human_clarification', source_name='human',
                            stage=updated.stage, attempt=updated.attempt,
                            query_chain_id=updated.query_chain_id,
                            query_chain_depth=updated.query_chain_depth,
                            continuation_index=updated.continuation_index,
                            summary=summary, created_at=updated.answered_at,
                        ))
                self._touch()
                return updated
        raise RuntimeError('Human clarification answers require a rollout event store')

    def close_human_clarification(self, *, session_id: str, reason: str) -> None:
        """Explicit cancellation/end, never an assertion of history delivery."""
        with self._clarification_lock:
            if self.run_event_store is None:
                raise RuntimeError('Clarification closure requires a run event store')
            close_interaction(self.run_event_store, session_id=session_id, reason=reason)
            session = self.state.workflow_state.pending_human_clarification
            if session is not None and session.session_id == session_id:
                self.state.workflow_state.pending_human_clarification = None
                if self.state.workflow_state.pending_tool_permission is None:
                    self.state.workflow_state.pause_reason = None
                self._touch()

    def submit_tool_permission_decision(
        self,
        *,
        session_id: str,
        decision: str,
        answered_at: str,
        scope: str = "once",
    ) -> ToolPermissionSession:
        with self._approval_lock:
            return self._submit_tool_permission_decision(
                session_id=session_id, decision=decision, answered_at=answered_at, scope=scope,
            )

    def _submit_tool_permission_decision(
        self, *, session_id: str, decision: str, answered_at: str, scope: str,
    ) -> ToolPermissionSession:
        session = self._live_tool_approvals.get(session_id)
        if session is None or session.session_id != session_id:
            raise RuntimeError(
                f"Active tool permission session not found: {session_id}"
            )
        if session.status != "pending":
            raise RuntimeError(
                f"Tool permission session is not pending: {session.status}"
            )
        normalized_decision = _normalized_tool_permission_decision(decision)
        if normalized_decision not in {"approve", "deny", "deny_command"}:
            raise RuntimeError(f"Unsupported tool permission decision: {decision}")
        normalized_scope = _normalize_tool_permission_scope(scope)
        if (
            normalized_decision in {"deny", "deny_command"}
            and normalized_scope != "once"
        ):
            raise RuntimeError("Deny decisions only support scope=once.")
        if normalized_scope not in {"once", "session"}:
            raise RuntimeError(
                "Permission decisions only support once or bounded session scope."
            )
        updated_session = replace(
            session,
            status="approved" if normalized_decision == "approve" else "denied",
            decision=normalized_decision,
            decision_scope=normalized_scope,
            approved_overlay=(
                dict(session.approval_request.get("requested_overlay", {}))
                if normalized_decision == "approve"
                and isinstance(session.approval_request.get("requested_overlay"), dict)
                else None
            ),
            answered_at=answered_at,
        )
        if normalized_decision == "approve" and normalized_scope == "session":
            approval_request = updated_session.approval_request
            overlay = approval_request.get("requested_overlay")
            if not isinstance(overlay, dict):
                raise RuntimeError(
                    "Approved session permission is missing a canonical overlay."
                )
            exact_input_required = (
                not overlay.get("file_system_entries")
                or overlay.get("network_enabled") is True
            )
            command_prefix = tuple(
                str(item)
                for item in approval_request.get("command_prefix", [])
                if str(item).strip()
            )
            from ownpi.runtime.permissions.session_identity import read_session_components, read_session_identity

            session_identity = read_session_identity(approval_request)
            match_kind = session_identity['session_match_kind']
            match_version = session_identity['session_match_version']
            match_digest = session_identity['session_match_digest']
            match_components = read_session_components(approval_request)
            tool_name = str(approval_request.get("tool_name", "") or "")
            is_typed_operation = (
                tool_name in {"exec_command", "patch_apply", "mcp_tool_invoke"}
                or tool_name.startswith("mcp__")
            )
            run_metadata = self.state.run_metadata
            grant_run_id = str(run_metadata.run_id or "")
            if updated_session.actor_kind == "subagent":
                if not updated_session.actor_id:
                    raise RuntimeError("Subagent session permission requires a child identity.")
                grant_run_id = updated_session.actor_id
            if is_typed_operation and (
                not match_kind or (match_kind == 'patch_path_exact' and not match_components)
            ):
                # Approval still applies to this invocation, but is not stored.
                grants = []
            else:
                grant_specs = (
                    [(f'{match_digest[:12]}:{component[:12]}', component) for component in match_components]
                    if match_kind == 'patch_path_exact' and match_components
                    else [('operation', match_digest)]
                )
                grants = [SessionPermissionGrant(
                    grant_id=f"session-grant:{updated_session.session_id}:{identity_suffix}",
                    run_id=grant_run_id,
                    runtime_session_id=str(run_metadata.session_id or ""),
                    task_id=str(run_metadata.task_id or "task"),
                    tool_name=str(approval_request.get("tool_name", "") or ""),
                    action=str(approval_request.get("action", "") or ""),
                    environment_hash=str(
                        approval_request.get("environment_hash", "") or ""
                    ),
                    base_policy_hash=str(
                        approval_request.get("base_policy_hash", "") or ""
                    ),
                    reviewer=updated_session.reviewer,
                    approved_overlay=dict(overlay),
                    exact_input_hash=(
                        str(approval_request.get("normalized_input_hash", "") or "")
                        if not is_typed_operation and exact_input_required and not command_prefix
                        else None
                    ),
                    command_prefix=command_prefix,
                    created_at=answered_at,
                    session_match_kind=match_kind,
                    session_match_version=match_version,
                    session_match_digest=identity_digest,
                ) for identity_suffix, identity_digest in grant_specs]
            if grants:
                grant_ids = {item.grant_id for item in grants}
                self.state.tool_state.session_permission_grants = [
                    item
                    for item in self.state.tool_state.session_permission_grants
                    if item.grant_id not in grant_ids
                ] + grants
        self.state.workflow_state.pending_tool_permission = updated_session
        if session_id in self._live_tool_approvals:
            self._live_tool_approvals[session_id] = updated_session
        self.record_tool_permission_event(
            ToolPermissionEvent(
                event_id=f"permission-answer:{session_id}",
                request_id=updated_session.request_id,
                tool_name=updated_session.tool_name,
                permission_class=updated_session.permission_class,
                behavior="ask",
                granted=(normalized_decision == "approve"),
                base_permission_class=updated_session.base_permission_class,
                destructive_action=updated_session.destructive_action,
                destructive_reason=updated_session.destructive_reason,
                destructive_kinds=updated_session.destructive_kinds,
                reason=_tool_permission_answer_reason(normalized_decision),
                stage=updated_session.stage,
                attempt=updated_session.attempt,
                query_chain_id=updated_session.query_chain_id,
                query_chain_depth=updated_session.query_chain_depth,
                continuation_index=updated_session.continuation_index,
                requested_by=updated_session.requested_by,
                session_id=updated_session.session_id,
                resolution=normalized_decision,
                decision_scope=normalized_scope,
                created_at=answered_at,
                answered_at=answered_at,
            )
        )
        self.record_tool_observation(
            ToolObservation(
                observation_id=f"tool_permission_answer:{session_id}",
                source_type="permission_answer",
                source_name=updated_session.tool_name,
                stage=updated_session.stage,
                attempt=updated_session.attempt,
                query_chain_id=updated_session.query_chain_id,
                query_chain_depth=updated_session.query_chain_depth,
                continuation_index=updated_session.continuation_index,
                summary=(
                    f"human {_tool_permission_answer_verb(normalized_decision)} "
                    f"{updated_session.tool_name} ({updated_session.permission_class}) scope={normalized_scope}"
                ),
                created_at=answered_at,
            )
        )
        if normalized_decision == "deny_command":
            self.record_denied_tool_command_resume_guidance(
                session=updated_session,
                created_at=answered_at,
            )
        self.record_workflow_event(
            HistoryEvent(
                event_type="tool_permission_answered",
                phase=updated_session.stage or self.state.workflow_state.current_phase,
                status=updated_session.status,
                timestamp=answered_at,
                round_index=updated_session.attempt or None,
                artifact_type="tool_permission_session",
                payload=updated_session.to_dict(),
            )
        )
        self.record_runtime_event(
            event_name="tool_permission_answered",
            category="interaction",
            status=updated_session.status,
            stage=updated_session.stage,
            attempt=updated_session.attempt,
            query_chain_id=updated_session.query_chain_id,
            query_chain_depth=updated_session.query_chain_depth,
            continuation_index=updated_session.continuation_index,
            tool_name=updated_session.tool_name,
            request_id=updated_session.request_id,
            session_id=updated_session.session_id,
            payload=updated_session.to_dict(),
            created_at=answered_at,
        )
        self._touch()
        return updated_session

    def record_denied_tool_command_resume_guidance(
        self,
        *,
        session: ToolPermissionSession,
        created_at: str | None = None,
    ) -> ToolObservation | None:
        return interaction_state_writer.record_denied_tool_command_resume_guidance(
            self,
            session=session,
            created_at=created_at,
        )

    def mark_human_clarification_resumed(
        self,
        *,
        session_id: str,
        resumed_at: str,
    ) -> HumanClarificationSession | None:
        if self.run_event_store is not None:
            with self._clarification_lock:
                session = self.state.workflow_state.pending_human_clarification
                if session is None or session.session_id != session_id:
                    return None
                if session.status != 'answered':
                    raise ValueError('Only an answered clarification can resume')
                resumed = replace(session, status='resumed', resumed_at=resumed_at)
                self.state.workflow_state.pending_human_clarification = None
                self.state.workflow_state.human_clarification_history.append(resumed)
                self.state.workflow_state.clarification_resume_count += 1
                if self.state.workflow_state.pending_tool_permission is None:
                    self.state.workflow_state.pause_reason = None
                self._touch()
                # Resume bookkeeping is not durable history delivery evidence.
                return resumed
        raise RuntimeError('Human clarification resume requires a rollout event store')

    def archive_answered_human_clarification(
        self,
        *,
        session_id: str,
    ) -> HumanClarificationSession | None:
        return interaction_state_writer.archive_answered_human_clarification(
            self,
            session_id=session_id,
        )


    def record_agent_output(
        self,
        *,
        stage: Stage,
        attempt: int,
        agent_config: AgentInstanceConfig,
        agent_output: AgentOutput,
        file_path: str | None = None,
    ) -> dict[str, Any] | None:
        serialized = _serialize_agent_payload(agent_output.payload)
        event_payload = serialized
        if agent_output.artifact_type in {"stage_feedback", "stage_review"}:
            record = FeedbackRecord.from_dict(serialized)
            current_ref = self.state.feedback_state.record_ref(record.record_id)
            if current_ref is None:
                raise ValueError(
                    "feedback output is missing its canonical metadata ref"
                )
            from ownpi.runtime.stage_execution.feedback_store import (
                load_feedback_record,
            )

            current, _ = load_feedback_record(
                current_ref, run_dir=self.run_event_store.path.parent.parent,
            )
            if current != record:
                raise ValueError(
                    "feedback output does not match its canonical metadata record"
                )
            event_payload = {"feedback_record_ref": current_ref.to_dict()}
        else:
            artifact_ref = self._append_artifact_record(
                stage=stage,
                attempt=attempt,
                agent_config=agent_config,
                artifact_type=agent_output.artifact_type,
                payload=serialized,
                summary=agent_output.summary,
                file_path=file_path,
            )
            event_payload = {
                "artifact_ref": artifact_ref.to_dict(),
            }
        if agent_output.artifact_type not in {"stage_feedback", "stage_review"}:
            self.state.workflow_state.scratchpad[
                f"{agent_config.id}:{agent_output.artifact_type}"
            ] = serialized
        self.record_workflow_event(
            HistoryEvent(
                event_type="agent_output",
                phase=self.state.workflow_state.current_phase,
                status=agent_output.status,
                timestamp=now_utc_iso(),
                round_index=attempt,
                agent_id=agent_config.id,
                role=agent_config.role,
                artifact_type=agent_output.artifact_type,
                payload=event_payload,
                file_path=file_path,
            )
        )
        context_update = self._record_context_trace(agent_output)
        tool_update = self._record_tool_trace(agent_output)
        self._touch()
        messages: list[str] = []
        for update in (context_update, tool_update):
            if not isinstance(update, dict):
                continue
            raw_message = update.get("message")
            if isinstance(raw_message, str) and raw_message.strip():
                messages.append(raw_message.strip())
        if not messages:
            return None
        return {"messages": messages}

    def record_stage_event(
        self,
        *,
        event_type: str,
        stage: Stage,
        attempt: int,
        payload: dict[str, Any] | None = None,
        status: str = "success",
    ) -> None:
        self.record_workflow_event(
            HistoryEvent(
                event_type=event_type,
                phase=stage.value,
                status=status,
                timestamp=now_utc_iso(),
                round_index=attempt,
                artifact_type=event_type,
                payload=payload or {},
            )
        )
        self._touch()

    def record_gate_outcome(
        self,
        *,
        stage: Stage,
        attempt: int,
        gate_trace: GateTrace,
    ) -> None:
        branch_id = self.state.workflow_state.current_branch_id
        stage_run_id = f"{branch_id}:{stage.value}:attempt{attempt}"
        from ownpi.runtime.rollout.stage_business_events import emit_stage_gate

        emit_stage_gate(
            self.run_event_store,
            stage=stage.value,
            attempt=attempt,
            stage_run_id=stage_run_id,
            gate=gate_trace.to_dict(),
        )
        self.state.workflow_state.last_stage_run_id = stage_run_id
        self.record_workflow_event(
            HistoryEvent(
                event_type="gate_decision",
                phase=stage.value,
                status=gate_trace.terminal_status or "success",
                timestamp=now_utc_iso(),
                round_index=attempt,
                artifact_type="gate_trace",
                payload=gate_trace.to_dict(),
                file_path=None,
            )
        )
        self._backfill_latest_revisit_source_run_id(stage_run_id=stage_run_id)
        self._clear_completed_active_revisit_context(
            stage=stage,
            attempt=attempt,
            gate_trace=gate_trace,
            stage_run_id=stage_run_id,
        )
        self._touch()
        return None

    def _stage_evidence_artifact_refs(
        self,
        *,
        stage: Stage,
        attempt: int,
    ) -> tuple[ArtifactRef, ...]:
        refs: list[ArtifactRef] = []
        seen: set[str] = set()
        for artifact in self.state.artifact_state.recent_artifacts:
            context = artifact.stage_context
            if (
                str(context.get("stage", "")) != stage.value
                or int(context.get("attempt", 0) or 0) != attempt
                or artifact.artifact_id in seen
            ):
                continue
            seen.add(artifact.artifact_id)
            refs.append(artifact.to_ref())
        return tuple(refs)

    def _clear_completed_active_revisit_context(
        self,
        *,
        stage: Stage,
        attempt: int,
        gate_trace: GateTrace,
        stage_run_id: str,
    ) -> None:
        revisit_state = self.state.workflow_state.revisit_state
        active_context = revisit_state.current_context_for(stage=stage, attempt=attempt)
        if active_context is None:
            return
        if (
            not is_completed_gate_decision(gate_trace.decision)
            and gate_trace.decision != GateDecision.FAIL
        ):
            return
        review_ref = self.state.feedback_state.record_ref(
            self.state.feedback_state.critic_loop.feedback_record_id
        )
        target_response_summary = (
            {
                "review_record_id": review_ref.record_id,
                "review_round": self.state.feedback_state.critic_loop.round,
                "review_record_ref": review_ref.to_dict(),
            }
            if review_ref is not None
            else {}
        )
        cleared_event = {
            "event_type": "revisit_contract_cleared",
            "target_stage": stage.value,
            "target_attempt": int(attempt or 0),
            "stage_run_id": stage_run_id,
            "decision": gate_trace.decision.value,
            "target_response_summary": target_response_summary,
            "cleared_at": now_utc_iso(),
            "revisit_contract_id": (
                active_context.get("revisit_contract", {}).get("contract_id")
                if isinstance(active_context.get("revisit_contract"), dict)
                else None
            ),
        }
        revisit_state.pending_target_stage = None
        revisit_state.pending_target_attempt = None
        revisit_state.pending_source_stage = None
        revisit_state.pending_source_attempt = None
        revisit_state.pending_source_stage_run_id = None
        revisit_state.pending_decision = None
        revisit_state.pending_reason = None
        revisit_state.pending_revisit_contract = None
        revisit_state.history.append(cleared_event)
        del revisit_state.history[:-32]
        self.state.workflow_state.scratchpad.pop("active_revisit_context", None)
        self.state.workflow_state.scratchpad["latest_revisit_history_trace"] = (
            cleared_event
        )

    def _backfill_latest_revisit_source_run_id(self, *, stage_run_id: str) -> None:
        revisit_state = self.state.workflow_state.revisit_state
        latest = revisit_state.latest_revisit_event
        if not isinstance(latest, dict):
            return
        if latest.get("source_stage_run_id"):
            return
        latest["source_stage_run_id"] = stage_run_id
        revisit_state.pending_source_stage_run_id = stage_run_id
        revisit_state.latest_revisit_event = latest
        if revisit_state.history:
            revisit_state.history[-1] = {
                **dict(revisit_state.history[-1]),
                "source_stage_run_id": stage_run_id,
            }
        latest_scratchpad = self.state.workflow_state.scratchpad.get(
            "latest_revisit_transition"
        )
        if isinstance(latest_scratchpad, dict) and not latest_scratchpad.get(
            "source_stage_run_id"
        ):
            latest_scratchpad["source_stage_run_id"] = stage_run_id
            self.state.workflow_state.scratchpad["latest_revisit_transition"] = (
                latest_scratchpad
            )

    def record_environment_receipt(
        self,
        receipt: EnvironmentExecutionReceipt | dict[str, Any],
        *,
        stage: str = "",
        attempt: int = 0,
        query_chain_id: str = "",
        continuation_index: int = 0,
    ) -> dict[str, Any]:
        payload = (
            receipt.to_dict()
            if isinstance(receipt, EnvironmentExecutionReceipt)
            else dict(receipt)
        )
        environment_state = self.state.environment_state
        receipt_id = str(payload.get("receipt_id", "") or "").strip()
        if receipt_id:
            for item in reversed(environment_state.latest_environment_receipts):
                if str(item.get("receipt_id", "") or "").strip() == receipt_id:
                    return dict(item)
        current_view = current_environment_receipt_view(payload)
        provider_kind = str(payload.get("provider_kind", "host_local") or "host_local")
        # This list is a one-item live projection. Full receipt history is owned
        # by the execution_observed trajectory and its durable object reference.
        environment_state.latest_environment_receipts = [current_view]
        environment_state.environment_receipt_count += 1
        environment_state.provider_counts[provider_kind] = (
            environment_state.provider_counts.get(provider_kind, 0) + 1
        )
        self.record_workflow_event(
            HistoryEvent(
                event_type="environment_receipt",
                phase=stage or self.state.workflow_state.current_phase,
                status=str(payload.get("status", "unknown") or "unknown"),
                timestamp=str(
                    payload.get("completed_at")
                    or payload.get("heartbeat_at")
                    or now_utc_iso()
                ),
                round_index=attempt or None,
                artifact_type="environment_receipt",
                payload=current_view,
            )
        )
        self.record_runtime_event(
            event_name="environment_receipt_recorded",
            category="environment",
            status=str(payload.get("status", "unknown") or "unknown"),
            state_domain="environment",
            mutation_kind="receipt_recorded",
            stage=stage or self.state.workflow_state.current_phase,
            attempt=attempt,
            query_chain_id=query_chain_id,
            continuation_index=continuation_index,
            tool_name=str(payload.get("tool_name", "") or "") or None,
            request_id=str(payload.get("request_id", "") or "") or None,
            session_id=str(payload.get("session_id", "") or "") or None,
            payload=current_view,
            created_at=str(
                payload.get("completed_at")
                or payload.get("heartbeat_at")
                or now_utc_iso()
            ),
        )
        self._touch()
        return dict(current_view)

    def retain_live_tool_results_for_scope(
        self,
        *,
        stage: str,
        attempt: int,
    ) -> None:
        """Keep only the live result projection for the active stage scope.

        The complete result body and trajectory record remain durable elsewhere.
        Results without a scope are retained because their ownership cannot be
        inferred safely from RuntimeState alone.
        """

        normalized_stage = str(stage or "").strip()
        normalized_attempt = int(attempt or 0)
        if not normalized_stage:
            return
        results = self.state.tool_state.last_tool_results
        retained = []
        for result in results:
            metadata = result.metadata if isinstance(result.metadata, dict) else {}
            result_stage = str(metadata.get("stage", "") or "").strip()
            if not result_stage:
                retained.append(result)
                continue
            result_attempt = int(metadata.get("attempt", 0) or 0)
            if result_stage == normalized_stage and result_attempt == normalized_attempt:
                retained.append(result)
        if len(retained) == len(results):
            return
        self.state.tool_state.last_tool_results = retained
        self._touch()

    def record_tool_result(
        self, tool_result: ToolResult, *, summary_override: str | None = None
    ) -> None:
        before_count = len(self.state.tool_state.last_tool_results)
        summary = summary_override or _tool_result_summary(tool_result)
        current_result = current_tool_result_projection(
            tool_result,
            summary=summary,
        )
        self.state.tool_state.last_tool_results.append(current_result)
        warning_entries = [
            str(item) for item in tool_result.warnings if str(item).strip()
        ]
        for warning in warning_entries:
            if warning not in self.state.tool_state.tool_warnings:
                self.state.tool_state.tool_warnings.append(warning)
        stage_label = str(
            tool_result.metadata.get("stage", self.state.workflow_state.current_phase)
        )
        attempt = int(
            tool_result.metadata.get(
                "attempt", self.state.workflow_state.current_stage_attempt or 0
            )
            or 0
        )
        observation = ToolObservation(
            observation_id=(
                str(tool_result.metadata.get("observation_id"))
                if tool_result.metadata.get("observation_id") is not None
                else f"tool_result:{tool_result.tool_name}:{len(self.state.tool_state.recent_observations) + 1}"
            ),
            source_type="tool_result",
            source_name=tool_result.tool_name,
            stage=stage_label,
            attempt=attempt,
            continuation_index=int(
                tool_result.metadata.get("continuation_index", 0) or 0
            ),
            summary=summary,
            payload_ref=(
                tool_result.artifacts[0].file_path
                if tool_result.artifacts
                and tool_result.artifacts[0].file_path is not None
                else None
            ),
            created_at=now_utc_iso(),
        )
        self.state.tool_state.recent_observations.append(observation)
        self.record_workflow_event(
            HistoryEvent(
                event_type="tool_result",
                phase=stage_label,
                status="success" if tool_result.ok else "failed",
                timestamp=now_utc_iso(),
                round_index=attempt or None,
                artifact_type="tool_result",
                payload=current_result.to_dict(),
            )
        )
        self.record_runtime_event(
            event_name="tool_result_recorded",
            category="tool_runtime",
            status="success" if tool_result.ok else "failed",
            state_domain="tool",
            mutation_kind="result_recorded",
            stage=stage_label,
            attempt=attempt,
            continuation_index=int(
                tool_result.metadata.get("continuation_index", 0) or 0
            ),
            tool_name=tool_result.tool_name,
            request_id=str(tool_result.metadata.get("request_id", "") or "") or None,
            before_payload={"last_tool_result_count": before_count},
            after_payload={
                "last_tool_result_count": len(self.state.tool_state.last_tool_results)
            },
            payload=current_result.to_dict(),
        )
        self._touch()

    def record_llm_usage_receipt(self, receipt: LLMUsageReceipt) -> None:
        self.state.context_state.llm_usage_receipts.append(receipt)
        snapshot = self.state.context_state.token_budget_snapshot
        if snapshot is not None:
            snapshot.provider_prompt_tokens = receipt.provider_prompt_tokens
            snapshot.provider_completion_tokens = receipt.provider_completion_tokens
            snapshot.provider_total_tokens = receipt.provider_total_tokens
            snapshot.provider_cache_read_tokens = receipt.provider_cache_read_tokens
            snapshot.provider_cache_write_tokens = receipt.provider_cache_write_tokens
            snapshot.estimate_vs_provider_delta = receipt.estimate_vs_provider_delta
            snapshot.usage_source = receipt.usage_source
        self.record_runtime_event(
            event_name="llm_usage_receipt",
            category="llm",
            status=receipt.usage_source,
            state_domain="context",
            mutation_kind="receipt_recorded",
            stage=str(receipt.scope.get("stage", "") or ""),
            attempt=int(receipt.scope.get("attempt", 0) or 0),
            continuation_index=int(receipt.scope.get("continuation_index", 0) or 0),
            payload=receipt.to_dict(),
            created_at=receipt.created_at,
        )
        self._touch()

    def record_tool_call_request(self, request: ToolCall) -> None:
        self.state.tool_state.tool_calls.append(request)
        if request.request_id not in self.state.tool_state.inflight_tool_requests:
            self.state.tool_state.inflight_tool_requests.append(request.request_id)
        self.record_workflow_event(
            HistoryEvent(
                event_type="tool_call_request",
                phase=request.stage or self.state.workflow_state.current_phase,
                status="queued",
                timestamp=request.created_at or now_utc_iso(),
                round_index=request.attempt or None,
                artifact_type="tool_call_request",
                payload=request.to_dict(),
            )
        )
        self.record_runtime_event(
            event_name="tool_call_requested",
            category="tool_runtime",
            status="queued",
            state_domain="tool",
            mutation_kind="request_created",
            stage=request.stage,
            attempt=request.attempt,
            continuation_index=request.continuation_index,
            tool_name=request.tool_name,
            request_id=request.request_id,
            payload=request.to_dict(),
            created_at=request.created_at,
        )
        self._touch()

    def record_tool_protocol_violation(
        self, record: ToolProtocolViolationRecord
    ) -> None:
        self.state.tool_state.tool_protocol_violations.append(record)
        self.record_workflow_event(
            HistoryEvent(
                event_type="tool_protocol_violation",
                phase=record.stage or self.state.workflow_state.current_phase,
                status=record.violation_kind,
                timestamp=record.created_at or now_utc_iso(),
                round_index=record.attempt or None,
                artifact_type="tool_protocol_violation",
                payload=record.to_dict(),
            )
        )
        self.record_runtime_event(
            event_name="tool_protocol_violation",
            category="tool_runtime",
            status=record.violation_kind,
            state_domain="tool",
            mutation_kind="result_recorded",
            stage=record.stage,
            attempt=record.attempt,
            continuation_index=record.continuation_index,
            tool_name=record.tool_name,
            request_id=record.request_id,
            payload=record.to_dict(),
            created_at=record.created_at,
        )
        self._touch()

    def record_tool_call_record(self, record: ToolInvocationRecord) -> None:
        self.state.tool_state.tool_call_history.append(record)
        if (
            record.request_id in self.state.tool_state.inflight_tool_requests
            and record.status
            in {
                "completed",
                "failed",
                "skipped",
                "denied",
                "tool_validation_error",
                "tool_runtime_failure",
                "tool_protocol_violation",
            }
        ):
            self.state.tool_state.inflight_tool_requests.remove(record.request_id)
        if record.warning:
            warning = str(record.warning)
            if warning not in self.state.tool_state.tool_warnings:
                self.state.tool_state.tool_warnings.append(warning)
        self.record_workflow_event(
            HistoryEvent(
                event_type="tool_call_result",
                phase=record.stage or self.state.workflow_state.current_phase,
                status=record.status,
                timestamp=record.finished_at or record.created_at or now_utc_iso(),
                round_index=record.attempt or None,
                artifact_type="tool_call_record",
                payload=record.to_dict(),
            )
        )
        self.record_runtime_event(
            event_name=(
                "tool_call_completed"
                if record.status == "completed"
                else "tool_call_failed"
                if record.status in {"failed", "denied", "skipped"}
                else "tool_call_progress"
            ),
            category="tool_runtime",
            status=record.status,
            state_domain="tool",
            mutation_kind="result_recorded",
            stage=record.stage,
            attempt=record.attempt,
            continuation_index=record.continuation_index,
            tool_name=record.tool_name,
            request_id=record.request_id,
            session_id=record.permission_session_id,
            payload=record.to_dict(),
            created_at=record.finished_at or record.created_at,
        )
        self._touch()

    def record_tool_permission_event(self, event: ToolPermissionEvent) -> None:
        self.state.tool_state.tool_permission_events.append(event)
        if event.reason:
            warning = str(event.reason)
            if warning not in self.state.tool_state.tool_warnings:
                self.state.tool_state.tool_warnings.append(warning)
        self.record_workflow_event(
            HistoryEvent(
                event_type="tool_permission_event",
                phase=event.stage or self.state.workflow_state.current_phase,
                status=event.behavior,
                timestamp=event.created_at or now_utc_iso(),
                artifact_type="tool_permission_event",
                payload=event.to_dict(),
            )
        )
        self.record_runtime_event(
            event_name="tool_permission_event",
            category="permission",
            status=event.behavior,
            state_domain="permission",
            mutation_kind="decision_recorded",
            stage=event.stage,
            attempt=event.attempt,
            continuation_index=event.continuation_index,
            tool_name=event.tool_name,
            request_id=event.request_id,
            session_id=event.session_id,
            payload=event.to_dict(),
            created_at=event.created_at,
        )
        self._touch()

    def record_tool_observation(self, observation: ToolObservation) -> None:
        self.state.tool_state.recent_observations.append(observation)
        self.record_workflow_event(
            HistoryEvent(
                event_type="tool_observation",
                phase=observation.stage or self.state.workflow_state.current_phase,
                status="success",
                timestamp=observation.created_at or now_utc_iso(),
                round_index=observation.attempt or None,
                artifact_type="tool_observation",
                payload=observation.to_dict(),
            )
        )
        mutation_ref = _tool_observation_mutation_ref(observation)
        file_event_name = _file_mutation_event_name(mutation_ref)
        self.record_runtime_event(
            event_name=file_event_name or "tool_observation_recorded",
            category="tool_runtime",
            status="recorded",
            state_domain="tool",
            mutation_kind="result_recorded" if file_event_name else "state_delta",
            mutation_refs=(mutation_ref,) if mutation_ref else (),
            stage=observation.stage,
            attempt=observation.attempt,
            continuation_index=observation.continuation_index,
            tool_name=observation.source_name,
            request_id=observation.observation_id,
            payload=observation.to_dict(),
            created_at=observation.created_at,
        )
        self._touch()
    def record_error(
        self,
        *,
        message: str,
        stage: Stage | None = None,
        attempt: int | None = None,
        agent_id: str | None = None,
    ) -> None:
        self.record_workflow_event(
            HistoryEvent(
                event_type="error",
                phase=(stage or self.state.workflow_state.current_stage).value,
                status="failed",
                timestamp=now_utc_iso(),
                round_index=attempt,
                agent_id=agent_id,
                payload={"message": message},
            )
        )
        self._touch()

    def _append_artifact_record(
        self,
        *,
        stage: Stage,
        attempt: int,
        agent_config: AgentInstanceConfig,
        artifact_type: str,
        payload: dict[str, Any],
        summary: str,
        file_path: str | None,
    ) -> ArtifactRef:
        artifact_sequence = (
            sum(
                1
                for item in self.state.artifact_state.recent_artifacts
                if item.artifact_type == artifact_type
                and item.stage_context.get("stage") == stage.value
                and int(item.stage_context.get("attempt", 0) or 0) == int(attempt or 0)
            )
            + 1
        )
        stage_context = {
            "stage": stage.value,
            "attempt": attempt,
            "agent_id": agent_config.id,
            "query_chain_id": str(
                self.state.workflow_state.current_query_chain_id or ""
            ),
            "query_chain_depth": int(
                self.state.workflow_state.current_query_chain_depth or 0
            ),
            "continuation_index": int(
                self.state.workflow_state.scratchpad.get("continuation_index", 0) or 0
            ),
            "artifact_sequence": artifact_sequence,
        }
        artifact = ArtifactRecord(
            artifact_id=self._artifact_id(
                stage=stage,
                artifact_type=artifact_type,
                attempt=attempt,
                stage_context=stage_context,
                file_path=file_path,
            ),
            artifact_type=artifact_type,
            producer_agent=agent_config.id,
            created_at=now_utc_iso(),
            source_paths=[file_path] if file_path is not None else [],
            summary=summary,
            payload=dict(payload),
            stage_context=stage_context,
        )
        self.state.artifact_state.recent_artifacts.append(artifact)
        self.state.artifact_state.artifact_index[artifact.artifact_id] = {
            "artifact_type": artifact.artifact_type,
            "producer_agent": artifact.producer_agent,
            "file_path": file_path,
            "stage": stage.value,
            "attempt": attempt,
        }
        return artifact.to_ref()

    def _record_context_trace(self, agent_output: AgentOutput) -> dict[str, Any] | None:
        context_trace = agent_output.metadata.get("context_trace")
        runtime_trace = self.state.workflow_state.scratchpad.setdefault(
            "context_runtime", {"agent_runs": []}
        )
        if isinstance(context_trace, dict):
            agent_runs = runtime_trace.setdefault("agent_runs", [])
            if isinstance(agent_runs, list):
                agent_runs.append(context_trace)
            latest_by_agent = runtime_trace.setdefault("latest_by_agent", {})
            if isinstance(latest_by_agent, dict):
                trace = AgentContextTrace.from_dict(context_trace)
                summary = trace.summary_dict()
                previous = latest_by_agent.get(trace.agent_id)
                latest_by_agent[trace.agent_id] = dict(summary)
                if previous is None:
                    return {"message": f"[ctx] {summary['inline_summary']}"}
                if (
                    isinstance(previous, dict)
                    and previous.get("event_fingerprint")
                    != summary["event_fingerprint"]
                ):
                    return {"message": f"[ctx] {summary['inline_summary']}"}
        return None

    def _record_tool_trace(self, agent_output: AgentOutput) -> dict[str, Any] | None:
        tool_trace = agent_output.metadata.get("tool_trace")
        if isinstance(tool_trace, dict):
            runtime_trace = self.state.workflow_state.scratchpad.setdefault(
                "context_runtime", {"agent_runs": []}
            )
            tool_runs = runtime_trace.setdefault("tool_runs", [])
            if isinstance(tool_runs, list):
                tool_runs.append(tool_trace)
            latest_by_agent = runtime_trace.setdefault("latest_tools_by_agent", {})
            if isinstance(latest_by_agent, dict):
                trace = ToolSelectionTrace.from_dict(tool_trace)
                summary = trace.summary_dict()
                previous = latest_by_agent.get(trace.agent_id)
                latest_by_agent[trace.agent_id] = dict(summary)
                if previous is None:
                    return {"message": f"[tool] {summary['inline_summary']}"}
                if (
                    isinstance(previous, dict)
                    and previous.get("event_fingerprint")
                    != summary["event_fingerprint"]
                ):
                    return {"message": f"[tool] {summary['inline_summary']}"}
        return None

    def _touch(self) -> None:
        self.state.run_metadata.updated_at = now_utc_iso()

    @staticmethod
    def _artifact_id(
        *,
        stage: Stage,
        artifact_type: str,
        attempt: int,
        stage_context: dict[str, Any] | None = None,
        file_path: str | None = None,
    ) -> str:
        prefix = f"{stage.value}:{artifact_type}:attempt{attempt}"
        if artifact_type != "native_tool_loop_result":
            return prefix
        context = dict(stage_context or {})
        continuation_index = int(context.get("continuation_index", 0) or 0)
        artifact_sequence = int(context.get("artifact_sequence", 0) or 0)
        hash_source = (
            f"{file_path or ''}:{context.get('query_chain_id', '')}:{artifact_sequence}"
        )
        short_hash = hashlib.sha256(hash_source.encode("utf-8")).hexdigest()[:8]
        return f"{prefix}:c{continuation_index}:n{artifact_sequence}:h{short_hash}"


AgentExecutionRecord = HistoryEvent


def _build_default_user_config() -> UserConfig:
    return UserConfig.from_dict(
        {
            "llms": {"default": {"provider": "mock", "model": "mock-neuro-core"}},
            "runtime": {
                "workspace_dir": "./workspace",
                "output_dir": "./outputs",
                "review_profile": "standard",
            },
            "agents": {
                "pipeline": [
                    {"id": "core", "role": "core", "name": "Core", "llm": "default"},
                    {
                        "id": "reviewer",
                        "role": "reviewer",
                        "name": "Reviewer",
                        "llm": "default",
                    },
                ]
            },
        }
    )


def _serialize_agent_payload(payload: object) -> dict[str, object]:
    if hasattr(payload, "to_dict"):
        return payload.to_dict()
    if isinstance(payload, dict):
        return dict(payload)
    raise TypeError(f"Unsupported agent payload type: {type(payload)!r}")


def _tool_result_summary(tool_result: ToolResult) -> str:
    return summarize_tool_result(tool_result, max_len=220)


def _tool_observation_mutation_ref(
    observation: ToolObservation,
) -> dict[str, Any] | None:
    source_type = str(observation.source_type or "").strip()
    if source_type not in {
        "file_read",
        "file_delete",
        "file_remove",
        "delete",
    }:
        return None
    ref_type = (
        "file_delete"
        if source_type in {"file_delete", "file_remove", "delete"}
        else source_type
    )
    ref_id = str(observation.payload_ref or observation.observation_id or "").strip()
    if not ref_id:
        ref_id = str(observation.summary or ref_type).strip()
    return compact_mutation_ref(
        ref_type=ref_type,
        ref_id=ref_id,
        path=str(observation.payload_ref or ""),
        summary=str(observation.summary or ref_id),
    )


def _file_mutation_event_name(mutation_ref: dict[str, Any] | None) -> str:
    if not isinstance(mutation_ref, dict):
        return ""
    ref_type = str(mutation_ref.get("ref_type", "") or "").strip()
    if ref_type not in {"file_read", "file_delete"}:
        return ""
    return f"{ref_type}_recorded"
