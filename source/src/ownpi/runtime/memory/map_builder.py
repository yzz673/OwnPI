from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any, Iterable

from ownpi.runtime.memory.map_contracts import (
    MEMORY_MAP_SCHEMA_VERSION,
    MemoryAuthority,
    MemoryDomain,
    MemoryMap,
    MemoryMapSection,
    MemoryMapStatus,
    MemoryMutability,
    memory_map_digest,
    memory_map_id,
)
from ownpi.runtime.tools.definitions import ToolDefinition
from ownpi.runtime.tools.router import ToolRouter


@dataclass(frozen=True)
class MemoryMapCallScope:
    run_id: str
    task_id: str
    agent_id: str
    role: str
    operation: str
    stage: str
    attempt: int
    continuation_index: int
    query_chain_id: str
    query_chain_depth: int
    schema_version: int = MEMORY_MAP_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MEMORY_MAP_SCHEMA_VERSION:
            raise ValueError('unsupported memory map call scope schema version')
        for field_name in ('run_id', 'task_id', 'agent_id', 'role', 'operation', 'stage'):
            if not str(getattr(self, field_name) or '').strip():
                raise ValueError(f'{field_name} must be non-empty')
        if self.attempt < 0 or self.continuation_index < 0 or self.query_chain_depth < 0:
            raise ValueError('memory map call scope indexes must be non-negative')

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        task_id: str,
        agent_id: str,
        role: str,
        operation: str,
        stage: str,
        attempt: int,
        continuation_index: int,
        query_chain_id: str = '',
        query_chain_depth: int = 0,
    ) -> MemoryMapCallScope:
        return cls(
            run_id=str(run_id or '').strip(),
            task_id=str(task_id or '').strip(),
            agent_id=str(agent_id or '').strip(),
            role=str(role or '').strip(),
            operation=str(operation or '').strip(),
            stage=str(stage or '').strip(),
            attempt=int(attempt),
            continuation_index=int(continuation_index),
            query_chain_id=str(query_chain_id or '').strip(),
            query_chain_depth=int(query_chain_depth),
        )

    @property
    def scope_id(self) -> str:
        digest = _sha256_payload(self.to_identity_dict())
        return f'memory-call:{digest[:24]}'

    def to_identity_dict(self) -> dict[str, Any]:
        return {
            'schema_version': self.schema_version,
            'run_id': self.run_id,
            'task_id': self.task_id,
            'agent_id': self.agent_id,
            'role': self.role,
            'operation': self.operation,
            'stage': self.stage,
            'attempt': self.attempt,
            'continuation_index': self.continuation_index,
            'query_chain_id': self.query_chain_id,
            'query_chain_depth': self.query_chain_depth,
        }


@dataclass(frozen=True)
class MemoryMapBuilder:
    def build(
        self,
        *,
        scope: MemoryMapCallScope,
        internal: MemoryMapSection | None,
        external: MemoryMapSection | None,
        callable_tool_names: Iterable[str],
        read_only_tool_names: Iterable[str],
    ) -> MemoryMap | None:
        if internal is None and external is None:
            return None
        callable_names = {
            str(name or '').strip() for name in callable_tool_names if str(name or '').strip()
        }
        read_only_names = {
            str(name or '').strip() for name in read_only_tool_names if str(name or '').strip()
        }
        internal_section = self._section_or_unavailable(
            domain=MemoryDomain.INTERNAL_MEMORY,
            contribution=internal,
            callable_tool_names=callable_names,
            read_only_tool_names=read_only_names,
        )
        external_section = self._section_or_unavailable(
            domain=MemoryDomain.EXTERNAL_KNOWLEDGE,
            contribution=external,
            callable_tool_names=callable_names,
            read_only_tool_names=read_only_names,
        )
        sections = (internal_section, external_section)
        digest = memory_map_digest(sections)
        return MemoryMap(
            map_id=memory_map_id(scope.scope_id, digest),
            call_scope_id=scope.scope_id,
            sections=sections,
            digest=digest,
        )

    def build_required(
        self,
        *,
        scope: MemoryMapCallScope,
        internal: MemoryMapSection | None,
        external: MemoryMapSection | None,
        callable_tool_names: Iterable[str],
        read_only_tool_names: Iterable[str],
    ) -> MemoryMap:
        """Build a truthful map even when both memory domains are unavailable."""
        result = self.build(
            scope=scope,
            internal=internal or _unavailable_section(domain=MemoryDomain.INTERNAL_MEMORY),
            external=external,
            callable_tool_names=callable_tool_names,
            read_only_tool_names=read_only_tool_names,
        )
        if result is None:
            raise RuntimeError("required memory map construction returned no map")
        return result

    @staticmethod
    def _section_or_unavailable(
        *,
        domain: MemoryDomain,
        contribution: MemoryMapSection | None,
        callable_tool_names: set[str],
        read_only_tool_names: set[str],
    ) -> MemoryMapSection:
        if contribution is None:
            return _unavailable_section(domain=domain)
        if contribution.domain != domain:
            raise ValueError(f'memory map contribution domain mismatch for {domain.value}')
        advertised = {contribution.search_tool, contribution.read_tool} - {''}
        tools_are_callable_and_read_only = advertised.issubset(
            callable_tool_names & read_only_tool_names
        )
        if (
            contribution.status != MemoryMapStatus.UNAVAILABLE
            and not tools_are_callable_and_read_only
        ):
            return replace(
                contribution,
                status=MemoryMapStatus.UNAVAILABLE,
                search_tool='',
                read_tool='',
            )
        return contribution


