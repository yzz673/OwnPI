//! Composer accessors and input state for `BottomPaneState`.
//!
//! Text, paste, history, vim-mode, and image accessors plus submission
//! classification. Completion popups live in `pane_popups`, overlays and
//! bottom views in `pane_overlays`.
use super::*;
impl BottomPaneState {
    pub(crate) fn resume_browser_key(&mut self, key: KeyEvent) -> bool {
        self.view_stack.active_as_mut::<SelectionViewState>()
            .is_some_and(|view| view.browser_key(key))
    }

    pub(crate) fn composer(&self) -> &str {
        self.human_question_view()
            .map(HumanQuestionViewState::composer)
            .unwrap_or(&self.composer)
            .text()
    }

    pub(crate) fn composer_text_with_pending(&self) -> String {
        self.human_question_view()
            .map(HumanQuestionViewState::composer)
            .unwrap_or(&self.composer)
            .current_text_with_pending()
    }

    pub(crate) fn vim_insert_mode_active(&self) -> bool {
        self.human_question_view()
            .map(HumanQuestionViewState::composer)
            .unwrap_or(&self.composer)
            .vim_insert_mode_active()
    }

    pub(crate) fn vim_is_enabled(&self) -> bool {
        self.human_question_view()
            .map(HumanQuestionViewState::composer)
            .unwrap_or(&self.composer)
            .vim_is_enabled()
    }

    pub(crate) fn vim_enter_normal_mode(&mut self) {
        if let Some(view) = self.human_question_view_mut() {
            view.composer_mut().vim_enter_normal_mode();
        } else {
            self.composer.vim_enter_normal_mode();
        }
    }

    pub(crate) fn set_vim_enabled(&mut self, enabled: bool) {
        self.composer.set_vim_enabled(enabled);
        if let Some(view) = self.human_question_view_mut() {
            view.composer_mut().set_vim_enabled(enabled);
        }
    }

    pub(crate) fn take_local_images(&mut self) -> Vec<LocalImageAttachment> {
        self.composer.take_local_images()
    }

    pub(crate) fn take_remote_images(
        &mut self,
    ) -> Vec<crate::bottom_pane::attachment::RemoteImageAttachment> {
        self.composer.take_remote_images()
    }

    pub(crate) fn restore_remote_images(
        &mut self,
        images: Vec<crate::bottom_pane::attachment::RemoteImageAttachment>,
    ) {
        self.composer.restore_remote_images(images);
    }

    pub(crate) fn attach_local_image(&mut self, path: std::path::PathBuf) {
        let detail = self.image_detail;
        self.composer.attach_local_image_with_detail(path, detail);
    }

    pub(crate) fn restore_local_images(&mut self, images: Vec<LocalImageAttachment>) {
        self.composer.restore_local_images(images);
    }

    pub(crate) fn set_composer(&mut self, value: impl Into<String>) {
        self.composer.set_text(value);
        self.dismissed_slash_input = None;
        self.sync_completion_popups();
    }

    pub(crate) fn clear_composer(&mut self) {
        self.composer.clear();
        self.dismissed_slash_input = None;
        self.sync_completion_popups();
    }

    pub(crate) fn flush_pending_input_if_due(&mut self) -> bool {
        let changed = if let Some(view) = self.human_question_view_mut() {
            view.flush_pending_input_if_due()
        } else {
            self.composer.flush_paste_burst_if_due()
        };
        if changed {
            self.sync_completion_popups();
        }
        changed
    }

    pub(crate) fn is_in_paste_burst(&self) -> bool {
        self.human_question_view()
            .map(HumanQuestionViewState::is_in_paste_burst)
            .unwrap_or_else(|| self.composer.is_in_paste_burst())
    }

