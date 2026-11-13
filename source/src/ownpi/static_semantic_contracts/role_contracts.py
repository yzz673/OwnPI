"""Canonical level-specific role contract resolution."""

from __future__ import annotations

from ownpi.schemas.workflow_plan import StageRuntimeProfile
from ownpi.static_semantic_contracts.store import (
    StaticSemanticContract,
    get_static_semantic_contract,
)
from ownpi.static_semantic_contracts.taxonomy import role_contract_id


def resolve_role_contract(
    role: str,
    level: StageRuntimeProfile,
) -> StaticSemanticContract:
    if not isinstance(level, StageRuntimeProfile):
        raise TypeError("level must be a StageRuntimeProfile")
    return get_static_semantic_contract(role_contract_id(role, level.value))


__all__ = ["resolve_role_contract"]