def build_memory_map_call_scope(
    *,
    state: Any,
    agent_id: str,
    role: str,
    operation: str,
) -> MemoryMapCallScope:
    workflow = state.workflow_state
    metadata = state.run_metadata
    return MemoryMapCallScope.create(
        run_id=metadata.run_id,
        task_id=metadata.task_id or 'unknown-task',
        agent_id=agent_id,
        role=role,
        operation=operation,
        stage=workflow.current_stage.value,
        attempt=workflow.current_stage_attempt,
        continuation_index=int(workflow.scratchpad.get('continuation_index', 0) or 0),
        query_chain_id=str(workflow.current_query_chain_id or ''),
        query_chain_depth=int(workflow.current_query_chain_depth or 0),
    )


def build_runtime_memory_map(
    *,
    state: Any,
    agent_id: str,
    role: str,
    operation: str,
    tool_router: ToolRouter,
    internal: MemoryMapSection | None = None,
    external: MemoryMapSection | None = None,
    internal_memory_snapshot: Any | None = None,
) -> MemoryMap:
    """Build the existing canonical Memory Map through the Memory owner."""
    from ownpi.runtime.memory.external.map_contributor import (
        build_external_memory_map_contribution,
    )
    from ownpi.runtime.memory.internal.map_contributor import (
        build_internal_memory_map_contribution,
    )

    visible_names = set(tool_router.visible_names)
    return MemoryMapBuilder().build_required(
        scope=build_memory_map_call_scope(
            state=state,
            agent_id=agent_id,
            role=role,
            operation=operation,
        ),
        internal=(
            internal
            if internal is not None
            else build_internal_memory_map_contribution(
                state,
                repository_snapshot=internal_memory_snapshot,
            )
        ),
        external=(
            external
            if external is not None
            else build_external_memory_map_contribution(
                state,
                definitions=tool_router.registry.definitions,
            )
        ),
        callable_tool_names=visible_names,
        read_only_tool_names=_read_only_visible_tool_names(
            tool_router.registry.definitions,
            visible_names=visible_names,
        ),
    )


def _read_only_visible_tool_names(
    definitions: Iterable[ToolDefinition],
    *,
    visible_names: set[str],
) -> set[str]:
    return {
        definition.name
        for definition in definitions
        if definition.name in visible_names and definition.read_only
    }


def _unavailable_section(*, domain: MemoryDomain) -> MemoryMapSection:
    if domain == MemoryDomain.INTERNAL_MEMORY:
        authority = MemoryAuthority.AGENT_OWNED
        mutability = MemoryMutability.WRITABLE
    else:
        authority = MemoryAuthority.FACTORY_OWNED
        mutability = MemoryMutability.READ_ONLY
    return MemoryMapSection.create(
        domain=domain,
        status=MemoryMapStatus.UNAVAILABLE,
        authority=authority,
        mutability=mutability,
    )


def _sha256_payload(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=False,
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()
