"""Read historical owners and child resources without probing or writing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from ownpi.runtime.process_owner.bindings import ProcessOwnerBinding
from ownpi.runtime.rollout.business_events import BusinessEvent


@dataclass
class ResourceProjection:
    owners: dict[str, list[ProcessOwnerBinding]] = field(default_factory=dict)
    resources: dict[str, dict[str, Any]] = field(default_factory=dict)
    released: set[tuple[str, str]] = field(default_factory=set)
    completed: set[str] = field(default_factory=set)
    diagnostics: dict[str, str] = field(default_factory=dict)
    execution_bindings: dict[str, ProcessOwnerBinding | None] = field(default_factory=dict)
    execution_owners: dict[str, str] = field(default_factory=dict)
    settled_executions: set[str] = field(default_factory=set)

    def binding_for_owner(self, owner: str) -> ProcessOwnerBinding | None:
        values = self.owners.get(owner, [])
        if len(values) == 1:
            return values[0]
        needed = {binding for identity, binding in self.execution_bindings.items()
                  if self.execution_owners[identity] == owner and binding is not None
                  and identity not in self.settled_executions}
        return next(iter(needed)) if len(needed) == 1 else None

    def installable_bindings(self) -> tuple[ProcessOwnerBinding, ...]:
        return tuple(binding for owner in self.owners
                     if (binding := self.binding_for_owner(owner)) is not None
                     and owner not in self.diagnostics)

    def child_resources(self, child_id: str) -> tuple[dict[str, Any], ...]:
        return tuple(dict(item) for item in self.resources.values()
                     if item['child_id'] == child_id
                     and (child_id, item['process_owner_actor_id']) not in self.released)

    def release_confirmed(self, child_id: str) -> bool:
        actors = {item['process_owner_actor_id'] for item in self.resources.values()
                  if item['child_id'] == child_id}
        return (bool(actors) and all((child_id, actor) in self.released for actor in actors)
                or not actors and any(child == child_id for child, _ in self.released))


def project_resources(events: Iterable[BusinessEvent]) -> ResourceProjection:
    result = ResourceProjection()
    handles: dict[str, tuple[str, str]] = {}
    terminal: set[str] = set()
    readback: set[str] = set()
    for event in events:
        data = event.data.to_object()
        if event.kind == 'process_owner_bound':
            owner = str(data.get('owner_id') or '')
            try:
                binding = ProcessOwnerBinding.from_dict(data)
                if binding.transport_kind in {'remote_ssh_unix', 'local_unix'} and not binding.resume_session_id:
                    raise ValueError('socket binding lacks its original session')
                values = result.owners.setdefault(owner, [])
                if binding not in values:
                    values.append(binding)
                if len(values) > 1:
                    result.diagnostics[owner] = 'multiple historical owner sessions require explicit resource association'
            except ValueError as error:
                result.diagnostics[owner] = str(error)
        elif event.kind == 'child_resource_bound':
            item = dict(data)
            bindings = result.owners.get(data['owner_id'], [])
            if len(bindings) == 1:
                item['binding'] = bindings[0].to_dict()
            result.resources[event.event_id] = item
        elif event.kind in {'execution_registered', 'execution_binding_updated'}:
            identity = data['execution_id']
            owner = data['handle'].get('owner_id', '')
            handle = (owner, data['handle'].get('process_id', ''))
            if owner:
                result.execution_owners[identity] = owner
                # Locator updates do not change a previously established process identity.
                if handles.get(identity) != handle:
                    bindings = result.owners.get(owner, [])
                    result.execution_bindings[identity] = bindings[0] if len(bindings) == 1 else None
            else:
                result.execution_owners.pop(identity, None)
                result.execution_bindings.pop(identity, None)
            handles[identity] = handle
        elif event.kind == 'execution_state_confirmed':
            identity = data['execution_id']
            if data.get('status') in {'terminal_success', 'terminal_failure'}:
                terminal.add(identity)
            else:
                terminal.discard(identity)
        elif event.kind == 'execution_readback_recorded':
            identity = data['execution_id']
            if data.get('readback_status') == 'complete':
                readback.add(identity)
            else:
                readback.discard(identity)
        elif event.kind == 'child_resource_locator_observed':
            identity = data['resource_event_id']
            if identity in result.resources:
                result.resources[identity].update({key: value for key, value in data.items()
                                                  if key != 'resource_event_id'})
        elif event.kind == 'child_resources_released':
            result.released.add((data['child_id'], data['process_owner_actor_id']))
        elif event.kind == 'child_completion_committed':
            result.completed.add(data['child_id'])
    result.settled_executions = terminal & readback
    for owner, bindings in result.owners.items():
        if len(bindings) > 1 and result.binding_for_owner(owner) is not None:
            result.diagnostics.pop(owner, None)
    return result
