from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

ProviderInputT = TypeVar("ProviderInputT")
MeasurementT = TypeVar("MeasurementT")


@dataclass(frozen=True)
class PreparedProviderContext(Generic[ProviderInputT, MeasurementT]):
    provider_input: ProviderInputT
    measurement: MeasurementT
    context_preparation_id: str


__all__ = ["PreparedProviderContext"]
