"""Record provider facts and tool outputs through one ordered history owner."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import replace

from ownpi.runtime.limits.tool_output import (
    DEFAULT_TOOL_RESULT_TRUNCATION_POLICY, TruncationPolicy, approx_token_count, truncate_text,
)
from ownpi.runtime.model_content import ModelContentItem
from ownpi.runtime.native_tools.history_items import HistoryEntry, HistoryMessage, ToolOutputItem
from ownpi.runtime.native_tools.history_prompt import call_key, filter_modalities, for_prompt, output_key
from ownpi.runtime.native_tools.provider import ProviderOutputEnvelope, ProviderOutputItem, ProviderOutputItemKind
from ownpi.runtime.native_tools.provider_output_attempt import iter_history_batches
from ownpi.runtime.native_tools.provider_json import FrozenProviderJson


_RECOGNIZED_PROVIDER_TYPES = frozenset({
    'message', 'agent_message', 'reasoning', 'function_call', 'function_call_output',
    'custom_tool_call', 'custom_tool_call_output', 'local_shell_call',
    'tool_search_call', 'tool_search_output', 'web_search_call',
    'image_generation_call', 'compaction', 'context_compaction', 'additional_tools',
})


class NativeHistory:
    def __init__(self, policy: TruncationPolicy = DEFAULT_TOOL_RESULT_TRUNCATION_POLICY) -> None:
        self.policy = policy
        self._items: list[HistoryEntry] = []
        self._consumed: tuple[HistoryEntry, ...] = ()
        self._generation = 0
        self.reference_context: FrozenProviderJson | None = None
        self.turn_status = 'none'
        # Recorded statistics are not a measured baseline for the current request.
        self.token_info: FrozenProviderJson | None = None

    @property
    def snapshot(self) -> tuple[HistoryEntry, ...]:
        return tuple(self._items)

    @property
    def generation(self) -> int:
        """Replacement identity for request caches, independent of ordinary appends."""
        return self._generation

    @property
    def consumed_outputs(self) -> tuple[HistoryEntry, ...]:
        """Protected entries acknowledged by the most recent normal request."""
        return self._consumed

    def for_prompt(
        self, input_modalities: Iterable[str] = ('text', 'image', 'audio'),
    ) -> tuple[HistoryEntry, ...]:
        """Return a normalized request copy; never change the recorded facts."""
        return for_prompt(self._items, input_modalities)

    def replace(self, entries: Iterable[HistoryEntry]) -> None:
        """Install a live replacement without recording, filtering or retruncation."""
        self._items = list(entries)
        retained = Counter(id(entry) for entry in self._items)
        consumed = []
        for entry in self._consumed:
            if retained[id(entry)]:
                consumed.append(entry)
                retained[id(entry)] -= 1
        self._consumed = tuple(consumed)
        self._generation += 1

    def mark_request_consumed(self, entries: Iterable[HistoryEntry]) -> None:
        """Confirm only a successful normal request's snapshot, never a compaction.

        The caller must not invoke this on failure/cancellation or summary requests.
        Both the original snapshot and its modality-filtered prompt view are accepted.
        Items appended while the request was running are not implicitly acknowledged.
        """
        remaining = [entry for entry in self._items if entry.preserve_output or entry.page is not None]
        consumed = []
        for submitted in entries:
            if not submitted.preserve_output and submitted.page is None:
                continue
            match = next((i for i, entry in enumerate(remaining) if entry is submitted), None)
            if match is None:
                match = next((i for i, entry in enumerate(remaining)
                              if _same_request_entry(entry, submitted)), None)
            if match is not None:
                consumed.append(remaining.pop(match))
        self._consumed = tuple(consumed)

    def restore_consumed(self, entries: Iterable[HistoryEntry]) -> None:
        """Install persisted acknowledgement facts without issuing a request."""
        remaining = Counter(id(entry) for entry in self._items)
        consumed = []
        for entry in entries:
            if remaining[id(entry)] and (entry.preserve_output or entry.page is not None):
                consumed.append(entry)
                remaining[id(entry)] -= 1
        self._consumed = tuple(consumed)

    def restore(self, entries: Iterable[HistoryEntry], *, generation: int,
                consumed: Iterable[HistoryEntry]) -> None:
        """Restore rollout facts without applying output truncation a second time."""
        self._items = list(entries)
        self._generation = generation
        self.restore_consumed(consumed)

    def protected_suffix(self) -> tuple[HistoryEntry, ...]:
        """Keep unconsumed full outputs, their calls and original provider batches."""
        consumed = Counter(id(entry) for entry in self._consumed)
        start = len(self._items)
        for index, entry in enumerate(self._items):
            if consumed[id(entry)]:
                consumed[id(entry)] -= 1
            elif entry.preserve_output or entry.page is not None:
                start = min(start, index)
        if start == len(self._items):
            return ()
        # Closing the continuous suffix also protects sibling outputs and hooks.
        checked = len(self._items)
        while start < checked:
            boundary = start
            for index in range(start, checked):
                call_index = _matching_call(self._items, index)
                if call_index is not None:
                    boundary = min(boundary, _batch_start(self._items, call_index))
            checked, start = start, boundary
        return tuple(self._items[start:])

    def record_items(self, entries: Iterable[HistoryEntry]) -> tuple[HistoryEntry, ...]:
        recorded = []
        for entry in entries:
            if not _is_history_item(entry):
                continue
            item = entry.item
            if isinstance(item, ToolOutputItem) and not entry.preserve_output:
                item = replace(item, body=_truncate_body(item.body, self.policy.scaled(1.2)))
                entry = replace(entry, item=item)
            self._items.append(entry)
            recorded.append(entry)
        return tuple(recorded)

    def record_provider(self, response: ProviderOutputEnvelope) -> tuple[HistoryEntry, ...]:
        return self.record_items(
            HistoryEntry(item, batch.exchange_id, item.sequence_index)
            for batch in iter_history_batches(response) for item in batch.completed_items
        )

    def record_message(self, role: str, text: str, *, exchange_id: str = '') -> None:
        self.record_items((HistoryEntry(HistoryMessage(role, text), exchange_id),))


def _is_history_item(entry: HistoryEntry) -> bool:
    item = entry.item
    if isinstance(item, HistoryMessage):
        return item.role != 'system'
    if not isinstance(item, ProviderOutputItem):
        return True
    if item.provider_type in {'compaction_trigger', 'other'}:
        return False
    if item.kind == ProviderOutputItemKind.MESSAGE:
        return item.provider_payload.to_object().get('role') != 'system'
    return item.kind != ProviderOutputItemKind.PROVIDER_SPECIFIC or item.provider_type in _RECOGNIZED_PROVIDER_TYPES


def _same_request_entry(original: HistoryEntry, submitted: HistoryEntry) -> bool:
    if (original.exchange_id, original.output_sequence_index, original.preserve_output, original.page) != (
        submitted.exchange_id, submitted.output_sequence_index, submitted.preserve_output, submitted.page,
    ):
        return False
    for modalities in (('text',), ('text', 'image'), ('text', 'audio')):
        filtered = filter_modalities(original, frozenset(modalities))
        if filtered is not original and filtered == submitted:
            return True
    return False


def _matching_call(entries: list[HistoryEntry], index: int) -> int | None:
    entry = entries[index]
    key = output_key(entry)
    if key is None:
        return None
    candidates = [i for i in range(index) if call_key(entries[i]) == key]
    if entry.exchange_id:
        same_exchange = [i for i in candidates if entries[i].exchange_id == entry.exchange_id]
        candidates = same_exchange or candidates
    same_sequence = [i for i in candidates if entry.output_sequence_index is not None
                     and entries[i].output_sequence_index == entry.output_sequence_index]
    candidates = same_sequence or candidates
    return candidates[-1] if candidates else None


def _batch_start(entries: list[HistoryEntry], index: int) -> int:
    exchange_id = entries[index].exchange_id
    while index > 0:
        previous = entries[index - 1]
        if not isinstance(previous.item, ProviderOutputItem) or previous.exchange_id != exchange_id:
            break
        current = entries[index]
        if not isinstance(current.item, ProviderOutputItem):
            break
        if not exchange_id and previous.item.sequence_index >= current.item.sequence_index:
            break
        index -= 1
    return index


def _truncate_body(
    body: str | tuple[ModelContentItem, ...], policy: TruncationPolicy,
) -> str | tuple[ModelContentItem, ...]:
    if isinstance(body, str):
        return truncate_text(body, policy)
    remaining = policy.limit
    output = []
    omitted = 0
    for block in body:
        if block.type == 'input_text':
            if remaining == 0:
                omitted += 1
                continue
            cost = len(block.text.encode('utf-8')) if policy.mode == 'bytes' else approx_token_count(block.text)
            output.append(replace(block, text=truncate_text(block.text, TruncationPolicy(policy.mode, remaining))))
            remaining = max(0, remaining - cost)
        else:
            output.append(block)
    if omitted:
        output.append(ModelContentItem.input_text(f'[omitted {omitted} text items ...]'))
    return tuple(output)
