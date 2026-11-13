from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Mapping, TypeAlias
from uuid import uuid4

from ownpi.runtime.events import now_utc_iso


HookEventName = Literal[
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PermissionRequest",
    "PreCompact",
    "PostCompact",
    "Stop",
    "SessionEnd",
    "Elicitation",
]
HookHandlerType = Literal["command", "mcp_tool", "callback"]
HookExecutionMode = Literal["sync", "async"]
HookTrustStatus = Literal["managed", "untrusted", "trusted", "modified"]
HOOK_FRAGMENT_REF_VERSION = 1


class HookOutputField(str, Enum):
    ADDITIONAL_CONTEXT = "additionalContext"
    SYSTEM_MESSAGE = "systemMessage"
    DECISION = "decision"
    CONTINUE = "continue"
    REASON = "reason"
    UPDATED_INPUT = "updatedInput"
    UPDATED_MCP_TOOL_OUTPUT = "updatedMCPToolOutput"
    ACTION = "action"
    CONTENT = "content"


class HookDeliveryTarget(str, Enum):
    IMMUTABLE_PREFIX = "immutable_prefix"
    CONVERSATION = "conversation"
    RUNTIME_CONTROL = "runtime_control"
    RUNTIME_MUTATION = "runtime_mutation"
    UI = "ui"
    OBSERVABILITY = "observability"


@dataclass(frozen=True)
class HookDeliveryScope:
    session_id: str = ""
    run_id: str = ""
    loop_kind: str = ""
    stage: str = ""
    attempt: int | None = None
    turn_id: str = ""
    tool_call_id: str = ""
    valid_from: str = ""
    one_shot: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "run_id": self.run_id,
            "loop_kind": self.loop_kind,
            "stage": self.stage,
            "attempt": self.attempt,
            "turn_id": self.turn_id,
            "tool_call_id": self.tool_call_id,
            "valid_from": self.valid_from,
            "one_shot": self.one_shot,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "HookDeliveryScope":
        attempt = payload.get("attempt")
        return cls(
            session_id=str(payload.get("session_id", "") or ""),
            run_id=str(payload.get("run_id", "") or ""),
            loop_kind=str(payload.get("loop_kind", "") or ""),
            stage=str(payload.get("stage", "") or ""),
            attempt=int(attempt) if attempt is not None else None,
            turn_id=str(payload.get("turn_id", "") or ""),
            tool_call_id=str(payload.get("tool_call_id", "") or ""),
            valid_from=str(payload.get("valid_from", "") or ""),
            one_shot=bool(payload.get("one_shot", False)),
        )


@dataclass(frozen=True)
class HookOutputAtom:
    field_kind: HookOutputField
    target: HookDeliveryTarget
    event_id: str
    event_name: HookEventName
    handler_key: str
    handler_name: str
    source: str
    trust_status: HookTrustStatus
    execution_mode: HookExecutionMode
    value: Any


@dataclass(frozen=True)
class HookFragmentSourceRefV1:
    """Reader-ready reference for a spilled Hook context fragment."""

    schema_version: int
    logical_ref: str
    content_digest: str
    content_bytes: int
    reader_route: str = "file_reader"

    def __post_init__(self) -> None:
        if self.schema_version != HOOK_FRAGMENT_REF_VERSION:
            raise ValueError("unsupported Hook fragment ref version")
        if not self.logical_ref.strip() or len(self.content_digest) != 64:
            raise ValueError("Hook fragment ref requires a path and digest")
        if self.content_bytes < 0:
            raise ValueError("Hook fragment ref byte count must not be negative")
        if self.reader_route != "file_reader":
            raise ValueError("Hook fragment refs must use file_reader")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "logical_ref": self.logical_ref,
            "source_kind": "hook_fragment",
            "content_digest": self.content_digest,
            "content_bytes": self.content_bytes,
            "reader_route": self.reader_route,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "HookFragmentSourceRefV1":
        return cls(
            schema_version=int(payload.get("schema_version", 0) or 0),
            logical_ref=str(payload.get("logical_ref", "") or ""),
            content_digest=str(payload.get("content_digest", "") or ""),
            content_bytes=int(payload.get("content_bytes", 0) or 0),
            reader_route=str(payload.get("reader_route", "") or ""),
        )


