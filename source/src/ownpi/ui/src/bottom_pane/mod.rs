//! Bottom pane state: composer, completion popups, selection views, and
//! human/permission interaction overlays.
//!
//! This module owns the `BottomPaneState` struct, shared imports, input wiring
//! (`handle_key`/`handle_escape`), and the private helper methods shared across
//! the focused child modules (impl fan-out; see `pane_*` modules).

mod approval_overlay;
pub(crate) mod attachment;
mod bottom_pane_view;
mod chat_composer;
mod chat_composer_history;
mod command_browser;
mod command_document;
mod command_popup;
mod file_popup;
pub(crate) mod footer;
mod human_question_layout;
mod human_question_view;
mod interaction_types;
mod menu_surface;
mod pane_composer;
mod pane_interaction;
mod pane_overlays;
mod pane_popups;
mod parse;
mod paste_burst;
mod scroll_state;
mod resume_browser;
mod selection_popup;
mod skill_popup;
mod skills_toggle_view;
mod slash_commands;
mod textarea;

use std::collections::{BTreeMap, VecDeque};

use crate::keymap::{DefaultKeymap, InteractionAction};
use crossterm::event::{KeyCode, KeyEvent, KeyEventKind, KeyModifiers};
use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Paragraph, Widget};
use serde_json::{json, Map, Value};

use crate::option_picker::OptionPickerState;
use crate::theme::{BottomPanePalette, ThemePalette};
pub(crate) use approval_overlay::ApprovalOverlayState;
pub(crate) use attachment::LocalImageAttachment;
pub(crate) use bottom_pane_view::{BottomPaneViewKind, BottomPaneViewStack, ViewCompletion};
pub(crate) use chat_composer::ChatComposer;
pub(crate) use command_browser::{CommandBrowserInput, CommandBrowserViewState};
pub(crate) use command_document::CommandDocumentViewState;
pub(crate) use command_popup::CommandPopupState;
pub(crate) use file_popup::{FileMatch, FilePopupState};
pub(crate) use human_question_view::HumanQuestionViewState;
pub(crate) use menu_surface::{
    menu_footer_lines, menu_surface_content_width, menu_surface_padding_height,
    render_menu_surface, scroll_position,
};
pub(crate) use scroll_state::ScrollState;
pub(crate) use selection_popup::{
    measure_rows_height, render_rows, wrapped_row_viewport, DisplayRow, ListSelectionView,
    SelectionAction, SelectionViewRow, SelectionViewState, MAX_POPUP_ROWS,
};
pub(crate) use skill_popup::SkillPopupState;
pub(crate) use skills_toggle_view::{SkillsToggleInput, SkillsToggleViewState};
pub(crate) use slash_commands::{commands_for_input, ServiceTierCommand, SlashCommandItem};
use textarea::TextElementSnapshot;

pub(crate) use interaction_types::{
    ActiveInteractionMut, ActiveInteractionRef, ClarificationQuestion, EscapeAction,
    HumanInteractionState, HumanOverlaySubmitAction, HumanQuestionFocus, InputResult,
    PendingInteractionState, PendingOption, PermissionFileChange, PermissionInteractionState,
    SlashCommandSubmission,
};
pub(crate) use parse::parse_question;
const HUMAN_OTHER_OPTION_LABEL: &str = "None of the above";
const HUMAN_OTHER_OPTION_DESCRIPTION: &str = "Optionally, add details in notes (tab).";

impl ClarificationQuestion {
    fn effective_option_count(&self) -> usize {
        self.options.len() + usize::from(self.is_other && !self.options.is_empty())
    }

    fn option_label_at(&self, index: usize) -> Option<&str> {
        if let Some(option) = self.options.get(index) {
            return Some(option.label.as_str());
        }
        (index == self.options.len() && self.is_other && !self.options.is_empty())
            .then_some(HUMAN_OTHER_OPTION_LABEL)
    }

    fn option_description_at(&self, index: usize) -> Option<&str> {
        if let Some(option) = self.options.get(index) {
            return Some(option.description.as_str());
        }
        (index == self.options.len() && self.is_other && !self.options.is_empty())
            .then_some(HUMAN_OTHER_OPTION_DESCRIPTION)
    }
}

