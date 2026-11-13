from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from getpass import getpass
from pathlib import Path
from typing import Callable, Mapping

from ownpi.app.bootstrap import scaffold_app_home
from ownpi.app.paths import AppPaths
from ownpi.runtime.limits.recovery import (
    DEFAULT_PROVIDER_REQUEST_TIMEOUT_SECONDS,
    DEFAULT_PROVIDER_SSE_IDLE_TIMEOUT_SECONDS,
    DEFAULT_REQUEST_MAX_RETRIES,
    DEFAULT_RETRY_BASE_DELAY_SECONDS,
    DEFAULT_STREAM_MAX_RETRIES,
)
from ownpi.runtime.limits.workflow import (
    review_profile_selection_from_value,
)
from ownpi.schemas.runtime import LLMConfig
from ownpi.schemas.workflow_plan import (
    runtime_profile_selection_from_value,
)
from ownpi.utils.io import write_yaml

DEFAULT_OPENAI_MODELS = ["gpt-5.2", "gpt-5.3-codex", "gpt-5.4"]
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_KIMI_CODE_MODEL = "kimi-for-coding"
DEFAULT_KIMI_CODE_BASE_URL = "https://api.kimi.com/coding/"
DEFAULT_REASONING_OPTIONS = ["low", "medium", "high", "xhigh"]
DEFAULT_REVIEW_PROFILE_OPTIONS = ["auto", "standard", "rigorous", "exhaustive"]


PromptFn = Callable[[str], str]


@dataclass(frozen=True)
class SetupResult:
    config_path: Path
    env_path: Path
    provider: str
    model: str
    reviewer_model: str
    reasoning_effort: str
    reviewer_reasoning_effort: str
    base_url: str | None
    review_profile: str

    def to_dict(self) -> dict[str, object]:
        return {
            "config_path": str(self.config_path),
            "env_path": str(self.env_path),
            "provider": self.provider,
            "model": self.model,
            "reviewer_model": self.reviewer_model,
            "reasoning_effort": self.reasoning_effort,
            "reviewer_reasoning_effort": self.reviewer_reasoning_effort,
            "base_url": self.base_url,
            "review_profile": self.review_profile,
        }


@dataclass(frozen=True)
class LLMRuntimeReadiness:
    provider: str
    model: str
    is_mock: bool
    has_api_key: bool
    has_base_url: bool

    @property
    def ready_for_real_llm(self) -> bool:
        if self.is_mock:
            return False
        return self.has_api_key and self.has_base_url


def inspect_llm_readiness(config: LLMConfig, env: Mapping[str, str] | None = None) -> LLMRuntimeReadiness:
    resolved_env = env or os.environ
    api_key = resolved_env.get(config.api_key_env or "", "")
    base_url = config.base_url or resolved_env.get(config.base_url_env or "", "")
    if config.provider.lower() == "openai" and not base_url:
        base_url = DEFAULT_OPENAI_BASE_URL
    return LLMRuntimeReadiness(
        provider=config.provider,
        model=config.model,
        is_mock=config.provider.lower() == "mock",
        has_api_key=bool(api_key),
        has_base_url=bool(base_url),
    )


def build_openai_compatible_config_payload(
    *,
    model: str,
    reviewer_model: str | None = None,
    reasoning_effort: str = "medium",
    reviewer_reasoning_effort: str | None = None,
    default_profile: str = "research",
    review_profile: str | None = None,
    provider: str = "openai_compatible",
    api_style: str = "openai_responses",
    api_key_env: str = "OPENAI_API_KEY",
    base_url_env: str = "OPENAI_BASE_URL",
    base_url: str | None = None,
    model_options: list[str] | None = None,
    request_timeout_seconds: int = DEFAULT_PROVIDER_REQUEST_TIMEOUT_SECONDS,
    sse_idle_timeout_seconds: int = DEFAULT_PROVIDER_SSE_IDLE_TIMEOUT_SECONDS,
    request_max_retries: int | None = None,
    stream_max_retries: int | None = None,
    retry_backoff_seconds: float = DEFAULT_RETRY_BASE_DELAY_SECONDS,
) -> dict[str, object]:
    normalized_runtime_profile = runtime_profile_selection_from_value(default_profile)
    resolved_review_profile = review_profile_selection_from_value(
        review_profile if review_profile is not None else "auto"
    )
    resolved_model_options = sorted({*(model_options or DEFAULT_OPENAI_MODELS), model})
    request_max_retries = (
        DEFAULT_REQUEST_MAX_RETRIES
        if request_max_retries is None
        else request_max_retries
    )
    stream_max_retries = (
        DEFAULT_STREAM_MAX_RETRIES
        if stream_max_retries is None
        else stream_max_retries
    )
    retry_config = {
        "request_max_retries": request_max_retries,
        "stream_max_retries": stream_max_retries,
    }
    llm_entry = {
        "provider": provider,
        "model": model,
        "api_key_env": api_key_env,
        "base_url": base_url or "",
        "api_style": api_style,
        **({"reasoning_effort": reasoning_effort} if reasoning_effort else {}),
        "supports_hosted_web_search": True,
        "web_search_mode": "live",
    }
    return {
        "llm": {"profile": "default", "fallback_profile": "default"},
        "llm_profiles": {"default": llm_entry},
        "runtime": {
            "workspace_dir": "./workspace",
            "output_dir": "./outputs",
            "default_profile": normalized_runtime_profile.value,
            "permissions": {"preset": "full_access"},
            "review_profile": resolved_review_profile,
        },
    }


