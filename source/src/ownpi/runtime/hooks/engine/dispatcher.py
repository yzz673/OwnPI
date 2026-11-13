from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
import time
from typing import Any
from uuid import uuid4

from ownpi.runtime.events import now_utc_iso
from ownpi.runtime.hooks.context_delivery import build_delivery_batch
from ownpi.runtime.hooks.engine.command_runner import run_command_handler
from ownpi.runtime.hooks.engine.mcp_runner import MCPHookExecutor, run_mcp_handler
from ownpi.runtime.hooks.engine.scheduler import HookScheduler
from ownpi.runtime.hooks.output_parser import normalized_event_output
from ownpi.runtime.hooks.observability import HookObservability
from ownpi.runtime.hooks.registry import HookRegistration, HookRegistry
from ownpi.runtime.hooks.types import (
    AnyHookOutcome,
    CompactOutcome,
    ElicitationOutcome,
    HookRequest,
    HookRunReceipt,
    PermissionOutcome,
    PostToolOutcome,
    PreToolOutcome,
    SessionOutcome,
    StopOutcome,
    UserPromptOutcome,
)
from ownpi.runtime.permissions.models import EffectivePermissions


class HookEngine:
    def __init__(
        self,
        registry: HookRegistry | None = None,
        *,
        effective_permissions: EffectivePermissions | None = None,
        mcp_executor: MCPHookExecutor | None = None,
        scheduler: HookScheduler | None = None,
        observability: HookObservability | None = None,
    ) -> None:
        self._registry = registry or HookRegistry()
        self._effective_permissions = effective_permissions
        self._mcp_executor = mcp_executor
        self._scheduler = scheduler or HookScheduler()
        self._observability = observability or HookObservability()

    @property
    def enabled(self) -> bool:
        return self._registry.enabled

    @property
    def registry(self) -> HookRegistry:
        return self._registry

    @property
    def observability(self) -> HookObservability:
        return self._observability

    def has_event(self, event_name: str) -> bool:
        try:
            return self._registry.has_event(event_name)  # type: ignore[arg-type]
        except ValueError:
            return False

    async def run_async(
        self,
        request: HookRequest,
        *,
        effective_permissions: EffectivePermissions | None = None,
    ) -> AnyHookOutcome:
        permissions = effective_permissions or self._effective_permissions
        receipts: list[HookRunReceipt] = []
        for registration in self._registry.matching(request):
            try:
                isolated_request = replace(request, payload=deepcopy(request.payload))
            except Exception as exc:
                receipt = _handler_failure_receipt(registration, request, error=exc)
            else:
                if registration.execution_mode == "async":
                    self._schedule_async(
                        registration,
                        isolated_request,
                        effective_permissions=permissions,
                    )
                    receipts.append(_scheduled_receipt(registration, request))
                    continue
                receipt = await self._run_registration(
                    registration,
                    isolated_request,
                    effective_permissions=permissions,
                )
            self._observability.completed(run_id=_request_run_id(request), receipt=receipt)
            receipts.append(receipt)
            if _terminal_control_receipt(request, receipt):
                break
        return _build_outcome(request, tuple(receipts))

    def run(
        self, request: HookRequest, *,
        effective_permissions: EffectivePermissions | None = None,
    ) -> AnyHookOutcome:
        return asyncio.run(self.run_async(
            request, effective_permissions=effective_permissions,
        ))

    def close(self, *, wait: bool = True) -> None:
        self._scheduler.close(wait=wait)

    def with_registrations(self, registrations: tuple[HookRegistration, ...]) -> "HookEngine":
        if not registrations:
            return self
        registry = HookRegistry((*self._registry.snapshot, *registrations))
        return HookEngine(
            registry,
            effective_permissions=self._effective_permissions,
            mcp_executor=self._mcp_executor,
            scheduler=self._scheduler,
            observability=self._observability,
        )

    async def _run_registration(
        self,
        registration: HookRegistration,
        request: HookRequest,
        *,
        effective_permissions: EffectivePermissions | None,
    ) -> HookRunReceipt:
        self._observability.started(
            run_id=_request_run_id(request),
            event_name=request.event_name,
            handler_key=registration.handler_key,
        )
        try:
            if registration.handler_type == "command":
                return await run_command_handler(
                    registration,
                    request,
                    effective_permissions=effective_permissions,
                )
            if registration.handler_type == "mcp_tool":
                return run_mcp_handler(
                    registration,
                    request,
                    executor=self._mcp_executor,
                )
            return _run_callback(registration, request)
        except Exception as exc:
            return _handler_failure_receipt(
                registration,
                request,
                error=exc,
            )

    def _schedule_async(
        self,
        registration: HookRegistration,
        request: HookRequest,
        *,
        effective_permissions: EffectivePermissions | None,
    ) -> None:
        async def operation() -> HookRunReceipt:
            receipt = await self._run_registration(
                registration,
                request,
                effective_permissions=effective_permissions,
            )
            self._observability.completed(run_id=_request_run_id(request), receipt=receipt)
            return receipt

        self._scheduler.submit(lambda: asyncio.run(operation()))


