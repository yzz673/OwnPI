"""Reserve valid child identities before the parent execution-start record."""

from typing import Any
from uuid import uuid4

from .live_state import LiveSubagentStateStore
from .state import ChildState
from .tools import _validate_run


def prepare_batch_invocation(
    store: LiveSubagentStateStore, *, invocation_id: str, payload: dict[str, Any],
) -> None:
    # Invalid model input must still receive the ordinary tool validation error.
    if set(payload) != {"tasks"}:
        return
    try:
        validated = _validate_run(None, payload)
    except ValueError:
        return
    if not invocation_id or validated is None:
        raise ValueError("subagent preparation requires a durable invocation identity")
    tasks = validated["tasks"]
    states = store.batch_states(invocation_id, tasks)
    if not states:
        batch_id = uuid4().hex
        states = tuple(
            ChildState(batch_id, task['task_name'], uuid4().hex,
                       task_message=task['message'], invocation_id=invocation_id)
            for task in tasks
        )
        store.reserve(states)
    if store.completion_events is None:
        raise RuntimeError('Child admission requires a rollout writer')
    store.completion_events.register(states)
