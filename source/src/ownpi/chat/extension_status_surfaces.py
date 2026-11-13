from __future__ import annotations

from pathlib import Path
from typing import Any

from ownpi.app.paths import AppPaths
from ownpi.runtime.hooks.engine import HookEngine
from ownpi.skills.manager import inspect_installed_skills


def render_skills_list(resolved_app_paths: AppPaths, *, project_root: Path | None = None) -> str:
    status = inspect_installed_skills(resolved_app_paths, project_root=project_root)
    installed = [item.to_dict() for item in status.installed]
    skipped = [item.to_dict() for item in status.skipped]
    if not installed and not skipped:
        return "# Skills\n\n_No skills installed._\n"
    lines = ["# Skills", "", "## Visible skills"]
    if not installed:
        lines.append("_No visible skills._")
        lines.append("")
    for index, item in enumerate(installed, start=1):
        status = "enabled" if item.get("enabled", True) else "disabled"
        lines.extend(
            [
                f"### {index}. {item['name']}",
                f"- **Status:** {status}",
                f"- **Source:** {item['source']}",
                f"- **Path:** {item['path']}",
                f"- **Description:** {item['description']}",
                "",
            ]
        )
    if skipped:
        lines.extend(["## Skipped skills", ""])
        for item in skipped:
            lines.extend(
                [
                    f"- `{item['path']}`",
                    f"  - source: {item['source']}",
                    f"  - reason: {item['reason']}",
                ]
            )
    return "\n".join(lines)


def render_commands_list(definitions: tuple[Any, ...] | list[Any]) -> str:
    visible = [definition for definition in definitions if getattr(definition, 'user_invocable', True)]
    if not visible:
        return "# Commands\n\n_No commands available._\n"
    lines = ["# Commands", ""]
    for index, definition in enumerate(visible, start=1):
        argument_hint = str(getattr(definition, 'argument_hint', '') or '').strip()
        lines.extend(
            [
                f"### {index}. {definition.name}",
                f"- **Kind:** {getattr(definition, 'kind', 'local')}",
                f"- **Source:** {getattr(definition, 'source', 'builtin')}",
                f"- **Description:** {definition.description}",
            ]
        )
        if argument_hint:
            lines.append(f"- **Arguments:** {argument_hint}")
        trust_policy = getattr(definition, 'metadata', {}).get('trust_policy')
        if isinstance(trust_policy, dict):
            restricted_effects = trust_policy.get('restricted_effects')
            if isinstance(restricted_effects, list | tuple | set) and restricted_effects:
                lines.append(f"- **Restricted effects:** {', '.join(str(item) for item in restricted_effects)}")
        lines.append("")
    return "\n".join(lines)


def render_extensions_status(snapshot: Any) -> str:
    lines = ["# Extensions", ""]
    sources = list(getattr(snapshot, 'sources', ()) or ())
    hook_packages = list(getattr(snapshot, 'hook_packages', ()) or ())
    skills = list(getattr(snapshot, 'skills', ()) or ())
    commands = list(getattr(snapshot, 'command_definitions', ()) or ())
    skipped = list(getattr(snapshot, 'skipped', ()) or ())
    lines.extend(
        [
            "## Summary",
            f"- **Sources:** {len(sources)}",
            f"- **Skills:** {len(skills)}",
            f"- **Commands:** {len(commands)}",
            f"- **Hook packages:** {len(hook_packages)}",
            f"- **Skipped/restricted:** {len(skipped)}",
            "",
        ]
    )
    if sources:
        lines.extend(["## Sources", ""])
        for source in sources:
            trusted = 'trusted' if getattr(source, 'trusted', False) else 'restricted'
            lines.append(f"- `{getattr(source, 'source', 'unknown')}` - {trusted} - {getattr(source, 'root', '')}")
        lines.append("")
    if hook_packages:
        lines.extend(["## Hook Packages", ""])
        for package in hook_packages:
            trusted = getattr(package, 'trust_status', '') or (
                'trusted' if getattr(package, 'trusted', False) else 'restricted'
            )
            enabled = 'enabled' if getattr(package, 'enabled', False) else 'disabled'
            lines.append(
                f"- `{getattr(package, 'source', 'unknown')}` - {trusted}, {enabled}, "
                f"events={getattr(package, 'event_count', 0)}, hooks={getattr(package, 'hook_count', 0)}"
            )
            content_hash = str(getattr(package, 'content_hash', '') or '').strip()
            if content_hash:
                lines.append(f"  - content hash: `{content_hash}`")
        lines.append("")
    if skipped:
        lines.extend(["## Skipped / Restricted", ""])
        for item in skipped:
            lines.append(
                f"- `{getattr(item, 'kind', 'extension')}` `{getattr(item, 'source', 'unknown')}`: "
                f"{getattr(item, 'reason', '')} ({getattr(item, 'path', '')})"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_hooks_status(
    *,
    hook_engine: HookEngine,
    snapshot: Any,
) -> str:
    lines = ["# Hooks", ""]
    registrations = hook_engine.registry.snapshot
    lifecycle_events = hook_engine.observability.snapshot()
    event_names = {item.event_name for item in registrations}
    lines.extend(
        [
            "## Summary",
            f"- **Enabled:** {hook_engine.enabled}",
            f"- **Events:** {len(event_names)}",
            f"- **Handlers:** {len(registrations)}",
            f"- **Extension hook packages:** {len(getattr(snapshot, 'hook_packages', ()) or ())}",
            f"- **Recent lifecycle events:** {len(lifecycle_events)}",
            "",
        ]
    )
    lines.extend(["## Registry", ""])
    if not registrations:
        lines.append("_No registered Hook handlers._")
        lines.append("")
    else:
        for event_name in sorted(event_names):
            lines.append(f"### {event_name}")
            for registration in registrations:
                if registration.event_name != event_name:
                    continue
                active = registration.trust_status in {"managed", "trusted"}
                lines.append(
                    f"- `{registration.source}` `{registration.name}` - "
                    f"type={registration.handler_type}, mode={registration.execution_mode}, "
                    f"matcher=`{registration.matcher or '*'}`, trust={registration.trust_status}, "
                    f"status={'active' if active else 'inactive'}"
                )
            lines.append("")
    hook_packages = list(getattr(snapshot, 'hook_packages', ()) or ())
    if hook_packages:
        lines.extend(["## Discovered hook packages", ""])
        for package in hook_packages:
            trusted = getattr(package, 'trust_status', '') or (
                'trusted' if getattr(package, 'trusted', False) else 'restricted'
            )
            enabled = 'enabled' if getattr(package, 'enabled', False) else 'disabled'
            lines.append(
                f"- `{getattr(package, 'source', 'unknown')}` - {trusted}, {enabled} - {getattr(package, 'path', '')}"
            )
        lines.append("")
    if lifecycle_events:
        lines.extend(["## Recent Lifecycle", ""])
        for event in lifecycle_events:
            line = (
                f"- `{event.created_at}` run=`{event.run_id}` event=`{event.event_name}` "
                f"handler=`{event.handler_key}` status={event.status}"
            )
            if event.error:
                line += f" error={event.error}"
            lines.append(line)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
