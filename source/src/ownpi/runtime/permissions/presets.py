from __future__ import annotations

from ownpi.runtime.permissions.models import (
    AskForApproval,
    ApprovalsReviewer,
    BUILT_IN_PERMISSION_PROFILE_DANGER_FULL_ACCESS,
    BUILT_IN_PERMISSION_PROFILE_WORKSPACE,
    PermissionPreset,
    PermissionPresetSelection,
)


_PRESETS = {
    PermissionPreset.ASK_FOR_APPROVAL: PermissionPresetSelection(
        preset=PermissionPreset.ASK_FOR_APPROVAL,
        profile_id=BUILT_IN_PERMISSION_PROFILE_WORKSPACE,
        approval_policy=AskForApproval.ON_REQUEST,
        approvals_reviewer=ApprovalsReviewer.USER,
    ),
    PermissionPreset.APPROVE_FOR_ME: PermissionPresetSelection(
        preset=PermissionPreset.APPROVE_FOR_ME,
        profile_id=BUILT_IN_PERMISSION_PROFILE_WORKSPACE,
        approval_policy=AskForApproval.ON_REQUEST,
        approvals_reviewer=ApprovalsReviewer.AUTO_REVIEW,
    ),
    PermissionPreset.FULL_ACCESS: PermissionPresetSelection(
        preset=PermissionPreset.FULL_ACCESS,
        profile_id=BUILT_IN_PERMISSION_PROFILE_DANGER_FULL_ACCESS,
        approval_policy=AskForApproval.NEVER,
        approvals_reviewer=ApprovalsReviewer.USER,
    ),
}


def permission_preset_selection(
    preset: PermissionPreset | str,
) -> PermissionPresetSelection:
    try:
        normalized = preset if isinstance(preset, PermissionPreset) else PermissionPreset(str(preset))
    except ValueError as exc:
        raise ValueError(f'unknown permission preset: {preset!r}') from exc
    return _PRESETS[normalized]
