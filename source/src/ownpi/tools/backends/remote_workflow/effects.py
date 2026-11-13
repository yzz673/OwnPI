from __future__ import annotations

from pathlib import Path
from typing import Any

from ownpi.runtime.permissions.action_compiler import OperationEffects, compile_canonical_action
from ownpi.runtime.permissions.models import EffectivePermissions, FileSystemAccessMode
from ownpi.runtime.permissions.orchestrator import FileSystemAccessRequest
from ownpi.runtime.permissions.policy import require_file_access
from ownpi.tools.backends.remote_workflow.profiles import RemoteProfile, profile_path, profiles_dir


def effective_permissions(context: Any) -> EffectivePermissions:
    permissions = getattr(context, 'effective_permissions', None)
    if permissions is None:
        permissions = getattr(context, 'canonical_permissions', None)
    if not isinstance(permissions, EffectivePermissions):
        raise PermissionError('remote workflow requires canonical effective permissions')
    return permissions


def resolve_remote_operation_effects(
    context: Any, arguments: dict[str, Any], *, operation: str,
    profile: RemoteProfile | None = None,
) -> OperationEffects:
    from ownpi.tools import remote_workflow as handlers
    from ownpi.tools.backends.remote_workflow import workflow

    config_dir = handlers._config_dir(arguments)
    accesses: list[FileSystemAccessRequest] = []
    network = operation not in {'profile_list', 'profile_show'}
    if operation == 'profile_list':
        directory = profiles_dir(config_dir)
        accesses.append(FileSystemAccessRequest(directory, FileSystemAccessMode.READ))
        try:
            require_file_access(effective_permissions(context), directory, FileSystemAccessMode.READ)
            accesses.extend(
                FileSystemAccessRequest(path, FileSystemAccessMode.READ)
                for path in sorted(directory.glob('*.toml'))
            )
        except (OSError, ValueError):
            pass
    else:
        try:
            path = profile_path(config_dir, handlers._profile_name(arguments, context))
            accesses.append(FileSystemAccessRequest(path, FileSystemAccessMode.READ))
            if profile is None:
                profile, _ = handlers._load_effective_profile(context, arguments)
            if operation == 'deploy':
                selected = ['.'] if arguments.get('all') else handlers._deploy_paths(context, profile, arguments)
                accesses.extend(
                    FileSystemAccessRequest(workflow.deploy_source_path(profile, item), FileSystemAccessMode.READ)
                    for item in selected
                )
            elif operation == 'pull':
                selected = ['.'] if arguments.get('all') else handlers._pull_paths(context, arguments)
                for item in selected:
                    _, _, destination = workflow._pull_source_destination(profile, item)
                    accesses.append(FileSystemAccessRequest(destination, FileSystemAccessMode.WRITE))
                    accesses.append(FileSystemAccessRequest(destination, FileSystemAccessMode.READ))
                    accesses.extend(
                        FileSystemAccessRequest(parent, FileSystemAccessMode.WRITE)
                        for parent in missing_directories(destination.parent)
                    )
        except (OSError, ValueError):
            # Declare known prerequisites without reading a denied profile.
            # The handler reports invalid input and rechecks the resolved operation.
            pass
    endpoint = f'ssh://{profile.target}:{profile.port}' if network and profile else ''
    return OperationEffects.declared(
        file_system_accesses=tuple(accesses),
        network_required=network,
        network_endpoints=(endpoint,) if endpoint else (),
        remote_required=network,
        remote_target=profile.target if network and profile else '',
        remote_workdir=profile.remote_root if network and profile else '',
        source='declared_remote_operation',
    )


def revalidate_remote_operation(
    context: Any, arguments: dict[str, Any], *, operation: str, profile: RemoteProfile,
) -> None:
    effects = resolve_remote_operation_effects(context, arguments, operation=operation, profile=profile)
    permissions = effective_permissions(context)
    for access in effects.file_system_accesses:
        require_file_access(permissions, access.path, access.access)
    expected_hash = str(getattr(context, 'canonical_effect_hash', '') or '')
    if expected_hash:
        current = compile_canonical_action(
            request_id='', tool_name='', permission_class='', input_payload={},
            environment={}, destructive=False, declared_effects=effects,
        )
        if current.effects.effect_hash != expected_hash:
            raise PermissionError('remote operation changed after authorization; retry to authorize the resolved profile and paths')


def missing_directories(directory: Path) -> list[Path]:
    missing: list[Path] = []
    current = directory
    while not current.exists():
        missing.append(current)
        if current.parent == current:
            break
        current = current.parent
    return list(reversed(missing))


def mkdir_with_permissions(directory: Path, permissions: EffectivePermissions) -> None:
    missing = missing_directories(directory)
    for path in missing:
        require_file_access(permissions, path, FileSystemAccessMode.WRITE)
    for path in missing:
        path.mkdir(exist_ok=True)
