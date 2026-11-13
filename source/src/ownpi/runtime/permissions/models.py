from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


BUILT_IN_PERMISSION_PROFILE_READ_ONLY = ':read-only'
BUILT_IN_PERMISSION_PROFILE_WORKSPACE = ':workspace'
BUILT_IN_PERMISSION_PROFILE_DANGER_FULL_ACCESS = ':danger-full-access'
PROTECTED_METADATA_NAMES = ('.git', '.agents', '.codex')


class FileSystemAccessMode(str, Enum):
    READ = 'read'
    WRITE = 'write'
    DENY = 'deny'

    @property
    def can_read(self) -> bool:
        return self is not FileSystemAccessMode.DENY

    @property
    def can_write(self) -> bool:
        return self is FileSystemAccessMode.WRITE


class FileSystemPathKind(str, Enum):
    PATH = 'path'
    GLOB = 'glob'
    SPECIAL = 'special'


class FileSystemSpecialPath(str, Enum):
    ROOT = 'root'
    MINIMAL = 'minimal'
    PROJECT_ROOTS = 'project_roots'
    TMPDIR = 'tmpdir'
    SLASH_TMP = 'slash_tmp'


class FileSystemSandboxKind(str, Enum):
    RESTRICTED = 'restricted'
    UNRESTRICTED = 'unrestricted'
    EXTERNAL = 'external'


class NetworkSandboxPolicy(str, Enum):
    RESTRICTED = 'restricted'
    ENABLED = 'enabled'


class SandboxEnforcement(str, Enum):
    MANAGED = 'managed'
    DISABLED = 'disabled'
    EXTERNAL = 'external'


class AskForApproval(str, Enum):
    UNLESS_TRUSTED = 'unless-trusted'
    ON_REQUEST = 'on-request'
    GRANULAR = 'granular'
    NEVER = 'never'


class ApprovalsReviewer(str, Enum):
    USER = 'user'
    AUTO_REVIEW = 'auto_review'


class PermissionPreset(str, Enum):
    ASK_FOR_APPROVAL = 'ask_for_approval'
    APPROVE_FOR_ME = 'approve_for_me'
    FULL_ACCESS = 'full_access'


@dataclass(frozen=True)
class FileSystemPath:
    kind: FileSystemPathKind
    value: str
    subpath: str = ''

    @classmethod
    def path(cls, value: str | Path) -> 'FileSystemPath':
        return cls(FileSystemPathKind.PATH, str(value))

    @classmethod
    def glob(cls, value: str) -> 'FileSystemPath':
        return cls(FileSystemPathKind.GLOB, value)

    @classmethod
    def special(
        cls,
        value: FileSystemSpecialPath,
        *,
        subpath: str = '',
    ) -> 'FileSystemPath':
        return cls(FileSystemPathKind.SPECIAL, value.value, subpath=subpath)


@dataclass(frozen=True)
class FileSystemSandboxEntry:
    path: FileSystemPath
    access: FileSystemAccessMode
    skip_if_missing: bool = False


@dataclass(frozen=True)
class FileSystemSandboxPolicy:
    kind: FileSystemSandboxKind
    entries: tuple[FileSystemSandboxEntry, ...] = ()
    glob_scan_max_depth: int | None = None


@dataclass(frozen=True)
class PermissionProfile:
    profile_id: str
    enforcement: SandboxEnforcement
    file_system: FileSystemSandboxPolicy
    network: NetworkSandboxPolicy
    description: str = ''
    extends: str | None = None


@dataclass(frozen=True)
class AdditionalPermissionProfile:
    file_system_entries: tuple[FileSystemSandboxEntry, ...] = ()
    network_enabled: bool | None = None

    @property
    def is_empty(self) -> bool:
        return not self.file_system_entries and self.network_enabled is None


@dataclass(frozen=True)
class PermissionPresetSelection:
    preset: PermissionPreset
    profile_id: str
    approval_policy: AskForApproval
    approvals_reviewer: ApprovalsReviewer


