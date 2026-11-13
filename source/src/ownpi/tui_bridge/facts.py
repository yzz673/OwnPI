"""Pure display adapters for backend replies and original pending requests."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import re

from ownpi.runtime.payload_redaction import redact_payload
from ownpi.tui_bridge.search_display import describe_search_query
from ownpi.tui_bridge.permission_scope import approval_reuse_scope


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _failure_facts(details: dict[str, Any]) -> list[tuple[str, str]]:
    """Compact display fields only; never decide whether to retry or resume."""
    provider = _mapping(details.get("failure_diagnostics"))
    facts: list[tuple[str, str]] = []
    error = []
    http_status = provider.get("http_status")
    if type(http_status) is int and 100 <= http_status <= 599:
        error.append(f"HTTP {http_status}")
    code = (
        provider.get("error_code")
        or provider.get("provider_error_code")
        or details.get("reason_code")
    )
    if isinstance(code, str) and code.strip():
        error.append(code.strip())
    if error:
        facts.append(("Error", " · ".join(error)))
    message = provider.get("error_message") or details.get("message")
    if isinstance(message, str) and message.strip():
        facts.append(("Message", message.strip()))
    retryable = provider.get("retryable")
    if isinstance(retryable, bool):
        facts.append(("Retryable", "Yes" if retryable else "No"))
    recovery = details.get("recommended_recovery")
    if isinstance(recovery, str) and recovery.strip():
        label = {
            "repair_provider_input_then_retry": "Repair provider input before retry",
            "retry_current_provider_turn": "Wait for provider recovery, then resume manually",
            "inspect_provider_protocol_then_retry": "Inspect provider protocol before retry",
            "repair_local_runtime_then_retry": "Repair local runtime before retry",
        }.get(recovery, recovery.replace("_", " "))
        facts.append(("Recovery", label))
    resume = []
    resumable = details.get("resumable")
    automatic = details.get("auto_resume_safe")
    if isinstance(resumable, bool):
        resume.append("Available · not started" if resumable else "Unavailable")
    if isinstance(automatic, bool):
        resume.append(
            "backend reports safe" if automatic else "backend requires checks"
        )
    if resume:
        facts.append(("Resume", " · ".join(resume)))
    return facts


def outcome_payload(
    status: str, package: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Missing delivery evidence stays unknown, including on cold history reads."""
    package = _mapping(package)
    outcome = _mapping(package.get("run_outcome"))
    summary = _mapping(package.get("run_summary"))
    diagnostics = _mapping(package.get("runtime_diagnostics"))
    details = _mapping(
        diagnostics.get("runtime_failure_details")
        or summary.get("runtime_failure_details")
        or {}
    )
    artifacts = []
    for item in package.get("artifact_manifest") or []:
        if (
            isinstance(item, dict)
            and isinstance(item.get("path"), str)
            and item.get("visibility") == "user"
        ):
            artifacts.append(
                {**item, "display_path": item.get("display_path") or item["path"]}
            )
    return {
        "outcome": status,
        "display_status": status,
        "task_success": outcome.get("task_success", False),
        "delivery_status": package.get("delivery_status") or "unknown",
        "delivery_class": package.get("delivery_class") or "",
        "delivery_summary": outcome.get("claim_boundary") or "",
        "primary_artifacts": artifacts,
        "caveats": (
            [outcome["claim_boundary"]]
            if outcome.get("claim_boundary")
            else (
                []
                if package
                else [
                    "Delivery details are not recorded in this canonical run boundary."
                ]
            )
        ),
        "failure_code": details.get("reason_code") or "",
        "failure_cause": diagnostics.get("runtime_failure_reason")
        or summary.get("runtime_failure_reason")
        or "",
        "failure_summary": details.get("message") or "",
        "failure_detail": json.dumps(details, ensure_ascii=False) if details else "",
        "failure_facts": _failure_facts(details),
        "runtime_rationale": list(outcome.get("rationale") or []),
        "recovery_actions": [],
        "outcome_class": outcome.get("outcome_class") or "",
        "outcome_source": "backend_reply" if package else "run_finalized",
    }


def approval_argument_text(value: Any, *, search_query: bool = False) -> str:
    """Readable approval text; never alter the submitted invocation."""
    if not isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if search_query:
        # Search phrases can arrive with one or more layers of quote escaping.
        # Do not apply this normalization to commands, paths or regular expressions.
        value = re.sub(r"\\+([\"'])", r"\1", value)
    # Strings are already decoded. JSON-encoding them again leaks transport escapes.
    # Keep terminal controls visible as text rather than passing them to the terminal.
    return "".join(json.dumps(char, ensure_ascii=False)[1:-1]
                   if ord(char) < 32 or 127 <= ord(char) <= 159 else char for char in value)


