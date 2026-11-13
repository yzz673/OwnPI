from __future__ import annotations

import importlib
import pkgutil

_AGENT_MODULES_LOADED = False


def load_agent_modules() -> None:
    global _AGENT_MODULES_LOADED
    if _AGENT_MODULES_LOADED:
        return

    excluded = {"base", "registry"}
    for module_info in pkgutil.iter_modules(__path__):
        if module_info.name in excluded:
            continue
        importlib.import_module(f"{__name__}.{module_info.name}")

    _AGENT_MODULES_LOADED = True


__all__ = ["load_agent_modules"]
