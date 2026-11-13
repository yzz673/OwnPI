from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

ContextBudgetMode = Literal["combined", "input_only"]


class NativeCompactionTrigger(str, Enum):
    AUTO = "auto"
    PROVIDER_OVERFLOW = "provider_overflow"


class NativeCompactionPhase(str, Enum):
    PRE_SAMPLING = "pre_sampling"
    MID_LOOP = "mid_loop"
    OVERFLOW_RECOVERY = "overflow_recovery"


@dataclass(frozen=True)
class NativeModelContextMetadataV1:
    provider_identity: str
    model_identity: str
    metadata_source: str
    context_budget_mode: ContextBudgetMode
    context_window_tokens: int
    maximum_output_tokens: int
    auto_compact_limit_tokens: int | None = None
    protocol_overhead_tokens: int = 0

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.provider_identity,
                self.model_identity,
                self.metadata_source,
            )
        ):
            raise ValueError("model context metadata requires provider, model, and source")
        if self.context_budget_mode not in {"combined", "input_only"}:
            raise ValueError("unsupported model context budget mode")
        if self.context_window_tokens < 1 or self.maximum_output_tokens < 1:
            raise ValueError("model context limits must be positive")
        if self.auto_compact_limit_tokens is not None and self.auto_compact_limit_tokens < 1:
            raise ValueError("auto compact limit must be positive when present")
        if self.protocol_overhead_tokens < 0:
            raise ValueError("model context overhead accounting is invalid")


@dataclass(frozen=True)
class NativeResolvedCompactionBudgetV1:
    model_identity: str
    provider_identity: str
    metadata_source: str
    context_window_tokens: int
    effective_input_limit_tokens: int
    requested_response_reserve_tokens: int
    auto_compact_limit_tokens: int
    protocol_overhead_tokens: int
    unknown_model_budget: bool = False

    def __post_init__(self) -> None:
        positive = (
            self.context_window_tokens,
            self.effective_input_limit_tokens,
            self.requested_response_reserve_tokens,
            self.auto_compact_limit_tokens,
        )
        if min(positive) < 1 or self.protocol_overhead_tokens < 0:
            raise ValueError("resolved compaction budget is invalid")
        if self.auto_compact_limit_tokens > self.effective_input_limit_tokens:
            raise ValueError("auto compact limit exceeds effective input limit")
