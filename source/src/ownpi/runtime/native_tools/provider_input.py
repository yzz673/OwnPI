"""A request view of live history, with external instructions kept separate."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from typing import Any

from ownpi.runtime.native_tools.conversation_items import canonical_content_hash
from ownpi.runtime.native_tools.history_items import HistoryEntry
from ownpi.runtime.native_tools.provider_json import FrozenProviderJson


@dataclass(frozen=True)
class NativeHistoryProviderInput:
    immutable_prefix: tuple[FrozenProviderJson, ...] = ()
    items: tuple[HistoryEntry, ...] = ()
    history_generation: int = 0

    def for_modalities(self, modalities: tuple[str, ...]) -> NativeHistoryProviderInput:
        from ownpi.runtime.native_tools.history_prompt import for_prompt

        return replace(self, items=for_prompt(self.items, modalities))

    @property
    def immutable_prefix_messages(self) -> list[dict[str, Any]]:
        return [item.to_object() for item in self.immutable_prefix]

    @property
    def immutable_prefix_digest(self) -> str:
        return canonical_content_hash(self.immutable_prefix_messages)
