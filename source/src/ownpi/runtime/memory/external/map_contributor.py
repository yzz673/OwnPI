from __future__ import annotations

from collections.abc import Iterable

from ownpi.runtime.memory.external.navigation import (
    EXTERNAL_KNOWLEDGE_READ_TOOL,
    EXTERNAL_KNOWLEDGE_SCOPES,
    EXTERNAL_KNOWLEDGE_SEARCH_TOOL,
    has_canonical_external_knowledge_tools,
)
from ownpi.runtime.memory.map_contracts import (
    MemoryAuthority,
    MemoryDomain,
    MemoryMapSection,
    MemoryMapStatus,
    MemoryMutability,
)
from ownpi.runtime.state_models import RuntimeState
from ownpi.runtime.tools.definitions import ToolDefinition


def build_external_memory_map_contribution(
    state: RuntimeState,
    *,
    definitions: Iterable[ToolDefinition],
) -> MemoryMapSection | None:
    config = state.runtime_preferences.external_knowledge
    if not bool(config.get('enabled', False)):
        return None
    pin = state.memory_state.external_knowledge_pin
    status = str(state.memory_state.external_knowledge_status or 'unavailable')
    if pin is None or status == MemoryMapStatus.UNAVAILABLE.value:
        return _unavailable_section()
    if not has_canonical_external_knowledge_tools(definitions):
        return _unavailable_section()
    map_status = (
        MemoryMapStatus.DEGRADED
        if status == MemoryMapStatus.DEGRADED.value
        else MemoryMapStatus.AVAILABLE
    )
    identity = ':'.join(
        (
            'nkf-release',
            pin.release_id,
            pin.snapshot_sha256[:16],
            '+'.join(source.source_id.value for source in pin.sources),
        )
    )
    return MemoryMapSection.create(
        domain=MemoryDomain.EXTERNAL_KNOWLEDGE,
        status=map_status,
        authority=MemoryAuthority.FACTORY_OWNED,
        mutability=MemoryMutability.READ_ONLY,
        search_tool=EXTERNAL_KNOWLEDGE_SEARCH_TOOL,
        read_tool=EXTERNAL_KNOWLEDGE_READ_TOOL,
        identity=identity,
        scopes=EXTERNAL_KNOWLEDGE_SCOPES,
    )


def _unavailable_section() -> MemoryMapSection:
    return MemoryMapSection.create(
        domain=MemoryDomain.EXTERNAL_KNOWLEDGE,
        status=MemoryMapStatus.UNAVAILABLE,
        authority=MemoryAuthority.FACTORY_OWNED,
        mutability=MemoryMutability.READ_ONLY,
    )
