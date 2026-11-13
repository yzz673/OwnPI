"""Model-visible runtime facts recorded in the ordinary conversation history."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ownpi.runtime.hooks.context_delivery import select_active_hook_fragments
from ownpi.runtime.hooks.types import HookDeliveryTarget
from ownpi.runtime.native_tools.conversation_items import canonical_content_hash
from ownpi.runtime.native_tools.history_items import HistoryEntry, HistoryMessage
from ownpi.runtime.native_tools.provider_json import FrozenProviderJson

if TYPE_CHECKING:
    from ownpi.runtime.rollout.session import RolloutHistory
    from ownpi.runtime.tools.runtime import ToolRuntimeContext


PERMISSION_CONTEXT_KIND = 'permissions'
COMPACT_HOOK_EVENTS = frozenset({'PreCompact', 'PostCompact'})


def record_permission_context(history: RolloutHistory, context: ToolRuntimeContext) -> None:
    permission = context.canonical_permissions
    visible = {
        'sandbox_enforcement': permission.profile.enforcement.value,
        'file_system_policy': permission.profile.file_system.kind.value,
        'network_policy': permission.profile.network.value,
        'approval_policy': permission.approval_policy.value,
        'workspace_roots': [str(root) for root in permission.workspace_roots],
    }
    digest = canonical_content_hash(visible)
    previous = next((entry for entry in reversed(history.snapshot)
                     if isinstance(entry.item, HistoryMessage)
                     and entry.item.metadata.to_object().get('context_kind') == PERMISSION_CONTEXT_KIND), None)
    if previous is not None and previous.item.metadata.to_object().get('context_digest') == digest:
        return
    history.record_items((HistoryEntry(HistoryMessage(
        'developer', '[Current permissions]\n' + json.dumps(visible, ensure_ascii=False, sort_keys=True),
        FrozenProviderJson.from_value({'context_kind': PERMISSION_CONTEXT_KIND, 'context_digest': digest}),
    )),))


def record_compact_hook_context(history: RolloutHistory, context: ToolRuntimeContext) -> None:
    fragments = select_active_hook_fragments(
        context.state_store.state.context_state, target=HookDeliveryTarget.CONVERSATION,
        run_id=context.run_id, loop_kind=context.actor_kind, stage=context.stage.value,
        attempt=context.attempt,
    )
    existing = {entry.item.metadata.to_object().get('delivery_id')
                for entry in history.snapshot if isinstance(entry.item, HistoryMessage)}
    for fragment in fragments:
        if fragment.event_name not in COMPACT_HOOK_EVENTS or fragment.delivery_id in existing:
            continue
        metadata = fragment.to_dict()
        metadata.pop('text')
        history.record_items((HistoryEntry(HistoryMessage(
            'developer', fragment.text, FrozenProviderJson.from_value(metadata),
        )),))
        existing.add(fragment.delivery_id)


def compact_hook_entries_in_request(history: RolloutHistory, items: tuple[HistoryEntry, ...]) -> tuple[HistoryEntry, ...]:
    submitted = {entry.item.metadata.to_object().get('delivery_id')
                 for entry in items if isinstance(entry.item, HistoryMessage)
                 and entry.item.metadata.to_object().get('event_name') in COMPACT_HOOK_EVENTS}
    return tuple(entry for entry in history.snapshot if isinstance(entry.item, HistoryMessage)
                 and entry.item.metadata.to_object().get('delivery_id') in submitted
                 and entry.item.metadata.to_object().get('event_name') in COMPACT_HOOK_EVENTS)
