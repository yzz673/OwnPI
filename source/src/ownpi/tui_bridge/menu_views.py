"""Display-only menus over the current validated configuration.

Navigation and uncommitted selections are UI state. No model catalog, provider
probe, workflow transition or historical recovery logic belongs here.
"""

from __future__ import annotations

from typing import Any

NEXT_RUN = "Execution settings apply to subsequent runs."
MODEL_FOOTER = "Enter to confirm · Esc to go back · ↑/↓ or j/k to move"
FIELD_LABELS = {
    "auto_compact_limit_tokens_override": "Auto compact limit",
    "response_reserve_tokens_override": "Response reserve",
    "context_safety_margin_tokens": "Safety margin",
    "request_max_retries": "Provider request retries",
    "stream_max_retries": "Provider stream retries",
}
FIELD_VALUES = {
    "auto_compact_limit_tokens_override": [None, 32000, 64000, 128000],
    "response_reserve_tokens_override": [None, 4000, 8000, 16000],
    "context_safety_margin_tokens": [None, 1024, 2048, 4096],
    "request_max_retries": [None, 0, 1, 2, 3],
    "stream_max_retries": [None, 0, 1, 2, 3],
}
PERMISSIONS = {
    "ask_for_approval": (
        "Ask for approval",
        "OwnPI can read and edit files in the current workspace and run commands. Approval is required to access the internet or edit other files.",
    ),
    "approve_for_me": (
        "Approve for me",
        "Only ask for actions detected as potentially unsafe.",
    ),
    "full_access": (
        "Full Access",
        "OwnPI can edit files outside this workspace and access the internet without asking for approval. Exercise caution when using.",
    ),
}


def menu(
    view_id: str,
    title: str,
    subtitle: str,
    rows: list[dict],
    *,
    footer: str = "Enter open   Esc back",
    **extra: Any,
) -> dict:
    return {
        "presenter": "selection",
        "view_id": view_id,
        "title": title,
        "subtitle": subtitle,
        "rows": rows,
        "footer_hint": footer,
        **extra,
    }


def action_row(
    key: str, name: str, description: str, method: str, params: dict, **extra: Any
) -> dict:
    return {
        "id": key,
        "name": name,
        "description": description,
        "dismiss_on_select": False,
        "action": {"method": method, "params": params},
        **extra,
    }


def open_row(
    key: str,
    name: str,
    description: str,
    text: str,
    *,
    context: dict | None = None,
    local: bool = False,
) -> dict:
    return action_row(
        key,
        name,
        description,
        "local" if local else "command",
        {"text": text, "menu_navigation": "push", "menu_context": context or {}},
    )


def setting_row(
    key: str,
    name: str,
    description: str,
    field: str,
    value: Any,
    current: Any,
    **extra: Any,
) -> dict:
    return action_row(
        key,
        name,
        description,
        "configure",
        {
            "field": field,
            "value": value,
            "menu_commit": True,
        },
        is_current=value == current,
        **extra,
    )


