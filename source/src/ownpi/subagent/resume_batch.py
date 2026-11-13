"""Continue one original blocking batch using its admitted child identities."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from ownpi.runtime.contracts import ToolResult
from ownpi.controller.runtime_control_signal import RuntimeControlRequested
from ownpi.runtime.rollout.resume import read_child_resume
from ownpi.subagent.contracts import SubagentTask
from ownpi.subagent.state import ChildState
from ownpi.runtime.interactions.pause import raise_child_interactions


def resume_parent_batch(context, call) -> ToolResult:
    owner = context.subagent_owner
    if owner is None or not owner.enabled or owner.execution_factory is None:
        raise RuntimeError('The original blocking batch requires an enabled child owner')
    completions = owner.coordinator.completion_events
    live = owner.coordinator.state_store
    if completions is None or live is None:
        raise RuntimeError('The original blocking batch requires current run facts')
    completions.read_session = context.resume_read_session
    projection = completions.hydrate(call.metadata['id'], source=call.source)
    selected = sorted((state for state in projection.states
                       if projection.sources[state.child_id] == call.source),
                      key=lambda state: projection.task_indexes[state.child_id])
    if not selected:
        from ownpi.subagent.tools import _validate_run

        tasks = _validate_run(None, call.arguments)['tasks']
        invocation_id = call.call.request_id or call.call.call_id
        owner.execution_factory.parent_context = replace(owner.execution_factory.parent_context,
                                                          rollout_conversation_id=call.metadata['id'])
        owner.prepare_batch_invocation(invocation_id=invocation_id, payload={'tasks': tasks},
                                       source=call.source)
        result = owner.run_batch(tuple(SubagentTask(**task) for task in tasks),
                                 invocation_id=invocation_id, cancel_checker=context.cancel_checker)
        raise_child_interactions(result.pending_interactions)
        return ToolResult(ok=result.status == 'completed', tool_name='run_subagents', data=result.to_dict())
    if [projection.task_indexes[state.child_id] for state in selected] != list(range(len(selected))):
        raise ValueError('Child admissions are not a prefix of the original batch')
    from ownpi.subagent.tools import _validate_run

    tasks = _validate_run(None, call.arguments)['tasks']
    owner.validate_batch_size(len(tasks))
    if len(selected) > len(tasks) or any(
        (state.task_name, state.task_message) != (task['task_name'], task['message'])
        for state, task in zip(selected, tasks)
    ):
        raise ValueError('Recovered child admission differs from original batch task')
    current = {state.child_id: state for state in live.load()}
    for state in selected:
        previous = current.get(state.child_id)
        if previous is not None and (
            previous.invocation_id, previous.task_name, previous.task_message
        ) != (state.invocation_id, state.task_name, state.task_message):
            raise ValueError('Recovered child admission differs from current owner state')
    live.hydrate(tuple(state for state in selected if state.child_id not in current))
    # A sibling may already have completed or been reclaimed during an earlier
    # attempt in this owner. Re-reading facts must not roll its live state back.
    selected = [current.get(state.child_id, state) for state in selected]
    # Unconfirmed admissions retain their reserved identity on same-owner retry.
    # The existing writer can then confirm the original event idempotently.
    reserved = {completions._task_indexes[state.child_id]: state for state in current.values()
                if state.invocation_id == selected[0].invocation_id
                and state.child_id in completions._task_indexes}
    missing = []
    for index, task in enumerate(tasks[len(selected):], start=len(selected)):
        state = reserved.get(index)
        if state is not None:
            if (state.status != 'created' or (state.task_name, state.task_message)
                    != (task['task_name'], task['message'])):
                raise ValueError('Reserved child differs from original batch task')
        else:
            state = ChildState(selected[0].batch_id, task['task_name'], uuid4().hex,
                               task_message=task['message'], invocation_id=selected[0].invocation_id)
        missing.append(state)
    missing = tuple(missing)
    if missing:
        live.reserve(missing, allow_reserved_ids=frozenset(current))
        for index, state in enumerate(missing, start=len(selected)):
            completions._task_indexes[state.child_id] = index
        completions.register(missing)
    states = (*selected, *missing)
    owner.execution_factory.parent_context = replace(owner.execution_factory.parent_context,
                                                      rollout_conversation_id=call.metadata['id'])
    scheduled = []
    facts_by_child = {}

    def record_failure(state: ChildState, error: str) -> None:
        live.save((replace(state, status='failed', terminal_status='failed',
                           error=f'{state.child_id} ({state.task_name}): {error}'),))

    for state in states:
        if state.status == 'created':
            scheduled.append(state)
            continue
        if state.status == 'reclaimed':
            if state.completion_committed:
                from ownpi.subagent.contracts import SubagentResult

                owner._completed[state.child_id] = SubagentResult(state.task_name, 'completed', state.completion_content)
            continue
        if state.completion_committed or state.terminal_status in {'failed', 'cancelled'}:
            if state.completion_committed:
                from ownpi.subagent.contracts import SubagentResult

                owner._completed[state.child_id] = SubagentResult(state.task_name, 'completed', state.completion_content)
            continue
        else:
            relationships = owner.historical_agent_relationships
            candidates = [relation for relation in relationships.relations
                          if relation.actor_id == state.child_id and relation.parent_id == call.metadata['id']] if relationships else []
            if len(candidates) != 1:
                if candidates:
                    record_failure(state, 'ambiguous conversations')
                    continue
                record_failure(state, 'running child has no conversation')
                continue
            try:
                facts = read_child_resume(context.run_dir, child_id=state.child_id,
                                          conversation_id=candidates[0].conversation_id,
                                          parent_conversation_id=call.metadata['id'],
                                          read_session=context.resume_read_session)
            except (OSError, ValueError, TypeError, KeyError) as error:
                record_failure(state, f'{type(error).__name__}: {error}')
                continue
            scheduled.append(state)
            facts_by_child[state.child_id] = facts
            continue
    results = owner.run_recovered_children(tuple(scheduled), facts_by_child=facts_by_child,
                                           cancel_checker=context.cancel_checker)
    waiting = []
    for state, result in zip(scheduled, results, strict=True):
        if isinstance(result, Exception):
            if isinstance(result, RuntimeControlRequested):
                raise result
            current = next(child for child in live.load() if child.child_id == state.child_id)
            if not current.completion_committed:
                record_failure(current, f'{type(result).__name__}: {result}')
            continue
        if result.status == 'waiting':
            waiting.extend(result.pending_interactions)
            live.save((replace(state, status='waiting', terminal_status=''),))
            continue
        current = next(child for child in live.load() if child.child_id == state.child_id)
        if result.status == 'completed' and not current.completion_committed:
            raise RuntimeError('Resumed child completion was not committed')
        live.save((replace(current, terminal_status=result.status,
                           error=result.content if result.status == 'failed' else current.error,
                           completion_content=result.content),))
    raise_child_interactions(tuple(waiting))
    result = owner.run_batch(tuple(SubagentTask(**task) for task in tasks),
                             invocation_id=states[0].invocation_id, cancel_checker=context.cancel_checker)
    raise_child_interactions(result.pending_interactions)
    return ToolResult(ok=result.status == 'completed', tool_name='run_subagents', data=result.to_dict())
