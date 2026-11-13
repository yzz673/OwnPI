from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Callable

from ownpi.runtime.hooks.declarations import CONTROL_EVENTS, validate_event_name
from ownpi.runtime.hooks.types import (
    HookEventName,
    HookExecutionMode,
    HookHandlerType,
    HookRequest,
    HookTrustStatus,
)
from ownpi.runtime.hooks.trust import HookTrustStore
from ownpi.schemas.runtime import HookHandlerConfig, HookRegistryConfig


ManagedHookCallback = Callable[[dict[str, Any]], dict[str, Any] | None]


@dataclass(frozen=True)
class HookRegistration:
    event_name: HookEventName
    handler_key: str
    name: str
    handler_type: HookHandlerType
    execution_mode: HookExecutionMode
    matcher: str
    source: str
    trust_status: HookTrustStatus
    content_hash: str
    config: HookHandlerConfig | None = None
    callback: ManagedHookCallback | None = None


class HookRegistry:
    def __init__(self, registrations: tuple[HookRegistration, ...] = ()) -> None:
        self._registrations = registrations

    @classmethod
    def from_config(
        cls,
        config: HookRegistryConfig | None,
        *,
        callbacks: tuple[HookRegistration, ...] = (),
        trust_store: HookTrustStore | None = None,
    ) -> "HookRegistry":
        registrations: list[HookRegistration] = list(callbacks)
        if config is not None and config.enabled:
            for event_name, handlers in config.events.items():
                typed_event = validate_event_name(event_name)
                for index, handler in enumerate(handlers, start=1):
                    registrations.append(
                        _registration_from_config(
                            typed_event,
                            handler,
                            index=index,
                            trust_store=trust_store,
                        )
                    )
        return cls(tuple(registrations))

    @property
    def enabled(self) -> bool:
        return bool(self._registrations)

    @property
    def snapshot(self) -> tuple[HookRegistration, ...]:
        return self._registrations

    def has_event(self, event_name: HookEventName) -> bool:
        return any(item.event_name == event_name for item in self._registrations)

    def matching(self, request: HookRequest) -> tuple[HookRegistration, ...]:
        return tuple(
            item
            for item in self._registrations
            if item.event_name == request.event_name
            and item.trust_status in {"managed", "trusted"}
            and _matches(item.matcher, request)
        )


def managed_callback_registration(
    event_name: HookEventName,
    *,
    name: str,
    callback: ManagedHookCallback,
    matcher: str = "*",
    source: str = "builtin",
) -> HookRegistration:
    identity = {
        "event": event_name,
        "name": name,
        "matcher": matcher,
        "source": source,
        "type": "callback",
    }
    content_hash = _stable_hash(identity)
    return HookRegistration(
        event_name=event_name,
        handler_key=f"{source}:{event_name}:{name}:{content_hash[:12]}",
        name=name,
        handler_type="callback",
        execution_mode="sync",
        matcher=matcher,
        source=source,
        trust_status="managed",
        content_hash=content_hash,
        callback=callback,
    )


def _registration_from_config(
    event_name: HookEventName,
    handler: HookHandlerConfig,
    *,
    index: int,
    trust_store: HookTrustStore | None,
) -> HookRegistration:
    if handler.execution_mode == "async" and event_name in CONTROL_EVENTS:
        raise ValueError(f"{event_name} control Hook cannot use async execution")
    identity = {"event": event_name, **handler.to_dict()}
    content_hash = _stable_hash(identity)
    source = handler.source or "settings"
    name = handler.name or f"{event_name}:{index}"
    trust_key = f"{source}:{event_name}:{name}"
    trust_status = (
        trust_store.evaluate(
            trust_key=trust_key,
            content_hash=content_hash,
            source=source,
        )
        if trust_store is not None
        else _default_trust_status(source)
    )
    return HookRegistration(
        event_name=event_name,
        handler_key=f"{source}:{event_name}:{name}:{content_hash[:12]}",
        name=name,
        handler_type=handler.handler_type,  # type: ignore[arg-type]
        execution_mode=handler.execution_mode,  # type: ignore[arg-type]
        matcher=handler.matcher,
        source=source,
        trust_status=trust_status,
        content_hash=content_hash,
        config=handler,
    )


def _stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _default_trust_status(source: str) -> HookTrustStatus:
    if source in {"settings", "managed", "app_home"} or source.startswith("plugin:app_home:"):
        return "managed"
    return "trusted"


def _matches(matcher: str, request: HookRequest) -> bool:
    matcher = matcher.strip()
    if not matcher or matcher == "*":
        return True
    targets = _matcher_targets(request)
    if matcher.startswith("regex:"):
        pattern = matcher.removeprefix("regex:")
        return any(re.search(pattern, target) is not None for target in targets)
    if _simple_pipe_matcher(matcher):
        choices = {item.strip() for item in matcher.split("|") if item.strip()}
        return any(target in choices for target in targets)
    try:
        return any(re.search(matcher, target) is not None for target in targets)
    except re.error:
        return matcher in targets


def _matcher_targets(request: HookRequest) -> tuple[str, ...]:
    payload = request.payload
    values = [
        str(payload.get("tool_name") or ""),
        str(payload.get("claude_tool_name") or ""),
        request.event_name,
    ]
    aliases = payload.get("tool_name_aliases")
    if isinstance(aliases, (list, tuple)):
        values.extend(str(item) for item in aliases)
    return tuple(dict.fromkeys(value for value in values if value))


def _simple_pipe_matcher(matcher: str) -> bool:
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_:-.")
    return all(character in allowed or character == "|" for character in matcher)
