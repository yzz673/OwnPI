"""UI commands backed by the current configuration and public services."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import shlex
from typing import Any

import yaml

from ownpi.app.paths import AppPaths
from ownpi.config.loader import load_user_config
from ownpi.schemas.runtime import UserConfig
from ownpi.skills.manager import (
    list_installed_skills,
    set_skill_enabled,
    install_skill,
    remove_skill,
    SkillInstallSpec,
    parse_github_url,
)
from ownpi.utils.io import write_text_atomic

CATALOG = [
    ("/subagents", "View subagent tasks (F2)"),
    ("/requests", "Review pending approvals and questions (F4)"),
    ("/model", "Choose the model or configured profile"),
    ("/reasoning", "Choose reasoning effort"),
    ("/direct", "Use Direct for new tasks"),
    ("/research", "Use Research for new tasks"),
    ("/runtime", "View and change the Direct / Research execution level"),
    ("/permissions", "Configure permissions for subsequent runs"),
    ("/settings", "Runtime and interface settings"),
    ("/resume", "Resume a canonical Runtime run"),
    ("/runs", "Browse canonical Runtime runs"),
    ("/project", "Show, open or create a project workspace"),
    ("/review", "Review a repository scope"),
    ("/skills", "Inspect and enable installed skills"),
    ("/memory", "Inspect instruction memory"),
    ("/status", "Inspect backend execution state"),
    ("/audit", "Inspect provider receipts and backend diagnostics"),
    ("/ps", "Inspect backend-owned processes"),
    ("/stop", "Stop the model loop; background commands may continue"),
    ("/diff", "Show the working-tree diff"),
    ("/copy", "Copy the latest response"),
    ("/raw", "Switch rich or raw transcript"),
    ("/theme", "Choose the terminal theme"),
    ("/compaction", "Configure context compaction for subsequent runs"),
    ("/clear", "Start a new chat in this project; keep files and history"),
    ("/commands", "Browse available commands"),
    ("/exit", "Stop active work and exit"),
]


class Configuration:
    def __init__(self, path: Path, paths: AppPaths, project_root: Path) -> None:
        self.path = path
        self.paths = paths
        self.project_root = project_root
        self.config = load_user_config(path)
        self.preference_path = paths.app_home / "tui" / "preferences.json"
        self.preferences = {
            "syntax_theme": "default",
            "raw": False,
            "vim": os.environ.get("OWNPI_VIM") == "1",
            "interface_mode": "user",
            "notifications": True,
            "full_access_confirmation_dismissed": False,
        }
        if self.preference_path.exists():
            loaded = json.loads(self.preference_path.read_text())
            for field, value in loaded.items():
                self.validate_preference(field, value)
            self.preferences.update(loaded)

    def public(self) -> dict[str, Any]:
        llm = self.config.llm
        return {
            "model": llm.model,
            "provider": llm.provider,
            "reasoning_effort": llm.reasoning_effort or "",
            "profile": self.config.selected_llm_profile or self.config.default_llm_name,
            "ready": llm.provider == "mock"
            or bool(llm.api_key_env and os.environ.get(llm.api_key_env)),
            "is_mock": llm.provider == "mock",
            "theme": self.config.runtime.ui.theme,
            "context_window_limit": llm.context_window_limit,
            "context_meter_enabled": self.config.runtime.ui.context_meter_enabled,
            "permissions": self.config.runtime.permissions.preset.value,
            "runtime_profile": self.config.runtime.default_profile,
            "review_profile": self.config.runtime.review_profile,
            "project_root": str(self.project_root),
            "config_path": str(self.path),
            "preferences": self.preferences,
            "compaction": self.config.runtime.native_conversation.conversation_compaction.to_dict(),
        }

    def update(self, field: str, value: Any) -> dict[str, Any]:
        if field in self.preferences:
            self.validate_preference(field, value)
            updated = {**self.preferences, field: value}
            self.preference_path.parent.mkdir(parents=True, exist_ok=True)
            write_text_atomic(self.preference_path, json.dumps(updated, indent=2))
            self.preferences = updated
            return {"config": self.public(), "message": "Interface preference saved."}
        numeric = {
            "context_safety_margin_tokens",
            "request_max_retries",
            "stream_max_retries",
            "auto_compact_limit_tokens_override",
            "response_reserve_tokens_override",
        }
        if (
            field in numeric
            and value is not None
            and (type(value) is not int or value < 0)
        ):
            raise ValueError("Expected a nonnegative integer or model default")
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Expected a configuration mapping")
        payload = deepcopy(raw)
        if field == "permission_selection":
            if not isinstance(value, dict) or value != {
                "preset": "full_access",
                "remember": True,
            }:
                raise ValueError("Invalid permission menu selection")
            result = self.update("permissions", "full_access")
            try:
                self.update("full_access_confirmation_dismissed", True)
            except OSError:
                result["message"] = (
                    "Full access saved. Could not remember the confirmation preference."
                )
            result["config"] = self.public()
            return result
        if field == "model_selection":
            if not isinstance(value, dict) or set(value) != {
                "profile",
                "model",
                "reasoning_effort",
            }:
                raise ValueError("Invalid model selection")
            profile = value["profile"]
            if profile not in self.config.selectable_llm_profiles:
                raise ValueError("Unknown model profile")
            llm = self.config.get_llm_config(profile)
            if value["model"] not in {llm.model, *llm.model_options}:
                raise ValueError("Choose a declared model option")
            if value["reasoning_effort"] not in {
                llm.reasoning_effort,
                *llm.reasoning_options,
            }:
                raise ValueError("Choose a declared reasoning option")
            payload.setdefault("llm", {})["profile"] = profile
            section = payload.get("llm_profiles", payload.get("llms"))
            target = section[profile] if section is not None else payload["llm"]
            target.update(
                model=value["model"], reasoning_effort=value["reasoning_effort"]
            )
        elif field == "profile":
            if value not in self.config.selectable_llm_profiles:
                raise ValueError("Unknown model profile")
            payload.setdefault("llm", {})["profile"] = value
        elif field in {
            "model",
            "reasoning_effort",
            "context_safety_margin_tokens",
            "request_max_retries",
            "stream_max_retries",
        }:
            llm = self.config.llm
            choices = (
                llm.model_options
                if field == "model"
                else llm.reasoning_options
                if field == "reasoning_effort"
                else []
            )
            if choices and value not in choices:
                raise ValueError(f"Choose a declared {field} option")
            if field in {"model", "reasoning_effort"} and (
                not isinstance(value, str) or not value.strip()
            ):
                raise ValueError(f"Invalid {field}")
            if "llm_profiles" in payload:
                selected = self.config.selected_llm_profile
                payload["llm_profiles"][selected][field] = value
            elif "llms" in payload:
                selected = (
                    self.config.selected_llm_profile or self.config.default_llm_name
                )
                payload["llms"][selected][field] = value
            else:
                payload["llm"][field] = value
        elif field in {"default_profile", "review_profile"}:
            payload.setdefault("runtime", {})[field] = value
        elif field == "permissions":
            payload.setdefault("runtime", {}).setdefault("permissions", {})[
                "preset"
            ] = value
        elif field == "theme":
            if value not in {"auto", "dark", "light"}:
                raise ValueError("Unknown theme")
            payload.setdefault("runtime", {}).setdefault("ui", {})["theme"] = value
        elif field in {
            "compaction_enabled",
            "auto_compact_limit_tokens_override",
            "response_reserve_tokens_override",
        }:
            if field == "compaction_enabled" and type(value) is not bool:
                raise ValueError("compaction_enabled must be a boolean")
            payload.setdefault("runtime", {}).setdefault(
                "native_conversation", {}
            ).setdefault("conversation_compaction", {})[
                "enabled" if field == "compaction_enabled" else field
            ] = value
        else:
            raise ValueError(f"Unsupported setting: {field}")
        validated = UserConfig.from_dict(payload)
        write_text_atomic(
            self.path, yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
        )
        self.config = validated
        return {
            "config": self.public(),
            "message": "Saved. Execution settings apply to subsequent runs.",
        }

    @staticmethod
    def validate_preference(field: str, value: Any) -> None:
        if (
            field
            in {"raw", "vim", "notifications", "full_access_confirmation_dismissed"}
            and type(value) is bool
        ):
            return
        if field == "interface_mode" and value in {"user", "developer"}:
            return
        if field == "syntax_theme" and value in {
            "default",
            "base16-ocean.dark",
            "base16-ocean.light",
            "base16-eighties.dark",
            "base16-mocha.dark",
            "InspiredGitHub",
            "Solarized (dark)",
            "Solarized (light)",
        }:
            return
        raise ValueError(f"Invalid interface preference: {field}")

    def skills(self) -> list[dict[str, Any]]:
        return [
            {
                **skill.to_dict(),
                "display_name": skill.name,
                "removable": skill.source == "app_home",
            }
            for skill in list_installed_skills(
                self.paths, project_root=self.project_root
            )
        ]

    def panel(self, name: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        from ownpi.tui_bridge.menu_views import build

        return build(self, name, context)

    def skill_action(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict) or payload.get("presenter") != "skills":
            raise ValueError("Invalid skill action")
        action, args = payload.get("action"), payload.get("arguments")
        if not isinstance(args, dict):
            raise ValueError("Invalid skill arguments")
        if action == "install":
            source = args.get("source")
            if not isinstance(source, str) or not source.strip():
                raise ValueError("Enter a skill source")
            if args.get("source_type") == "github_url":
                spec = parse_github_url(source)
            elif args.get("source_type") == "local":
                spec = SkillInstallSpec(
                    "local", str(Path(source).expanduser().resolve())
                )
            else:
                raise ValueError("Unknown skill source type")
            install_skill(self.paths, spec)
            return
        path = args.get("path")
        matches = [s for s in self.skills() if s["path"] == path]
        if len(matches) != 1:
            raise ValueError(
                "Skill path is no longer uniquely visible; refresh the catalog"
            )
        skill = matches[0]
        if action == "set_enabled" and type(args.get("enabled")) is bool:
            set_skill_enabled(
                self.paths,
                skill["name"],
                args["enabled"],
                project_root=self.project_root,
            )
        elif action == "remove" and skill["removable"]:
            # The manager's removal argument is an installed directory name,
            # while the visible catalog name may come from SKILL.md metadata.
            directory = Path(skill["path"]).resolve().parent
            if directory.parent != self.paths.skills_dir.resolve():
                raise ValueError("Only app-home skill installations can be removed")
            remove_skill(self.paths, directory.name)
        else:
            raise ValueError("Unknown skill action or skill is not removable")

    def skill_command(self, tokens: list[str]) -> dict[str, Any]:
        if len(tokens) == 1:
            return self.panel("skills")
        action = tokens[1]
        if action == "remove" and len(tokens) == 2:
            return self.panel("skills:remove")
        if len(tokens) != 3:
            raise ValueError(
                "Usage: /skills enable|disable|install|remove <name or path>"
            )
        target = tokens[2]
        if action in {"enable", "disable"}:
            set_skill_enabled(
                self.paths, target, action == "enable", project_root=self.project_root
            )
        elif action == "install":
            spec = (
                parse_github_url(target)
                if target.startswith("https://github.com/")
                else SkillInstallSpec("local", str(Path(target).expanduser().resolve()))
            )
            install_skill(self.paths, spec)
        elif action == "remove":
            if target not in {
                item["name"] for item in self.skills() if item["removable"]
            }:
                raise ValueError("Only app-home installed skills can be removed")
            return {
                "presenter": "selection",
                "title": "Remove skill?",
                "rows": [
                    {
                        "id": "remove",
                        "name": target,
                        "description": "Delete this installed skill directory",
                        "action": {
                            "method": "command",
                            "params": {
                                "text": shlex.join(
                                    ["/skills", "remove-confirm", target]
                                )
                            },
                        },
                    }
                ],
            }
        elif action == "remove-confirm":
            if target not in {
                item["name"] for item in self.skills() if item["removable"]
            }:
                raise ValueError("Skill is not removable")
            remove_skill(self.paths, target)
        else:
            raise ValueError("Unknown skill action")
        return {**self.panel("skills"), "message": "Skill catalog updated."}