    pub(crate) fn insert_paste(&mut self, pasted: &str) {
        if pasted.is_empty() {
            return;
        }
        if self.view_stack.active_as_mut::<SelectionViewState>()
            .is_some_and(|view| view.browser_paste(pasted)) {
            return;
        }
        if let Some(view) = self.view_stack.active_as_mut::<SkillsToggleViewState>() {
            if view.insert_text(pasted) {
                return;
            }
        }
        if matches!(
            self.view_stack.active(),
            Some(
                BottomPaneViewKind::Selection
                    | BottomPaneViewKind::OptionPicker
                    | BottomPaneViewKind::CommandDocument
                    | BottomPaneViewKind::CommandBrowser
                    | BottomPaneViewKind::Skills
            )
        ) {
            return;
        }
        if let Some(mut view) = self.view_stack.take_active::<HumanQuestionViewState>() {
            let open_notes = view.human().current_question_has_options()
                && !view.human().notes_visible_for_current_question();
            if open_notes {
                view.human_mut().show_notes_for_current_question();
                view.restore_current_draft();
            }
            view.composer_mut().insert_paste(pasted);
            view.save_current_draft();
            self.view_stack.replace(view);
            return;
        }
        self.composer.insert_paste(pasted);
        self.sync_input_to_overlay();
        self.sync_completion_popups();
    }

    pub(crate) fn composer_height(&self, width: u16) -> u16 {
        if !self.view_stack.is_empty() {
            return 0;
        }
        self.composer.desired_height(width)
    }

    pub(crate) fn composer_cursor_position(&self, area: Rect) -> Option<(u16, u16)> {
        if !self.view_stack.is_empty() {
            return None;
        }
        self.composer.cursor_pos(area)
    }

    pub(crate) fn render_composer(
        &self,
        area: Rect,
        buf: &mut Buffer,
        theme: &ThemePalette,
        palette: BottomPanePalette,
    ) {
        self.composer.render(area, buf, theme, palette)
    }

    pub(crate) fn record_submission(&mut self, text: &str) {
        if self.composer.has_pending_pastes()
            && self.composer.current_text_with_pending().trim() == text.trim()
        {
            self.composer.record_current_submission();
        } else {
            self.composer.record_submission(text);
        }
    }

    pub(crate) fn set_persistent_history(&mut self, entries: Vec<String>, log_id: u64) {
        self.composer.set_persistent_history(entries, log_id);
    }

    #[allow(dead_code)]
    pub(crate) fn composer_draft_snapshot(&self) -> ComposerDraftSnapshot {
        if let Some(view) = self.human_question_view() {
            return ComposerDraftSnapshot {
                text: view.composer().text().to_string(),
                text_elements: view.composer().text_elements(),
                pending_pastes: view.composer().pending_pastes(),
            };
        }
        ComposerDraftSnapshot {
            text: self.composer.text().to_string(),
            text_elements: self.composer.text_elements(),
            pending_pastes: self.composer.pending_pastes(),
        }
    }

    pub(crate) fn restore_composer_state(&mut self, draft: ComposerDraftSnapshot) {
        if let Some(view) = self.human_question_view_mut() {
            view.composer_mut().set_text_with_pending(
                draft.text,
                draft.text_elements,
                draft.pending_pastes,
            );
        } else {
            self.composer.set_text_with_pending(
                draft.text,
                draft.text_elements,
                draft.pending_pastes,
            );
        }
        self.sync_completion_popups();
    }

    pub(crate) fn classify_current_submission(&self) -> InputResult {
        let visible_text = self.composer.text().trim().to_string();
        if visible_text.is_empty() {
            return InputResult::None;
        }
        let raw_text = self.composer.current_text_with_pending().trim().to_string();
        if self.slash_visible() {
            if let Some(command) = self.command_popup.selected_item() {
                return InputResult::Command(SlashCommandSubmission {
                    text: format!("/{}", command.command()),
                    command,
                    args: None,
                });
            }
        }
        let token = visible_text.split_whitespace().next().unwrap_or_default();
        if !token.starts_with('/') {
            return InputResult::SubmittedText(raw_text);
        }
        let Some(command) = self.command_popup.exact_match(token) else {
            if token.len() > 1
                && !token[1..].contains('/')
                && token[1..]
                    .chars()
                    .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '-' | '_'))
            {
                return InputResult::UnknownSlash(raw_text);
            }
            return InputResult::SubmittedText(raw_text);
        };
        let args = raw_text[token.len()..].trim().to_string();
        if args.is_empty() {
            return InputResult::Command(SlashCommandSubmission {
                text: format!("/{}", command.command()),
                command,
                args: None,
            });
        }
        InputResult::CommandWithArgs(SlashCommandSubmission {
            text: raw_text,
            command,
            args: Some(args),
        })
    }
}
