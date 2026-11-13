use ratatui::layout::Rect;

use crate::theme::ThemePalette;

use ratatui::style::Style;
use ratatui::text::{Line, Span};
use ratatui::widgets::Widget;

use super::menu_surface::{menu_footer_lines, menu_surface_content_width, render_menu_surface};
use super::selection_popup::{
    render_rows_single_line, single_line_row_viewport, DisplayRow, SelectionItem, MAX_POPUP_ROWS,
};
use super::slash_commands::SlashCommandItem;
use super::ScrollState;

const ALIAS_COMMANDS: &[&str] = &["quit"];

#[derive(Clone, Debug)]
pub(crate) struct CommandPopupState {
    commands: Vec<SlashCommandItem>,
    filter: String,
    state: ScrollState,
}

impl CommandPopupState {
    pub(crate) fn new(commands: Vec<SlashCommandItem>) -> Self {
        Self {
            commands,
            filter: String::new(),
            state: ScrollState::default(),
        }
    }

    pub(crate) fn on_composer_change(&mut self, composer: &str) {
        let previous = self.filter.clone();
        let trimmed = composer.trim_start();
        let next_filter = trimmed
            .strip_prefix('/')
            .filter(|after_slash| !after_slash.chars().any(char::is_whitespace))
            .unwrap_or_default()
            .to_ascii_lowercase();
        self.filter = next_filter;
        if self.filter != previous {
            self.state.reset();
        }
        self.sync_selection();
    }

    pub(crate) fn is_visible(&self, composer: &str) -> bool {
        let trimmed = composer.trim_start();
        if !trimmed.starts_with('/') {
            return false;
        }
        let after_slash = &trimmed[1..];
        if after_slash.chars().any(char::is_whitespace) {
            return false;
        }
        !self.filtered_items().is_empty()
    }

    pub(crate) fn required_height(&self, width: u16) -> u16 {
        let rows = self.display_rows();
        let list_height = rows.len().min(MAX_POPUP_ROWS) as u16;
        let (start, visible_rows) =
            single_line_row_viewport(&rows, &self.state, MAX_POPUP_ROWS, list_height);
        let position = command_scroll_position(&rows, start, visible_rows, self.filtered_len());
        let footer = menu_footer_lines(
            &command_popup_hint(&ThemePalette::default(), menu_surface_content_width(width)),
            position.as_deref(),
            menu_surface_content_width(width),
            &ThemePalette::default(),
        )
        .len() as u16;
        list_height.saturating_add(1).saturating_add(footer)
    }

    pub(crate) fn selected_item(&self) -> Option<SlashCommandItem> {
        let items = self.filtered_items();
        self.state
            .selected_idx
            .and_then(|idx| items.get(idx).cloned())
            .filter(|item| !item.is_disabled())
    }

    pub(crate) fn exact_match(&self, command: &str) -> Option<SlashCommandItem> {
        let normalized = command.trim().trim_start_matches('/');
        self.commands
            .iter()
            .find(|item| {
                item.command().trim_start_matches('/') == normalized
                    || item
                        .aliases()
                        .iter()
                        .any(|alias| alias.trim_start_matches('/') == normalized)
            })
            .cloned()
            .filter(|item| !item.is_disabled())
    }

    pub(crate) fn move_up(&mut self) {
        let items = self.filtered_items();
        self.state.selected_idx = previous_enabled_index(&items, self.state.selected_idx);
        self.sync_selection();
    }

    pub(crate) fn move_down(&mut self) {
        let items = self.filtered_items();
        self.state.selected_idx = next_enabled_index(&items, self.state.selected_idx);
        self.sync_selection();
    }

    pub(crate) fn filtered_len(&self) -> usize {
        self.filtered_items().len()
    }

    pub(crate) fn visible_rows(&self) -> usize {
        self.filtered_len().min(MAX_POPUP_ROWS)
    }

