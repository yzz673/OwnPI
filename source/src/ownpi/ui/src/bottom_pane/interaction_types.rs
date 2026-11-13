//! Clarification, permission, and human-interaction data types for the bottom pane.
//!
//! Pure data plus small per-question draft/cursor state helpers. Rendering lives in
//! `human_question_layout` and the overlay widgets; `BottomPaneState`-level transitions
//! live in `pane_interaction`.
use super::*;
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct SlashCommandSubmission {
    pub(crate) text: String,
    pub(crate) command: SlashCommandItem,
    pub(crate) args: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum InputResult {
    None,
    Edited,
    SubmittedText(String),
    UnknownSlash(String),
    Command(SlashCommandSubmission),
    CommandWithArgs(SlashCommandSubmission),
    OptionPicked(String),
    SelectionPicked(SelectionAction),
    CommandAction(Value),
    ComposerReplace(String),
    HumanQuestionAdvanceOrSubmit,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum EscapeAction {
    None,
    Consumed,
    CloseSelectionView(Option<SelectionAction>),
    CloseOptionPicker,
    CloseCommandDocument,
    CloseCommandBrowser,
    CloseSkills,
    CloseSlashPopup,
    InterruptHumanInteraction,
    CancelPermission { option_id: Option<String> },
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct PendingOption {
    pub(crate) id: String,
    pub(crate) label: String,
    pub(crate) description: String,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct ClarificationQuestion {
    pub(crate) question_id: String,
    pub(crate) header: String,
    pub(crate) question: String,
    pub(crate) required: bool,
    pub(crate) multi_select: bool,
    pub(crate) options: Vec<PendingOption>,
    pub(crate) is_other: bool,
    pub(crate) is_secret: bool,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct ClarificationDraft {
    pub(crate) selected_labels: Vec<String>,
    pub(crate) free_text: String,
    pub(crate) text: String,
    pub(crate) text_elements: Vec<TextElementSnapshot>,
    pub(crate) pending_pastes: Vec<(String, String)>,
    pub(crate) answer_committed: bool,
    pub(crate) notes_visible: bool,
}

impl ClarificationDraft {
    pub(crate) fn has_value(&self) -> bool {
        !self.selected_labels.is_empty() || !self.free_text.trim().is_empty()
    }

    pub(crate) fn to_json(&self) -> Value {
        json!({
            "selected_labels": self.selected_labels,
            "free_text": if self.free_text.trim().is_empty() {
                Value::Null
            } else {
                Value::String(self.free_text.trim().to_string())
            },
        })
    }
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct PermissionFileChange {
    pub(crate) path: String,
    pub(crate) destination: String,
    pub(crate) change_kind: String,
    pub(crate) added_lines: Option<u64>,
    pub(crate) removed_lines: Option<u64>,
    pub(crate) size_bytes: Option<u64>,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) enum PermissionSubmission {
    #[default]
    Ready,
    Submitting {
        started: std::time::Instant,
    },
}
impl PermissionSubmission {
    pub(crate) fn blocked(&self) -> bool {
        !matches!(self, Self::Ready)
    }
    pub(crate) fn label(&self) -> String {
        match self {
            Self::Ready => "Press enter to confirm or esc to deny".into(),
            Self::Submitting { started } => {
                format!("Submitting… ({}s)", started.elapsed().as_secs())
            }
        }
    }
    pub(crate) fn set_requested(&mut self, requested: bool) {
        *self = if requested {
            Self::Submitting {
                started: std::time::Instant::now(),
            }
        } else {
            Self::Ready
        };
    }
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct PermissionInteractionState {
    pub(crate) role_label: String,
    pub(crate) stage_label: String,
    pub(crate) remember_scope: String,
    pub(crate) saved_options: Vec<PendingOption>,

    pub(crate) interaction_id: String,
    pub(crate) turn_id: String,
    pub(crate) run_dir: String,
    pub(crate) approval_kind: String,
    pub(crate) call_id: String,
    pub(crate) approval_id: String,
    pub(crate) environment_id: String,
    pub(crate) cwd: String,
    pub(crate) reason: String,
    pub(crate) command: String,
    pub(crate) tool_name: String,
    pub(crate) request_summary: String,
    pub(crate) operation_details: String,
    pub(crate) file_changes: Vec<PermissionFileChange>,
    pub(crate) permission_class: String,
    pub(crate) permission_rule: String,
    pub(crate) network_host: String,
    pub(crate) title: String,
    pub(crate) body_markdown: String,
    pub(crate) options: Vec<PendingOption>,
    pub(crate) submission: PermissionSubmission,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub(crate) enum HumanInteractionKind {
    HumanQuestion,
    #[default]
    HumanClarification,
    RequestUserInput,
    PlanApproval,
}

impl HumanInteractionKind {
    #[cfg(test)]
    pub(crate) fn as_wire_str(self) -> &'static str {
        match self {
            Self::HumanQuestion => "human_question",
            Self::HumanClarification => "human_clarification",
            Self::RequestUserInput => "request_user_input",
            Self::PlanApproval => "plan_approval",
        }
    }
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct HumanInteractionState {
    pub(crate) interaction_id: String,
    pub(crate) turn_id: String,
    pub(crate) run_dir: String,
    pub(crate) title: String,
    pub(crate) body_markdown: String,
    pub(crate) plan_markdown: Option<String>,
    pub(crate) questions: Vec<ClarificationQuestion>,
    pub(crate) drafts_by_question_id: BTreeMap<String, ClarificationDraft>,
    pub(crate) current_question_index: usize,
    pub(crate) interaction_kind: HumanInteractionKind,
    pub(crate) focus: HumanQuestionFocus,
    pub(crate) option_cursor_by_question_id: BTreeMap<String, usize>,
    pub(crate) option_scroll_top_by_question_id: BTreeMap<String, usize>,
    pub(crate) confirm_unanswered: bool,
    pub(crate) confirm_unanswered_selection: usize,
    pub(crate) cancel_requested: bool,
    pub(crate) submit_requested: bool,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub(crate) enum HumanQuestionFocus {
    #[default]
    Options,
    Notes,
}

impl HumanInteractionState {
    pub(crate) fn is_plan_approval(&self) -> bool {
        self.interaction_kind == HumanInteractionKind::PlanApproval
    }

    pub(crate) fn current_question(&self) -> Option<&ClarificationQuestion> {
        self.questions.get(self.current_question_index)
    }

    fn draft_for_mut(&mut self, question_id: &str) -> &mut ClarificationDraft {
        self.drafts_by_question_id
            .entry(question_id.to_string())
            .or_default()
    }

    pub(crate) fn draft_for(&self, question_id: &str) -> ClarificationDraft {
        self.drafts_by_question_id
            .get(question_id)
            .cloned()
            .unwrap_or_default()
    }

    pub(crate) fn sync_input_into_current_question(
        &mut self,
        input: &str,
        draft_snapshot: ComposerDraftSnapshot,
    ) {
        if let Some(question) = self.current_question() {
            let question_id = question.question_id.clone();
            let draft = self.draft_for_mut(&question_id);
            draft.free_text = input.to_string();
            draft.text = draft_snapshot.text;
            draft.text_elements = draft_snapshot.text_elements;
            draft.pending_pastes = draft_snapshot.pending_pastes;
            if !draft.free_text.trim().is_empty() {
                draft.notes_visible = true;
            }
        }
    }

    pub(crate) fn load_input_for_current_question(&self) -> ComposerDraftSnapshot {
        self.current_question()
            .map(|question| {
                let draft = self.draft_for(&question.question_id);
                ComposerDraftSnapshot {
                    text: if draft.text.is_empty() {
                        draft.free_text
                    } else {
                        draft.text
                    },
                    text_elements: draft.text_elements,
                    pending_pastes: draft.pending_pastes,
                }
            })
            .unwrap_or_default()
    }

    pub(crate) fn next_question(&mut self) {
        self.confirm_unanswered = false;
        self.confirm_unanswered_selection = 0;
        if self.questions.is_empty() {
            self.current_question_index = 0;
            return;
        }
        self.current_question_index = (self.current_question_index + 1) % self.questions.len();
        self.ensure_focus_available();
    }

    pub(crate) fn previous_question(&mut self) {
        self.confirm_unanswered = false;
        self.confirm_unanswered_selection = 0;
        if self.questions.is_empty() {
            self.current_question_index = 0;
            return;
        }
        self.current_question_index = if self.current_question_index == 0 {
            self.questions.len() - 1
        } else {
            self.current_question_index - 1
        };
        self.ensure_focus_available();
    }

    pub(crate) fn toggle_option(&mut self, option_index: usize) {
        let Some(question) = self.current_question() else {
            return;
        };
        let Some(option_label) = question.option_label_at(option_index).map(str::to_string) else {
            return;
        };
        let question_id = question.question_id.clone();
        let multi_select = question.multi_select;
        let draft = self.draft_for_mut(&question_id);
        if multi_select {
            if option_label == HUMAN_OTHER_OPTION_LABEL {
                if let Some(existing) = draft
                    .selected_labels
                    .iter()
                    .position(|item| item.eq_ignore_ascii_case(&option_label))
                {
                    draft.selected_labels.remove(existing);
                } else {
                    draft.selected_labels = vec![option_label];
                }
                return;
            }
            if let Some(existing) = draft
                .selected_labels
                .iter()
                .position(|item| item.eq_ignore_ascii_case(&option_label))
            {
                draft.selected_labels.remove(existing);
            } else {
                draft
                    .selected_labels
                    .retain(|item| item != HUMAN_OTHER_OPTION_LABEL);
                draft.selected_labels.push(option_label);
            }
            return;
        }
        draft.selected_labels = vec![option_label];
        draft.answer_committed = true;
    }

    pub(crate) fn current_question_has_option(&self, option_index: usize) -> bool {
        self.current_question()
            .is_some_and(|question| option_index < question.effective_option_count())
    }

    pub(crate) fn current_option_is_other(&self) -> bool {
        self.current_question().is_some_and(|question| {
            self.option_cursor() == question.options.len()
                && question.is_other
                && !question.options.is_empty()
        })
    }

    pub(crate) fn current_question_has_options(&self) -> bool {
        self.current_question()
            .is_some_and(|question| !question.options.is_empty())
    }

    pub(crate) fn current_question_has_selected_option(&self) -> bool {
        self.current_question().is_some_and(|question| {
            !self
                .draft_for(&question.question_id)
                .selected_labels
                .is_empty()
        })
    }

    pub(crate) fn current_question_id(&self) -> Option<String> {
        self.current_question()
            .map(|question| question.question_id.clone())
    }

    pub(crate) fn option_cursor(&self) -> usize {
        let Some(question) = self.current_question() else {
            return 0;
        };
        self.option_cursor_by_question_id
            .get(&question.question_id)
            .copied()
            .unwrap_or(0)
            .min(question.effective_option_count().saturating_sub(1))
    }

    pub(crate) fn set_option_cursor(&mut self, option_index: usize) {
        let Some(question) = self.current_question() else {
            return;
        };
        let len = question.effective_option_count();
        if len == 0 {
            return;
        }
        let question_id = question.question_id.clone();
        let selected = option_index.min(len.saturating_sub(1));
        self.option_cursor_by_question_id
            .insert(question_id.clone(), selected);
        let scroll_top = self
            .option_scroll_top_by_question_id
            .entry(question_id)
            .or_default();
        if selected < *scroll_top {
            *scroll_top = selected;
        } else if selected >= scroll_top.saturating_add(MAX_POPUP_ROWS) {
            *scroll_top = selected + 1 - MAX_POPUP_ROWS;
        }
    }

    pub(crate) fn option_scroll_top(&self) -> usize {
        self.current_question()
            .and_then(|question| {
                self.option_scroll_top_by_question_id
                    .get(&question.question_id)
            })
            .copied()
            .unwrap_or(0)
    }

    pub(crate) fn move_option_cursor_up(&mut self) {
        let Some(question) = self.current_question() else {
            return;
        };
        let len = question.effective_option_count();
        if len == 0 {
            return;
        }
        let current = self.option_cursor();
        self.set_option_cursor(if current == 0 { len - 1 } else { current - 1 });
    }

    pub(crate) fn move_option_cursor_down(&mut self) {
        let Some(question) = self.current_question() else {
            return;
        };
        let len = question.effective_option_count();
        if len == 0 {
            return;
        }
        self.set_option_cursor((self.option_cursor() + 1) % len);
    }

    pub(crate) fn select_current_option(&mut self) {
        self.toggle_option(self.option_cursor());
    }

    pub(crate) fn clear_current_option_answer(&mut self) {
        let Some(question_id) = self.current_question_id() else {
            return;
        };
        let draft = self.draft_for_mut(&question_id);
        draft.selected_labels.clear();
        draft.free_text.clear();
        draft.text.clear();
        draft.text_elements.clear();
        draft.pending_pastes.clear();
        draft.answer_committed = false;
        draft.notes_visible = false;
        self.focus = HumanQuestionFocus::Options;
    }

    pub(crate) fn notes_visible_for_current_question(&self) -> bool {
        if !self.current_question_has_options() {
            return true;
        }
        self.current_question()
            .map(|question| {
                let draft = self.draft_for(&question.question_id);
                draft.notes_visible || !draft.free_text.trim().is_empty()
            })
            .unwrap_or(false)
    }

    pub(crate) fn show_notes_for_current_question(&mut self) {
        let Some(question_id) = self.current_question_id() else {
            return;
        };
        self.draft_for_mut(&question_id).notes_visible = true;
        self.focus = HumanQuestionFocus::Notes;
    }

    pub(crate) fn hide_notes_for_current_question(&mut self) {
        let Some(question_id) = self.current_question_id() else {
            return;
        };
        let draft = self.draft_for_mut(&question_id);
        draft.notes_visible = false;
        draft.free_text.clear();
        draft.text.clear();
        draft.text_elements.clear();
        draft.pending_pastes.clear();
        draft.answer_committed = false;
        self.focus = HumanQuestionFocus::Options;
    }

    pub(crate) fn ensure_focus_available(&mut self) {
        if !self.current_question_has_options() {
            self.focus = HumanQuestionFocus::Notes;
        } else if !self.notes_visible_for_current_question() {
            self.focus = HumanQuestionFocus::Options;
        }
    }

    pub(crate) fn unanswered_count(&self) -> usize {
        self.questions
            .iter()
            .filter(|question| !self.draft_for(&question.question_id).has_value())
            .count()
    }

    pub(crate) fn optional_unanswered_count(&self) -> usize {
        self.questions
            .iter()
            .filter(|question| {
                !question.required && !self.draft_for(&question.question_id).has_value()
            })
            .count()
    }

    pub(crate) fn first_unanswered_question(&self) -> Option<usize> {
        self.questions
            .iter()
            .enumerate()
            .find_map(|(index, question)| {
                (!self.draft_for(&question.question_id).has_value()).then_some(index)
            })
    }

    pub(crate) fn first_optional_unanswered_question(&self) -> Option<usize> {
        self.questions
            .iter()
            .enumerate()
            .find_map(|(index, question)| {
                (!question.required && !self.draft_for(&question.question_id).has_value())
                    .then_some(index)
            })
    }

    pub(crate) fn close_unanswered_confirmation_and_go_back(&mut self) {
        self.confirm_unanswered = false;
        self.confirm_unanswered_selection = 0;
        if let Some(index) = self
            .first_optional_unanswered_question()
            .or_else(|| self.first_unanswered_question())
        {
            self.current_question_index = index.min(self.questions.len().saturating_sub(1));
            self.ensure_focus_available();
        }
    }

    pub(crate) fn commit_current_question(&mut self) {
        if let Some(question) = self.current_question() {
            let question_id = question.question_id.clone();
            let has_selected_option = !self.draft_for(&question_id).selected_labels.is_empty();
            if question.effective_option_count() > 0 && !has_selected_option {
                self.select_current_option();
            }
            self.draft_for_mut(&question_id).answer_committed = true;
        }
    }

    pub(crate) fn is_last_question(&self) -> bool {
        self.questions.is_empty()
            || self.current_question_index.saturating_add(1) >= self.questions.len()
    }

    pub(crate) fn can_submit(&self) -> bool {
        self.questions.iter().all(|question| {
            if !question.required {
                return true;
            }
            self.draft_for(&question.question_id).has_value()
        })
    }

    pub(crate) fn first_missing_required_question(&self) -> Option<usize> {
        self.questions
            .iter()
            .enumerate()
            .find_map(|(index, question)| {
                if question.required && !self.draft_for(&question.question_id).has_value() {
                    Some(index)
                } else {
                    None
                }
            })
    }

    pub(crate) fn answers_json(&self) -> Value {
        let mut payload = Map::new();
        for question in &self.questions {
            payload.insert(
                question.question_id.clone(),
                self.draft_for(&question.question_id).to_json(),
            );
        }
        Value::Object(payload)
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum PendingInteractionState {
    Permission(PermissionInteractionState),
    Human(HumanInteractionState),
}

#[derive(Clone, Copy, Debug)]
pub(crate) enum ActiveInteractionRef<'a> {
    Permission(&'a PermissionInteractionState),
    Human(&'a HumanInteractionState),
}

impl<'a> ActiveInteractionRef<'a> {
    pub(crate) fn interaction_id(self) -> &'a str {
        match self {
            Self::Permission(interaction) => &interaction.interaction_id,
            Self::Human(interaction) => &interaction.interaction_id,
        }
    }
}

pub(crate) enum ActiveInteractionMut<'a> {
    Permission(&'a mut PermissionInteractionState),
    Human(&'a mut HumanInteractionState),
}

impl ActiveInteractionMut<'_> {
    pub(crate) fn interaction_id(&self) -> &str {
        match self {
            Self::Permission(interaction) => &interaction.interaction_id,
            Self::Human(interaction) => &interaction.interaction_id,
        }
    }

    pub(crate) fn submit_requested(&self) -> bool {
        match self {
            Self::Permission(interaction) => interaction.submission.blocked(),
            Self::Human(interaction) => interaction.submit_requested,
        }
    }

    pub(crate) fn set_submit_requested(&mut self, requested: bool) {
        match self {
            Self::Permission(interaction) => interaction.submission.set_requested(requested),
            Self::Human(interaction) => interaction.submit_requested = requested,
        }
    }
}

impl PendingInteractionState {
    pub(crate) fn interaction_id(&self) -> &str {
        match self {
            Self::Permission(interaction) => &interaction.interaction_id,
            Self::Human(interaction) => &interaction.interaction_id,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum HumanOverlaySubmitAction {
    None,
    Advanced,
    Submit {
        interaction_id: String,
        answers_json: Value,
    },
}
