from __future__ import annotations

import os
import re
from collections.abc import Mapping


_SAFE_EXACT_KEYS = {
    'COLORTERM',
    'HOME',
    'LANG',
    'LC_ALL',
    'LC_CTYPE',
    'LOGNAME',
    'PATH',
    'PWD',
    'SHELL',
    'SHLVL',
    'TERM',
    'TMP',
    'TMPDIR',
    'TEMP',
    'USER',
    'USERNAME',
    'VIRTUAL_ENV',
}
_SENSITIVE_EXACT_KEYS = {
    'ANTHROPIC_API_KEY',
    'ANTHROPIC_AUTH_TOKEN',
    'ANTHROPIC_CUSTOM_HEADERS',
    'CLAUDE_CODE_OAUTH_TOKEN',
    'OPENAI_API_KEY',
    'OTEL_EXPORTER_OTLP_HEADERS',
    'AWS_SECRET_ACCESS_KEY',
    'AWS_SESSION_TOKEN',
    'GOOGLE_APPLICATION_CREDENTIALS',
    'AZURE_CLIENT_SECRET',
    'ACTIONS_ID_TOKEN_REQUEST_TOKEN',
    'ACTIONS_RUNTIME_TOKEN',
    'GITHUB_TOKEN',
}
_SENSITIVE_KEY_RE = re.compile(
    r'(?:^|_)(?:API[_-]?KEY|AUTH[_-]?TOKEN|TOKEN|SECRET|PASSWORD|PASSWD|PRIVATE[_-]?KEY|CREDENTIALS?|COOKIE)(?:_|$)',
    re.IGNORECASE,
)


def build_subprocess_env(
    *,
    extra_env: Mapping[str, str] | None = None,
    base_env: Mapping[str, str] | None = None,
    scrub: bool = True,
    allow_explicit_sensitive: bool = True,
) -> dict[str, str]:
    """Build child-process environment with sensitive parent values removed.

    ``extra_env`` is treated as an explicit per-call override. By default it is
    allowed to reintroduce sensitive values because command tools and MCP/LSP
    config may intentionally need a scoped credential. Ambient parent secrets
    are scrubbed unless ``scrub`` is false.
    """

    source = os.environ if base_env is None else base_env
    env: dict[str, str] = {}
    for key, value in source.items():
        key_text = str(key)
        if not key_text:
            continue
        if scrub and is_sensitive_env_key(key_text):
            continue
        env[key_text] = str(value)
    for key, value in (extra_env or {}).items():
        key_text = str(key)
        if not key_text:
            continue
        if scrub and not allow_explicit_sensitive and is_sensitive_env_key(key_text):
            continue
        env[key_text] = str(value)
    return env


def is_sensitive_env_key(key: str) -> bool:
    normalized = str(key or '').strip().upper()
    if not normalized:
        return False
    if normalized in _SAFE_EXACT_KEYS:
        return False
    if normalized in _SENSITIVE_EXACT_KEYS:
        return True
    return bool(_SENSITIVE_KEY_RE.search(normalized))
