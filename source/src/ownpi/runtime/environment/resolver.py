from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from ownpi.runtime.permissions.models import (
    EffectivePermissions,
)
from ownpi.runtime.environment.contracts import (
    EnvironmentDescriptor,
    build_permission_enforcement_projection,
    normalize_environment_provider_kind,
)
from ownpi.runtime.environment.providers import ResolvedEnvironmentSelection
from ownpi.schemas.runtime_tooling import ToolDescriptorSummary


DEFAULT_ENVIRONMENT_CONFIG: dict[str, Any] = {
    'default_provider': 'host_local',
    'docker': {
        'enabled': False,
        'image': '',
    },
    'ssh': {
        'targets': [],
    },
    'local': {
        'default_env_name': '',
        'default_python': '',
        'activation': {
            'kind': '',
            'conda_sh': '',
            'env_name': '',
        },
        'env_vars': {},
        'path_prepend': [],
    },
    'resources': {
        'mirrors': {},
        'env_vars': {},
        'materialization': {},
    },
    'routing': {
        'exec_default_provider': 'host_local',
        'destructive_exec_provider': 'docker_sandbox',
    },
    'proxy_env': {},
}


def default_environment_config() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_ENVIRONMENT_CONFIG)


class EnvironmentResolver:
    def __init__(self, *, config_snapshot: dict[str, Any] | None = None) -> None:
        self._config = default_environment_config()
        if isinstance(config_snapshot, dict):
            self._merge(self._config, config_snapshot)

    def resolve_tool_environment(
        self,
        *,
        project_root: Path,
        run_dir: Path | None,
        stage: str,
        tool_summary: ToolDescriptorSummary,
        input: dict[str, Any],
        effective_permissions: EffectivePermissions,
        destructive_action: bool,
        extra_readable_roots: tuple[str, ...] = (),
    ) -> ResolvedEnvironmentSelection:
        del destructive_action
        backend_kind = str(tool_summary.backend_kind or 'generic')
        execution_class = str(tool_summary.execution_class or 'read_only')
        if execution_class == 'command_exec' and bool(input.get('use_pty', False)):
            execution_class = 'interactive_pty'
        environment_affinity = str(tool_summary.environment_affinity or 'host_preferred')
        docker_cfg = self._config.get('docker', {}) if isinstance(self._config.get('docker'), dict) else {}
        local_cfg = self._config.get('local', {}) if isinstance(self._config.get('local'), dict) else {}
        activation_cfg = local_cfg.get('activation', {}) if isinstance(local_cfg.get('activation'), dict) else {}
        routing_cfg = self._config.get('routing', {}) if isinstance(self._config.get('routing'), dict) else {}
        default_provider = normalize_environment_provider_kind(
            self._config.get('default_provider', 'host_local') or 'host_local'
        )
        exec_default_provider = normalize_environment_provider_kind(
            routing_cfg.get('exec_default_provider', default_provider) or default_provider
        )
        sandbox_hint = _coerce_bool(input.get('sandbox_required')) or str(input.get('environment_provider', '') or '').strip() == 'docker_sandbox'
        remote_target = str(input.get('remote_target') or input.get('ssh_target') or '').strip()
        raw_explicit_provider = str(
            input.get('environment_provider', '') or input.get('provider', '') or ''
        ).strip()
        explicit_provider = (
            normalize_environment_provider_kind(raw_explicit_provider)
            if raw_explicit_provider
            else ''
        )
        remote_required = explicit_provider == 'ssh_remote' or bool(remote_target) or environment_affinity == 'remote_required'
        requires_isolation = (
            sandbox_hint
            or environment_affinity == 'sandbox_preferred'
        )
        provider_kind = default_provider
        selection_bits: list[str] = []
        if backend_kind == 'local_exec':
            provider_kind = exec_default_provider or default_provider
            selection_bits.append('local_exec')
            if requires_isolation:
                provider_kind = 'docker_sandbox'
                selection_bits.append('isolation_required')
        elif remote_required:
            provider_kind = 'ssh_remote'
            selection_bits.append('remote_required')
        elif backend_kind in {'browser', 'mcp'}:
            provider_kind = 'host_local'
            selection_bits.append('receipt_only_host_managed')
        else:
            provider_kind = 'host_local'
            selection_bits.append(f'backend:{backend_kind}')
        if explicit_provider == 'host_local' and requires_isolation:
            provider_kind = 'docker_sandbox'
            selection_bits.append('host_local_denied_requires_isolation')
        elif explicit_provider in {'host_local', 'docker_sandbox', 'ssh_remote'}:
            provider_kind = explicit_provider
            selection_bits.append(f'explicit:{explicit_provider}')
        if provider_kind == 'ssh_remote':
            remote_required = True
            if not remote_target:
                selection_bits.append('remote_default_target')
        effective_remote_target = (
            (remote_target or _default_ssh_target_name(self._config)) if provider_kind == 'ssh_remote' else None
        )
        temp_artifact_root = _task_local_temp_artifact_root(run_dir)
        descriptor = EnvironmentDescriptor(
            provider_kind=provider_kind,
            workspace_root=str(project_root),
            workdir=_resolve_workdir(project_root=project_root, input=input),
            env_policy='scrubbed_inherit',
            env_name=str(local_cfg.get('default_env_name', '') or activation_cfg.get('env_name', '') or '')
            if provider_kind == 'host_local'
            else '',
            python_executable=str(local_cfg.get('default_python', '') or '') if provider_kind == 'host_local' else '',
            activation_kind=str(activation_cfg.get('kind', '') or '') if provider_kind == 'host_local' else '',
            image_ref=(str(docker_cfg.get('image', '') or '') if provider_kind == 'docker_sandbox' else None) or None,
            remote_target=effective_remote_target,
            remote_workspace_root=(
                _remote_workspace_root(self._config, str(effective_remote_target or ''))
                if provider_kind == 'ssh_remote'
                else None
            ),
            ephemeral=(provider_kind == 'docker_sandbox'),
            sandboxed=(provider_kind == 'docker_sandbox'),
            host_managed=(backend_kind in {'browser', 'mcp'}),
            temp_artifact_root=temp_artifact_root,
            mounts=(str(project_root),) if provider_kind == 'docker_sandbox' else (),
        )
        sandbox_policy = build_permission_enforcement_projection(
            effective_permissions=effective_permissions,
            provider_kind=provider_kind,
            requires_isolation=requires_isolation,
            remote_required=remote_required,
            host_managed=descriptor.host_managed,
            reason=';'.join(selection_bits),
        )
        target_name = descriptor.remote_target if provider_kind == 'ssh_remote' else None
        return ResolvedEnvironmentSelection(
            provider_kind=provider_kind,
            descriptor=descriptor,
            sandbox_policy=sandbox_policy,
            selection_reason=';'.join(selection_bits) or 'default',
            environment_affinity=environment_affinity,
            execution_class=execution_class,
            backend_kind=backend_kind,
            target_name=target_name,
        )

    @staticmethod
    def _merge(base: dict[str, Any], patch: dict[str, Any]) -> None:
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                EnvironmentResolver._merge(base[key], value)
            else:
                base[key] = copy.deepcopy(value)


