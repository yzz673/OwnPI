//! Shared selection and viewport state for bottom-pane lists.
//!
//! Mirrors the latest Codex list contract: single-row navigation can wrap,
//! page/jump navigation clamps, and every mutation can keep the selection in
//! the visible window without the renderer reconstructing scroll state.

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub(crate) struct ScrollState {
    pub(crate) selected_idx: Option<usize>,
    pub(crate) scroll_top: usize,
}

impl ScrollState {
    pub(crate) fn reset(&mut self) {
        self.selected_idx = None;
        self.scroll_top = 0;
    }

    pub(crate) fn clamp_selection(&mut self, len: usize) {
        if self.clear_if_empty(len) {
            return;
        }
        self.selected_idx = Some(self.selected_idx.unwrap_or(0).min(len - 1));
    }

    pub(crate) fn move_up(&mut self, len: usize) {
        if self.clear_if_empty(len) {
            return;
        }
        let current = self.selected_idx.unwrap_or(0).min(len - 1);
        self.selected_idx = Some(current.saturating_sub(1));
    }

    pub(crate) fn move_down(&mut self, len: usize) {
        if self.clear_if_empty(len) {
            return;
        }
        let current = self.selected_idx.unwrap_or(0).min(len - 1);
        self.selected_idx = Some(current.saturating_add(1).min(len - 1));
    }

    pub(crate) fn move_up_wrap(&mut self, len: usize) {
        if self.clear_if_empty(len) {
            return;
        }
        self.selected_idx = Some(match self.selected_idx {
            Some(index) if index > 0 => index - 1,
            Some(_) => len - 1,
            None => 0,
        });
    }

    pub(crate) fn move_down_wrap(&mut self, len: usize) {
        if self.clear_if_empty(len) {
            return;
        }
        self.selected_idx = Some(match self.selected_idx {
            Some(index) if index + 1 < len => index + 1,
            _ => 0,
        });
    }

    pub(crate) fn page_up_clamped(&mut self, len: usize, visible_rows: usize) {
        if self.clear_if_empty(len) {
            return;
        }
        let current = self.selected_idx.unwrap_or(0).min(len - 1);
        self.selected_idx = Some(current.saturating_sub(visible_rows.max(1)));
        self.ensure_visible(len, visible_rows);
    }

    pub(crate) fn page_down_clamped(&mut self, len: usize, visible_rows: usize) {
        if self.clear_if_empty(len) {
            return;
        }
        let current = self.selected_idx.unwrap_or(0).min(len - 1);
        self.selected_idx = Some(current.saturating_add(visible_rows.max(1)).min(len - 1));
        self.ensure_visible(len, visible_rows);
    }

    pub(crate) fn jump_top(&mut self, len: usize, visible_rows: usize) {
        if self.clear_if_empty(len) {
            return;
        }
        self.selected_idx = Some(0);
        self.ensure_visible(len, visible_rows);
    }

    pub(crate) fn jump_bottom(&mut self, len: usize, visible_rows: usize) {
        if self.clear_if_empty(len) {
            return;
        }
        self.selected_idx = Some(len - 1);
        self.ensure_visible(len, visible_rows);
    }

    pub(crate) fn ensure_visible(&mut self, len: usize, visible_rows: usize) {
        if len == 0 || visible_rows == 0 {
            self.scroll_top = 0;
            return;
        }
        let visible_rows = visible_rows.min(len);
        let selected = self.selected_idx.unwrap_or(0).min(len - 1);
        if selected < self.scroll_top {
            self.scroll_top = selected;
        } else if selected >= self.scroll_top.saturating_add(visible_rows) {
            self.scroll_top = selected + 1 - visible_rows;
        }
        self.scroll_top = self.scroll_top.min(len.saturating_sub(visible_rows));
    }

    fn clear_if_empty(&mut self, len: usize) -> bool {
        if len > 0 {
            return false;
        }
        self.reset();
        true
    }
}

#[cfg(test)]
mod tests {
    use super::ScrollState;

    #[test]
    fn row_navigation_wraps_and_keeps_selection_visible() {
        let mut state = ScrollState::default();
        state.clamp_selection(10);
        state.move_up_wrap(10);
        state.ensure_visible(10, 4);
        assert_eq!(state.selected_idx, Some(9));
        assert_eq!(state.scroll_top, 6);

        state.move_down_wrap(10);
        state.ensure_visible(10, 4);
        assert_eq!(state.selected_idx, Some(0));
        assert_eq!(state.scroll_top, 0);
    }

    #[test]
    fn page_and_jump_navigation_clamp_at_edges() {
        let mut state = ScrollState::default();
        state.clamp_selection(10);
        state.page_down_clamped(10, 4);
        state.page_down_clamped(10, 4);
        state.page_down_clamped(10, 4);
        assert_eq!(state.selected_idx, Some(9));
        assert_eq!(state.scroll_top, 6);

        state.page_up_clamped(10, 4);
        assert_eq!(state.selected_idx, Some(5));
        state.jump_top(10, 4);
        assert_eq!((state.selected_idx, state.scroll_top), (Some(0), 0));
        state.jump_bottom(10, 4);
        assert_eq!((state.selected_idx, state.scroll_top), (Some(9), 6));
    }
}
