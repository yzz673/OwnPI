"""Composition helpers; the writer outlives individual model turns."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ownpi.runtime.rollout.hook_events import hook_contexts
from ownpi.runtime.rollout.run_events import RunEventStore


def attach_run_events(state_store: Any, run_dir: Path) -> RunEventStore:
    current = getattr(state_store, 'run_event_store', None)
    if current is not None and not current.closed:
        return current
    store = RunEventStore(run_dir, state_store.state.run_metadata.run_id)
    state_store.run_event_store = store
    context = getattr(state_store.state, 'context_state', None)
    if context is not None:
        context._run_event_store = store
        context.hook_delivery_records = hook_contexts(store)
    return store


def close_run_events(state_store: Any) -> bool:
    store = getattr(state_store, 'run_event_store', None)
    if store is None:
        return True
    if not store.close():
        logging.getLogger(__name__).warning('Run event close unconfirmed: %s', store.path)
        return False
    return True
