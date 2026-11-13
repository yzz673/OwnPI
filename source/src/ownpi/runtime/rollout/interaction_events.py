"""Narrow clarification facts and projections, independent of workflow persistence."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Protocol

from ownpi.runtime.rollout.business_events import BusinessEvent
from ownpi.runtime.rollout.business_scope import business_stage
from ownpi.schemas.runtime_human_clarification import (
    HumanClarificationQuestion,
    HumanClarificationSession,
    human_answer_has_value,
    normalize_human_answer_payload,
)


class InteractionEventStore(Protocol):
    @property
    def events(self) -> tuple[BusinessEvent, ...]: ...

    def emit(self, kind: str, event_id: str, data: dict[str, Any]) -> BusinessEvent: ...


@dataclass(frozen=True)
class InteractionProjection:
    request_event_id: str
    session: HumanClarificationSession
    answer_event_id: str | None = None
    closed_event_id: str | None = None


def validate_request(session: HumanClarificationSession) -> None:
    if not session.session_id.strip() or not session.stage.strip():
        raise ValueError('Clarification requires session and stage identities')
    if session.origin_kind not in {'controller', 'tool'}:
        raise ValueError('Only durable clarification requests are supported')
    if session.origin_kind == 'tool' and (
        session.tool_name != 'human_question' or not session.tool_request_id
    ):
        raise ValueError('Tool clarification requires its human_question call identity')
    if session.status != 'pending' or session.answers or session.answered_at or session.resumed_at:
        raise ValueError('A new clarification request must be pending and unanswered')
    for value in (session.attempt, session.query_chain_depth, session.continuation_index, session.critic_round):
        if type(value) is not int or value < 0:
            raise ValueError('Clarification scope counters must be nonnegative integers')
    # Producers own authoring policy. Persistence only validates readable shape
    # and unambiguous answer keys, including controller-generated fallback IDs.
    if not session.questions:
        raise ValueError('Clarification requires questions')
    keys: set[str] = set()
    for question in session.questions:
        parsed = HumanClarificationQuestion.from_dict(question.to_dict())
        if parsed.question_id in keys:
            raise ValueError('Clarification has duplicate question IDs')
        keys.add(parsed.question_id)


def validate_answers(
    session: HumanClarificationSession, answers: dict[str, Any],
) -> dict[str, Any]:
    """Validate wire types before using the existing pure domain normalizer."""
    if not isinstance(answers, dict):
        raise ValueError('Answers must be a canonical question_id mapping')
    questions = {question.question_id: question for question in session.questions}
    if answers.keys() - questions.keys():
        raise ValueError('Answers contain unknown question IDs')
    validated: dict[str, Any] = {}
    for question_id, raw in answers.items():
        if not isinstance(raw, dict) or raw.keys() - {'selected_labels', 'free_text'}:
            raise ValueError('Answers must use canonical payloads')
        labels = raw.get('selected_labels', [])
        free_text = raw.get('free_text')
        if not isinstance(labels, list) or any(not isinstance(label, str) for label in labels):
            raise ValueError('selected_labels must be an array of strings')
        if free_text is not None and not isinstance(free_text, str):
            raise ValueError('free_text must be a string or null')
        payload = normalize_human_answer_payload(raw)
        question = questions[question_id]
        selected = payload['selected_labels']
        if any(label not in question.option_labels for label in selected):
            raise ValueError(f'Answer must use declared option labels: {question_id}')
        if not question.multi_select and len(selected) > 1:
            raise ValueError(f'Answer must select at most one option: {question_id}')
        validated[question_id] = payload
    missing = [question.question_id for question in session.questions
               if question.required and not human_answer_has_value(validated.get(question.question_id, {}))]
    if missing:
        raise ValueError('Required human clarification answers are missing: ' + ', '.join(missing))
    return validated


def project_interactions(events: Iterable[BusinessEvent]) -> dict[str, InteractionProjection]:
    """Fold facts in file order; never infer delivery from acceptance or closure."""
    projected: dict[str, InteractionProjection] = {}
    seen: dict[str, tuple[str, dict[str, Any]]] = {}
    for event in events:
        if event.kind not in {'interaction_requested', 'interaction_answer_accepted', 'interaction_closed'}:
            continue
        data = event.data.to_object()
        identity = (event.kind, data)
        if event.event_id in seen:
            if seen[event.event_id] != identity:
                raise ValueError(f'Conflicting interaction event: {event.event_id}')
            continue
        seen[event.event_id] = identity
        session_id = data.get('session_id')
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError('Missing interaction session identity')
        if event.kind == 'interaction_requested':
            session = HumanClarificationSession.from_dict(data['request'])
            validate_request(session)
            if session.session_id != session_id or data.get('request_id') != session.interaction_request_id:
                raise ValueError('Mismatched interaction request identity')
            if session_id in projected:
                raise ValueError(f'Conflicting interaction request: {session_id}')
            projected[session_id] = InteractionProjection(event.event_id, session)
            continue
        current = projected.get(session_id)
        if current is None or data.get('request_event_id') != current.request_event_id:
            raise ValueError('Interaction fact refers to an unknown request')
        if data.get('request_id') != current.session.interaction_request_id:
            raise ValueError('Mismatched interaction request identity')
        if current.closed_event_id is not None:
            raise ValueError('Interaction is already closed')
        if event.kind == 'interaction_answer_accepted':
            answers = validate_answers(current.session, data['answers'])
            if current.answer_event_id is not None:
                raise ValueError('Interaction already has an accepted answer')
            timestamp = event.timestamp
            if not isinstance(timestamp, str) or not timestamp.strip():
                raise ValueError('Missing answer acceptance timestamp')
            projected[session_id] = replace(
                current, answer_event_id=event.event_id,
                session=replace(current.session, status='answered', answers=answers, answered_at=timestamp),
            )
        else:
            if not isinstance(data.get('reason'), str) or not data['reason'].strip():
                raise ValueError('Interaction closure requires a reason')
            projected[session_id] = replace(current, closed_event_id=event.event_id)
    return projected


def project_actor_interactions(
    events: Iterable[BusinessEvent], metadata: dict[str, Any],
) -> dict[str, InteractionProjection]:
    """Project this actor's sessions; require proof before excluding another session."""
    relevant = tuple(event for event in events if event.kind in {
        'interaction_requested', 'interaction_answer_accepted', 'interaction_closed',
    })
    scopes: dict[str, bool] = {}
    for event in relevant:
        data = event.data.to_object()
        identity = data.get('session_id')
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError('Missing interaction session identity')
        if event.kind != 'interaction_requested':
            continue
        request = data.get('request')
        if not isinstance(request, dict) or request.get('session_id') != identity:
            raise ValueError(f'Interaction request scope is unresolved: {identity}')
        lane = str(metadata.get('stage') or '')
        unrelated = any((
            isinstance(request.get('stage'), str) and request['stage'] != business_stage(lane),
            type(request.get('attempt')) is int and request['attempt'] != metadata.get('attempt'),
            isinstance(request.get('actor_kind'), str)
            and request['actor_kind'] != metadata.get('actor_kind'),
            isinstance(request.get('actor_id'), str) and bool(request['actor_id'])
            and request['actor_id'] != metadata.get('actor_id'),
            isinstance(request.get('conversation_lane'), str) and bool(request['conversation_lane'])
            and request['conversation_lane'] != lane,
        ))
        matches = not unrelated
        if identity in scopes and scopes[identity] != matches:
            raise ValueError(f'Conflicting interaction scope: {identity}')
        scopes[identity] = matches
    for event in relevant:
        identity = event.data.to_object()['session_id']
        if identity not in scopes:
            raise ValueError(f'Interaction fact refers to an unknown request: {identity}')
    return project_interactions(event for event in relevant
                                if scopes[event.data.to_object()['session_id']])


