from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, TypeVar
from uuid import uuid4

from ownpi.runtime.context.budget_snapshot import budget_snapshot_from_measurement
from ownpi.runtime.context.contracts import PreparedProviderContext
from ownpi.runtime.context.provider_usage import read_provider_usage
from ownpi.runtime.native_tools.provider_request_measurement import (
    NativeProviderRequestMeasurement,
)
from ownpi.runtime.state_models import RuntimeState, TokenBudgetSnapshot
from ownpi.schemas.runtime import LLMConfig

ProviderInputT = TypeVar("ProviderInputT")
MeasurementT = TypeVar("MeasurementT")


@dataclass(frozen=True)
class _PendingProviderContext:
    estimated_tokens: int
    snapshot: TokenBudgetSnapshot | None


class ContextRuntimeManager:
    """Pair provider requests with usage without retaining request contents."""

    def __init__(
        self, *, state: RuntimeState | None = None, llm_config: LLMConfig | None = None
    ) -> None:
        self._state = state
        self._llm_config = llm_config
        self._pending: dict[str, _PendingProviderContext] = {}

    def prepare_provider_context(
        self,
        *,
        build_input: Callable[[], ProviderInputT],
        tool_specs: Iterable[Any],
        measure: Callable[[ProviderInputT, tuple[Any, ...]], MeasurementT],
    ) -> PreparedProviderContext[ProviderInputT, MeasurementT]:
        specs = tuple(tool_specs)
        provider_input = build_input()
        measurement = measure(provider_input, specs)
        snapshot = None
        if self._state is not None and isinstance(
            measurement, NativeProviderRequestMeasurement
        ):
            snapshot = budget_snapshot_from_measurement(
                measurement, llm_config=self._llm_config
            )
            self._state.context_state.token_budget_snapshot = snapshot
        preparation_id = uuid4().hex
        self._pending[preparation_id] = _PendingProviderContext(
            estimated_tokens=_estimated_tokens(measurement),
            snapshot=snapshot,
        )
        return PreparedProviderContext(
            provider_input=provider_input,
            measurement=measurement,
            context_preparation_id=preparation_id,
        )

    def commit_provider_usage(
        self,
        prepared_context_id: str,
        canonical_usage: Mapping[str, Any],
    ) -> None:
        pending = self._pending.pop(prepared_context_id, None)
        if pending is None or pending.snapshot is None:
            return
        snapshot = pending.snapshot
        usage = read_provider_usage(canonical_usage)
        input_tokens = usage["provider_prompt_tokens"]
        output_tokens = usage["provider_completion_tokens"]
        total_tokens = usage["provider_total_tokens"]
        if (
            total_tokens is None
            and input_tokens is not None
            and output_tokens is not None
        ):
            total_tokens = input_tokens + output_tokens
        snapshot.provider_prompt_tokens = input_tokens
        snapshot.provider_completion_tokens = output_tokens
        snapshot.provider_total_tokens = total_tokens
        snapshot.provider_cache_read_tokens = usage["provider_cache_read_tokens"]
        snapshot.provider_cache_write_tokens = usage["provider_cache_write_tokens"]
        snapshot.estimate_vs_provider_delta = (
            input_tokens - pending.estimated_tokens
            if input_tokens is not None
            else None
        )
        snapshot.usage_source = "provider_reported"

    def abort_provider_context(self, prepared_context_id: str) -> None:
        self._pending.pop(prepared_context_id, None)


def _estimated_tokens(measurement: object) -> int:
    return max(
        0,
        int(
            getattr(
                measurement,
                "estimated_tokens",
                getattr(measurement, "value", 0),
            )
            or 0
        ),
    )


__all__ = ["ContextRuntimeManager"]
