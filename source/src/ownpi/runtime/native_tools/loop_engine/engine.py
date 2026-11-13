from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from contextlib import nullcontext

from ownpi.runtime.native_tools.loop_engine.contracts import (
    NativeLoopConversation,
    NativeLoopExecutor,
    NativeLoopInvocation,
    NativeLoopLifecycle,
    NativeLoopObservationSink,
    NativeLoopPolicy,
    NativeLoopResult,
    NativeLoopRunScope,
    NativeLoopTurnPreparer,
    NativeLoopTurnRunner,
)
from ownpi.runtime.native_tools.loop_engine.batch_admission import NativeToolBatchGrammar
from ownpi.runtime.native_tools.loop_engine.kernel import NativeToolLoopKernel
from ownpi.runtime.native_tools.loop_engine.provider import provider_request_id
from ownpi.runtime.native_tools.provider import ProviderOutputEnvelope
from ownpi.runtime.native_tools.spec import NativeToolSpec


@dataclass(frozen=True)
class NativeToolLoopEngine:
    """Composable provider-to-tool loop with domain behavior supplied through ports."""

    turn_runner: NativeLoopTurnRunner
    conversation: NativeLoopConversation
    executor: NativeLoopExecutor
    policy: NativeLoopPolicy
    observer: NativeLoopObservationSink
    request_id_resolver: Callable[[ProviderOutputEnvelope], str] = provider_request_id
    lifecycle: NativeLoopLifecycle | None = None
    run_scope: NativeLoopRunScope | None = None
    turn_preparer: NativeLoopTurnPreparer | None = None
    batch_admission_specs: tuple[NativeToolSpec, ...] = ()
    batch_grammar: NativeToolBatchGrammar | None = None

    def run(self, invocation: NativeLoopInvocation | None = None) -> NativeLoopResult:
        if invocation is not None and self.lifecycle is not None:
            self.lifecycle.bind_invocation(invocation)
        scope = self.run_scope.bind(invocation) if self.run_scope and invocation else nullcontext()
        with scope:
            return NativeToolLoopKernel(
                turn_runner=self.turn_runner,
                conversation=self.conversation,
                executor=self.executor,
                policy=self.policy,
                observer=self.observer,
                request_id_resolver=self.request_id_resolver,
                lifecycle=self.lifecycle,
                turn_preparer=self.turn_preparer,
                batch_admission_specs=self.batch_admission_specs,
                batch_grammar=self.batch_grammar,
            ).run()


__all__ = ["NativeToolLoopEngine"]