@dataclass(frozen=True)
class HookModelContextFragment:
    delivery_id: str
    target: HookDeliveryTarget
    text: str
    event_id: str
    event_name: HookEventName
    handler_key: str
    handler_name: str
    source: str
    trust_status: HookTrustStatus
    execution_mode: HookExecutionMode
    content_digest: str
    scope: HookDeliveryScope
    locator: str = ""
    source_ref: HookFragmentSourceRefV1 | None = None
    reader_route: str = ""
    content_truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "delivery_id": self.delivery_id,
            "target": self.target.value,
            "text": self.text,
            "event_id": self.event_id,
            "event_name": self.event_name,
            "handler_key": self.handler_key,
            "handler_name": self.handler_name,
            "source": self.source,
            "trust_status": self.trust_status,
            "execution_mode": self.execution_mode,
            "content_digest": self.content_digest,
            "scope": self.scope.to_dict(),
            "locator": self.locator,
            "source_ref": self.source_ref.to_dict() if self.source_ref is not None else None,
            "reader_route": self.reader_route,
            "content_truncated": self.content_truncated,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "HookModelContextFragment":
        return cls(
            delivery_id=str(payload["delivery_id"]),
            target=HookDeliveryTarget(str(payload["target"])),
            text=str(payload.get("text", "") or ""),
            event_id=str(payload.get("event_id", "") or ""),
            event_name=str(payload["event_name"]),  # type: ignore[arg-type]
            handler_key=str(payload.get("handler_key", "") or ""),
            handler_name=str(payload.get("handler_name", "") or ""),
            source=str(payload.get("source", "") or ""),
            trust_status=str(payload.get("trust_status", "untrusted")),  # type: ignore[arg-type]
            execution_mode=str(payload.get("execution_mode", "sync")),  # type: ignore[arg-type]
            content_digest=str(payload.get("content_digest", "") or ""),
            scope=HookDeliveryScope.from_dict(dict(payload.get("scope", {}))),
            locator=str(payload.get("locator", "") or ""),
            source_ref=(
                HookFragmentSourceRefV1.from_dict(dict(payload["source_ref"]))
                if isinstance(payload.get("source_ref"), dict)
                else None
            ),
            reader_route=str(payload.get("reader_route", "") or ""),
            content_truncated=bool(payload.get("content_truncated", False)),
        )


@dataclass(frozen=True)
class HookUIEffect:
    event_id: str
    event_name: HookEventName
    handler_key: str
    source: str
    message: str


@dataclass(frozen=True)
class HookDeliveryBatch:
    atoms: tuple[HookOutputAtom, ...] = ()
    model_context_fragments: tuple[HookModelContextFragment, ...] = ()
    ui_effects: tuple[HookUIEffect, ...] = ()
    receipt_ids: tuple[str, ...] = ()

    def fragments_for(
        self,
        target: HookDeliveryTarget,
    ) -> tuple[HookModelContextFragment, ...]:
        return tuple(
            fragment
            for fragment in self.model_context_fragments
            if fragment.target is target
        )

    @property
    def p_fragments(self) -> tuple[HookModelContextFragment, ...]:
        return self.fragments_for(HookDeliveryTarget.IMMUTABLE_PREFIX)

    @property
    def conversation_fragments(self) -> tuple[HookModelContextFragment, ...]:
        return self.fragments_for(HookDeliveryTarget.CONVERSATION)

    @property
    def system_messages(self) -> tuple[str, ...]:
        return tuple(effect.message for effect in self.ui_effects if effect.message)


@dataclass(frozen=True)
class HookRequest:
    event_name: HookEventName
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: f"hook-event:{uuid4().hex}")
    created_at: str = field(default_factory=now_utc_iso)


@dataclass(frozen=True)
class SessionStartRequest(HookRequest):
    event_name: HookEventName = "SessionStart"


@dataclass(frozen=True)
class UserPromptSubmitRequest(HookRequest):
    event_name: HookEventName = "UserPromptSubmit"


@dataclass(frozen=True)
class PreToolUseRequest(HookRequest):
    event_name: HookEventName = "PreToolUse"


@dataclass(frozen=True)
class PostToolUseRequest(HookRequest):
    event_name: HookEventName = "PostToolUse"


@dataclass(frozen=True)
class PermissionRequestHookRequest(HookRequest):
    event_name: HookEventName = "PermissionRequest"


@dataclass(frozen=True)
class PreCompactRequest(HookRequest):
    event_name: HookEventName = "PreCompact"


@dataclass(frozen=True)
class PostCompactRequest(HookRequest):
    event_name: HookEventName = "PostCompact"


@dataclass(frozen=True)
class StopRequest(HookRequest):
    event_name: HookEventName = "Stop"


@dataclass(frozen=True)
class SessionEndRequest(HookRequest):
    event_name: HookEventName = "SessionEnd"


