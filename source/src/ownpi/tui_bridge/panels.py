"""Read-only command panels over current Runtime public inspection APIs."""

from __future__ import annotations

import json
import shlex
from dataclasses import asdict
from typing import Any

from ownpi.runtime.instructions import discover_instructions
from ownpi.runtime.rollout.business_reader import read_business_events
from ownpi.runtime.rollout.execution_events import project_execution_events
from ownpi.runtime.rollout.run_events import run_events_path
from ownpi.tui_bridge.lifecycle import run_capabilities


def document(title: str, body: str) -> dict[str, Any]:
    return {"presenter": "document", "title": title, "body": body}


def row(
    name: str, command: str, description: str = "", *, navigation: str = "push"
) -> dict[str, Any]:
    params: dict[str, Any] = {"text": command}
    if navigation == "leave":
        params["close_menus"] = True
        if command.startswith("/resolve_run "):
            params["open_requests"] = True
    else:
        params["menu_navigation"] = navigation
    return {
        "id": command,
        "name": name,
        "description": description,
        "dismiss_on_select": navigation == "leave",
        "action": {"method": "command", "params": params},
    }


def selection(
    title: str, rows: list[dict[str, Any]], subtitle: str = "", *, view_id: str = ""
) -> dict[str, Any]:
    return {
        "presenter": "selection",
        "view_id": view_id or title.lower().replace(" ", "."),
        "title": title,
        "rows": rows,
        "subtitle": subtitle,
        "footer_hint": "Enter select   Esc back",
    }


def run_panel(bridge: Any, tokens: list[str]) -> dict[str, Any]:
    if len(tokens) == 1:
        from ownpi.tui_bridge.resume_picker import resume_picker

        return resume_picker(bridge)
    return run_detail_panel(bridge, tokens)


def run_detail_panel(bridge: Any, tokens: list[str]) -> dict[str, Any]:
    name = tokens[0].lstrip("/")
    target = tokens[1]
    if name == "run_actions":
        resolved = bridge.service().resolve_run_target(target)
        status = bridge.service().status(resolved).payload
        capability = run_capabilities(resolved)
        messages = [item["payload"].get("text", "") for item in bridge.projector.read_run(resolved).items
                    if item["kind"] == "user"]
        preview = messages[-1][:240] if messages else ""
        can_open = capability["available"] or status["status"] == "completed"
        return selection(
            "Direct session" if capability["mode"] == "direct" else "Workflow run",
            [
                row(
                    "Open history",
                    shlex.join(["/inspect_run", target]),
                    "View this run's canonical conversation history.",
                    navigation="leave",
                ),
                row(
                    "Inspect",
                    shlex.join(["/run_inspect", target]),
                    "Show the current backend run state and execution owner.",
                ),
                row(
                    "Context",
                    shlex.join(["/run_context", target]),
                    "Show context usage and source diagnostics.",
                ),
                {**row(
                    "Continue conversation" if capability["can_submit"] else "Open completed run" if status["status"] == "completed" else "Resume",
                    shlex.join(["/resume", target]),
                    "Open without executing; enter your next message." if capability["can_submit"] else
                    "Continue through the backend's standard recovery entry." if can_open else capability["reason"],
                    navigation="leave",
                ), "disabled": not can_open, "disabled_reason": capability["reason"] if not can_open else ""},
                row(
                    "Resolve interaction",
                    shlex.join(["/resolve_run", target]),
                    "Open this run's pending approvals or questions.",
                    navigation="leave",
                ),
            ],
            f"{status['status']} · {target}\n{preview}",
            view_id="runs.actions",
        )
    path = bridge.service().resolve_run_target(target)
    if name == "run_inspect":
        return document(
            "Run inspection",
            "```json\n"
            + json.dumps(bridge.service().status(path).payload, indent=2)
            + "\n```",
        )
    projected = bridge.projector.read_run(path)
    kinds = {}
    for item in projected.items:
        if (item.get("payload") or {}).get("source_list") is True:
            continue
        kinds[item["kind"]] = kinds.get(item["kind"], 0) + 1
    body = f"Run: {path.name}\n\n" + "\n".join(
        f"- {key}: {value}" for key, value in kinds.items()
    )
    body += (
        "\n\n## Latest normal core context usage\n```json\n"
        + json.dumps(projected.usage, indent=2)
        + "\n```"
    )
    body += "\n\n## Source diagnostics\n" + ("\n".join(projected.warnings) or "None")
    return document("Context and evidence", body)


