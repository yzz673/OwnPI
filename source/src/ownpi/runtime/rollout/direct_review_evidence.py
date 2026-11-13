"""Read-only Direct review navigation rebuilt from confirmed original sources."""

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from ownpi.runtime.native_tools.history_items import HistoryMessage, ToolSearchOutputItem
from ownpi.runtime.native_tools.provider import ProviderOutputItem, ProviderOutputItemKind
from ownpi.runtime.permissions.policy import FileSystemPolicyMatcher
from ownpi.runtime.permissions.models import EffectivePermissions
from ownpi.runtime.rollout.codec import RolloutRecord, decode_entry
from ownpi.runtime.rollout.direct_inputs import direct_request_scope
from ownpi.runtime.rollout.direct_submission import direct_request_source, source_location
from ownpi.runtime.rollout.resume_read_session import ResumeReadSession
from ownpi.runtime.rollout.source_reader import OriginalCall, read_original_call
from ownpi.utils.io import write_text_atomic


@dataclass(frozen=True)
class DirectReviewEvidence:
    request_context: str
    request_context_identity: str
    review_evidence: str
    review_evidence_identity: str


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _key(source: dict[str, str]) -> str:
    return sha256(json.dumps(source, sort_keys=True).encode('utf-8')).hexdigest()


def build_direct_review_evidence(
    state_store: Any, run_dir: Path, *, review_request: OriginalCall,
    read_session: ResumeReadSession, effective_permissions: EffectivePermissions | None, project_root: Path,
) -> DirectReviewEvidence:
    source = review_request.source
    root = run_dir.resolve()
    folder = root / 'readback' / 'direct-review' / _key(source)
    request_source = direct_request_source(state_store)
    target = state_store.state.feedback_state.critic_loop.review_target
    if (review_request.metadata.get('run_id') != target.run_id
            or review_request.metadata.get('actor_kind') != 'core'
            or review_request.metadata.get('stage') != target.stage
            or review_request.metadata.get('attempt') != target.attempt):
        raise ValueError('Direct evidence requires the current Core conversation')
    files: dict[Path, str] = {}
    with read_session.locked_index(root / source['path']) as index:
        start = source_location(index, request_source, path=source['path']).offset
        stop = source_location(index, source, path=source['path']).offset
        if start >= stop:
            raise ValueError('Direct review precedes its request')
        requests = []
        for event in state_store.run_event_store.events:
            data = event.data.to_object()
            input_id = str(data.get('input_id') or '')
            if event.kind != 'direct_input_delivered' or not input_id.startswith('direct-user:'):
                continue
            original = data['source']
            location = source_location(index, original, path=source['path'])
            if location.offset > start:
                continue
            _, raw = next(read_session.raw_records(index, (location,)))
            entry = decode_entry(RolloutRecord.from_dict(raw))
            if (not isinstance(entry.item, HistoryMessage) or entry.item.role != 'user'
                    or entry.exchange_id != input_id
                    or event.event_id != f'direct-input:{input_id}'):
                raise ValueError('Direct request source is not its original user message')
            path = folder / 'requests' / f'{_key(original)}.txt'
            files[path] = entry.item.text
            requests.append((location.offset, {
                'request_scope': input_id.removeprefix('direct-user:'),
                'source': original, 'timestamp': location.timestamp, 'path': str(path),
            }))
        scope = direct_request_scope(stage=target.stage, attempt=target.attempt,
                                     loop_index=target.loop_index)
        if (len({offset for offset, _ in requests}) != len(requests)
                or not any(item['source'] == request_source and item['request_scope'] == scope
                           for _, item in requests)):
            raise ValueError('Direct request evidence has missing or ambiguous inputs')
        files[folder / 'requests.md'] = '# Original User Requests\n\n' + '\n\n'.join(
            _json(item) for _, item in sorted(requests, key=lambda pair: pair[0]))
        calls, messages = [], []
        locations = [location for location in index.records
                     if start < location.offset <= stop and location.type == 'response_item'
                     and (location.variant == 'search' or (location.variant == 'provider'
                          and location.kind in {'message', 'tool_call', 'hosted_tool_call',
                                                'hosted_tool_result'}))]
        for location, raw in read_session.raw_records(index, locations):
            entry = decode_entry(RolloutRecord.from_dict(raw))
            item = entry.item
            original = {'path': source['path'], 'item_id': location.item_id}
            if isinstance(item, ProviderOutputItem) and item.kind is ProviderOutputItemKind.MESSAGE:
                messages.append(_json({'source': original, 'timestamp': location.timestamp,
                                       'exchange_id': entry.exchange_id, 'text': item.text}))
                continue
            detail: dict[str, Any] = {'source': original, 'timestamp': location.timestamp,
                                      'type': item.kind, 'tool_name': item.tool_name,
                                      'call_id': item.call_id, 'request_id': item.request_id}
            if isinstance(item, ProviderOutputItem) and item.kind is ProviderOutputItemKind.TOOL_CALL:
                call = read_original_call(root, original, run_id=target.run_id,
                                          read_session=read_session)
                outputs = []
                for output, output_source in zip(call.outputs, call.output_sources, strict=True):
                    output_location = source_location(index, output_source, path=source['path'])
                    if output_location.offset > stop:
                        continue
                    _, output_raw = next(read_session.raw_records(index, (output_location,)))
                    output_entry = decode_entry(RolloutRecord.from_dict(output_raw))
                    outputs.append({
                        'source': output_source, 'success': output.success,
                        'body': (output.body if isinstance(output.body, str) else
                                 [block.to_dict(redact_image_url=True) for block in output.body]),
                        'page': asdict(output_entry.page) if output_entry.page else None,
                        'projection_source': (output_entry.projection_source.to_value()
                                              if output_entry.projection_source else None),
                    })
                detail.update(arguments=item.raw_arguments, outputs=outputs,
                              success=outputs[-1]['success'] if outputs else None)
            elif isinstance(item, ToolSearchOutputItem):
                detail.update(type='tool_search_output', call_type=item.call_type,
                              success=item.success, status=item.status, execution=item.execution,
                              body=item.body, receipt_ref=item.receipt_ref,
                              tools=[tool.to_value() for tool in item.tools])
            else:
                detail.update(success=None, provider_type=item.provider_type,
                              status=item.provider_status, text=item.text,
                              semantics=_hosted_semantics(item.provider_payload.to_object()))
            path = folder / 'calls' / f'{_key(original)}.md'
            files[path] = '# Original Tool Evidence\n\n' + _json(detail)
            calls.append(_json({**{key: detail[key] for key in (
                'source', 'timestamp', 'type', 'tool_name', 'call_id', 'request_id', 'success')},
                'path': str(path)}))
        files[folder / 'tools.md'] = (
            '# Current Request Tool Evidence\n\n'
            'Recorded receipts only; null success means unknown. Outputs may already be '
            'paginated/truncated. Image data is omitted; inspect available resource references.\n\n'
            + '\n\n'.join(calls))
        files[folder / 'core-messages.md'] = (
            '# Core Process Messages and Earlier Versions\n\n'
            'Not the current submission and not proof of tool execution.\n\n' + '\n\n'.join(messages))
        read_session.check_version(index)
    if effective_permissions is None:
        raise PermissionError('Direct review evidence requires caller read permissions')
    matcher = FileSystemPolicyMatcher(effective_permissions.profile.file_system)
    for path in files:
        _validate_readback(root, path)
        if not matcher.can_read(str(path), cwd=project_root):
            raise PermissionError('Caller filesystem policy denied reading Direct review evidence')
    for path, content in files.items():
        _validate_readback(root, path)
        if not path.exists() or path.read_bytes() != content.encode('utf-8'):
            write_text_atomic(path, content)
    identity = f"{source['path']}:{source['item_id']}"
    return DirectReviewEvidence(
        request_context=f"Use file_reader for original requests: {folder / 'requests.md'}",
        request_context_identity=f'direct-request-context:{identity}',
        review_evidence=(f"Request source: {_json(request_source)}\nReview source: {_json(source)}\n"
                         f"Use file_reader for tool evidence: {folder / 'tools.md'}\n"
                         f"Process messages and earlier versions: {folder / 'core-messages.md'}"),
        review_evidence_identity=f'direct-review-evidence:{identity}',
    )


def _validate_readback(root: Path, path: Path) -> None:
    for candidate in (*reversed(path.parents), path):
        if candidate == root or not candidate.is_relative_to(root):
            continue
        if candidate.is_symlink() or (candidate.exists() and not (
            candidate.is_file() if candidate == path else candidate.is_dir()
        )):
            raise ValueError('Direct evidence requires regular files within the run')
    if not path.resolve().is_relative_to(root):
        raise ValueError('Direct evidence readback is outside the run')


def _hosted_semantics(value: Any) -> Any:
    # Only inspectable tool semantics, never headers, authentication or reasoning payloads.
    if isinstance(value, dict):
        return {key: _hosted_semantics(item) for key, item in value.items() if key in {
            'type', 'status', 'action', 'query', 'queries', 'url', 'urls', 'title', 'text',
            'content', 'results', 'sources', 'output', 'name', 'arguments', 'pattern',
        }}
    if isinstance(value, list):
        return [_hosted_semantics(item) for item in value]
    if isinstance(value, str) and value.startswith('data:'):
        return '<inline data omitted>'
    return value
