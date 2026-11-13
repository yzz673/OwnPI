from __future__ import annotations

from pathlib import Path

from .contracts import InternalMemoryScopeType
from .queries import InternalMemoryScopeFilter


def build_runtime_scope_filters(
    *,
    project_root: Path,
    workflow_id: str,
) -> tuple[InternalMemoryScopeFilter, ...]:
    resolved_root = project_root.resolve(strict=False)
    root_id = resolved_root.name.strip()
    filters = [InternalMemoryScopeFilter(InternalMemoryScopeType.USER_GLOBAL)]
    for scope_type, scope_id in (
        (InternalMemoryScopeType.PROJECT, root_id),
        (InternalMemoryScopeType.REPOSITORY, root_id),
        (InternalMemoryScopeType.WORKFLOW, workflow_id.strip()),
    ):
        if scope_id:
            filters.append(InternalMemoryScopeFilter(scope_type, scope_id))
    return tuple(filters)