    #[cfg(test)]
    pub(crate) fn scroll_top(&self) -> usize {
        self.state.scroll_top
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
        let hint = command_popup_hint(theme, inner.width);
        let mut footer_lines = menu_footer_lines(&hint, None, inner.width, theme);
        let mut footer_height = footer_lines.len() as u16;
        let mut list_area = Rect::new(
            inner.x,
            inner.y,
            inner.width,
            inner.height.saturating_sub(footer_height.saturating_add(1)),
        );
        let (mut start, mut visible_rows) =
            single_line_row_viewport(&rows, &self.state, MAX_POPUP_ROWS, list_area.height);
        let mut position = command_scroll_position(&rows, start, visible_rows, self.filtered_len());
        footer_lines = menu_footer_lines(&hint, position.as_deref(), inner.width, theme);
        if footer_lines.len() as u16 != footer_height {
            footer_height = footer_lines.len() as u16;
            list_area.height = inner.height.saturating_sub(footer_height.saturating_add(1));
            (start, visible_rows) =
                single_line_row_viewport(&rows, &self.state, MAX_POPUP_ROWS, list_area.height);
            position = command_scroll_position(&rows, start, visible_rows, self.filtered_len());
            footer_lines = menu_footer_lines(&hint, position.as_deref(), inner.width, theme);
        }
        render_rows_single_line(
            list_area,
            buf,
            &rows,
            &self.state,
            MAX_POPUP_ROWS,
            "no matches",
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

    fn filtered_items(&self) -> Vec<SlashCommandItem> {
        if self.filter.is_empty() {
            return self
                .commands
                .iter()
                .filter(|command| !is_alias_command(command.command()))
                .cloned()
                .collect();
        }
        let mut exact_matches = Vec::new();
        let mut prefix_matches = Vec::new();
        for command in &self.commands {
            let lower = command.command().to_ascii_lowercase();
            if lower == self.filter {
                exact_matches.push(command.clone());
            } else if lower.starts_with(&self.filter) {
                prefix_matches.push(command.clone());
            }
        }
        exact_matches.extend(prefix_matches);
        exact_matches
    }

    fn display_rows(&self) -> Vec<DisplayRow> {
        let items = self.filtered_items();
        let show_sections =
            self.filter.is_empty() && items.iter().any(|command| !command.tab().trim().is_empty());
        let mut rows = Vec::new();
        let mut current_section: Option<String> = None;
        for (selection_index, command) in items.into_iter().enumerate() {
            let tab = command.tab().trim();
            if show_sections && !tab.is_empty() && current_section.as_deref() != Some(tab) {
                current_section = Some(tab.to_string());
                rows.push(DisplayRow {
                    name: tab.to_string(),
                    description: String::new(),
                    match_indices: None,
                    selection_index: None,
                    is_section_header: true,
                });
            }
            let selected = self.state.selected_idx == Some(selection_index);
            let name = command.display_name();
            let match_indices = (!self.filter.is_empty()).then(|| {
                let chars = self.filter.chars().count();
                (1..=chars).collect()
            });
            let mut row = SelectionItem {
                name,
                description: command.description().to_string(),
                selected_description: command.description().to_string(),
                display_shortcut: (!command.shortcut().trim().is_empty())
                    .then(|| command.shortcut().to_string()),
                is_disabled: command.is_disabled(),
                disabled_reason: (!command.disabled_reason().trim().is_empty())
                    .then(|| command.disabled_reason().to_string()),
                tab_id: (!command.tab().trim().is_empty()).then(|| command.tab().to_string()),
                side_content: (!command.side_content().trim().is_empty())
                    .then(|| command.side_content().to_string()),
                match_indices,
                ..SelectionItem::default()
            }
            .to_display_row(selected);
            row.selection_index = Some(selection_index);
            rows.push(row);
        }
        rows
    }

    fn sync_selection(&mut self) {
        let items = self.filtered_items();
        if items.is_empty() {
            self.state.reset();
            return;
        }
        let len = items.len();
        let selected = self.state.selected_idx.unwrap_or(0).min(len - 1);
        self.state.selected_idx = if items.get(selected).is_some_and(|item| !item.is_disabled()) {
            Some(selected)
        } else {
            first_enabled_index(&items)
        };
        self.state.ensure_visible(len, self.visible_rows());
    }
}

fn command_scroll_position(
    rows: &[DisplayRow],
    start: usize,
    visible_rows: usize,
    total_commands: usize,
) -> Option<String> {
    if total_commands == 0 || visible_rows == 0 {
        return None;
    }
    let visible_commands = rows
        .iter()
        .skip(start)
        .take(visible_rows)
        .filter_map(|row| row.selection_index)
        .collect::<Vec<_>>();
    let first = *visible_commands.first()?;
    let last = *visible_commands.last()?;
    if first == 0 && last.saturating_add(1) >= total_commands {
        None
    } else {
        Some(format!("{}-{}/{total_commands}", first + 1, last + 1))
    }
}

fn command_popup_hint(theme: &ThemePalette, width: u16) -> Line<'static> {
    let text = if width >= 28 {
        "Enter/Tab insert  Esc close"
    } else {
        "Enter/Tab Esc"
    };
    Line::from(Span::styled(text, Style::default().fg(theme.text_muted)))
}

fn is_alias_command(command: &str) -> bool {
    ALIAS_COMMANDS
        .iter()
        .any(|alias| command.trim_start_matches('/').eq_ignore_ascii_case(alias))
}

fn first_enabled_index(items: &[SlashCommandItem]) -> Option<usize> {
    items.iter().position(|item| !item.is_disabled())
}

fn previous_enabled_index(items: &[SlashCommandItem], selected: Option<usize>) -> Option<usize> {
    if items.is_empty() {
        return None;
    }
    let current = selected.unwrap_or(0).min(items.len() - 1);
    (0..current)
        .rev()
        .find(|idx| !items[*idx].is_disabled())
        .or_else(|| (!items[current].is_disabled()).then_some(current))
        .or_else(|| first_enabled_index(items))
}

fn next_enabled_index(items: &[SlashCommandItem], selected: Option<usize>) -> Option<usize> {
    if items.is_empty() {
        return None;
    }
    let current = selected.unwrap_or(0).min(items.len() - 1);
    ((current + 1)..items.len())
        .find(|idx| !items[*idx].is_disabled())
        .or_else(|| (!items[current].is_disabled()).then_some(current))
        .or_else(|| first_enabled_index(items))
}
