"""Reduce live tool deliveries, never provider facts or opaque media."""

from __future__ import annotations

from dataclasses import replace

from ownpi.runtime.native_tools.history_items import HistoryEntry, ToolOutputItem
from ownpi.runtime.tools.result_projection import reduce_tool_result_projection


def reduce_tool_deliveries(entries: tuple[HistoryEntry, ...]) -> tuple[HistoryEntry, ...]:
    # Halving every reducible output shares a finite budget across parallel calls.
    return tuple(_reduce_entry(entry) for entry in entries)


def _reduce_entry(entry: HistoryEntry) -> HistoryEntry:
    item = entry.item
    if not isinstance(item, ToolOutputItem):
        return entry
    body = item.body
    if not isinstance(body, str) and entry.page is None:
        blocks = tuple(
            replace(block, text=_reduce_entry(replace(entry, item=replace(item, body=block.text))).item.body)
            if block.type == 'input_text' else block
            for block in body
        )
        return replace(entry, item=replace(item, body=blocks)) if blocks != body else entry
    text = body if isinstance(body, str) else next(
        (block.text for block in body if block.type == 'input_text'), '',
    )
    size = len(text.encode('utf-8'))
    budget = max(96, size // 2)
    reduced, page = reduce_tool_result_projection(
        tool_name=item.tool_name, text=text, byte_limit=budget, page=entry.page,
        source=entry.projection_source.to_object()
        if entry.page is not None and entry.projection_source is not None else None,
    )
    if len(reduced.encode('utf-8')) >= size:
        return entry
    if isinstance(body, str):
        new_body = reduced
    else:
        replaced = False
        blocks = []
        for block in body:
            if block.type == 'input_text' and not replaced:
                blocks.append(replace(block, text=reduced))
                replaced = True
            else:
                blocks.append(block)
        new_body = tuple(blocks)
    return replace(entry, item=replace(item, body=new_body), page=page)
