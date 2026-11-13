from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any

from ownpi.llm.base import LLMClient
from ownpi.runtime.native_tools.loop_engine.provider import create_client_session, NativeProviderTurnRunner, ProviderTurnRunScope
from ownpi.runtime.native_tools.loop_engine.session import build_native_session_engine
from ownpi.runtime.native_tools.loop_engine.session_bindings import history_session_bindings
from ownpi.runtime.native_tools.loop_engine.tool_surface import FixedNativeToolSurfaceProvider
from ownpi.runtime.native_tools.loop_engine.contracts import NativeLoopInvocation, NativeLoopIdentity, NativeLoopPaths
from ownpi.runtime.rollout.session import RecordingTurnRunner
from ownpi.runtime.rollout.resume import ResumedConversation
from ownpi.runtime.native_tools.loop_outcome import kernel_outcome
from ownpi.app.paths import AppPaths
from ownpi.runtime.native_tools.loop_engine import (
    CallableNativeToolExecutor,
    NativeLoopObserver,
    NativeLoopContinuation,
)
from ownpi.runtime.p_assembly import (
    PAssemblyBindings,
    PAssemblyRequest,
    PLoopKind,
    PSourceInputs,
    assemble_native_loop_p,
)
from ownpi.runtime.task_context import RuntimeTaskContext
from ownpi.task_authoring.contracts import (
    TaskSpecAuthoringOutcome,
    TaskSpecAuthoringRequest,
)
from ownpi.task_authoring.policy import TaskSpecAuthoringPolicy
from ownpi.task_authoring.tool_handlers import (
    TaskSpecAuthoringToolContext,
    execute_task_spec_authoring_call,
)
from ownpi.task_authoring.tool_specs import TASK_SPEC_AUTHORING_TOOLS

@dataclass(frozen=True)
class TaskSpecAuthoringLoopResult:
    outcome: TaskSpecAuthoringOutcome
    trace_events: tuple[dict[str, Any], ...] = ()
    usage: dict[str, int] = field(default_factory=dict)
    elapsed_ms: float = 0.0


class _TaskAuthoringObserver(NativeLoopObserver):
    def correction(self, *, turn_index: int, text: str, reason: str) -> None:
        del text, reason
        self.add("task_spec_authoring_protocol_correction", turn_index=turn_index)


def run_task_spec_authoring_loop(
    *,
    client: LLMClient,
    request: TaskSpecAuthoringRequest,
    context: TaskSpecAuthoringToolContext,
    replay: ResumedConversation | None = None,
) -> TaskSpecAuthoringLoopResult:
    started = time.perf_counter()
    app_paths = AppPaths.discover()
    task_context = RuntimeTaskContext.from_paths(
        raw_user_message_path=context.layout.raw_message_path,
        task_spec_path=None,
    )
    p = assemble_native_loop_p(
        request=PAssemblyRequest(
            loop_kind=PLoopKind.TASK_SPEC_AUTHORING,
            bindings=PAssemblyBindings(
                project_workspace_root=str(request.project_root),
                managed_venv_path=str(app_paths.managed_venv_path),
                managed_non_python_dependency_root=(
                    str(app_paths.managed_non_python_dependency_root)
                ),
                current_working_directory=str(request.project_root),
            ),
        ),
        source_inputs=PSourceInputs(
            task_context=task_context,
            hook_prefix_fragments=request.hook_prefix_fragments,
        ),
    )
    policy = TaskSpecAuthoringPolicy(context=context)
    observer = _TaskAuthoringObserver(
        event_prefix="task_spec_authoring",
        base_payload={"request_id": request.request_id, "p_digest": p.digest},
    )
    tool_specs = tuple(TASK_SPEC_AUTHORING_TOOLS)
    executor = CallableNativeToolExecutor(
        tool_specs=tool_specs,
        native_tool_strict=client.native_tool_provider_capabilities.native_tool_strict,
        handlers={
            tool.name: lambda call: execute_task_spec_authoring_call(
                call=call,
                context=context,
            )
            for tool in TASK_SPEC_AUTHORING_TOOLS
        },
        default_handler=lambda call: execute_task_spec_authoring_call(
            call=call,
            context=context,
        ),
    )
    session = create_client_session(
        client=client, messages=list(p.ledger_messages()), directory=request.run_dir / 'rollouts',
        metadata={'run_id': request.run_id, 'session_id': request.session_id, 'task_id': request.task_id,
                  'actor_kind': 'task_authoring', 'actor_id': 'task_authoring',
                  'stage': 'task_authoring', 'attempt': 1, 'workspace': str(request.project_root)},
        bindings=history_session_bindings(),
        replay=replay,
    )
    continuation = NativeLoopContinuation(session)
    try:
        engine = build_native_session_engine(
            session=session, tool_surface=FixedNativeToolSurfaceProvider(tool_specs), batch_admission_specs=tool_specs,
            turn_runner=RecordingTurnRunner(NativeProviderTurnRunner(client, 'task_spec_authoring'), session.history),
            run_scope=ProviderTurnRunScope(client.set_cancel_checker), executor=executor,
            policy=policy, observer=observer,
        )
        continuation.execute = engine.run
        kernel_result = continuation.run(NativeLoopInvocation(
            identity=NativeLoopIdentity(run_id=request.run_id, session_id=request.session_id,
                                        task_id=request.task_id, actor_kind='task_authoring', actor_id='task_authoring'),
            paths=NativeLoopPaths(project_root=request.project_root, run_dir=request.run_dir),
        ))
    finally:
        continuation.close()

    if kernel_outcome(kernel_result).kind == 'business':
        assert policy.artifact is not None
        observer.add(
            "task_spec_authoring_completed",
            turn_index=kernel_result.turns,
            artifact_hash=policy.artifact.content_hash,
        )
        outcome = TaskSpecAuthoringOutcome(
            status="completed",
            request_id=request.request_id,
            artifact=policy.artifact,
            turns=kernel_result.turns,
            tool_call_count=kernel_result.tool_call_count,
            inspected_path_count=len(context.inspected_paths),
            provider_request_ids=kernel_result.provider_request_ids,
        )
    else:
        outcome = TaskSpecAuthoringOutcome(
            status="failed",
            request_id=request.request_id,
            turns=kernel_result.turns,
            tool_call_count=kernel_result.tool_call_count,
            inspected_path_count=len(context.inspected_paths),
            provider_request_ids=kernel_result.provider_request_ids,
            failure_class=kernel_result.failure_class,
            failure_message=kernel_result.failure_message[:500],
        )
    return TaskSpecAuthoringLoopResult(
        outcome=outcome,
        trace_events=observer.events,
        usage=kernel_result.usage,
        elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
    )


__all__ = [
    "TaskSpecAuthoringLoopResult",
    "run_task_spec_authoring_loop",
]
