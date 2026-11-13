"""Direct answers are original reviewed submissions, not inferred chat messages."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable

from ownpi.runtime.events import now_utc_iso
from ownpi.runtime.native_tools.provider_json import FrozenProviderJson
from ownpi.runtime.rollout.business_events import BusinessEvent
from ownpi.runtime.rollout.business_scope import business_stage
from ownpi.runtime.rollout.resume_read_session import ResumeReadSession
from ownpi.runtime.rollout.source_reader import OriginalCall, read_original_call


def answer_text(arguments: dict[str, Any], *, required: bool = False) -> str:
    if 'answer' not in arguments and not required:
        return ''
    answer = arguments.get('answer')
    if (not isinstance(answer, dict) or set(answer) != {'text'}
            or not isinstance(answer.get('text'), str) or not answer['text'].strip()):
        raise ValueError(
            'request_review requires a non-empty answer.text for task-level delivery '
            '(and whenever answer is provided). answer.text must contain only the complete '
            'user-facing answer or delivery summary; put revision notes and Reviewer-directed '
            'content in review_context, then call request_review again.'
        )
    return answer['text']


def answer_protocol_enabled(events: Iterable[BusinessEvent], *, attempt: int,
                            loop_index: int) -> bool:
    for event in events:
        if event.kind == 'answer_protocol_enabled':
            data = event.data.to_object()
            if data.get('version') != 1 or data.get('stage') != 'direct_qa':
                raise ValueError('Unsupported direct answer protocol')
            return (attempt, loop_index) >= (data['attempt'], data['loop_index'])
    return False


def enable_answer_protocol(store: Any, *, attempt: int, loop_index: int) -> None:
    if not any(e.kind == 'answer_protocol_enabled' for e in store.run_event_store.events):
        store.run_event_store.emit('answer_protocol_enabled', 'answer-protocol-enabled', {
            'version': 1, 'stage': 'direct_qa', 'attempt': attempt, 'loop_index': loop_index,
        })


@dataclass(frozen=True)
class PublishedAnswer:
    answer_id: str
    text: str
    revision: int
    content_sha256: str
    artifacts: tuple[str, ...] = ()


def _scope(data: dict[str, Any]) -> tuple[Any, Any, Any]:
    return data.get('stage'), data.get('attempt'), data.get('stage_loop_index', data.get('loop_index'))


def read_published_answer(
    events: Iterable[BusinessEvent], event: BusinessEvent, *, run_dir: Path,
    read_session: ResumeReadSession | None = None,
) -> PublishedAnswer:
    if read_session is None:
        with ResumeReadSession() as reader:
            return read_published_answer(events, event, run_dir=run_dir, read_session=reader)
    events = tuple(events)
    by_id = {item.event_id: item for item in events}
    if len(by_id) != len(events) or by_id.get(event.event_id) != event:
        raise ValueError('Answer publication is missing or ambiguous')
    if event.kind != 'answer_published':
        raise ValueError('Not an answer publication')
    order = {item.event_id: i for i, item in enumerate(events)}
    data = event.data.to_object()
    scope = _scope(data)
    if scope[0] != 'direct_qa' or not answer_protocol_enabled(
        events[:order[event.event_id]], attempt=scope[1], loop_index=scope[2],
    ):
        raise ValueError('Answer publication has no direct protocol')
    try:
        review = by_id[data['review_event_id']]
        accepted = by_id[review.data.to_object()['accepted_event_id']]
        feedback = by_id[data['feedback_event_id']]
        completed = by_id[data['completion_event_id']]
    except KeyError as exc:
        raise ValueError('Answer publication has missing source events') from exc
    rd, ad, fd, cd = (item.data.to_object() for item in (review, accepted, feedback, completed))
    if (any(item.run_id != event.run_id for item in (review, accepted, feedback, completed))
            or review.kind != 'critic_loop_transition' or rd.get('action') != 'review_started'
            or rd.get('target_kind') != 'direct'
            or accepted.kind != 'critic_loop_transition' or ad.get('action') != 'core_intent_accepted'
            or completed.kind != 'critic_loop_transition' or cd.get('action') != 'core_intent_accepted'
            or feedback.kind != 'feedback_submitted' or fd.get('review_event_id') != review.event_id
            or data.get('feedback_record_id') != fd.get('record_id')
            or cd.get('feedback_record_id') != fd.get('record_id')
            or _scope(ad) != scope or _scope(cd) != scope
            or cd.get('round') != ad.get('round', -1) + 1
            or not (order[accepted.event_id] < order[review.event_id] < order[feedback.event_id]
                    < order[completed.event_id] < order[event.event_id])):
        raise ValueError('Answer publication does not bind one completed review')
    for item in events[order[accepted.event_id] + 1:order[event.event_id]]:
        value = item.data.to_object()
        if (item.kind == 'critic_loop_transition' and value.get('action') == 'core_intent_accepted'
                and _scope(value) == scope and item.event_id != completed.event_id):
            raise ValueError('Answer submission was superseded')

    def call(source: dict[str, str], name: str, actor: str) -> OriginalCall:
        original = read_original_call(run_dir, source, run_id=event.run_id,
                                      tool_name=name, read_session=read_session)
        if (original.metadata.get('actor_kind') != actor
                or business_stage(original.metadata.get('stage', '')) != scope[0]
                or original.metadata.get('attempt') != scope[1]
                or not any(output.success is True for output in original.outputs)):
            raise ValueError('Answer source is not a successful call in this scope')
        return original

    submission = call(ad['source'], 'request_review', 'core')
    critique = call(fd['source'], 'submit_feedback', 'reviewer')
    closure = call(cd['source'], 'complete_feedback', 'core')
    if (closure.source['path'] != submission.source['path']
            or critique.metadata.get('stage_loop_index') != scope[2]
            or critique.metadata.get('critic_round') != cd['round']):
        raise ValueError('Answer calls belong to different review scopes')
    _verify_request_sources(events, data, accepted, completed, submission, closure,
                            run_dir=run_dir, read_session=read_session)
    content = critique.arguments.get('content')
    if (not isinstance(content, str) or content.strip() != 'No findings.'
            or sha256(content.encode('utf-8')).hexdigest() != fd.get('content_hash')):
        raise ValueError('Answer has no matching passing feedback')
    text = answer_text(submission.arguments, required=True)
    answer_id = f"answer:{scope[0]}:{scope[1]}:{scope[2]}:{submission.source['item_id']}"
    if (data.get('source') != submission.source or data.get('answer_id') != answer_id
            or data.get('content_sha256') != sha256(text.encode('utf-8')).hexdigest()
            or event.event_id != f'answer-published:{scope[0]}:{scope[1]}:{scope[2]}'):
        raise ValueError('Published answer differs from its original submission')
    return PublishedAnswer(answer_id, text, cd['round'], data['content_sha256'])


def _verify_request_sources(
    events: tuple[BusinessEvent, ...], data: dict[str, Any],
    accepted: BusinessEvent, completed: BusinessEvent,
    submission: OriginalCall, closure: OriginalCall, *,
    run_dir: Path, read_session: ResumeReadSession,
) -> None:
    from ownpi.runtime.rollout.direct_submission import source_location

    stage, attempt, loop_index = _scope(data)
    scope = f'{stage}:{attempt}:{loop_index}'
    for event, original in ((accepted, submission), (completed, closure)):
        wire = original.call.request_id or original.call.call_id
        if event.event_id != f'critic-intent:{scope}:{wire}':
            raise ValueError('Answer intent differs from original call identity')
    delivery = next((e for e in events if e.event_id == f'direct-input:direct-user:{scope}'), None)
    if delivery is None or delivery.kind != 'direct_input_delivered' or delivery.run_id != accepted.run_id:
        raise ValueError('Answer has no confirmed original user request')
    source = delivery.data.to_object()['source']
    path = submission.source['path']
    with read_session.locked_index(run_dir / path) as index:
        start = source_location(index, source, path=path).offset
        submitted = source_location(index, submission.source, path=path).offset
        closed = source_location(index, closure.source, path=path).offset
        if not start < submitted < closed:
            raise ValueError('Answer calls are outside their request interval')
        for event in events:
            value = event.data.to_object()
            if (event.kind == 'direct_input_delivered' and event != delivery
                    and value.get('input_id', '').startswith('direct-user:')
                    and value['source']['path'] == path):
                offset = source_location(index, value['source'], path=path).offset
                if start < offset <= closed:
                    raise ValueError('Answer crosses a later user request')
        read_session.check_version(index)


def publish_direct_answer(store: Any, run_dir: Path, *, attempt: int, loop_index: int) -> None:
    events = store.run_event_store.events
    if not answer_protocol_enabled(events, attempt=attempt, loop_index=loop_index):
        return
    event_id = f'answer-published:direct_qa:{attempt}:{loop_index}'
    existing = next((e for e in events if e.event_id == event_id), None)
    if existing is not None:
        read_published_answer(events, existing, run_dir=run_dir)
        return
    loop = store.state.feedback_state.critic_loop
    target, receipt = loop.review_target, loop.core_intent_receipt
    if loop.phase != 'completed' or receipt is None or receipt.intent != 'complete_feedback':
        raise ValueError('Direct answer requires a completed critic loop')
    from ownpi.runtime.rollout.direct_submission import read_review_request

    submission = read_review_request(store, run_dir)
    text = answer_text(submission.arguments, required=True)
    scope = f'direct_qa:{attempt}:{loop_index}'
    data = {
        'stage': 'direct_qa', 'attempt': attempt, 'loop_index': loop_index,
        'answer_id': f"answer:{scope}:{submission.source['item_id']}",
        'source': submission.source, 'content_sha256': sha256(text.encode('utf-8')).hexdigest(),
        'review_event_id': f'review-started:{scope}:{loop.round}',
        'feedback_event_id': f'feedback-submitted:{loop.feedback_record_id}',
        'feedback_record_id': loop.feedback_record_id,
        'completion_event_id': f'critic-intent:{scope}:{receipt.tool_call_id}',
    }
    if (target.attempt, target.loop_index) != (attempt, loop_index):
        raise ValueError('Answer target differs from the active request')
    candidate = BusinessEvent('answer_published', event_id, store.state.run_metadata.run_id,
                              FrozenProviderJson.from_value(data), now_utc_iso())
    read_published_answer((*events, candidate), candidate, run_dir=run_dir)
    store.run_event_store.emit('answer_published', event_id, data)
