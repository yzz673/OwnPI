from __future__ import annotations

from dataclasses import asdict, dataclass
import shutil
from typing import Any

from ownpi.runtime.environment.contracts import EnvironmentExecRequest
from ownpi.runtime.environment.providers import ResolvedEnvironmentSelection
from ownpi.runtime.permissions.models import EffectivePermissions, SandboxEnforcement


@dataclass(frozen=True)
class EnvironmentPreflightDecision:
    ok: bool
    provider_kind: str
    unavailable_reason: str = ''
    user_action_hint: str = ''
    violation_class: str = ''
    advisory: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def preflight_environment_provider(
    *,
    selection: ResolvedEnvironmentSelection,
    config_snapshot: dict[str, Any] | None,
    request: EnvironmentExecRequest | None = None,
) -> EnvironmentPreflightDecision:
    provider_kind = selection.provider_kind
    if provider_kind == 'docker_sandbox':
        docker_cfg = (config_snapshot or {}).get('docker', {}) if isinstance((config_snapshot or {}).get('docker'), dict) else {}
        if not bool(docker_cfg.get('enabled', False)):
            return _blocked(
                selection,
                reason='docker_sandbox_disabled',
                hint='Set runtime.environment.docker.enabled=true and configure runtime.environment.docker.image.',
                violation_class='provider_unavailable',
            )
        if not str(docker_cfg.get('image', '') or '').strip():
            return _blocked(
                selection,
                reason='docker_image_missing',
                hint='Configure runtime.environment.docker.image before running container commands.',
                violation_class='provider_unavailable',
            )
        if shutil.which('docker') is None:
            return _blocked(
                selection,
                reason='docker_cli_unavailable',
                hint='Install Docker or switch the environment provider to host_local for non-isolated commands.',
                violation_class='provider_unavailable',
            )
        if selection.sandbox_policy.blocked_host_roots:
            return _blocked(
                selection,
                reason='docker_protected_path_mask_unsupported',
                hint='Use an execution backend that can enforce the requested filesystem deny entries.',
                violation_class='unsupported_policy',
            )
    if provider_kind == 'ssh_remote':
        ssh_cfg = (config_snapshot or {}).get('ssh', {}) if isinstance((config_snapshot or {}).get('ssh'), dict) else {}
        target = _ssh_target_config(ssh_cfg, selection.target_name)
        if not str(target.get('host', '') or '').strip():
            return _blocked(
                selection,
                reason='ssh_remote_host_missing',
                hint='Configure runtime.environment.ssh.targets with a host for the selected remote target.',
                violation_class='provider_unavailable',
            )
        if shutil.which('ssh') is None:
            return _blocked(
                selection,
                reason='ssh_cli_unavailable',
                hint='Install an SSH client before using the ssh_remote provider.',
                violation_class='provider_unavailable',
            )
        permissions = request.metadata.get('effective_permissions') if request is not None else None
        if isinstance(permissions, dict) and (
            EffectivePermissions.from_dict(permissions).profile.enforcement
            is not SandboxEnforcement.DISABLED
        ):
            return _blocked(
                selection,
                reason='ssh_remote_sandbox_unsupported',
                hint='Use a backend with sandbox enforcement for the active restricted profile.',
                violation_class='unsupported_policy',
            )
    return EnvironmentPreflightDecision(ok=True, provider_kind=provider_kind)


def classify_environment_failure(error: str) -> str:
    text = str(error or '').casefold()
    if (
        'deny glob' in text
        or 'symlink components' in text
        or 'unsupported' in text
        or 'compatible remote sandbox protocol' in text
        or 'cannot enforce canonical filesystem' in text
    ):
        return 'unsupported_policy'
    if 'bubblewrap is unavailable' in text:
        return 'provider_unavailable'
    if 'docker' in text and ('disabled' in text or 'unavailable' in text or 'not found' in text):
        return 'provider_unavailable'
    if 'requires isolation' in text:
        return 'isolation_required'
    if 'env override' in text or 'transport-sensitive env' in text or 'invalid docker env' in text:
        return 'environment_input_validation_failed'
    if 'timeout' in text:
        return 'timeout'
    return 'provider_prepare_failed'


def _blocked(
    selection: ResolvedEnvironmentSelection,
    *,
    reason: str,
    hint: str,
    violation_class: str,
) -> EnvironmentPreflightDecision:
    return EnvironmentPreflightDecision(
        ok=False,
        provider_kind=selection.provider_kind,
        unavailable_reason=reason,
        user_action_hint=hint,
        violation_class=violation_class,
    )


def _ssh_target_config(ssh_cfg: dict[str, Any], target_name: str | None) -> dict[str, Any]:
    targets = ssh_cfg.get('targets', []) if isinstance(ssh_cfg.get('targets'), list) else []
    if not targets:
        return {}
    if not target_name:
        first = targets[0]
        return dict(first) if isinstance(first, dict) else {}
    for item in targets:
        if isinstance(item, dict) and str(item.get('name', '') or '').strip() == str(target_name).strip():
            return dict(item)
    return {}
