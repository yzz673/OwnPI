//! Overlay and bottom-view state for `BottomPaneState`.
//!
//! Selection views, the option picker, approval/human overlay install/resolve
//! lifecycle, and whole-surface visibility queries.
use super::*;
impl BottomPaneState {
    pub(crate) fn close_active_bottom_view(&mut self) {
        if !self.view_stack.requires_human_action() {
            self.view_stack.clear();
            self.sync_completion_popups();
        }
    }

    pub(crate) fn set_command_browser(&mut self, view: Option<CommandBrowserViewState>) {
        if let Some(view) = view {
            if self.interaction_view_active() {
                return;
            }
            self.view_stack.replace(view);
        } else if matches!(
            self.view_stack.active(),
            Some(BottomPaneViewKind::CommandBrowser)
        ) {
            self.view_stack.clear();
        }
        self.sync_completion_popups();
    }

    pub(crate) fn command_browser_is_visible(&self) -> bool {
        matches!(
            self.view_stack.active(),
            Some(BottomPaneViewKind::CommandBrowser)
        ) && self
            .view_stack
            .active_as::<CommandBrowserViewState>()
            .is_some()
    }

    #[cfg(test)]
    pub(crate) fn set_skills_toggle(&mut self, view: Option<SkillsToggleViewState>) {
        if let Some(view) = view {
            self.view_stack.replace(view);
        } else if matches!(self.view_stack.active(), Some(BottomPaneViewKind::Skills)) {
            self.view_stack.clear();
        }
        self.sync_completion_popups();
    }

    pub(crate) fn push_skills_toggle(&mut self, view: SkillsToggleViewState) {
        self.view_stack.push(view);
        self.sync_completion_popups();
    }

    pub(crate) fn skills_toggle_is_visible(&self) -> bool {
        matches!(self.view_stack.active(), Some(BottomPaneViewKind::Skills))
            && self
                .view_stack
                .active_as::<SkillsToggleViewState>()
                .is_some_and(SkillsToggleViewState::is_visible)
    }

    pub(crate) fn skills_toggle_copy_source(&self) -> Option<String> {
        self.skills_toggle_is_visible()
            .then(|| {
                self.view_stack
                    .active_as::<SkillsToggleViewState>()
                    .map(SkillsToggleViewState::copy_source)
            })
            .flatten()
    }

    pub(crate) fn apply_command_action_result(
        &mut self,
        request_id: &str,
        action: &str,
        ok: bool,
        message: &str,
    ) -> bool {
        self.view_stack
            .active_as_mut::<SkillsToggleViewState>()
            .is_some_and(|view| view.apply_action_result(request_id, action, ok, message))
    }

    pub(crate) fn set_command_document(
        &mut self,
        command_document: Option<CommandDocumentViewState>,
    ) {
        if command_document
            .as_ref()
            .is_some_and(|view| !view.is_visible())
        {
            if matches!(
                self.view_stack.active(),
                Some(BottomPaneViewKind::CommandDocument)
            ) {
                self.view_stack.clear();
            }
            return;
        }
        if let Some(view) = command_document {
            if self.interaction_view_active() {
                return;
            }
            self.view_stack.replace(view);
        } else if matches!(
            self.view_stack.active(),
            Some(BottomPaneViewKind::CommandDocument)
        ) {
            self.view_stack.clear();
        }
        self.sync_completion_popups();
    }

    pub(crate) fn command_document_is_visible(&self) -> bool {
        matches!(
            self.view_stack.active(),
            Some(BottomPaneViewKind::CommandDocument)
        ) && self
            .view_stack
            .active_as::<CommandDocumentViewState>()
            .is_some_and(CommandDocumentViewState::is_visible)
    }

    pub(crate) fn pop_menu_view(&mut self) {
        if !self.view_stack.requires_human_action() {
            self.view_stack.pop(ViewCompletion::Cancelled);
            self.sync_completion_popups();
        }
    }

    pub(crate) fn menu_view_id(&self) -> Option<String> {
        self.view_stack
            .active_as::<SelectionViewState>()
            .map(|view| view.view_id.clone())
    }

    pub(crate) fn refresh_selection_view(&mut self, view: SelectionViewState) -> bool {
        if let Some(current) = self.view_stack.active_as_mut::<SelectionViewState>() {
            if current.view_id == view.view_id {
                current.refresh_from(view);
                return true;
            }
        }
        false
    }

    pub(crate) fn push_command_document(&mut self, view: CommandDocumentViewState) {
        if !self.interaction_view_active() {
            self.view_stack.push(view);
            self.sync_completion_popups();
        }
    }

    pub(crate) fn push_command_browser(&mut self, view: CommandBrowserViewState) {
        if !self.interaction_view_active() {
            self.view_stack.push(view);
            self.sync_completion_popups();
        }
    }

    pub(crate) fn set_selection_view(&mut self, selection_view: Option<SelectionViewState>) {
        if selection_view
            .as_ref()
            .is_some_and(|view| !view.is_visible())
        {
            if matches!(
                self.view_stack.active(),
                Some(BottomPaneViewKind::Selection)
            ) {
                self.view_stack.clear();
            }
            self.sync_completion_popups();
            return;
        }
        if let Some(view) = selection_view {
            if self.interaction_view_active() {
                return;
            }
            self.view_stack.replace(view);
        } else if matches!(
            self.view_stack.active(),
            Some(BottomPaneViewKind::Selection)
        ) {
            self.view_stack.clear();
        }
        self.sync_completion_popups();
    }

