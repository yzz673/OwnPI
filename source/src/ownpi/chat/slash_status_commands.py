from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from typing import Any, Callable

from ownpi.app.paths import AppPaths
from ownpi.chat.command_input import PickerCandidate, prompt_pick_candidate
from ownpi.chat.extension_status_surfaces import (
    render_commands_list,
    render_extensions_status,
    render_hooks_status,
    render_skills_list,
)
from ownpi.schemas.runtime import UserConfig
from ownpi.skills.manager import (
    SkillInstallSpec,
    install_skill,
    list_available_skills,
    list_installed_skills,
    parse_github_url,
    remove_skill,
    set_skill_enabled,
    toggle_skill_enabled,
)

ListInstalledSkills = Callable[..., Any]
ToggleSkillEnabled = Callable[..., Any]
SetSkillEnabled = Callable[..., Any]
RemoveSkill = Callable[..., Any]
InstallSkill = Callable[..., Any]
ListAvailableSkills = Callable[..., Any]


def render_skills_usage() -> str:
    return "\n".join(
        [
            "# Skills",
            "",
            "## Choose an action",
            "1. List skills — Tip: press $ to open this list directly.",
            "2. Enable/Disable Skills — Enable or disable skills.",
            "",
            "## Commands",
            "- /skills list",
            "- /skills available [--channel curated|experimental] [--repo <owner/repo>] [--path <repo/path>] [--ref <ref>] [--json]",
            "- /skills enable <name>",
            "- /skills disable <name>",
            "- /skills toggle <name>",
            "- /skills install --from-path <path> [--name <name>]",
            "- /skills install --github-url <url> [--name <name>]",
            "- /skills install --github-repo <owner/repo> --path <repo/path> [--ref <ref>] [--name <name>]",
            "- /skills remove <name>",
        ]
    )


def render_skill_install_result(info: dict[str, str]) -> str:
    return "\n".join(
        [
            "# Skill Installed",
            "",
            f"- **Name:** {info['name']}",
            f"- **Source:** {info['source']}",
            f"- **Path:** {info['path']}",
            f"- **Description:** {info['description']}",
            "",
        ]
    )


def render_skill_remove_result(info: dict[str, str]) -> str:
    return "\n".join(
        [
            "# Skill Removed",
            "",
            f"- **Name:** {info['name']}",
            f"- **Removed path:** {info['removed_path']}",
            "",
        ]
    )


def render_skill_toggle_result(info: dict[str, str | bool]) -> str:
    status = "enabled" if info.get("enabled", True) else "disabled"
    return "\n".join(
        [
            "# Skill Updated",
            "",
            f"- **Name:** {info['name']}",
            f"- **Status:** {status}",
            f"- **Source:** {info['source']}",
            f"- **Path:** {info['path']}",
            "",
        ]
    )


def handle_skills_action_picker(
    resolved_app_paths: AppPaths,
    *,
    user_config: UserConfig,
    project_root: Path | None = None,
    list_installed_skills_fn: ListInstalledSkills = list_installed_skills,
    toggle_skill_enabled_fn: ToggleSkillEnabled = toggle_skill_enabled,
) -> str | None:
    options = [
        PickerCandidate(
            label="1. List skills",
            insert_text="list",
            meta="Tip: press $ to open this list directly.",
            kind="action",
        ),
        PickerCandidate(
            label="2. Enable/Disable Skills",
            insert_text="toggle",
            meta="Enable or disable skills.",
            kind="action",
        ),
    ]
    selected = prompt_pick_candidate(
        options,
        title="Skills",
        theme_setting=str(user_config.runtime.ui.theme),
        use_terminal_background=bool(user_config.runtime.ui.use_terminal_background),
    )
    if selected is None:
        return None
    if selected.insert_text == "list":
        return render_skills_list(resolved_app_paths, project_root=project_root)
    toggled = handle_skill_toggle_picker(
        resolved_app_paths,
        user_config=user_config,
        project_root=project_root,
        list_installed_skills_fn=list_installed_skills_fn,
        toggle_skill_enabled_fn=toggle_skill_enabled_fn,
    )
    if toggled is None:
        return None
    return toggled


