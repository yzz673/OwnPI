from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import contextmanager, nullcontext
from threading import RLock
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Any, Callable

from ownpi.agents.streaming import TextDeltaReceipt
from ownpi.app.paths import AppPaths
from ownpi.app.setup import inspect_llm_readiness
from ownpi.chat.answer_normalizer import normalize_human_answer_mapping
from ownpi.chat.admission import (
    NewMessageAdmission,
    select_new_message_admission,
)
from ownpi.chat.command_input import (
    DEFAULT_SLASH_COMMANDS,
    SlashCommandInput,
    slash_commands_from_summaries,
)
from ownpi.chat.command_registry import (
    CommandExecutionContext,
    CommandResult,
    build_default_command_registry,
)
from ownpi.chat.history import ChatTurn
from ownpi.chat.mcp_commands import sync_mcp_prompt_commands
from ownpi.chat.interaction_command_helpers import (
    human_dialog_emergency_fallback_message,
    is_clarify_capture_cancel_command,
    is_permission_capture_cancel_command,
    parse_clarification_answers,
    parse_tool_permission_decision,
    parse_tool_permission_response,
    pending_human_clarification_for_run,
    pending_tool_permission_for_run,
    render_pending_human_clarification,
    render_pending_tool_permission_panel,
)
from ownpi.chat.interaction_router import InteractionRouter
from ownpi.chat.extension_status_surfaces import render_commands_list, render_extensions_status, render_skills_list
from ownpi.chat.slash_status_commands import (
    handle_skill_toggle_picker,
    handle_skills_action_picker,
    handle_skills_command,
    parse_skills_install_tokens,
    render_extensions_reloaded,
    render_hooks_status_for_session,
    render_skill_install_result,
    render_skill_remove_result,
    render_skill_toggle_result,
    render_skills_usage,
)
from ownpi.chat.interaction_dialog import (
    DIALOG_FALLBACK,
    HumanDialogFallbackResult,
    focused_dialog_fallback_message,
    interaction_dialog_available,
    run_human_interaction_dialog,
)
from ownpi.chat.permission_dialog import PermissionDialogResult, run_permission_dialog
from ownpi.chat.live_status import build_direct_thinking_status, build_live_runtime_status
from ownpi.chat.model_picker import (
    get_model_candidates,
    get_reasoning_candidates,
    get_ui_mode_candidates,
    pick_model_interactively,
    pick_reasoning_interactively,
    pick_ui_mode_interactively,
)
from ownpi.chat.renderer import (
    render_available_models_markdown,
    render_event_message,
    render_event_rich_text,
    render_final_package_markdown,
    render_help,
    render_inline_round_artifact,
    render_runs_list,
    render_session_banner,
    render_session_banner_rich,
    render_session_status_markdown,
)
from ownpi.chat.theme import (
    build_rich_markdown_styles,
    get_ui_tokens,
    resolve_theme_setting,
)
from ownpi.controller import WorkflowEngine
from ownpi.controller.runtime_control import RuntimeControlService
from ownpi.controller.interaction_actions import CancelLocalCapture, RespondPermission, SubmitHumanAnswers
from ownpi.controller.interaction_controller import InteractionController
from ownpi.controller.run_store import (
    list_run_dirs,
)
from ownpi.controller.run_context import PreparedRunContext
from ownpi.controller.runtime_control_signal import submit_runtime_control
from ownpi.runtime.interactions.live_approval import pending_live_approvals
from ownpi.controller.pre_intake_bootstrap import prepare_pre_intake_bootstrap
from ownpi.controller.session_manager import RuntimeSessionManager
from ownpi.controller.workflow import run_fresh_admission
from ownpi.chat.workflow_commands import handle_workflow_command
from ownpi.hooks.builtin import build_builtin_hook_registrations
from ownpi.extensions.discovery import discover_extensions, sync_custom_commands
from ownpi.llm.factory import build_llm_client
from ownpi.runtime.hooks.builder import build_hook_engine
from ownpi.runtime.hooks.types import (
    HookDeliveryBatch,
    HookModelContextFragment,
    hook_request,
)
from ownpi.runtime.mcp.client_manager import close_persistent_mcp_connections
from ownpi.runtime.permissions.explainer import (
    build_permission_explanation,
    permission_preset_label,
    render_permission_explanation,
)
from ownpi.runtime.permissions.models import PermissionPreset
from ownpi.runtime.permissions.runtime_config import compile_runtime_permissions
from ownpi.runtime.sessions import is_session_run_dir
from ownpi.runtime.instructions import discover_instructions
from ownpi.runtime.state_models import MCPRuntimeState
from ownpi.schemas.runtime import AgentInstanceConfig, LLMConfig, HookRegistryConfig, UserConfig
from ownpi.schemas.workflow_plan import (
    ExecutionTier,
    StageRuntimeProfile,
    resolve_runtime_profile,
    runtime_profile_selection_from_value,
)
from ownpi.task_authoring.contracts import (
    TaskSpecAuthoringOutcome,
    TaskSpecAuthoringRequest,
)
from ownpi.task_authoring.service import TaskSpecAuthoringService
from ownpi.skills.manager import (
    build_enabled_skill_catalog,
    install_skill,
    list_installed_skills,
    remove_skill,
    set_skill_enabled,
    toggle_skill_enabled,
)
from ownpi.skills.mentions import is_explicit_skill_slash_message
from ownpi.utils.ansi import style_text


_UI_MODES = {"user", "developer"}
_IMPLICIT_REASONING_DEFAULTS = {
    "openai": "medium",
    "openai_compatible": "medium",
}

_render_commands_list = render_commands_list
_render_extensions_status = render_extensions_status
_render_hooks_status = render_hooks_status_for_session
_render_skills_list = render_skills_list
_human_dialog_emergency_fallback_message = human_dialog_emergency_fallback_message
_is_clarify_capture_cancel_command = is_clarify_capture_cancel_command
_is_permission_capture_cancel_command = is_permission_capture_cancel_command
_parse_clarification_answers = parse_clarification_answers
_parse_tool_permission_decision = parse_tool_permission_decision
_parse_tool_permission_response = parse_tool_permission_response
_pending_human_clarification_for_run = pending_human_clarification_for_run
_pending_tool_permission_for_run = pending_tool_permission_for_run
_render_pending_human_clarification = render_pending_human_clarification
_render_pending_tool_permission = render_pending_tool_permission_panel


@dataclass(frozen=True)
class ChatSurfacePolicy:
    show_interpreted_task: bool
    show_runtime_trace: bool
    show_verbose_result: bool


def _surface_policy(ui_mode: str) -> ChatSurfacePolicy:
    developer = ui_mode == 'developer'
    return ChatSurfacePolicy(
        show_interpreted_task=developer,
        show_runtime_trace=developer,
        show_verbose_result=developer,
    )


def _implicit_reasoning_effort(provider: str) -> str | None:
    return _IMPLICIT_REASONING_DEFAULTS.get(provider)


def _matches_command(message: str, command: str) -> bool:
    return message == command or message.startswith(f"{command} ")


_CAPTURE_PASSTHROUGH_COMMANDS = tuple(command.name for command in DEFAULT_SLASH_COMMANDS) + (
    '/clarify-run',
    '/permission-run',
    '/cancel-clarify',
    '/cancel-permission',
    '/run-task',
    '/summarize-pdf',
    '/review-task',
)


def _is_capture_passthrough_command(message: str) -> bool:
    return any(_matches_command(message, command) for command in _CAPTURE_PASSTHROUGH_COMMANDS)


def _resolve_active_llm_config(
    user_config: UserConfig,
    active_profile_name: str,
    model_override: str | None,
    reasoning_override: str | None,
) -> LLMConfig:
    base_config = user_config.get_llm_config(active_profile_name)
    updates: dict[str, str] = {}
    if model_override:
        updates["model"] = model_override
    effective_reasoning = (
        reasoning_override
        or base_config.reasoning_effort
        or _implicit_reasoning_effort(base_config.provider)
    )
    if effective_reasoning and effective_reasoning != base_config.reasoning_effort:
        updates["reasoning_effort"] = effective_reasoning
    if updates:
        return replace(base_config, **updates)
    return base_config


def _session_reviewer_profile_name(user_config: UserConfig, active_profile_name: str) -> str | None:
    candidate = f"reviewer_{active_profile_name}"
    try:
        return user_config.resolve_llm_name(candidate)
    except ValueError:
        return None


def _build_session_user_config(
    user_config: UserConfig,
    active_profile_name: str,
    model_override: str | None,
    reasoning_override: str | None,
) -> UserConfig:
    active_provider = user_config.get_llm_config(active_profile_name).provider
    active_config = _resolve_active_llm_config(
        user_config=user_config,
        active_profile_name=active_profile_name,
        model_override=model_override,
        reasoning_override=reasoning_override,
    )
    payload = user_config.to_dict()
    for profile_name, llm_payload in payload["llms"].items():
        provider = str(llm_payload.get("provider", ""))
        if reasoning_override and provider == active_provider:
            llm_payload["reasoning_effort"] = reasoning_override
        elif not llm_payload.get("reasoning_effort"):
            implicit = _implicit_reasoning_effort(provider)
            if implicit is not None:
                llm_payload["reasoning_effort"] = implicit
        payload["llms"][profile_name] = llm_payload
    payload["llms"][active_profile_name] = active_config.to_dict()
    runtime_payload = payload["runtime"]
    payload["runtime"] = runtime_payload
    llm_selector = payload.get("llm")
    if isinstance(llm_selector, dict) and user_config.selected_llm_profile:
        llm_selector["profile"] = active_profile_name
        payload["llm"] = llm_selector
    if active_profile_name != user_config.default_llm_name:
        payload["llms"]["default"] = active_config.to_dict()
        reviewer_profile_name = _session_reviewer_profile_name(user_config, active_profile_name)
        if reviewer_profile_name is not None:
            payload["llms"]["reviewer_model"] = user_config.get_llm_config(reviewer_profile_name).to_dict()
        else:
            payload["llms"]["reviewer_model"] = active_config.to_dict()
    return UserConfig.from_dict(payload)


def _try_switch_profile(user_config: UserConfig, token: str) -> str | None:
    try:
        return user_config.resolve_llm_name(token)
    except ValueError:
        return None


