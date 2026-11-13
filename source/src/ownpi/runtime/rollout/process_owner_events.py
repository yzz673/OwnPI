"""Shared identities for established process-owner bindings."""

from __future__ import annotations

from hashlib import sha256
import json

from ownpi.runtime.process_owner.bindings import ProcessOwnerBinding
from ownpi.runtime.rollout.business_events import BusinessEvent
from ownpi.runtime.rollout.run_events import RunEventStore


def process_owner_binding_fact(binding: ProcessOwnerBinding) -> tuple[str, dict[str, str]]:
    data = {'owner_id': binding.owner_id, 'transport_kind': binding.transport_kind}
    identity = f'process-owner:{binding.owner_id}'
    if binding.transport_kind in {'remote_ssh_unix', 'local_unix'}:
        if not binding.resume_session_id.strip():
            raise ValueError('Established socket owner binding requires resume_session_id')
        key = json.dumps(
            [binding.owner_id, binding.transport_kind, binding.resume_session_id],
            ensure_ascii=True, separators=(',', ':'),
        )
        transport = 'remote' if binding.transport_kind == 'remote_ssh_unix' else 'local'
        identity = f'process-owner:{transport}:{sha256(key.encode("utf-8")).hexdigest()}'
        data.update(endpoint_ref=binding.endpoint_ref, resume_session_id=binding.resume_session_id)
    return identity, data


def record_process_owner_binding(store: RunEventStore, binding: ProcessOwnerBinding) -> BusinessEvent:
    identity, data = process_owner_binding_fact(binding)
    return store.emit('process_owner_bound', identity, data)