def _run_callback(registration: HookRegistration, request: HookRequest) -> HookRunReceipt:
    started_at = now_utc_iso()
    started = time.monotonic()
    output: dict[str, Any] = {}
    error = ""
    try:
        if registration.callback is None:
            raise RuntimeError("managed Hook callback is missing")
        output = dict(registration.callback(dict(request.payload)) or {})
    except (RuntimeError, TypeError, ValueError) as exc:
        error = str(exc)
    return HookRunReceipt(
        receipt_id=f"hook-receipt:{uuid4().hex}",
        event_id=request.event_id,
        event_name=request.event_name,
        handler_key=registration.handler_key,
        handler_name=registration.name,
        handler_type="callback",
        execution_mode="sync",
        source=registration.source,
        trust_status=registration.trust_status,
        started_at=started_at,
        finished_at=now_utc_iso(),
        duration_ms=int((time.monotonic() - started) * 1000),
        status="failed" if error else "completed",
        error=error,
        output=output,
    )


def _handler_failure_receipt(
    registration: HookRegistration,
    request: HookRequest,
    *,
    error: Exception,
) -> HookRunReceipt:
    timestamp = now_utc_iso()
    return HookRunReceipt(
        receipt_id=f"hook-receipt:{uuid4().hex}",
        event_id=request.event_id,
        event_name=request.event_name,
        handler_key=registration.handler_key,
        handler_name=registration.name,
        handler_type=registration.handler_type,
        execution_mode=registration.execution_mode,
        source=registration.source,
        trust_status=registration.trust_status,
        started_at=timestamp,
        finished_at=timestamp,
        duration_ms=0,
        status="failed",
        error=str(error),
    )


def _scheduled_receipt(registration: HookRegistration, request: HookRequest) -> HookRunReceipt:
    timestamp = now_utc_iso()
    return HookRunReceipt(
        receipt_id=f"hook-receipt:{uuid4().hex}",
        event_id=request.event_id,
        event_name=request.event_name,
        handler_key=registration.handler_key,
        handler_name=registration.name,
        handler_type=registration.handler_type,
        execution_mode="async",
        source=registration.source,
        trust_status=registration.trust_status,
        started_at=timestamp,
        finished_at=timestamp,
        duration_ms=0,
        status="scheduled",
    )


