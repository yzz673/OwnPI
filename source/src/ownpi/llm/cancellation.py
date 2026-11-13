"""Allow the Runtime wakeup signal to interrupt only blocking Provider I/O."""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Callable, TypeVar

from ownpi.llm.provider_retry import RetryCancelled


_Result = TypeVar('_Result')
_provider_io_active: ContextVar[bool] = ContextVar('ownpi_provider_io_active', default=False)


def run_provider_io(operation: Callable[..., _Result], *args: Any, **kwargs: Any) -> _Result:
    token = _provider_io_active.set(True)
    try:
        return operation(*args, **kwargs)
    finally:
        _provider_io_active.reset(token)


def interrupt_provider_io() -> None:
    if _provider_io_active.get():
        raise RetryCancelled('Provider HTTP I/O cancelled by Runtime stop')
