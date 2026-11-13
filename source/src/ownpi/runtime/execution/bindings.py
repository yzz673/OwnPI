from __future__ import annotations

import hashlib
from typing import Any

from ownpi.runtime.artifacts.layout import RunArtifactScope


def remote_run_scope_from_context(context: Any) -> RunArtifactScope | None:
    state_store = getattr(context, 'state_store', None)
    state = getattr(state_store, 'state', None)
    run_metadata = getattr(state, 'run_metadata', None)
    session_id = str(getattr(run_metadata, 'session_id', '') or '').strip()
    task_id = str(getattr(run_metadata, 'task_id', '') or '').strip()
    run_id = str(getattr(run_metadata, 'run_id', '') or '').strip()
    if not (session_id and task_id and run_id):
        return None
    return RunArtifactScope(session_id=session_id, task_id=task_id, run_id=run_id)


def remote_run_scoped_tmux_session(
    context: Any,
    *,
    artifact_root: str = '',
) -> str:
    scope = remote_run_scope_from_context(context)
    if scope is None:
        return ''
    root = str(artifact_root or '').strip()
    if not root:
        return scope.tmux_session
    digest = hashlib.sha256(root.encode('utf-8')).hexdigest()[:10]
    base = scope.tmux_session[: 80 - len(digest) - 1]
    return f'{base}_{digest}'