def executions(bridge: Any) -> tuple[Any, ...]:
    if not bridge.run_dir or not run_events_path(bridge.run_dir).exists():
        return ()
    events = read_business_events(run_events_path(bridge.run_dir)).events
    return project_execution_events(events).registry.records


def process_panel(bridge: Any, tokens: list[str]) -> dict[str, Any]:
    records = executions(bridge)
    show_all = len(tokens) > 1 and tokens[1] == "--all"
    if len(tokens) > 1 and not show_all:
        record = next(
            (value for value in records if value.execution_id == tokens[1]), None
        )
        if record is None:
            raise ValueError("Unknown backend execution")
        return document(
            "Runtime execution",
            f"Command: {record.handle.command_display or 'Unavailable in this record'}\n\n"
            f"Working directory: {record.handle.local_workdir or record.handle.remote_workdir or 'Unknown'}\n\n"
            f"Last recorded state: {record.status}\n\n"
            f"Observed at: {record.updated_at or 'Unknown'}\n\n"
            + ("Current process state: unverified. Reloading records does not probe the process.\n\n"
               if not record.terminal else "")
            + "## Recorded details\n```json\n" + json.dumps(asdict(record), default=str, indent=2) + "\n```",
        )
    unresolved = [item for item in records if not item.terminal]
    completed_count = len(records) - len(unresolved)
    visible = sorted(records if show_all else unresolved,
                     key=lambda item: (item.terminal, -item.launch_order))
    rows = [
        {**row(
            item.handle.command_display
            or f"Command unavailable · {item.handle.tool_name or 'execution'} · #{item.launch_order}",
            shlex.join(["/ps", item.execution_id]),
            f"Last recorded: {item.status} · {item.updated_at or 'time unknown'}"
            + (" · current state unverified" if not item.terminal else "")
            + f"\n{item.origin.stage or 'stage unknown'} · {item.execution_id}",
        ), "selected_description": f"Last recorded: {item.status}\n{item.updated_at or 'time unknown'}"
            + ("\nCurrent state: unverified" if not item.terminal else "")}
        for item in visible
    ]
    if show_all:
        rows.append(row("Show unresolved only", "/ps", f"{len(unresolved)} executions without a recorded outcome.", navigation="refresh"))
    elif completed_count:
        rows.append(row(f"Show all records ({len(records)})", "/ps --all",
                        f"Includes {completed_count} executions with recorded outcomes.", navigation="refresh"))
    refresh_command = "/ps --all" if show_all else "/ps"
    rows.extend(
        [
            row(
                "Reload records",
                refresh_command,
                "Reread execution history; does not check process liveness or resume the model.",
                navigation="refresh",
            ),
            row(
                "Stop the model loop",
                "/stop",
                "Background commands may continue; this does not terminate them.",
                navigation="leave",
            ),
        ]
    )
    return {**selection("Background processes", rows,
                     f"{len(unresolved)} unresolved · {completed_count} recorded outcomes. "
                     "Last recorded execution states; current liveness is unverified. "
                     "Stopping the model loop does not confirm that background commands have ended.",
                     view_id="processes.all" if show_all else "processes.unresolved"),
            "preserve_selected_description": True}


def memory_panel(bridge: Any, tokens: list[str]) -> dict[str, Any]:
    if len(tokens) == 1:
        return selection(
            "Memory",
            [
                row("Overview", "/memory status", "Show loaded instruction layers."),
                row(
                    "Reload",
                    "/memory reload",
                    "Reread instruction memory for subsequent prompts.",
                ),
                row("Edit user memory", "/memory edit user", "Open the user OWNPI.md."),
                row(
                    "Edit project memory",
                    "/memory edit project",
                    "Open project instruction memory.",
                ),
            ],
            "The current Runtime loads user and project OWNPI.md layers; nested project OWNPI.md is directory-scoped.",
        )
    action = tokens[1]
    if action == "edit":
        target = tokens[2] if len(tokens) == 3 else "user"
        if target not in {"user", "project"}:
            raise ValueError(
                "Current Runtime supports user/project OWNPI.md; local overrides use nested project OWNPI.md."
            )
        path = (
            bridge.paths.app_home if target == "user" else bridge.project_root
        ) / "OWNPI.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("# OWNPI.md\n\n")
        return {"presenter": "editor", "title": "Edit memory", "path": str(path)}
    if action not in {"status", "reload"}:
        raise ValueError("Usage: /memory [status|reload|edit user|project]")
    discovered = discover_instructions(
        project_root=bridge.project_root,
        app_home=bridge.paths.app_home,
        current_working_directory=bridge.project_root,
    )
    body = (
        "Memory reread. Changes apply to subsequent prompt assembly.\n\n"
        if action == "reload"
        else ""
    )
    body += "\n\n".join(
        f"## {item.scope} · {item.locator}\n\n{item.content}" for item in discovered
    )
    return document("Instruction memory", body)


