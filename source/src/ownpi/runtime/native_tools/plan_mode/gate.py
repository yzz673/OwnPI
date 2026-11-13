from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PlanModeFeatureGate:
    """The only capability gate for the reusable Plan Mode subsystem."""

    enabled: bool = False

    @classmethod
    def from_runtime_config(cls, runtime_config: Any | None) -> "PlanModeFeatureGate":
        plan_mode = getattr(runtime_config, "plan_mode", None)
        return cls(enabled=bool(getattr(plan_mode, "enabled", False)))

    def require_enabled(self) -> None:
        if not self.enabled:
            raise RuntimeError("Plan Mode capability is disabled")


__all__ = ["PlanModeFeatureGate"]