#[derive(Debug)]
pub(crate) struct BottomPaneState {
    composer: ChatComposer,
    image_detail: crate::bottom_pane::attachment::ImageDetail,
    overlay_queue: VecDeque<PendingInteractionState>,
    command_popup: CommandPopupState,
    dismissed_slash_input: Option<String>,
    file_popup: FilePopupState,
    skill_popup: SkillPopupState,
    view_stack: BottomPaneViewStack,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct ComposerDraftSnapshot {
    pub(crate) text: String,
    pub(crate) text_elements: Vec<TextElementSnapshot>,
    pub(crate) pending_pastes: Vec<(String, String)>,
}

impl BottomPaneState {
    fn human_question_view(&self) -> Option<&HumanQuestionViewState> {
        self.view_stack.active_as::<HumanQuestionViewState>()
    }

    fn human_question_view_mut(&mut self) -> Option<&mut HumanQuestionViewState> {
        self.view_stack.active_as_mut::<HumanQuestionViewState>()
    }

    fn approval_overlay(&self) -> Option<&ApprovalOverlayState> {
        self.view_stack.active_as::<ApprovalOverlayState>()
    }

    fn approval_overlay_mut(&mut self) -> Option<&mut ApprovalOverlayState> {
        self.view_stack.active_as_mut::<ApprovalOverlayState>()
    }

    fn interaction_view_active(&self) -> bool {
        matches!(
            self.view_stack.active(),
            Some(BottomPaneViewKind::HumanQuestion | BottomPaneViewKind::Permission)
        )
    }

    pub(crate) fn recommended_input_flush_delay() -> std::time::Duration {
        ChatComposer::recommended_input_flush_delay()
    }

    pub(crate) fn default_with_commands() -> Self {
        let vim_enabled = std::env::var("OWNPI_VIM")
            .map(|value| value == "1")
            .unwrap_or(false);
        let composer = {
            let mut composer = ChatComposer::default();
            composer.set_vim_enabled(vim_enabled);
            composer
        };
        Self {
            composer,
            image_detail: crate::bottom_pane::attachment::ImageDetail::default(),
            overlay_queue: VecDeque::new(),
            command_popup: CommandPopupState::new(commands_for_input(&[])),
            dismissed_slash_input: None,
            file_popup: FilePopupState::new(),
            skill_popup: SkillPopupState::new(),
            view_stack: BottomPaneViewStack::default(),
        }
    }