    pub(crate) fn push_selection_view(&mut self, selection_view: SelectionViewState) {
        if !selection_view.is_visible() || self.interaction_view_active() {
            return;
        }
        self.view_stack.push(selection_view);
        self.sync_completion_popups();
    }

    pub(crate) fn set_selection_feedback(&mut self, message: impl Into<String>) -> bool {
        let Some(view) = self.view_stack.active_as_mut::<SelectionViewState>() else {
            return false;
        };
        view.set_feedback(message);
        true
    }

    pub(crate) fn selection_view_is_visible(&self) -> bool {
        matches!(
            self.view_stack.active(),
            Some(BottomPaneViewKind::Selection)
        ) && self
            .view_stack
            .active_as::<SelectionViewState>()
            .is_some_and(SelectionViewState::is_visible)
    }

    pub(crate) fn set_option_picker(&mut self, picker: Option<OptionPickerState>) {
        if picker.as_ref().is_some_and(|view| !view.is_visible()) {
            if matches!(
                self.view_stack.active(),
                Some(BottomPaneViewKind::OptionPicker)
            ) {
                self.view_stack.clear();
            }
            self.sync_completion_popups();
            return;
        }
        if let Some(view) = picker {
            if self.interaction_view_active() {
                return;
            }
            self.view_stack.replace(view);
        } else if matches!(
            self.view_stack.active(),
            Some(BottomPaneViewKind::OptionPicker)
        ) {
            self.view_stack.clear();
        }
        self.sync_completion_popups();
    }

    pub(crate) fn option_picker_is_visible(&self) -> bool {
        matches!(
            self.view_stack.active(),
            Some(BottomPaneViewKind::OptionPicker)
        ) && self
            .view_stack
            .active_as::<OptionPickerState>()
            .is_some_and(OptionPickerState::is_visible)
    }

    #[cfg(test)]
    pub(crate) fn selected_selection_row(&self) -> Option<&str> {
        self.view_stack
            .active_as::<SelectionViewState>()
            .and_then(SelectionViewState::selected_row)
            .map(|row| row.name.as_str())
    }

    pub(crate) fn no_modal_or_popup_active(&self) -> bool {
        self.view_stack.is_empty()
            && !self.slash_visible()
            && !self.file_visible()
            && !self.skill_visible()
    }

    pub(crate) fn surface_popup_is_visible(&self) -> bool {
        self.active_bottom_view_is_visible()
            || self.slash_visible()
            || self.file_visible()
            || self.skill_visible()
    }

    pub(crate) fn active_bottom_view_is_visible(&self) -> bool {
        self.command_browser_is_visible()
            || self.skills_toggle_is_visible()
            || self.command_document_is_visible()
            || self.option_picker_is_visible()
            || self.selection_view_is_visible()
            || self.permission_overlay_is_visible()
            || self.human_question_bottom_view_is_visible()
    }

    pub(crate) fn terminal_title_requires_action(&self) -> bool {
        self.view_stack.requires_human_action()
    }

    #[cfg(test)]
    pub(crate) fn active_view_stack_state(&self) -> (Option<BottomPaneViewKind>, usize) {
        (self.view_stack.active(), self.view_stack.depth())
    }

    pub(crate) fn set_overlay(&mut self, overlay: Option<PendingInteractionState>) {
        let Some(overlay) = overlay else {
            self.clear_overlay();
            return;
        };
        if self.interaction_view_active() {
            if let Some(existing) = self
                .overlay_queue
                .iter_mut()
                .find(|queued| queued.interaction_id() == overlay.interaction_id())
            {
                *existing = overlay;
                return;
            }
            self.overlay_queue.push_back(overlay);
            self.sync_completion_popups();
            return;
        }
        self.install_overlay(overlay);
    }

    fn install_overlay(&mut self, overlay: PendingInteractionState) {
        match overlay {
            PendingInteractionState::Human(mut interaction) => {
                interaction.ensure_focus_available();
                let mut view = HumanQuestionViewState::new(interaction);
                view.restore_current_draft();
                self.view_stack.replace(view);
                self.sync_completion_popups();
            }
            PendingInteractionState::Permission(interaction) => {
                self.view_stack
                    .replace(ApprovalOverlayState::new(interaction));
                self.composer.clear();
                self.sync_completion_popups();
            }
        }
    }

    pub(crate) fn overlay(&self) -> Option<ActiveInteractionRef<'_>> {
        if let Some(view) = self.human_question_view() {
            return Some(ActiveInteractionRef::Human(&view.interaction));
        }
        self.approval_overlay()
            .map(|view| ActiveInteractionRef::Permission(view.permission()))
    }

    pub(crate) fn clear_overlay(&mut self) {
        let preserve_composer = self.human_question_view().is_some();
        self.view_stack.clear();
        self.overlay_queue.clear();
        if !preserve_composer {
            self.composer.clear();
        }
        self.sync_completion_popups();
    }

    pub(crate) fn resolve_current_overlay(&mut self) {
        let preserve_composer = self.human_question_view().is_some();
        self.view_stack.clear();
        if let Some(next_overlay) = self.overlay_queue.pop_front() {
            crate::bridge::append_debug_log(
                "overlay_queue_pop",
                &serde_json::json!({
                    "kind": match &next_overlay {
                        PendingInteractionState::Human(_) => "human",
                        PendingInteractionState::Permission(_) => "permission",
                    },
                    "queue_remaining": self.overlay_queue.len(),
                }),
            );
            self.install_overlay(next_overlay);
            return;
        }
        if !preserve_composer {
            self.composer.clear();
        }
        self.sync_completion_popups();
    }
}