def audit_panel(bridge: Any) -> dict[str, Any]:
    if not bridge.run_dir:
        return document("Provider audit", "No run selected.")
    from ownpi.runtime.payload_redaction import redact_payload

    projection = bridge.projector.read_run(bridge.run_dir)
    body = (
        "# Source diagnostics\n\n"
        + ("\n\n".join(projection.warnings) or "No source warnings.")
        + "\n\n# Canonical response receipts\n\n"
    )
    for item in projection.items:
        if item["kind"] == "model_activity":
            body += item["payload"].get("transcript", "") + "\n\n"
    if bridge.run_dir.name in bridge.backend_replies:
        body += (
            "\n# Backend result reply\n```json\n"
            + json.dumps(
                redact_payload(bridge.backend_replies[bridge.run_dir.name]),
                ensure_ascii=False,
                indent=2,
            )
            + "\n```"
        )
    path = bridge.run_dir / "diagnostics/runtime.jsonl"
    body += "\n# Backend diagnostics\n\nBest-effort diagnostics do not decide execution or delivery status.\n\n"
    if path.exists():
        records = []
        for line in path.read_text().splitlines():
            try:
                records.append(redact_payload(json.loads(line)))
            except ValueError:
                continue
        body += (
            "```json\n"
            + json.dumps(records[-200:], indent=2, ensure_ascii=False)
            + "\n```"
        )
    else:
        body += "No backend diagnostics were recorded."
    body += bridge.stream_delivery.audit()
    return document("Provider audit", body)


def extensions_panel(bridge: Any, tokens: list[str]) -> dict[str, Any]:
    """Inspect current discovery; never load a second hook/command runtime."""
    from ownpi.chat.extension_status_surfaces import render_extensions_status
    from ownpi.extensions.discovery import discover_extensions
    from ownpi.tui_bridge.menu_views import menu, open_row

    action = tokens[1] if len(tokens) > 1 else ""
    if action not in {"", "status", "hooks", "reload"}:
        raise ValueError("Unknown extension menu action")
    if action in {"status", "hooks", "reload"}:
        snapshot = discover_extensions(
            bridge.paths,
            project_root=bridge.project_root,
            trust_config=bridge.settings.config.runtime.extension_trust,
        )
        if action == "status":
            return {
                **document("Extensions overview", render_extensions_status(snapshot)),
                "view_id": "extensions.overview",
            }
        if action == "hooks":
            body = "# Hook packages\n\n" + (
                "\n\n".join(
                    f"- **{package.source}** · {package.trust_status} · {'Enabled' if package.enabled else 'Disabled'}\n  {package.path}\n  Events: {package.event_count} · Hooks: {package.hook_count}"
                    for package in snapshot.hook_packages
                )
                or "No hook packages discovered."
            )
            body += (
                "\n\n# Configured hooks\n```json\n"
                + json.dumps(
                    snapshot.hooks_config.to_dict(), ensure_ascii=False, indent=2
                )
                + "\n```"
            )
            return {**document("Hooks", body), "view_id": "extensions.hooks"}
    rows = [
        open_row(
            "status",
            "Overview",
            "Show skills, commands, hooks, and plugins.",
            "/extensions status",
        ),
        open_row("skills", "Skills", "Open the native skill manager.", "/skills"),
        open_row(
            "commands",
            "Commands",
            "Open the searchable command browser.",
            "/commands",
            local=True,
        ),
        open_row(
            "hooks",
            "Hooks",
            "Inspect runtime hooks by source and event.",
            "/extensions hooks",
        ),
        open_row(
            "reload", "Reload", "Refresh extension discovery.", "/extensions reload"
        ),
    ]
    rows[-1]["action"]["params"]["menu_navigation"] = "refresh"
    result = menu(
        "extensions",
        "Extensions",
        "Inspect discovered capabilities or reload",
        rows,
        footer="Enter select   Esc back",
    )
    if action == "reload":
        bridge.emit("skills", {"skills": bridge.settings.skills()})
        result["footer_hint"] = "Discovery refreshed · Enter select   Esc back"
    return result
