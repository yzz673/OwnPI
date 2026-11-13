from __future__ import annotations

from typing import Any, Protocol

from ownpi.runtime.execution.state import ExecutionObservation
from ownpi.runtime.native_tools.messages import NativeToolMessage


class ExecutionBackendAdapter(Protocol):
    backend_name: str

    def observation_from_native_message(
        self,
        message: NativeToolMessage,
        *,
        tool_name: str,
        data: dict[str, Any],
        metadata: dict[str, Any],
    ) -> ExecutionObservation | None:
        ...
