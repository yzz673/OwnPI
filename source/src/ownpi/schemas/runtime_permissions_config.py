from __future__ import annotations

from dataclasses import asdict, dataclass, field
from dataclasses import replace
from pathlib import Path
from typing import Any

from ownpi.runtime.permissions.models import (
    FileSystemAccessMode,
    FileSystemPath,
    FileSystemSandboxEntry,
    FileSystemSandboxKind,
    FileSystemSandboxPolicy,
    NetworkSandboxPolicy,
    AskForApproval,
    ApprovalsReviewer,
    GranularApprovalConfig,
    PermissionPreset,
    PermissionProfile,
    SandboxEnforcement,
)


@dataclass(frozen=True)
class PermissionFileSystemEntryConfig:
    path: str
    access: FileSystemAccessMode
    skip_if_missing: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'PermissionFileSystemEntryConfig':
        path = str(data.get('path', '') or '').strip()
        if not path:
            raise ValueError('runtime.permissions filesystem entry requires path')
        return cls(
            path=path,
            access=FileSystemAccessMode(str(data.get('access', '') or '')),
            skip_if_missing=bool(data.get('skip_if_missing', False)),
        )


@dataclass(frozen=True)
class CustomPermissionProfileConfig:
    profile_id: str
    description: str = ''
    extends: str | None = None
    enforcement: SandboxEnforcement = SandboxEnforcement.MANAGED
    network: NetworkSandboxPolicy = NetworkSandboxPolicy.RESTRICTED
    file_system: tuple[PermissionFileSystemEntryConfig, ...] = ()
    glob_scan_max_depth: int | None = None

    @classmethod
    def from_dict(
        cls,
        profile_id: str,
        data: dict[str, Any],
    ) -> 'CustomPermissionProfileConfig':
        normalized_id = str(profile_id or '').strip()
        if not normalized_id or normalized_id.startswith(':'):
            raise ValueError('custom permission profile id must be non-empty and not built-in')
        raw_depth = data.get('glob_scan_max_depth')
        depth = None if raw_depth is None else int(raw_depth)
        if depth is not None and depth < 0:
            raise ValueError('glob_scan_max_depth must be nonnegative')
        entries = data.get('file_system', [])
        if not isinstance(entries, list):
            raise ValueError('custom permission profile file_system must be a list')
        return cls(
            profile_id=normalized_id,
            description=str(data.get('description', '') or ''),
            extends=(str(data['extends']).strip() if data.get('extends') else None),
            enforcement=SandboxEnforcement(str(data.get('enforcement', 'managed') or 'managed')),
            network=NetworkSandboxPolicy(str(data.get('network', 'restricted') or 'restricted')),
            file_system=tuple(
                PermissionFileSystemEntryConfig.from_dict(item)
                for item in entries
                if isinstance(item, dict)
            ),
            glob_scan_max_depth=depth,
        )

    def to_profile(self, *, base_dir: Path) -> PermissionProfile:
        entries = tuple(
            FileSystemSandboxEntry(
                path=(
                    FileSystemPath.glob(item.path)
                    if any(token in item.path for token in ('*', '?', '['))
                    else FileSystemPath.path(_absolute_config_path(item.path, base_dir=base_dir))
                ),
                access=item.access,
                skip_if_missing=item.skip_if_missing,
            )
            for item in self.file_system
        )
        kind = (
            FileSystemSandboxKind.UNRESTRICTED
            if self.enforcement is SandboxEnforcement.DISABLED
            else FileSystemSandboxKind.RESTRICTED
        )
        return PermissionProfile(
            profile_id=self.profile_id,
            enforcement=self.enforcement,
            file_system=FileSystemSandboxPolicy(kind, entries, self.glob_scan_max_depth),
            network=self.network,
            description=self.description,
            extends=self.extends,
        )


@dataclass(frozen=True)
class RuntimePermissionsConfig:
    preset: PermissionPreset = PermissionPreset.FULL_ACCESS
    approval_policy: AskForApproval | None = None
    approvals_reviewer: ApprovalsReviewer | None = None
    granular_approval: GranularApprovalConfig | None = None
    additional_workspace_roots: tuple[str, ...] = ()
    custom_profiles: dict[str, CustomPermissionProfileConfig] = field(default_factory=dict)
    active_profile: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> 'RuntimePermissionsConfig':
        payload = data or {}
        raw_profiles = payload.get('custom_profiles', {})
        if not isinstance(raw_profiles, dict):
            raise ValueError('runtime.permissions.custom_profiles must be a mapping')
        roots = payload.get('additional_workspace_roots', [])
        if isinstance(roots, str):
            roots = [roots]
        if not isinstance(roots, list):
            raise ValueError('runtime.permissions.additional_workspace_roots must be a list')
        active_profile = str(payload.get('active_profile', '') or '').strip() or None
        return cls(
            preset=PermissionPreset(str(payload.get('preset', 'full_access') or 'full_access')),
            approval_policy=(
                AskForApproval(str(payload['approval_policy']))
                if payload.get('approval_policy') is not None
                else None
            ),
            approvals_reviewer=(
                ApprovalsReviewer(str(payload['approvals_reviewer']))
                if payload.get('approvals_reviewer') is not None
                else None
            ),
            granular_approval=(
                GranularApprovalConfig(
                    **{
                        key: bool(payload['granular_approval'].get(key, False))
                        for key in {
                            'sandbox_approval', 'rules', 'skill_approval',
                            'request_permissions', 'mcp_elicitations',
                        }
                    }
                )
                if isinstance(payload.get('granular_approval'), dict)
                else None
            ),
            additional_workspace_roots=tuple(
                str(item).strip() for item in roots if str(item).strip()
            ),
            custom_profiles={
                str(profile_id): CustomPermissionProfileConfig.from_dict(
                    str(profile_id),
                    profile_data,
                )
                for profile_id, profile_data in raw_profiles.items()
                if isinstance(profile_data, dict)
            },
            active_profile=active_profile,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            'preset': self.preset.value,
            'approval_policy': self.approval_policy.value if self.approval_policy else None,
            'approvals_reviewer': self.approvals_reviewer.value if self.approvals_reviewer else None,
            'granular_approval': (
                asdict(self.granular_approval) if self.granular_approval is not None else None
            ),
            'additional_workspace_roots': list(self.additional_workspace_roots),
            'custom_profiles': {
                profile_id: {
                    **asdict(profile),
                    'enforcement': profile.enforcement.value,
                    'network': profile.network.value,
                    'file_system': [
                        {
                            'path': item.path,
                            'access': item.access.value,
                            'skip_if_missing': item.skip_if_missing,
                        }
                        for item in profile.file_system
                    ],
                }
                for profile_id, profile in self.custom_profiles.items()
            },
            'active_profile': self.active_profile,
        }


def _absolute_config_path(path: str, *, base_dir: Path) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    return candidate.absolute()
