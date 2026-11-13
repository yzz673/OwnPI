use super::*;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub(crate) enum PermissionPreset {
    AskForApproval,
    ApproveForMe,
    FullAccess,
    #[default]
    Unknown,
}

impl PermissionPreset {
    pub(super) fn parse(value: Option<&str>) -> Self {
        match value {
            Some("ask_for_approval") => Self::AskForApproval,
            Some("approve_for_me") => Self::ApproveForMe,
            Some("full_access") => Self::FullAccess,
            _ => Self::Unknown,
        }
    }

    fn label(self) -> &'static str {
        match self {
            Self::AskForApproval => "Ask for approval",
            Self::ApproveForMe => "Approve for me",
            Self::FullAccess => "Full access",
            Self::Unknown => "Unknown",
        }
    }
}

impl AppState {
    pub(crate) fn current_permission_label(&self) -> &'static str {
        if self.active {
            self.execution_permissions
        } else {
            self.workspace.permissions
        }
        .label()
    }
}
