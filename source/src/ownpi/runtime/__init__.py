"""Runtime support package."""

from __future__ import annotations

from typing import Any

__all__ = [
    'AgentInput',
    'EffectivePermissions',
    'HistoryEvent',
    'RuntimeEventRecord',
    'RuntimeState',
    'ToolResult',
    'build_agent_input',
    'now_utc_iso',
]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(name)

    _MODULE_MAP = {
        'AgentInput': 'ownpi.runtime.contracts',
        'EffectivePermissions': 'ownpi.runtime.permissions.models',
        'HistoryEvent': 'ownpi.runtime.events',
        'RuntimeEventRecord': 'ownpi.runtime.runtime_events',
        'RuntimeState': 'ownpi.runtime.state_models',
        'ToolResult': 'ownpi.runtime.contracts',
        'build_agent_input': 'ownpi.runtime.builders',
        'now_utc_iso': 'ownpi.runtime.events',
    }

    import importlib

    module = importlib.import_module(_MODULE_MAP[name])
    return getattr(module, name)