    pub(crate) fn handle_key(&mut self, key: KeyEvent) -> InputResult {
        if key.kind == KeyEventKind::Release {
            return InputResult::None;
        }
        // Keep this order aligned with Codex: modal/picker surfaces own keys first,
        // then active completion popups, then the normal composer/history path.
        if matches!(
            self.view_stack.active(),
            Some(BottomPaneViewKind::CommandBrowser)
        ) {
            let Some(view) = self.view_stack.active_as_mut::<CommandBrowserViewState>() else {
                self.view_stack.clear();
                return InputResult::None;
            };
            return match view.handle_key(key) {
                CommandBrowserInput::None => InputResult::None,
                CommandBrowserInput::Edited => InputResult::Edited,
                CommandBrowserInput::Close => {
                    self.view_stack.pop(ViewCompletion::Cancelled);
                    InputResult::Edited
                }
                CommandBrowserInput::Selected(text) => {
                    self.view_stack.pop(ViewCompletion::Accepted);
                    InputResult::OptionPicked(text)
                }
                CommandBrowserInput::Inserted(text) => {
                    self.view_stack.pop(ViewCompletion::Accepted);
                    InputResult::ComposerReplace(text)
                }
            };
        }
        if matches!(self.view_stack.active(), Some(BottomPaneViewKind::Skills)) {
            let Some(view) = self.view_stack.active_as_mut::<SkillsToggleViewState>() else {
                self.view_stack.clear();
                return InputResult::None;
            };
            return match view.handle_key(key) {
                SkillsToggleInput::None => InputResult::None,
                SkillsToggleInput::Edited => InputResult::Edited,
                SkillsToggleInput::Close => {
                    self.view_stack.pop(ViewCompletion::Cancelled);
                    InputResult::Edited
                }
                SkillsToggleInput::Action(payload) => InputResult::CommandAction(payload),
            };
        }

        if matches!(
            self.view_stack.active(),
            Some(BottomPaneViewKind::CommandDocument)
        ) {
            let Some(view) = self.view_stack.active_as_mut::<CommandDocumentViewState>() else {
                self.view_stack.clear();
                return InputResult::None;
            };
            return if view.handle_key(key) {
                InputResult::Edited
            } else {
                InputResult::None
            };
        }
        if matches!(
            self.view_stack.active(),
            Some(BottomPaneViewKind::Selection)
        ) {
            let Some(selection_view) = self.view_stack.active_as_mut::<SelectionViewState>() else {
                self.view_stack.clear();
                return InputResult::None;
            };
            if selection_view.resume_browser.is_some() {
                if key.code == KeyCode::Char('o') && key.modifiers == KeyModifiers::CONTROL {
                    return selection_view.details_action().map(InputResult::SelectionPicked)
                        .unwrap_or(InputResult::Edited);
                }
                if selection_view.browser_key(key) {
                    return InputResult::Edited;
                }
            }
            match DefaultKeymap::interaction_action(key) {
                Some(InteractionAction::Cancel) => {
                    let cancel_action = selection_view.cancel_action.clone();
                    self.view_stack.pop(ViewCompletion::Cancelled);
                    return cancel_action
                        .map(InputResult::SelectionPicked)
                        .unwrap_or(InputResult::Edited);
                }
                Some(InteractionAction::Previous) => {
                    selection_view.move_up();
                    return InputResult::Edited;
                }
                Some(InteractionAction::Next) => {
                    selection_view.move_down();
                    return InputResult::Edited;
                }
                Some(InteractionAction::Accept) => {
                    let action = selection_view.accept_action();
                    let dismiss = selection_view.selected_row_dismisses_on_select();
                    if dismiss {
                        self.view_stack.pop(ViewCompletion::Accepted);
                    }
                    return action
                        .map(InputResult::SelectionPicked)
                        .unwrap_or(InputResult::Edited);
                }
                Some(InteractionAction::PageUp) => {
                    selection_view.page_up();
                    return InputResult::Edited;
                }
                Some(InteractionAction::PageDown) => {
                    selection_view.page_down();
                    return InputResult::Edited;
                }
                Some(InteractionAction::JumpTop) => {
                    selection_view.jump_top();
                    return InputResult::Edited;
                }
                Some(InteractionAction::JumpBottom) => {
                    selection_view.jump_bottom();
                    return InputResult::Edited;
                }
                None => {}
            }
            match key {
                KeyEvent {
                    code: KeyCode::Char(ch),
                    modifiers,
                    ..
                } if modifiers.is_empty() => {
                    let Some(number) = ch.to_digit(10).map(|value| value as usize) else {
                        return InputResult::None;
                    };
                    if !selection_view.select_enabled_number(number) {
                        return InputResult::Edited;
                    }
                    let action = selection_view.accept_action();
                    let dismiss = selection_view.selected_row_dismisses_on_select();
                    if dismiss {
                        self.view_stack.pop(ViewCompletion::Accepted);
                    }
                    return action
                        .map(InputResult::SelectionPicked)
                        .unwrap_or(InputResult::Edited);
                }
                _ => return InputResult::None,
            }
        }
        if matches!(
            self.view_stack.active(),
            Some(BottomPaneViewKind::OptionPicker)
        ) {
            let Some(picker) = self.view_stack.active_as_mut::<OptionPickerState>() else {
                self.view_stack.clear();
                return InputResult::None;
            };
            match key {
                KeyEvent {
                    code: KeyCode::Esc, ..
                } => {
                    self.view_stack.pop(ViewCompletion::Cancelled);
                    return InputResult::Edited;
                }
                KeyEvent {
                    code: KeyCode::Up | KeyCode::Char('k'),
                    modifiers: KeyModifiers::NONE,
                    ..
                }
                | KeyEvent {
                    code: KeyCode::BackTab,
                    ..
                } => {
                    picker.move_up();
                    return InputResult::Edited;
                }
                KeyEvent {
                    code: KeyCode::Down | KeyCode::Char('j'),
                    modifiers: KeyModifiers::NONE,
                    ..
                } => {
                    picker.move_down();
                    return InputResult::Edited;
                }
                KeyEvent {
                    code: KeyCode::PageUp,
                    modifiers: KeyModifiers::NONE,
                    ..
                } => {
                    picker.page_up();
                    return InputResult::Edited;
                }
                KeyEvent {
                    code: KeyCode::PageDown,
                    modifiers: KeyModifiers::NONE,
                    ..
                } => {
                    picker.page_down();
                    return InputResult::Edited;
                }
                KeyEvent {
                    code: KeyCode::Home,
                    modifiers: KeyModifiers::NONE,
                    ..
                } => {
                    picker.jump_top();
                    return InputResult::Edited;
                }
                KeyEvent {
                    code: KeyCode::End,
                    modifiers: KeyModifiers::NONE,
                    ..
                } => {
                    picker.jump_bottom();
                    return InputResult::Edited;
                }
                KeyEvent {
                    code: KeyCode::Tab, ..
                }
                | KeyEvent {
                    code: KeyCode::Right,
                    ..
                } => {
                    let submission = picker.submission_text();
                    self.view_stack.pop(ViewCompletion::Accepted);
                    return submission
                        .map(InputResult::OptionPicked)
                        .unwrap_or(InputResult::Edited);
                }
                KeyEvent {
                    code: KeyCode::Enter,
                    modifiers,
                    ..
                } if modifiers.is_empty() => {
                    let submission = picker.submission_text();
                    self.view_stack.pop(ViewCompletion::Accepted);
                    return submission
                        .map(InputResult::OptionPicked)
                        .unwrap_or(InputResult::Edited);
                }
                KeyEvent {
                    code: KeyCode::Char(ch @ '1'..='9'),
                    modifiers,
                    ..
                } if modifiers.is_empty() => {
                    let idx = (ch as usize) - ('1' as usize);
                    if idx < picker.options.len() {
                        picker.select_index(idx);
                        let submission = picker.submission_text();
                        self.view_stack.pop(ViewCompletion::Accepted);
                        return submission
                            .map(InputResult::OptionPicked)
                            .unwrap_or(InputResult::Edited);
                    }
                    return InputResult::None;
                }
                _ => return InputResult::None,
            }
        }
        if self.active_human_is_question() {
            if self.active_human_cancel_requested() {
                return InputResult::None;
            }
            return self.handle_human_question_key(key);
        }
        match key {
            KeyEvent {
                code: KeyCode::BackTab,
                ..
            } if !self.interaction_view_active() && self.slash_visible() => {
                self.composer.flush_paste_burst();
                self.sync_completion_popups();
                self.select_previous_slash();
                InputResult::Edited
            }
            KeyEvent {
                code: KeyCode::Esc, ..
            } if !self.interaction_view_active() && self.file_visible() => {
                let token = self.composer.file_token();
                self.file_popup.dismiss(token.as_deref());
                InputResult::Edited
            }
            KeyEvent {
                code: KeyCode::Esc, ..
            } if !self.interaction_view_active() && self.skill_visible() => {
                self.skill_popup.dismiss(self.composer.text());
                InputResult::Edited
            }
            KeyEvent {
                code: KeyCode::Tab,
                modifiers,
                ..
            } if modifiers.is_empty() && !self.interaction_view_active() => {
                self.composer.flush_paste_burst();
                self.sync_completion_popups();
                if self.file_visible() {
                    if let Some(path) = self.file_popup.selected_path() {
                        self.composer.insert_file_completion(&path);
                        self.sync_completion_popups();
                    }
                    return InputResult::Edited;
                }
                if self.skill_visible() {
                    if let Some(insert_text) = self.skill_popup.selected_insert_text() {
                        self.composer.set_text(insert_text);
                        self.sync_completion_popups();
                    }
                    return InputResult::Edited;
                }
                if self.slash_visible() {
                    if self.complete_selected_slash().is_some() {
                        return InputResult::Edited;
                    }
                    return InputResult::None;
                }
                InputResult::Edited
            }
            KeyEvent {
                code: KeyCode::Up,
                modifiers,
                ..
            } if modifiers.is_empty() && !self.interaction_view_active() && self.file_visible() => {
                self.file_popup.move_up();
                InputResult::Edited
            }
            KeyEvent {
                code: KeyCode::Up,
                modifiers,
                ..
            } if modifiers.is_empty()
                && !self.interaction_view_active()
                && self.skill_visible() =>
            {
                self.skill_popup.move_up();
                InputResult::Edited
            }
            KeyEvent {
                code: KeyCode::Up,
                modifiers,
                ..
            } if modifiers.is_empty()
                && !self.interaction_view_active()
                && self.slash_visible() =>
            {
                self.select_previous_slash();
                InputResult::Edited
            }
            KeyEvent {
                code: KeyCode::Down,
                modifiers,
                ..
            } if modifiers.is_empty() && !self.interaction_view_active() && self.file_visible() => {
                self.file_popup.move_down();
                InputResult::Edited
            }
            KeyEvent {
                code: KeyCode::Down,
                modifiers,
                ..
            } if modifiers.is_empty()
                && !self.interaction_view_active()
                && self.skill_visible() =>
            {
                self.skill_popup.move_down();
                InputResult::Edited
            }
            KeyEvent {
                code: KeyCode::Down,
                modifiers,
                ..
            } if modifiers.is_empty()
                && !self.interaction_view_active()
                && self.slash_visible() =>
            {
                self.select_next_slash();
                InputResult::Edited
            }
            KeyEvent {
                code: KeyCode::Enter,
                modifiers,
                ..
            } if modifiers.is_empty() => {
                self.composer.flush_paste_burst();
                self.sync_input_to_overlay();
                self.sync_completion_popups();
                if !self.interaction_view_active() && self.file_visible() {
                    if let Some(path) = self.file_popup.selected_path() {
                        self.composer.insert_file_completion(&path);
                        self.sync_completion_popups();
                        return InputResult::Edited;
                    }
                }
                if !self.interaction_view_active() && self.skill_visible() {
                    if let Some(insert_text) = self.skill_popup.selected_insert_text() {
                        self.composer.set_text(insert_text);
                        self.sync_completion_popups();
                        return InputResult::Edited;
                    }
                }
                if !self.interaction_view_active() {
                    if self.complete_selected_slash().is_some() {
                        return self.classify_current_submission();
                    }
                }
                self.classify_current_submission()
            }
            _ => {
                let changed = self.composer.handle_key(key);
                if changed {
                    self.sync_input_to_overlay();
                    self.sync_completion_popups();
                    InputResult::Edited
                } else {
                    InputResult::None
                }
            }
        }
    }

