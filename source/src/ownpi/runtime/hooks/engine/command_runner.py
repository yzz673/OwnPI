from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import time
from uuid import uuid4

from ownpi.runtime.events import now_utc_iso
from ownpi.runtime.hooks.output_parser import parse_hook_output
from ownpi.runtime.hooks.registry import HookRegistration
from ownpi.runtime.hooks.types import HookRequest, HookRunReceipt
from ownpi.runtime.permissions.action_compiler import compile_canonical_action
from ownpi.runtime.permissions.models import EffectivePermissions
from ownpi.runtime.permissions.orchestrator import PermissionOrchestrator
from ownpi.runtime.sandbox import SandboxCommandRequest, SandboxManager


_ENV_ALLOWLIST = frozenset({"PATH", "HOME", "SHELL", "LANG", "TERM", "TMPDIR", "TEMP", "TMP"})


async def run_command_handler(
    registration: HookRegistration,
    request: HookRequest,
    *,
    effective_permissions: EffectivePermissions | None,
) -> HookRunReceipt:
    config = registration.config
    if config is None:
        raise ValueError("command Hook registration is missing config")
    started_at = now_utc_iso()
    started = time.monotonic()
    env = _build_environment(config.env, inherit_env=config.inherit_env)
    command = _resolve_command(config.command, env=env)
    stdout = ""
    stderr = ""
    error = ""
    exit_code: int | None = None
    timed_out = False
    action_hash = ""
    effect_hash = ""
    policy_hash = ""
    try:
        if effective_permissions is None:
            raise RuntimeError("Hook command execution requires canonical effective permissions")
        cwd = Path(config.cwd or request.payload.get("cwd") or Path.cwd()).expanduser().resolve()
        action = compile_canonical_action(
            request_id=f"hook:{request.event_id}:{registration.handler_key}",
            tool_name="hook_command",
            permission_class="execution",
            input_payload={"command": list(command), "cwd": str(cwd)},
            environment={"hook_event": request.event_name, "cwd": str(cwd)},
            destructive=False,
        )
        action_hash = action.action_hash
        effect_hash = action.effects.effect_hash
        orchestrator = PermissionOrchestrator()
        evaluation = orchestrator.evaluate(action.operation, effective_permissions=effective_permissions)
        if not evaluation.allowed:
            reason = evaluation.denial_reason
            if evaluation.approval_request is not None:
                reason = "Hook command requires approval at a boundary that cannot request it"
            raise RuntimeError(reason or "Hook command permission evaluation denied execution")
        rechecked = orchestrator.revalidate_for_execution(
            evaluation,
            effective_permissions=evaluation.effective_permissions,
        )
        if not rechecked.allowed:
            raise RuntimeError(rechecked.denial_reason or "Hook command permission revalidation failed")
        policy_hash = rechecked.effective_permissions.policy_hash
        result = await SandboxManager().execute_async(
            SandboxCommandRequest(
                argv=command,
                cwd=cwd,
                env=env,
                effective_permissions=rechecked.effective_permissions,
                timeout_seconds=config.timeout_seconds,
                stdin_text=json.dumps(_stdin_payload(request, registration), ensure_ascii=False, sort_keys=True),
            )
        )
        stdout = result.stdout
        stderr = result.stderr
        exit_code = result.exit_code
        timed_out = result.timed_out
    except (OSError, RuntimeError, ValueError) as exc:
        error = str(exc)
    parsed = parse_hook_output(stdout, stderr, event_name=request.event_name)
    status = "completed"
    if error or timed_out or (exit_code not in {None, 0, 2}):
        status = "failed"
    elif exit_code == 2:
        status = "blocked"
    return HookRunReceipt(
        receipt_id=f"hook-receipt:{uuid4().hex}",
        event_id=request.event_id,
        event_name=request.event_name,
        handler_key=registration.handler_key,
        handler_name=registration.name,
        handler_type="command",
        execution_mode=registration.execution_mode,
        source=registration.source,
        trust_status=registration.trust_status,
        started_at=started_at,
        finished_at=now_utc_iso(),
        duration_ms=int((time.monotonic() - started) * 1000),
        status=status,  # type: ignore[arg-type]
        exit_code=exit_code,
        timed_out=timed_out,
        stdout=stdout,
        stderr=stderr,
        error=error,
        output=parsed.payload,
        schema_error=parsed.schema_error,
        action_hash=action_hash,
        effect_hash=effect_hash,
        policy_hash=policy_hash,
    )


def _stdin_payload(request: HookRequest, registration: HookRegistration) -> dict[str, object]:
    payload = dict(request.payload)
    return {
        **payload,
        "hook_event_name": request.event_name,
        "event": {
            "event_id": request.event_id,
            "event_name": request.event_name,
            "payload": payload,
            "created_at": request.created_at,
        },
        "hook": {
            "key": registration.handler_key,
            "name": registration.name,
            "matcher": registration.matcher,
            "source": registration.source,
        },
    }


def _build_environment(configured: dict[str, str], *, inherit_env: bool) -> dict[str, str]:
    env = dict(os.environ) if inherit_env else {key: value for key, value in os.environ.items() if key in _ENV_ALLOWLIST}
    env.update({str(key): str(value) for key, value in configured.items()})
    return env


def _resolve_command(command: tuple[str, ...], *, env: dict[str, str]) -> tuple[str, ...]:
    values = list(command)
    if len(values) == 1:
        values = shlex.split(values[0])
    return tuple(_expand_environment(value, env=env) for value in values)


def _expand_environment(value: str, *, env: dict[str, str]) -> str:
    pattern = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")

    def replace(match: re.Match[str]) -> str:
        key = match.group(1) or match.group(2) or ""
        return env.get(key, match.group(0))

    return pattern.sub(replace, value)
