"""Runtime context assembly, disclosure, prefetch, and rendering helpers."""

# ruff: noqa: F401

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ownpi.runtime.context.contracts import (
        PreparedProviderContext,
    )
    from ownpi.runtime.context.manager import ContextRuntimeManager


_EXPORT_MODULES = {
    'PreparedProviderContext': 'ownpi.runtime.context.contracts',
    'ContextRuntimeManager': 'ownpi.runtime.context.manager',
}

__all__ = sorted(_EXPORT_MODULES)


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
