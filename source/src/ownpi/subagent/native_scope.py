"""Bind child provider identity independently of parent and worker contexts."""

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator

from ownpi.llm.provider_request_context import (
    ProviderRequestContext,
    bind_provider_request_context,
)
from ownpi.runtime.native_tools.loop_engine import NativeLoopInvocation, ProviderTurnRunScope


@dataclass(frozen=True)
class SubagentProviderRunScope:
    cancel_checker_setter: Callable[[Callable[[], bool] | None], None] | None = None

    @contextmanager
    def bind(self, invocation: NativeLoopInvocation) -> Iterator[None]:
        identity = invocation.identity
        context = ProviderRequestContext(
            session_id=identity.session_id,
            task_id=identity.task_id,
            run_id=identity.run_id,
        )
        with bind_provider_request_context(context), ProviderTurnRunScope(
            cancel_checker_setter=self.cancel_checker_setter,
        ).bind(invocation):
            yield
