from __future__ import annotations

from typing import Iterable

from ownpi.chat.command_input import PickerCandidate, prompt_pick_candidate
from ownpi.schemas.runtime import LLMConfig, UserConfig

_OPENAI_DEFAULT_MODEL_OPTIONS = [
    "gpt-5.2",
    "gpt-5.3-codex",
    "gpt-5.4",
]
_OPENAI_DEFAULT_REASONING_OPTIONS = [
    "low",
    "medium",
    "high",
    "xhigh",
]


def get_model_candidates(
    *,
    user_config: UserConfig,
    active_profile_name: str,
    active_llm_config: LLMConfig,
) -> list[str]:
    candidates: list[str] = []

    if active_llm_config.model_options:
        candidates.extend(active_llm_config.model_options)

    if active_llm_config.provider in {"openai", "openai_compatible"}:
        candidates.extend(_OPENAI_DEFAULT_MODEL_OPTIONS)

    same_provider_models = [
        cfg.model for cfg in user_config.llms.values() if cfg.provider == active_llm_config.provider and cfg.model
    ]
    candidates.extend(same_provider_models)

    active_profile_config = user_config.get_llm_config(active_profile_name)
    candidates.append(active_profile_config.model)
    candidates.append(active_llm_config.model)
    return _dedupe_keep_order(candidates)


def get_reasoning_candidates(
    *,
    user_config: UserConfig,
    active_profile_name: str,
    active_llm_config: LLMConfig,
) -> list[str]:
    candidates: list[str] = []

    if active_llm_config.reasoning_options:
        candidates.extend(active_llm_config.reasoning_options)

    if active_llm_config.provider in {"openai", "openai_compatible"}:
        candidates.extend(_OPENAI_DEFAULT_REASONING_OPTIONS)

    same_provider_reasoning = [
        cfg.reasoning_effort
        for cfg in user_config.llms.values()
        if cfg.provider == active_llm_config.provider and cfg.reasoning_effort
    ]
    candidates.extend([value for value in same_provider_reasoning if value is not None])

    active_profile_config = user_config.get_llm_config(active_profile_name)
    if active_profile_config.reasoning_effort:
        candidates.append(active_profile_config.reasoning_effort)
    if active_llm_config.reasoning_effort:
        candidates.append(active_llm_config.reasoning_effort)
    return _dedupe_keep_order(candidates)


def get_ui_mode_candidates(current_mode: str) -> list[str]:
    return _dedupe_keep_order([current_mode, "user", "developer"])


def get_llm_profile_candidates(
    *,
    user_config: UserConfig,
    current_profile_name: str | None,
) -> list[str]:
    base = [current_profile_name] if current_profile_name else []
    return _dedupe_keep_order([*base, *user_config.selectable_llm_profiles])


def _dedupe_keep_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        cleaned = value.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        ordered.append(cleaned)
    return ordered


def _build_option_picker_candidates(options: list[str], current_value: str) -> tuple[list[PickerCandidate], int]:
    candidates = [
        PickerCandidate(
            label=option,
            insert_text=option,
            meta="active" if option == current_value else "",
            kind="option",
        )
        for option in options
    ]
    initial_index = options.index(current_value) if current_value in options else 0
    return candidates, initial_index


def pick_option_interactively(
    options: list[str],
    current_value: str,
    *,
    title: str,
    theme_setting: str = "auto",
    use_terminal_background: bool = True,
) -> str | None:
    if not options:
        return None
    candidates, initial_index = _build_option_picker_candidates(options, current_value)
    selected = prompt_pick_candidate(
        candidates,
        title=title,
        initial_index=initial_index,
        theme_setting=theme_setting,
        use_terminal_background=use_terminal_background,
    )
    if selected is None:
        return None
    return selected.insert_text


def pick_model_interactively(
    options: list[str],
    current_model: str,
    *,
    theme_setting: str = "auto",
    use_terminal_background: bool = True,
) -> str | None:
    return pick_option_interactively(
        options,
        current_model,
        title="Select a model",
        theme_setting=theme_setting,
        use_terminal_background=use_terminal_background,
    )

def pick_reasoning_interactively(
    options: list[str],
    current_reasoning: str,
    *,
    theme_setting: str = "auto",
    use_terminal_background: bool = True,
) -> str | None:
    return pick_option_interactively(
        options,
        current_reasoning,
        title="Select a reasoning effort",
        theme_setting=theme_setting,
        use_terminal_background=use_terminal_background,
    )


def pick_ui_mode_interactively(
    options: list[str],
    current_mode: str,
    *,
    theme_setting: str = "auto",
    use_terminal_background: bool = True,
) -> str | None:
    return pick_option_interactively(
        options,
        current_mode,
        title="Select a UI mode",
        theme_setting=theme_setting,
        use_terminal_background=use_terminal_background,
    )
