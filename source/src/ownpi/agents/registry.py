from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ownpi.agents.base import RegisteredAgent
from ownpi.app.paths import AppPaths
from ownpi.llm.base import LLMClient
from ownpi.schemas.runtime import AgentInstanceConfig

AgentBuilder = Callable[[AgentInstanceConfig, LLMClient, Path, AppPaths], RegisteredAgent]

_AGENT_REGISTRY: dict[str, AgentBuilder] = {}


def register_agent_role(role: str, builder: AgentBuilder) -> None:
    key = role.strip().lower()
    if not key:
        raise ValueError("Agent role must be non-empty.")
    _AGENT_REGISTRY[key] = builder


def build_agent(
    agent_config: AgentInstanceConfig,
    llm_client: LLMClient,
    project_root: Path,
    app_paths: AppPaths,
) -> RegisteredAgent:
    role = agent_config.role.strip().lower()
    try:
        builder = _AGENT_REGISTRY[role]
    except KeyError as exc:
        available = ", ".join(sorted(_AGENT_REGISTRY)) or "<none>"
        raise ValueError(
            f"Unsupported agent role: {agent_config.role!r}. Registered roles: {available}."
        ) from exc
    return builder(agent_config, llm_client, project_root, app_paths)


def list_agent_roles() -> list[str]:
    return sorted(_AGENT_REGISTRY)
