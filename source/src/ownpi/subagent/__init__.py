"""Optional subagent owner.

The package is intentionally inert unless the user enables subagent mode.
"""

from .contracts import SubagentContext, SubagentResult, SubagentTask
from .coordinator import BatchCoordinator, BatchResult, ChildOutcome
from .owner import SubagentOwner, SubagentShutdownReport
from .tools import build_subagent_tool_definitions, tool_definitions_for_actor
from .state import ChildState, VALID_CHILD_STATES
from .adapter import CallableSubagentLoopAdapter, EngineSubagentLoopAdapter
from .continuation import ContinuationResult, SubagentContinuationLoop
from .context import SubagentContextAssembler, SubagentContextBundle
from .factory import SubagentExecutionFactory
from .native_factory import SubagentNativeLoopFactory

__all__ = [
    "BatchCoordinator",
    "BatchResult",
    "ChildOutcome",
    "SubagentContext",
    "SubagentResult",
    "SubagentTask",
    "SubagentOwner",
    "SubagentShutdownReport",
    "build_subagent_tool_definitions",
    "tool_definitions_for_actor",
    "ChildState",
    "VALID_CHILD_STATES",
    "CallableSubagentLoopAdapter",
    "EngineSubagentLoopAdapter",
    "ContinuationResult",
    "SubagentContinuationLoop",
    "SubagentContextAssembler",
    "SubagentContextBundle",
    "SubagentExecutionFactory",
    "SubagentNativeLoopFactory",
]