def model_menu(settings: Any, page: str, context: dict) -> dict:
    current = settings.public()
    profile = context.get("profile", current["profile"])
    if profile not in settings.config.selectable_llm_profiles:
        raise ValueError("Unknown model profile")
    llm = settings.config.get_llm_config(profile)
    models = list(dict.fromkeys([llm.model, *llm.model_options]))
    selected = context.get("model", llm.model)
    if selected not in models:
        raise ValueError("Choose a declared model option")
    draft = {"profile": profile, "model": selected}
    if page in {"model", "model_all"}:
        choices = models if page == "model_all" else models[:2]
        rows = [
            open_row(
                model,
                model,
                "Configured model in this profile",
                "/model_reasoning",
                context={"profile": profile, "model": model},
            )
            for model in choices
        ]
        for row in rows:
            row["is_current"] = (
                profile == current["profile"] and row["id"] == current["model"]
            )
            row["is_default"] = row["id"] == llm.model
        if page == "model":
            rows.append(
                open_row(
                    "all",
                    "All models",
                    "Browse every model declared in the active profile.",
                    "/model_all",
                    context={"profile": profile},
                )
            )
            if len(settings.config.selectable_llm_profiles) > 1:
                rows.append(
                    open_row(
                        "profiles",
                        "Model profile",
                        f"Active profile: {profile}",
                        "/model_profiles",
                    )
                )
        return menu(
            "model." + page,
            "Select model" if page == "model" else "Select model and reasoning",
            f"Active profile: {profile}",
            rows,
            footer=MODEL_FOOTER,
        )
    if page == "model_profiles":
        rows = []
        for name in settings.config.selectable_llm_profiles:
            config = settings.config.get_llm_config(name)
            row = open_row(
                name,
                name,
                f"{config.provider} · {config.model}",
                "/model",
                context={"profile": name},
            )
            row["is_current"] = name == current["profile"]
            rows.append(row)
        return menu(
            "model.profiles",
            "Select model profile",
            "Choose a profile, then its model and reasoning level.",
            rows,
            footer=MODEL_FOOTER,
        )
    # Classification controls presentation only; availability comes exclusively
    # from reasoning_options. Never infer a provider/model's capabilities.
    standard = {"none", "minimal", "low", "medium", "high"}
    options = list(dict.fromkeys(llm.reasoning_options))
    advanced = [value for value in options if value not in standard]
    values = (
        advanced if page == "model_advanced" else [v for v in options if v in standard]
    )
    descriptions = {
        "none": "No additional reasoning effort.",
        "minimal": "Minimal reasoning effort.",
        "low": "Fast responses for straightforward tasks.",
        "medium": "Balance reasoning depth and latency.",
        "high": "More reasoning for complex tasks.",
    }
    rows = []
    for value in values:
        row = action_row(
            value,
            value.title(),
            descriptions.get(
                value,
                "Advanced level declared in this configuration; may increase latency and cost.",
            ),
            "configure",
            {
                "field": "model_selection",
                "value": {**draft, "reasoning_effort": value},
                "menu_commit": True,
            },
            is_current=profile == current["profile"]
            and selected == current["model"]
            and value == current["reasoning_effort"],
            is_default=value == llm.reasoning_effort,
        )
        rows.append(row)
    if page != "model_advanced" and advanced:
        rows.append(
            open_row(
                "advanced",
                "Advanced reasoning…",
                "Open high-resource reasoning levels explicitly.",
                "/model_advanced",
                context=draft,
            )
        )
    if not options:
        rows.append(
            action_row(
                "configured",
                "Use configured reasoning",
                "No selectable reasoning levels are declared. Keep the configured value.",
                "configure",
                {
                    "field": "model_selection",
                    "value": {**draft, "reasoning_effort": llm.reasoning_effort},
                    "menu_commit": True,
                },
            )
        )
    return menu(
        "model." + page,
        "Select advanced reasoning"
        if page == "model_advanced"
        else "Select reasoning level",
        "Advanced levels can significantly increase latency and cost."
        if page == "model_advanced"
        else f"Reasoning for {selected} · {NEXT_RUN}",
        rows,
        footer=MODEL_FOOTER,
        selected_row_id=(
            llm.reasoning_effort
            if llm.reasoning_effort in values
            else "advanced"
            if page != "model_advanced" and llm.reasoning_effort in advanced
            else None
        ),
    )


