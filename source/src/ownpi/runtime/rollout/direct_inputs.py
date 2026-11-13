"""Direct request boundaries and source-backed delivery into the live Core history."""

from typing import TYPE_CHECKING, Any

from ownpi.runtime.native_tools.history_items import HistoryEntry, HistoryMessage
from ownpi.runtime.native_tools.provider import ProviderOutputItem
from ownpi.runtime.stage_execution.feedback_handoff import render_feedback_envelope

if TYPE_CHECKING:
    from ownpi.runtime.native_tools.core_loop_session import CoreLoopSession


def direct_request_scope(*, stage: str, attempt: int, loop_index: int) -> str:
    return f'{stage}:{attempt}:{loop_index}'


def record_direct_request(store: Any, *, stage: str, attempt: int, loop_index: int) -> str:
    scope = direct_request_scope(stage=stage, attempt=attempt, loop_index=loop_index)
    input_id = f'direct-user:{scope}'
    store.run_event_store.emit('direct_request_started', f'direct-request:{scope}', {
        'stage': stage, 'attempt': attempt, 'loop_index': loop_index, 'input_id': input_id,
    })
    return input_id


def prepare_direct_inputs(session: 'CoreLoopSession', *, current_request: str) -> None:
    context = session.context
    store = context.state_store
    index = int(store.state.workflow_state.scratchpad.get('stage_loop_index', 1))
    production = store.state.workflow_state.scratchpad.get('direct_session_production')
    scope = direct_request_scope(stage=context.conversation_stage, attempt=context.attempt, loop_index=index)
    input_id = f'direct-user:{scope}'
    if production:
        store.run_event_store.emit('direct_session_bound', 'direct-session:core', {
            'conversation_id': session.shared.history.conversation_id,
        })
    else:
        record_direct_request(store, stage=context.conversation_stage,
                              attempt=context.attempt, loop_index=index)
    _deliver_input(session, input_id=input_id, text=current_request)
    feedback = store.state.feedback_state
    handoff = feedback.active_handoff
    if (feedback.critic_loop.phase == 'core_post_feedback' and handoff.record_id
            and handoff.consumer_stage == context.conversation_stage
            and handoff.consumer_attempt == context.attempt):
        _deliver_input(
            session, input_id=f'direct-feedback:{handoff.record_id}',
            text=render_feedback_envelope(feedback, record_id=handoff.record_id,
                                          run_dir=context.run_dir),
        )
    nudge = str(store.state.workflow_state.scratchpad.get('role_loop_continuation_nudge') or '').strip()
    if nudge:
        # Each nudge answers the preceding provider output, including across interrupted delivery.
        exchange_id = next(entry.exchange_id for entry in reversed(session.shared.history.snapshot)
                           if isinstance(entry.item, ProviderOutputItem))
        _deliver_input(session, input_id=f'direct-nudge:{input_id}:{exchange_id}',
                       text=f'Runtime continuation:\n{nudge}')


def _deliver_input(session: 'CoreLoopSession', *, input_id: str, text: str) -> None:
    context = session.context
    store = context.state_store
    history = session.shared.history
    event_id = f'direct-input:{input_id}'
    if any(event.event_id == event_id for event in store.run_event_store.events):
        return
    # Before the delivery event, no provider call or compaction can remove this input.
    entry = next((entry for entry in history.snapshot if entry.exchange_id == input_id), None)
    if entry is None:
        recorded = history.record_items((HistoryEntry(HistoryMessage('user', text), input_id),))
        entry = recorded[0]
    reference = history.confirmed_reference(entry=entry)
    if reference is None:
        raise OSError('Direct input persistence is unconfirmed')
    if input_id.startswith('direct-user:'):
        record_direct_request(
            store, stage=context.conversation_stage, attempt=context.attempt,
            loop_index=int(store.state.workflow_state.scratchpad.get('stage_loop_index', 1)),
        )
    store.poll_runtime_control('direct_input_recorded')
    store.run_event_store.emit('direct_input_delivered', event_id, {
        'input_id': input_id,
        'source': {
            'path': str(history.recorder.path.resolve().relative_to(context.run_dir.resolve())),
            'item_id': reference['item_id'],
        },
    })
