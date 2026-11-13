"""Recovery-scoped file indexes; no writer ownership or workflow decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from contextlib import contextmanager
import json
from pathlib import Path
from threading import RLock
from typing import Any, Iterator, TYPE_CHECKING

from ownpi.runtime.rollout.codec import RolloutRecord, validate_raw_record

if TYPE_CHECKING:
    from ownpi.runtime.rollout.resume import ResumedConversation


def file_version(path: Path) -> tuple[int, int, int, int]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


class SnapshotChanged(OSError):
    """The offsets no longer describe the current file."""


@dataclass(frozen=True)
class ConversationMetadata:
    path: Path
    conversation_id: str
    metadata: dict[str, Any]
    parse_errors: int
    read_session: ResumeReadSession = field(repr=False, compare=False)
    file_version: tuple[int, int, int, int] = field(repr=False, compare=False)


@dataclass(frozen=True)
class RecordLocation:
    offset: int
    type: str
    item_id: str = ''
    variant: str = ''
    kind: str = ''
    tool_name: str = ''
    call_id: str = ''
    request_id: str = ''
    replacement_ids: tuple[str, ...] = ()
    timestamp: str = ''


@dataclass
class ConversationIndex:
    descriptor: ConversationMetadata
    version: tuple[int, int, int, int]
    records: tuple[RecordLocation, ...]
    metadata: tuple[dict[str, Any], ...]
    spawns: tuple[dict[str, Any], ...]
    originals: dict[str, tuple[RecordLocation, ...]]
    wire_calls: dict[str, tuple[RecordLocation, ...]]
    outputs: dict[tuple[str, str], tuple[RecordLocation, ...]]
    replay: ResumedConversation | None = None
    calls: dict[str, Any] = field(default_factory=dict)


class ResumeReadSession:
    def __init__(self) -> None:
        self._indexes: dict[Path, ConversationIndex] = {}
        self._locks: dict[Path, RLock] = {}
        self._lock = RLock()
        self._relationship_lock = RLock()
        self._business: dict[Path, tuple[tuple[int, int, int, int], Any]] = {}
        self._relationships: dict[Path, tuple[Any, Any]] = {}
        self._hints: dict[Path, dict[str, Any]] = {}

    def __enter__(self) -> ResumeReadSession:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.clear()

    def metadata_hint(self, path: Path) -> dict[str, Any] | None:
        return self._hints.get(path.resolve())

    def _file_lock(self, path: Path) -> RLock:
        with self._lock:
            return self._locks.setdefault(path, RLock())

    def invalidate(self, path: Path) -> None:
        path = path.resolve()
        with self._file_lock(path):
            self._indexes.pop(path, None)
            self._business.pop(path, None)

    def release_replay(self, path: Path) -> None:
        path = path.resolve()
        with self._file_lock(path):
            index = self._indexes.get(path)
            if index is not None:
                index.replay = None

    def clear(self) -> None:
        with self._lock:
            self._indexes.clear()
            self._business.clear()
            self._relationships.clear()
            self._hints.clear()

    def index(self, path: Path) -> ConversationIndex:
        path = path.expanduser().resolve(strict=True)
        if not path.is_file():
            raise OSError(f'Rollout is not a file: {path}')
        with self._file_lock(path):
            version = file_version(path)
            cached = self._indexes.get(path)
            if cached is not None and cached.version == version:
                return cached
            # A changing file never publishes a partly scanned recovery snapshot.
            for _ in range(3):
                self._hints.pop(path, None)
                result = self._scan(path, version)
                current = file_version(path)
                if current == version:
                    self._indexes[path] = result
                    return result
                version = current
            raise OSError(f'Rollout changed during recovery read: {path}')

    def _scan(self, path: Path, version: tuple[int, int, int, int]) -> ConversationIndex:
        records: list[RecordLocation] = []
        metas: list[dict[str, Any]] = []
        spawns: list[dict[str, Any]] = []
        originals: dict[str, list[RecordLocation]] = {}
        wire_calls: dict[str, list[RecordLocation]] = {}
        outputs: dict[tuple[str, str], list[RecordLocation]] = {}
        errors, nonempty = 0, False
        with path.open('rb') as source:
            while True:
                offset = source.tell()
                line = source.readline()
                if not line:
                    break
                if not line.strip():
                    continue
                nonempty = True
                # Decode UTF-8 outside the malformed-record boundary, as read_rollout does.
                text = line.decode('utf-8')
                try:
                    raw = json.loads(text)
                    validate_raw_record(raw)
                except (ValueError, TypeError, KeyError, AttributeError):
                    errors += 1
                    continue
                kind, payload, meta = raw['type'], raw['payload'], raw.get('metadata', {})
                if kind == 'session_meta':
                    metas.append(payload)
                    self._hints.setdefault(path, payload)
                if kind == 'event_msg' and payload.get('type') == 'thread_spawned':
                    spawns.append(payload)
                variant = meta.get('variant', '') if kind == 'response_item' else ''
                adapter = meta.get('adapter', {}) if variant == 'provider' else {}
                location = RecordLocation(
                    offset, kind, str(meta.get('item_id') or ''), variant,
                    meta.get('kind', '') if variant == 'provider' else '',
                    adapter.get('tool_name', payload.get('name', '')) if variant == 'provider'
                    else payload.get('tool_name', '') if variant in {'tool', 'search'} else '',
                    adapter.get('call_id', payload.get('call_id', '')) if variant == 'provider'
                    else payload.get('call_id', '') if variant in {'tool', 'search'} else '',
                    adapter.get('request_id', '') if variant == 'provider'
                    else payload.get('request_id', '') if variant in {'tool', 'search'} else '',
                    tuple(str(value.get('item_id') or '') for value in
                          payload['replacement_history_metadata']) if kind == 'compacted' else (),
                    raw['timestamp'],
                )
                records.append(location)
                if kind == 'response_item':
                    originals.setdefault(location.item_id, []).append(location)
                    for identity in {location.call_id, location.request_id} - {''}:
                        if variant == 'provider' and location.kind == 'tool_call':
                            wire_calls.setdefault(identity, []).append(location)
                        elif variant == 'tool':
                            outputs.setdefault((location.tool_name, identity), []).append(location)
        if not nonempty:
            raise OSError('empty session file')
        if not metas:
            raise OSError('failed to parse conversation ID from rollout file')
        if metas[0].get('scope', 'conversation') != 'conversation':
            raise OSError('Run events are not conversation history')
        descriptor = ConversationMetadata(path, metas[0]['id'], metas[0], errors, self, version)
        return ConversationIndex(descriptor, version, tuple(records), tuple(metas), tuple(spawns),
                                 {key: tuple(values) for key, values in originals.items()},
                                 {key: tuple(values) for key, values in wire_calls.items()},
                                 {key: tuple(values) for key, values in outputs.items()})

    def raw_records(self, index: ConversationIndex,
                    locations: Any) -> Iterator[tuple[RecordLocation, dict[str, Any]]]:
        self.check_version(index)
        with index.descriptor.path.open('rb') as source:
            for location in locations:
                source.seek(location.offset)
                yield location, json.loads(source.readline().decode('utf-8'))
        self.check_version(index)

    def check_version(self, index: ConversationIndex) -> None:
        if file_version(index.descriptor.path) != index.version:
            raise SnapshotChanged(f'Rollout changed during recovery read: {index.descriptor.path}')

    @contextmanager
    def locked_index(self, path: Path) -> Iterator[ConversationIndex]:
        path = path.resolve()
        with self._file_lock(path):
            yield self.index(path)

    def replay_records(self, index: ConversationIndex, checkpoint: int
                       ) -> Iterator[tuple[RecordLocation, dict[str, Any] | None]]:
        self.check_version(index)
        with index.descriptor.path.open('rb') as source:
            for location in index.records:
                if location.type == 'compacted' and location.offset != checkpoint:
                    yield location, None
                    continue
                if (location.type in {'response_item', 'inter_agent_communication'}
                        and location.offset < checkpoint):
                    continue
                if location.type not in {'response_item', 'compacted', 'turn_context', 'event_msg',
                                         'inter_agent_communication'}:
                    continue
                source.seek(location.offset)
                yield location, json.loads(source.readline().decode('utf-8'))
        self.check_version(index)

    def original_records(self, path: Path, *, tool_names: frozenset[str] | None = None,
                         variants: frozenset[str] | None = None) -> Iterator[RolloutRecord]:
        index = self.index(path)
        locations = (value for value in index.records if value.type == 'response_item'
                     and (tool_names is None or value.tool_name in tool_names)
                     and (variants is None or value.variant in variants))
        for _, raw in self.raw_records(index, locations):
            yield RolloutRecord.from_dict(raw)

    def original_call_sources(self, run_root: Path, path: Path, *,
                              tool_names: frozenset[str]) -> tuple[dict[str, str], ...]:
        relative = str(path.resolve().relative_to(run_root.resolve()))
        index = self.index(path)
        return tuple({'path': relative, 'item_id': value.item_id} for value in index.records
                     if value.type == 'response_item' and value.variant == 'provider'
                     and value.kind == 'tool_call' and value.tool_name in tool_names)

    def replay(self, path: Path, *, conversation_id: str | None = None) -> ResumedConversation:
        from ownpi.runtime.rollout.resume import _replay_index

        path = path.expanduser().resolve(strict=True)
        with self._file_lock(path):
            for _ in range(3):
                index = self.index(path)
                if conversation_id is not None and conversation_id != index.descriptor.conversation_id:
                    raise ValueError('Conversation ID does not match rollout metadata')
                try:
                    if index.replay is None:
                        index.replay = _replay_index(self, index)
                    self.check_version(index)
                    return index.replay
                except SnapshotChanged:
                    self.invalidate(path)
            raise OSError(f'Rollout changed during recovery read: {path}')

    def business_events(self, path: Path, *, run_id: str | None = None) -> Any:
        from ownpi.runtime.rollout.business_reader import read_business_events

        path = path.resolve()
        with self._file_lock(path):
            for _ in range(3):
                version = file_version(path)
                cached = self._business.get(path)
                if cached is not None and cached[0] == version:
                    result = cached[1]
                else:
                    result = read_business_events(path, run_id=run_id)
                    if file_version(path) != version:
                        continue
                    self._business[path] = version, result
                if run_id and result.run_id != run_id:
                    raise ValueError('Run event identity differs from current run')
                return result
            raise OSError(f'Run events changed during recovery read: {path}')

    def signature(self, run_dir: Path) -> tuple:
        root = run_dir.resolve()
        paths = sorted({*root.glob('rollouts/*.jsonl'), *root.glob('subagents/*/rollouts/*.jsonl')})
        signature = []
        for path in paths:
            try:
                if path.name != 'run-events.jsonl' and path.is_file():
                    signature.append((path.resolve(), file_version(path)))
            except OSError:
                signature.append((path, None))
        return tuple(signature)

    def relationships(self, run_dir: Path) -> Any:
        from ownpi.runtime.rollout.agent_relationships import read_agent_relationships

        root = run_dir.resolve()
        key = self.signature(root)
        with self._relationship_lock:
            cached = self._relationships.get(root)
            if cached is None or cached[0] != key:
                result = read_agent_relationships(root, read_session=self)
                self._relationships[root] = key, result
            return self._relationships[root][1]
