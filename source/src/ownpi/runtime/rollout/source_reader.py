"""Read original business calls without replaying tools or compacted history."""

from __future__ import annotations

from dataclasses import dataclass, replace
from bisect import bisect_right
import json
from pathlib import Path
from typing import Any, cast

from ownpi.runtime.native_tools.history_items import HistoryEntry, ToolOutputItem
from ownpi.runtime.native_tools.provider import ProviderOutputItem, ProviderOutputItemKind
from ownpi.runtime.rollout.call_source import validate_call_source
from ownpi.runtime.rollout.codec import RolloutRecord, decode_entry
from ownpi.runtime.rollout.reader import open_history
from ownpi.runtime.rollout.resume_read_session import ResumeReadSession, SnapshotChanged


@dataclass(frozen=True)
class OriginalCall:
    source: dict[str, str]
    metadata: dict[str, Any]
    entry: HistoryEntry
    timestamp: str
    outputs: tuple[ToolOutputItem, ...]
    output_sources: tuple[dict[str, str], ...] = ()

    @property
    def call(self) -> ProviderOutputItem:
        return cast(ProviderOutputItem, self.entry.item)

    @property
    def arguments(self) -> dict[str, Any]:
        value = json.loads(self.call.raw_arguments)
        if not isinstance(value, dict):
            raise ValueError('Business call arguments must be an object')
        return value

    @property
    def delivered(self) -> bool:
        return bool(self.outputs)


def original_items(path: Path) -> tuple[dict[str, Any], tuple[RolloutRecord, ...]]:
    result = open_history(path)
    metadata = next(record.payload.to_object() for record in result.records
                    if record.type == 'session_meta'
                    and record.payload.to_object().get('id') == result.conversation_id)
    return metadata, tuple(record for record in result.records if record.type == 'response_item')


def read_original_call(run_root: Path, source: dict[str, str], *,
                       run_id: str = '', tool_name: str = '', actor_id: str = '',
                       read_session: ResumeReadSession | None = None) -> OriginalCall:
    source = validate_call_source(source)
    root = run_root.resolve()
    path = (root / source['path']).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Business source is outside its run')
    session = read_session or ResumeReadSession()
    for _ in range(3):
        with session.locked_index(path) as index:
            metadata = index.descriptor.metadata
            if run_id and metadata.get('run_id') != run_id:
                raise ValueError('Business source belongs to another run')
            if actor_id and metadata.get('actor_id') != actor_id:
                raise ValueError('Business source belongs to another actor')
            matches = index.originals.get(source['item_id'], ())
            if len(matches) != 1:
                raise ValueError('Business source must identify one original item')
            try:
                cached = index.calls.get(source['item_id'])
                if cached is None:
                    cached = _read_call(session, index, matches[0], source)
                session.check_version(index)
            except SnapshotChanged:
                session.invalidate(path)
                continue
            index.calls[source['item_id']] = cached
            if tool_name and cached.call.tool_name != tool_name:
                raise ValueError('Business source has a different tool identity')
            return cached if cached.source == source else replace(
                cached, source=source,
                output_sources=tuple({**output, 'path': source['path']}
                                     for output in cached.output_sources),
            )
    raise OSError(f'Rollout changed during recovery read: {path}')


def _read_call(session, index, location, source) -> OriginalCall:
    _, raw = next(session.raw_records(index, (location,)))
    record = RolloutRecord.from_dict(raw)
    entry = decode_entry(record)
    call = entry.item
    if not isinstance(call, ProviderOutputItem) or call.kind is not ProviderOutputItemKind.TOOL_CALL:
        raise ValueError('Business source is not a tool call')
    identities = {value for value in (call.call_id, call.request_id) if value}
    if not identities:
        raise ValueError('Business source lacks a call identity')
    outputs: list[ToolOutputItem] = []
    output_sources: list[dict[str, str]] = []
    end = index.version[2]
    for identity in identities:
        calls = index.wire_calls.get(identity, ())
        position = bisect_right(calls, location.offset, key=lambda value: value.offset)
        if position < len(calls):
            end = min(end, calls[position].offset)
    candidates = {}
    for identity in identities:
        matches = index.outputs.get((call.tool_name, identity), ())
        start = bisect_right(matches, location.offset, key=lambda value: value.offset)
        stop = bisect_right(matches, end, key=lambda value: value.offset)
        for output in matches[start:stop]:
            candidates[output.offset] = output
    for _, raw in session.raw_records(index, (candidates[key] for key in sorted(candidates))):
        later = RolloutRecord.from_dict(raw)
        item = decode_entry(later).item
        if isinstance(item, ProviderOutputItem) and item.kind is ProviderOutputItemKind.TOOL_CALL:
            if identities.intersection({item.call_id, item.request_id}):
                break
        if isinstance(item, ToolOutputItem) and item.tool_name == call.tool_name:
            if identities.intersection({item.call_id, item.request_id}):
                if ((item.call_id and call.call_id and item.call_id != call.call_id)
                        or (item.request_id and call.request_id and item.request_id != call.request_id)):
                    raise ValueError('Business result has conflicting call identities')
                outputs.append(item)
                output_sources.append({'path': source['path'], 'item_id': later.metadata.to_object()['item_id']})
    return OriginalCall(source, index.descriptor.metadata, entry, record.timestamp,
                        tuple(outputs), tuple(output_sources))
