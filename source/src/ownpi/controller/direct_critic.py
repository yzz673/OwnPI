"""Internal direct critic and sequential session; no product entry ownership."""

from dataclasses import replace
from typing import Any, Callable

from ownpi.agents.base import AgentRuntimeInvocationContext
from ownpi.controller.critic_loop import CriticLoopBindings, execute_critic_loop
from ownpi.controller.stage_kernel import CoreRoundSpec, StageKernel
from ownpi.controller.stages import Stage
from ownpi.runtime.builders import build_agent_input
from ownpi.runtime.call_continuation.critic_loop import reset_critic_loop
from ownpi.runtime.native_tools.core_agent_loop import CoreAgentLoopContinuation
from ownpi.runtime.rollout.direct_inputs import direct_request_scope, record_direct_request
from ownpi.schemas.feedback import DirectReviewTarget
from ownpi.schemas.workflow_plan import StageRuntimeProfile


def _validate_direct_context(context: AgentRuntimeInvocationContext) -> None:
    workflow = context.state_store.state.workflow_state
    if (workflow.current_stage != Stage.DIRECT_QA
            or workflow.stage_runtime_profile != StageRuntimeProfile.TASK_LEVEL
            or context.stage != Stage.DIRECT_QA
            or type(context.attempt) is not int or context.attempt < 1
            or context.attempt != workflow.current_stage_attempt):
        raise ValueError('direct critic requires matching task-level direct coordinates')