@dataclass(frozen=True)
class ElicitationRequest(HookRequest):
    event_name: HookEventName = "Elicitation"


@dataclass(frozen=True)
class HookRunReceipt:
    receipt_id: str
    event_id: str
    event_name: HookEventName
    handler_key: str
    handler_name: str
    handler_type: HookHandlerType
    execution_mode: HookExecutionMode
    source: str
    trust_status: HookTrustStatus
    started_at: str
    finished_at: str
    duration_ms: int
    status: Literal["completed", "failed", "blocked", "scheduled"]
    exit_code: int | None = None
    timed_out: bool = False
    stdout: str = ""
    stderr: str = ""
    error: str = ""
    output: dict[str, Any] = field(default_factory=dict)
    schema_error: str = ""
    action_hash: str = ""
    effect_hash: str = ""
    policy_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "event_id": self.event_id,
            "event_name": self.event_name,
            "handler_key": self.handler_key,
            "handler_name": self.handler_name,
            "handler_type": self.handler_type,
            "execution_mode": self.execution_mode,
            "source": self.source,
            "trust_status": self.trust_status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error": self.error,
            "output": dict(self.output),
            "schema_error": self.schema_error,
            "action_hash": self.action_hash,
            "effect_hash": self.effect_hash,
            "policy_hash": self.policy_hash,
        }


@dataclass(frozen=True)
class HookOutcome:
    event_name: HookEventName
    receipts: tuple[HookRunReceipt, ...] = ()
    delivery: HookDeliveryBatch = field(default_factory=HookDeliveryBatch)


@dataclass(frozen=True)
class SessionOutcome(HookOutcome):
    pass


@dataclass(frozen=True)
class UserPromptOutcome(HookOutcome):
    blocked: bool = False
    reason: str = ""


@dataclass(frozen=True)
class PreToolOutcome(HookOutcome):
    blocked: bool = False
    reason: str = ""
    updated_input: dict[str, Any] | None = None


@dataclass(frozen=True)
class PostToolOutcome(HookOutcome):
    blocked: bool = False
    reason: str = ""
    updated_mcp_tool_output: dict[str, Any] | None = None


@dataclass(frozen=True)
class PermissionOutcome(HookOutcome):
    decision: Literal["allow", "deny", "no_opinion"] = "no_opinion"
    reason: str = ""


@dataclass(frozen=True)
class CompactOutcome(HookOutcome):
    blocked: bool = False
    reason: str = ""


@dataclass(frozen=True)
class StopOutcome(HookOutcome):
    blocked: bool = False
    reason: str = ""


@dataclass(frozen=True)
class ElicitationOutcome(HookOutcome):
    action: Literal["accept", "decline", "cancel", "no_opinion"] = "no_opinion"
    content: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


AnyHookOutcome: TypeAlias = (
    SessionOutcome
    | UserPromptOutcome
    | PreToolOutcome
    | PostToolOutcome
    | PermissionOutcome
    | CompactOutcome
    | StopOutcome
    | ElicitationOutcome
)


def outcome_metadata(outcome: AnyHookOutcome) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "hook_event": outcome.event_name,
        "receipt_ids": [item.receipt_id for item in outcome.receipts],
        "statuses": [item.status for item in outcome.receipts],
        "schema_errors": [
            {"receipt_id": item.receipt_id, "error": item.schema_error}
            for item in outcome.receipts
            if item.schema_error
        ],
        "delivery_ids": [
            item.delivery_id for item in outcome.delivery.model_context_fragments
        ],
        "ui_messages": list(outcome.delivery.system_messages),
    }
    for name in (
        "blocked",
        "reason",
        "updated_input",
        "updated_mcp_tool_output",
        "decision",
        "action",
        "content",
    ):
        if hasattr(outcome, name):
            payload[name] = getattr(outcome, name)
    return payload


def hook_request(event_name: str, payload: dict[str, Any] | None = None) -> HookRequest:
    request_types = {
        "SessionStart": SessionStartRequest,
        "UserPromptSubmit": UserPromptSubmitRequest,
        "PreToolUse": PreToolUseRequest,
        "PostToolUse": PostToolUseRequest,
        "PermissionRequest": PermissionRequestHookRequest,
        "PreCompact": PreCompactRequest,
        "PostCompact": PostCompactRequest,
        "Stop": StopRequest,
        "SessionEnd": SessionEndRequest,
        "Elicitation": ElicitationRequest,
    }
    request_type = request_types.get(event_name)
    if request_type is None:
        raise ValueError(f"unsupported public Hook event: {event_name}")
    return request_type(payload=dict(payload or {}))
