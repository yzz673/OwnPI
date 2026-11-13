"""Live conversation ownership independent of a role's business state."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from ownpi.runtime.context.manager import ContextRuntimeManager
from ownpi.runtime.native_tools.compaction.local import NativeCompactionInvoke
from ownpi.runtime.native_tools.compaction.contracts import NativeModelContextMetadataV1
from ownpi.runtime.native_tools.history_budget import HistoryUsage
from ownpi.runtime.native_tools.history_items import HistoryEntry
from ownpi.runtime.native_tools.loop_engine.contracts import (
    NativePreparedRequest, NativeLoopExecutor, NativeLoopPolicy,
    NativeLoopObservationSink, NativeLoopLifecycleBindings, NativeLoopRunScope,
    NativeLoopToolSurfaceProvider, NativeLoopTurnRunner,
)
from ownpi.runtime.native_tools.loop_engine.session_bindings import NativeSessionBindings
from ownpi.runtime.native_tools.provider import ProviderOutputEnvelope, NativeToolProviderCapabilities
from ownpi.runtime.native_tools.provider_input import NativeHistoryProviderInput
from ownpi.runtime.native_tools.provider_request_measurement import NativeProviderRequestMeasurement
from ownpi.runtime.native_tools.spec import NativeToolSpec
from ownpi.runtime.rollout.session import RolloutHistory
from ownpi.runtime.limits.tool_output import TruncationPolicy
from ownpi.runtime.native_tools.loop_engine.batch_admission import NativeToolBatchGrammar

if TYPE_CHECKING:
    from ownpi.runtime.native_tools.loop_engine.engine import NativeToolLoopEngine
    from ownpi.runtime.rollout.resume import ResumedConversation


@dataclass(kw_only=True)
class NativeLoopSession:
    history: RolloutHistory
    canonical_ledger_messages: list[dict[str, Any]]
    context_runtime_manager: ContextRuntimeManager
    request_measure: Callable[[NativeHistoryProviderInput, tuple[NativeToolSpec, ...]], NativeProviderRequestMeasurement]
    bindings: NativeSessionBindings
    provider_capabilities: NativeToolProviderCapabilities
    compaction_invoke: NativeCompactionInvoke | None = None
    model_metadata: NativeModelContextMetadataV1 | None = None
    rollout_model: dict[str, Any] = field(default_factory=dict)
    current_request: NativePreparedRequest | None = None
    current_response: ProviderOutputEnvelope | None = None
    pending_input: list[HistoryEntry] = field(default_factory=list)
    history_usage: HistoryUsage = field(default_factory=HistoryUsage)
    resumed_from_rollout: bool = False

    def __post_init__(self) -> None:
        self.canonical_ledger_messages = [dict(message) for message in self.canonical_ledger_messages]

    def record_pending_input(self) -> None:
        self.history.record_items(self.pending_input)
        self.pending_input.clear()


def create_native_session(
    *, directory: Path, metadata: dict[str, Any],
    canonical_ledger_messages: list[dict[str, Any]], policy: TruncationPolicy,
    context_runtime_manager: ContextRuntimeManager,
    request_measure: Callable[[NativeHistoryProviderInput, tuple[NativeToolSpec, ...]], NativeProviderRequestMeasurement],
    bindings: NativeSessionBindings, provider_capabilities: NativeToolProviderCapabilities,
    compaction_invoke: NativeCompactionInvoke | None = None,
    model_metadata: NativeModelContextMetadataV1 | None = None,
    rollout_model: dict[str, Any] | None = None,
    initial_input: tuple[HistoryEntry, ...] = (), replay: ResumedConversation | None = None,
) -> NativeLoopSession:
    history = (RolloutHistory.resume(replay, policy=policy) if replay is not None else
               RolloutHistory(directory=directory, metadata=metadata,
                              prefix=canonical_ledger_messages, policy=policy))
    try:
        return NativeLoopSession(
            history=history, canonical_ledger_messages=canonical_ledger_messages,
            context_runtime_manager=context_runtime_manager, request_measure=request_measure,
            bindings=bindings, provider_capabilities=provider_capabilities,
            compaction_invoke=compaction_invoke, model_metadata=model_metadata,
            rollout_model=dict(rollout_model or {}), resumed_from_rollout=replay is not None,
            pending_input=list(initial_input) if replay is None else [],
        )
    except BaseException as error:
        try:
            history.close()
        except BaseException as close_error:
            error.add_note(f'Session construction close failed: {close_error!r}')
        raise


def build_native_session_engine(
    *, session: NativeLoopSession, tool_surface: NativeLoopToolSurfaceProvider,
    turn_runner: NativeLoopTurnRunner, executor: NativeLoopExecutor,
    policy: NativeLoopPolicy, observer: NativeLoopObservationSink,
    lifecycle_bindings: NativeLoopLifecycleBindings | None = None,
    run_scope: NativeLoopRunScope | None = None,
    batch_admission_specs: tuple[NativeToolSpec, ...] = (),
    batch_grammar: NativeToolBatchGrammar | None = None,
) -> NativeToolLoopEngine:
    from ownpi.runtime.native_tools.loop_engine.engine import NativeToolLoopEngine
    from ownpi.runtime.native_tools.loop_engine.session_conversation import NativeSessionConversation
    from ownpi.runtime.native_tools.loop_engine.session_preparation import NativeSessionPreparation
    from ownpi.runtime.native_tools.loop_engine.session_lifecycle import NativeSessionLifecycle

    return NativeToolLoopEngine(
        turn_runner=turn_runner, executor=executor, policy=policy, observer=observer,
        conversation=NativeSessionConversation(session),
        turn_preparer=NativeSessionPreparation(session, tool_surface),
        lifecycle=NativeSessionLifecycle(session, lifecycle_bindings),
        run_scope=run_scope, batch_admission_specs=batch_admission_specs, batch_grammar=batch_grammar,
    )
