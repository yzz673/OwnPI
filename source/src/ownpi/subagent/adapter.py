"""Isolation adapter between the subagent owner and the native tool loop."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, TYPE_CHECKING

from .contracts import SubagentContext, SubagentResult

if TYPE_CHECKING:
    from .context import SubagentContextBundle


class SubagentLoopAdapter(ABC):
    """Boundary between child lifecycle management and native loop execution."""

    @abstractmethod
    def run(
        self,
        context: 'SubagentContextBundle | SubagentContext',
        *,
        tool_config: Any = None,
    ) -> SubagentResult:
        """Run one subagent execution scope."""


class CallableSubagentLoopAdapter(SubagentLoopAdapter):
    """Adapter used to inject an existing native-loop implementation."""

    def __init__(
        self,
        runner: Callable[['SubagentContextBundle | SubagentContext', Any], SubagentResult],
    ) -> None:
        self._runner = runner

    def run(
        self,
        context: 'SubagentContextBundle | SubagentContext',
        *,
        tool_config: Any = None,
    ) -> SubagentResult:
        return self._runner(context, tool_config)


class EngineSubagentLoopAdapter(SubagentLoopAdapter):
    """Run a child through an injected shared ``NativeToolLoopEngine`` factory.

    The factory owns provider, executor, lifecycle, and conversation wiring.  This
    adapter only supplies the already assembled child bundle and invocation data,
    keeping Core-specific policy out of the subagent owner.
    """

    def __init__(
        self,
        engine_factory: Callable[[Any, Any], Any],
        result_projector: Callable[[Any, Any], SubagentResult],
    ) -> None:
        self._engine_factory = engine_factory
        self._result_projector = result_projector

    def run(
        self,
        context: 'SubagentContextBundle | SubagentContext',
        *,
        tool_config: Any = None,
    ) -> SubagentResult:
        if not hasattr(context, "runtime_context"):
            raise TypeError("EngineSubagentLoopAdapter requires SubagentContextBundle")
        built = self._engine_factory(context, tool_config)
        history = None
        keep_live = False
        try:
            if isinstance(built, tuple) and len(built) == 2:
                engine, invocation = built
                history = getattr(engine, 'history', None)
                native_result = engine.run(invocation)
            else:
                history = getattr(built, 'history', None)
                native_result = built.run()
            result = self._result_projector(context, native_result)
            keep_live = result.status == 'incomplete'
            return result
        finally:
            if history is not None and not keep_live:
                history.close()
