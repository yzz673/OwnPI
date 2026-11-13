from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Protocol

from ownpi.runtime.native_tools.search import NativeToolSearchEntry
from ownpi.runtime.native_tools.spec import NativeToolResult, NativeToolSpec
from ownpi.runtime.native_tools.provider import NativeToolProviderCapabilities
from ownpi.runtime.native_tools.loop_engine.contracts import NativeLoopToolSurfaceSnapshot
from ownpi.runtime.native_tools.loop_engine.tool_surface import build_native_tool_surface_snapshot


@dataclass(frozen=True)
class NativeLoadedToolRefV1:
    canonical_name: str
    spec_digest: str
    search_receipt_ref: str


@dataclass(frozen=True)
class NativeLoadedToolSet:
    refs: tuple[NativeLoadedToolRefV1, ...] = ()

    @property
    def canonical_names(self) -> tuple[str, ...]:
        return tuple(ref.canonical_name for ref in self.refs)

    @property
    def spec_digests(self) -> tuple[str, ...]:
        return tuple(ref.spec_digest for ref in self.refs)

    @property
    def search_receipt_refs(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(ref.search_receipt_ref for ref in self.refs))

    def with_search_result(self, result: NativeToolResult) -> 'NativeLoadedToolSet':
        if result.tool_name != 'tool_search':
            return self
        receipt_ref = str(result.data.get('receipt_ref', '') or '').strip()
        matches = result.data.get('matches')
        if not receipt_ref or not isinstance(matches, list):
            return self
        by_identity = {
            (ref.canonical_name.casefold(), ref.spec_digest): ref
            for ref in self.refs
        }
        for match in matches:
            if not isinstance(match, Mapping):
                continue
            canonical_name = str(match.get('canonical_name', '') or '').strip()
            spec_digest = str(match.get('digest', '') or '').strip()
            if not canonical_name or not spec_digest:
                continue
            identity = (canonical_name.casefold(), spec_digest)
            by_identity[identity] = NativeLoadedToolRefV1(
                canonical_name=canonical_name,
                spec_digest=spec_digest,
                search_receipt_ref=receipt_ref,
            )
        return NativeLoadedToolSet(refs=tuple(by_identity.values()))

    def effective_specs(
        self,
        *,
        base_specs: Iterable[NativeToolSpec],
        search_entries: Iterable[NativeToolSearchEntry],
    ) -> tuple[NativeToolSpec, ...]:
        specs = list(base_specs)
        visible = {spec.name.casefold() for spec in specs}
        entries = _search_entries_by_identity(search_entries)
        for ref in self.refs:
            entry = entries.get((ref.canonical_name.casefold(), ref.spec_digest))
            if entry is None or entry.canonical_name.casefold() in visible:
                continue
            specs.append(entry.spec)
            visible.add(entry.canonical_name.casefold())
        return tuple(specs)

    def trace_projection(self) -> dict[str, object]:
        return {
            'schema_version': 1,
            'loaded_tool_names': list(self.canonical_names),
            'loaded_tool_digests': list(self.spec_digests),
            'search_receipt_refs': list(self.search_receipt_refs),
        }


@dataclass(frozen=True)
class NativeToolSearchReceiptV1:
    call_id: str
    receipt_ref: str
    matched_refs: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class NativeLoadedToolState:
    loaded_set: NativeLoadedToolSet = NativeLoadedToolSet()
    search_receipts: tuple[NativeToolSearchReceiptV1, ...] = ()

    @classmethod
    def from_history(cls, entries: Iterable[object]) -> 'NativeLoadedToolState':
        """Replay search receipts; current search entries still govern availability."""
        from ownpi.runtime.native_tools.history_items import HistoryEntry, ToolSearchOutputItem

        state = cls()
        for entry in entries:
            if not isinstance(entry, HistoryEntry):
                continue
            item = entry.item
            if not isinstance(item, ToolSearchOutputItem) or not item.success:
                continue
            matches = []
            for frozen in item.tools:
                definition = frozen.to_object()
                if isinstance(definition, Mapping):
                    matches.append({'canonical_name': definition.get('name'),
                                    'digest': definition.get('digest')})
            state = state.with_search_result(NativeToolResult(
                tool_name='tool_search', request_id=item.request_id,
                provider_call_id=item.call_id, status='completed', output_text='',
                data={'receipt_ref': item.receipt_ref, 'matches': matches},
            ))
        return state

    def with_search_result(self, result: NativeToolResult) -> 'NativeLoadedToolState':
        loaded_set = self.loaded_set.with_search_result(result)
        receipt = _search_receipt_from_result(result)
        if receipt is None:
            return NativeLoadedToolState(
                loaded_set=loaded_set,
                search_receipts=self.search_receipts,
            )
        active_receipt_refs = set(loaded_set.search_receipt_refs)
        by_receipt_ref = {
            item.receipt_ref: item
            for item in self.search_receipts
            if item.receipt_ref in active_receipt_refs
        }
        if (
            receipt.receipt_ref in active_receipt_refs
            and receipt.receipt_ref not in by_receipt_ref
        ):
            by_receipt_ref[receipt.receipt_ref] = receipt
        return NativeLoadedToolState(
            loaded_set=loaded_set,
            search_receipts=tuple(by_receipt_ref.values()),
        )

    def with_search_invocation(
        self,
        *,
        tool_result: object,
        provider_call_id: str,
        request_id: str,
    ) -> 'NativeLoadedToolState':
        """Consume runtime-owned search data without exposing it to provider history."""

        from ownpi.runtime.contracts import ToolResult

        if not isinstance(tool_result, ToolResult) or tool_result.tool_name != 'tool_search':
            return self
        return self.with_search_result(
            NativeToolResult(
                tool_name=tool_result.tool_name,
                request_id=request_id,
                provider_call_id=provider_call_id,
                status='completed',
                output_text=str(tool_result.stdout or ''),
                data=(dict(tool_result.data) if isinstance(tool_result.data, dict) else {}),
            )
        )

    def state_projection(self) -> dict[str, object]:
        return {
            'schema_version': 1,
            'loaded_tool_refs': [
                {
                    'canonical_name': ref.canonical_name,
                    'spec_digest': ref.spec_digest,
                    'search_receipt_ref': ref.search_receipt_ref,
                }
                for ref in self.loaded_set.refs
            ],
            'search_receipts': [
                {
                    'call_id': receipt.call_id,
                    'receipt_ref': receipt.receipt_ref,
                    'matched_refs': [list(ref) for ref in receipt.matched_refs],
                }
                for receipt in self.search_receipts
            ],
        }