def build_provider_runtime_config(
    *,
    selection: ResolvedEnvironmentSelection,
    config_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = config_snapshot or default_environment_config()
    proxy_env = payload.get('proxy_env', {}) if isinstance(payload.get('proxy_env'), dict) else {}
    if selection.provider_kind == 'docker_sandbox':
        docker_cfg = payload.get('docker', {}) if isinstance(payload.get('docker'), dict) else {}
        return {**dict(docker_cfg), 'proxy_env': dict(proxy_env)}
    if selection.provider_kind == 'ssh_remote':
        ssh_cfg = payload.get('ssh', {}) if isinstance(payload.get('ssh'), dict) else {}
        targets = ssh_cfg.get('targets', []) if isinstance(ssh_cfg.get('targets'), list) else []
        target_name = selection.target_name or _default_ssh_target_name(payload)
        for item in targets:
            if not isinstance(item, dict):
                continue
            if str(item.get('name', '') or '').strip() == str(target_name or '').strip():
                return {**dict(item), 'proxy_env': dict(proxy_env)}
        return {'proxy_env': dict(proxy_env)}
    local_cfg = payload.get('local', {}) if isinstance(payload.get('local'), dict) else {}
    resource_cfg = payload.get('resources', {}) if isinstance(payload.get('resources'), dict) else {}
    return {
        'proxy_env': dict(proxy_env),
        'local': dict(local_cfg),
        'resources': dict(resource_cfg),
    }



def _resolve_workdir(*, project_root: Path, input: dict[str, Any]) -> str:
    raw_cwd = str(input.get('cwd', '.') or '.').strip()
    candidate = Path(raw_cwd).expanduser()
    if not candidate.is_absolute():
        candidate = (project_root / candidate).resolve()
    return str(candidate)


def _task_local_temp_artifact_root(run_dir: Path | None) -> str | None:
    if not isinstance(run_dir, Path):
        return None
    root = run_dir.expanduser().resolve() / 'tool_artifacts' / 'tmp'
    return str(root)


def materialize_environment_artifact_root(selection: ResolvedEnvironmentSelection) -> None:
    raw_root = selection.descriptor.temp_artifact_root
    if raw_root:
        Path(raw_root).mkdir(parents=True, exist_ok=True)


def _remote_workspace_root(config: dict[str, Any], target_name: str) -> str | None:
    ssh_cfg = config.get('ssh', {}) if isinstance(config.get('ssh'), dict) else {}
    targets = ssh_cfg.get('targets', []) if isinstance(ssh_cfg.get('targets'), list) else []
    for item in targets:
        if not isinstance(item, dict):
            continue
        if str(item.get('name', '') or '').strip() == str(target_name or '').strip():
            value = str(item.get('remote_workspace_root', '') or '').strip()
            return value or None
    return None



def _default_ssh_target_name(config: dict[str, Any]) -> str | None:
    ssh_cfg = config.get('ssh', {}) if isinstance(config.get('ssh'), dict) else {}
    targets = ssh_cfg.get('targets', []) if isinstance(ssh_cfg.get('targets'), list) else []
    if not targets:
        return None
    first = targets[0]
    if not isinstance(first, dict):
        return None
    name = str(first.get('name', '') or '').strip()
    return name or None



def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {'1', 'true', 'yes', 'on'}:
            return True
        if lowered in {'0', 'false', 'no', 'off', ''}:
            return False
    return bool(value)
