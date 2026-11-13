from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from threading import Lock
from typing import Iterator
from uuid import uuid4


class ProviderRequestContextError(RuntimeError):
    """Raised when a runtime-scoped Provider request has no session identity."""


@dataclass(frozen=True)
class ProviderRequestContext:
    session_id: str
    task_id: str = ""
    run_id: str = ""

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise ValueError("Provider request context requires a non-empty session_id")


@dataclass
class ProviderTurnSession:
    """Volatile Provider routing state owned by one logical model turn."""

    turn_scope_id: str = field(default_factory=lambda: f"provider-turn-{uuid4().hex}")
    _turn_state: str | None = field(default=None, init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.turn_scope_id.strip():
            raise ValueError("Provider turn session requires a non-empty turn_scope_id")

    @property
    def turn_state(self) -> str | None:
        with self._lock:
            return self._turn_state

    def capture_turn_state(self, value: str | None) -> bool:
        normalized = str(value or "").strip()
        if not normalized:
            return False
        with self._lock:
            if self._turn_state is not None:
                return False
            self._turn_state = normalized
            return True


_PROVIDER_REQUEST_CONTEXT: ContextVar[ProviderRequestContext | None] = ContextVar(
    "provider_request_context",
    default=None,
)
_PROVIDER_REQUEST_CONTEXT_REQUIRED: ContextVar[bool] = ContextVar(
    "provider_request_context_required",
    default=False,
)
_PROVIDER_TURN_SESSION: ContextVar[ProviderTurnSession | None] = ContextVar(
    "provider_turn_session",
    default=None,
)


def current_provider_request_context() -> ProviderRequestContext | None:
    context = _PROVIDER_REQUEST_CONTEXT.get()
    if context is None and _PROVIDER_REQUEST_CONTEXT_REQUIRED.get():
        raise ProviderRequestContextError(
            "runtime-scoped OpenAI Responses request requires a bound session identity"
        )
    return context


def current_provider_turn_session() -> ProviderTurnSession | None:
    return _PROVIDER_TURN_SESSION.get()


def provider_turn_session_for_request() -> ProviderTurnSession:
    return current_provider_turn_session() or ProviderTurnSession()


@contextmanager
def bind_provider_request_context(context: ProviderRequestContext) -> Iterator[None]:
    context_token: Token[ProviderRequestContext | None] = _PROVIDER_REQUEST_CONTEXT.set(context)
    required_token: Token[bool] = _PROVIDER_REQUEST_CONTEXT_REQUIRED.set(True)
    try:
        yield
    finally:
        _PROVIDER_REQUEST_CONTEXT.reset(context_token)
        _PROVIDER_REQUEST_CONTEXT_REQUIRED.reset(required_token)


@contextmanager
def bind_provider_turn_session(session: ProviderTurnSession) -> Iterator[None]:
    token: Token[ProviderTurnSession | None] = _PROVIDER_TURN_SESSION.set(session)
    try:
        yield
    finally:
        _PROVIDER_TURN_SESSION.reset(token)


@contextmanager
def require_provider_request_context() -> Iterator[None]:
    token: Token[bool] = _PROVIDER_REQUEST_CONTEXT_REQUIRED.set(True)
    try:
        yield
    finally:
        _PROVIDER_REQUEST_CONTEXT_REQUIRED.reset(token)
