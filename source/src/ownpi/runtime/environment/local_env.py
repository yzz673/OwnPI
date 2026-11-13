from __future__ import annotations

import os
from pathlib import Path
import shlex
from typing import Any, Mapping

from ownpi.runtime.environment.contracts import EnvironmentDescriptor
from ownpi.runtime.environment.subprocess_env import is_sensitive_env_key


RESOURCE_MIRROR_ENV_KEYS = {
    'huggingface_endpoint': 'HF_ENDPOINT',
}


def local_env_bin_dir(default_python: str) -> str:
    text = str(default_python or '').strip()
    if not text:
        return ''
    return str(Path(text).expanduser().parent)


def target_python_from_descriptor(descriptor: EnvironmentDescriptor | Mapping[str, Any] | None) -> str:
    if descriptor is None:
        return ''
    if isinstance(descriptor, EnvironmentDescriptor):
        return str(descriptor.python_executable or '').strip()
    return str(descriptor.get('python_executable', '') or '').strip()


def target_python_from_runtime_config(runtime_config: Mapping[str, Any] | None) -> str:
    payload = runtime_config or {}
    local_cfg = payload.get('local', {}) if isinstance(payload.get('local'), Mapping) else {}
    return str(local_cfg.get('default_python', '') or '').strip()


def target_env_name_from_descriptor(descriptor: EnvironmentDescriptor | Mapping[str, Any] | None) -> str:
    if descriptor is None:
        return ''
    if isinstance(descriptor, EnvironmentDescriptor):
        return str(descriptor.env_name or '').strip()
    return str(descriptor.get('env_name', '') or '').strip()


def build_local_env_overrides(
    *,
    descriptor: EnvironmentDescriptor,
    runtime_config: Mapping[str, Any] | None,
    request_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    payload = runtime_config or {}
    local_cfg = payload.get('local', {}) if isinstance(payload.get('local'), dict) else {}
    resource_cfg = payload.get('resources', {}) if isinstance(payload.get('resources'), dict) else {}
    env: dict[str, str] = {}
    for source in (local_cfg.get('env_vars'), _resource_mirror_env(resource_cfg), resource_cfg.get('env_vars')):
        if isinstance(source, dict):
            env.update(_safe_env_mapping(source))
    path_entries = _path_entries(local_cfg.get('path_prepend'))
    target_bin = local_env_bin_dir(descriptor.python_executable)
    if target_bin:
        path_entries.insert(0, target_bin)
    if path_entries:
        current_path = str((request_env or {}).get('PATH') or os.environ.get('PATH') or os.defpath)
        env['PATH'] = os.pathsep.join(_dedupe([*path_entries, *current_path.split(os.pathsep)]))
    if descriptor.env_name:
        if descriptor.activation_kind == 'conda':
            env.setdefault('CONDA_DEFAULT_ENV', descriptor.env_name)
        elif descriptor.activation_kind == 'venv':
            python_path = Path(descriptor.python_executable).expanduser()
            if len(python_path.parents) > 1:
                env.setdefault('VIRTUAL_ENV', str(python_path.parents[1]))
    env.update(_safe_env_mapping(request_env or {}, allow_sensitive=True))
    if descriptor.temp_artifact_root:
        env['OWNPI_TEMP_ARTIFACT_ROOT'] = str(descriptor.temp_artifact_root)
    return env


def rewrite_argv_for_local_env(argv: list[str], descriptor: EnvironmentDescriptor) -> list[str]:
    if not argv or not descriptor.python_executable:
        return list(argv)
    executable = Path(argv[0])
    if executable.is_absolute():
        return list(argv)
    name = executable.name
    if name in {'python', 'python3'} or name.startswith('python3.'):
        return [descriptor.python_executable, *argv[1:]]
    if name in {'pip', 'pip3'}:
        return [descriptor.python_executable, '-m', 'pip', *argv[1:]]
    if name == 'pytest':
        return [descriptor.python_executable, '-m', 'pytest', *argv[1:]]
    return list(argv)


def rewrite_shell_command_for_local_env(command: str, descriptor: EnvironmentDescriptor) -> str:
    if not descriptor.python_executable:
        return command
    stripped = str(command or '')
    leading = stripped.lstrip()
    prefix = stripped[: len(stripped) - len(leading)]
    target_python = shlex.quote(descriptor.python_executable)
    replacements = (
        ('python ', f'{target_python} '),
        ('python3 ', f'{target_python} '),
        ('pip ', f'{target_python} -m pip '),
        ('pip3 ', f'{target_python} -m pip '),
        ('pytest ', f'{target_python} -m pytest '),
    )
    for executable, replacement in replacements:
        if leading.startswith(executable):
            return f'{prefix}{replacement}{leading[len(executable):]}'
    if leading in {'python', 'python3'}:
        return f'{prefix}{target_python}'
    if leading in {'pip', 'pip3'}:
        return f'{prefix}{target_python} -m pip'
    if leading == 'pytest':
        return f'{prefix}{target_python} -m pytest'
    return command


def local_env_metadata(
    *,
    descriptor: EnvironmentDescriptor,
    runtime_config: Mapping[str, Any] | None,
    injected_env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    resource_cfg = (runtime_config or {}).get('resources', {}) if isinstance((runtime_config or {}).get('resources'), dict) else {}
    mirrors = resource_cfg.get('mirrors', {}) if isinstance(resource_cfg.get('mirrors'), dict) else {}
    return {
        'target_env_name': descriptor.env_name,
        'target_python_executable': descriptor.python_executable,
        'activation_kind': descriptor.activation_kind,
        'resource_runtime': {
            'configured_mirrors': sorted(str(key) for key in mirrors if str(key).strip()),
            'injected_env_keys': sorted(str(key) for key in (injected_env or {}) if str(key).strip()),
        },
    }


def dependency_recovery_hint(*, stderr: str, descriptor: EnvironmentDescriptor) -> str:
    text = str(stderr or '')
    if not any(marker in text for marker in ('ModuleNotFoundError', 'ImportError', 'PackageNotFoundError')):
        return ''
    target_python = descriptor.python_executable or 'python'
    return (
        'Missing dependency appears fixable. Request permission to install it into the configured target env with '
        f'`{target_python} -m pip install <package>` and then rerun the failed command.'
    )


def _resource_mirror_env(resource_cfg: Mapping[str, Any]) -> dict[str, str]:
    mirrors = resource_cfg.get('mirrors', {}) if isinstance(resource_cfg.get('mirrors'), dict) else {}
    env: dict[str, str] = {}
    for key, env_key in RESOURCE_MIRROR_ENV_KEYS.items():
        value = str(mirrors.get(key, '') or '').strip()
        if value:
            env[env_key] = value
    return env


def _safe_env_mapping(value: Mapping[str, Any], *, allow_sensitive: bool = False) -> dict[str, str]:
    env: dict[str, str] = {}
    for key, item in value.items():
        key_text = str(key or '').strip()
        if not key_text:
            continue
        if not allow_sensitive and is_sensitive_env_key(key_text):
            continue
        env[key_text] = str(item)
    return env


def _path_entries(value: Any) -> list[str]:
    if value is None:
        return []
    raw_items = value if isinstance(value, list) else [value]
    entries: list[str] = []
    for item in raw_items:
        text = str(item or '').strip()
        if text:
            entries.append(text)
    return entries


def _dedupe(entries: list[str]) -> list[str]:
    output: list[str] = []
    for entry in entries:
        if not entry or entry in output:
            continue
        output.append(entry)
    return output
