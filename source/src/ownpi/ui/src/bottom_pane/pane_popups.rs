//! Completion popup management for `BottomPaneState`.
//!
//! Slash-command, file (`@`), and skill (`$`) popup visibility, selection,
//! scroll metrics, rendering, and catalog/file-search wiring.
use super::*;
impl BottomPaneState {
    pub(crate) fn slash_visible(&self) -> bool {
        self.view_stack.is_empty()
            && self.dismissed_slash_input.as_deref() != Some(self.composer.text())
            && self.command_popup.is_visible(self.composer.text())
    }

    pub(crate) fn file_visible(&self) -> bool {
        self.view_stack.is_empty()
            && !self.slash_visible()
            && !self.skill_visible()
            && self
                .file_popup
                .is_visible(self.composer.file_token().as_deref())
    }

    pub(crate) fn skill_visible(&self) -> bool {
        self.view_stack.is_empty()
            && !self.slash_visible()
            && self.skill_popup.is_visible(self.composer.text())
    }

    pub(crate) fn active_popup_height(&self, width: u16) -> u16 {
        if self.slash_visible() {
            self.command_popup.required_height(width)
        } else if self.skill_visible() {
            self.skill_popup.required_height(width)
        } else if self.file_visible() {
            self.file_popup.required_height(width)
        } else {
            0
        }
    }

    #[cfg(test)]
    pub(crate) fn slash_popup_scroll_metrics(&self) -> Option<(usize, usize, usize)> {
        self.slash_visible().then(|| {
            (
                self.command_popup.filtered_len(),
                self.command_popup.visible_rows(),
                self.command_popup.scroll_top(),
            )
        })
    }

    #[cfg(test)]
    pub(crate) fn file_popup_scroll_metrics(&self) -> Option<(usize, usize, usize)> {
        self.file_visible().then(|| {
            (
                self.file_popup.filtered_len(),
                self.file_popup.visible_rows(),
                self.file_popup.scroll_top(),
            )
        })
    }

    #[cfg(test)]
    pub(crate) fn skill_popup_scroll_metrics(&self) -> Option<(usize, usize, usize)> {
        self.skill_visible().then(|| {
            (
                self.skill_popup.filtered_len(),
                self.skill_popup.visible_rows(),
                self.skill_popup.scroll_top(),
            )
        })
    }

    #[cfg(test)]
    pub(crate) fn selected_skill_name(&self) -> Option<String> {
        self.skill_visible()
            .then(|| self.skill_popup.selected_item().map(|item| item.name))
            .flatten()
    }

    #[cfg(test)]
    pub(crate) fn selected_file_path(&self) -> Option<String> {
        self.file_visible()
            .then(|| self.file_popup.selected_path())
            .flatten()
    }

    #[cfg(test)]
    pub(crate) fn file_popup_is_waiting(&self) -> bool {
        self.file_visible() && self.file_popup.is_waiting()
    }

    #[cfg(test)]
    pub(crate) fn file_popup_is_empty_prompt(&self) -> bool {
        self.file_visible() && self.file_popup.is_empty_prompt()
    }

    #[cfg(test)]
    pub(crate) fn selected_slash_command(&self) -> Option<SlashCommandItem> {
        self.slash_visible()
            .then(|| self.command_popup.selected_item())
            .flatten()
    }

    pub(crate) fn select_next_slash(&mut self) {
        self.command_popup.move_down();
    }

    pub(crate) fn select_previous_slash(&mut self) {
        self.command_popup.move_up();
    }

    #[cfg(test)]
    pub(crate) fn selected_slash_command_text(&self) -> Option<String> {
        self.selected_slash_command()
            .map(|command| command.display_name())
    }

    pub(crate) fn complete_selected_slash(&mut self) -> Option<String> {
        if !self.slash_visible() {
            return None;
        }
        let completed = self
            .command_popup
            .selected_item()
            .map(|item| format!("/{} ", item.command()))?;
        self.set_composer(completed.clone());
        Some(completed)
    }

    pub(crate) fn render_active_popup(
        &self,
        area: Rect,
        buf: &mut ratatui::buffer::Buffer,
        theme: &ThemePalette,
    ) {
        if self.slash_visible() {
            self.command_popup.render(area, buf, theme);
        } else if self.skill_visible() {
            self.skill_popup.render(area, buf, theme);
        } else if self.file_visible() {
            self.file_popup.render(area, buf, theme);
        }
    }

    pub(crate) fn set_command_catalog(&mut self, commands: Vec<ServiceTierCommand>) {
        self.command_popup = CommandPopupState::new(commands_for_input(&commands));
        self.sync_completion_popups();
    }

    pub(crate) fn set_skill_catalog(
        &mut self,
        skills: Vec<crate::bridge::protocol::SkillCatalogEntry>,
    ) {
        if let Some(view) = self.view_stack.active_as_mut::<SkillsToggleViewState>() {
            view.refresh(skills.clone());
        }
        self.skill_popup.set_skills(skills);
        self.sync_completion_popups();
    }

    pub(crate) fn set_file_search_root(&mut self, root: Option<String>) {
        self.file_popup.set_root(root);
        self.sync_completion_popups();
    }

    pub(crate) fn take_file_search_request(&mut self) -> Option<String> {
        self.file_popup.take_search_request()
    }

    pub(crate) fn apply_file_search_result(
        &mut self,
        query: &str,
        matches: Vec<FileMatch>,
    ) -> bool {
        let Some(token) = self.composer.file_token() else {
            return false;
        };
        if !token.starts_with(query) {
            return false;
        }
        self.file_popup.apply_search_result(query, matches)
    }
}
