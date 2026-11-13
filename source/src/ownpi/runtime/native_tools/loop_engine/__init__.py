from ownpi.runtime.native_tools.loop_engine.batch_admission import (
    NativeToolBatchAdmission,
    NativeToolBatchGrammar,
    admit_native_tool_batch,
)
from ownpi.runtime.native_tools.loop_engine.contracts import NativeLoopIdentity, NativeLoopInvocation, NativeLoopPaths, NativeLoopServices, NativeLoopDirective, NativeLoopResult, NativeLoopSnapshot, NativeLoopToolSurfaceProvider, NativeLoopToolSurfaceSnapshot, NativeLoopTurnPreparer, NativeLoopTurnPlan
from ownpi.runtime.native_tools.loop_engine.engine import NativeToolLoopEngine
from ownpi.runtime.native_tools.loop_engine.continuation import NativeLoopContinuation
from ownpi.runtime.native_tools.loop_engine.kernel import NativeToolLoopKernel
from ownpi.runtime.native_tools.loop_engine.observer import (
    NativeLoopObserver,
    NullNativeLoopObserver,
)
from ownpi.runtime.native_tools.loop_engine.provider import (
    NativeProviderSession,
    NativeProviderTurnRunner,
    ProjectedNativeProviderTurnRunner,
    ProviderTurnRunScope,
)
from ownpi.runtime.native_tools.loop_engine.provider_recovery import (
    is_max_tokens_truncation,
    is_provider_continuation,
    max_tokens_recovery_text,
    provider_failure_details,
)
from ownpi.runtime.native_tools.loop_engine.tool_surface import (
    FixedNativeToolSurfaceProvider,
    build_native_tool_surface_snapshot,
)

__all__ = [
    "CallableNativeToolExecutor",
    "NativeLoopDirective",
    "NativeLoopIdentity",
    "NativeLoopInvocation",
    "NativeLoopObserver",
    "NativeLoopPaths",
    "NativeLoopServices",
    "NullNativeLoopObserver",
    "NativeLoopResult",
    "NativeLoopSnapshot",
    "NativeLoopToolSurfaceProvider",
    "NativeLoopToolSurfaceSnapshot",
    "NativeLoopTurnPreparer",
    "NativeLoopTurnPlan",
    "NativeProviderTurnRunner",
    "NativeProviderSession",
    "ProjectedNativeProviderTurnRunner",
    "ProviderTurnRunScope",
    "NativeToolLoopEngine",
    "NativeLoopContinuation",
    "NativeToolLoopKernel",
    "NativeToolBatchAdmission",
    "NativeToolBatchGrammar",
    "FixedNativeToolSurfaceProvider",
    "build_native_tool_surface_snapshot",
    "admit_native_tool_batch",
    "is_max_tokens_truncation",
    "is_provider_continuation",
    "max_tokens_recovery_text",
    "provider_failure_details",
]


def __getattr__(name: str) -> object:
    if name == "CallableNativeToolExecutor":
        from ownpi.runtime.native_tools.callable_executor import CallableNativeToolExecutor

        return CallableNativeToolExecutor
    raise AttributeError(name)
