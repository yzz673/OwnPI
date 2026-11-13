"""Conservative session identities for ordinary local command approvals."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping

from ownpi.runtime.permissions.bash_policy import analyze_bash_command

EXEC_COMMAND_MATCH_KIND = 'exec_command_exact'
EXEC_COMMAND_MATCH_VERSION = 1
_EXEC_FIELDS = {
    'cmd', 'program', 'args', 'justification', 'login', 'max_output_tokens',
    'prefix_rule', 'sandbox_permissions', 'shell', 'tty', 'use_pty',
    'pty_columns', 'pty_rows', 'cwd', 'workdir', 'yield_time_ms', 'env_overrides',
}


def build_exec_command_match(
    *,
    input_payload: Mapping[str, Any],
    environment: Mapping[str, Any],
    effective_permissions: Mapping[str, Any] | None = None,
    effects: Mapping[str, Any] | None = None,
) -> tuple[str, int, str] | None:
    """Return a secret-safe typed key, or None when reuse is unsafe."""
    if set(input_payload) - _EXEC_FIELDS:
        return None
    env_overrides = input_payload.get('env_overrides')
    if env_overrides is not None and not isinstance(env_overrides, Mapping):
        return None
    if env_overrides:
        return None
    prefix = input_payload.get('prefix_rule')
    if prefix not in (None, []) and not isinstance(prefix, list):
        return None
    if _nonempty_sequence(prefix) and any(not isinstance(item, str) or not item.strip() for item in prefix):
        return None

    program = str(input_payload.get('program', '') or '').strip()
    args = input_payload.get('args', [])
    if not isinstance(args, list):
        return None
    args = [str(item) for item in args]
    command_text = str(input_payload.get('cmd', '') or '')
    if program and command_text.strip():
        return None
    shell = str(input_payload.get('shell', '') or '').strip() or _default_shell()
    login = bool(input_payload.get('login', True))
    if program:
        executable = {'kind': 'argv', 'program': program, 'args': args}
        command_identity: object = [program, *args]
    else:
        analysis = analyze_bash_command(command_text)
        safe_simple = (
            analysis.kind == 'simple'
            and len(analysis.commands) == 1
            and not analysis.control_operators
            and not analysis.unknown_constructs
            and not analysis.commands[0].env_assignments
            and not analysis.commands[0].redirects
            # The Bash analyzer is also used with non-Bash shells. Keep exact
            # text whenever quote removal could conceal shell-specific syntax.
            and Path(shell).name in {'sh', 'bash', 'zsh'}
            # Quoting the command word can disable aliases or shell precommands.
            and re.match(r'^[A-Za-z0-9_./-]+(?:\s|$)', command_text.lstrip()) is not None
            and not re.search(r'[~{}!#$*?\[\]()<>;&|\\\r\n]', command_text)
        )
        executable = {'kind': 'shell', 'shell': shell, 'login': login}
        command_identity = (
            list(analysis.commands[0].argv)
            if safe_simple
            else {'raw': command_text}
        )

    cwd = _resolved_cwd(input_payload, environment)
    if cwd is None:
        return None
    kind = EXEC_COMMAND_MATCH_KIND
    if _nonempty_sequence(prefix):
        kind = 'exec_command_prefix'
        command_identity = {'prefix': list(prefix)}
    tty = bool(input_payload.get('tty')) if 'tty' in input_payload else bool(input_payload.get('use_pty', False))
    payload = {
        'schema': f'ownpi.{kind}.session_match.v1',
        'executable': executable,
        'command': command_identity,
        'cwd': cwd,
        'tty': tty,
        'pty_columns': _positive_int(input_payload.get('pty_columns')),
        'pty_rows': _positive_int(input_payload.get('pty_rows')),
        'sandbox_permissions': str(input_payload.get('sandbox_permissions', 'use_default') or 'use_default'),
        'environment': dict(environment),
        'effective_permissions': dict(effective_permissions or {}),
        'effects': dict(effects or {}),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str)
    return kind, EXEC_COMMAND_MATCH_VERSION, hashlib.sha256(encoded.encode()).hexdigest()


def _resolved_cwd(input_payload: Mapping[str, Any], environment: Mapping[str, Any]) -> str | None:
    raw = str(input_payload.get('workdir') or input_payload.get('cwd') or '')
    descriptor = environment.get('descriptor')
    descriptor_root = descriptor.get('workspace_root') if isinstance(descriptor, Mapping) else ''
    root = str(environment.get('project_root') or environment.get('cwd') or descriptor_root or '')
    if not root and raw and Path(raw).is_absolute():
        root = raw
    if not root:
        return None
    try:
        path = Path(raw).expanduser() if raw else Path(root).expanduser()
        if not path.is_absolute():
            path = Path(root).expanduser() / path
        try:
            return str(path.resolve(strict=True))
        except FileNotFoundError:
            return str(path.resolve())
    except (OSError, RuntimeError, ValueError):
        return None


def _default_shell() -> str:
    candidate = str(os.environ.get('SHELL', '') or '')
    return candidate if candidate and Path(candidate).exists() else '/bin/sh'


def _positive_int(value: Any) -> int | None:
    if value in (None, ''):
        return None
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return None


def _nonempty_sequence(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and any(str(item).strip() for item in value)
