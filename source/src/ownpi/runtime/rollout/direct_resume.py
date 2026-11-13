"""Resolve direct requests from business facts and original input sources."""

from dataclasses import dataclass, replace

from ownpi.controller.stages import Stage
from ownpi.runtime.native_tools.history_items import HistoryMessage
from ownpi.runtime.native_tools.provider import ProviderOutputItem, ProviderOutputItemKind
from ownpi.runtime.rollout.codec import RolloutRecord, decode_entry
from ownpi.runtime.rollout.direct_inputs import direct_request_scope
from ownpi.runtime.rollout.interaction_events import project_actor_interactions
from ownpi.runtime.rollout.research_projection import project_research_feedback
from ownpi.runtime.rollout.resume_entry import StageResumeEntry
from ownpi.runtime.state_models import RevisitState


def is_direct_session(events) -> bool:
    return any(event.kind in {'direct_session_bound', 'direct_request_started'} for event in events)


def core_conversation_id(events) -> str:
    for event in reversed(events):
        if event.kind == 'direct_session_bound':
            return event.data.to_object()['conversation_id']
        if event.kind == 'direct_input_delivered':
            from pathlib import Path

            return Path(event.data.to_object()['source']['path']).stem
    raise ValueError('Direct request has no prepared Core conversation')


@dataclass(frozen=True)
class DirectResumeEntry:
    entry: StageResumeEntry
    message: str
    core_id: str
    active: bool


def resolve_direct_resume(facts) -> DirectResumeEntry:
    from ownpi.runtime.rollout.business_scope import business_stage
    from ownpi.runtime.rollout.resume import conversation_catalog, find_conversation_metadata

    core_id = core_conversation_id(facts.events)
    core = find_conversation_metadata(facts.run_root, conversation_id=core_id,
                                      read_session=facts.read_session)
    starts = [event.data.to_object() for event in facts.events if event.kind == 'direct_request_started']
    latest = starts[-1] if starts else None
    # Bootstrap may have persisted the input just before the request fact was committed.
    latest_input = None
    with facts.read_session.locked_index(core.path) as catalog:
        for location in reversed(catalog.records):
            if location.type != 'response_item' or location.variant != 'message':
                continue
            for _, raw in facts.read_session.raw_records(catalog, (location,)):
                item = decode_entry(RolloutRecord.from_dict(raw))
                if (isinstance(item.item, HistoryMessage) and item.item.role == 'user'
                        and item.exchange_id.startswith('direct-user:direct_qa:')):
                    latest_input = item
            if latest_input is not None:
                _, _, attempt_text, index_text = latest_input.exchange_id.split(':')
                candidate = {'attempt': int(attempt_text), 'loop_index': int(index_text)}
                if latest is None or candidate['loop_index'] > latest['loop_index']:
                    latest = candidate
                break
        facts.read_session.check_version(catalog)
    if latest is None:
        raise ValueError('Direct session has no durably prepared input')
    attempt, index = latest['attempt'], latest['loop_index']
    scope = direct_request_scope(stage=Stage.DIRECT_QA.value, attempt=attempt, loop_index=index)
    input_id = f'direct-user:{scope}'
    delivery = next((event.data.to_object()['source'] for event in facts.events
                     if event.event_id == f'direct-input:{input_id}'), None)
    message = None
    if delivery is not None:
        if (facts.run_root / delivery['path']).resolve() != core.path.resolve():
            raise ValueError('Direct input source does not belong to its Core conversation')
        with facts.read_session.locked_index(core.path) as catalog:
            locations = catalog.originals.get(delivery['item_id'], ())
            records = tuple(RolloutRecord.from_dict(raw)
                            for _, raw in facts.read_session.raw_records(catalog, locations))
            facts.read_session.check_version(catalog)
    else:
        records = ()
        if latest_input is not None and latest_input.exchange_id == input_id:
            message = latest_input.item.text
    for record in records:
        entry = decode_entry(record)
        if ((delivery is not None and record.metadata.to_object()['item_id'] == delivery['item_id'])
                or (delivery is None and entry.exchange_id == input_id)):
            if isinstance(entry.item, HistoryMessage) and entry.item.role == 'user':
                message = entry.item.text
                break
    if message is None:
        raise ValueError('Direct request input was not durably prepared; no accepted input to resume')
    feedback = project_research_feedback(facts, stage=Stage.DIRECT_QA.value,
                                         attempt=attempt, loop_index=index)
    active = not any(event.event_id == f'direct-complete:{scope}' for event in facts.events)
    selected = core
    if active and feedback.critic_loop.phase == 'critic_pending':
        candidates = [item for item in conversation_catalog(facts.run_root, read_session=facts.read_session)
                      if item.metadata.get('actor_kind') == 'reviewer'
                      and business_stage(item.metadata.get('stage', '')) == Stage.DIRECT_QA.value
                      and item.metadata.get('attempt') == attempt
                      and item.metadata.get('stage_loop_index') == index
                      and item.metadata.get('critic_round') == feedback.critic_loop.round]
        if len(candidates) > 1:
            raise ValueError('Ambiguous direct Reviewer recovery scope')
        if candidates:
            selected = candidates[0]
    facts = replace(facts, conversation=selected)
    interactions = project_actor_interactions(facts.events, selected.metadata)
    pending = [value.session for value in interactions.values() if not value.closed_event_id]
    interaction = pending[-1] if pending else None
    entry = StageResumeEntry(
        facts=facts, stage=Stage.DIRECT_QA, attempt=attempt, attempt_started=True,
        counters={Stage.DIRECT_QA.value: attempt}, query_chain_id='', query_chain_depth=0,
        loop_index=index, continuation_index=(interaction.continuation_index if interaction else 0),
        action='reviewer' if selected.metadata['actor_kind'] == 'reviewer' else 'core',
        feedback=feedback, revisit=RevisitState(), interaction=interaction,
    )
    return DirectResumeEntry(entry, message, core_id, active)


