from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    'PlannedTools',
    'ToolDefinition',
    'ToolEnvironmentSnapshot',
    'ToolExposure',
    'ToolPlanContext',
    'ToolRegistry',
    'ToolRouter',
    'build_default_tool_definitions',
    'plan_tools',
]


_EXPORT_MODULES = {
    'PlannedTools': 'ownpi.runtime.tools.planning',
    'ToolDefinition': 'ownpi.runtime.tools.definitions',
    'ToolEnvironmentSnapshot': 'ownpi.runtime.tools.planning',
    'ToolExposure': 'ownpi.runtime.tools.definitions',
    'ToolPlanContext': 'ownpi.runtime.tools.planning',
    'ToolRegistry': 'ownpi.runtime.tools.registry',
    'ToolRouter': 'ownpi.runtime.tools.router',
    'build_default_tool_definitions': 'ownpi.runtime.tools.defaults',
    'plan_tools': 'ownpi.runtime.tools.planning',
}


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(name)
    return getattr(import_module(module_name), name)