def _switch_active_llm(
    user_config: UserConfig,
    active_profile_name: str,
    model_override: str | None,
    reasoning_override: str | None,
) -> tuple[LLMConfig, Any]:
    active_llm_config = _resolve_active_llm_config(
        user_config=user_config,
        active_profile_name=active_profile_name,
        model_override=model_override,
        reasoning_override=reasoning_override,
    )
    llm_client = build_llm_client(active_llm_config)
    return active_llm_config, llm_client


def _print_startup_guidance(active_llm_config: LLMConfig) -> None:
    readiness = inspect_llm_readiness(active_llm_config)
    if readiness.is_mock:
        print(style_text("Warning: you are currently using the mock provider, not a real LLM.", color='yellow', bold=True))
        print("Run ownpi setup to configure your API key, base URL, and default model.\n")
        return
    if not readiness.has_api_key or not readiness.has_base_url:
        print(style_text("Warning: real LLM config is incomplete.", color='yellow', bold=True))
        print("Run ownpi setup to configure your API key, base URL, and default model.\n")


def _theme_kwargs(user_config: UserConfig) -> dict[str, object]:
    return {
        'theme_setting': str(user_config.runtime.ui.theme),
        'use_terminal_background': bool(user_config.runtime.ui.use_terminal_background),
    }


def _collect_recent_activity(user_config: UserConfig, project_root: Path) -> list[str]:
    output_dir = _resolve_output_dir(project_root, user_config)
    return [f'{run_dir.name}: {run_dir}' for run_dir in list_run_dirs(output_dir)[:3]]


def _print_startup_banner(
    *,
    user_config: UserConfig,
    active_profile_name: str,
    active_llm_config: LLMConfig,
    model_override: str | None,
    reasoning_override: str | None,
    ui_mode: str,
    project_root: Path,
) -> None:
    recent_activity = _collect_recent_activity(user_config, project_root)
    rich_banner = render_session_banner_rich(
        user_config=user_config,
        active_profile_name=active_profile_name,
        active_llm_config=active_llm_config,
        model_override=model_override,
        reasoning_override=reasoning_override,
        ui_mode=ui_mode,
        workspace_path=str(project_root),
        recent_activity=recent_activity,
        **_theme_kwargs(user_config),
    )
    if rich_banner is not None:
        try:
            from rich.console import Console

            Console().print(rich_banner)
            return
        except Exception:
            pass

    print(
        render_session_banner(
            user_config=user_config,
            active_profile_name=active_profile_name,
            active_llm_config=active_llm_config,
            model_override=model_override,
            reasoning_override=reasoning_override,
            ui_mode=ui_mode,
            workspace_path=str(project_root),
            recent_activity=recent_activity,
            **_theme_kwargs(user_config),
        )
    )


def _print_candidate_models(candidates: list[str], active_model: str) -> None:
    print("Candidate models:")
    for candidate in candidates:
        marker = "*" if candidate == active_model else " "
        print(f"{marker} {candidate}")
    print()


def _print_candidate_reasoning(candidates: list[str], active_reasoning: str | None) -> None:
    print("Candidate reasoning efforts:")
    for candidate in candidates:
        marker = "*" if candidate == active_reasoning else " "
        print(f"{marker} {candidate}")
    print()


def _build_input_prompt(ui_mode: str) -> str:
    return "dev ❯ " if ui_mode == 'developer' else "❯ "


def _render_final_package_for_ui(result, *, ui_mode: str) -> str:
    return render_final_package_markdown(result, ui_mode=ui_mode)


def _print_markdown_output(
    markdown_text: str,
    *,
    theme_setting: str = 'auto',
    use_terminal_background: bool = True,
) -> None:
    try:
        from rich.console import Console
        from rich.markdown import Markdown
        from rich.theme import Theme
    except ImportError:
        print(markdown_text)
        return

    tokens = get_ui_tokens(
        resolve_theme_setting(theme_setting),
        use_terminal_background=use_terminal_background,
    )
    Console(theme=Theme(build_rich_markdown_styles(tokens))).print(Markdown(markdown_text))


def _render_reasoning_candidates_markdown(candidates: list[str], current_reasoning: str | None) -> str:
    lines = [
        "# Reasoning Options",
        "",
        f"- **Current reasoning:** {current_reasoning or '<unset>'}",
        "",
        "## Candidates",
    ]
    for candidate in candidates:
        marker = " _(current)_" if candidate == current_reasoning else ""
        lines.append(f"- {candidate}{marker}")
    lines.append("")
    return "\n".join(lines)


def _render_ui_mode_markdown(current_mode: str, available_modes: list[str], *, selected_mode: str | None = None) -> str:
    lines = ["# UI Mode", ""]
    if selected_mode is not None:
        lines.append(f"- **Selected mode:** {selected_mode}")
    lines.append(f"- **Current mode:** {current_mode}")
    lines.append("")
    lines.append("## Available modes")
    for mode in available_modes:
        marker = " _(current)_" if mode == current_mode else ""
        lines.append(f"- {mode}{marker}")
    lines.append("")
    return "\n".join(lines)


def _render_permission_preset_choices(current: PermissionPreset) -> str:
    lines = ['## Permission Presets']
    for preset in PermissionPreset:
        marker = ' _(current)_' if preset is current else ''
        lines.append(f"- {permission_preset_label(preset)}{marker}")
    return '\n'.join(lines)


def _permission_preset_from_command(value: str) -> PermissionPreset | None:
    normalized = str(value or '').strip().casefold().replace('-', '_').replace(' ', '_')
    presets = {
        'ask_for_approval': PermissionPreset.ASK_FOR_APPROVAL,
        'approve_for_me': PermissionPreset.APPROVE_FOR_ME,
        'full_access': PermissionPreset.FULL_ACCESS,
    }
    return presets.get(normalized)


def _confirm_full_access_selection() -> bool:
    print('Full Access disables managed filesystem and network restrictions and approval prompts.')
    return input("Type 'Full Access' to confirm: ").strip() == 'Full Access'


def _user_config_with_permission_preset(
    user_config: UserConfig,
    preset: PermissionPreset,
) -> UserConfig:
    permissions = replace(
        user_config.runtime.permissions,
        preset=preset,
        active_profile=None,
    )
    return replace(
        user_config,
        runtime=replace(user_config.runtime, permissions=permissions),
    )


def _trusted_command_runtime_override(
    user_config: UserConfig,
    metadata: dict[str, Any],
    *,
    key: str,
    effect: str,
) -> str | None:
    if _command_runtime_effect_is_restricted(metadata, effect) and not _command_metadata_allows_restricted_effect(
        user_config,
        metadata,
        effect=effect,
    ):
        return None
    return _optional_metadata_string(metadata, key)


def _build_direct_thinking_status_for_ui(
    *,
    ui_mode: str,
    theme_setting: str,
    use_terminal_background: bool,
):
    try:
        return build_direct_thinking_status(
            ui_mode=ui_mode,
            theme_setting=theme_setting,
            use_terminal_background=use_terminal_background,
        )
    except TypeError:
        return build_direct_thinking_status(ui_mode=ui_mode)


def _handle_model_picker(
    user_config: UserConfig,
    active_profile_name: str,
    active_llm_config: LLMConfig,
) -> str | None:
    candidates = get_model_candidates(
        user_config=user_config,
        active_profile_name=active_profile_name,
        active_llm_config=active_llm_config,
    )
    selected = pick_model_interactively(
        candidates,
        active_llm_config.model,
        theme_setting=str(user_config.runtime.ui.theme),
        use_terminal_background=bool(user_config.runtime.ui.use_terminal_background),
    )
    if selected is not None:
        return selected

    if sys.stdin.isatty() and sys.stdout.isatty():
        print("Model selection cancelled.")
    else:
        print("Interactive picker is unavailable in this terminal. Use /model <name> instead.")
        _print_candidate_models(candidates, active_llm_config.model)
    return None


def _handle_reasoning_picker(
    user_config: UserConfig,
    active_profile_name: str,
    active_llm_config: LLMConfig,
) -> str | None:
    candidates = get_reasoning_candidates(
        user_config=user_config,
        active_profile_name=active_profile_name,
        active_llm_config=active_llm_config,
    )
    current = active_llm_config.reasoning_effort or "medium"
    selected = pick_reasoning_interactively(
        candidates,
        current,
        theme_setting=str(user_config.runtime.ui.theme),
        use_terminal_background=bool(user_config.runtime.ui.use_terminal_background),
    )
    if selected is not None:
        return selected

    if sys.stdin.isatty() and sys.stdout.isatty():
        print("Reasoning selection cancelled.")
    else:
        _print_markdown_output(
            _render_reasoning_candidates_markdown(candidates, current)
            + "Use /reasoning <level> to switch in a non-interactive terminal.\n",
            theme_setting=str(user_config.runtime.ui.theme),
            use_terminal_background=bool(user_config.runtime.ui.use_terminal_background),
        )
    return None


def _handle_ui_mode_picker(current_mode: str, *, user_config: UserConfig) -> str | None:
    candidates = get_ui_mode_candidates(current_mode)
    selected = pick_ui_mode_interactively(
        candidates,
        current_mode,
        theme_setting=str(user_config.runtime.ui.theme),
        use_terminal_background=bool(user_config.runtime.ui.use_terminal_background),
    )
    if selected is not None:
        return selected

    if sys.stdin.isatty() and sys.stdout.isatty():
        print("UI mode selection cancelled.")
    else:
        _print_markdown_output(
            _render_ui_mode_markdown(current_mode, candidates)
            + "Use /mode <name> to switch in a non-interactive terminal.\n",
            theme_setting=str(user_config.runtime.ui.theme),
            use_terminal_background=bool(user_config.runtime.ui.use_terminal_background),
        )
    return None


def _build_artifact_callback(ui_mode: str, *, user_config: UserConfig):
    if ui_mode != "developer":
        return None

    def _callback(
        round_label: str,
        agent_config: AgentInstanceConfig,
        artifact_type: str,
        payload: dict[str, Any],
        file_path: str,
    ) -> None:
        print(
            render_inline_round_artifact(
                round_label=round_label,
                agent_id=agent_config.id,
                role=agent_config.role,
                artifact_type=artifact_type,
                payload=payload,
                file_path=file_path,
                **_theme_kwargs(user_config),
            )
        )

    return _callback


def _handle_skills_command(
    user_message: str,
    resolved_app_paths: AppPaths,
    *,
    user_config: UserConfig | None = None,
    project_root: Path | None = None,
) -> str:
    return handle_skills_command(
        user_message,
        resolved_app_paths,
        user_config=user_config,
        project_root=project_root,
        list_installed_skills_fn=list_installed_skills,
        toggle_skill_enabled_fn=toggle_skill_enabled,
        set_skill_enabled_fn=set_skill_enabled,
        remove_skill_fn=remove_skill,
        install_skill_fn=install_skill,
    )


