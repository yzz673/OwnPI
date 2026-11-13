from __future__ import annotations

from enum import Enum
import logging
from typing import Any


class ExecutionSchedulerShutdownTimeout(TimeoutError):
    """The scheduler still owns a quiescing worker after bounded shutdown."""


def stop_execution_for_cleanup(service: Any) -> bool:
    try:
        return service.stop() is not False
    except ExecutionSchedulerShutdownTimeout:
        logging.getLogger(__name__).warning(
            'Execution scheduler shutdown timed out; runtime ownership retained', exc_info=True,
        )
        return False


class ExecutionShutdownPhase(str, Enum):
    RUNNING = "running"
    QUIESCING = "quiescing"
    CLOSED = "closed"


__all__ = ["ExecutionShutdownPhase", "ExecutionSchedulerShutdownTimeout", "stop_execution_for_cleanup"]
