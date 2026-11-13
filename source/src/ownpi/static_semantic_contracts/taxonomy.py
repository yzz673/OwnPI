"""Canonical ownership taxonomy for core runtime static semantics."""

from __future__ import annotations


CORE_SEMANTIC_FAMILIES: tuple[str, ...] = ("common", "roles", "stages")
CORE_SEMANTIC_LEVELS: tuple[str, ...] = ("task_level", "research_level")
CORE_SEMANTIC_ROLES: tuple[str, ...] = ("core", "reviewer", "subagent")


def role_contract_id(role: str, level: str) -> str:
    """Return the canonical ID for one level-specific primary role contract."""
    normalized_role = str(role or "").strip()
    normalized_level = str(level or "").strip()
    if normalized_role not in CORE_SEMANTIC_ROLES:
        raise ValueError(f"Unsupported core semantic role: {normalized_role}")
    if normalized_level not in CORE_SEMANTIC_LEVELS:
        raise ValueError(f"Unsupported core semantic level: {normalized_level}")
    return f"role.{normalized_level}.{normalized_role}"


def stage_contract_id(stage: str, level: str) -> str:
    """Return the canonical ID for one level-specific stage contract."""
    normalized_stage = str(stage or "").strip()
    normalized_level = str(level or "").strip()
    if not normalized_stage:
        raise ValueError("stage must not be empty")
    if normalized_level not in CORE_SEMANTIC_LEVELS:
        raise ValueError(f"Unsupported core semantic level: {normalized_level}")
    return f"stage.{normalized_level}.{normalized_stage}"


__all__ = [
    "CORE_SEMANTIC_FAMILIES",
    "CORE_SEMANTIC_LEVELS",
    "CORE_SEMANTIC_ROLES",
    "role_contract_id",
    "stage_contract_id",
]