@dataclass(frozen=True)
class EffectivePermissions:
    active_profile_id: str
    profile: PermissionProfile
    approval_policy: AskForApproval
    approvals_reviewer: ApprovalsReviewer
    workspace_roots: tuple[Path, ...]
    policy_hash: str
    approved_overlay: AdditionalPermissionProfile | None = None
    granular_approval: GranularApprovalConfig = field(default_factory=lambda: GranularApprovalConfig())

    def to_dict(self) -> dict[str, Any]:
        return {
            'active_profile_id': self.active_profile_id,
            'profile': _profile_to_dict(self.profile),
            'approval_policy': self.approval_policy.value,
            'approvals_reviewer': self.approvals_reviewer.value,
            'workspace_roots': [str(path) for path in self.workspace_roots],
            'policy_hash': self.policy_hash,
            'approved_overlay': (
                _overlay_to_dict(self.approved_overlay)
                if self.approved_overlay is not None
                else None
            ),
            'granular_approval': asdict(self.granular_approval),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'EffectivePermissions':
        profile_payload = data.get('profile')
        if not isinstance(profile_payload, dict):
            raise ValueError('effective permissions checkpoint is missing profile')
        overlay_payload = data.get('approved_overlay')
        return cls(
            active_profile_id=str(data.get('active_profile_id', '') or ''),
            profile=_profile_from_dict(profile_payload),
            approval_policy=AskForApproval(str(data.get('approval_policy', '') or '')),
            approvals_reviewer=ApprovalsReviewer(
                str(data.get('approvals_reviewer', '') or '')
            ),
            workspace_roots=tuple(
                Path(str(item)) for item in data.get('workspace_roots', []) if str(item).strip()
            ),
            policy_hash=str(data.get('policy_hash', '') or ''),
            approved_overlay=(
                _overlay_from_dict(overlay_payload)
                if isinstance(overlay_payload, dict)
                else None
            ),
            granular_approval=GranularApprovalConfig(
                **{
                    key: bool(value)
                    for key, value in (data.get('granular_approval') or {}).items()
                    if key in {
                        'sandbox_approval', 'rules', 'skill_approval',
                        'request_permissions', 'mcp_elicitations',
                    }
                }
            ),
        )


@dataclass(frozen=True)
class GranularApprovalConfig:
    sandbox_approval: bool = True
    rules: bool = True
    skill_approval: bool = True
    request_permissions: bool = True
    mcp_elicitations: bool = True


@dataclass(frozen=True)
class PermissionRequest:
    request_id: str
    tool_name: str
    action: str
    normalized_input_hash: str
    environment_hash: str
    base_policy_hash: str
    effect_hash: str = ''
    requested_overlay: AdditionalPermissionProfile = field(
        default_factory=AdditionalPermissionProfile
    )
    reason: str = ''
    command_prefix: tuple[str, ...] = ()
    session_match_kind: str = ''
    session_match_version: int | None = None
    session_match_digest: str = ''
    session_match_components: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            'request_id': self.request_id,
            'tool_name': self.tool_name,
            'action': self.action,
            'normalized_input_hash': self.normalized_input_hash,
            'environment_hash': self.environment_hash,
            'base_policy_hash': self.base_policy_hash,
            'effect_hash': self.effect_hash,
            'requested_overlay': _overlay_to_dict(self.requested_overlay),
            'reason': self.reason,
            'command_prefix': list(self.command_prefix),
            'session_match_kind': self.session_match_kind,
            'session_match_version': self.session_match_version,
            'session_match_digest': self.session_match_digest,
            'session_match_components': list(self.session_match_components),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'PermissionRequest':
        from ownpi.runtime.permissions.session_identity import read_session_components, read_session_identity

        overlay = data.get('requested_overlay')
        return cls(
            request_id=str(data.get('request_id', '') or ''),
            tool_name=str(data.get('tool_name', '') or ''),
            action=str(data.get('action', '') or ''),
            normalized_input_hash=str(data.get('normalized_input_hash', '') or ''),
            environment_hash=str(data.get('environment_hash', '') or ''),
            base_policy_hash=str(data.get('base_policy_hash', '') or ''),
            effect_hash=str(data.get('effect_hash', '') or ''),
            requested_overlay=(
                _overlay_from_dict(overlay)
                if isinstance(overlay, dict)
                else AdditionalPermissionProfile()
            ),
            reason=str(data.get('reason', '') or ''),
            command_prefix=tuple(
                str(item) for item in data.get('command_prefix', []) if str(item).strip()
            ),
            **read_session_identity(data),
            session_match_components=read_session_components(data),
        )


@dataclass(frozen=True)
class ApprovalReceipt:
    request_id: str
    approved: bool
    reviewer: ApprovalsReviewer
    fingerprint: str
    approved_overlay: AdditionalPermissionProfile | None = None
    reason: str = ''


def _entry_to_dict(entry: FileSystemSandboxEntry) -> dict[str, Any]:
    return {
        'path': {
            'kind': entry.path.kind.value,
            'value': entry.path.value,
            'subpath': entry.path.subpath,
        },
        'access': entry.access.value,
        'skip_if_missing': entry.skip_if_missing,
    }


def _entry_from_dict(data: dict[str, Any]) -> FileSystemSandboxEntry:
    path_payload = data.get('path')
    if not isinstance(path_payload, dict):
        raise ValueError('filesystem policy entry is missing path')
    return FileSystemSandboxEntry(
        path=FileSystemPath(
            kind=FileSystemPathKind(str(path_payload.get('kind', '') or '')),
            value=str(path_payload.get('value', '') or ''),
            subpath=str(path_payload.get('subpath', '') or ''),
        ),
        access=FileSystemAccessMode(str(data.get('access', '') or '')),
        skip_if_missing=bool(data.get('skip_if_missing', False)),
    )


def _profile_to_dict(profile: PermissionProfile) -> dict[str, Any]:
    return {
        'profile_id': profile.profile_id,
        'enforcement': profile.enforcement.value,
        'file_system': {
            'kind': profile.file_system.kind.value,
            'entries': [_entry_to_dict(entry) for entry in profile.file_system.entries],
            'glob_scan_max_depth': profile.file_system.glob_scan_max_depth,
        },
        'network': profile.network.value,
        'description': profile.description,
        'extends': profile.extends,
    }


def _profile_from_dict(data: dict[str, Any]) -> PermissionProfile:
    file_system = data.get('file_system')
    if not isinstance(file_system, dict):
        raise ValueError('permission profile is missing filesystem policy')
    return PermissionProfile(
        profile_id=str(data.get('profile_id', '') or ''),
        enforcement=SandboxEnforcement(str(data.get('enforcement', '') or '')),
        file_system=FileSystemSandboxPolicy(
            kind=FileSystemSandboxKind(str(file_system.get('kind', '') or '')),
            entries=tuple(
                _entry_from_dict(item)
                for item in file_system.get('entries', [])
                if isinstance(item, dict)
            ),
            glob_scan_max_depth=(
                int(file_system['glob_scan_max_depth'])
                if file_system.get('glob_scan_max_depth') is not None
                else None
            ),
        ),
        network=NetworkSandboxPolicy(str(data.get('network', '') or '')),
        description=str(data.get('description', '') or ''),
        extends=(str(data['extends']) if data.get('extends') is not None else None),
    )


def _overlay_to_dict(overlay: AdditionalPermissionProfile) -> dict[str, Any]:
    return {
        'file_system_entries': [
            _entry_to_dict(entry) for entry in overlay.file_system_entries
        ],
        'network_enabled': overlay.network_enabled,
    }


def _overlay_from_dict(data: dict[str, Any]) -> AdditionalPermissionProfile:
    return AdditionalPermissionProfile(
        file_system_entries=tuple(
            _entry_from_dict(item)
            for item in data.get('file_system_entries', [])
            if isinstance(item, dict)
        ),
        network_enabled=(
            bool(data['network_enabled']) if data.get('network_enabled') is not None else None
        ),
    )
