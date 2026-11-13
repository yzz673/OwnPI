from __future__ import annotations

import os
from pathlib import Path

from dataclasses import replace

from ownpi.runtime.permissions.models import EffectivePermissions, GranularApprovalConfig
from ownpi.runtime.permissions.policy import permission_policy_hash
from ownpi.runtime.permissions.presets import permission_preset_selection
from ownpi.runtime.permissions.profiles import materialize_profile, resolve_profile
from ownpi.schemas.runtime_permissions_config import RuntimePermissionsConfig


def compile_runtime_permissions(
    config: RuntimePermissionsConfig,
    *,
    project_root: Path,
) -> EffectivePermissions:
    workspace_roots = _workspace_roots(config, project_root=project_root)
    selection = permission_preset_selection(config.preset)
    if config.approval_policy is not None:
        selection = replace(selection, approval_policy=config.approval_policy)
    if config.approvals_reviewer is not None:
        selection = replace(selection, approvals_reviewer=config.approvals_reviewer)
    custom_profiles = {
        profile_id: profile_config.to_profile(base_dir=project_root)
        for profile_id, profile_config in config.custom_profiles.items()
    }
    active_profile_id = config.active_profile or selection.profile_id
    profile = materialize_profile(
        resolve_profile(active_profile_id, custom_profiles=custom_profiles),
        workspace_roots=workspace_roots,
    )
    return EffectivePermissions(
        active_profile_id=active_profile_id,
        profile=profile,
        approval_policy=selection.approval_policy,
        approvals_reviewer=selection.approvals_reviewer,
        workspace_roots=workspace_roots,
        policy_hash=permission_policy_hash(
            profile,
            approval_policy=selection.approval_policy,
            approvals_reviewer=selection.approvals_reviewer,
            granular_approval=(
                config.granular_approval
                if config.granular_approval is not None
                else GranularApprovalConfig()
            ),
        ),
        granular_approval=(
            config.granular_approval
            if config.granular_approval is not None
            else GranularApprovalConfig()
        ),
    )


def default_runtime_permissions(*, project_root: Path | None = None) -> EffectivePermissions:
    return compile_runtime_permissions(
        RuntimePermissionsConfig(),
        project_root=project_root or Path.cwd(),
    )


def _workspace_roots(
    config: RuntimePermissionsConfig,
    *,
    project_root: Path,
) -> tuple[Path, ...]:
    candidates = (project_root, *(Path(item) for item in config.additional_workspace_roots))
    roots: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        expanded = candidate.expanduser()
        if not expanded.is_absolute():
            expanded = project_root / expanded
        normalized = Path(os.path.abspath(expanded))
        key = os.path.normcase(str(normalized))
        if key not in seen:
            seen.add(key)
            roots.append(normalized)
    return tuple(roots)
