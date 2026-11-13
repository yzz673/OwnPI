"""Pure native-tool presentation; inputs and receipts remain canonical sources."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ownpi.runtime.execution.state import ExecutionRecord
from ownpi.tui_bridge.execution_display import PROCESS_TOOLS, command_execution_state, is_rg_no_matches
from ownpi.tui_bridge.web_evidence import web_evidence

READ_TOOLS = frozenset(
    {
        "file_reader",
        "config_reader",
        "notebook_reader",
        "pdf_reader",
        "plan_read",
        "contract_read",
        "experiment_result_reader",
        "citation_evidence_reader",
        "conversation_ref_reader",
        "internal_memory_read",
        "external_knowledge_read",
        "inspect_user_path",
    }
)
SEARCH_TOOLS = frozenset(
    {
        "tool_search",
        "grep_search",
        "lsp_query",
        "internal_memory_search",
        "external_knowledge_search",
    }
)
LIST_TOOLS = frozenset({"glob_search", "repo_inspector", "skills_list"})
CHANGE_TOOLS = frozenset(
    {
        "patch_apply",
        "file_write",
        "file_writer",
        "file_edit",
        "file_move",
        "file_rename",
        "file_delete",
        "mkdir",
        "notebook_edit",
        "write_task_spec",
        "write_stage_semantics",
    }
)
COMMAND_TOOLS = frozenset(
    {
        "exec_command",
        "write_stdin",
        "bash",
        "bash_runner",
        "shell",
        "exec",
        "execution_monitor",
        "execution_logs",
        "execution_kill",
        "runtime_task_list",
        "runtime_task_get",
        "runtime_task_poll",
        "runtime_task_kill",
        "latex_build",
        "pdf_inspect",
        "model_snapshot_materialize",
        "repository_materialize",
        "remote_profile_list",
        "remote_profile_show",
        "remote_check",
        "remote_resources",
        "remote_deploy",
        "remote_run",
        "remote_monitor",
        "remote_logs",
        "remote_pull",
        "citation_package_validate",
    }
)


def _text(values: dict[str, Any], *keys: str) -> str:
    return next(
        (
            values[key]
            for key in keys
            if isinstance(values.get(key), str) and values[key]
        ),
        "",
    )


def _preview(value: str, limit: int = 180) -> str:
    first = next((line.strip() for line in value.splitlines() if line.strip()), "")
    return first if len(first) <= limit else first[:limit - 1] + "…"


def present_hosted_tool(provider_type: str, raw: dict[str, Any]) -> dict[str, Any]:
    state = {"completed": "completed", "in_progress": "running", "searching": "running",
             "failed": "failed", "cancelled": "cancelled"}.get(raw.get("status"), "pending")
    action = raw.get("action") if isinstance(raw.get("action"), dict) else {}
    if provider_type == "web_search_call" and action.get("type") in {"search", "open_page", "find_in_page"}:
        queries = action.get("queries")
        queries = [value for value in queries if isinstance(value, str)] if isinstance(queries, list) else []
        query = " · ".join(queries) or _text(action, "query", "pattern")
        return {"kind": "web_search", "action": action["type"], "status": state,
                "payload": {"query": query, "web_action": dict(action),
                            "evidence": web_evidence(raw, hosted=True),
                            "transcript": json.dumps(raw, ensure_ascii=False, indent=2)}}
    return {"kind": "command_execution", "action": "hosted_tool", "status": state,
            "payload": {"command": provider_type or "Hosted tool", "output": [str(raw.get("status") or "Status unavailable")],
                        "transcript": json.dumps(raw, ensure_ascii=False, indent=2)}}


def display_path(value: str, workspace: Path) -> str:
    """Shorten only the displayed label, never the original tool arguments."""
    if not value:
        return ""
    path = Path(value)
    if path.is_absolute():
        try:
            relative = path.relative_to(workspace)
        except ValueError:
            return str(path)
        # Runtime records have long generated identities; the filename remains
        # visible and the complete identity stays in the original-source view.
        if (
            relative.parts
            and relative.parts[0] in {".ownpi", "app"}
            and "runs" in relative.parts
        ):
            return f"run/{path.name}"
        return str(relative)
    return value


def output_fields(raw: str) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    try:
        envelope = json.loads(raw)
    except (ValueError, TypeError):
        return {}, {}, raw, ""
    if not isinstance(envelope, dict):
        return {}, {}, raw, ""
    data = envelope.get("data")
    data = data if isinstance(data, dict) else {}
    # Presence, including an empty output, is meaningful. Do not fall back to
    # serializing execution handles/receipts for a successful silent command.
    stdout = _text(envelope, "stdout") or _text(data, "output", "stdout")
    stderr = _text(envelope, "stderr") or _text(data, "stderr")
    return envelope, data, stdout, stderr


def _patch_sections(patch: str) -> dict[str, list[tuple[str, list[str]]]]:
    sections: dict[str, list[tuple[str, list[str]]]] = {}
    current: list[str] | None = None
    for line in patch.splitlines():
        match = next(
            (
                (prefix, kind)
                for prefix, kind in (
                    ("*** Add File: ", "add"),
                    ("*** Update File: ", "update"),
                    ("*** Delete File: ", "delete"),
                )
                if line.startswith(prefix)
            ),
            None,
        )
        if match:
            prefix, kind = match
            current = []
            sections.setdefault(line[len(prefix) :], []).append((kind, current))
        elif current is not None and not line.startswith("*** "):
            current.append(line)
    return sections


def _file_changes(
    arguments: dict[str, Any], data: dict[str, Any], state: str, workspace: Path
) -> list[dict[str, Any]]:
    sections = _patch_sections(_text(arguments, "patch"))
    def path_key(path: str) -> str:
        value = Path(path)
        return os.path.normpath(str(value if value.is_absolute() else workspace / value))

    normalized: dict[str, list[tuple[str, list[str]]]] = {}
    for path, values in sections.items():
        normalized.setdefault(path_key(path), []).extend(values)
    changes = []
    # Only explicit delivered change receipts authorize an applied diff. A
    # proposed/failed patch is never presented as having modified the workspace.
    for receipt in data.get("changes", []) if state == "completed" else []:
        if not isinstance(receipt, dict) or not isinstance(receipt.get("path"), str):
            continue
        path, kind = receipt["path"], receipt.get("kind", "unknown")
        change: dict[str, Any] = {
            "path": path,
            "kind": kind,
            "operation": kind,
            "body_complete": False,
        }
        candidates = normalized.get(path_key(path), [])
        section = next((section for section in candidates if section[0] == kind), None)
        if section:
            candidates.remove(section)
        # Legacy Add File could replace an existing file. Its submitted body
        # proves the new content only, never a zero-deletion diff.
        if kind == "add" and receipt.get("before_exists") is True:
            change.update(kind="unknown", operation="overwrite", body_complete=False)
            if isinstance(receipt.get("unified_diff"), str):
                change.update(kind="update", unified_diff=receipt["unified_diff"], body_complete=True)
            changes.append(change)
            continue
        if isinstance(receipt.get("unified_diff"), str):
            change.update(unified_diff=receipt["unified_diff"], body_complete=True)
        elif section:
            body = section[1]
            if kind == "add":
                change.update(
                    content="\n".join(
                        line[1:] for line in body if line.startswith("+")
                    ),
                    body_complete=True,
                )
            elif kind == "update":
                # Preserve the submitted hunk context; do not reopen today's
                # filesystem to reconstruct yesterday's file contents.
                change["patch_excerpt"] = "\n".join(body)
                change["added_lines"] = sum(line.startswith("+") for line in body)
                change["removed_lines"] = sum(line.startswith("-") for line in body)
                change["body_complete"] = True
        if receipt.get("destination"):
            change["move_path"] = receipt["destination"]
        changes.append(change)
    if not changes:
        path = _text(data, "task_spec_path", "file_path", "path") or _text(
            arguments, "file_path", "path"
        )
        if path:
            changes.append(
                {
                    "path": path,
                    "kind": "unknown" if state == "completed" else "pending",
                    "body_complete": False,
                }
            )
    return changes


def present_tool(
    name: str,
    arguments: dict[str, Any],
    raw: str,
    state: str,
    *,
    workspace: Path,
    read_windows: list[dict[str, Any]] | None = None,
    execution_records: tuple[ExecutionRecord, ...] = (),
) -> dict[str, Any]:
    envelope, data, stdout, stderr = output_fields(raw)
    error = _text(envelope, "error") or _text(data, "error")
    if state == "failed" and not error:
        error = stderr if name in PROCESS_TOOLS else stderr or stdout or raw
    output = "\n".join(value for value in (stdout, stderr) if value)
    if not envelope:
        output = raw
    payload: dict[str, Any] = {
        "output": output.splitlines(),
        "transcript": output,
        "terminal_status": state,
        "error": error,
        "failure_class": _text(data, "failure_class") or _text(envelope, "failure_class"),
        "metadata": {},
        "cwd": _text(arguments, "workdir", "cwd") or str(workspace),
    }
    code = data.get("exit_code", envelope.get("exit_code"))
    if isinstance(code, int) and not isinstance(code, bool):
        payload["exit_code"] = code
    duration = data.get("wall_time_seconds")
    if (
        isinstance(duration, (int, float))
        and not isinstance(duration, bool)
        and duration >= 0
    ):
        payload["duration_ms"] = round(duration * 1000)
    payload["transcript_complete"] = data.get("output_truncated") is not True
    state = command_execution_state(name, arguments, data, payload, state, execution_records)
    payload["terminal_status"] = state
    kind, action = "command_execution", "run"
    payload["command"] = _text(arguments, "cmd", "command") or name
    if name == "exec_command" and state == "failed" and is_rg_no_matches(
        payload["command"], code, output, data.get("output_truncated") is True
    ) and error in {"", "process failed"}:
        payload["result_state"] = "no_matches"
    if name in READ_TOOLS | SEARCH_TOOLS | LIST_TOOLS or name == "skills_read":
        action = (
            "read"
            if name in READ_TOOLS or name == "skills_read"
            else "search"
            if name in SEARCH_TOOLS
            else "list"
        )
        target = display_path(
            _text(arguments, "file_path", "path", "search_root", "root", "directory"),
            workspace,
        )
        if name == "skills_read":
            package = _text(arguments, "package", "skill_name", "name")
            resource = Path(_text(arguments, "resource") or "SKILL.md").name
            label = (
                resource
                if resource.lower() == "skill.md"
                else Path(resource).stem.replace("-", " ").replace("_", " ")
            )
            target = f"{label} ({package} skill)"
        elif name == "contract_read":
            target = _text(arguments, "contract_id") or "contract (identity unavailable)"
        elif name == "tool_search":
            target = "tools"
        target = target or ("skills" if name == "skills_list" else "workspace")
        query = _text(arguments, "query", "pattern", "symbol", "text")
        payload.update(
            target=target,
            display_target=target,
            query=query,
            output=[],
            aggregation_key=f"{action}:{name}:{target}:{query}",
        )
        if read_windows:
            payload["read_windows"] = read_windows
        if name == "pdf_reader":
            payload["page_ranges"] = arguments.get("pages", [])
        if not output:
            payload["transcript"] = f"{action.title()} {target}"
    elif name in CHANGE_TOOLS:
        kind, action = (
            ("command_execution", "run")
            if state in {"pending", "unverified"}
            else ("file_change", "change")
        )
        payload.update(
            changes=_file_changes(arguments, data, state, workspace), stdout="", stderr=stderr
        )
    elif name in {"run_subagents", "complete_subagent"}:
        kind, action = "sub_agent_activity", "delegate" if name == "run_subagents" else "report"
        result = data or envelope
        if name == "run_subagents":
            tasks = arguments.get("tasks")
            tasks = tasks if isinstance(tasks, list) else []
            receipts = result.get("feedback_index")
            receipts = receipts if isinstance(receipts, list) else []
            rows = []
            for task in tasks:
                if not isinstance(task, dict):
                    continue
                label = _text(task, "task_name") or "Unnamed task"
                receipt = next((row for row in receipts if isinstance(row, dict) and row.get("task_name") == label), {})
                status = _text(receipt, "status") or state
                detail = f"{label} · {status}"
                if receipt.get("child_id"):
                    detail += f" · {str(receipt['child_id'])[:8]}"
                if receipt:
                    if receipt.get("feedback_submitted"):
                        detail += " · feedback submitted"
                        detail += " · full text available" if receipt.get("feedback_available") else " · full text unavailable"
                    else:
                        detail += " · feedback available" if receipt.get("feedback_available") else " · feedback unavailable"
                    if receipt.get("reclaimed"):
                        detail += " · resources released"
                rows.append(detail)
                if task.get("message"):
                    rows.append("Task: " + _preview(str(task["message"])))
            title = "Subagent tasks" if state == "completed" else "Subagent tasks · " + state
            payload.update(title=title, text="\n".join(rows), transcript=raw)
        else:
            label = _text(result, "task_name") or "Subagent"
            title = f"Subagent completion {'reported' if state == 'completed' else state} · {label}"
            # Only a delivered receipt supplies a completed response. Arguments
            # alone remain a proposed report, never proof of parent delivery.
            summary = _preview(_text(result, "content")) if state == "completed" else error
            payload.update(title=title, text=summary, transcript=raw)
    elif name == "update_plan":
        kind, action = "plan_update", "update"
        payload.update(
            plan=arguments.get("plan", []), explanation=arguments.get("explanation", "")
        )
    elif name in {"web_search", "web_fetch", "page_snapshot", "page_fetch_rendered"}:
        kind, action = "web_search", "search" if name == "web_search" else "open_page"
        payload.update(
            query=_text(arguments, "query"),
            web_action={"type": action, "url": _text(arguments, "url")},
            http_status=data.get("status_code"),
            evidence=web_evidence(data),
        )
    elif name in {"view_image", "image_reader", "page_screenshot"}:
        kind = "image_generation" if name == "page_screenshot" else "image_view"
        action = "generate" if name == "page_screenshot" else "view"
        payload.update(
            path=_text(data, "file_path", "path")
            or _text(arguments, "file_path", "path"),
            prompt=_text(arguments, "prompt"),
        )
    elif name == "skill_invoke":
        kind, action = "skill_activity", "invoke"
        payload.update(
            skill_name=_text(arguments, "skill_name", "name"),
            purpose=_text(arguments, "purpose"),
        )
    elif name == "citation_resolve":
        action = "resolve_citation"
        receipt = data.get("receipt") if isinstance(data.get("receipt"), dict) else {}
        metadata = (
            receipt.get("metadata")
            if isinstance(receipt.get("metadata"), dict)
            else receipt
        )
        payload.update(
            result_state=_text(data, "outcome"),
            requested_identifier=_text(arguments, "identifier"),
            requested_title=_text(arguments, "title"),
            requested_author=_text(arguments, "author"),
            requested_year=_text(arguments, "year"),
            resolved_identifier=_text(data, "canonical_id"),
            resolved_title=_text(metadata, "title"),
            resolved_authors=metadata.get("authors", []),
            resolved_year=_text(metadata, "year"),
            content_access_status=_text(receipt, "content_access_status"),
            citation_candidates=data.get("candidates", []),
            diagnostics=data.get("diagnostics", []),
            output=[],
        )
    elif name == "paper_fetch":
        coverage = (
            data.get("coverage") if isinstance(data.get("coverage"), dict) else {}
        )
        payload["metadata"] = {
            key: value
            for key, value in {
                "doi": _text(data, "doi"),
                "identifier": "" if _text(data, "doi") else _text(arguments, "identifier", "url", "doi"),
                "title": _text(data, "title"),
                "outcome": _text(data, "outcome"),
                "content_level": _text(data, "content_status"),
                "extraction_coverage": _text(coverage, "kind"),
                "selected_source": _text(data, "selected_source"),
            }.items()
            if value
        }
        payload["output"] = []
    elif name == "report_progress" and state == "completed":
        kind, action = "agent_message", "progress"
        payload = {"text": _text(arguments, "text"), "role": "assistant", "phase": "commentary",
                   "phase_recorded": True, "message_purpose": "progress"}
    elif name in {
        "request_review",
        "complete_feedback",
        "submit_feedback",
    }:
        action = "stage_control"
        label = {
            "request_review": "Requested review",
            "complete_feedback": "Recorded core feedback",
            "submit_feedback": "Submitted review feedback",
        }[name]
        payload.update(
            command=label, output=[], transcript=label + ("\n" + error if error else "")
        )
    elif name not in COMMAND_TOOLS:
        kind, action = "mcp_tool_call", "call"
        payload.update(
            server="OwnPI", tool=name, arguments=arguments, is_error=state == "failed"
        )
    if state == "unverified":
        # This is a display diagnostic, not an execution failure or success.
        if name in CHANGE_TOOLS:
            kind, action = "command_execution", "stage_control"
            payload["command"] = name.replace("_", " ").capitalize()
        payload.update(
            error="",
            error_summary="",
            output=["Result unavailable · see raw source"],
            transcript="Result unavailable: canonical call identity could not be verified.",
        )
    return {"kind": kind, "action": action, "status": state, "payload": payload}
