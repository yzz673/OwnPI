"""Project child tasks and committed outcomes from original call references."""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from typing import Iterable

from ownpi.runtime.rollout.business_events import BusinessEvent
from ownpi.runtime.rollout.source_reader import read_original_call
from ownpi.subagent.state import ChildState
from ownpi.runtime.rollout.resume_read_session import ResumeReadSession


@dataclass(frozen=True)
class ChildProjection:
    states: tuple[ChildState, ...]
    sources: dict[str, dict[str, str]]
    task_indexes: dict[str, int]
    completion_sources: dict[str, dict[str, str]]


def project_children(events: Iterable[BusinessEvent], *, run_root: Path,
                     run_id: str, parent_conversation_id: str | None = None,
                     source: dict[str, str] | None = None,
                     selected_child_id: str | None = None,
                     read_session: ResumeReadSession | None = None) -> ChildProjection:
    read_session = read_session or ResumeReadSession()
    events = tuple(events)
    states: dict[str, ChildState] = {}
    sources: dict[str, dict[str, str]] = {}
    indexes: dict[str, int] = {}
    completion_sources: dict[str, dict[str, str]] = {}
    admitted_parents: dict[str, str] = {}
    for event in events:
        data = event.data.to_object()
        child_id = data.get('child_id')
        if event.kind == 'child_admitted':
            if source is not None and data['source'] != source:
                continue
            if selected_child_id is not None and child_id != selected_child_id:
                continue
            call = read_original_call(run_root, data['source'], run_id=run_id, tool_name='run_subagents', read_session=read_session)
            if parent_conversation_id and call.metadata['id'] != parent_conversation_id:
                continue
            tasks = call.arguments['tasks']
            index = data['task_index']
            if type(index) is not int or index < 0 or index >= len(tasks):
                raise ValueError('Child admission task_index is outside its original batch')
            task = tasks[index]
            batch = sha256(f'{call.metadata["id"]}:{data["source"]["item_id"]}'.encode()).hexdigest()
            # Owner-local coordination must not conflate reused provider call IDs.
            identity = f'resumed:{batch}'
            state = ChildState(batch, task['task_name'].strip(), child_id,
                               task_message=task['message'], invocation_id=identity)
            if child_id in states and states[child_id] != state:
                raise ValueError('Conflicting child admission')
            if any(value.batch_id == batch and indexes[key] == index for key, value in states.items()
                   if key != child_id):
                raise ValueError('Multiple children admitted for one task_index')
            states[child_id], sources[child_id], indexes[child_id] = state, data['source'], index
            admitted_parents[child_id] = call.metadata['id']
        elif child_id in states:
            state = states[child_id]
            if event.kind == 'child_execution_started':
                states[child_id] = replace(state, status='running')
            elif event.kind == 'child_execution_finished':
                states[child_id] = replace(state, status=data['status'], terminal_status=data['status'],
                                           error=data.get('error'))
            elif event.kind == 'child_completion_committed':
                call = read_original_call(run_root, data['source'], tool_name='complete_subagent',
                                          actor_id=child_id, run_id=child_id, read_session=read_session)
                if call.metadata.get('parent_id') != admitted_parents[child_id]:
                    raise ValueError('Child completion belongs to a different parent')
                content = call.arguments.get('content')
                if not isinstance(content, str) or not content.strip():
                    raise ValueError('Committed child completion lacks content')
                states[child_id] = replace(state, status='completed', completion_content=content.strip(),
                                           completion_committed=True, terminal_status='completed')
                completion_sources[child_id] = dict(data['source'])
            elif event.kind == 'child_worker_reclaimed':
                states[child_id] = replace(state, status='reclaimed')
    return ChildProjection(tuple(states.values()), sources, indexes, completion_sources)