_render_skills_usage = render_skills_usage
_render_skill_install_result = render_skill_install_result
_render_skill_remove_result = render_skill_remove_result
_render_skill_toggle_result = render_skill_toggle_result
_parse_skills_install_tokens = parse_skills_install_tokens


def _handle_skills_action_picker(
    resolved_app_paths: AppPaths,
    *,
    user_config: UserConfig,
    project_root: Path | None = None,
) -> str | None:
    return handle_skills_action_picker(
        resolved_app_paths,
        user_config=user_config,
        project_root=project_root,
        list_installed_skills_fn=list_installed_skills,
        toggle_skill_enabled_fn=toggle_skill_enabled,
    )


def _handle_skill_toggle_picker(
    resolved_app_paths: AppPaths,
    *,
    user_config: UserConfig,
    project_root: Path | None = None,
) -> str | None:
    return handle_skill_toggle_picker(
        resolved_app_paths,
        user_config=user_config,
        project_root=project_root,
        list_installed_skills_fn=list_installed_skills,
        toggle_skill_enabled_fn=toggle_skill_enabled,
    )


def _resolve_output_dir(project_root: Path, user_config: UserConfig) -> Path:
    candidate = Path(user_config.runtime.output_dir).expanduser()
    if candidate.is_absolute():
        return candidate
    return project_root / candidate


@contextmanager
def _runtime_output_surface(user_config: UserConfig, *, ui_mode: str):
    surface_policy = _surface_policy(ui_mode)
    live_status = build_live_runtime_status(
        enabled=bool(user_config.runtime.ui.context_meter_enabled),
        mode=str(user_config.runtime.ui.context_meter_mode),
        ui_mode=ui_mode,
        meter_width=int(user_config.runtime.ui.context_meter_width),
        show_threshold=bool(user_config.runtime.ui.context_meter_show_threshold),
        theme_setting=str(user_config.runtime.ui.theme),
        use_terminal_background=bool(user_config.runtime.ui.use_terminal_background),
    )

    def _event_callback(message: str) -> None:
        if live_status.enabled:
            if surface_policy.show_runtime_trace:
                live_status.print_event(
                    render_event_rich_text(
                        message,
                        **_theme_kwargs(user_config),
                        tokens=live_status.theme_tokens,
                    )
                )
            return
        if surface_policy.show_runtime_trace:
            print(
                render_event_message(
                    message,
                    **_theme_kwargs(user_config),
                )
            )

    streamed_text_by_item: dict[tuple[str, int, str], TextDeltaReceipt] = {}

    def _status_callback(payload: dict[str, Any] | None) -> None:
        if not isinstance(payload, dict):
            return
        if payload.get('event_kind') == 'llm_text_delta':
            delta = str(payload.get('delta') or '')
            if not delta:
                return
            key = (
                str(payload.get('response_id') or ''),
                int(payload.get('provider_attempt', payload.get('attempt', 0)) or 0),
                str(payload.get('item_id') or ''),
            )
            if key[2]:
                streamed_text_by_item.setdefault(key, TextDeltaReceipt()).append(delta)
            if live_status.enabled:
                live_status.print_event(delta)
            else:
                print(delta, end='', flush=True)
            return
        if (
            payload.get('event_kind') == 'llm_item_completed'
            and payload.get('display') == 'user_text'
        ):
            key = (
                str(payload.get('response_id') or ''),
                int(payload.get('provider_attempt', payload.get('attempt', 0)) or 0),
                str(payload.get('item_id') or ''),
            )
            receipt = streamed_text_by_item.pop(key, TextDeltaReceipt())
            completed = str(payload.get('completed_text') or '')
            correction = receipt.completed_suffix(completed)
            if correction is None:
                correction = f'\n{completed}' if completed else ''
            if correction:
                if live_status.enabled:
                    live_status.print_event(correction)
                else:
                    print(correction, end='', flush=True)
            return
        if payload.get('canonical_event_type') == 'response_completed':
            streamed_text_by_item.clear()
            return
        if payload.get('event_kind') == 'llm_protocol_delta':
            if live_status.surface_mode != 'disabled':
                live_status.update(
                    {
                        key: value
                        for key, value in payload.items()
                        if key not in {'delta'}
                    }
                )
            return
        if payload.get('event_kind') in {
            'llm_reasoning_delta',
            'llm_tool_arguments_delta',
        }:
            if live_status.surface_mode != 'disabled':
                live_status.update(
                    {
                        key: value
                        for key, value in payload.items()
                        if key not in {'delta'}
                    }
                )
            return
        if payload.get('event_kind') == 'llm_stream_attempt_discarded':
            streamed_text_by_item.clear()
            if live_status.surface_mode != 'disabled':
                live_status.update(payload)
                return
            discarded_chars = int(payload.get('discarded_delta_char_count', 0) or 0)
            if discarded_chars > 0:
                print(
                    f"\n[stream] discarded {discarded_chars} streamed character(s) from a failed provider attempt.",
                    flush=True,
                )
            return
        if live_status.surface_mode != 'disabled':
            live_status.update(payload)

    status_callback = _status_callback
    with live_status:
        yield _event_callback, status_callback, live_status


def _resolve_run_dir_argument(token: str, *, project_root: Path, user_config: UserConfig) -> Path:
    output_dir = _resolve_output_dir(project_root, user_config)
    if token.isdigit():
        runs = list_run_dirs(output_dir)
        index = int(token) - 1
        if index < 0 or index >= len(runs):
            raise ValueError(f"Run index out of range: {token}")
        return runs[index]
    run_dir = Path(token).expanduser().resolve()
    if not is_session_run_dir(run_dir):
        raise ValueError(
            'Run path must be session-managed: '
            '<app_home>/sessions/<session_id>/tasks/<task_id>/runs/<run_id>'
        )
    return run_dir


def _result_run_dir(result: Any) -> Path | None:
    run_dir = (
        result.run_artifacts.get('run_dir')
        if isinstance(getattr(result, 'run_artifacts', None), dict)
        else None
    )
    return Path(str(run_dir)).expanduser().resolve() if run_dir else None


def _optional_metadata_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    text = str(value).strip() if value is not None else ''
    return text or None


def _print_hook_block_decision(decision: Any, *, fallback: str) -> None:
    metadata = decision.to_metadata() if hasattr(decision, 'to_metadata') else {}
    if isinstance(metadata, dict) and metadata.get('suppress_output') is True:
        return
    message = str(getattr(decision, 'reason', '') or '').strip()
    if not message and isinstance(metadata, dict):
        message = str(metadata.get('stop_reason') or metadata.get('system_message') or '').strip()
    print(message or fallback)


def _user_config_with_command_hooks(user_config: UserConfig, metadata: dict[str, Any]) -> UserConfig:
    if _command_runtime_effect_is_restricted(metadata, 'hooks') and not _command_metadata_allows_restricted_effect(
        user_config,
        metadata,
        effect='hooks',
    ):
        return user_config
    hooks_config = metadata.get('hooks_config')
    if not isinstance(hooks_config, dict) or not hooks_config:
        return user_config
    source = 'command'
    if isinstance(hooks_config.get('events'), dict):
        command_hooks = HookRegistryConfig.from_dict(hooks_config, source=source)
    else:
        command_hooks = HookRegistryConfig.from_events_dict(hooks_config, source=source)
    if not command_hooks.enabled:
        return user_config
    merged_hooks = HookRegistryConfig.merge(user_config.runtime.hooks, command_hooks)
    return replace(user_config, runtime=replace(user_config.runtime, hooks=merged_hooks))


def _user_config_with_command_runtime_effects(user_config: UserConfig, metadata: dict[str, Any]) -> UserConfig:
    return _user_config_with_command_hooks(user_config, metadata)


def _command_metadata_allows_restricted_effect(
    user_config: UserConfig,
    metadata: dict[str, Any],
    *,
    effect: str,
) -> bool:
    if effect not in {'allowed_tools', 'hooks', 'model', 'reasoning_effort'}:
        return False
    extension_payload = metadata.get('extension_invocation')
    if isinstance(extension_payload, dict):
        source_id = str(extension_payload.get('source_id') or '').strip().casefold()
        source_origin = str(extension_payload.get('source_origin') or '').strip().casefold()
        if source_id == 'app_home' or source_origin == 'app_home':
            return True
        trust = user_config.runtime.extension_trust
        trusted_sources = {
            str(item).strip().casefold()
            for item in trust.trusted_sources
            if str(item).strip()
        }
        trusted_plugins = {
            str(item).strip().casefold()
            for item in trust.trusted_plugins
            if str(item).strip()
        }
        if source_id in trusted_sources or source_origin in trusted_sources:
            return True
        if source_id.startswith('plugin:'):
            return source_id in trusted_plugins
        if source_origin == 'project' and effect == 'hooks':
            return bool(trust.allow_project_hooks)
        return False
    return False


def _command_runtime_effect_is_restricted(metadata: dict[str, Any], effect: str) -> bool:
    trust_policy = metadata.get('trust_policy')
    if not isinstance(trust_policy, dict):
        return False
    restricted_effects = trust_policy.get('restricted_effects')
    if not isinstance(restricted_effects, list | tuple | set):
        return False
    effect_key = str(effect or '').strip().casefold()
    return any(str(item).strip().casefold() == effect_key for item in restricted_effects)


def _slash_command_arguments(message: str) -> str:
    stripped = str(message or '').strip()
    if not stripped:
        return ''
    try:
        parts = shlex.split(stripped)
    except ValueError:
        parts = stripped.split(maxsplit=1)
    if len(parts) <= 1:
        return ''
    return ' '.join(str(item) for item in parts[1:]).strip()


