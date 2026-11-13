"""Core-loop bindings for side effects owned by the runtime context."""

from __future__ import annotations

from ownpi.runtime.native_tools.loop_engine.contracts import NativeExecutionRequest
from ownpi.runtime.native_tools.core_loop_session import CoreLoopSession
from ownpi.runtime.native_tools.spec import NativeToolError
from ownpi.runtime.tools.runtime import ToolRuntimeContext


class CoreLoopBindings:
    """Attach existing runtime-control and child-admission behavior to a session."""

    def __init__(self, session: CoreLoopSession, context: ToolRuntimeContext) -> None:
        self._session = session
        self._context = context

    def before_turn(self) -> None:
        self._context.state_store.poll_runtime_control(
            "before_provider_request"
        )

    def after_provider(self) -> None:
        self._context.state_store.poll_runtime_control(
            "after_provider_response"
        )

    def before_tools(self) -> None:
        session = self._session
        context = self._context
        context.state_store.poll_runtime_control("before_tool_batch")

        def before_handler(call: NativeExecutionRequest) -> None:
            owner = context.subagent_owner
            if context.actor_kind not in {"core", "reviewer"} or owner is None or not owner.enabled:
                return
            if call.tool_name != "run_subagents":
                return
            parsed = session.executor.parse_call(context, call)
            if isinstance(parsed, NativeToolError):
                raise ValueError("subagent start callback requires dispatch-parsed arguments")
            owner.prepare_batch_invocation(
                invocation_id=call.request_id or call.provider_call_id,
                payload=dict(parsed.input),
                source=session.shared.history.confirmed_call_source(
                    call.request_id or call.provider_call_id,
                    run_dir=context.state_store.run_event_store.path.parent.parent,
                    tool_name="run_subagents",
                ),
            )

        session.on_tool_started = before_handler

    def after_results(self) -> None:
        self._context.state_store.poll_runtime_control(
            "after_tool_results"
        )


__all__ = ["CoreLoopBindings"]
