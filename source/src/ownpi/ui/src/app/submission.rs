//! Local input presentation. Canonical history is never synthesized here.
use super::*;
use crate::bottom_pane::attachment::RemoteImageAttachment;
use crate::bottom_pane::{ComposerDraftSnapshot, LocalImageAttachment};

#[derive(Default, PartialEq, Eq)]
pub(super) enum SubmissionState {
    #[default]
    Sending,
    Recorded(String),
    NotSent,
    Unconfirmed,
}

pub(super) struct Submission {
    pub request_id: String,
    pub text: String,
    pub draft: ComposerDraftSnapshot,
    pub local_images: Vec<LocalImageAttachment>,
    pub remote_images: Vec<RemoteImageAttachment>,
    pub state: SubmissionState,
}

impl AppState {
    pub(super) fn reconcile_submission(&mut self, receipts: &Value) {
        let Some(submission) = &mut self.submission else {
            return;
        };
        if let Some(receipt) = receipts.as_array().and_then(|receipts| {
            receipts.iter().find(|r| {
                r["request_id"] == submission.request_id
                    && r["item_id"].as_str().is_some_and(|id| !id.is_empty())
            })
        }) {
            submission.state = SubmissionState::Recorded(text(receipt, "item_id"));
        }
    }

    pub(crate) fn submission_lines(&self, width: usize) -> Vec<Line<'static>> {
        if self.agents.active.is_some() {
            return Vec::new();
        }
        let Some(submission) = &self.submission else {
            return Vec::new();
        };
        if let SubmissionState::Recorded(id) = &submission.state {
            if self
                .canonical
                .iter()
                .any(|item| item["kind"] == "user" && item["item_id"] == *id)
            {
                return Vec::new();
            }
        }
        let label = match submission.state {
            // Normal delivery is represented by the existing Working status.
            // Retain acknowledgement state internally without flashing a second label.
            SubmissionState::Sending | SubmissionState::Recorded(_) => None,
            SubmissionState::NotSent => Some("Not sent · draft retained"),
            SubmissionState::Unconfirmed => {
                Some("Delivery unconfirmed · inspect the run before retrying")
            }
        };
        let cell = BasicHistoryCell::new(
            BasicHistoryCellKind::User,
            submission.text.clone(),
            self.theme.clone(),
        );
        let mut lines =
            crate::terminal_hyperlinks::visible_lines(cell.display_hyperlink_lines_edged(width));
        if let Some(label) = label {
            lines.extend(
                crate::wrapping::adaptive_wrap_text(label, width.max(1))
                    .into_iter()
                    .map(Line::from),
            );
        }
        lines
    }

    pub(super) fn submission_failed(&mut self, request: &str, not_sent: bool) -> bool {
        let Some(submission) = &mut self.submission else {
            return false;
        };
        if submission.request_id != request {
            return false;
        }
        if matches!(submission.state, SubmissionState::Recorded(_)) {
            return true;
        }
        submission.state = if not_sent {
            SubmissionState::NotSent
        } else {
            SubmissionState::Unconfirmed
        };
        if not_sent && self.bottom_pane.composer_text_with_pending().is_empty() {
            self.bottom_pane
                .restore_composer_state(submission.draft.clone());
            self.bottom_pane
                .restore_local_images(submission.local_images.clone());
            self.bottom_pane
                .restore_remote_images(submission.remote_images.clone());
        } else if not_sent {
            self.status.set_notice(Some(format!(
                "{} · Current draft kept; previous request is in input history.",
                self.last_error
            )));
        }
        true
    }
}