def latest_direct_answer(store, run_dir, *, loop_index: int) -> str:
    """Read the published answer, or bounded legacy text for an old request."""
    from ownpi.runtime.rollout.resume_read_session import ResumeReadSession
    from ownpi.runtime.rollout.answer_publication import answer_protocol_enabled, read_published_answer

    events = store.run_event_store.events
    attempt = store.state.workflow_state.current_stage_attempt
    if answer_protocol_enabled(events, attempt=attempt, loop_index=loop_index):
        publication = next((e for e in events if e.event_id ==
                            f'answer-published:direct_qa:{attempt}:{loop_index}'), None)
        return read_published_answer(events, publication, run_dir=run_dir).text if publication else ''

    scope = direct_request_scope(stage=Stage.DIRECT_QA.value,
                                attempt=store.state.workflow_state.current_stage_attempt,
                                loop_index=loop_index)
    delivery = next((event.data.to_object()['source'] for event in store.run_event_store.events
                     if event.event_id == f'direct-input:direct-user:{scope}'), None)
    if delivery is None:
        return ''
    answer = ''
    with ResumeReadSession() as reader:
        with reader.locked_index(run_dir / delivery['path']) as index:
            start = index.originals[delivery['item_id']][0].offset
            stops = [index.originals[data['source']['item_id']][0].offset
                     for event in events if event.kind == 'direct_input_delivered'
                     for data in (event.data.to_object(),)
                     if data.get('input_id', '').startswith('direct-user:')
                     and data['source']['path'] == delivery['path']
                     and index.originals[data['source']['item_id']][0].offset > start]
            stop = min(stops) if stops else float('inf')
            locations = [location for location in index.records if location.offset > start
                         and location.offset < stop
                         and location.type == 'response_item' and location.variant == 'provider'
                         and location.kind == 'message']
            for _, raw in reader.raw_records(index, locations):
                item = decode_entry(RolloutRecord.from_dict(raw)).item
                if isinstance(item, ProviderOutputItem) and item.kind is ProviderOutputItemKind.MESSAGE:
                    if item.text:
                        answer = item.text
            reader.check_version(index)
    return answer
