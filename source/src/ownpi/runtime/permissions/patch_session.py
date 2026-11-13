"""Conservative, path-scoped identities for patch approvals."""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path
from typing import Any, Mapping

from ownpi.tools.patching.parser import parse_patch
from ownpi.tools.patching.models import PatchError
from ownpi.runtime.permissions.policy import lexical_absolute_path

PATCH_MATCH_KIND = 'patch_path_exact'
PATCH_MATCH_VERSION = 1


def build_patch_match(
    *, input_payload: Mapping[str, Any], project_root: Path,
) -> tuple[str, int, str, tuple[str, ...]] | None:
    if set(input_payload) != {'patch'}:
        return None
    patch = input_payload.get('patch')
    if not isinstance(patch, str) or not patch.strip():
        return None
    try:
        operations = parse_patch(patch)
    except PatchError:
        return None
    if not operations:
        return None
    try:
        root = project_root.resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    operation_keys: list[dict[str, str]] = []
    for operation in operations:
        path = _stable_patch_path(operation.path, root)
        if path is None:
            return None
        item = {'path': path}
        if operation.move_path is not None:
            destination = _stable_patch_path(operation.move_path, root)
            if destination is None or destination == path:
                return None
            item['destination'] = destination
        operation_keys.append(item)
    components = tuple(sorted({
        _digest({
            'schema': 'ownpi.patch_path.session_match.v1',
            'operation': operation,
        })
        for operation in operation_keys
    }))
    digest = _digest({
        'schema': 'ownpi.patch_apply.session_match.v1',
        'operations': list(components),
    })
    return PATCH_MATCH_KIND, PATCH_MATCH_VERSION, digest, components


def _digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _stable_patch_path(raw_path: Path, project_root: Path) -> str | None:
    if '..' in raw_path.parts:
        return None
    path = lexical_absolute_path(raw_path, cwd=project_root)
    # Do not reuse a grant if an existing path component redirects through a
    # symlink. The ordinary execution path still performs its normal checks.
    candidate = Path(path.anchor)
    for part in path.parts[1:]:
        candidate = candidate / part
        try:
            mode = candidate.lstat().st_mode
        except FileNotFoundError:
            continue
        except OSError:
            return None
        if stat.S_ISLNK(mode):
            return None
    return str(path)
