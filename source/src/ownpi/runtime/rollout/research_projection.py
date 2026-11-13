"""Fold committed research actions into existing in-memory domain state."""

from __future__ import annotations

from dataclasses import replace
import json
from typing import TYPE_CHECKING

from ownpi.controller.stages import Stage, GateDecision, is_completed_gate_decision
from ownpi.runtime.call_continuation.critic_loop import (
    begin_critic_round, begin_core_feedback, record_critic_feedback,
    record_core_feedback_intent, record_analysis_feedback,
    record_next_stage_receipt,
)
from ownpi.runtime.call_continuation.critic_policy import reviewer_policy
from ownpi.runtime.state_models import FeedbackState, RevisitState
from ownpi.runtime.rollout.feedback_projection import project_feedback_record, review_target
from ownpi.runtime.rollout.source_reader import read_original_call
from ownpi.runtime.rollout.codec import decode_entry
from ownpi.runtime.native_tools.provider import ProviderOutputItem, ProviderOutputItemKind
from ownpi.schemas.feedback import CoreFeedbackIntentReceipt, FeedbackRecordRef, NextStageReceipt

if TYPE_CHECKING:
    from ownpi.runtime.rollout.resume import RunResumeFacts


def project_research_feedback(facts: RunResumeFacts, *, stage: str, attempt: int,
                              loop_index: int) -> FeedbackState:
    state = FeedbackState()
    reviews: set[str] = set()
    scoped_intents: set[str] = set()
    for event in facts.events:
        data = event.data.to_object()
        if event.kind == 'critic_loop_transition':
            action = data['action']
            if action == 'core_intent_accepted':
                if (data['stage'], data['attempt'], data['stage_loop_index']) != (stage, attempt, loop_index):
                    continue
                call = read_original_call(facts.run_root, data['source'], run_id=facts.run_id, read_session=facts.read_session)
                receipt = CoreFeedbackIntentReceipt(call.call.tool_name, stage, attempt, 'reviewer',
                    data['round'], data.get('feedback_record_id', ''),
                    call.call.request_id or call.call.call_id, event.timestamp)
                record_core_feedback_intent(state, receipt=receipt)
                scoped_intents.add(event.event_id)
            elif action == 'review_started':
                if data['accepted_event_id'] not in scoped_intents:
                    continue
                target, _ = review_target(facts.events, event.event_id,
                                                run_root=facts.run_root, run_id=facts.run_id, read_session=facts.read_session)
                if (target.stage, target.attempt, target.loop_index) != (stage, attempt, loop_index):
                    continue
                begin_critic_round(state, policy=reviewer_policy(), stage=stage,
                                   attempt=attempt, review_target=target)
                reviews.add(event.event_id)
            elif action == 'core_feedback_started' and state.record_ref(data['feedback_record_id']):
                begin_core_feedback(state)
        elif event.kind == 'feedback_submitted':
            if 'review_event_id' in data:
                if data['review_event_id'] not in reviews:
                    continue
            elif (data.get('stage'), data.get('attempt'), data.get('stage_loop_index')) != (stage, attempt, loop_index):
                continue
            record = project_feedback_record(facts.events, run_root=facts.run_root,
                                             run_id=facts.run_id, record_id=data['record_id'], read_session=facts.read_session)
            if record.author_role == 'analysis':
                record_analysis_feedback(state, record=record, record_ref=FeedbackRecordRef(record))
            else:
                record_critic_feedback(state, record=record, record_ref=FeedbackRecordRef(record))
        elif event.kind == 'feedback_delivered':
            if (data.get('record_id') == state.active_handoff.record_id
                    and data.get('target_stage') == stage and data.get('target_attempt') == attempt):
                state.active_handoff = replace(state.active_handoff, status='consumed')
    if stage == 'analysis_decision' and state.analysis_transition.feedback_record_id:
        records = facts.read_session.original_records(facts.conversation.path, tool_names=frozenset({'next_stage'}), variants=frozenset({'provider'}))
        for record in records:
            item = decode_entry(record).item
            if (not isinstance(item, ProviderOutputItem) or item.kind is not ProviderOutputItemKind.TOOL_CALL
                    or item.tool_name != 'next_stage'):
                continue
            source = {'path': str(facts.conversation.path.relative_to(facts.run_root)),
                      'item_id': record.metadata.to_object()['item_id']}
            call = read_original_call(facts.run_root, source, run_id=facts.run_id, tool_name='next_stage', read_session=facts.read_session)
            feedback = state.record_ref(state.analysis_transition.feedback_record_id).record
            target = call.arguments.get('target_stage')
            matched = False
            for output in call.outputs:
                if output.success is not True or not isinstance(output.body, str):
                    continue
                try:
                    payload = json.loads(output.body)
                except ValueError:
                    continue
                result = payload.get('data', {}) if isinstance(payload, dict) else {}
                if isinstance(result, dict) and result.get('feedback_record_id') == feedback.record_id and result.get('target_stage') == target:
                    matched = True
            if record.timestamp < feedback.created_at or not matched:
                continue
            if not isinstance(target, str):
                raise ValueError('Successful next_stage lacks its target')
            record_next_stage_receipt(state, receipt=NextStageReceipt(stage, attempt,
                state.analysis_transition.feedback_record_id, target,
                item.request_id or item.call_id, record.timestamp))
    return state


def project_revisit(facts: RunResumeFacts) -> RevisitState:
    state = RevisitState()
    for event in facts.events:
        data = event.data.to_object()
        if event.kind == 'revisit_transition':
            for name in ('target_stage', 'target_attempt', 'source_stage', 'source_attempt',
                         'source_stage_run_id', 'decision', 'reason', 'revisit_contract'):
                setattr(state, f'pending_{name}', data.get(name))
            state.latest_revisit_event = dict(data)
            state.history.append(dict(data))
            state.total_revisits += 1
            target = data['target_stage']
            state.stage_revisit_counters[target] = state.stage_revisit_counters.get(target, 0) + 1
        elif event.kind == 'stage_gate_decided' and state.pending_target_stage:
            if (data['stage'] != state.pending_target_stage
                    or data['attempt'] < (state.pending_target_attempt or 0)):
                continue
            decision = GateDecision(data['gate']['decision'])
            if is_completed_gate_decision(decision) or decision == GateDecision.FAIL:
                state.current_context_for(stage=Stage(data['stage']), attempt=data['attempt'])
                for name in ('target_stage', 'target_attempt', 'source_stage', 'source_attempt',
                             'source_stage_run_id', 'decision', 'reason', 'revisit_contract'):
                    setattr(state, f'pending_{name}', None)
        del state.history[:-32]
    return state