def _build_outcome(
    request: HookRequest,
    receipts: tuple[HookRunReceipt, ...],
) -> AnyHookOutcome:
    outputs = tuple(
        normalized_event_output(item.output, event_name=request.event_name)
        for item in receipts
        if item.output and not item.schema_error
    )
    delivery = build_delivery_batch(
        request,
        receipts,
    )
    common = {
        "event_name": request.event_name,
        "receipts": receipts,
        "delivery": delivery,
    }
    if request.event_name in {"SessionStart", "SessionEnd"}:
        return SessionOutcome(**common)
    if request.event_name == "UserPromptSubmit":
        blocked, reason = _blocking(outputs, receipts)
        return UserPromptOutcome(**common, blocked=blocked, reason=reason)
    if request.event_name == "PreToolUse":
        blocked, reason = _blocking(outputs, receipts)
        return PreToolOutcome(
            **common,
            blocked=blocked,
            reason=reason,
            updated_input=_last_mapping(outputs, "updatedInput"),
        )
    if request.event_name == "PostToolUse":
        blocked, reason = _blocking(outputs, receipts, default_reason="Hook blocked the tool result")
        return PostToolOutcome(
            **common,
            blocked=blocked,
            reason=reason,
            updated_mcp_tool_output=_last_mapping(outputs, "updatedMCPToolOutput"),
        )
    if request.event_name == "PermissionRequest":
        decision = _last_string(outputs, "decision").lower()
        if decision not in {"allow", "deny"}:
            decision = "no_opinion"
        return PermissionOutcome(
            **common,
            decision=decision,  # type: ignore[arg-type]
            reason=_last_string(outputs, "reason"),
        )
    if request.event_name in {"PreCompact", "PostCompact"}:
        blocked, reason = _blocking(outputs, receipts) if request.event_name == "PreCompact" else (False, "")
        return CompactOutcome(**common, blocked=blocked, reason=reason)
    if request.event_name == "Stop":
        blocked, reason = _blocking(outputs, receipts)
        return StopOutcome(**common, blocked=blocked, reason=reason)
    action = _last_string(outputs, "action").lower()
    if action not in {"accept", "decline", "cancel"}:
        action = "no_opinion"
    return ElicitationOutcome(
        **common,
        action=action,  # type: ignore[arg-type]
        content=_last_mapping(outputs, "content") or {},
        reason=_last_string(outputs, "reason"),
    )


def _blocking(
    outputs: tuple[dict[str, Any], ...], receipts: tuple[HookRunReceipt, ...],
    *, default_reason: str = "Hook blocked execution",
) -> tuple[bool, str]:
    blocked_receipt = next((item for item in receipts if item.status == "blocked"), None)
    for output in outputs:
        decision = output.get("decision")
        behavior = ""
        if isinstance(decision, str):
            behavior = decision.strip().lower()
        elif isinstance(decision, dict):
            behavior = str(decision.get("behavior", "") or "").strip().lower()
        if behavior in {"block", "deny"} or output.get("continue") is False:
            return True, _reason(output) or default_reason
    if blocked_receipt is not None:
        return True, (blocked_receipt.stderr or blocked_receipt.stdout or blocked_receipt.error).strip()
    return False, _last_string(outputs, "reason")


def _reason(output: dict[str, Any]) -> str:
    direct = output.get("reason")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    decision = output.get("decision")
    if isinstance(decision, dict):
        for key in ("reason", "message"):
            value = decision.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _last_string(outputs: tuple[dict[str, Any], ...], key: str) -> str:
    for output in reversed(outputs):
        value = output.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _last_mapping(outputs: tuple[dict[str, Any], ...], key: str) -> dict[str, Any] | None:
    for output in reversed(outputs):
        value = output.get(key)
        if isinstance(value, dict):
            return dict(value)
        decision = output.get("decision")
        if isinstance(decision, dict) and isinstance(decision.get(key), dict):
            return dict(decision[key])
    return None


def _terminal_control_receipt(request: HookRequest, receipt: HookRunReceipt) -> bool:
    if receipt.status == "blocked":
        return True
    if request.event_name not in {
        "UserPromptSubmit",
        "PreToolUse",
        "PermissionRequest",
        "PreCompact",
        "Stop",
        "Elicitation",
    }:
        return False
    output = normalized_event_output(receipt.output, event_name=request.event_name)
    decision = output.get("decision")
    behavior = ""
    if isinstance(decision, str):
        behavior = decision.strip().lower()
    elif isinstance(decision, dict):
        behavior = str(decision.get("behavior", "") or "").strip().lower()
    if behavior in {"block", "deny"} or output.get("continue") is False:
        return True
    if request.event_name == "PermissionRequest" and behavior == "deny":
        return True
    if request.event_name == "Elicitation":
        return str(output.get("action", "") or "").strip().lower() in {
            "accept",
            "decline",
            "cancel",
        }
    return False


def _request_run_id(request: HookRequest) -> str:
    return str(request.payload.get("run_id", "") or request.event_id)
