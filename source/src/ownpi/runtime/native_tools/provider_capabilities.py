from __future__ import annotations

from dataclasses import dataclass

from ownpi.runtime.native_tools.spec import NativeToolSpec
from ownpi.runtime.native_tools.hosted_web_search import WebSearchExecutionMode


@dataclass(frozen=True)
class NativeToolProviderCapabilities:
    # Enables strict only for schemas supported by the shared function projection.
    native_tool_strict: bool = False
    native_tool_search_output: bool = False
    supports_parallel_tool_calls: bool = False
    web_search_execution_mode: WebSearchExecutionMode = WebSearchExecutionMode.LOCAL_FALLBACK
    hosted_web_search_spec_digest: str = ''


@dataclass(frozen=True)
class NativeToolSearchOutputV1:
    call_id: str
    receipt_ref: str
    tool_specs: tuple[NativeToolSpec, ...]
    status: str = 'completed'
    execution: str = 'client'
