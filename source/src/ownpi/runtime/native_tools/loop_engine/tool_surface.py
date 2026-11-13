from __future__ import annotations

from dataclasses import dataclass

from ownpi.runtime.native_tools.loop_engine.contracts import (
    NativeLoopToolSurfaceSnapshot,
)
from ownpi.runtime.native_tools.spec import NativeToolSpec


@dataclass(frozen=True)
class FixedNativeToolSurfaceProvider:
    specs: tuple[NativeToolSpec, ...]

    def refresh(self, *, turn_index: int) -> NativeLoopToolSurfaceSnapshot:
        del turn_index
        return build_native_tool_surface_snapshot(
            provider_specs=self.specs,
            executable_specs=self.specs,
        )


def build_native_tool_surface_snapshot(
    *,
    provider_specs: tuple[NativeToolSpec, ...],
    executable_specs: tuple[NativeToolSpec, ...],
) -> NativeLoopToolSurfaceSnapshot:
    return NativeLoopToolSurfaceSnapshot(
        provider_specs=provider_specs,
        executable_specs=executable_specs,
    )


__all__ = [
    "FixedNativeToolSurfaceProvider",
    "build_native_tool_surface_snapshot",
]
