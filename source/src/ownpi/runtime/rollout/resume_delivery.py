"""Deliver committed business results to their original calls, without replay."""

from __future__ import annotations

from dataclasses import replace
import json
import logging
from typing import Any

from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.native_tools.executor import NativeToolExecutionOutcome
from ownpi.runtime.native_tools.history_recording import record_tool_delivery
from ownpi.runtime.native_tools.tool_invocation import NativeToolInvocation
from ownpi.runtime.rollout.business_scope import interaction_matches
from ownpi.runtime.rollout.feedback_projection import project_feedback_record
from ownpi.runtime.rollout.interaction_events import project_actor_interactions
from ownpi.runtime.rollout.source_reader import read_original_call
from ownpi.runtime.tools.runtime import ToolInvocationOutcome
from ownpi.schemas.runtime_tooling import ToolCall, ToolInvocationRecord
from ownpi.runtime.rollout.resume_read_session import ResumeReadSession


def _reader(context: Any) -> ResumeReadSession:
    return getattr(context, 'resume_read_session', None) or ResumeReadSession()


def _live_deliveries(history: Any) -> set[tuple[str, str]]:
    if not hasattr(history, '_resumed_deliveries'):
        history._resumed_deliveries = set()
    return history._resumed_deliveries


def deliver_interaction_results(history: Any, *, context: Any, tool_runtime: Any) -> int:
    store = getattr(context.state_store, 'run_event_store', None)
    if store is None:
        return 0
    path = history.recorder.path
    reader = _reader(context)
    metadata = reader.index(path).descriptor.metadata
    root = store.path.parent.parent
    sources = reader.original_call_sources(root, path, tool_names=frozenset({'human_question'}))
    interactions = project_actor_interactions(store.events, metadata)
    delivered = _deliver_controller_answers(history, metadata, interactions, reader)
    for source in sources:
        call = read_original_call(root, source, actor_id=metadata['actor_id'], read_session=reader)
        delivered += _deliver_question_answer(history, context, tool_runtime, metadata, interactions, call, reader)
    return delivered


def _deliver_controller_answers(history: Any, metadata: Any, interactions: Any, reader: ResumeReadSession) -> int:
    from ownpi.runtime.rollout.interaction_projection import answer_is_delivered
    from ownpi.runtime.native_tools.history_items import HistoryEntry, HistoryMessage
    from ownpi.runtime.native_tools.provider_json import FrozenProviderJson
    from ownpi.schemas.runtime_human_clarification import build_controller_clarification_handoff

    delivered = 0
    path = history.recorder.path
    for interaction in interactions.values():
        answered = interaction.session
        if (answered.origin_kind != 'controller' or answered.status != 'answered'
                or not interaction_matches(answered, metadata) or answer_is_delivered(path, answered, read_session=reader)):
            continue
        identity = ('controller', answered.session_id)
        if identity in _live_deliveries(history):
            continue
        handoff = build_controller_clarification_handoff(answered)
        entries = history.record_items((HistoryEntry(HistoryMessage('user',
            json.dumps(handoff, ensure_ascii=False), FrozenProviderJson.from_value({
                'controller_clarification_session': answered.session_id,
            }))),))
        if not entries:
            raise RuntimeError('Controller clarification was not delivered to live history')
        _live_deliveries(history).add(identity)
        history.flush()
        reader.invalidate(path)
        delivered += 1
    return delivered


def _deliver_question_answer(history: Any, context: Any, tool_runtime: Any,
                             metadata: Any, interactions: Any, call: Any, reader: ResumeReadSession) -> int:
    from ownpi.runtime.rollout.interaction_projection import interaction_call

    if not call.call.raw_arguments:
        return 0
    path = history.recorder.path
    if (any(output.success is True for output in call.outputs)
            or _delivery_identity(call) in _live_deliveries(history)):
        return 0
    candidates = [value.session for value in interactions.values()
                  if interaction_matches(value.session, metadata)
                  and value.session.tool_request_id in {call.call.call_id, call.call.request_id}
                  and value.session.status == 'answered'
                  and (original := interaction_call(path, value.session, read_session=reader)) is not None
                  and original.source['item_id'] == call.source['item_id']]
    if len(candidates) != 1:
        return 0
    answered = candidates[0]
    invocation = tool_runtime.complete_human_question_interaction(
        context=context, session=replace(answered, status='pending', answers={}, answered_at=None),
        answered_session=answered,
    )
    _deliver(history, context, call, invocation)
    reader.invalidate(path)
    return 1