def handle_skill_toggle_picker(
    resolved_app_paths: AppPaths,
    *,
    user_config: UserConfig,
    project_root: Path | None = None,
    list_installed_skills_fn: ListInstalledSkills = list_installed_skills,
    toggle_skill_enabled_fn: ToggleSkillEnabled = toggle_skill_enabled,
) -> str | None:
    installed = list_installed_skills_fn(resolved_app_paths, project_root=project_root)
    if not installed:
        return "# Skills\n\n_No skills installed._\n"
    candidates = [
        PickerCandidate(
            label=f"{'enabled' if item.enabled else 'disabled'} {item.name}",
            insert_text=item.name,
            meta=f"Source: {item.source}",
            kind="skill",
        )
        for item in installed
    ]
    label_map = {item.name: item for item in installed}
    selected = prompt_pick_candidate(
        candidates,
        title="Enable/Disable Skills",
        theme_setting=str(user_config.runtime.ui.theme),
        use_terminal_background=bool(user_config.runtime.ui.use_terminal_background),
    )
    if selected is None:
        return None
    item = label_map[selected.insert_text]
    updated = toggle_skill_enabled_fn(resolved_app_paths, item.name, project_root=project_root)
    return render_skill_toggle_result(updated.to_dict())


def handle_skills_command(
    user_message: str,
    resolved_app_paths: AppPaths,
    *,
    user_config: UserConfig | None = None,
    project_root: Path | None = None,
    list_installed_skills_fn: ListInstalledSkills = list_installed_skills,
    toggle_skill_enabled_fn: ToggleSkillEnabled = toggle_skill_enabled,
    set_skill_enabled_fn: SetSkillEnabled = set_skill_enabled,
    remove_skill_fn: RemoveSkill = remove_skill,
    install_skill_fn: InstallSkill = install_skill,
    list_available_skills_fn: ListAvailableSkills = list_available_skills,
) -> str:
    try:
        tokens = shlex.split(user_message)
    except ValueError as exc:
        return f"Failed to parse /skills command: {exc}"

    if len(tokens) == 1:
        if sys.stdin.isatty() and sys.stdout.isatty():
            resolved_config = user_config or UserConfig.from_dict({})
            picked = handle_skills_action_picker(
                resolved_app_paths,
                user_config=resolved_config,
                project_root=project_root,
                list_installed_skills_fn=list_installed_skills_fn,
                toggle_skill_enabled_fn=toggle_skill_enabled_fn,
            )
            if picked is None:
                return "# Skills\n\n_Action cancelled._\n"
            return picked
        return render_skills_usage()

    action = tokens[1]
    if action == "list":
        return render_skills_list(resolved_app_paths, project_root=project_root)

    if action == "available":
        options = parse_skills_available_tokens(tokens[2:])
        available = list_available_skills_fn(
            resolved_app_paths,
            channel=options["channel"],
            repo=options["repo"],
            ref=options["ref"],
            path=options["path"],
        )
        payload = [item.to_dict() for item in available]
        if options["json"]:
            return json.dumps(payload, indent=2, sort_keys=True)
        lines = [f"# Available Skills: {options['channel']}", ""]
        for index, item in enumerate(payload, start=1):
            flags = [key for key in ("installed", "disabled", "shadowed") if item[key]]
            suffix = f" ({', '.join(flags)})" if flags else ""
            lines.append(f"{index}. {item['name']}{suffix}")
        lines.extend(["", f"Source: {options['repo']}@{options['ref']}:{options['path'] or options['channel']}"])
        return "\n".join(lines)

    if action in {"enable", "disable", "toggle"}:
        if len(tokens) < 3:
            return f"Usage: /skills {action} <name>"
        skill_name = " ".join(tokens[2:])
        if action == "toggle":
            updated = toggle_skill_enabled_fn(resolved_app_paths, skill_name, project_root=project_root)
        else:
            updated = set_skill_enabled_fn(
                resolved_app_paths,
                skill_name,
                action == "enable",
                project_root=project_root,
            )
        return render_skill_toggle_result(updated.to_dict())

    if action == "remove":
        if len(tokens) < 3:
            return "Usage: /skills remove <name>"
        removed = remove_skill_fn(resolved_app_paths, tokens[2])
        return render_skill_remove_result(removed.to_dict())

    if action == "install":
        spec = parse_skills_install_tokens(tokens[2:])
        installed = install_skill_fn(resolved_app_paths, spec)
        return render_skill_install_result(installed.to_dict())

    return render_skills_usage()


