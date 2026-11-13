use ratatui::layout::Rect;
use ratatui::style::Style;
use ratatui::text::{Line, Span};
use ratatui::widgets::Widget;

use crate::theme::ThemePalette;

use super::menu_surface::{
    menu_footer_lines, menu_surface_content_width, render_menu_surface, scroll_position,
};
use super::selection_popup::{
    render_rows_single_line, single_line_row_viewport, DisplayRow, MAX_POPUP_ROWS,
};
use super::ScrollState;

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct FileMatch {
    pub(crate) path: String,
    pub(crate) is_dir: bool,
    pub(crate) match_indices: Option<Vec<usize>>,
}

#[derive(Clone, Debug)]
pub(crate) struct FilePopupState {
    pending_query: Option<String>,
    display_query: String,
    dismissed_token: Option<String>,
    matches: Vec<FileMatch>,
    state: ScrollState,
    waiting: bool,
    empty_prompt: bool,
    no_matches: bool,
    pending_search_request: Option<String>,
}

impl FilePopupState {
    pub(crate) fn new() -> Self {
        Self {
            pending_query: None,
            display_query: String::new(),
            dismissed_token: None,
            matches: Vec::new(),
            state: ScrollState::default(),
            waiting: false,
            empty_prompt: false,
            no_matches: false,
            pending_search_request: None,
        }
    }

    pub(crate) fn set_root(&mut self, _root: Option<String>) {
        self.dismissed_token = None;
        self.clear();
        self.pending_search_request = Some(String::new());
    }

    pub(crate) fn on_token_change(&mut self, token: Option<&str>) {
        let Some(token) = token else {
            if self.pending_query.is_some()
                || self.empty_prompt
                || self.waiting
                || self.no_matches
                || !self.matches.is_empty()
            {
                self.pending_search_request = Some(String::new());
            }
            self.clear();
            return;
        };
        if self.dismissed_token.as_deref() == Some(token) {
            self.clear_visible_state();
            return;
        }
        if token.is_empty() {
            if !self.empty_prompt || self.pending_query.is_some() {
                self.pending_search_request = Some(String::new());
            }
            self.set_empty_prompt();
            return;
        }
        if self.pending_query.as_deref() == Some(token) {
            return;
        }
        self.pending_query = Some(token.to_string());
        self.display_query.clear();
        self.matches.clear();
        self.no_matches = false;
        self.state.reset();
        self.waiting = true;
        self.empty_prompt = false;
        self.pending_search_request = Some(token.to_string());
    }

    pub(crate) fn take_search_request(&mut self) -> Option<String> {
        self.pending_search_request.take()
    }

    pub(crate) fn apply_search_result(&mut self, query: &str, matches: Vec<FileMatch>) -> bool {
        if self.pending_query.as_deref() != Some(query) {
            return false;
        }
        self.display_query = query.to_string();
        self.matches = matches;
        self.waiting = false;
        self.empty_prompt = false;
        self.no_matches = self.matches.is_empty();
        self.state.clamp_selection(self.matches.len());
        self.state
            .ensure_visible(self.matches.len(), self.visible_rows());
        true
    }

    pub(crate) fn dismiss(&mut self, token: Option<&str>) {
        self.dismissed_token = token.map(str::to_string);
        self.pending_search_request = Some(String::new());
        self.clear_visible_state();
    }

    pub(crate) fn is_visible(&self, token: Option<&str>) -> bool {
        let Some(token) = token else {
            return false;
        };
        self.dismissed_token.as_deref() != Some(token)
            && (self.empty_prompt || self.waiting || self.no_matches || !self.matches.is_empty())
    }

    pub(crate) fn required_height(&self, width: u16) -> u16 {
        let rows = self.display_rows();
        let list_height = rows.len().clamp(1, MAX_POPUP_ROWS) as u16;
        let (start, visible) =
            single_line_row_viewport(&rows, &self.state, MAX_POPUP_ROWS, list_height);
        let position = scroll_position(start, visible, self.filtered_len());
        let footer = menu_footer_lines(
            &file_popup_hint(&ThemePalette::default(), menu_surface_content_width(width)),
            position.as_deref(),
            menu_surface_content_width(width),
            &ThemePalette::default(),
        )
        .len() as u16;
        list_height.saturating_add(1).saturating_add(footer)
    }

    pub(crate) fn selected_path(&self) -> Option<String> {
        if self.waiting || self.empty_prompt {
            return None;
        }
        self.state
            .selected_idx
            .and_then(|idx| self.matches.get(idx))
            .map(|item| item.path.clone())
    }

    pub(crate) fn move_up(&mut self) {
        self.state.move_up(self.matches.len());
        self.sync_selection();
    }

    pub(crate) fn move_down(&mut self) {
        self.state.move_down(self.matches.len());
        self.sync_selection();
    }

    pub(crate) fn filtered_len(&self) -> usize {
        self.matches.len()
    }

