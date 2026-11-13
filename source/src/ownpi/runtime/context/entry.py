from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ownpi.runtime.hooks.types import HookModelContextFragment


RuntimeEntryOrigin = Literal['live', 'resume']


@dataclass(frozen=True)
class RuntimeEntryContext:
    origin: RuntimeEntryOrigin = 'live'
    source_resume_id: str = ''
    continuation_kind: str = ''
    hook_prefix_fragments: tuple[HookModelContextFragment, ...] = ()
    session_start_hook_emitted: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.hook_prefix_fragments, tuple):
            raise TypeError('Hook prefix fragments must be an immutable tuple')
        if any(
            not isinstance(item, HookModelContextFragment)
            for item in self.hook_prefix_fragments
        ):
            raise TypeError('Runtime entry Hook prefix contains an invalid fragment')
        if not isinstance(self.session_start_hook_emitted, bool):
            raise TypeError('SessionStart Hook lifecycle marker must be a boolean')

    @property
    def reconstruction_source(self) -> str:
        return {
            'resume': 'resume_restore',
        }.get(self.origin, 'live_runtime')

    @property
    def is_resume(self) -> bool:
        return self.origin == 'resume'
