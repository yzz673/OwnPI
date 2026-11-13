"""Recover canonical feedback bindings from submitted facts and original calls."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Iterable

from ownpi.runtime.rollout.business_events import BusinessEvent
from ownpi.runtime.rollout.source_reader import read_original_call
from ownpi.schemas.feedback import DirectReviewTarget, FeedbackRecord, ReviewTarget, WorkspaceReviewTarget
from ownpi.utils.io import write_text_atomic
from ownpi.runtime.rollout.resume_read_session import ResumeReadSession


def review_target(events: Iterable[BusinessEvent], event_id: str, *,
                  run_root: Path, run_id: str, read_session: ResumeReadSession | None = None) -> tuple[ReviewTarget, int]:
    by_id = {event.event_id: event for event in events}
    review = by_id[event_id].data.to_object()
    if review.get('action') != 'review_started':
        raise ValueError('Feedback references a non-review event')
    accepted = by_id[review['accepted_event_id']].data.to_object()
    if accepted.get('action') != 'core_intent_accepted':
        raise ValueError('Review references a non-intent event')
    source = read_original_call(run_root, accepted['source'], run_id=run_id, tool_name='request_review', read_session=read_session)
    target: ReviewTarget
    if review.get('target_kind') == 'direct':
        if 'contract_sha256' in review:
            raise ValueError('Direct review cannot bind a stage contract')
        target = DirectReviewTarget(run_id, accepted['stage'], accepted['attempt'],
                                    accepted['stage_loop_index'],
                                    source.call.request_id or source.call.call_id)
    elif 'target_kind' not in review:
        target = WorkspaceReviewTarget(run_id, accepted['stage'], accepted['attempt'],
                                       accepted['stage_loop_index'], review['contract_sha256'],
                                       source.call.request_id or source.call.call_id)
    else:
        raise ValueError('Unknown review target kind')
    return target, accepted['round'] + 1


def project_feedback_record(events: Iterable[BusinessEvent], *, run_root: Path,
                            run_id: str, record_id: str,
                            expected_record: FeedbackRecord | None = None,
                            read_session: ResumeReadSession | None = None) -> FeedbackRecord | None:
    read_session = read_session or ResumeReadSession()
    events = tuple(events)
    matches = [event for event in events if event.kind == 'feedback_submitted'
               and event.data.to_object().get('record_id') == record_id]
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError('Feedback submission identity is ambiguous')
    event = matches[0]
    data = event.data.to_object()
    call = read_original_call(run_root, data['source'], run_id=run_id, tool_name='submit_feedback', read_session=read_session)
    root = run_root.resolve()
    path = (root / data['path']).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Canonical feedback is outside its run')
    content = call.arguments['content']
    if not isinstance(content, str) or sha256(content.encode('utf-8')).hexdigest() != data['content_hash']:
        raise ValueError('Original feedback digest differs from submission')
    if 'review_event_id' in data:
        target, round_index = review_target(events, data['review_event_id'], run_root=root, run_id=run_id, read_session=read_session)
        role, stage, attempt, loop, identity = ('reviewer', target.stage, target.attempt,
                                               target.loop_index, target.identity)
    else:
        role, stage, attempt, loop, identity = (data['author_role'], data['stage'], data['attempt'],
                                               data['stage_loop_index'], data['review_target'])
        round_index = 1
    record = FeedbackRecord(record_id, role, stage, attempt, round_index, identity,
                            f'native_tool:{call.call.request_id or call.call.call_id}',
                            str(path), data['content_hash'], len(content), event.timestamp, loop)
    if expected_record is not None:
        verify_feedback_binding(record, expected_record)
    candidate = root / data['path']
    try:
        saved = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        if candidate.is_symlink():
            raise
        write_text_atomic(path, content)
        saved = path.read_text(encoding='utf-8')
    if sha256(saved.encode('utf-8')).hexdigest() != data['content_hash']:
        raise ValueError('Canonical feedback digest differs from submission')
    if saved != content:
        raise ValueError('Canonical feedback differs from the submitted call')
    return record


def verify_feedback_binding(saved: FeedbackRecord, record: FeedbackRecord) -> None:
    fields = ('record_id', 'author_role', 'source_stage', 'attempt', 'round', 'review_target',
              'provider_operation_id', 'feedback_path', 'content_hash', 'content_chars', 'stage_loop_index')
    if any(getattr(saved, field) != getattr(record, field) for field in fields):
        raise ValueError('Feedback submission changed its canonical binding')
