from __future__ import annotations

from pathlib import Path
from typing import Any

from ownpi.app.paths import AppPaths
from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.permissions.action_compiler import OperationEffects
from ownpi.runtime.permissions.models import EffectivePermissions, FileSystemAccessMode
from ownpi.runtime.permissions.orchestrator import FileSystemAccessRequest
from ownpi.runtime.permissions.policy import lexical_absolute_path
from ownpi.runtime.environment.materialization.github import GitHubRepositoryAdapter
from ownpi.runtime.environment.materialization.huggingface import HuggingFaceSnapshotAdapter
from ownpi.runtime.environment.materialization.service import MaterializationService
from ownpi.runtime.environment.materialization.transport import (
    BoundedTransport,
    TransportPolicy,
    command_executor_for_context,
)
from ownpi.tools.common import metadata_for_context


async def run_model_snapshot_materialize(context: Any, arguments: dict[str, Any]) -> ToolResult:
    resource_cfg, materialization_cfg = _resource_config(context)
    route_status = str(materialization_cfg.get('huggingface_route_status', 'unavailable'))
    if route_status == 'unavailable':
        return _route_unavailable('model_snapshot_materialize', context, 'huggingface_mirror')
    try:
        permissions = _effective_permissions(context)
        transport = _transport(context, materialization_cfg, tool_name='model_snapshot_materialize')
        adapter = HuggingFaceSnapshotAdapter(
            repo_id=str(arguments.get('repo_id', '')),
            revision=str(arguments.get('revision', 'main') or 'main'),
            allow_patterns=tuple(str(item) for item in arguments.get('allow_patterns', []) if str(item).strip()),
            endpoint=str(_mirrors(resource_cfg).get('huggingface_endpoint', '')),
            transport=transport,
            effective_permissions=permissions,
            max_snapshot_bytes=int(materialization_cfg.get('max_model_snapshot_bytes', 50_000_000_000)),
        )
        root = _cache_root(context, materialization_cfg, key='model_cache_root', default_name='models')
        result = await MaterializationService(
            cache_root=root,
            effective_permissions=permissions,
            lock_timeout_seconds=int(materialization_cfg.get('lock_timeout_seconds', 30)),
            max_receipt_chars=int(materialization_cfg.get('max_receipt_chars', 16_384)),
        ).materialize(adapter)
    except PermissionError as exc:
        return _permission_denied('model_snapshot_materialize', context, exc)
    except (TypeError, ValueError) as exc:
        return ToolResult(ok=False, tool_name='model_snapshot_materialize', error=str(exc))
    payload = result.bounded_payload()
    payload.update(
        {
            'resource_kind': 'model_snapshot',
            'resource_identity': adapter.resource_identity,
            'requested_revision': adapter.requested_revision,
        }
    )
    return _tool_result('model_snapshot_materialize', context, payload)


async def run_repository_materialize(context: Any, arguments: dict[str, Any]) -> ToolResult:
    resource_cfg, materialization_cfg = _resource_config(context)
    content_mode = str(arguments.get('content_mode', 'source_snapshot') or 'source_snapshot')
    route_key = 'github_archive_route_status' if content_mode == 'source_snapshot' else 'github_ssh_route_status'
    if str(materialization_cfg.get(route_key, 'unavailable')) == 'unavailable':
        return _route_unavailable('repository_materialize', context, route_key.removesuffix('_route_status'))
    try:
        permissions = _effective_permissions(context)
        transport = _transport(context, materialization_cfg, tool_name='repository_materialize')
        adapter = GitHubRepositoryAdapter(
            repository=str(arguments.get('repository', '')),
            revision=str(arguments.get('revision', 'HEAD') or 'HEAD'),
            content_mode=content_mode,
            api_endpoint=str(materialization_cfg.get('github_api_endpoint', 'https://api.github.com')),
            archive_endpoint=str(materialization_cfg.get('github_archive_endpoint', 'https://codeload.github.com')),
            api_route_status=str(materialization_cfg.get('github_api_route_status', 'unavailable')),
            ssh_route_status=str(materialization_cfg.get('github_ssh_route_status', 'unavailable')),
            ssh_host_alias=str(materialization_cfg.get('github_ssh_host_alias', '')),
            ssh_identity_path=str(materialization_cfg.get('github_ssh_identity_path', '')),
            transport=transport,
            effective_permissions=permissions,
            max_archive_bytes=int(materialization_cfg.get('max_repository_archive_bytes', 2_000_000_000)),
            max_expanded_bytes=int(materialization_cfg.get('max_repository_expanded_bytes', 2_000_000_000)),
        )
        root = _cache_root(context, materialization_cfg, key='repository_cache_root', default_name='repositories')
        result = await MaterializationService(
            cache_root=root,
            effective_permissions=permissions,
            lock_timeout_seconds=int(materialization_cfg.get('lock_timeout_seconds', 30)),
            max_receipt_chars=int(materialization_cfg.get('max_receipt_chars', 16_384)),
        ).materialize(adapter)
    except PermissionError as exc:
        return _permission_denied('repository_materialize', context, exc)
    except (TypeError, ValueError) as exc:
        return ToolResult(ok=False, tool_name='repository_materialize', error=str(exc))
    payload = result.bounded_payload()
    payload.update(
        {
            'resource_kind': 'repository',
            'resource_identity': adapter.resource_identity,
            'requested_revision': adapter.requested_revision,
            'content_mode': content_mode,
        }
    )
    return _tool_result('repository_materialize', context, payload)