    pub(crate) fn visible_rows(&self) -> usize {
        self.filtered_len().min(MAX_POPUP_ROWS)
    }

    #[cfg(test)]
    pub(crate) fn scroll_top(&self) -> usize {
        self.state.scroll_top
    }

    #[cfg(test)]
    pub(crate) fn is_waiting(&self) -> bool {
        self.waiting
    }

    #[cfg(test)]
    pub(crate) fn is_empty_prompt(&self) -> bool {
        self.empty_prompt
    }

    pub(crate) fn render(
        &self,
        area: Rect,
        buf: &mut ratatui::buffer::Buffer,
        theme: &ThemePalette,
    ) {
        if area.is_empty() {
            return;
        }
        let inner = render_menu_surface(area, buf, theme, false);
        let rows = self.display_rows();
        let hint = file_popup_hint(theme, inner.width);
        let mut footer_lines = menu_footer_lines(&hint, None, inner.width, theme);
        let mut footer_height = footer_lines.len() as u16;
        if inner.height <= footer_height.saturating_add(1) {
            render_rows_single_line(
                inner,
                buf,
                &rows,
                &self.state,
                MAX_POPUP_ROWS,
                self.empty_message(),
                theme,
            );
            return;
        }
        let mut list_area = Rect::new(
            inner.x,
            inner.y,
            inner.width,
            inner.height.saturating_sub(footer_height.saturating_add(1)),
        );
        let (mut start, mut visible) =
            single_line_row_viewport(&rows, &self.state, MAX_POPUP_ROWS, list_area.height);
        let mut position = scroll_position(start, visible, self.filtered_len());
        footer_lines = menu_footer_lines(&hint, position.as_deref(), inner.width, theme);
        if footer_lines.len() as u16 != footer_height {
            footer_height = footer_lines.len() as u16;
            list_area.height = inner.height.saturating_sub(footer_height.saturating_add(1));
            if list_area.height == 0 {
                render_rows_single_line(
                    inner,
                    buf,
                    &rows,
                    &self.state,
                    MAX_POPUP_ROWS,
                    self.empty_message(),
                    theme,
                );
                return;
            }
            (start, visible) =
                single_line_row_viewport(&rows, &self.state, MAX_POPUP_ROWS, list_area.height);
            position = scroll_position(start, visible, self.filtered_len());
            footer_lines = menu_footer_lines(&hint, position.as_deref(), inner.width, theme);
        }
        render_rows_single_line(
            list_area,
            buf,
            &rows,
            &self.state,
            MAX_POPUP_ROWS,
            self.empty_message(),
            theme,
        );
        if footer_height > 0 && inner.height > footer_height {
            let footer_area = Rect::new(
                inner.x,
                inner
                    .y
                    .saturating_add(inner.height.saturating_sub(footer_height)),
                inner.width,
                footer_height,
            );
            for (offset, line) in footer_lines.into_iter().enumerate() {
                line.render(
                    Rect::new(
                        footer_area.x,
                        footer_area.y.saturating_add(offset as u16),
                        footer_area.width,
                        1,
                    ),
                    buf,
                );
            }
        }
    }

    fn set_empty_prompt(&mut self) {
        self.pending_query = None;
        self.display_query.clear();
        self.waiting = false;
        self.empty_prompt = true;
        self.no_matches = false;
        self.matches.clear();
        self.state.reset();
    }

    fn clear(&mut self) {
        self.pending_query = None;
        self.display_query.clear();
        self.clear_visible_state();
    }

    fn clear_visible_state(&mut self) {
        self.waiting = false;
        self.empty_prompt = false;
        self.no_matches = false;
        self.matches.clear();
        self.state.reset();
    }

    fn empty_message(&self) -> &'static str {
        if self.empty_prompt {
            "type to search files"
        } else if self.waiting {
            "loading..."
        } else {
            "no matches"
        }
    }

    fn display_rows(&self) -> Vec<DisplayRow> {
        self.matches
            .iter()
            .map(|item| DisplayRow {
                name: if item.is_dir {
                    format!("{}/", item.path)
                } else {
                    item.path.clone()
                },
                description: if item.is_dir {
                    "directory".to_string()
                } else {
                    String::new()
                },
                match_indices: item.match_indices.clone(),
                selection_index: None,
                is_section_header: false,
            })
            .collect()
    }

    fn sync_selection(&mut self) {
        let len = self.matches.len();
        self.state.clamp_selection(len);
        self.state.ensure_visible(len, self.visible_rows());
    }
}

fn file_popup_hint(theme: &ThemePalette, width: u16) -> Line<'static> {
    let text = if width >= 28 {
        "Enter/Tab insert  Esc close"
    } else {
        "Enter/Tab Esc"
    };
    Line::from(Span::styled(text, Style::default().fg(theme.text_muted)))
}

pub(crate) fn normalize_inserted_path(path: &str) -> String {
    if path.chars().any(char::is_whitespace) && !path.contains('"') {
        format!("\"{path}\"")
    } else {
        path.to_string()
    }
}