def approval_view(item: dict[str, Any]) -> dict[str, Any]:
    request = item.get("request_payload") or {}
    arguments = request.get("input") or {}
    if not isinstance(arguments, dict):
        arguments = {}
    tool = item.get("tool_name") or ""
    changes = list(request.get("file_changes") or [])
    patch = arguments.get("patch") or arguments.get("patch_text") or ""
    if isinstance(patch, str):
        for operation, path in re.findall(
            r"^\*\*\* (Add|Update|Delete) File: (.+)$", patch, re.M
        ):
            changes.append(
                {
                    "path": path,
                    "change_kind": {
                        "Add": "create",
                        "Update": "modify",
                        "Delete": "delete",
                    }[operation],
                }
            )
    path = arguments.get("file_path") or arguments.get("path")
    if (
        not changes
        and isinstance(path, str)
        and tool
        in {
            "file_write",
            "file_writer",
            "file_edit",
            "file_delete",
            "file_move",
            "file_rename",
            "notebook_edit",
            "mkdir",
        }
    ):
        changes.append(
            {
                "path": path,
                "change_kind": "delete" if tool == "file_delete" else "write",
            }
        )
    command = str(arguments.get("cmd") or arguments.get("command") or "")
    cwd = str(arguments.get("workdir") or arguments.get("cwd") or "")
    rule = str(item.get("permission_class") or "")
    summary = tool + (f"\nWorking directory: {cwd}" if cwd else "")
    visible_arguments = redact_payload({
        key: "[REDACTED]" if key.casefold() == "env_overrides" else value
        for key, value in arguments.items()
    })
    display_values = {key: approval_argument_text(value, search_query=tool == "web_search" and key == "query")
                      for key, value in visible_arguments.items() if key != "max_chars"}
    if tool == "web_search" and "query" in display_values:
        display_values["query"] = describe_search_query(display_values["query"], str(arguments.get("backend") or ""))
    operation_details = "\n".join(
        f"{key}: {value}" for key, value in display_values.items()
    ) or ("Arguments: none" if isinstance(request.get("input"), dict) else "Arguments unavailable")
    details = patch or command or json.dumps(visible_arguments, indent=2, ensure_ascii=False)
    language = "diff" if patch else "sh" if command else "json"
    body = f"## {tool}\n\n{item.get('reason') or item.get('human_prompt') or ''}\n\nRule: {rule}\n\n```{language}\n{details}\n```"
    return {
        **item,
        "kind": "permission",
        "command": command,
        "cwd": cwd,
        "approval_kind": "apply_patch"
        if tool == "patch_apply"
        else "file"
        if changes
        else "command",
        "file_changes": changes,
        "permission_rule": rule,
        "network_host": str(arguments.get("url") or arguments.get("host") or ""),
        "call_id": item.get("request_id") or "",
        "approval_id": item.get("session_id") or "",
        "actor_kind": item.get("actor_kind") or "core",
        "actor_id": item.get("actor_id"),
        "stage": item.get("stage") or "",
        "attempt": item.get("attempt"),
        "created_at": item.get("created_at"),
        "request_summary": summary,
        "operation_details": operation_details,
        "remember_scope": approval_reuse_scope(item),
        "request_payload": {**redact_payload(request), "input": visible_arguments} if "input" in request else redact_payload(request),
        "body_markdown": body,
    }


def resume_command(config: Path, app_home: Path, workspace: Path, run_id: str) -> str:
    import shlex

    return shlex.join(
        [
            "ownpi-tui",
            "--config",
            str(config),
            "--app-home",
            str(app_home),
            "--project-root",
            str(workspace),
            "--resume",
            run_id,
        ]
    )


def reply_activities(run_id: str, package: dict[str, Any]) -> list[dict[str, Any]]:
    """Transient artifact receipts; approvals are bound to original calls separately."""
    result = []
    artifacts = [
        item
        for item in package.get("artifact_manifest") or []
        if item.get("visibility") == "user" and item.get("exists") is True
    ]
    if artifacts:
        result.append(
            {
                "schema_version": "ownpi.tui.item.v1",
                "visibility": "user",
                "kind": "artifact_activity",
                "item_id": f"{run_id}:backend-artifacts",
                "status": "completed",
                "source_kind": "backend_reply",
                "payload": {
                    "artifact_kind": "Backend reported artifact",
                    "run_id": run_id,
                    "artifacts": [
                        {"path": item["path"], "display_path": item["path"]}
                        for item in artifacts
                    ],
                },
            }
        )
    return result
