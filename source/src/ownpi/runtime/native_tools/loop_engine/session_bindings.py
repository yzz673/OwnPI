"""Operations supplied by an entry's adapter at existing algorithm boundaries."""

from dataclasses import dataclass
from typing import Any, Callable

from ownpi.runtime.native_tools.history_items import HistoryEntry
from ownpi.runtime.native_tools.provider import ProviderOutputEnvelope
from ownpi.runtime.rollout.session import RolloutHistory



@dataclass(frozen=True)
class NativeSessionBindings:
    prepare_context: Callable[[], None]
    effective_permissions: Callable[[], dict[str, Any]]
    provider_recorded: Callable[[ProviderOutputEnvelope], None]
    consume_compact_hook_deliveries: Callable[[RolloutHistory, tuple[HistoryEntry, ...]], None]
    record_tool_delivery: Callable[..., HistoryEntry | None]
    record_tool_context: Callable[..., None]
    before_compact: Callable[[int], None]
    after_compact: Callable[[int], None]
    confirm_visible_file_pages: Callable[[tuple[HistoryEntry, ...]], None] = lambda items: None


def history_session_bindings() -> NativeSessionBindings:
    from ownpi.runtime.native_tools.history_recording import record_tool_delivery

    return NativeSessionBindings(
        prepare_context=lambda: None,
        effective_permissions=lambda: {},
        provider_recorded=lambda response: None,
        consume_compact_hook_deliveries=lambda history, items: None,
        record_tool_delivery=record_tool_delivery,
        record_tool_context=lambda history, **kwargs: None,
        before_compact=lambda turn: None,
        after_compact=lambda turn: None,
    )