def execute_direct_critic_loop(
    *, current_request: str, invocation_context: AgentRuntimeInvocationContext,
    runtime_agents: dict[str, tuple[Any, Any]],
    emit: Callable[[str], None] = lambda _: None,
    core_continuation: CoreAgentLoopContinuation | None = None,
    operation_runner: Callable[..., Any] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    _validate_direct_context(invocation_context)
    holder = core_continuation or CoreAgentLoopContinuation(identity=(
        str(invocation_context.state_store.state.run_metadata.run_id),
        Stage.DIRECT_QA.value, str(invocation_context.attempt), 'core',
    ))
    try:
        return _execute_direct_critic_loop(
            current_request=current_request, invocation_context=invocation_context,
            runtime_agents=runtime_agents, emit=emit, core_continuation=holder,
            operation_runner=operation_runner,
        )
    finally:
        if core_continuation is None:
            holder.close()


def _execute_direct_critic_loop(
    *, current_request: str, invocation_context: AgentRuntimeInvocationContext,
    runtime_agents: dict[str, tuple[Any, Any]], emit: Callable[[str], None],
    core_continuation: CoreAgentLoopContinuation,
    operation_runner: Callable[..., Any] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    store = invocation_context.state_store
    state = store.state
    workflow = state.workflow_state
    def run_operation(*, agent_config: Any, agent: Any, operation: str,
                      native_loop_continuation: Any = None, **_: Any) -> Any:
        if operation_runner is not None:
            return operation_runner(
                agent_config=agent_config, agent=agent, operation=operation,
                current_request=current_request,
                native_loop_continuation=native_loop_continuation,
            )
        actor = 'reviewer' if operation == 'direct_critic_reviewer' else 'core'
        agent_input = build_agent_input(
            state, agent_config, operation=operation, current_request=current_request,
            project_root=store.project_root, state_store=store,
            app_paths=invocation_context.app_paths,
        )
        context = replace(
            invocation_context, operation=operation,
            native_loop_continuation=native_loop_continuation,
            resume_conversation_id=store.take_resume_conversation(
                stage=Stage.DIRECT_QA.value, attempt=invocation_context.attempt,
                actor_kind=actor,
            ),
        )
        previous = agent.set_runtime_invocation_context(context)
        try:
            return agent.run(agent_input)
        finally:
            agent.set_runtime_invocation_context(previous)

    def no_stage_gate(**_: Any) -> Any:
        raise AssertionError('single direct critic must not enter a stage gate')

    # The direct caller owns history; the shared runner still handles control and nudges.
    continuation_runner = StageKernel(
        run_agent_operation=run_operation, runtime_gate=no_stage_gate, emit=emit,
    )

    def run_core(initial: bool, on_complete: Callable[[], None] | None) -> list[dict[str, Any]]:
        _, results = continuation_runner._run_produce_with_continuation_loop(
            core_round=CoreRoundSpec(
                agent_role='core', operation='direct_critic_core',
                event_name='core_round_completed',
                filename_pattern='direct_core_attempt_{attempt}.json',
            ),
            state_store=store, run_dir=invocation_context.run_dir,
            saved_files=invocation_context.saved_files, stage=Stage.DIRECT_QA,
            attempt=invocation_context.attempt, label='direct critic',
            runtime_agents=runtime_agents,
            start_round_index=int(workflow.scratchpad.get('continuation_index', 0) or 0),
            return_on_initial_core_complete=initial,
            on_initial_core_complete=on_complete,
            native_loop_continuation=core_continuation,
        )
        return results

    def review_target() -> DirectReviewTarget:
        receipt = state.feedback_state.critic_loop.core_intent_receipt
        if receipt is None or receipt.intent != 'request_review':
            raise ValueError('direct review requires request_review receipt')
        return DirectReviewTarget(
            run_id=state.run_metadata.run_id, stage=Stage.DIRECT_QA.value,
            attempt=invocation_context.attempt,
            loop_index=int(workflow.scratchpad.get('stage_loop_index', 1)),
            request_id=receipt.tool_call_id,
        )

    def run_reviewer() -> Any:
        config, agent = runtime_agents['reviewer']
        return run_operation(agent_config=config, agent=agent, operation='direct_critic_reviewer')

    return execute_critic_loop(CriticLoopBindings(
        state_store=store, run_dir=invocation_context.run_dir,
        stage=Stage.DIRECT_QA.value, attempt=invocation_context.attempt,
        review_target=review_target, run_core=run_core, run_reviewer=run_reviewer,
        saved_files=invocation_context.saved_files,
        reviewer_operation='direct_critic_reviewer',
    ))


class DirectCriticSession:
    """Serialize user requests through existing critic loops with one Core history."""

    def __init__(
        self, *, invocation_context: AgentRuntimeInvocationContext,
        runtime_agents: dict[str, tuple[Any, Any]],
        emit: Callable[[str], None] = lambda _: None,
        operation_runner: Callable[..., Any] | None = None,
        prepare_request: Callable[..., None] | None = None,
    ) -> None:
        _validate_direct_context(invocation_context)
        self.invocation_context = invocation_context
        self.runtime_agents = runtime_agents
        self.emit = emit
        self.operation_runner = operation_runner
        self.prepare_request = prepare_request
        self.core_continuation = CoreAgentLoopContinuation(identity=(
            str(invocation_context.state_store.state.run_metadata.run_id),
            Stage.DIRECT_QA.value, str(invocation_context.attempt), 'core',
        ))
        self.current_request: str | None = None
        self.loop_index = 0
        self.active = False
        self.closed = False
        self._started = False

    def submit(self, message: str) -> tuple[str, list[dict[str, Any]]]:
        self.admit(message)
        return self.continue_request()

    def admit(self, message: str) -> None:
        if self.closed or self.active:
            raise RuntimeError('Direct session is closed or has an unfinished request')
        self.current_request = message
        self.loop_index += 1
        self.active = True
        self._started = False
        self._start_request()
        if self.prepare_request is not None:
            self.prepare_request(current_request=message, continuation=self.core_continuation)

    def restore(self, *, message: str, loop_index: int, active: bool) -> None:
        if self.active or self.closed:
            raise RuntimeError('Cannot replace an attached request')
        self.current_request = message
        self.loop_index = loop_index
        self.active = active
        self._started = True

    def _start_request(self) -> None:
        context = self.invocation_context
        store = context.state_store
        if not self._started:
            from ownpi.runtime.rollout.answer_publication import enable_answer_protocol

            enable_answer_protocol(store, attempt=context.attempt, loop_index=self.loop_index)
            if self.prepare_request is None:
                record_direct_request(
                    store, stage=Stage.DIRECT_QA.value, attempt=context.attempt,
                    loop_index=self.loop_index,
                )
            reset_critic_loop(store.state.feedback_state)
            scratchpad = store.state.workflow_state.scratchpad
            scratchpad['stage_loop_index'] = self.loop_index
            for name in ('continuation_index', 'role_loop_continuation_nudge'):
                scratchpad.pop(name, None)
            self._started = True

    def continue_request(self) -> tuple[str, list[dict[str, Any]]]:
        if self.closed or not self.active:
            raise RuntimeError('Direct session has no active request')
        context = self.invocation_context
        _validate_direct_context(context)
        store = context.state_store
        self._start_request()
        store.poll_runtime_control('direct_request_started')
        assert self.current_request is not None
        result = execute_direct_critic_loop(
            current_request=self.current_request, invocation_context=context,
            runtime_agents=self.runtime_agents, emit=self.emit,
            core_continuation=self.core_continuation,
            operation_runner=self.operation_runner,
        )
        if result[0] == 'critic_complete':
            from ownpi.runtime.rollout.answer_publication import publish_direct_answer

            publish_direct_answer(store, context.run_dir, attempt=context.attempt,
                                  loop_index=self.loop_index)
            scope = direct_request_scope(
                stage=Stage.DIRECT_QA.value, attempt=context.attempt, loop_index=self.loop_index,
            )
            store.run_event_store.emit('direct_request_completed', f'direct-complete:{scope}', {
                'stage': Stage.DIRECT_QA.value, 'attempt': context.attempt,
                'loop_index': self.loop_index,
            })
            self.active = False
        return result

    def close(self) -> bool:
        if not self.closed:
            self.closed = self.core_continuation.close()
        return self.closed

    def __enter__(self) -> 'DirectCriticSession':
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if not self.close() and exc_type is None:
            raise OSError('Direct session close persistence is unconfirmed')
