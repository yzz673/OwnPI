"""Current-context usage, separate from cumulative billing counters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from ownpi.runtime.native_tools.history_items import HistoryEntry, HistoryMessage
from ownpi.runtime.native_tools.provider import ProviderOutputEnvelope, ProviderOutputItem, ProviderOutputItemKind
from ownpi.runtime.native_tools.provider_request_measurement import NativeProviderRequestMeasurement, _model_item_payload
from ownpi.runtime.native_tools.token_estimation import estimate_item_tokens


def estimate_entries(entries: tuple[HistoryEntry, ...]) -> int:
    return sum(estimate_item_tokens(_model_item_payload(entry)) for entry in entries)


def external_tokens(measurement: NativeProviderRequestMeasurement) -> int:
    return (measurement.prefix_tokens + measurement.tool_schema_tokens
            + measurement.tool_search_output_tokens + measurement.envelope_tokens)


@dataclass
class HistoryUsage:
    total: int | None = None
    recorded_count: int = 0
    generation: int = -1
    external: int = 0
    model_key: tuple[str, str] = ('', '')
    _static_properties: str = field(default='', init=False, repr=False)
    _baseline: tuple[HistoryEntry, ...] = field(default=(), init=False, repr=False)
    _server_reasoning_included: bool = field(default=True, init=False, repr=False)
    _normalization_tokens: int = field(default=0, init=False, repr=False)

    def estimate(self, entries: tuple[HistoryEntry, ...], generation: int,
                 measurement: NativeProviderRequestMeasurement) -> int:
        outside = external_tokens(measurement)
        if (self.total is None or generation != self.generation
                or self.model_key != (measurement.provider_id, measurement.model_id)
                or self._static_properties != measurement.request_static_properties_digest
                or entries[:self.recorded_count] != self._baseline):
            self.reset()
            return measurement.estimated_tokens
        estimate = measurement.estimate_history or estimate_entries
        reasoning = 0 if self._server_reasoning_included else _past_reasoning_tokens(entries, estimate)
        normalization = measurement.estimate_normalization(entries) if measurement.estimate_normalization else 0
        return max(0, self.total + estimate(entries[self.recorded_count:])
                   + reasoning + outside - self.external
                   + normalization - self._normalization_tokens)

    def completed(self, response: ProviderOutputEnvelope, entries: tuple[HistoryEntry, ...],
                  generation: int, measurement: NativeProviderRequestMeasurement) -> None:
        """Install the latest completion baseline; entries include its recorded items.

        This never sums billed usage or acknowledges delivery of protected pages.
        Only the normal-request owner calls this after successful completion.
        """
        self.total = _context_total(response)
        # Locally appended outputs/hooks after the completion are not in its usage.
        positions = [index for index, entry in enumerate(entries)
                     if isinstance(entry.item, ProviderOutputItem)
                     and any(entry.item is item for item in response.completed_items)]
        self.recorded_count = positions[-1] + 1 if positions else len(entries)
        self._baseline = entries[:self.recorded_count]
        self.generation = generation
        self.external = external_tokens(measurement)
        request_entries = entries[:positions[0]] if positions else entries
        self._normalization_tokens = (measurement.estimate_normalization(request_entries)
                                      if measurement.estimate_normalization else 0)
        self.model_key = (measurement.provider_id, measurement.model_id)
        self._static_properties = measurement.request_static_properties_digest
        self._server_reasoning_included = (
            response.completion.provider_metadata.to_object().get('server_reasoning_included') is not False
        )

    def mark_full(self, entries: tuple[HistoryEntry, ...], generation: int,
                  measurement: NativeProviderRequestMeasurement, limit: int) -> None:
        """Mark a normal-request overflow as a full current-context baseline.

        This is occupancy state, not provider-reported or billed usage. The
        supplied limit already covers the snapshot and its external inputs.
        """
        if type(limit) is not int or limit < 0:
            raise ValueError('context limit must be a non-negative integer')
        self.total = limit
        self.recorded_count = len(entries)
        self._baseline = entries
        self.generation = generation
        self.external = external_tokens(measurement)
        self._normalization_tokens = (measurement.estimate_normalization(entries)
                                      if measurement.estimate_normalization else 0)
        self.model_key = (measurement.provider_id, measurement.model_id)
        self._static_properties = measurement.request_static_properties_digest
        self._server_reasoning_included = True

    def reset(self) -> None:
        self.total = None
        self.recorded_count = 0
        self.generation = -1
        self.external = 0
        self.model_key = ('', '')
        self._static_properties = ''
        self._baseline = ()
        self._server_reasoning_included = True
        self._normalization_tokens = 0


def _past_reasoning_tokens(entries: tuple[HistoryEntry, ...], estimate=estimate_entries) -> int:
    last_user = next((index for index in range(len(entries) - 1, -1, -1)
                      if _is_user_message(entries[index])), None)
    if last_user is None:
        return 0
    return estimate(tuple(entry for entry in entries[:last_user]
                                  if isinstance(entry.item, ProviderOutputItem)
                                  and entry.item.kind == ProviderOutputItemKind.REASONING
                                  and isinstance(entry.item.provider_payload.to_object().get('encrypted_content'), str)))


def _is_user_message(entry: HistoryEntry) -> bool:
    item = entry.item
    if isinstance(item, HistoryMessage):
        return item.role == 'user'
    return (isinstance(item, ProviderOutputItem) and item.kind == ProviderOutputItemKind.MESSAGE
            and item.provider_payload.to_object().get('role') == 'user')


def _counter(values: Mapping[str, object], *names: str) -> int | None:
    return next((value for name in names if type(value := values.get(name)) is int and value >= 0), None)


def _context_total(response: ProviderOutputEnvelope) -> int | None:
    completion = response.completion
    usage = response.usage
    exact = completion.exact_usage
    raw = exact if isinstance(exact, dict) else {}
    input_tokens = _counter(raw, 'input_tokens', 'prompt_tokens')
    if input_tokens is None:
        input_tokens = _counter(usage, 'input_tokens', 'prompt_tokens', 'provider_prompt_tokens')
    output_tokens = _counter(raw, 'output_tokens', 'completion_tokens')
    if output_tokens is None:
        output_tokens = _counter(usage, 'output_tokens', 'completion_tokens', 'provider_completion_tokens')
    if completion.provider_protocol.startswith('anthropic'):
        # Anthropic input_tokens excludes both cache buckets. Its normalized total
        # is currently input + output, so derive from the disjoint components.
        if input_tokens is None or output_tokens is None:
            return None
        read = _counter(raw, 'cache_read_input_tokens')
        write = _counter(raw, 'cache_creation_input_tokens')
        return (input_tokens + output_tokens
                + (read if read is not None else _counter(usage, 'cache_read_input_tokens', 'cache_read_tokens') or 0)
                + (write if write is not None else _counter(usage, 'cache_creation_input_tokens', 'cache_write_tokens') or 0))
    total = _counter(raw, 'total_tokens')
    if total is None:
        total = _counter(usage, 'total_tokens', 'provider_total_tokens')
    # OpenAI cache and reasoning details are subsets of input/output, not additions.
    if total is not None:
        return total
    return input_tokens + output_tokens if input_tokens is not None and output_tokens is not None else None