def model_snapshot_materialization_effects(
    context: Any,
    arguments: dict[str, Any],
) -> OperationEffects:
    del arguments
    resource_cfg, materialization_cfg = _resource_config(context)
    return _materialization_effects(
        context,
        materialization_cfg,
        key='model_cache_root',
        default_name='models',
        source='resource:model_snapshot',
        network_endpoints=tuple(
            value
            for value in (str(_mirrors(resource_cfg).get('huggingface_endpoint', '') or '').strip(),)
            if value
        ),
    )


def repository_materialization_effects(
    context: Any,
    arguments: dict[str, Any],
) -> OperationEffects:
    del arguments
    _, materialization_cfg = _resource_config(context)
    return _materialization_effects(
        context,
        materialization_cfg,
        key='repository_cache_root',
        default_name='repositories',
        source='resource:repository',
        network_endpoints=tuple(
            value
            for value in (
                str(materialization_cfg.get('github_api_endpoint', '') or '').strip(),
                str(materialization_cfg.get('github_archive_endpoint', '') or '').strip(),
                str(materialization_cfg.get('github_ssh_host_alias', '') or '').strip(),
            )
            if value
        ),
    )


def _materialization_effects(
    context: Any,
    config: dict[str, Any],
    *,
    key: str,
    default_name: str,
    source: str,
    network_endpoints: tuple[str, ...],
) -> OperationEffects:
    cache_root = _cache_root(context, config, key=key, default_name=default_name)
    return OperationEffects.declared(
        file_system_accesses=(
            FileSystemAccessRequest(cache_root, FileSystemAccessMode.WRITE),
        ),
        network_required=True,
        network_endpoints=network_endpoints,
        process_required=True,
        process_argv=('bounded_resource_transport',),
        source=source,
    )


def _transport(context: Any, config: dict[str, Any], *, tool_name: str) -> BoundedTransport:
    return BoundedTransport(
        effective_permissions=_effective_permissions(context),
        executor=command_executor_for_context(context, tool_name=tool_name),
        policy=TransportPolicy(
            overall_deadline_seconds=int(config.get('overall_deadline_seconds', 600)),
            attempt_timeout_seconds=int(config.get('attempt_timeout_seconds', 120)),
            max_attempts=int(config.get('max_attempts', 3)),
        ),
        cancel_checker=getattr(context, 'cancel_checker', None),
    )


def _effective_permissions(context: Any) -> EffectivePermissions:
    permissions = getattr(context, 'effective_permissions', None)
    if not isinstance(permissions, EffectivePermissions):
        raise PermissionError('canonical effective permissions are required for materialization')
    return permissions


def _permission_denied(tool_name: str, context: Any, exc: PermissionError) -> ToolResult:
    return _tool_result(tool_name, context, {
        'status': 'unavailable', 'failure_owner': 'policy_owned',
        'failure_code': 'materialization_permission_denied', 'error': str(exc),
    })


def _resource_config(context: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    explicit_snapshot = getattr(context, 'environment_config_snapshot', None)
    if isinstance(explicit_snapshot, dict):
        snapshot = explicit_snapshot
    else:
        environment_state = getattr(
            getattr(getattr(context, 'state_store', None), 'state', None),
            'environment_state',
            None,
        )
        state_snapshot = getattr(environment_state, 'config_snapshot', None)
        snapshot = state_snapshot if isinstance(state_snapshot, dict) else {}
    resource = snapshot.get('resources', {}) if isinstance(snapshot.get('resources'), dict) else {}
    materialization = resource.get('materialization', {}) if isinstance(resource.get('materialization'), dict) else {}
    return resource, materialization


def _mirrors(resource_cfg: dict[str, Any]) -> dict[str, Any]:
    value = resource_cfg.get('mirrors', {})
    return value if isinstance(value, dict) else {}


def _cache_root(context: Any, config: dict[str, Any], *, key: str, default_name: str) -> Path:
    configured = str(config.get(key, '') or '').strip()
    if configured:
        return lexical_absolute_path(configured)
    app_paths = context.app_paths or AppPaths.discover(repo_root=Path(context.project_root))
    return lexical_absolute_path(app_paths.app_home / 'resources' / default_name)


def _route_unavailable(tool_name: str, context: Any, route_class: str) -> ToolResult:
    return ToolResult(
        ok=False,
        tool_name=tool_name,
        data={
            'status': 'unavailable',
            'route_class': route_class,
            'failure_owner': 'runtime_capability_missing',
            'failure_code': 'configured_route_unavailable',
        },
        error=f'configured resource route is unavailable: {route_class}',
        metadata=metadata_for_context(context),
    )


def _tool_result(tool_name: str, context: Any, payload: dict[str, object]) -> ToolResult:
    ok = payload.get('status') == 'materialized'
    return ToolResult(
        ok=ok,
        tool_name=tool_name,
        data=payload,
        error=None if ok else str(payload.get('error', '') or 'resource materialization failed'),
        warnings=[str(item) for item in payload.get('warnings', [])],
        metadata=metadata_for_context(context),
    )
