//! Human/permission interaction transitions for `BottomPaneState`.
//!
//! Overlay input syncing, question navigation, option toggling, submission
//! payloads and permission option accessors.
use super::*;
impl BottomPaneState {
    pub(crate) fn interaction_submission_requested(&self) -> bool {
        match self.pending_interaction() {
            Some(ActiveInteractionRef::Human(interaction)) => interaction.submit_requested,
            Some(ActiveInteractionRef::Permission(interaction)) => interaction.submission.blocked(),
            None => false,
        }
    }

    pub(crate) fn mark_interaction_submitting(&mut self, interaction_id: &str) -> bool {
        let Some(mut pending) = self.pending_interaction_mut() else {
            return false;
        };
        if pending.interaction_id() != interaction_id || pending.submit_requested() {
            return false;
        }
        pending.set_submit_requested(true);
        self.set_permission_submission_error(None);
        true
    }

    pub(crate) fn set_permission_submission_error(&mut self, error: Option<String>) {
        if let Some(overlay) = self.approval_overlay_mut() {
            overlay.set_submission_error(error);
        }
    }

    pub(crate) fn clear_interaction_submitting(&mut self, interaction_id: Option<&str>) {
        if let Some(mut pending) = self.pending_interaction_mut() {
            if interaction_id.is_none_or(|expected| pending.interaction_id() == expected) {
                pending.set_submit_requested(false);
            }
        }
    }

    pub(crate) fn permission_overlay_is_visible(&self) -> bool {
        matches!(
            self.view_stack.active(),
            Some(BottomPaneViewKind::Permission)
        ) && self.approval_overlay().is_some()
    }

    pub(crate) fn human_question_bottom_view_is_visible(&self) -> bool {
        matches!(
            self.view_stack.active(),
            Some(BottomPaneViewKind::HumanQuestion)
        ) && self.human_question_view().is_some()
    }

    pub(crate) fn suppress_human_question_activity(&self) -> bool {
        self.human_question_view().is_some()
    }

    pub(crate) fn suppress_human_question_status(&self) -> bool {
        self.suppress_human_question_activity()
    }

    pub(crate) fn active_human_is_question(&self) -> bool {
        self.human_question_view().is_some()
    }

    pub(crate) fn active_human_cancel_requested(&self) -> bool {
        self.human_question_view()
            .is_some_and(|view| view.human().cancel_requested)
    }

    pub(crate) fn sync_input_to_overlay(&mut self) {
        if let Some(view) = self.human_question_view_mut() {
            view.save_current_draft();
        }
    }

    pub(crate) fn reload_input_from_overlay(&mut self) {
        if let Some(view) = self.human_question_view_mut() {
            view.restore_current_draft();
        }
    }

    pub(crate) fn next_question(&mut self) {
        if let Some(view) = self.human_question_view_mut() {
            view.move_question(true);
        }
    }

    pub(crate) fn previous_question(&mut self) {
        if let Some(view) = self.human_question_view_mut() {
            view.move_question(false);
        }
    }

    pub(crate) fn page_permission_details(&self, down: bool) {
        if let Some(overlay) = self.approval_overlay() {
            overlay.page_details(down);
        }
    }

    pub(crate) fn permission_option_id_for_shortcut(&self, shortcut: char) -> Option<String> {
        if self.interaction_submission_requested() {
            return None;
        }
        self.approval_overlay()
            .and_then(|overlay| overlay.permission_option_id_for_shortcut(shortcut))
    }

    pub(crate) fn selected_permission_option_id(&self) -> Option<String> {
        if self.interaction_submission_requested() {
            return None;
        }
        self.approval_overlay()
            .and_then(ApprovalOverlayState::selected_permission_option_id)
    }

    pub(crate) fn human_advance_or_submit(&mut self) -> HumanOverlaySubmitAction {
        self.sync_input_to_overlay();
        let action = {
            let Some(ActiveInteractionMut::Human(interaction)) = self.pending_interaction_mut()
            else {
                return HumanOverlaySubmitAction::None;
            };
            if interaction.confirm_unanswered {
                if interaction.confirm_unanswered_selection == 0 {
                    if let Some(index) = interaction.first_missing_required_question() {
                        interaction.focus_question(index);
                        return HumanOverlaySubmitAction::None;
                    }
                    interaction.confirm_unanswered = false;
                    return HumanOverlaySubmitAction::Submit {
                        interaction_id: interaction.interaction_id.clone(),
                        answers_json: interaction.answers_json(),
                    };
                }
                interaction.close_unanswered_confirmation_and_go_back();
                return HumanOverlaySubmitAction::None;
            }
            if interaction.current_option_is_other()
                && !interaction.notes_visible_for_current_question()
            {
                interaction.select_current_option();
                interaction.show_notes_for_current_question();
                return HumanOverlaySubmitAction::None;
            }
            interaction.commit_current_question();
            if interaction.current_question_missing_required() {
                interaction.confirm_unanswered = false;
                return HumanOverlaySubmitAction::None;
            }
            if !interaction.is_last_question() {
                interaction.next_question();
                HumanOverlaySubmitAction::Advanced
            } else if let Some(index) = interaction.first_missing_required_question() {
                interaction.focus_question(index);
                HumanOverlaySubmitAction::None
            } else if interaction.optional_unanswered_count() > 0 {
                interaction.confirm_unanswered = true;
                interaction.confirm_unanswered_selection = 0;
                HumanOverlaySubmitAction::None
            } else {
                HumanOverlaySubmitAction::Submit {
                    interaction_id: interaction.interaction_id.clone(),
                    answers_json: interaction.answers_json(),
                }
            }
        };
        if matches!(action, HumanOverlaySubmitAction::Advanced) {
            self.reload_input_from_overlay();
        }
        action
    }

    pub(crate) fn first_missing_required_question(&self) -> Option<usize> {
        match self.pending_interaction() {
            Some(ActiveInteractionRef::Human(interaction)) => {
                interaction.first_missing_required_question()
            }
            _ => None,
        }
    }

    pub(crate) fn move_overlay_up(&mut self) {
        if let Some(view) = self.human_question_view_mut() {
            let interaction = view.human_mut();
            if !interaction.confirm_unanswered {
                interaction.move_option_cursor_up();
            }
            return;
        }
        if let Some(overlay) = self.approval_overlay_mut() {
            overlay.move_up();
        }
    }

    pub(crate) fn move_overlay_down(&mut self) {
        if let Some(view) = self.human_question_view_mut() {
            let interaction = view.human_mut();
            if !interaction.confirm_unanswered {
                interaction.move_option_cursor_down();
            }
            return;
        }
        if let Some(overlay) = self.approval_overlay_mut() {
            overlay.move_down();
        }
    }
}

impl HumanInteractionState {
    pub(crate) fn current_question_missing_required(&self) -> bool {
        self.current_question().is_some_and(|question| {
            question.required && !self.draft_for(&question.question_id).has_value()
        })
    }

    pub(crate) fn current_question_show_required_error(&self) -> bool {
        self.current_question().is_some_and(|question| {
            let draft = self.draft_for(&question.question_id);
            question.required && draft.answer_committed && !draft.has_value()
        })
    }

    fn focus_question(&mut self, index: usize) {
        if self.questions.is_empty() {
            self.current_question_index = 0;
            return;
        }
        self.current_question_index = index.min(self.questions.len().saturating_sub(1));
        self.confirm_unanswered = false;
        self.ensure_focus_available();
    }
}
