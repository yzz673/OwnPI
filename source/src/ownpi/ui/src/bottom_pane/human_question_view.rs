use super::{ChatComposer, ComposerDraftSnapshot, HumanInteractionState};

/// Codex `RequestUserInputOverlay` parity: human_question gets a dedicated view
/// state machine instead of sharing the generic approval overlay container.
/// The view owns both its interaction state and a plain, isolated composer so
/// question input cannot overwrite the user's pending chat draft.
#[derive(Debug)]
pub(crate) struct HumanQuestionViewState {
    pub(crate) interaction: HumanInteractionState,
    composer: ChatComposer,
}

impl HumanQuestionViewState {
    pub(crate) fn new(interaction: HumanInteractionState) -> Self {
        let initial_draft = interaction.load_input_for_current_question();
        let mut composer = ChatComposer::default();
        composer.set_text_with_pending(
            initial_draft.text,
            initial_draft.text_elements,
            initial_draft.pending_pastes,
        );
        Self {
            interaction,
            composer,
        }
    }

    pub(crate) fn human(&self) -> &HumanInteractionState {
        &self.interaction
    }

    pub(crate) fn human_mut(&mut self) -> &mut HumanInteractionState {
        &mut self.interaction
    }

    pub(crate) fn composer(&self) -> &ChatComposer {
        &self.composer
    }

    pub(crate) fn composer_mut(&mut self) -> &mut ChatComposer {
        &mut self.composer
    }

    pub(crate) fn save_current_draft(&mut self) {
        self.composer.flush_paste_burst();
        let text = self.composer.current_text_with_pending();
        let draft = ComposerDraftSnapshot {
            text: self.composer.text().to_string(),
            text_elements: self.composer.text_elements(),
            pending_pastes: self.composer.pending_pastes(),
        };
        self.human_mut()
            .sync_input_into_current_question(&text, draft);
    }

    pub(crate) fn restore_current_draft(&mut self) {
        let draft = self.human().load_input_for_current_question();
        self.composer
            .set_text_with_pending(draft.text, draft.text_elements, draft.pending_pastes);
    }

    pub(crate) fn move_question(&mut self, next: bool) {
        self.save_current_draft();
        let interaction = self.human_mut();
        if next {
            interaction.next_question();
        } else {
            interaction.previous_question();
        }
        self.restore_current_draft();
    }

    pub(crate) fn flush_pending_input_if_due(&mut self) -> bool {
        let changed = self.composer.flush_paste_burst_if_due();
        if changed {
            self.save_current_draft();
        }
        changed
    }

    pub(crate) fn is_in_paste_burst(&self) -> bool {
        self.composer.is_in_paste_burst()
    }
}
