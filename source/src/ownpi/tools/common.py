from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from ownpi.runtime.permissions.models import FileSystemAccessMode
from ownpi.runtime.permissions.policy import FileSystemPolicyMatcher, lexical_absolute_path


def resolve_workspace_path(
    project_root: Path,
    raw_path: Any,
    *,
    default: str = '.',
    require_inside_workspace: bool = False,
) -> Path:
    candidate = Path(str(raw_path or default)).expanduser()
    if not candidate.is_absolute():
        candidate = project_root / candidate
    resolved = candidate.resolve()
    if require_inside_workspace:
        root = project_root.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(f'path must stay inside workspace: {resolved}') from exc
    return resolved


def resolve_file_tool_path(
    context: Any,
    raw_path: Any,
    *,
    tool_name: str,
    access: Literal['read', 'write'],
    default: str = ".",
    require_inside_workspace: bool = False,
) -> Path:
    del tool_name
    path = lexical_absolute_path(raw_path or default, cwd=context.project_root)
    permissions = context.effective_permissions
    if permissions is None:
        raise PermissionError('canonical effective permissions are required for file access')
    decision = FileSystemPolicyMatcher(permissions.profile.file_system).decide(
        path, requested_access=FileSystemAccessMode(access), cwd=context.project_root,
    )
    if not decision.allowed:
        raise PermissionError(f'filesystem policy denied {access}: {path}')
    return resolve_workspace_path(
        context.project_root,
        raw_path,
        default=default,
        require_inside_workspace=require_inside_workspace,
    )


def display_path(path: Path, *, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def metadata_for_context(context: Any, **extra: Any) -> dict[str, Any]:
    payload = {
        'stage': context.stage.value,
        'attempt': context.attempt,
        'continuation_index': context.continuation_index,
        'query_chain_id': str(getattr(context, 'query_chain_id', '') or '').strip(),
        'query_chain_depth': int(getattr(context, 'query_chain_depth', 0) or 0),
    }
    payload.update({key: value for key, value in extra.items() if value is not None})
    return payload


def clamp_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value if value is not None else default)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def truncate_text(text: str, *, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[: max(0, limit - 24)] + '\n...[truncated]...', True