def deliver_committed_results(session: Any, *, context: Any) -> int:
    if getattr(session, '_business_results_recovered', False):
        return 0
    store = getattr(context.state_store, 'run_event_store', None)
    if store is None:
        return 0
    root = store.path.parent.parent
    history = session.shared.history
    path = history.recorder.path
    reader = _reader(context)
    metadata = reader.index(path).descriptor.metadata
    # Read the selected calls before delivery appends to this same rollout.
    sources = reader.original_call_sources(root, path, tool_names=frozenset({'run_subagents', 'human_question'}))
    interactions = project_actor_interactions(store.events, metadata)
    delivered = _deliver_controller_answers(history, metadata, interactions, reader)
    for source in sources:
        call = read_original_call(root, source, actor_id=metadata['actor_id'], read_session=reader)
        if call.call.tool_name != 'run_subagents' or not call.call.raw_arguments:
            if call.call.tool_name == 'human_question':
                delivered += _deliver_question_answer(
                    history, context, session.executor._tool_runtime, metadata, interactions, call, reader,
                )
            continue
        if not call.delivered and _delivery_identity(call) not in _live_deliveries(history):
            from ownpi.subagent.resume_batch import resume_parent_batch

            result = resume_parent_batch(context, call)
            _deliver(history, context, call, committed_invocation(call, result))
            reader.invalidate(path)
            delivered += 1
    for event in store.events:
        data = event.data.to_object()
        if event.kind == 'feedback_submitted':
            source = data['source']
        elif event.kind == 'critic_loop_transition' and data.get('action') == 'core_intent_accepted':
            source = data['source']
        else:
            continue
        if (root / source['path']).resolve() != path.resolve():
            continue
        call = read_original_call(root, source, actor_id=metadata['actor_id'], read_session=reader)
        if call.delivered or _delivery_identity(call) in _live_deliveries(history):
            continue
        if event.kind == 'feedback_submitted':
            feedback = project_feedback_record(store.events, run_root=root, run_id=store.run_id,
                                               record_id=data['record_id'], read_session=reader)
            result = ToolResult(ok=True, tool_name='submit_feedback', data={
                'author_role': feedback.author_role, 'record_id': feedback.record_id,
                'feedback_path': feedback.feedback_path, 'content_hash': feedback.content_hash,
                'review_target': feedback.review_target, 'idempotent': True,
            }, stdout='Feedback persisted')
        else:
            result = ToolResult(ok=True, tool_name=call.call.tool_name, data={
                'intent': call.call.tool_name, 'feedback_record_id': data.get('feedback_record_id', ''),
                'critic_role': 'reviewer', 'round': data['round'],
            }, stdout='Core feedback intent recorded')
        _deliver(history, context, call, committed_invocation(call, result))
        reader.invalidate(path)
        delivered += 1
    session._business_results_recovered = True
    if metadata.get('actor_kind') in {'core', 'reviewer'}:
        reader.clear()
    return delivered


def committed_invocation(call: Any, result: ToolResult) -> ToolInvocationOutcome:
    identity = call.call.request_id or call.call.call_id
    return ToolInvocationOutcome(
        ToolCall(identity, call.call.tool_name, call.arguments),
        ToolInvocationRecord(identity, call.call.tool_name, 'completed', True), result,
    )


def deliver_completed_review(state_store: Any, *, record_id: str, run_root: Any,
                             read_session: ResumeReadSession | None = None) -> None:
    """Finish a committed review's output without running the reviewer again."""
    from ownpi.runtime.limits.tool_output import DEFAULT_TOOL_RESULT_TRUNCATION_POLICY
    from ownpi.runtime.rollout.resume import replay_conversation
    from ownpi.runtime.rollout.session import RolloutHistory

    store = state_store.run_event_store
    if store is None:
        raise ValueError('Feedback delivery requires current run facts')
    event = next(event for event in store.events if event.kind == 'feedback_submitted'
                 and event.data.to_object()['record_id'] == record_id)
    reader = read_session or ResumeReadSession()
    call = read_original_call(run_root, event.data.to_object()['source'], tool_name='submit_feedback', read_session=reader)
    if call.delivered:
        return
    record = project_feedback_record(store.events, run_root=run_root, run_id=store.run_id, record_id=record_id, read_session=reader)
    result = ToolResult(ok=True, tool_name='submit_feedback', data={
        'author_role': record.author_role, 'record_id': record.record_id,
        'feedback_path': record.feedback_path, 'content_hash': record.content_hash,
        'review_target': record.review_target, 'idempotent': True,
    }, stdout='Feedback persisted')
    history = RolloutHistory.resume(replay_conversation(run_root / call.source['path'], read_session=reader),
                                    policy=DEFAULT_TOOL_RESULT_TRUNCATION_POLICY)
    try:
        _deliver(history, None, call,
                 committed_invocation(call, result))
        reader.invalidate(history.recorder.path)
    finally:
        try:
            if not history.close():
                logging.getLogger(__name__).warning('Feedback history close unconfirmed; writer retained')
        except OSError:
            logging.getLogger(__name__).warning('Feedback history close unconfirmed', exc_info=True)


def _delivery_identity(original: Any) -> tuple[str, str]:
    return (original.source['path'], original.source['item_id'])


def _deliver(history: Any, context: Any, original: Any, invocation: ToolInvocationOutcome) -> None:
    identity = _delivery_identity(original)
    if identity in _live_deliveries(history):
        return
    item = original.call
    call = NativeToolInvocation(item.tool_name, item.raw_arguments, item.call_id,
                                item.request_id, item.sequence_index, item.provider_payload)
    entry = record_tool_delivery(history, NativeToolExecutionOutcome(call, invocation),
                                 call=call, exchange_id=original.entry.exchange_id,
                                 context=context)
    if entry is None:
        raise RuntimeError('Resumed business result was not delivered to live history')
    _live_deliveries(history).add(identity)
    history.flush()