def _admit_raw_message(
    *,
    message: str,
    llm_client,
    llm_config: LLMConfig,
    project_root: Path,
    app_paths: AppPaths,
    runtime_profile_selection: str,
    config_runtime_profile: str | None = None,
    provider_profile_name: str = "",
    hook_prefix_fragments: tuple[HookModelContextFragment, ...] = (),
    session_start_hook_emitted: bool = False,
    direct_session=None,
) -> NewMessageAdmission:
    session_manager = RuntimeSessionManager(
        app_paths=app_paths,
        project_root=project_root,
    )
    entry_mode = 'raw_message_admission'
    stage_runtime_profile = resolve_runtime_profile(
        invocation_selection=runtime_profile_selection,
        config_selection=config_runtime_profile,
    ).resolved
    if direct_session is not None:
        if stage_runtime_profile is StageRuntimeProfile.TASK_LEVEL and direct_session.run_context is not None:
            return select_new_message_admission(
                raw_user_message=message, stage_runtime_profile=stage_runtime_profile,
                run_context=direct_session.run_context, hook_prefix_fragments=hook_prefix_fragments,
                session_start_hook_emitted=session_start_hook_emitted,
            )
        direct_session.detach()
    run_context = session_manager.prepare_workflow_run(entry=entry_mode)
    admission: NewMessageAdmission | None = None
    try:
        if stage_runtime_profile is not StageRuntimeProfile.TASK_LEVEL:
            prepare_pre_intake_bootstrap(run_context=run_context, raw_user_message=message)
        admission = select_new_message_admission(
            raw_user_message=message,
            stage_runtime_profile=stage_runtime_profile,
            run_context=run_context,
            hook_prefix_fragments=hook_prefix_fragments,
            session_start_hook_emitted=session_start_hook_emitted,
        )
        execution_tier = (
            ExecutionTier.DIRECT if admission.is_direct else ExecutionTier.FULL
        )
        from ownpi.runtime.rollout.run_events import RunEventStore
        from ownpi.runtime.rollout.stage_business_events import emit_run_initialized

        with RunEventStore(run_context.run_dir, run_context.run_id) as events:
            emit_run_initialized(
                events,
                session_id=run_context.session_id,
                task_id=run_context.task_id,
                execution_tier=execution_tier.value,
                stage_runtime_profile=stage_runtime_profile.value,
            )
        if admission.is_direct:
            return admission
        authoring_outcome = TaskSpecAuthoringService(llm_client).author(
            request=TaskSpecAuthoringRequest(
                run_id=run_context.run_id,
                session_id=run_context.session_id,
                task_id=run_context.task_id,
                request_id=admission.request_id,
                raw_user_message=message,
                run_dir=run_context.run_dir,
                project_root=project_root,
                provider=llm_config.provider,
                model=llm_config.model,
                hook_prefix_fragments=hook_prefix_fragments,
            )
        )
        admission = replace(admission, task_spec_authoring=authoring_outcome)
        _require_completed_task_spec_authoring(
            outcome=authoring_outcome,
            run_context=run_context,
        )
    except Exception as exc:
        _mark_admission_run_failed(
            session_manager=session_manager,
            run_context=run_context,
            entry_mode=entry_mode,
            error=exc,
        )
        raise
    if admission is None:
        raise RuntimeError('Raw-message admission completed without an admission result.')
    return admission


def _require_completed_task_spec_authoring(
    *,
    outcome: TaskSpecAuthoringOutcome,
    run_context: PreparedRunContext,
) -> None:
    if outcome.status != 'completed' or outcome.artifact is None:
        detail = outcome.failure_class or outcome.status
        raise RuntimeError(f'Full-tier task spec authoring did not complete: {detail}')
    expected_path = (run_context.run_dir / 'task_spec.md').resolve()
    artifact_path = Path(outcome.artifact.task_spec_path).expanduser().resolve()
    if artifact_path != expected_path:
        raise RuntimeError(
            'Full-tier task spec authoring returned a non-canonical artifact path: '
            f'{artifact_path}'
        )
    if not artifact_path.is_file() or not artifact_path.read_text(encoding='utf-8').strip():
        raise RuntimeError('Full-tier task spec authoring did not commit a non-empty task_spec.md')


def _mark_admission_run_failed(
    *,
    session_manager: RuntimeSessionManager,
    run_context: PreparedRunContext,
    entry_mode: str,
    error: Exception,
) -> None:
    failure_class = type(error).__name__
    session_manager.store.update_run_status(
        run_context.session_id,
        run_context.task_id,
        run_context.run_id,
        status='failed',
        metadata={
            'entry': entry_mode,
            'failure': 'pre_stage_admission_exception',
            'failure_class': failure_class,
        },
    )
    session_manager.store.update_task_status(
        run_context.session_id,
        run_context.task_id,
        status='failed',
        active_run_id=run_context.run_id,
        metadata={
            'last_run_status': 'failed',
            'failure_class': failure_class,
        },
    )


def _render_memory_status(
    *,
    app_paths: AppPaths,
    project_root: Path,
) -> str:
    layers = discover_instructions(
        project_root=project_root,
        app_home=app_paths.app_home,
    )
    type_counts: dict[str, int] = {}
    for layer in layers:
        source = layer.source_family
        type_counts[source] = type_counts.get(source, 0) + 1
    lines = [
        '# Memory',
        '',
        '## Always-on instructions',
        f"- user: `{app_paths.app_home / 'OWNPI.md'}` {'present' if (app_paths.app_home / 'OWNPI.md').exists() else 'missing'}",
        f"- project: `{project_root / 'OWNPI.md'}` {'present' if (project_root / 'OWNPI.md').exists() else 'missing'}",
        f"- active_sources_now: {len(layers)}",
    ]
    if type_counts:
        lines.append("- source_counts: " + ', '.join(f'{key}={value}' for key, value in sorted(type_counts.items())))
    if layers:
        lines.extend(['', '### Loaded preview'])
        for layer in layers[:8]:
            lines.append(
                f"- `{layer.relative_path}` "
                f"source={layer.source_family} scope={layer.scope}"
            )
    lines.extend(
        [
            '',
            '## Runtime memory',
            f"- root: `{app_paths.memory_root}`",
            f"- internal: `{app_paths.internal_memory_root}`",
        ]
    )
    return '\n'.join(lines)


def _memory_edit_path(target: str, *, app_paths: AppPaths, project_root: Path) -> Path:
    normalized = str(target or '').strip().casefold()
    if normalized in {'user', 'global'}:
        return app_paths.app_home / 'OWNPI.md'
    if normalized == 'project':
        return project_root / 'OWNPI.md'
    raise ValueError('Usage: /memory edit user|project')


def _run_with_live_approvals(
    action_fn: Callable[[], Any], *, run_dir: Path, user_config: UserConfig,
    project_root: Path, app_paths: AppPaths, ui_mode: str,
) -> Any:
    answer_engine = None
    dismissed: set[str] = set()
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(action_fn)
    try:
        while True:
            try:
                return future.result(timeout=0.1)
            except TimeoutError:
                if future.done():
                    return future.result()
            for _, session in pending_live_approvals(run_dir):
                if session.session_id in dismissed:
                    continue
                try:
                    pending = session.to_dict()
                    response = DIALOG_FALLBACK
                    if interaction_dialog_available():
                        response = run_permission_dialog(
                            run_dir=run_dir, pending=pending, ui_mode=ui_mode,
                            theme_setting=str(user_config.runtime.ui.theme),
                            use_terminal_background=bool(user_config.runtime.ui.use_terminal_background),
                        )
                    if response is DIALOG_FALLBACK:
                        _print_markdown_output(_render_pending_tool_permission(
                            run_dir, pending, ui_mode=ui_mode,
                        ))
                        while True:
                            reply = input('Permission (approve / approve session / deny / cancel): ')
                            if _is_permission_capture_cancel_command(reply):
                                response = None
                                break
                            try:
                                response = _parse_tool_permission_response(reply)
                                break
                            except ValueError as exc:
                                print(str(exc))
                    if response is not None:
                        # Another UI can answer while this dialog is open.
                        if not any(item.session_id == session.session_id
                                   for _, item in pending_live_approvals(run_dir)):
                            continue
                        if answer_engine is None:
                            answer_engine = WorkflowEngine(
                                user_config=user_config, project_root=project_root, app_paths=app_paths,
                            )
                        InteractionController(engine=answer_engine).apply_action(
                            run_dir, RespondPermission(
                                interaction_id=session.session_id,
                                decision=response.decision, scope=response.scope,
                            ),
                        )
                        continue
                except EOFError:
                    raise
                except Exception as exc:
                    print(f'Permission interaction failed for {session.session_id}: {exc}')
                dismissed.add(session.session_id)
                print(
                    f'Approval prompt dismissed for {session.session_id}. The invocation is still waiting. '
                    f'Answer using ownpi permission in another terminal for {run_dir}, '
                    'or press Ctrl-C here to interrupt the run.'
                )
    except (KeyboardInterrupt, EOFError):
        if not future.done():
            submit_runtime_control(
                run_dir, action='interrupt', reason='REPL live interaction interrupted',
            )
        raise
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
        if answer_engine is not None:
            answer_engine.close(wait=True)


def _execute_fresh_admission(
    *,
    admission: NewMessageAdmission,
    user_config: UserConfig,
    active_profile_name: str,
    model_override: str | None,
    reasoning_override: str | None,
    project_root: Path,
    resolved_app_paths: AppPaths,
    ui_mode: str,
    render_result: bool = True,
    run_lock: RLock | None = None,
    direct_session=None,
):
    lock_context = run_lock if run_lock is not None else nullcontext()
    with lock_context:
        session_user_config = _build_session_user_config(
            user_config=user_config,
            active_profile_name=active_profile_name,
            model_override=model_override,
            reasoning_override=reasoning_override,
        )
        with _runtime_output_surface(session_user_config, ui_mode=ui_mode) as (
            event_callback,
            runtime_status_callback,
            _live_status,
        ):
            runner = (direct_session.execute if admission.is_direct and direct_session is not None
                      else run_fresh_admission)
            result = _run_with_live_approvals(partial(
                runner,
                raw_user_message=admission.raw_user_message,
                user_config=session_user_config,
                project_root=project_root,
                run_context=admission.run_context,
                app_paths=resolved_app_paths,
                event_callback=event_callback,
                runtime_status_callback=runtime_status_callback,
                artifact_callback=_build_artifact_callback(
                    ui_mode,
                    user_config=session_user_config,
                ),
                verbose_llm_progress=True,
                hook_prefix_fragments=admission.hook_prefix_fragments,
                session_start_hook_emitted=admission.session_start_hook_emitted,
                stage_runtime_profile=admission.stage_runtime_profile,
            ), run_dir=admission.run_context.run_dir, user_config=session_user_config,
                project_root=project_root, app_paths=resolved_app_paths, ui_mode=ui_mode)
        if render_result:
            _print_markdown_output(_render_final_package_for_ui(result, ui_mode=ui_mode))
        return result