    pub(crate) fn handle_escape(&mut self) -> EscapeAction {
        if self.resume_browser_key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE)) {
            return EscapeAction::Consumed;
        }
        if self
            .view_stack
            .take_active::<CommandBrowserViewState>()
            .is_some()
        {
            return EscapeAction::CloseCommandBrowser;
        }
        if self
            .view_stack
            .active_as::<SkillsToggleViewState>()
            .is_some_and(SkillsToggleViewState::has_pending_action)
        {
            return EscapeAction::Consumed;
        }
        if self
            .view_stack
            .take_active::<SkillsToggleViewState>()
            .is_some()
        {
            return EscapeAction::CloseSkills;
        }
        if self
            .view_stack
            .take_active::<CommandDocumentViewState>()
            .is_some()
        {
            return EscapeAction::CloseCommandDocument;
        }
        if let Some(selection_view) = self.view_stack.take_active::<SelectionViewState>() {
            return EscapeAction::CloseSelectionView(selection_view.cancel_action);
        }
        if self.view_stack.take_active::<OptionPickerState>().is_some() {
            return EscapeAction::CloseOptionPicker;
        }
        let mut draft_to_restore = None;
        if let Some(view) = self.human_question_view_mut() {
            let interaction = view.human_mut();
            if interaction.confirm_unanswered {
                interaction.close_unanswered_confirmation_and_go_back();
                draft_to_restore = Some(interaction.load_input_for_current_question());
            } else if interaction.current_question_has_options()
                && interaction.notes_visible_for_current_question()
            {
                interaction.hide_notes_for_current_question();
                draft_to_restore = Some(interaction.load_input_for_current_question());
            } else {
                return EscapeAction::InterruptHumanInteraction;
            }
        }
        if let Some(overlay) = self.approval_overlay_mut() {
            let option_id = overlay.permission_cancel_option_id();
            return EscapeAction::CancelPermission { option_id };
        }
        if let Some(draft) = draft_to_restore {
            self.restore_composer_state(draft);
            return EscapeAction::Consumed;
        }
        if self.slash_visible() {
            self.dismissed_slash_input = Some(self.composer.text().to_string());
            return EscapeAction::CloseSlashPopup;
        }
        if self.file_visible() {
            let token = self.composer.file_token();
            self.file_popup.dismiss(token.as_deref());
            return EscapeAction::CloseSlashPopup;
        }
        if self.skill_visible() {
            self.skill_popup.dismiss(self.composer.text());
            return EscapeAction::CloseSlashPopup;
        }
        EscapeAction::None
    }

    pub(crate) fn pending_interaction(&self) -> Option<ActiveInteractionRef<'_>> {
        if let Some(view) = self.human_question_view() {
            return Some(ActiveInteractionRef::Human(&view.interaction));
        }
        self.approval_overlay()
            .map(|view| ActiveInteractionRef::Permission(view.permission()))
    }

    pub(crate) fn pending_interaction_mut(&mut self) -> Option<ActiveInteractionMut<'_>> {
        if self.human_question_view().is_some() {
            self.human_question_view_mut()
                .map(|view| ActiveInteractionMut::Human(&mut view.interaction))
        } else {
            self.approval_overlay_mut()
                .map(|view| ActiveInteractionMut::Permission(view.permission_mut()))
        }
    }

    fn handle_human_question_key(&mut self, key: KeyEvent) -> InputResult {
        let Some(mut view) = self.view_stack.take_active::<HumanQuestionViewState>() else {
            return InputResult::None;
        };
        let result = Self::handle_human_question_key_inner(key, &mut view);
        self.view_stack.replace(view);
        result
    }

    fn handle_human_question_key_inner(
        key: KeyEvent,
        view: &mut HumanQuestionViewState,
    ) -> InputResult {
        let interaction = view.human_mut();
        interaction.ensure_focus_available();

        if interaction.confirm_unanswered {
            match key {
                KeyEvent {
                    code: KeyCode::Up | KeyCode::Down | KeyCode::Char('k') | KeyCode::Char('j'),
                    modifiers: KeyModifiers::NONE,
                    ..
                } => {
                    interaction.confirm_unanswered_selection =
                        1usize.saturating_sub(interaction.confirm_unanswered_selection.min(1));
                }
                KeyEvent {
                    code: KeyCode::Backspace,
                    modifiers: KeyModifiers::NONE,
                    ..
                } => {
                    interaction.close_unanswered_confirmation_and_go_back();
                    view.restore_current_draft();
                }
                KeyEvent {
                    code: KeyCode::Char('1'),
                    modifiers: KeyModifiers::NONE,
                    ..
                } => interaction.confirm_unanswered_selection = 0,
                KeyEvent {
                    code: KeyCode::Char('2'),
                    modifiers: KeyModifiers::NONE,
                    ..
                } => interaction.confirm_unanswered_selection = 1,
                _ => {}
            }
            return InputResult::Edited;
        }

        match key {
            KeyEvent {
                code: KeyCode::Char('p'),
                modifiers: KeyModifiers::CONTROL,
                ..
            }
            | KeyEvent {
                code: KeyCode::PageUp,
                modifiers: KeyModifiers::NONE,
                ..
            }
            | KeyEvent {
                code: KeyCode::BackTab,
                ..
            } => {
                view.move_question(false);
                return InputResult::Edited;
            }
            KeyEvent {
                code: KeyCode::Char('n'),
                modifiers: KeyModifiers::CONTROL,
                ..
            }
            | KeyEvent {
                code: KeyCode::PageDown,
                modifiers: KeyModifiers::NONE,
                ..
            } => {
                view.move_question(true);
                return InputResult::Edited;
            }
            _ => {}
        }

        let has_options = view.human().current_question_has_options();
        let focus_options = matches!(view.human().focus, HumanQuestionFocus::Options);
        if has_options {
            match key.code {
                KeyCode::Up if key.modifiers == KeyModifiers::NONE => {
                    view.human_mut().move_option_cursor_up();
                    return InputResult::Edited;
                }
                KeyCode::Down if key.modifiers == KeyModifiers::NONE => {
                    view.human_mut().move_option_cursor_down();
                    return InputResult::Edited;
                }
                KeyCode::Char('k') if key.modifiers == KeyModifiers::NONE && focus_options => {
                    view.human_mut().move_option_cursor_up();
                    return InputResult::Edited;
                }
                KeyCode::Char('j') if key.modifiers == KeyModifiers::NONE && focus_options => {
                    view.human_mut().move_option_cursor_down();
                    return InputResult::Edited;
                }
                KeyCode::Left | KeyCode::Char('h')
                    if key.modifiers == KeyModifiers::NONE
                        && matches!(view.human().focus, HumanQuestionFocus::Options) =>
                {
                    view.move_question(false);
                    return InputResult::Edited;
                }
                KeyCode::Right | KeyCode::Char('l')
                    if key.modifiers == KeyModifiers::NONE
                        && matches!(view.human().focus, HumanQuestionFocus::Options) =>
                {
                    view.move_question(true);
                    return InputResult::Edited;
                }
                KeyCode::Tab => {
                    let interaction = view.human_mut();
                    if interaction.notes_visible_for_current_question() {
                        interaction.hide_notes_for_current_question();
                    } else {
                        if interaction.current_question_has_options()
                            && !interaction.current_question_has_selected_option()
                        {
                            interaction.select_current_option();
                        }
                        interaction.show_notes_for_current_question();
                    }
                    view.restore_current_draft();
                    return InputResult::Edited;
                }
                KeyCode::Char(ch)
                    if ch.is_ascii_digit()
                        && focus_options
                        && (key.modifiers.is_empty() || key.modifiers == KeyModifiers::SHIFT) =>
                {
                    let option_index = ch
                        .to_digit(10)
                        .and_then(|digit| digit.checked_sub(1))
                        .map(|digit| digit as usize)
                        .filter(|index| view.human().current_question_has_option(*index));
                    if let Some(option_index) = option_index {
                        let (multi_select, other) = view
                            .human()
                            .current_question()
                            .map(|question| {
                                (
                                    question.multi_select,
                                    option_index == question.options.len() && question.is_other,
                                )
                            })
                            .unwrap_or((false, false));
                        let interaction = view.human_mut();
                        interaction.set_option_cursor(option_index);
                        interaction.select_current_option();
                        if other {
                            interaction.show_notes_for_current_question();
                        }
                        if other {
                            view.restore_current_draft();
                            return InputResult::Edited;
                        }
                        return if multi_select {
                            InputResult::Edited
                        } else {
                            InputResult::HumanQuestionAdvanceOrSubmit
                        };
                    }
                }
                KeyCode::Char(' ') if key.modifiers.is_empty() && focus_options => {
                    view.human_mut().select_current_option();
                    return InputResult::Edited;
                }
                KeyCode::Backspace | KeyCode::Delete
                    if key.modifiers == KeyModifiers::NONE && focus_options =>
                {
                    view.human_mut().clear_current_option_answer();
                    view.restore_current_draft();
                    return InputResult::Edited;
                }
                KeyCode::Char(_)
                    if !view.human().notes_visible_for_current_question()
                        && matches!(view.human().focus, HumanQuestionFocus::Options) =>
                {
                    return InputResult::None;
                }
                KeyCode::Backspace | KeyCode::Delete
                    if view.human().notes_visible_for_current_question()
                        && view
                            .composer()
                            .current_text_with_pending()
                            .trim()
                            .is_empty() =>
                {
                    view.human_mut().hide_notes_for_current_question();
                    view.restore_current_draft();
                    return InputResult::Edited;
                }
                _ => {}
            }
        }

        if matches!(view.human().focus, HumanQuestionFocus::Notes) {
            let changed = view.composer_mut().handle_key(key);
            if changed {
                view.save_current_draft();
                return InputResult::Edited;
            }
        }
        InputResult::None
    }

    fn sync_completion_popups(&mut self) {
        if self.dismissed_slash_input.as_deref() != Some(self.composer.text()) {
            self.dismissed_slash_input = None;
        }
        self.command_popup.on_composer_change(self.composer.text());
        self.skill_popup.on_composer_change(self.composer.text());
        self.file_popup
            .on_token_change(self.composer.file_token().as_deref());
    }
}

impl Default for BottomPaneState {
    fn default() -> Self {
        Self::default_with_commands()
    }
}

#[cfg(test)]
mod tests;

#[cfg(test)]
use interaction_types::HumanInteractionKind;
