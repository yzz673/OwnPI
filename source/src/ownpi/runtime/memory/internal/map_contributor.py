from __future__ import annotations

from pathlib import Path
from typing import Any

from ownpi.runtime.memory.map_contracts import (
    MemoryAuthority,
    MemoryDomain,
    MemoryMapSection,
    MemoryMapStatus,
    MemoryMutability,
)

from .contracts import InternalMemoryScopeType
from .repository import (
    InternalMemoryRepository,
    InternalMemoryRepositorySnapshot,
    InternalMemoryRepositoryStatus,
)


INTERNAL_MEMORY_SEARCH_TOOL = "internal_memory_search"
INTERNAL_MEMORY_READ_TOOL = "internal_memory_read"


def build_internal_memory_map_contribution(
    state: Any,
    *,
    repository_snapshot: InternalMemoryRepositorySnapshot | None = None,
) -> MemoryMapSection | None:
    config = getattr(state.runtime_preferences, "internal_memory", {})
    if not bool(config.get("enabled", False)):
        return None
    snapshot = repository_snapshot or internal_memory_repository_snapshot(state)
    _remember_snapshot_identity(state, snapshot)
    if snapshot.status in {
        InternalMemoryRepositoryStatus.CORRUPT,
        InternalMemoryRepositoryStatus.UNAVAILABLE,
    }:
        return _unavailable_section()
    identity = ":".join(
        (
            "nim-store",
            snapshot.identity[:24],
            str(snapshot.record_count),
            str(snapshot.committed_count),
        )
    )
    return MemoryMapSection.create(
        domain=MemoryDomain.INTERNAL_MEMORY,
        status=MemoryMapStatus.AVAILABLE,
        authority=MemoryAuthority.AGENT_OWNED,
        mutability=MemoryMutability.WRITABLE,
        search_tool=INTERNAL_MEMORY_SEARCH_TOOL,
        read_tool=INTERNAL_MEMORY_READ_TOOL,
        identity=identity,
        scopes=tuple(item.value for item in InternalMemoryScopeType),
    )


def internal_memory_tool_identity(state: Any) -> dict[str, Any]:
    section = build_internal_memory_map_contribution(state)
    return {
        "enabled": section is not None,
        "status": section.status.value if section is not None else "disabled",
        "identity_digest": section.identity_digest if section is not None else "",
    }


def internal_memory_repository_snapshot(
    state: Any,
) -> InternalMemoryRepositorySnapshot:
    config = getattr(state.runtime_preferences, "internal_memory", {})
    root = str(config.get("root", "") or "").strip()
    if not root:
        return InternalMemoryRepositorySnapshot(
            status=InternalMemoryRepositoryStatus.UNAVAILABLE,
            identity="",
            record_count=0,
            committed_count=0,
            diagnostic="missing_root",
        )
    try:
        return InternalMemoryRepository(Path(root)).snapshot()
    except (OSError, TypeError, ValueError) as exc:
        return InternalMemoryRepositorySnapshot(
            status=InternalMemoryRepositoryStatus.UNAVAILABLE,
            identity="",
            record_count=0,
            committed_count=0,
            diagnostic=type(exc).__name__,
        )


def _unavailable_section() -> MemoryMapSection:
    return MemoryMapSection.create(
        domain=MemoryDomain.INTERNAL_MEMORY,
        status=MemoryMapStatus.UNAVAILABLE,
        authority=MemoryAuthority.AGENT_OWNED,
        mutability=MemoryMutability.WRITABLE,
    )


def _remember_snapshot_identity(
    state: Any,
    snapshot: InternalMemoryRepositorySnapshot,
) -> None:
    memory_state = getattr(state, "memory_state", None)
    if memory_state is None:
        return
    memory_state.internal_memory_status = snapshot.status.value
    memory_state.internal_memory_repository_identity = snapshot.identity
    memory_state.internal_memory_diagnostic = str(snapshot.diagnostic or "")[:240]
    if not snapshot.identity:
        return
    history = [
        str(item)
        for item in getattr(
            memory_state,
            "internal_memory_repository_identity_history",
            [],
        )
        if str(item).strip()
    ]
    if not history or history[-1] != snapshot.identity:
        history.append(snapshot.identity)
    memory_state.internal_memory_repository_identity_history = history[-16:]
