"""Minimal child resource observations; no recovery or checkpoint projection."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import TYPE_CHECKING, Any

from ownpi.runtime.rollout.process_owner_events import record_process_owner_binding

if TYPE_CHECKING:
    from ownpi.runtime.tools.runtime import ToolRuntimeContext


def _identity(values: Any) -> str:
    return sha256(json.dumps(values, sort_keys=True).encode('utf-8')).hexdigest()


async def record_child_resources(context: ToolRuntimeContext) -> None:
    manager = context.process_owner_manager
    actor = context.process_owner_actor_id or context.actor_id
    refs = manager.actor_process_refs(run_id=context.run_id, actor_id=actor) if manager else ()
    store = context.state_store.run_event_store
    if store is None:
        raise OSError('Child resource observations require a rollout writer')
    for owner_id, process_id in refs:
        binding = manager.bindings.get(owner_id)
        if binding is None:
            raise RuntimeError(f'Child process has no owner binding: {owner_id}')
        record_process_owner_binding(store, binding)
        identity = _identity([context.actor_id, actor, owner_id, process_id])
        resource_event_id = f'child-resource:{identity}'
        store.emit('child_resource_bound', resource_event_id, {
            'child_id': context.actor_id, 'process_owner_actor_id': actor,
            'owner_id': owner_id, 'process_id': process_id,
        })
        if binding.transport_kind == 'local_unix' and manager.has_client(owner_id):
            from ownpi.subagent.local_resource_liveness import local_host_scope

            response = await manager.read_process_async(
                owner_id=owner_id, process_id=process_id, max_bytes=1,
            )
            group = response.get('process_group_id')
            scope = local_host_scope()
            if type(group) is int and group > 0 and scope is not None:
                locator = {'resource_event_id': resource_event_id,
                           'local_process_group_id': group, 'local_host_scope': scope}
                store.emit('child_resource_locator_observed',
                           f'child-locator:{_identity(locator)}', locator)


def record_child_resources_released(context: ToolRuntimeContext) -> None:
    actor = context.process_owner_actor_id or context.actor_id
    context.state_store.run_event_store.emit(
        'child_resources_released', f'child-released:{_identity([context.actor_id, actor])}',
        {'child_id': context.actor_id, 'process_owner_actor_id': actor},
    )
