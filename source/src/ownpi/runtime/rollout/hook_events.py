"""Durable hook delivery facts without a hook receipt or snapshot dependency."""

from __future__ import annotations

import logging
import hashlib
import os
from pathlib import Path
import stat
from typing import Any

from ownpi.runtime.rollout.run_events import RunEventStore
from ownpi.runtime.native_tools.provider_json import FrozenProviderJson


def register_hook_context(store: RunEventStore, fragment: dict[str, Any]) -> None:
    # Child contexts may be reclaimed before run close; the shared owner keeps
    # unconfirmed content, not the transient child or its legacy checkpoint.
    with store._lock:
        identity = str(fragment.get('delivery_id') or '')
        frozen = FrozenProviderJson.from_value(fragment)
        previous = store.pending_hook_contexts.get(identity)
        if previous is not None and previous != frozen:
            raise ValueError('Conflicting pending hook context')
        store.pending_hook_contexts[identity] = frozen
        _register_hook_context(store, fragment)
        store.pending_hook_contexts.pop(identity, None)


def flush_hook_registrations(store: RunEventStore) -> None:
    with store._lock:
        for fragment in tuple(store.pending_hook_contexts.values()):
            register_hook_context(store, fragment.to_object())


def _register_hook_context(store: RunEventStore, fragment: dict[str, Any]) -> None:
    identity = str(fragment.get('delivery_id') or '')
    if not identity:
        raise ValueError('Hook context requires delivery identity')
    source = fragment.get('source_ref')
    if fragment.get('content_truncated') and isinstance(source, dict):
        path = Path(source['logical_ref'])
        if not stat.S_ISREG(path.lstat().st_mode) or path.stat().st_nlink != 1:
            raise OSError('Hook attachment must be a regular unlinked file')
        with path.open('rb') as handle:
            content = handle.read()
            if (hashlib.sha256(content).hexdigest() != source['content_digest']
                    or len(content) != source['content_bytes']):
                raise OSError('Hook attachment does not match its source reference')
            os.fsync(handle.fileno())
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    store.emit('hook_context_registered', f'hook:{identity}', {'fragment': fragment})


def hook_contexts(store: RunEventStore) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for event in store.events:
        data = event.data.to_object()
        if event.kind == 'hook_context_registered':
            fragment = data['fragment']
            values[fragment['delivery_id']] = {'fragment': fragment, 'status': 'active',
                                                'persisted_at': event.timestamp}
        elif event.kind == 'delivery_recorded' and data.get('source_kind') == 'hook':
            value = values.get(data['source_id'])
            if value is not None:
                value.update(status='consumed', consumer=data['consumer'], consumed_at=event.timestamp)
    return values


def record_hook_delivery(
    context_state: Any, identities: tuple[str, ...], *, history: Any,
    consumer: str, entry: Any = None, exchange_id: str = '',
) -> bool:
    store = getattr(context_state, '_run_event_store', None)
    if store is None or not identities:
        return True
    reference = history.confirmed_reference(entry=entry, exchange_id=exchange_id)
    if reference is None:
        return False
    try:
        for identity in dict.fromkeys(identities):
            if not any(event.event_id == f'hook:{identity}' for event in store.events):
                record = getattr(context_state, 'hook_delivery_records', {}).get(identity)
                if not record:
                    return False
                register_hook_context(store, record['fragment'])
            occurrence = ':'.join(str(reference.get(key) or '') for key in (
                'conversation_id', 'item_id', 'exchange_id', 'turn_id',
            ))
            store.emit('delivery_recorded', f'hook-delivered:{identity}:{consumer}:{occurrence}', {
                'source_kind': 'hook', 'source_id': identity, 'consumer': consumer,
            })
    except OSError:
        logging.getLogger(__name__).warning('Hook delivery persistence unconfirmed', exc_info=True)
        return False
    return True