def parse_skills_install_tokens(tokens: list[str]) -> SkillInstallSpec:
    from_path: str | None = None
    github_url: str | None = None
    github_repo: str | None = None
    repo_path: str | None = None
    ref = "main"
    name: str | None = None

    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--from-path":
            index += 1
            if index >= len(tokens):
                raise ValueError("Missing value after --from-path")
            from_path = tokens[index]
        elif token == "--github-url":
            index += 1
            if index >= len(tokens):
                raise ValueError("Missing value after --github-url")
            github_url = tokens[index]
        elif token == "--github-repo":
            index += 1
            if index >= len(tokens):
                raise ValueError("Missing value after --github-repo")
            github_repo = tokens[index]
        elif token == "--path":
            index += 1
            if index >= len(tokens):
                raise ValueError("Missing value after --path")
            repo_path = tokens[index]
        elif token == "--ref":
            index += 1
            if index >= len(tokens):
                raise ValueError("Missing value after --ref")
            ref = tokens[index]
        elif token == "--name":
            index += 1
            if index >= len(tokens):
                raise ValueError("Missing value after --name")
            name = tokens[index]
        else:
            raise ValueError(f"Unknown /skills install option: {token}")
        index += 1

    sources = [item for item in (from_path, github_url, github_repo) if item]
    if len(sources) != 1:
        raise ValueError("Use exactly one of --from-path, --github-url, or --github-repo")
    if from_path:
        return SkillInstallSpec(source_type="local", source=from_path, name=name)
    if github_url:
        parsed = parse_github_url(github_url)
        return SkillInstallSpec(
            source_type=parsed.source_type,
            source=parsed.source,
            ref=parsed.ref,
            path=parsed.path,
            name=name,
        )
    if not repo_path:
        raise ValueError("--github-repo requires --path")
    return SkillInstallSpec(source_type="github", source=github_repo or "", ref=ref, path=repo_path, name=name)


def parse_skills_available_tokens(tokens: list[str]) -> dict[str, str | bool | None]:
    options: dict[str, str | bool | None] = {
        "channel": "curated",
        "repo": "openai/skills",
        "path": None,
        "ref": "main",
        "json": False,
    }
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--json":
            options["json"] = True
            index += 1
            continue
        mapping = {"--channel": "channel", "--repo": "repo", "--path": "path", "--ref": "ref"}
        key = mapping.get(token)
        if key is None:
            raise ValueError(f"Unknown /skills available option: {token}")
        index += 1
        if index >= len(tokens):
            raise ValueError(f"Missing value after {token}")
        options[key] = tokens[index]
        index += 1
    return options


def render_extensions_reloaded(registrations: dict[str, Any], *, skipped_count: int) -> str:
    command_count = len(registrations.get('commands', ()))
    skill_count = len(registrations.get('skills', ()))
    mcp_count = len(registrations.get('mcp', ()))
    return "\n".join(
        [
            "# Extensions Reloaded",
            "",
            f"- **Commands refreshed:** {command_count}",
            f"- **Skills refreshed:** {skill_count}",
            f"- **MCP prompts refreshed:** {mcp_count}",
            f"- **Skipped/restricted:** {skipped_count}",
            "",
        ]
    )


def render_hooks_status_for_session(
    *,
    hook_engine: Any,
    snapshot: Any,
) -> str:
    return render_hooks_status(
        hook_engine=hook_engine,
        snapshot=snapshot,
    )


__all__ = [
    "handle_skill_toggle_picker",
    "handle_skills_action_picker",
    "handle_skills_command",
    "parse_skills_install_tokens",
    "parse_skills_available_tokens",
    "render_commands_list",
    "render_extensions_reloaded",
    "render_extensions_status",
    "render_hooks_status_for_session",
    "render_hooks_status",
    "render_skill_install_result",
    "render_skill_remove_result",
    "render_skill_toggle_result",
    "render_skills_list",
    "render_skills_usage",
]