def run_chat_session(
    user_config: UserConfig,
    project_root: Path,
    app_paths: AppPaths | None = None,
    ui_mode: str = "user",
    runtime_profile: str | None = None,
) -> int:
    history: list[ChatTurn] = []
    active_profile_name = user_config.selected_llm_profile or user_config.default_llm_name
    model_override: str | None = None
    reasoning_override: str | None = None
    runtime_profile_preference = runtime_profile_selection_from_value(
        runtime_profile if runtime_profile is not None else user_config.runtime.default_profile
    ).value
    ui_mode = ui_mode if ui_mode in _UI_MODES else "user"
    try:
        active_llm_config, llm_client = _switch_active_llm(
            user_config=user_config,
            active_profile_name=active_profile_name,
            model_override=model_override,
            reasoning_override=reasoning_override,
        )
    except Exception as exc:
        print(f"Failed to initialize LLM client: {exc}")
        print("Run ownpi setup to configure API key, base URL, and default model.")
        return 1

    resolved_app_paths = app_paths or AppPaths.discover()
    _print_startup_banner(
        user_config=user_config,
        active_profile_name=active_profile_name,
        active_llm_config=active_llm_config,
        model_override=model_override,
        reasoning_override=reasoning_override,
        ui_mode=ui_mode,
        project_root=project_root,
    )
    _print_startup_guidance(active_llm_config)
    command_registry = build_default_command_registry(workflow_command_handler=handle_workflow_command)
    interaction_router = InteractionRouter()
    clarify_capture: dict[str, Any] | None = None
    permission_capture: dict[str, Any] | None = None
    last_run_dir: Path | None = None
    chat_run_lock = RLock()
    from ownpi.chat.direct_session import DirectChatSession

    direct_session = DirectChatSession()
    mcp_prompt_command_cache: dict[str, list[dict[str, Any]]] = {}
    mcp_command_runtime_state = MCPRuntimeState(
        configured_servers=[item.to_dict() for item in user_config.runtime.mcp_servers]
    )
    session_hook_prefix_fragments: tuple[HookModelContextFragment, ...] = ()
    extension_snapshot = discover_extensions(
        resolved_app_paths,
        project_root=project_root,
        trust_config=user_config.runtime.extension_trust,
    )
    builtin_hook_callbacks = build_builtin_hook_registrations(user_config.runtime.builtin_hooks)
    session_hooks_config = HookRegistryConfig.merge(
        user_config.runtime.hooks,
        extension_snapshot.hooks_config,
    )
    session_user_config = replace(user_config, runtime=replace(user_config.runtime, hooks=session_hooks_config))
    hook_runner = build_hook_engine(
        session_user_config.runtime.hooks,
        builtin_hook_callbacks,
        effective_permissions=compile_runtime_permissions(
            session_user_config.runtime.permissions,
            project_root=project_root,
        ),
    )
    def _emit_chat_hook(
        event_name: str,
        payload: dict[str, Any] | None = None,
    ) -> HookDeliveryBatch:
        try:
            decision = hook_runner.run(
                hook_request(event_name, {
                    'ui_mode': ui_mode,
                    'project_root': str(project_root),
                    'cwd': str(project_root),
                    **dict(payload or {}),
                }),
                effective_permissions=compile_runtime_permissions(
                    session_user_config.runtime.permissions,
                    project_root=project_root,
                ),
            )
        except Exception as exc:
            print(f"Hook event {event_name} failed: {exc}")
            return HookDeliveryBatch()
        for message in decision.delivery.system_messages:
            print(message)
        return decision.delivery

    def _check_user_prompt_submit_hooks(
        prompt: str,
    ) -> tuple[bool, tuple[HookModelContextFragment, ...]]:
        try:
            decision = hook_runner.run(
                hook_request('UserPromptSubmit', {
                    'ui_mode': ui_mode,
                    'project_root': str(project_root),
                    'cwd': str(project_root),
                    'prompt': prompt,
                    'user_prompt': prompt,
                }),
                effective_permissions=compile_runtime_permissions(
                    session_user_config.runtime.permissions,
                    project_root=project_root,
                ),
            )
        except Exception as exc:
            print(f"Hook event UserPromptSubmit failed: {exc}")
            return False, ()
        for message in decision.delivery.system_messages:
            print(message)
        if decision.blocked:
            _print_hook_block_decision(decision, fallback='UserPromptSubmit hook blocked this prompt.')
            return True, ()
        return False, decision.delivery.p_fragments

    def _check_stop_hooks(reason: str) -> bool:
        try:
            decision = hook_runner.run(
                hook_request('Stop', {
                    'ui_mode': ui_mode,
                    'project_root': str(project_root),
                    'cwd': str(project_root),
                    'reason': reason,
                }),
                effective_permissions=compile_runtime_permissions(
                    session_user_config.runtime.permissions,
                    project_root=project_root,
                ),
            )
        except Exception as exc:
            print(f"Hook event Stop failed: {exc}")
            return False
        if decision.blocked:
            _print_hook_block_decision(decision, fallback='Stop hook blocked session exit.')
            return True
        return False

    def _refresh_dynamic_commands():
        nonlocal extension_snapshot, session_user_config, hook_runner, builtin_hook_callbacks
        previous_hook_engine = hook_runner
        extension_snapshot = discover_extensions(
            resolved_app_paths,
            project_root=project_root,
            trust_config=user_config.runtime.extension_trust,
        )
        builtin_hook_callbacks = build_builtin_hook_registrations(user_config.runtime.builtin_hooks)
        session_hooks_config = HookRegistryConfig.merge(
            user_config.runtime.hooks,
            extension_snapshot.hooks_config,
        )
        session_user_config = replace(user_config, runtime=replace(user_config.runtime, hooks=session_hooks_config))
        hook_runner = build_hook_engine(
            session_user_config.runtime.hooks,
            builtin_hook_callbacks,
            effective_permissions=compile_runtime_permissions(
                session_user_config.runtime.permissions,
                project_root=project_root,
            ),
            observability=previous_hook_engine.observability,
        )
        previous_hook_engine.close(wait=False)
        custom_registrations = sync_custom_commands(command_registry, extension_snapshot.command_definitions)
        mcp_registrations = sync_mcp_prompt_commands(
            command_registry,
            session_user_config,
            cache=mcp_prompt_command_cache,
            runtime_state=mcp_command_runtime_state,
            project_root=project_root,
        )
        return {'commands': custom_registrations, 'mcp': mcp_registrations}

    def _current_slash_commands():
        _refresh_dynamic_commands()
        definitions = command_registry.visible_definitions(
            CommandExecutionContext(
                project_root=project_root,
                user_config=session_user_config,
                history=history,
                hook_engine=hook_runner,
                app_paths=resolved_app_paths,
                mcp_resource_cache=dict(mcp_command_runtime_state.resource_cache),
                state={'mcp_runtime_state': mcp_command_runtime_state},
            )
        )
        return slash_commands_from_summaries(
            (
                definition.name,
                definition.description,
                definition.argument_hint or '',
                definition.trust_level,
                definition.source,
            )
            for definition in definitions
        )

    input_reader = SlashCommandInput(
        DEFAULT_SLASH_COMMANDS,
        command_provider=_current_slash_commands,
        skill_names_provider=lambda: [
            item.name for item in list_installed_skills(resolved_app_paths, project_root=project_root)
        ],
        theme_setting=str(user_config.runtime.ui.theme),
        use_terminal_background=bool(user_config.runtime.ui.use_terminal_background),
    )

    def _build_current_session_user_config() -> UserConfig:
        return _build_session_user_config(
            user_config=session_user_config,
            active_profile_name=active_profile_name,
            model_override=model_override,
            reasoning_override=reasoning_override,
        )

    @contextmanager
    def _interaction_controller_scope():
        session_user_config = _build_current_session_user_config()
        with _runtime_output_surface(session_user_config, ui_mode=ui_mode) as (
            event_callback,
            runtime_status_callback,
            live_status,
        ):
            engine = WorkflowEngine(
                user_config=session_user_config,
                project_root=project_root,
                app_paths=resolved_app_paths,
                event_callback=event_callback,
                runtime_status_callback=runtime_status_callback,
                artifact_callback=_build_artifact_callback(ui_mode, user_config=session_user_config),
            )
            try:
                yield InteractionController(engine=engine), live_status
            finally:
                engine.close(wait=True)

    def _cancel_local_capture(run_dir: Path, pending: dict[str, Any], *, label: str) -> str:
        controller = InteractionController(engine=None)
        controller.apply_action(
            run_dir,
            CancelLocalCapture(interaction_id=str(pending.get('session_id', '') or '')),
        )
        return f'{label} cancelled. Run remains paused_for_human.'

    def _run_interaction_action_with_progress(
        *,
        live_status: Any,
        action_fn,
        label: str,
        detail: str,
    ):
        managed_status = live_status
        manage_context = False
        if getattr(live_status, 'surface_mode', 'disabled') == 'disabled':
            session_user_config = _build_current_session_user_config()
            managed_status = _build_direct_thinking_status_for_ui(
                ui_mode=ui_mode,
                theme_setting=str(session_user_config.runtime.ui.theme),
                use_terminal_background=bool(session_user_config.runtime.ui.use_terminal_background),
            )
            manage_context = True

        def _run_blocking():
            executor = ThreadPoolExecutor(max_workers=1)
            future = executor.submit(action_fn)
            last_static_heartbeat_at: float | None = None
            try:
                while True:
                    elapsed_ms = (time.monotonic() - started_at) * 1000.0
                    try:
                        managed_status.update(elapsed_ms=elapsed_ms, label=label, detail=detail)
                    except TypeError:
                        managed_status.update(
                            {
                                'elapsed_ms': elapsed_ms,
                                'label': label,
                                'operation': detail,
                                'provider_inflight': True,
                            }
                        )
                    except Exception:
                        pass
                    if getattr(managed_status, 'surface_mode', 'disabled') == 'static_only':
                        now = time.monotonic()
                        if last_static_heartbeat_at is None or (now - last_static_heartbeat_at) >= 5.0:
                            print(managed_status.render_text())
                            last_static_heartbeat_at = now
                    try:
                        outcome = future.result(timeout=0.1)
                        executor.shutdown(wait=False, cancel_futures=True)
                        return outcome
                    except TimeoutError:
                        continue
            except BaseException:
                future.cancel()
                executor.shutdown(wait=False, cancel_futures=True)
                raise

        started_at = time.monotonic()
        if manage_context:
            with managed_status:
                return _run_blocking()
        return _run_blocking()

    def _answer_human_interaction(run_dir: Path, pending: dict[str, Any], answers: dict[str, Any]):
        action = SubmitHumanAnswers(
            interaction_id=str(pending.get('session_id', '') or ''),
            answers_by_question_id=normalize_human_answer_mapping(answers, pending=pending),
        )
        detail = 'human_question' if str(pending.get('origin_kind', 'controller') or 'controller') == 'tool' else 'human_clarification'
        with _interaction_controller_scope() as (controller, live_status):
            result = _run_interaction_action_with_progress(
                live_status=live_status,
                action_fn=lambda: controller.apply_action(run_dir, action),
                label='Resuming run',
                detail=detail,
            )
        return result.payload

    def _answer_permission_interaction(
        run_dir: Path,
        pending: dict[str, Any],
        response: PermissionDialogResult,
    ):
        action = RespondPermission(
            interaction_id=str(pending.get('session_id', '') or ''),
            decision=response.decision,
            scope=response.scope,
        )
        with _interaction_controller_scope() as (controller, live_status):
            result = _run_interaction_action_with_progress(
                live_status=live_status,
                action_fn=lambda: controller.apply_action(run_dir, action),
                label='Resuming run',
                detail='tool_permission',
            )
        return result.payload

    def _try_human_dialog(run_dir: Path, pending: dict[str, Any], *, render_fallback_panel: bool = True):
        nonlocal clarify_capture
        if not interaction_dialog_available():
            clarify_capture = {"run_dir": run_dir, "pending": pending}
            print(_human_dialog_emergency_fallback_message())
            if render_fallback_panel:
                _print_markdown_output(_render_pending_human_clarification(run_dir, pending, ui_mode=ui_mode))
            return True, None
        answers = run_human_interaction_dialog(
            run_dir=run_dir,
            pending=pending,
            ui_mode=ui_mode,
            theme_setting=str(user_config.runtime.ui.theme),
            use_terminal_background=bool(user_config.runtime.ui.use_terminal_background),
        )
        if answers is DIALOG_FALLBACK or isinstance(answers, HumanDialogFallbackResult):
            clarify_capture = {"run_dir": run_dir, "pending": pending}
            if isinstance(answers, HumanDialogFallbackResult) and answers.draft_answers_by_question_id:
                clarify_capture["draft_answers_by_question_id"] = answers.draft_answers_by_question_id
            print(focused_dialog_fallback_message(kind='human_question'))
            if render_fallback_panel:
                _print_markdown_output(_render_pending_human_clarification(run_dir, pending, ui_mode=ui_mode))
            return True, None
        if answers is None:
            print(_cancel_local_capture(run_dir, pending, label='Question dialog'))
            return True, None
        return True, _answer_human_interaction(run_dir, pending, answers)

    def _try_permission_dialog(run_dir: Path, pending: dict[str, Any], *, render_fallback_panel: bool = True):
        nonlocal permission_capture
        if not interaction_dialog_available():
            permission_capture = {"run_dir": run_dir, "pending": pending}
            print('Focused permission dialog is unavailable. Falling back to text reply mode.')
            if render_fallback_panel:
                _print_markdown_output(_render_pending_tool_permission(run_dir, pending, ui_mode=ui_mode))
            return True, None
        response = run_permission_dialog(
            run_dir=run_dir,
            pending=pending,
            ui_mode=ui_mode,
            theme_setting=str(user_config.runtime.ui.theme),
            use_terminal_background=bool(user_config.runtime.ui.use_terminal_background),
        )
        if response is DIALOG_FALLBACK:
            permission_capture = {"run_dir": run_dir, "pending": pending}
            print(focused_dialog_fallback_message(kind='permission'))
            if render_fallback_panel:
                _print_markdown_output(_render_pending_tool_permission(run_dir, pending, ui_mode=ui_mode))
            return True, None
        if response is None:
            print(_cancel_local_capture(run_dir, pending, label='Permission dialog'))
            return True, None
        return True, _answer_permission_interaction(run_dir, pending, response)

    def _drive_paused_interaction_dialogs(result):
        current = result
        for _ in range(8):
            if getattr(current, 'status', None) != 'paused_for_human':
                return current
            run_dir_value = (
                current.run_artifacts.get('run_dir')
                if isinstance(getattr(current, 'run_artifacts', None), dict)
                else None
            )
            if not run_dir_value:
                return current
            run_dir = Path(str(run_dir_value)).expanduser().resolve()
            route = interaction_router.route_pending_interaction(run_dir)
            if route is None:
                return current
            if route.kind in {'human_question', 'human_clarification'}:
                handled, resumed = _try_human_dialog(run_dir, route.pending, render_fallback_panel=False)
            else:
                handled, resumed = _try_permission_dialog(run_dir, route.pending, render_fallback_panel=False)
            if not handled or resumed is None:
                return current
            current = resumed
        return current

    def _register_repl_command_handlers() -> None:
        handlers = {
            '/help': _cmd_help,
            '/exit': _cmd_exit,
            '/last': _cmd_last,
            '/status': _cmd_status,
            '/runtime-profile': _cmd_runtime_profile,
            '/runs': _cmd_runs,
            '/memory': _cmd_memory,
            '/resume-run': _cmd_resume_run,
            '/clarify-run': _cmd_clarify_run,
            '/cancel-clarify': _cmd_cancel_clarify,
            '/permission-run': _cmd_permission_run,
            '/cancel-permission': _cmd_cancel_permission,
            '/permissions': _cmd_permissions,
            '/skills': _cmd_skills,
            '/commands': _cmd_commands,
            '/extensions': _cmd_extensions,
            '/hooks': _cmd_hooks,
            '/mode': _cmd_mode,
            '/models': _cmd_models,
            '/model': _cmd_model,
            '/reasonings': _cmd_reasonings,
            '/reasoning': _cmd_reasoning,
        }
        for name, handler in handlers.items():
            command_registry.attach_handler(name, handler)

    def _cmd_help(_context: CommandExecutionContext, _message: str) -> CommandResult:
        _print_markdown_output(render_help(command_registry.visible_definitions()))
        return CommandResult(handled=True)

    def _cmd_exit(_context: CommandExecutionContext, _message: str) -> CommandResult:
        if _check_stop_hooks('exit_command'):
            return CommandResult(handled=True)
        close_persistent_mcp_connections()
        direct_session.detach()
        _emit_chat_hook('SessionEnd', {'reason': 'exit_command'})
        hook_runner.close(wait=True)
        print("Bye.")
        return CommandResult(handled=True, exit_code=0)

    def _cmd_last(_context: CommandExecutionContext, _message: str) -> CommandResult:
        if not history:
            print("No previous request recorded yet.")
        else:
            print(history[-1].user_message)
        return CommandResult(handled=True)

    def _cmd_status(_context: CommandExecutionContext, _message: str) -> CommandResult:
        _print_markdown_output(
            render_session_status_markdown(
                user_config=user_config,
                active_profile_name=active_profile_name,
                active_llm_config=active_llm_config,
                model_override=model_override,
                reasoning_override=reasoning_override,
                ui_mode=ui_mode,
                runtime_profile_selection=runtime_profile_preference,
            )
        )
        return CommandResult(handled=True)


    def _cmd_runtime_profile(_context: CommandExecutionContext, message: str) -> CommandResult:
        nonlocal runtime_profile_preference
        argument = message[len('/runtime-profile') :].strip()
        if argument in {'', 'status'}:
            resolution = resolve_runtime_profile(
                session_selection=runtime_profile_preference,
                config_selection=session_user_config.runtime.default_profile,
            )
            print(
                'Runtime profile: '
                f"{resolution.resolved.value} "
                f"(requested={resolution.requested.value}, source={resolution.source})"
            )
            return CommandResult(handled=True)
        try:
            runtime_profile_preference = runtime_profile_selection_from_value(argument).value
            direct_session.detach()
        except ValueError:
            print('Usage: /runtime-profile [status|auto|task|research]')
            return CommandResult(handled=True)
        print(f'Runtime profile preference set to {runtime_profile_preference}.')
        return CommandResult(handled=True)

    def _cmd_runs(_context: CommandExecutionContext, _message: str) -> CommandResult:
        output_dir = _resolve_output_dir(project_root, user_config)
        runs = [{'run_id': path.name, 'run_dir': str(path)} for path in list_run_dirs(output_dir)[:10]]
        _print_markdown_output(render_runs_list(runs))
        return CommandResult(handled=True)


    def _cmd_memory(_context: CommandExecutionContext, message: str) -> CommandResult:
        nonlocal last_run_dir
        argument = message[len("/memory") :].strip()
        tokens = argument.split()
        action = tokens[0] if tokens else 'status'
        if action == 'status':
            _print_markdown_output(
                _render_memory_status(
                    app_paths=resolved_app_paths,
                    project_root=project_root,
                )
            )
            return CommandResult(handled=True)
        if action == 'reload':
            print("Instruction memory is file-backed; the next prompt assembly will reread OWNPI.md layers.")
            return CommandResult(handled=True)
        if action == 'edit':
            target = tokens[1] if len(tokens) > 1 else 'user'
            try:
                path = _memory_edit_path(target, app_paths=resolved_app_paths, project_root=project_root)
            except ValueError as exc:
                print(str(exc))
                return CommandResult(handled=True)
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text("# OWNPI.md\n\n", encoding='utf-8')
            editor = os.environ.get('VISUAL') or os.environ.get('EDITOR')
            if not editor:
                print(f"Memory file: {path}")
                return CommandResult(handled=True)
            try:
                subprocess.run([*shlex.split(editor), str(path)], check=False)
            except OSError as exc:
                print(f"Failed to launch editor: {exc}\nMemory file: {path}")
            return CommandResult(handled=True)
        print("Usage: /memory [status|reload|edit user|edit project|edit local]")
        return CommandResult(handled=True)


    def _cmd_resume_run(_context: CommandExecutionContext, message: str) -> CommandResult:
        nonlocal last_run_dir
        argument = message[len('/resume-run') :].strip()
        if argument:
            try:
                run_dir = _resolve_run_dir_argument(
                    argument, project_root=project_root, user_config=user_config,
                )
            except Exception as exc:
                print(f'/resume-run failed: {exc}')
                return CommandResult(handled=True)
        elif last_run_dir is not None:
            run_dir = last_run_dir
        else:
            print('Usage: /resume-run <path|index>')
            return CommandResult(handled=True)
        try:
            direct_session.detach()
            from ownpi.runtime.rollout.business_reader import read_business_events
            from ownpi.runtime.rollout.direct_resume import is_direct_session
            from ownpi.runtime.rollout.run_events import run_events_path

            if is_direct_session(read_business_events(run_events_path(run_dir)).events):
                config = _build_session_user_config(
                    user_config=session_user_config, active_profile_name=active_profile_name,
                    model_override=model_override, reasoning_override=reasoning_override,
                )
                result = _run_with_live_approvals(partial(
                    direct_session.resume, run_dir, user_config=config,
                    project_root=project_root, app_paths=resolved_app_paths,
                ), run_dir=run_dir, user_config=config, project_root=project_root,
                    app_paths=resolved_app_paths, ui_mode=ui_mode)
                _print_markdown_output(_render_final_package_for_ui(result, ui_mode=ui_mode))
            else:
                result = RuntimeControlService(
                    user_config=session_user_config, project_root=project_root,
                    app_paths=resolved_app_paths,
                ).resume(run_dir)
                print(result.message)
                print(f'Run {run_dir.name}: {result.payload.get("status", result.disposition)}')
            last_run_dir = run_dir
        except Exception as exc:
            print(f'/resume-run failed: {exc}')
        return CommandResult(handled=True)

    def _cmd_clarify_run(_context: CommandExecutionContext, message: str) -> CommandResult:
        nonlocal last_run_dir
        argument = message[len("/clarify-run") :].strip()
        if not argument:
            print("Usage: /clarify-run <path|index>")
            return CommandResult(handled=True)
        try:
            run_dir = _resolve_run_dir_argument(argument, project_root=project_root, user_config=user_config)
            pending = _pending_human_clarification_for_run(run_dir)
            if not pending:
                print("No pending human clarification for that run.")
                return CommandResult(handled=True)
            handled, result = _try_human_dialog(run_dir, pending)
            if handled and result is not None:
                result = _drive_paused_interaction_dialogs(result)
                _print_markdown_output(_render_final_package_for_ui(result, ui_mode=ui_mode))
                last_run_dir = run_dir
        except Exception as exc:
            print(f"/clarify-run failed: {exc}")
        return CommandResult(handled=True)

    def _cmd_cancel_clarify(_context: CommandExecutionContext, _message: str) -> CommandResult:
        nonlocal clarify_capture
        if clarify_capture is None:
            print("No active clarify capture.")
        else:
            print(
                _cancel_local_capture(
                    clarify_capture["run_dir"],
                    clarify_capture["pending"],
                    label='Clarify capture',
                )
            )
            clarify_capture = None
        return CommandResult(handled=True)

    def _cmd_permission_run(_context: CommandExecutionContext, message: str) -> CommandResult:
        nonlocal last_run_dir, permission_capture
        argument = message[len("/permission-run") :].strip()
        if not argument:
            print("Usage: /permission-run <path|index>")
            return CommandResult(handled=True)
        try:
            run_dir = _resolve_run_dir_argument(argument, project_root=project_root, user_config=user_config)
            pending = _pending_tool_permission_for_run(run_dir)
            if not pending:
                print("No pending tool permission for that run.")
                return CommandResult(handled=True)
            handled, result = _try_permission_dialog(run_dir, pending)
            if handled:
                if result is not None:
                    result = _drive_paused_interaction_dialogs(result)
                    _print_markdown_output(_render_final_package_for_ui(result, ui_mode=ui_mode))
                    last_run_dir = run_dir
                return CommandResult(handled=True)
            permission_capture = {"run_dir": run_dir, "pending": pending}
            _print_markdown_output(_render_pending_tool_permission(run_dir, pending, ui_mode=ui_mode))
        except Exception as exc:
            print(f"/permission-run failed: {exc}")
        return CommandResult(handled=True)

    def _cmd_cancel_permission(_context: CommandExecutionContext, _message: str) -> CommandResult:
        nonlocal permission_capture
        if permission_capture is None:
            print("No active permission capture.")
        else:
            print(
                _cancel_local_capture(
                    permission_capture["run_dir"],
                    permission_capture["pending"],
                    label='Permission capture',
                )
            )
            permission_capture = None
        return CommandResult(handled=True)

    def _cmd_permissions(_context: CommandExecutionContext, message: str) -> CommandResult:
        nonlocal session_user_config
        argument = message[len("/permissions") :].strip()
        try:
            selected_preset = _permission_preset_from_command(argument)
            if argument and selected_preset is None:
                print("Usage: /permissions [ask-for-approval|approve-for-me|full-access]")
                return CommandResult(handled=True)
            if selected_preset is not None:
                if (
                    selected_preset is PermissionPreset.FULL_ACCESS
                    and session_user_config.runtime.permissions.preset is not PermissionPreset.FULL_ACCESS
                    and not _confirm_full_access_selection()
                ):
                    print('Full Access selection cancelled.')
                    return CommandResult(handled=True)
                session_user_config = _user_config_with_permission_preset(
                    session_user_config, selected_preset,
                )
                print(f"Permission preset selected: {permission_preset_label(selected_preset)}")
            effective_permissions = compile_runtime_permissions(
                session_user_config.runtime.permissions, project_root=project_root,
            )
            rendered = render_permission_explanation(build_permission_explanation(effective_permissions))
            _print_markdown_output(
                f"{rendered}\n\n"
                f"{_render_permission_preset_choices(session_user_config.runtime.permissions.preset)}"
            )
        except Exception as exc:
            print(f"/permissions failed: {exc}")
        return CommandResult(handled=True)

    def _cmd_skills(_context: CommandExecutionContext, message: str) -> CommandResult:
        try:
            _print_markdown_output(
                _handle_skills_command(
                    message,
                    resolved_app_paths,
                    user_config=user_config,
                    project_root=project_root,
                )
            )
            _refresh_dynamic_commands()
        except Exception as exc:
            print(f"/skills failed: {exc}")
        return CommandResult(handled=True)

    def _cmd_commands(_context: CommandExecutionContext, message: str) -> CommandResult:
        try:
            _refresh_dynamic_commands()
            argument = message[len('/commands') :].strip()
            if argument and argument != 'status':
                print("Usage: /commands [status]")
                return CommandResult(handled=True)
            _print_markdown_output(render_commands_list(command_registry.visible_definitions()))
        except Exception as exc:
            print(f"/commands failed: {exc}")
        return CommandResult(handled=True)

    def _cmd_extensions(_context: CommandExecutionContext, message: str) -> CommandResult:
        try:
            argument = message[len('/extensions') :].strip()
            if argument in {'', 'status'}:
                _refresh_dynamic_commands()
                _print_markdown_output(render_extensions_status(extension_snapshot))
            elif argument == 'reload':
                registrations = _refresh_dynamic_commands()
                _print_markdown_output(
                    render_extensions_reloaded(
                        registrations,
                        skipped_count=len(extension_snapshot.skipped),
                    )
                )
            else:
                print("Usage: /extensions status|reload")
        except Exception as exc:
            print(f"/extensions failed: {exc}")
        return CommandResult(handled=True)

    def _cmd_hooks(_context: CommandExecutionContext, message: str) -> CommandResult:
        try:
            argument = message[len('/hooks') :].strip()
            if argument and argument != 'status':
                print("Usage: /hooks [status]")
                return CommandResult(handled=True)
            _refresh_dynamic_commands()
            _print_markdown_output(
                render_hooks_status_for_session(
                    hook_engine=hook_runner,
                    snapshot=extension_snapshot,
                )
            )
        except Exception as exc:
            print(f"/hooks failed: {exc}")
        return CommandResult(handled=True)

    def _cmd_mode(_context: CommandExecutionContext, message: str) -> CommandResult:
        nonlocal ui_mode
        argument = message[len("/mode") :].strip()
        if not argument:
            selected = _handle_ui_mode_picker(ui_mode, user_config=user_config)
            if selected is None:
                return CommandResult(handled=True)
            ui_mode = selected
        elif argument not in _UI_MODES:
            print("Unsupported UI mode. Use /mode user or /mode developer.")
            return CommandResult(handled=True)
        else:
            ui_mode = argument
        _print_markdown_output(_render_ui_mode_markdown(ui_mode, get_ui_mode_candidates(ui_mode), selected_mode=ui_mode))
        return CommandResult(handled=True)

    def _cmd_models(_context: CommandExecutionContext, _message: str) -> CommandResult:
        _print_markdown_output(
            render_available_models_markdown(
                user_config=user_config,
                active_profile_name=active_profile_name,
                active_llm_config=active_llm_config,
            )
        )
        return CommandResult(handled=True)

    def _cmd_reasonings(_context: CommandExecutionContext, _message: str) -> CommandResult:
        candidates = get_reasoning_candidates(
            user_config=user_config,
            active_profile_name=active_profile_name,
            active_llm_config=active_llm_config,
        )
        _print_markdown_output(_render_reasoning_candidates_markdown(candidates, active_llm_config.reasoning_effort or "medium"))
        return CommandResult(handled=True)

    def _cmd_model(_context: CommandExecutionContext, message: str) -> CommandResult:
        nonlocal active_profile_name, model_override, reasoning_override, active_llm_config, llm_client
        argument = message[len("/model") :].strip()
        if not argument:
            selected = _handle_model_picker(
                user_config=user_config,
                active_profile_name=active_profile_name,
                active_llm_config=active_llm_config,
            )
            if selected is None:
                return CommandResult(handled=True)
            model_override = selected
            try:
                active_llm_config, llm_client = _switch_active_llm(
                    user_config=session_user_config,
                    active_profile_name=active_profile_name,
                    model_override=model_override,
                    reasoning_override=reasoning_override,
                )
            except Exception as exc:
                print(f"Failed to switch model: {exc}")
                return CommandResult(handled=True)
            selected_reasoning = _handle_reasoning_picker(
                user_config=user_config,
                active_profile_name=active_profile_name,
                active_llm_config=active_llm_config,
            )
            if selected_reasoning is not None:
                reasoning_override = selected_reasoning
                try:
                    active_llm_config, llm_client = _switch_active_llm(
                        user_config=user_config,
                        active_profile_name=active_profile_name,
                        model_override=model_override,
                        reasoning_override=reasoning_override,
                    )
                except Exception as exc:
                    print(f"Failed to switch reasoning effort: {exc}")
                    return CommandResult(handled=True)
                print(f"Selected model: {active_llm_config.model}; reasoning: {active_llm_config.reasoning_effort or 'medium'}")
                return CommandResult(handled=True)
            print(f"Selected model: {active_llm_config.model}")
            return CommandResult(handled=True)
        switched_profile = _try_switch_profile(user_config, argument)
        if switched_profile is not None:
            active_profile_name = switched_profile
            model_override = None
            reasoning_override = None
            try:
                active_llm_config, llm_client = _switch_active_llm(
                    user_config=user_config,
                    active_profile_name=active_profile_name,
                    model_override=model_override,
                    reasoning_override=reasoning_override,
                )
            except Exception as exc:
                print(f"Failed to switch profile: {exc}")
                return CommandResult(handled=True)
            print(f"Switched active profile to {active_profile_name} ({active_llm_config.model}).")
            return CommandResult(handled=True)
        model_override = argument
        try:
            active_llm_config, llm_client = _switch_active_llm(
                user_config=user_config,
                active_profile_name=active_profile_name,
                model_override=model_override,
                reasoning_override=reasoning_override,
            )
        except Exception as exc:
            print(f"Failed to override model: {exc}")
            return CommandResult(handled=True)
        print(f"Overrode model on profile {active_profile_name}: {active_llm_config.model}")
        return CommandResult(handled=True)

    def _cmd_reasoning(_context: CommandExecutionContext, message: str) -> CommandResult:
        nonlocal reasoning_override, active_llm_config, llm_client
        argument = message[len("/reasoning") :].strip()
        if not argument:
            selected = _handle_reasoning_picker(
                user_config=user_config,
                active_profile_name=active_profile_name,
                active_llm_config=active_llm_config,
            )
            if selected is None:
                return CommandResult(handled=True)
            reasoning_override = selected
        else:
            reasoning_override = argument
        try:
            active_llm_config, llm_client = _switch_active_llm(
                user_config=user_config,
                active_profile_name=active_profile_name,
                model_override=model_override,
                reasoning_override=reasoning_override,
            )
        except Exception as exc:
            verb = 'switch' if not argument else 'override'
            print(f"Failed to {verb} reasoning effort: {exc}")
            return CommandResult(handled=True)
        verb = 'Selected' if not argument else 'Overrode'
        print(f"{verb} reasoning effort: {active_llm_config.reasoning_effort}")
        return CommandResult(handled=True)

    _register_repl_command_handlers()
    _refresh_dynamic_commands()
    session_hook_prefix_fragments = _emit_chat_hook(
        'SessionStart',
        {
            'active_profile_name': active_profile_name,
            'model': active_llm_config.model,
        },
    ).p_fragments

    while True:
        try:
            submitted_message = input_reader.read_message(_build_input_prompt(ui_mode)).strip()
        except (EOFError, KeyboardInterrupt):
            if _check_stop_hooks('interrupted'):
                continue
            close_persistent_mcp_connections()
            direct_session.detach()
            _emit_chat_hook('SessionEnd', {'reason': 'interrupted'})
            hook_runner.close(wait=True)
            print("\nBye.")
            return 0

        if not submitted_message:
            continue
        user_message = submitted_message
        local_capture_pending = clarify_capture is not None or permission_capture is not None
        slash_command_message = user_message.startswith('/')
        hook_blocked, prompt_hook_fragments = _check_user_prompt_submit_hooks(user_message)
        if hook_blocked:
            continue
        if local_capture_pending or slash_command_message:
            prompt_hook_fragments = ()
        hook_prefix_fragments = (
            *session_hook_prefix_fragments,
            *prompt_hook_fragments,
        )

        if clarify_capture is not None and _is_clarify_capture_cancel_command(user_message):
            print(
                _cancel_local_capture(
                    clarify_capture["run_dir"],
                    clarify_capture["pending"],
                    label='Clarify capture',
                )
            )
            clarify_capture = None
            continue

        if permission_capture is not None and _is_permission_capture_cancel_command(user_message):
            print(
                _cancel_local_capture(
                    permission_capture["run_dir"],
                    permission_capture["pending"],
                    label='Permission capture',
                )
            )
            permission_capture = None
            continue

        if clarify_capture is not None and not _is_capture_passthrough_command(user_message):
            try:
                answers = _parse_clarification_answers(
                    user_message,
                    clarify_capture["pending"],
                    draft_answers_by_question_id=clarify_capture.get("draft_answers_by_question_id"),
                )
                result = _answer_human_interaction(
                    clarify_capture["run_dir"],
                    clarify_capture["pending"],
                    answers,
                )
                last_run_dir = Path(str(clarify_capture["run_dir"])).expanduser().resolve()
                clarify_capture = None
                result = _drive_paused_interaction_dialogs(result)
                _print_markdown_output(_render_final_package_for_ui(result, ui_mode=ui_mode))
            except Exception as exc:
                print(f"/clarify-run failed: {exc}")
            continue

        if permission_capture is not None and not _is_capture_passthrough_command(user_message):
            try:
                response = _parse_tool_permission_response(user_message)
                result = _answer_permission_interaction(
                    permission_capture["run_dir"],
                    permission_capture["pending"],
                    response,
                )
                last_run_dir = Path(str(permission_capture["run_dir"])).expanduser().resolve()
                permission_capture = None
                result = _drive_paused_interaction_dialogs(result)
                _print_markdown_output(_render_final_package_for_ui(result, ui_mode=ui_mode))
            except Exception as exc:
                print(f"/permission-run failed: {exc}")
            continue

        try:
            command_result = command_registry.execute(
                user_message,
                CommandExecutionContext(
                    project_root=project_root,
                    user_config=session_user_config,
                    history=history,
                    hook_engine=hook_runner,
                    app_paths=resolved_app_paths,
                    mcp_resource_cache=dict(mcp_command_runtime_state.resource_cache),
                    state={'mcp_runtime_state': mcp_command_runtime_state},
                ),
            )
            if command_result.handled:
                if command_result.exit_code is not None:
                    direct_session.detach()
                    return command_result.exit_code
                if command_result.message:
                    print(command_result.message)
                if command_result.prompt is not None:
                    invocation_user_config = _user_config_with_command_runtime_effects(
                        session_user_config,
                        command_result.metadata,
                    )
                    invocation_model_override = (
                        _trusted_command_runtime_override(
                            session_user_config,
                            command_result.metadata,
                            key='model',
                            effect='model',
                        )
                        or model_override
                    )
                    invocation_reasoning_override = (
                        _trusted_command_runtime_override(
                            session_user_config,
                            command_result.metadata,
                            key='reasoning_effort',
                            effect='reasoning_effort',
                        )
                        or reasoning_override
                    )
                    try:
                        invocation_llm_config, invocation_llm_client = _switch_active_llm(
                            user_config=invocation_user_config,
                            active_profile_name=active_profile_name,
                            model_override=invocation_model_override,
                            reasoning_override=invocation_reasoning_override,
                        )
                    except Exception as exc:
                        print(f"/command failed to initialize requested model: {exc}")
                        continue
                    routed_message = command_result.prompt
                    admission: NewMessageAdmission | None = None
                    try:
                        admission = _admit_raw_message(
                            message=routed_message,
                            llm_client=invocation_llm_client,
                            llm_config=invocation_llm_config,
                            project_root=project_root,
                            app_paths=resolved_app_paths,
                            runtime_profile_selection=runtime_profile_preference,
                            config_runtime_profile=invocation_user_config.runtime.default_profile,
                            provider_profile_name=active_profile_name,
                            hook_prefix_fragments=session_hook_prefix_fragments,
                            session_start_hook_emitted=True,
                            direct_session=direct_session,
                        )
                        history.append(
                            ChatTurn(
                                user_message=user_message,
                                run_dir=str(admission.run_context.run_dir),
                            )
                        )
                        result = _execute_fresh_admission(
                            admission=admission,
                            user_config=invocation_user_config,
                            active_profile_name=active_profile_name,
                            model_override=invocation_model_override,
                            reasoning_override=invocation_reasoning_override,
                            project_root=project_root,
                            resolved_app_paths=resolved_app_paths,
                            ui_mode=ui_mode,
                            render_result=False,
                            run_lock=chat_run_lock,
                            direct_session=direct_session,
                        )
                        result = _drive_paused_interaction_dialogs(result)
                        _print_markdown_output(
                            _render_final_package_for_ui(result, ui_mode=ui_mode)
                        )
                        result_run_dir = _result_run_dir(result)
                        if result_run_dir is not None:
                            last_run_dir = result_run_dir
                    except Exception as exc:
                        print(f"OwnPI request failed: {exc}")
                    continue
                continue
        except Exception as exc:
            print(f"/command failed: {exc}")
            continue

        current_skill_catalog = build_enabled_skill_catalog(
            resolved_app_paths,
            project_root=project_root,
        )
        if user_message.startswith('/') and not is_explicit_skill_slash_message(
            user_message,
            current_skill_catalog,
        ):
            print(f"Unknown command: {user_message.split()[0]}. Use /help for available commands.")
            continue

        admission: NewMessageAdmission | None = None
        try:
            admission = _admit_raw_message(
                message=user_message,
                llm_client=llm_client,
                llm_config=active_llm_config,
                project_root=project_root,
                app_paths=resolved_app_paths,
                runtime_profile_selection=runtime_profile_preference,
                config_runtime_profile=session_user_config.runtime.default_profile,
                provider_profile_name=active_profile_name,
                hook_prefix_fragments=hook_prefix_fragments,
                session_start_hook_emitted=True,
                direct_session=direct_session,
            )
            history.append(
                ChatTurn(
                    user_message=user_message,
                    run_dir=str(admission.run_context.run_dir),
                )
            )
            result = _execute_fresh_admission(
                admission=admission,
                user_config=session_user_config,
                active_profile_name=active_profile_name,
                model_override=model_override,
                reasoning_override=reasoning_override,
                project_root=project_root,
                resolved_app_paths=resolved_app_paths,
                ui_mode=ui_mode,
                render_result=False,
                run_lock=chat_run_lock,
                direct_session=direct_session,
            )
            result = _drive_paused_interaction_dialogs(result)
            _print_markdown_output(_render_final_package_for_ui(result, ui_mode=ui_mode))
            result_run_dir = _result_run_dir(result)
            if result_run_dir is not None:
                last_run_dir = result_run_dir
        except Exception as exc:
            print(f"OwnPI request failed: {exc}")
            print(
                "Tip: if your provider is slow, increase request_timeout_seconds, "
                "request_max_retries, or stream_max_retries "
                "in ~/.ownpi/config.yaml, then retry."
            )
            continue
