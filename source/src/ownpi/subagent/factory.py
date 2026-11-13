"""Runtime-owned construction of isolated subagent execution contexts."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from threading import RLock
from uuid import uuid4
from typing import Any

from ownpi.controller.stages import Stage
from ownpi.controller.runtime_control_signal import RuntimeControlRequested
from ownpi.runtime.tools.runtime import ToolRuntimeContext
from ownpi.schemas.workflow_plan import StageRuntimeProfile

from .context import SubagentContextAssembler, SubagentContextBundle
from .contracts import SubagentResult, SubagentTask
from .adapter import EngineSubagentLoopAdapter
from .native_factory import SubagentNativeLoopFactory
from .owner import SubagentOwner
from .live_state import LiveSubagentStateStore


@dataclass
class SubagentExecutionFactory:
    """Create child context bundles without sharing parent runtime state."""

    parent_context: ToolRuntimeContext
    child_root: Path
    level: StageRuntimeProfile
    stage: Stage
    memory_map: Any
    assembler: SubagentContextAssembler = SubagentContextAssembler()
    _child_contexts: dict[str, ToolRuntimeContext] = field(default_factory=dict, init=False, repr=False)
    _state_lock: RLock = field(default_factory=RLock, init=False, repr=False)
    parent_rollout: Any | None = field(default=None, init=False, repr=False)

    def build_resumed(self, facts: Any, *, cancel_checker: Any | None = None) -> SubagentContextBundle:
        """Build a child context from its admitted task and original conversation."""
        from .contracts import SubagentTask

        metadata = facts.conversation.metadata
        child_id = metadata.get('actor_id')
        if (metadata.get('actor_kind') != 'subagent' or not isinstance(child_id, str)
                or facts.run_root != self.parent_context.run_dir.resolve()
                or metadata.get('parent_id') != self.parent_context.rollout_conversation_id):
            raise ValueError('Resumed child does not match the active parent context')
        admitted = facts.admitted_child(child_id)
        if admitted is None:
            raise ValueError('Resumed child lacks a durable task admission')
        if any(event.data.to_object().get('child_id') == child_id
               for event in facts.events if event.kind == 'child_completion_committed'):
            raise ValueError('Resumed child has already committed completion')
        from ownpi.runtime.rollout.business_scope import interaction_matches
        from ownpi.runtime.rollout.interaction_projection import answer_is_delivered, interaction_call

        interactions = [value.session for value in facts.interaction_projection().values()
                        if interaction_matches(value.session, metadata)
                        and (value.session.origin_kind != 'tool'
                             or interaction_call(facts.conversation.path, value.session, read_session=facts.read_session) is not None)
                        and ((value.session.status == 'answered'
                              and not answer_is_delivered(facts.conversation.path, value.session, read_session=facts.read_session))
                             or (value.session.status == 'pending' and value.closed_event_id is None))]
        if len(interactions) > 1:
            raise ValueError('Child resume has multiple pending interactions')
        if interactions and interactions[0].status == 'pending':
            from ownpi.runtime.interactions.pause import raise_child_interactions

            raise_child_interactions(tuple(interactions))
        # As with Core, restoring history does not require old process handles.
        # Recorded resources are reconciled when accessed.
        bundle = self.build(
            SubagentTask(admitted.task_name, admitted.task_message), child_id,
            cancel_checker, resume_conversation_id=facts.conversation.conversation_id,
        )
        bundle = replace(bundle, runtime_context=replace(bundle.runtime_context,
                         conversation_lane=str(metadata.get('stage') or ''),
                         resume_read_session=facts.read_session))
        with self._state_lock:
            self._child_contexts[child_id] = bundle.runtime_context
        if interactions:
            question = interactions[0]
            bundle.runtime_context.state_store.state.workflow_state.pending_human_clarification = question
            bundle = replace(bundle, runtime_context=replace(bundle.runtime_context,
                             continuation_index=question.continuation_index))
            with self._state_lock:
                self._child_contexts[child_id] = bundle.runtime_context
        return bundle

    def build(
        self,
        task: SubagentTask,
        child_id: str,
        cancel_checker: Any | None = None,
        *,
        resume_conversation_id: str | None = None,
    ) -> SubagentContextBundle:
        child_key = str(child_id or '').strip()
        if not child_key:
            raise ValueError('child_id must be non-empty')
        child_dir = self.child_root / child_key
        child_store = self.parent_context.state_store.fork_child(
            run_id=child_key,
            task_id=f'{self.parent_context.state_store.state.run_metadata.task_id or "task"}:{task.task_name}',
            session_id=self.parent_context.state_store.state.run_metadata.session_id,
            app_paths=self.parent_context.app_paths,
        )
        child_store.run_event_store = getattr(self.parent_context.state_store, 'run_event_store', None)
        child_store.state.context_state._run_event_store = child_store.run_event_store
        parent_metadata = self.parent_context.state_store.state.run_metadata
        child_store.state.tool_state.session_permission_grants = [
            replace(grant, task_id=child_store.state.run_metadata.task_id)
            for grant in child_store.state.tool_state.session_permission_grants
            if grant.run_id == child_key
            and grant.task_id == str(parent_metadata.task_id or "task")
            and grant.runtime_session_id == str(parent_metadata.session_id or "")
        ]
        if resume_conversation_id is None:
            child_store.record_runtime_event(
                event_name="run_started", category="workflow", status="running",
                payload={"actor_kind": "subagent", "child_id": child_key,
                         "parent_run_id": self.parent_context.run_id, "task_name": task.task_name},
            )
        parent_context = replace(self.parent_context, cancel_checker=cancel_checker)
        bundle = self.assembler.assemble(
            core_context=parent_context,
            task_message=task.message,
            child_state_store=child_store,
            child_run_dir=child_dir,
            child_id=child_key,
            level=self.level,
            stage=self.stage,
            memory_map=self.memory_map,
        )
        if resume_conversation_id is not None:
            bundle.live.resume_conversation_id = resume_conversation_id
        with self._state_lock:
            self._child_contexts[child_key] = bundle.runtime_context
        # Resource acquisition confirms minimal ownership facts before any tool wait.
        from .recovery_cleanup import checkpoint_child_resources, checkpoint_child_resources_async

        if resume_conversation_id is None:
            checkpoint_child_resources(bundle.runtime_context)
        context = bundle.runtime_context
        if context.process_owner_manager is not None:
            context.process_owner_manager.bind_actor_resource_callback(
                run_id=context.run_id,
                actor_id=context.process_owner_actor_id or context.actor_id,
                callback=lambda: checkpoint_child_resources_async(context),
            )
        return bundle

    def reclaim(self, child_id: str, _outcome: Any = None) -> None:
        asyncio.run(self.reclaim_async(child_id, _outcome))

    async def reclaim_async(self, child_id: str, _outcome: Any = None) -> None:
        """Checkpoint facts and detach the child worker without closing its processes."""
        from .recovery_cleanup import checkpoint_child_resources_async

        child_key = str(child_id or "")
        with self._state_lock:
            context = self._child_contexts.get(child_key)
        if context is None:
            return
        try:
            await checkpoint_child_resources_async(context)
            service = context.execution_runtime_service
            if service is not None and not service.flush_pending_events():
                raise RuntimeError(f'subagent resource persistence unconfirmed: {child_key}')
        except RuntimeControlRequested:
            raise
        except Exception:
            logging.getLogger(__name__).warning('Child cleanup observation failed: %s', child_key, exc_info=True)
        finally:
            try:
                manager = context.process_owner_manager
                if manager is not None:
                    manager.bind_actor_resource_callback(
                        run_id=context.run_id,
                        actor_id=context.process_owner_actor_id or context.actor_id,
                        callback=None,
                    )
            finally:
                with self._state_lock:
                    if self._child_contexts.get(child_key) is context:
                        del self._child_contexts[child_key]

    @staticmethod
    def new_child_id() -> str:
        return uuid4().hex

    def build_engine_adapter(
        self,
        *,
        engine_factory: Any,
        result_projector: Any,
    ) -> EngineSubagentLoopAdapter:
        """Build the adapter that executes bundles from this factory."""
        if not callable(engine_factory) or not callable(result_projector):
            raise TypeError("engine_factory and result_projector must be callable")
        return EngineSubagentLoopAdapter(engine_factory, result_projector)

    def build_owner(
        self,
        *,
        llm_client: Any,
        tool_runtime: Any,
        canonical_ledger_messages: list[dict[str, Any]],
        tool_specs: tuple[Any, ...],
        enabled: bool = True,
        existing_owner: SubagentOwner | None = None,
        rollout_resume: bool = False,
    ) -> SubagentOwner:
        """Compose the production child context and native-loop boundaries."""
        if not enabled:
            return SubagentOwner.from_config(False)
        from ownpi.runtime.native_tools.executor import NativeToolExecutor

        def build_native_loop(bundle: SubagentContextBundle, tool_config: Any) -> tuple[Any, Any]:
            native_factory = SubagentNativeLoopFactory.from_llm_client(
                llm_client=llm_client,
                executor=NativeToolExecutor(
                    tool_runtime.fork_for_agent(),
                    native_tool_strict=llm_client.native_tool_provider_capabilities.native_tool_strict,
                ),
                canonical_ledger_messages=canonical_ledger_messages,
                tool_specs=tool_specs,
            )
            first_segment = bundle.live.session is None
            built = native_factory.build(bundle, tool_config)
            history = getattr(built[0], 'history', None)
            if first_segment and not bundle.live.resume_conversation_id and self.parent_rollout is not None and history is not None:
                self.parent_rollout.event('thread_spawned', {
                    'child_conversation_id': history.conversation_id,
                    'child_actor_id': bundle.runtime_context.actor_id,
                })
            return built

        adapter = self.build_engine_adapter(
            engine_factory=build_native_loop,
            result_projector=_project_native_result,
        )
        max_count = getattr(getattr(self.parent_context.user_config, 'runtime', None),
                            'subagent_max_count', 8)
        if rollout_resume:
            if existing_owner is not None:
                raise ValueError('Rollout child resume requires a fresh owner')
            owner = SubagentOwner.from_config(
                True, max_count=max_count, adapter=adapter, execution_factory=self,
                state_store=LiveSubagentStateStore(self.parent_context.run_dir / 'subagents'),
            )
        else:
            owner = existing_owner or SubagentOwner.from_config(True)
            if owner.coordinator.state_store is not None and not isinstance(
                owner.coordinator.state_store, LiveSubagentStateStore
            ):
                raise ValueError('Fresh child execution requires a live state store')
            owner.bind_execution(
                max_count=max_count, adapter=adapter,
                state_store=(owner.coordinator.state_store or LiveSubagentStateStore(
                    self.parent_context.run_dir / 'subagents'
                )),
                execution_factory=self,
            )
        event_store = getattr(self.parent_context.state_store, 'run_event_store', None)
        if event_store is not None:
            from ownpi.runtime.rollout.completion_events import CompletionEvents

            completions = owner.coordinator.completion_events or CompletionEvents(event_store)
            owner.coordinator.completion_events = completions
            if owner.coordinator.state_store is not None:
                owner.coordinator.state_store.completion_events = completions
        self.parent_context = replace(self.parent_context, subagent_owner=owner)
        if owner.continuation is not None:
            owner.continuation.nudge = _nudge_child
        return owner


def _nudge_child(bundle: SubagentContextBundle, count: int) -> SubagentContextBundle:
    context = replace(bundle.runtime_context, continuation_index=count)
    return replace(bundle, runtime_context=context, continuation_message=(
        "Continue the assigned task. When it is complete, submit your feedback "
        "with complete_subagent. Ending a response does not complete the task."
    ))


def _project_native_result(bundle: SubagentContextBundle, native_result: Any) -> "SubagentResult":
    context = bundle.runtime_context
    native_status = str(getattr(native_result, "status", ""))
    completion = context.subagent_owner.completed(context.actor_id)
    from ownpi.runtime.native_tools.loop_outcome import kernel_outcome

    boundary = kernel_outcome(native_result)
    if boundary.kind == 'cancelled':
        return SubagentResult("", "cancelled", "Child execution was cancelled.")
    if boundary.kind == 'waiting':
        question = context.state_store.current_human_clarification_session()
        return SubagentResult('', 'waiting', pending_interactions=(question,) if question is not None else ())
    if native_status == "completed" and completion is not None:
        context.state_store.record_runtime_event(
            event_name="run_completed", category="workflow", status="completed",
            payload={"actor_kind": "subagent", "child_id": context.actor_id},
        )
        return completion
    status = "failed" if boundary.kind == 'failed' else "incomplete"
    if status == "failed":
        context.state_store.record_runtime_event(
            event_name="run_failed", category="workflow", status="failed",
            payload={"actor_kind": "subagent", "child_id": context.actor_id,
                     "failure_class": getattr(native_result, "failure_class", ""),
                     "message": getattr(native_result, "failure_message", "")},
        )
    return SubagentResult(
        task_name="",
        status=status,
        content=str(getattr(native_result, "failure_message", "") or
                    getattr(native_result, "final_assistant_text", "") or ""),
        metadata={"native_status": str(getattr(native_result, "status", "") or "")},
    )
