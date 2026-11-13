"""Typed model history, independent of wire requests and durable projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ownpi.runtime.model_content import ModelContentItem
from ownpi.runtime.native_tools.provider import ProviderOutputItem
from ownpi.runtime.native_tools.provider_json import FrozenProviderJson
from ownpi.runtime.tools.result_projection_utils import FilePageProjection


@dataclass(frozen=True)
class ToolOutputItem:
    tool_name: str
    call_id: str
    request_id: str
    body: str | tuple[ModelContentItem, ...]
    success: bool | None
    kind: Literal['function_call_output'] = 'function_call_output'


@dataclass(frozen=True)
class ToolSearchOutputItem:
    """Search definitions and original call protocol, without request projection."""

    tool_name: str
    call_id: str
    request_id: str
    tools: tuple[FrozenProviderJson, ...]
    success: bool
    call_type: str
    receipt_ref: str = ''
    body: str = ''
    status: str = 'completed'
    execution: str = 'client'

    @property
    def kind(self) -> Literal['function_call_output', 'tool_search_output']:
        return 'tool_search_output' if self.call_type == 'tool_search_call' else 'function_call_output'


@dataclass(frozen=True)
class HistoryMessage:
    role: str
    text: str
    metadata: FrozenProviderJson = FrozenProviderJson()


@dataclass(frozen=True)
class HistoryEntry:
    item: ProviderOutputItem | ToolOutputItem | ToolSearchOutputItem | HistoryMessage
    exchange_id: str = ''
    output_sequence_index: int | None = None
    preserve_output: bool = False
    page: FilePageProjection | None = None
    turn_id: str = ''
    projection_source: FrozenProviderJson | None = None