def _write_env_file(
    path: Path,
    *,
    api_key: str,
    base_url: str,
    api_key_env: str = "OPENAI_API_KEY",
    base_url_env: str = "OPENAI_BASE_URL",
) -> None:
    contents = "\n".join(
        [
            "# Generated by `ownpi setup`",
            f"{api_key_env}={api_key}",
            f"{base_url_env}={base_url}",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")


def _prompt_with_default(
    label: str,
    *,
    default: str | None = None,
    input_fn: PromptFn = input,
    allow_empty: bool = False,
) -> str:
    while True:
        suffix = f" [{default}]" if default is not None else ""
        value = input_fn(f"{label}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return default
        if allow_empty:
            return ""
        print("This field cannot be empty.")


def _prompt_secret(label: str, *, secret_input_fn: PromptFn = getpass) -> str:
    while True:
        if secret_input_fn is not getpass or (sys.stdin.isatty() and sys.stdout.isatty()):
            value = secret_input_fn(f"{label}: ").strip()
        else:
            value = input(f"{label}: ").strip()
        if value:
            return value
        print("This field cannot be empty.")


def _prompt_review_profile(
    label: str,
    *,
    default: str,
    input_fn: PromptFn = input,
) -> str:
    allowed = set(DEFAULT_REVIEW_PROFILE_OPTIONS)
    while True:
        raw_value = input_fn(
            f"{label} [{default}] (options: {', '.join(DEFAULT_REVIEW_PROFILE_OPTIONS)}): "
        ).strip()
        value = (raw_value or default).lower()
        if value in allowed:
            return value
        print(f"Please choose one of: {', '.join(DEFAULT_REVIEW_PROFILE_OPTIONS)}")


def _prompt_reasoning_effort(
    label: str,
    *,
    default: str,
    input_fn: PromptFn = input,
) -> str:
    allowed = set(DEFAULT_REASONING_OPTIONS)
    while True:
        raw_value = input_fn(
            f"{label} [{default}] (options: {', '.join(DEFAULT_REASONING_OPTIONS)}): "
        ).strip()
        value = raw_value or default
        if value in allowed:
            return value
        print(f"Please choose one of: {', '.join(DEFAULT_REASONING_OPTIONS)}")


def _prompt_choice(*, input_fn: PromptFn = input) -> str:
    print("Choose provider type:")
    print("  1) OpenAI-compatible")
    print("  2) Anthropic-compatible")
    while True:
        value = input_fn("Enter 1 or 2 [1]: ").strip() or "1"
        if value in {"1", "2"}:
            return value
        print("Please enter 1 or 2.")


def run_interactive_setup(
    paths: AppPaths,
    *,
    input_fn: PromptFn = input,
    secret_input_fn: PromptFn = getpass,
) -> SetupResult:
    scaffold_app_home(paths)
    print(f"Configuring OwnPI at: {paths.app_home}")
    provider_choice = _prompt_choice(input_fn=input_fn)
    is_anthropic = provider_choice == "2"
    if is_anthropic:
        default_base_url = DEFAULT_KIMI_CODE_BASE_URL
        api_key_label = "Anthropic-compatible API key"
        default_model = DEFAULT_KIMI_CODE_MODEL
        provider = "anthropic_compatible"
        api_style = "anthropic_messages"
        api_key_env = "KIMI_CODE_API_KEY"
        base_url_env = "KIMI_CODE_BASE_URL"
        setup_model_options = [DEFAULT_KIMI_CODE_MODEL]
        request_max_retries = DEFAULT_REQUEST_MAX_RETRIES
        stream_max_retries = DEFAULT_STREAM_MAX_RETRIES
        retry_backoff_seconds = 1.0
    else:
        default_base_url = "https://your-gateway.example.com/v1"
        api_key_label = "OpenAI-compatible API key"
        default_model = "gpt-5.4"
        provider = "openai_compatible"
        api_style = "openai_responses"
        api_key_env = "OPENAI_API_KEY"
        base_url_env = "OPENAI_BASE_URL"
        setup_model_options = DEFAULT_OPENAI_MODELS
        request_max_retries = DEFAULT_REQUEST_MAX_RETRIES
        stream_max_retries = DEFAULT_STREAM_MAX_RETRIES
        retry_backoff_seconds = DEFAULT_RETRY_BASE_DELAY_SECONDS

    api_key = _prompt_secret(api_key_label, secret_input_fn=secret_input_fn)
    base_url = _prompt_with_default("Base URL", default=default_base_url, input_fn=input_fn)
    model = _prompt_with_default("Default model", default=default_model, input_fn=input_fn)
    reasoning_effort = None if is_anthropic else _prompt_reasoning_effort(
        "Default reasoning effort", default="medium", input_fn=input_fn
    )
    reviewer_model = model
    reviewer_reasoning_effort = reasoning_effort
    review_profile = _prompt_review_profile(
        "Review profile",
        default="auto",
        input_fn=input_fn,
    )

    payload = build_openai_compatible_config_payload(
        model=model,
        reviewer_model=reviewer_model,
        reasoning_effort=reasoning_effort,
        reviewer_reasoning_effort=reviewer_reasoning_effort,
        review_profile=review_profile,
        provider=provider,
        api_style=api_style,
        api_key_env=api_key_env,
        base_url_env=base_url_env,
        base_url=base_url,
        model_options=setup_model_options,
        request_max_retries=request_max_retries,
        stream_max_retries=stream_max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
    )
    write_yaml(paths.config_path, payload)
    _write_env_file(
        paths.env_path,
        api_key=api_key,
        base_url=base_url,
        api_key_env=api_key_env,
        base_url_env=base_url_env,
    )

    return SetupResult(
        config_path=paths.config_path,
        env_path=paths.env_path,
        provider=provider,
        model=model,
        reviewer_model=reviewer_model,
        reasoning_effort=reasoning_effort,
        reviewer_reasoning_effort=reviewer_reasoning_effort,
        base_url=base_url,
        review_profile=review_profile,
    )
