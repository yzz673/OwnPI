from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CriticLoopPolicy:
    critic_kind: str
    target_kind: str
    clean_transition: str

    def __post_init__(self) -> None:
        if self.critic_kind != 'reviewer':
            raise ValueError(f'invalid critic kind: {self.critic_kind}')
        if self.target_kind not in {'core_trajectory'}:
            raise ValueError(f'invalid critic target kind: {self.target_kind}')
        if self.clean_transition != 'critic_complete':
            raise ValueError(f'invalid clean transition: {self.clean_transition}')


def reviewer_policy() -> CriticLoopPolicy:
    return CriticLoopPolicy(
        critic_kind='reviewer',
        target_kind='core_trajectory',
        clean_transition='critic_complete',
    )