def build(settings: Any, name: str, context: dict | None = None) -> dict:
    context = context or {}
    if not isinstance(context, dict):
        raise ValueError("Invalid menu context")
    public, prefs = settings.public(), settings.preferences
    if name in {
        "model",
        "model_all",
        "model_profiles",
        "model_reasoning",
        "model_advanced",
        "reasoning",
    }:
        return model_menu(
            settings, "model_reasoning" if name == "reasoning" else name, context
        )
    if name in {"permissions", "permissions_confirm"}:
        if name == "permissions_confirm":
            rows = [
                setting_row(
                    "once",
                    "Yes, continue anyway",
                    "Apply full access for subsequent runs.",
                    "permissions",
                    "full_access",
                    None,
                ),
                setting_row(
                    "remember",
                    "Yes, and don't ask again",
                    "Enable full access and remember this menu choice.",
                    "permission_selection",
                    {"preset": "full_access", "remember": True},
                    None,
                ),
                action_row(
                    "cancel",
                    "Cancel",
                    "Go back without enabling full access.",
                    "local",
                    {"text": "/menu_back"},
                ),
            ]
            return menu(
                "permissions.confirm",
                "Enable full access?",
                PERMISSIONS["full_access"][1],
                rows,
                footer="Press enter to confirm or esc to go back",
            )
        rows = []
        for value, (label, description) in PERMISSIONS.items():
            if value == "full_access" and not prefs.get(
                "full_access_confirmation_dismissed", False
            ):
                row = open_row(value, label, description, "/permissions_confirm")
                row["is_current"] = value == public["permissions"]
            else:
                row = setting_row(
                    value,
                    label,
                    description,
                    "permissions",
                    value,
                    public["permissions"],
                )
            rows.append(row)
        return menu(
            "permissions",
            "Update Model Permissions",
            NEXT_RUN,
            rows,
            footer="Press enter to confirm or esc to go back",
        )
    if name == "theme":
        return menu(
            "appearance",
            "Terminal appearance",
            "Choose how OwnPI uses the terminal palette.",
            [
                setting_row(
                    v,
                    v.title(),
                    {
                        "auto": "Follow the terminal colors.",
                        "dark": "Use a dark interface palette.",
                        "light": "Use a light interface palette.",
                    }[v],
                    "theme",
                    v,
                    public["theme"],
                )
                for v in ("auto", "dark", "light")
            ],
            footer="Enter apply   Esc back",
        )
    if name == "settings":
        entries = [
            ("model", "Model", "Choose the active model", public["model"]),
            (
                "reasoning",
                "Reasoning",
                "Choose reasoning effort for the active model",
                public["reasoning_effort"] or "Configured default",
            ),
            (
                "permissions",
                "Permissions",
                "Update model permissions",
                PERMISSIONS[public["permissions"]][0],
            ),
            (
                "theme",
                "Theme",
                "Choose syntax highlighting theme",
                prefs["syntax_theme"],
            ),
            (
                "raw",
                "Transcript rendering",
                "Choose rich or raw history",
                "Raw" if prefs["raw"] else "Rich",
            ),
            (
                "runtime",
                "Runtime level",
                "Choose Direct or Research for new tasks",
                {"task": "Direct", "research": "Research", "auto": "Automatic"}[public["runtime_profile"]],
            ),
            (
                "mode",
                "Interface mode",
                "Switch user or developer mode",
                prefs["interface_mode"],
            ),
            (
                "compaction",
                "Compaction",
                "Configure compaction and provider retries",
                "Enabled" if public["compaction"]["enabled"] else "Disabled",
            ),
            (
                "memory",
                "Memory",
                "Inspect or edit instruction memory",
                "User and project OWNPI.md",
            ),
            (
                "vim",
                "Vim editing",
                "Configure vim-style composer editing",
                "Enabled" if prefs["vim"] else "Disabled",
            ),
            (
                "extensions",
                "Extensions and hooks",
                "Inspect discovered capabilities",
                "Skills, commands and hooks",
            ),
            (
                "review_profile",
                "Review profile",
                "Choose review thoroughness for new runs",
                public["review_profile"],
            ),
            (
                "appearance",
                "Terminal appearance",
                "Choose automatic, light or dark interface colors",
                public["theme"],
            ),
            (
                "notifications",
                "Notifications",
                "Notify when work needs attention",
                "Enabled" if prefs["notifications"] else "Disabled",
            ),
        ]
        rows = [
            open_row(
                key,
                label,
                description,
                "/" + key,
                local=key in {"theme", "raw", "vim", "mode", "notifications"},
            )
            for key, label, description, _ in entries
        ]
        concise = {
            "model": "Choose active model",
            "reasoning": "Choose reasoning",
            "permissions": "Permission preset",
            "theme": "Syntax highlighting",
            "raw": "History rendering",
            "runtime": "Execution level",
            "mode": "Interface mode",
            "compaction": "Compaction",
            "memory": "Instruction memory",
            "vim": "Composer editing",
            "review_profile": "Review thoroughness",
            "appearance": "Interface colors",
            "notifications": "Terminal notifications",
        }
        for row, (key, _, description, value) in zip(rows, entries):
            shown = str(value)
            if len(shown) > 20:
                shown = shown[:19] + "…"
            row["selected_description"] = (
                f"{concise[key]} · {shown}" if key in concise else description
            )
        return menu(
            "settings",
            "Settings",
            "Runtime and interface configuration",
            rows,
            footer="Enter open   Esc cancel",
        )
    if name in {"runtime", "runtime_profile"}:
        labels = {"task": "Direct", "research": "Research", "auto": "Automatic (Direct)"}
        selected = public["runtime_profile"]
        current = {"direct": "Direct", "research": "Research", "historical": "Historical",
                   "unknown": "Unknown", "none": "None"}.get(context.get("current_mode", "none"), "Unknown")
        return menu(
            "runtime", "Runtime level",
            f"Current run: {current}. New tasks: {labels.get(selected, selected)}. Existing runs keep their original level.",
            [setting_row(value, label, description, "default_profile", value, selected)
             for value, label, description in [
                 ("task", "Direct", "Conversation with tools, review and follow-up messages."),
                 ("research", "Research", "Seven-stage research workflow with review."),
                 ("auto", "Automatic", "Use the default execution level (Direct)."),
             ]],
            selected_row_id=selected, footer="Enter apply for new tasks   Esc back",
        )
    if name == "review_profile":
        return menu(
            name, "Review profile", NEXT_RUN,
            [setting_row(value, label, description, name, value, public[name])
             for value, label, description in [
                 ("auto", "Automatic", "Use the configured review default."),
                 ("standard", "Standard", "Research: up to one revisit. Reviewer rounds are not capped."),
                 ("rigorous", "Rigorous", "Research: up to three revisits. Reviewer rounds are not capped."),
                 ("exhaustive", "Exhaustive", "Research: no revisit cap. Reviewer rounds are not capped."),
             ]], footer="Enter apply   Esc back",
        )
    if name == "compaction":
        compact = settings.config.runtime.native_conversation.conversation_compaction
        rows = [
            setting_row(
                "enabled",
                "Enabled",
                str(compact.enabled),
                "compaction_enabled",
                not compact.enabled,
                None,
            )
        ]
        for field, label in FIELD_LABELS.items():
            value = getattr(compact, field, getattr(settings.config.llm, field, None))
            rows.append(
                open_row(
                    field,
                    label,
                    str(value) if value is not None else "Model default",
                    "/compaction_field " + field,
                )
            )
        return menu(
            "compaction",
            "Compaction",
            "Native conversation compaction · settings apply to subsequent runs.",
            rows,
            footer="Enter select   Esc back",
        )
    if name.startswith("compaction_field:"):
        field = name.split(":", 1)[1]
        if field not in FIELD_VALUES:
            raise ValueError("Unsupported compaction field")
        value = getattr(
            settings.config.runtime.native_conversation.conversation_compaction,
            field,
            getattr(settings.config.llm, field, None),
        )
        return menu(
            "compaction." + field,
            FIELD_LABELS[field],
            "Choose a value to persist for subsequent runs.",
            [
                setting_row(
                    "default" if v is None else str(v),
                    "Model default" if v is None else str(v),
                    "current" if v == value else "",
                    field,
                    v,
                    value,
                )
                for v in FIELD_VALUES[field]
            ],
            footer="Enter apply   Esc back",
        )
    if name == "skills":
        enabled = any(s["enabled"] for s in settings.skills())
        rows = [
            action_row(
                "list",
                "List skills",
                "Insert $ and choose an enabled skill.",
                "local",
                {"text": "/skills_list"},
                disabled=not enabled,
                disabled_reason=None if enabled else "No enabled skills are available.",
                dismiss_on_select=True,
            ),
            action_row(
                "manage",
                "Enable/Disable Skills",
                "Search and toggle skills by their SKILL.md path.",
                "local",
                {"text": "/skills_manage"},
            ),
            action_row(
                "install",
                "Install a skill",
                "Install from a local path or GitHub URL.",
                "local",
                {"text": "/skills_install"},
            ),
            action_row(
                "remove",
                "Remove a skill",
                "Remove a user-installed skill by SKILL.md path.",
                "local",
                {"text": "/skills_remove"},
            ),
        ]
        return menu(
            "skills",
            "Skills",
            "Choose an action",
            rows,
            footer="Enter select   Esc back",
        )
    if name == "skills:remove":
        return menu(
            "skills.remove",
            "Remove Skills",
            "Select a user-installed skill.",
            [
                open_row(s["name"], s["name"], s["path"], "/skills remove " + s["name"])
                for s in settings.skills()
                if s["removable"]
            ],
        )
    raise ValueError(f"Unknown settings panel: {name}")
