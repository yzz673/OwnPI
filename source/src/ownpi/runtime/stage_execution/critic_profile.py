from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ownpi.runtime.tools.definitions import ToolDefinition
from ownpi.runtime.tools.role_policy import tool_role_allowed


CriticKind = Literal['reviewer']

@dataclass(frozen=True)
class StageCriticRoleProfile:
    critic_kind: CriticKind
    actor_kind: CriticKind
    terminal_tool_name: str = 'submit_feedback'

    def __post_init__(self) -> None:
        if self.critic_kind != 'reviewer':
            raise ValueError('Reviewer is the only stage critic')
        if self.critic_kind != self.actor_kind:
            raise ValueError('critic kind and actor kind must match')

    def filter_definitions(
        self,
        definitions: tuple[ToolDefinition, ...],
    ) -> tuple[ToolDefinition, ...]:
        selected = tuple(
            definition
            for definition in definitions
            if tool_role_allowed(definition.name, self.actor_kind)
        )
        terminal_count = sum(
            definition.name == self.terminal_tool_name for definition in selected
        )
        if terminal_count != 1:
            raise ValueError('critic tool surface requires exactly one feedback terminal tool')
        return selected


def reviewer_role_profile() -> StageCriticRoleProfile:
    return StageCriticRoleProfile(critic_kind='reviewer', actor_kind='reviewer')
