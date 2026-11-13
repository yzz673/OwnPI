"""Versioned session-match metadata shared by requests and stored grants."""

from __future__ import annotations

import re
from typing import Any, Mapping


def read_session_identity(data: Mapping[str, Any]) -> dict[str, Any]:
    kind = data.get('session_match_kind')
    version = data.get('session_match_version')
    digest = data.get('session_match_digest')
    if (
        kind not in (
            'exec_command_exact', 'exec_command_prefix',
            'patch_path_exact', 'mcp_remote_tool',
        )
        or type(version) is not int
        or version != 1
        or not isinstance(digest, str)
        or len(digest) != 64
        or any(char not in '0123456789abcdef' for char in digest)
    ):
        return {
            'session_match_kind': '',
            'session_match_version': None,
            'session_match_digest': '',
        }
    return {
        'session_match_kind': kind,
        'session_match_version': version,
        'session_match_digest': digest,
    }


def valid_session_identity(value: Any) -> bool:
    return bool(read_session_identity({
        'session_match_kind': value.session_match_kind,
        'session_match_version': value.session_match_version,
        'session_match_digest': value.session_match_digest,
    })['session_match_kind'])


def read_session_components(data: Mapping[str, Any]) -> tuple[str, ...]:
    components = data.get('session_match_components', ())
    if not isinstance(components, (list, tuple)) or not components:
        return ()
    if any(not isinstance(item, str) or re.fullmatch(r'[0-9a-f]{64}', item) is None for item in components):
        return ()
    return tuple(dict.fromkeys(components))
