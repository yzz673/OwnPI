from __future__ import annotations

from ownpi.controller.runtime_control_signal import RuntimeControlRequested


def is_runtime_control_requested(error: BaseException) -> bool:
    return isinstance(error, RuntimeControlRequested)


__all__ = ["is_runtime_control_requested"]
