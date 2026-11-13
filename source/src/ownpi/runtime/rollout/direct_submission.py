"""Read one Core submission from its original conversation, never compacted history."""

from pathlib import Path
from typing import Any

from ownpi.runtime.native_tools.provider import ProviderOutputItem, ProviderOutputItemKind
from ownpi.runtime.rollout.codec import RolloutRecord, decode_entry
from ownpi.runtime.rollout.resume_read_session import ConversationIndex, RecordLocation, ResumeReadSession
from ownpi.runtime.rollout.source_reader import OriginalCall, read_original_call
from ownpi.runtime.rollout.direct_inputs import direct_request_scope
from ownpi.runtime.rollout.call_source import validate_call_source
from ownpi.schemas.feedback import DirectReviewTarget


def direct_request_source(state_store: Any) -> dict[str, str]:
    target = state_store.state.feedback_state.critic_loop.review_target
    if not isinstance(target, DirectReviewTarget):
        raise ValueError('Direct submission requires a direct review target')
    scope = direct_request_scope(stage=target.stage, attempt=target.attempt,
                                 loop_index=target.loop_index)
    matches = [event for event in state_store.run_event_store.events
               if event.kind == 'direct_input_delivered'
               and event.event_id == f'direct-input:direct-user:{scope}'
               and event.data.to_object().get('input_id') == f'direct-user:{scope}']
    if len(matches) != 1:
        raise ValueError('Direct request requires one confirmed original user input')
    return validate_call_source(matches[0].data.to_object()['source'])


def source_location(index: ConversationIndex, source: dict[str, str], *, path: str) -> RecordLocation:
    source = validate_call_source(source)
    if source['path'] != path:
        raise ValueError('Direct source belongs to a different Core conversation')
    matches = index.originals.get(source['item_id'], ())
    if len(matches) != 1:
        raise ValueError('Direct source must identify one original item')
    return matches[0]


def read_direct_submission(
    state_store: Any, run_dir: Path, *, read_session: ResumeReadSession | None = None,
    review_request: OriginalCall | None = None,
) -> tuple[str, str]:
    if read_session is None:
        with ResumeReadSession() as session:
            return read_direct_submission(state_store, run_dir, read_session=session,
                                          review_request=review_request)
    session = read_session
    target = state_store.state.feedback_state.critic_loop.review_target
    start_source = direct_request_source(state_store)
    call = review_request or read_review_request(state_store, run_dir, read_session=session)
    from ownpi.runtime.rollout.answer_publication import answer_protocol_enabled, answer_text

    with session.locked_index(run_dir / call.source['path']) as index:
        source = call.source
        start = source_location(index, start_source, path=source['path']).offset
        stop = source_location(index, source, path=source['path']).offset
        if start >= stop:
            raise ValueError('Review request precedes its user input')
        if answer_protocol_enabled(state_store.run_event_store.events, attempt=target.attempt,
                                   loop_index=target.loop_index):
            text = answer_text(call.arguments, required=True)
            session.check_version(index)
            return text, f"direct-answer:{source['path']}:{source['item_id']}"
        prefix = f'critic-intent:{target.stage}:{target.attempt}:{target.loop_index}:'
        for event in state_store.run_event_store.events:
            data = event.data.to_object()
            if (event.kind != 'critic_loop_transition' or not event.event_id.startswith(prefix)
                    or data.get('action') != 'core_intent_accepted'
                    or data.get('stage') != target.stage or data.get('attempt') != target.attempt):
                continue
            candidate = data.get('source')
            if candidate is None:
                continue
            offset = source_location(index, candidate, path=source['path']).offset
            if start < offset < stop:
                previous = read_original_call(run_dir, candidate, run_id=target.run_id,
                                              tool_name='request_review', read_session=session)
                if any(output.success is True for output in previous.outputs):
                    start = offset
        texts, exchange = [], None
        locations = [location for location in index.records
                     if start < location.offset <= stop and location.type == 'response_item'
                     and location.variant == 'provider' and location.kind == 'message']
        for location, raw in session.raw_records(index, locations):
            entry = decode_entry(RolloutRecord.from_dict(raw))
            item = entry.item
            if isinstance(item, ProviderOutputItem) and item.kind is ProviderOutputItemKind.MESSAGE:
                if item.text.strip():
                    identity = entry.exchange_id or location.item_id
                    if identity != exchange:
                        texts, exchange = [], identity
                    texts.append(item.text)
        session.check_version(index)
    # Workspace-only submissions remain valid, without inventing an answer file.
    text = '\n\n'.join(texts) or 'Core submitted the current workspace for review (no assistant text).'
    return text, f"direct-submission:{source['path']}:{source['item_id']}"


def read_review_request(
    state_store: Any, run_dir: Path, *, read_session: ResumeReadSession | None = None,
) -> OriginalCall:
    target = state_store.state.feedback_state.critic_loop.review_target
    source = None
    accepted_id = f'critic-intent:{target.stage}:{target.attempt}:{target.loop_index}:{target.request_id}'
    for event in state_store.run_event_store.events:
        data = event.data.to_object()
        if (event.event_id == accepted_id and event.kind == 'critic_loop_transition'
                and data.get('action') == 'core_intent_accepted'
                and data.get('stage') == target.stage
                and data.get('attempt') == target.attempt):
            candidate = data.get('source')
            if candidate is not None:
                source = candidate
                break
    if source is None:
        raise ValueError('Direct submission has no committed original call')
    call = read_original_call(run_dir, source, run_id=target.run_id,
                              tool_name='request_review', read_session=read_session)
    if (call.metadata.get('actor_kind') != 'core'
            or call.metadata.get('stage') != target.stage
            or call.metadata.get('attempt') != target.attempt):
        raise ValueError('Review request belongs to a different Core scope')
    if ((call.call.request_id or call.call.call_id) != target.request_id
            or not any(item.success is True for item in call.outputs)):
        raise ValueError('Review request does not match successful request_review')
    return call
