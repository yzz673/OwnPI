from __future__ import annotations

from typing import Any, Protocol

from ownpi.runtime.execution.state import ExecutionRecord


class ExecutionDispatch(Protocol):
    def monitor(self, record: ExecutionRecord) -> Any: ...

    def readback(self, record: ExecutionRecord) -> Any: ...


def record_needs_runtime_work(record: ExecutionRecord) -> bool:
    if record.is_active:
        return True
    return (
        record.terminal
        and record.handle.backend == "remote_workflow"
        and record.readback_status != "complete"
    )


__all__ = [
    "ExecutionDispatch",
    "record_needs_runtime_work",
]