class NativeLoadedToolSurface:
    def __init__(
        self, *, base_specs: tuple[NativeToolSpec, ...],
        search_entries: tuple[NativeToolSearchEntry, ...],
        provider_capabilities: NativeToolProviderCapabilities,
    ) -> None:
        self.base_specs = base_specs
        self.search_entries = search_entries
        self.provider_capabilities = provider_capabilities
        self.loaded_state = NativeLoadedToolState()
        self.executable_specs = base_specs

    def replay(self, entries: Iterable[object]) -> None:
        self.loaded_state = NativeLoadedToolState.from_history(entries)

    def record_search_invocation(
        self, *, tool_result: object, provider_call_id: str, request_id: str,
    ) -> bool:
        previous = self.loaded_state
        self.loaded_state = previous.with_search_invocation(
            tool_result=tool_result, provider_call_id=provider_call_id, request_id=request_id,
        )
        return self.loaded_state != previous

    def refresh(self, *, turn_index: int) -> NativeLoopToolSurfaceSnapshot:
        del turn_index
        executable_specs = self.loaded_state.loaded_set.effective_specs(
            base_specs=self.base_specs, search_entries=self.search_entries,
        )
        provider_specs = (
            self.base_specs if self.provider_capabilities.native_tool_search_output else executable_specs
        )
        self.executable_specs = executable_specs
        return build_native_tool_surface_snapshot(
            provider_specs=provider_specs, executable_specs=executable_specs,
        )


class NativeLoadedToolStateFields(Protocol):
    native_loaded_tool_refs: list[dict[str, str]]
    native_tool_search_receipts: list[dict[str, Any]]
    native_loaded_tool_scope_stage: str
    native_loaded_tool_scope_attempt: int
    native_loaded_tool_refs_verified: bool


def update_native_loaded_tool_state(
    tool_state: NativeLoadedToolStateFields,
    loaded_state: NativeLoadedToolState,
    *,
    stage: str,
    attempt: int,
) -> None:
    projection = loaded_state.state_projection()
    tool_state.native_loaded_tool_refs = [
        {
            'canonical_name': str(item['canonical_name']),
            'spec_digest': str(item['spec_digest']),
            'search_receipt_ref': str(item['search_receipt_ref']),
        }
        for item in projection['loaded_tool_refs']
        if isinstance(item, dict)
    ]
    tool_state.native_tool_search_receipts = [
        dict(item)
        for item in projection['search_receipts']
        if isinstance(item, dict)
    ]
    tool_state.native_loaded_tool_scope_stage = str(stage or '').strip()
    tool_state.native_loaded_tool_scope_attempt = max(0, int(attempt))
    tool_state.native_loaded_tool_refs_verified = True


def verified_native_loaded_tool_refs(
    tool_state: NativeLoadedToolStateFields,
    *,
    stage: str,
    attempt: int,
) -> tuple[Mapping[str, object], ...]:
    if not tool_state.native_loaded_tool_refs_verified:
        return ()
    if tool_state.native_loaded_tool_scope_stage != str(stage or '').strip():
        return ()
    if tool_state.native_loaded_tool_scope_attempt != max(0, int(attempt)):
        return ()
    return tuple(
        item
        for item in tool_state.native_loaded_tool_refs
        if isinstance(item, Mapping)
    )


def _search_entries_by_identity(
    search_entries: Iterable[NativeToolSearchEntry],
) -> dict[tuple[str, str], NativeToolSearchEntry]:
    return {
        (entry.canonical_name.casefold(), entry.digest): entry
        for entry in search_entries
    }


def _search_receipt_from_result(
    result: NativeToolResult,
) -> NativeToolSearchReceiptV1 | None:
    if result.tool_name != 'tool_search':
        return None
    receipt_ref = str(result.data.get('receipt_ref', '') or '').strip()
    matches = result.data.get('matches')
    call_id = str(result.provider_call_id or result.request_id or '').strip()
    if not receipt_ref or not call_id or not isinstance(matches, list):
        return None
    refs = tuple(
        (
            str(match.get('canonical_name', '') or '').strip(),
            str(match.get('digest', '') or '').strip(),
        )
        for match in matches
        if isinstance(match, Mapping)
        and str(match.get('canonical_name', '') or '').strip()
        and str(match.get('digest', '') or '').strip()
    )
    return NativeToolSearchReceiptV1(
        call_id=call_id,
        receipt_ref=receipt_ref,
        matched_refs=refs,
    )


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in '0123456789abcdef' for char in value)
