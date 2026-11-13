from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Callable, Iterator


LLMTraceSink = Callable[[dict[str, Any]], None]

_TRACE_SCOPE_STACK: ContextVar[tuple[dict[str, Any], ...]] = ContextVar('llm_trace_scope_stack', default=())
_TRACE_SINK: ContextVar[LLMTraceSink | None] = ContextVar('llm_trace_sink', default=None)


def current_llm_trace_scope() -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for item in _TRACE_SCOPE_STACK.get():
        merged.update(item)
    return merged


@contextmanager
def llm_trace_scope(**metadata: Any) -> Iterator[None]:
    cleaned = {key: value for key, value in metadata.items() if value is not None}
    stack = _TRACE_SCOPE_STACK.get()
    token = _TRACE_SCOPE_STACK.set((*stack, cleaned))
    try:
        yield
    finally:
        _TRACE_SCOPE_STACK.reset(token)


@contextmanager
def llm_trace_session(*, sink: LLMTraceSink | None) -> Iterator[None]:
    token = _TRACE_SINK.set(sink)
    try:
        yield
    finally:
        _TRACE_SINK.reset(token)


def emit_llm_trace(event: dict[str, Any]) -> None:
    from ownpi.diagnostics import record_diagnostic

    payload = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        **current_llm_trace_scope(),
        **event,
    }
    record_diagnostic(payload)
    sink = _TRACE_SINK.get()
    if sink is None:
        return
    sink(payload)


def summarize_prompt(prompt: str, *, limit: int = 120) -> str:
    for line in str(prompt).splitlines():
        normalized = line.strip()
        if normalized:
            return normalized[:limit]
    normalized = ' '.join(str(prompt).split())
    return normalized[:limit]
