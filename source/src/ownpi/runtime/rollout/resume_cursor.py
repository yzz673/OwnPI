"""Pure workflow boundaries derived from rollout facts, never resume snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ownpi.schemas.workflow_plan import RunWorkflowPlan
from ownpi.runtime.rollout.business_scope import business_stage, interaction_matches

if TYPE_CHECKING:
    from ownpi.runtime.rollout.resume import RunResumeFacts


@dataclass(frozen=True)
class ResumeCursor:
    conversation_id: str
    actor_kind: str
    actor_id: str
    stage: str
    attempt: int | None
    continuation_index: int
    workflow_plan: RunWorkflowPlan | None
    stage_attempt_counters: tuple[tuple[str, int], ...]
    query_chain_id: str
    query_chain_depth: int
    stage_loop_index: int
    pending_interactions: tuple[str, ...]
    pauses: tuple[tuple[str, str], ...]


def project_resume_cursor(facts: RunResumeFacts) -> ResumeCursor:
    """Do not turn a missing business boundary into conversation inadmissibility."""
    metadata = facts.conversation.metadata
    stage = business_stage(str(metadata.get('stage') or ''))
    attempt = metadata.get('attempt')
    if type(attempt) is not int or attempt < 1:
        attempt = None
    continuation = 0
    actor = str(metadata.get('actor_kind') or '')
    actor_id = str(metadata.get('actor_id') or '')
    pauses: list[tuple[str, str]] = []
    if not actor or not stage or attempt is None:
        pauses.append(('actor', 'conversation metadata lacks the current actor scope'))
    if facts.business_issue:
        pauses.append(('workflow', facts.business_issue))
    stages = facts.stage_projection()
    plan: RunWorkflowPlan | None = None
    if stage != 'direct_qa':
        try:
            plan = stages.workflow_plan
            if plan is None:
                pauses.append(('workflow', 'complete workflow plan fact is absent'))
            elif stage and not plan.contains(stage):
                pauses.append(('actor', 'conversation stage is outside the recorded workflow plan'))
        except ValueError as error:
            pauses.append(('workflow', f'workflow plan cannot be projected: {error}'))
    starts = [event for event in stages.events if event.kind == 'stage_attempt_started']
    counters: dict[str, int] = {}
    for event in starts:
        data = event.data.to_object()
        event_stage, event_attempt = data.get('stage'), data.get('attempt')
        if isinstance(event_stage, str) and type(event_attempt) is int and event_attempt > 0:
            counters[event_stage] = max(counters.get(event_stage, 0), event_attempt)
        else:
            pauses.append(('workflow', f'invalid stage attempt fact: {event.event_id}'))
    query_chain_id = ''
    query_chain_depth = 0
    scoped_starts = [event for event in starts if event.data.to_object().get('stage') == stage
                     and event.data.to_object().get('attempt') == attempt]
    if starts and (starts[-1].data.to_object().get('stage'), starts[-1].data.to_object().get('attempt')) != (stage, attempt):
        pauses.append(('actor', 'a later stage attempt is already committed; select its conversation'))
    if scoped_starts:
        latest = scoped_starts[-1].data.to_object()
        if isinstance(latest.get('query_chain_id'), str):
            query_chain_id = latest['query_chain_id']
        if type(latest.get('query_chain_depth')) is int and latest['query_chain_depth'] >= 0:
            query_chain_depth = latest['query_chain_depth']
    if any(event.data.to_object().get('stage') == stage and event.data.to_object().get('attempt') == attempt
           for event in stages.gates):
        pauses.append(('workflow', 'stage gate already decided; continue through the gate boundary'))
    loop_index = 1
    for event in stages.events:
        if event.kind != 'stage_loop_advanced':
            continue
        data = event.data.to_object()
        if data.get('stage') != stage or data.get('attempt') != attempt:
            continue
        next_index = data.get('loop_index')
        if type(next_index) is not int or next_index < 2:
            pauses.append(('workflow', f'invalid stage loop advance fact: {event.event_id}'))
            break
        loop_index = next_index
    pending: list[str] = []
    try:
        interactions = facts.interaction_projection()
        from ownpi.runtime.rollout.interaction_projection import answer_is_delivered, interaction_call

        for identity, value in interactions.items():
            session = value.session
            if not interaction_matches(session, metadata):
                continue
            if session.origin_kind == 'tool' and interaction_call(facts.conversation.path, session, read_session=facts.read_session) is None:
                continue
            if session.status == 'answered':
                pending_delivery = not answer_is_delivered(facts.conversation.path, session, read_session=facts.read_session)
            else:
                pending_delivery = value.closed_event_id is None
            if pending_delivery:
                continuation = session.continuation_index
                pending.append(identity)
                pauses.append(('actor', f'interaction {identity} requires its current runtime entry'))
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        pauses.append(('interactions', str(error)))
    if actor == 'subagent' and any(event.kind == 'child_completion_committed'
                                and event.data.to_object().get('child_id') == actor_id
                                for event in facts.events):
        pauses.append(('actor', 'child completion has already been committed'))
    return ResumeCursor(facts.conversation.conversation_id, actor, actor_id, stage, attempt,
                        continuation, plan, tuple(counters.items()), query_chain_id,
                        query_chain_depth, loop_index, tuple(pending), tuple(pauses))
