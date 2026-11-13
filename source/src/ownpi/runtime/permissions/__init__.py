"""Canonical permission contracts and policy helpers.

Exports are loaded lazily so schema-level permission models do not import Runtime
state during configuration parsing.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORT_MODULES = {
    'BashAnalysis': 'bash_policy',
    'BashCommandSegment': 'bash_policy',
    'BashNetworkIntent': 'bash_policy',
    'BashRedirect': 'bash_policy',
    'analyze_bash_command': 'bash_policy',
    'build_permission_explanation': 'explainer',
    'render_permission_explanation': 'explainer',
}

__all__ = sorted(_EXPORT_MODULES)


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(name)
    module = import_module(f'ownpi.runtime.permissions.{module_name}')
    return getattr(module, name)
