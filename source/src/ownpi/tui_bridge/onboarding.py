"""Frontend startup configuration: collect, validate, then save and enter chat.

The existing Runtime schema remains the authority. Existing mappings and dotenv
entries are preserved; secrets never enter command arguments or progress logs.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from getpass import getpass
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Callable
from urllib.parse import urlsplit
from uuid import uuid4

import yaml

from ownpi.app.paths import AppPaths
from ownpi.schemas.runtime import UserConfig


class SetupCancelled(Exception):
    pass


class NeedsSetup(Exception):
    pass


class ConfigurationProblem(ValueError):
    """A safe, field-level diagnostic, without YAML snippets or credential values."""


@dataclass
class Console:
    read: Callable[[str], str] = input
    secret: Callable[[str], str] = getpass
    write: Callable[[str], None] = print

    def ask(
        self,
        label: str,
        *,
        default: str = "",
        secret: bool = False,
        valid: Callable[[str], bool] = bool,
    ) -> str:
        while True:
            suffix = f" [{default}]" if default else ""
            try:
                value = (self.secret if secret else self.read)(
                    f"{label}{suffix}: "
                ).strip()
            except (EOFError, KeyboardInterrupt) as error:
                raise SetupCancelled from error
            if value == "\x1b":
                raise SetupCancelled
            value = value or default
            if valid(value):
                return value
            self.write("Please enter a valid value; Ctrl+C cancels setup.")

    def choose(
        self, label: str, choices: list[tuple[str, str]], default: str = "1"
    ) -> str:
        self.write(label)
        for index, (_, description) in enumerate(choices, 1):
            self.write(f"  {index}) {description}")
        value = self.ask(
            "Choose",
            default=default,
            valid=lambda v: v.isdigit() and 1 <= int(v) <= len(choices),
        )
        return choices[int(value) - 1][0]


def _ask(console: Console | None, label: str, **kwargs: Any) -> str:
    if console is None:
        raise NeedsSetup(label)
    return console.ask(label, **kwargs)


def _choose(console: Console | None, label: str, choices: list[tuple[str, str]]) -> str:
    if console is None:
        raise NeedsSetup(label)
    return console.choose(label, choices)


def _mapping(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.setdefault(key, {})
    if value is None:
        value = parent[key] = {}
    if not isinstance(value, dict):
        raise ConfigurationProblem(f"{key} must be a configuration mapping.")
    return value


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def valid_url(value: str) -> bool:
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        return (
            url.scheme in {"http", "https"}
            and bool(host)
            and url.username is None
            and url.password is None
            and not url.query
            and not url.fragment
            and not any(c.isspace() or ord(c) < 32 for c in value)
            and not host.endswith((".example.com", ".example.org", ".invalid"))
            and host not in {"example.com", "example.org"}
            and (url.port is None or 0 < url.port < 65536)
        )
    except ValueError:
        return False


def _token(value: str) -> bool:
    return bool(value) and not any(
        c.isspace() or ord(c) < 32 or c in "\"'" for c in value
    )


def _dotenv(data: bytes | None) -> dict[str, str]:
    result = {}
    for line in (data or b"").decode("utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def _complete_model(
    profiles: dict[str, Any], name: str, console: Console | None
) -> None:
    profile = _mapping(profiles, name)
    if not _text(profile.get("provider")):
        profile["provider"] = _choose(
            console,
            f"Provider for {name}",
            [
                ("openai", "OpenAI"),
                ("openai_compatible", "OpenAI-compatible service"),
                ("anthropic_compatible", "Anthropic-compatible service"),
            ],
        )
        profile.setdefault(
            "api_style",
            "anthropic_messages"
            if profile["provider"] == "anthropic_compatible"
            else "openai_responses",
        )
    if not _text(profile.get("model")):
        profile["model"] = _ask(console, f"Model name for {name}", valid=_token)


def _complete(
    raw: dict[str, Any],
    env: dict[str, str],
    project: Path,
    paths: AppPaths,
    console: Console | None,
) -> tuple[UserConfig, dict[str, str]]:
    secrets: dict[str, str] = {}
    if "llms" in raw:
        profiles = _mapping(raw, "llms")
    elif "llm_profiles" in raw or "llm" not in raw or "profile" in _mapping(raw, "llm"):
        profiles = _mapping(raw, "llm_profiles")
    else:
        profiles = {"default": _mapping(raw, "llm")}
    if not profiles:
        profiles["default"] = {}
    selector = _mapping(raw, "llm") if "llm" in raw or "llm_profiles" in raw else {}
    selected = _text(env.get("OWNPI_LLM_PROFILE")) or _text(selector.get("profile"))
    if not selected and "llm_profiles" in raw and len(profiles) == 1:
        selected = next(iter(profiles))
        selector["profile"] = selected
    if selected not in profiles and (selected or "llm_profiles" in raw):
        selected = _choose(
            console, "Model profile", [(name, name) for name in profiles]
        )
        _mapping(raw, "llm")["profile"] = selected
        if env.get("OWNPI_LLM_PROFILE"):
            env["OWNPI_LLM_PROFILE"] = selected
    selected = selected or (
        "default" if "default" in profiles else next(iter(profiles))
    )
    binding = (
        selected
        if "llm_profiles" in raw
        or selector.get("profile")
        or env.get("OWNPI_LLM_PROFILE")
        else None
    )
    for name in profiles:
        _complete_model(profiles, name, console)
    runtime = _mapping(raw, "runtime")
    if not _text(runtime.get("workspace_dir")):
        runtime["workspace_dir"] = str(project)
    if not _text(runtime.get("output_dir")):
        runtime["output_dir"] = str(paths.app_home / "outputs")
    if not _text(runtime.get("default_profile")):
        runtime["default_profile"] = _choose(
            console,
            "Default mode",
            [
                ("task", "Task — answer or complete a concrete request"),
                ("research", "Research — work through research stages"),
            ],
        )
    permission = _mapping(runtime, "permissions")
    if not _text(permission.get("preset")):
        permission["preset"] = _choose(
            console,
            "Permissions",
            [
                ("ask_for_approval", "Ask for approval"),
                ("approve_for_me", "Automatic approval review"),
                ("full_access", "Full access"),
            ],
        )
    try:
        parsed = UserConfig.from_dict(raw, llm_profile=binding)
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise ConfigurationProblem(
            "Existing configuration contains invalid settings; it has not been overwritten."
        ) from error
    # Check only profiles actually used by enabled agents, not unused accounts.
    used = {selected}
    for agent in parsed.agents.get_enabled_pipeline():
        name = (
            selected
            if agent.llm == "default" or (agent.llm == "reviewer_model" and binding)
            else agent.llm
        )
        if name not in profiles:
            _complete_model(profiles, name, console)
        used.add(name)
    for name in sorted(used):
        profile = profiles[name]
        provider = profile["provider"].lower()
        if provider == "mock":
            continue
        key_name = _text(profile.get("api_key_env"))
        if not key_name:
            key_name = (
                "ANTHROPIC_API_KEY"
                if provider == "anthropic_compatible"
                else "OPENAI_API_KEY"
            )
            profile["api_key_env"] = key_name
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_name):
            raise ConfigurationProblem(
                "api_key_env must name a valid environment variable."
            )
        if not _text(env.get(key_name)):
            secrets[key_name] = env[key_name] = _ask(
                console, f"API key for {name} (hidden)", secret=True, valid=_token
            )
        url = _text(profile.get("base_url")) or _text(
            env.get(profile.get("base_url_env", ""))
        )
        if provider == "openai" and not url:
            url = "https://api.openai.com/v1"
        if not valid_url(url):
            profile["base_url"] = _ask(console, f"Base URL for {name}", valid=valid_url)
    return UserConfig.from_dict(raw, llm_profile=binding), secrets


def _read(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


def _atomic(path: Path, data: bytes, mode: int = 0o600) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _save(changes: dict[Path, bytes], originals: dict[Path, bytes | None]) -> None:
    # Validate *both* files before writing either; don't overwrite external edits.
    if any(_read(path) != content for path, content in originals.items()):
        raise ConfigurationProblem(
            "Configuration changed during setup. Run ownpi-tui again to use the latest files."
        )
    written = []
    try:
        for path, content in changes.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            if originals[path] is not None:
                backup = path.with_name(f"{path.name}.before-setup-{uuid4().hex[:12]}")
                _atomic(backup, originals[path])
            _atomic(path, content)
            written.append(path)
    except BaseException:
        for path in reversed(written):
            if originals[path] is None:
                path.unlink()
            else:
                _atomic(path, originals[path])
        raise


def prepare_configuration(
    config_path: Path,
    paths: AppPaths,
    project: Path,
    *,
    interactive: bool,
    console: Console | None = None,
    environ: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return the child environment; no writes or questions for a ready config."""
    original = _read(config_path)
    env_original = _read(paths.env_path)
    env = {
        **_dotenv(env_original),
        **{
            k: v
            for k, v in (os.environ if environ is None else environ).items()
            if v.strip()
        },
    }
    console = console or Console()
    malformed = False
    try:
        raw = yaml.safe_load(original) if original else {}
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise ConfigurationProblem("Configuration must be a YAML mapping.")
    except (yaml.YAMLError, UnicodeError, ConfigurationProblem):
        raw, malformed = {}, True
    pending = deepcopy(raw)
    validation_error = False
    try:
        if malformed:
            raise ConfigurationProblem("The configuration file cannot be parsed.")
        _complete(pending, dict(env), project, paths, None)
        if pending == raw:
            return env
    except NeedsSetup:
        pass
    except ConfigurationProblem:
        validation_error = True
    if not interactive:
        raise ConfigurationProblem(
            "Configuration needs setup. Run ownpi-tui in an interactive terminal to complete it."
        )
    console.write(
        "\nOwnPI setup\nComplete the missing settings to start chatting. Ctrl+C cancels without saving."
    )
    console.write(f"Configuration: {config_path}\nWorking directory: {project}")
    if malformed or validation_error:
        choice = console.choose(
            "Existing settings could not be validated.",
            [
                ("cancel", "Keep existing files and cancel"),
                ("replace", "Back up existing configuration and set up afresh"),
            ],
        )
        if choice == "cancel":
            raise SetupCancelled
        raw = {}
    pending = deepcopy(raw)
    parsed, secrets = _complete(pending, env, project, paths, console)
    console.write(
        f"\nModel: {parsed.llm.model}\nMode: {'Task' if parsed.runtime.default_profile == 'task' else 'Research'}"
        f"\nPermissions: {parsed.runtime.permissions.preset.value.replace('_', ' ')}"
    )
    if console.ask(
        "Save and open chat?",
        default="y",
        valid=lambda v: v.lower() in {"y", "yes", "n", "no"},
    ).lower() in {"n", "no"}:
        raise SetupCancelled
    changes = {}
    if secrets:
        lines = (env_original or b"").decode().splitlines()
        lines = [line for line in lines if line.split("=", 1)[0].strip() not in secrets]
        lines.extend(f"{key}={value}" for key, value in secrets.items())
        changes[paths.env_path] = ("\n".join(lines) + "\n").encode()
    if pending != raw or original is None or malformed or validation_error:
        changes[config_path] = yaml.safe_dump(
            pending, sort_keys=False, allow_unicode=True
        ).encode()
    _save(changes, {config_path: original, paths.env_path: env_original})
    console.write("Configuration saved. Opening OwnPI…\n")
    return env