def publish_request(store: InteractionEventStore, session: HumanClarificationSession) -> BusinessEvent:
    validate_request(session)
    request = session.to_dict()
    event_id = f'interaction_requested:{session.session_id}'
    existing = project_interactions(store.events).get(session.session_id)
    if existing is not None:
        original = next(event for event in store.events if event.event_id == existing.request_event_id)
        if original.data.to_object()['request'] != request:
            raise ValueError(f'Conflicting interaction request: {session.session_id}')
        if existing.closed_event_id or existing.answer_event_id:
            raise ValueError('Cannot republish a completed clarification')
        # Re-emit the same fact to retry a possibly failed durable barrier.
        return store.emit(original.kind, original.event_id, original.data.to_object())
    return store.emit('interaction_requested', event_id, {
        'session_id': session.session_id,
        'request_id': request['interaction_request_id'],
        'request': request,
    })


def accept_answer(
    store: InteractionEventStore, *, session_id: str, answers: dict[str, Any], answered_at: str,
) -> HumanClarificationSession:
    current = project_interactions(store.events).get(session_id)
    if current is None:
        raise ValueError(f'Unknown clarification session: {session_id}')
    if current.closed_event_id is not None:
        raise ValueError('Interaction is already closed')
    normalized = validate_answers(current.session, answers)
    if current.answer_event_id is not None:
        if current.session.answers != normalized:
            raise ValueError(f'Conflicting clarification answer: {session_id}')
        event = next(item for item in store.events if item.event_id == current.answer_event_id)
        store.emit(event.kind, event.event_id, event.data.to_object())
        return current.session
    if not isinstance(answered_at, str) or not answered_at.strip():
        raise ValueError('Missing answer acceptance timestamp')
    event = store.emit('interaction_answer_accepted', f'interaction_answer_accepted:{session_id}', {
        'session_id': session_id,
        'request_id': current.session.interaction_request_id,
        'request_event_id': current.request_event_id,
        'answers': normalized,
    })
    return replace(current.session, status='answered', answers=normalized,
                   answered_at=event.timestamp)


def close_interaction(
    store: InteractionEventStore, *, session_id: str, reason: str,
) -> BusinessEvent:
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('Interaction closure requires a reason')
    current = project_interactions(store.events).get(session_id)
    if current is None:
        raise ValueError(f'Unknown clarification session: {session_id}')
    return store.emit('interaction_closed', f'interaction_closed:{session_id}', {
        'session_id': session_id,
        'request_id': current.session.interaction_request_id,
        'request_event_id': current.request_event_id,
        'reason': reason,
    })
