"""Read display evidence, keeping legacy task-authoring identity out of recovery."""

from bisect import bisect_right
from dataclasses import dataclass, fields
from pathlib import Path

from ownpi.runtime.native_tools.history_items import ToolOutputItem
from ownpi.runtime.native_tools.provider import ProviderOutputItem, ProviderOutputItemKind
from ownpi.runtime.rollout.call_source import validate_call_source
from ownpi.runtime.rollout.codec import RolloutRecord, decode_entry
from ownpi.runtime.rollout.resume_read_session import ResumeReadSession, SnapshotChanged
from ownpi.runtime.rollout.source_reader import OriginalCall, read_original_call


@dataclass(frozen=True)
class DisplayCall(OriginalCall):
    identity_compatibility: str = ""


def read_display_call(
    run_root: Path, source: dict[str, str], *, run_id: str,
    read_session: ResumeReadSession,
) -> DisplayCall:
    try:
        original = read_original_call(
            run_root, source, run_id=run_id, read_session=read_session,
        )
    except ValueError as error:
        if str(error) != 'Business result has conflicting call identities':
            raise
        return _read_legacy_authoring(run_root, source, run_id, read_session)
    return DisplayCall(**{field.name: getattr(original, field.name) for field in fields(OriginalCall)})


def _read_legacy_authoring(
    run_root: Path, source: dict[str, str], run_id: str, session: ResumeReadSession,
) -> DisplayCall:
    source = validate_call_source(source)
    root = run_root.resolve()
    path = (root / source['path']).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Business source is outside its run')
    conflict = 'Business result has conflicting call identities'
    for _ in range(3):
        with session.locked_index(path) as index:
            try:
                metadata = index.descriptor.metadata
                if (not metadata.get('run_id')
                        or (run_id and metadata['run_id'] != run_id)
                        or any(metadata.get(key) != 'task_authoring'
                               for key in ('actor_kind', 'actor_id', 'stage'))):
                    raise ValueError(conflict)
                matches = index.originals.get(source['item_id'], ())
                if len(matches) != 1:
                    raise ValueError('Business source must identify one original item')
                location = matches[0]
                _, raw = next(session.raw_records(index, (location,)))
                record = RolloutRecord.from_dict(raw)
                entry = decode_entry(record)
                call = entry.item
                if (not isinstance(call, ProviderOutputItem)
                        or call.kind is not ProviderOutputItemKind.TOOL_CALL
                        or call.tool_name not in {'write_task_spec', 'inspect_user_path'}
                        or not call.call_id or call.call_id != call.request_id):
                    raise ValueError(conflict)
                # Only this invocation may supply a result. A later reuse of
                # the provider ID must never become this call's evidence.
                calls = index.wire_calls.get(call.call_id, ())
                next_call = bisect_right(calls, location.offset, key=lambda item: item.offset)
                stop = calls[next_call].offset if next_call < len(calls) else index.version[2]
                outputs, output_sources = [], []
                compatibility = False
                candidates = index.outputs.get((call.tool_name, call.call_id), ())
                candidates = tuple(item for item in candidates if location.offset < item.offset < stop)
                for _, raw in session.raw_records(index, candidates):
                    output_record = RolloutRecord.from_dict(raw)
                    item = decode_entry(output_record).item
                    if (not isinstance(item, ToolOutputItem) or item.tool_name != call.tool_name
                            or item.call_id != call.call_id
                            or item.request_id not in {call.request_id, f"{metadata['run_id']}:task_authoring"}):
                        raise ValueError(conflict)
                    compatibility |= item.request_id != call.request_id
                    outputs.append(item)
                    output_sources.append({
                        'path': source['path'],
                        'item_id': output_record.metadata.to_object()['item_id'],
                    })
                session.check_version(index)
                if not compatibility:
                    raise ValueError(conflict)
                # Do not populate index.calls: backend recovery remains strict
                # even when it shares this read session with the UI.
                return DisplayCall(
                    source=source, metadata=metadata, entry=entry, timestamp=record.timestamp,
                    outputs=tuple(outputs), output_sources=tuple(output_sources),
                    identity_compatibility=(
                        'Legacy task-authoring result: matched by exact provider call ID within '
                        'its original invocation; task-level request ID retained.'
                    ),
                )
            except SnapshotChanged:
                session.invalidate(path)
    raise OSError(f'Rollout changed during display read: {path}')
