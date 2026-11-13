from __future__ import annotations

from pathlib import Path

from ownpi.runtime.permissions.policy import lexical_absolute_path
from ownpi.tools.backends.remote_workflow.profiles import RemoteProfile
from ownpi.tools.backends.remote_workflow.shell import remote_arg


def _pull_source_destination(profile: RemoteProfile, item: str) -> tuple[str, str, Path]:
    raw = str(item or "").strip()
    if raw in {"", "."}:
        return (
            remote_arg(profile, f"{profile.remote_root}/"),
            f"{profile.local_root}/",
            profile.local_root,
        )
    path = Path(raw)
    if path.is_absolute():
        if ".." in path.parts:
            raise ValueError(f"pull path must not contain '..': {item}")
        remote_path = path.as_posix().rstrip("/")
        if not remote_path or remote_path == "/":
            raise ValueError("pull absolute path must point to a file or directory, not remote root")
        mirror_parent = profile.local_root / "remote_artifacts" / Path(*path.parts[1:-1])
        return (
            remote_arg(profile, remote_path),
            f"{mirror_parent}/",
            mirror_parent / path.name,
        )

    normalized = _normalize_profile_remote_path(
        profile,
        raw,
        command_name="pull",
    )
    if normalized == ".":
        return (
            remote_arg(profile, f"{profile.remote_root}/"),
            f"{profile.local_root}/",
            profile.local_root,
        )
    local_parent = profile.local_root / Path(normalized).parent
    return (
        remote_arg(profile, f"{profile.remote_root}/{normalized}"),
        f"{local_parent}/",
        local_parent / Path(normalized).name,
    )


def deploy_source_path(profile: RemoteProfile, item: str) -> Path:
    candidate = Path(item).expanduser()
    normalized = str(candidate) if candidate.is_absolute() else _normalize_relative_path(item)
    source = lexical_absolute_path(normalized, cwd=profile.local_root)
    if not _is_relative_to(source, profile.local_root):
        raise ValueError(f'path escapes local root: {item}')
    return source


def deploy_relative_path(profile: RemoteProfile, item: str) -> str:
    source = deploy_source_path(profile, item)
    try:
        return str(source.relative_to(profile.local_root))
    except ValueError:
        return str(source.resolve().relative_to(profile.local_root.resolve()))


def _normalize_relative_path(value: str, *, command_name: str = "path", absolute_hint: str = "") -> str:
    raw = str(value or "").strip()
    if raw in {"", "."}:
        return "."
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        hint = f"; {absolute_hint}" if absolute_hint else ""
        raise ValueError(f"{command_name} path must be relative and stay inside profile.remote_root: {value}{hint}")
    return path.as_posix().rstrip("/")


def _normalize_profile_remote_path(
    profile: RemoteProfile,
    value: str,
    *,
    command_name: str,
    absolute_hint: str = "",
) -> str:
    raw = str(value or "").strip()
    if raw in {"", "."}:
        return "."
    remote_root = str(profile.remote_root or "").rstrip("/")
    if raw.startswith("/"):
        if raw == remote_root:
            return "."
        remote_prefix = f"{remote_root}/"
        if raw.startswith(remote_prefix):
            raw = raw[len(remote_prefix):]
        else:
            hint = f"; {absolute_hint}" if absolute_hint else ""
            raise ValueError(
                f"{command_name} path must be relative and stay inside profile.remote_root: {value}{hint}"
            )
    return _normalize_relative_path(raw, command_name=command_name, absolute_hint=absolute_hint)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
