use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::Widget;
use serde_json::Value;
use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

use super::menu_surface::{
    menu_footer_lines, menu_surface_content_width, menu_surface_padding_height,
    render_menu_surface, scroll_position,
};
use super::ScrollState;
use crate::theme::ThemePalette;

pub(crate) const MAX_POPUP_ROWS: usize = 8;

#[derive(Clone, Debug, Default)]
pub(crate) struct ListSelectionView {
    header: Vec<Line<'static>>,
    rows: Vec<DisplayRow>,
    state: ScrollState,
    footer_hint: Option<Line<'static>>,
    footer_aligned_to_header: bool,
    compact: bool,
}

impl ListSelectionView {
    pub(crate) fn new(
        header: Vec<Line<'static>>,
        rows: Vec<DisplayRow>,
        selected_idx: usize,
        footer_hint: Option<Line<'static>>,
    ) -> Self {
        let mut state = ScrollState {
            selected_idx: (!rows.is_empty())
                .then_some(selected_idx.min(rows.len().saturating_sub(1))),
            scroll_top: 0,
        };
        state.ensure_visible(rows.len(), MAX_POPUP_ROWS);
        Self {
            header,
            rows,
            state,
            footer_hint,
            footer_aligned_to_header: false,
            compact: false,
        }
    }

    pub(crate) fn new_compact(
        header: Vec<Line<'static>>,
        rows: Vec<DisplayRow>,
        selected_idx: usize,
        footer_hint: Option<Line<'static>>,
    ) -> Self {
        let mut view = Self::new(header, rows, selected_idx, footer_hint);
        view.compact = true;
        view
    }

    pub(crate) fn with_footer_aligned_to_header(mut self) -> Self {
        self.footer_aligned_to_header = true;
        self
    }

    fn footer_inset(&self) -> u16 {
        if self.footer_aligned_to_header {
            0
        } else {
            2
        }
    }

    pub(crate) fn desired_height(&self, width: u16) -> u16 {
        let content_width = menu_surface_content_width(width);
        let rows_height =
            measure_rows_height(&self.rows, &self.state, MAX_POPUP_ROWS, content_width);
        let (start, visible_items) = wrapped_row_viewport(
            &self.rows,
            &self.state,
            MAX_POPUP_ROWS,
            content_width,
            rows_height,
        );
        let position = scroll_position(start, visible_items, self.rows.len());
        let footer_height = self
            .footer_lines(content_width, position.as_deref(), &ThemePalette::default())
            .len() as u16;
        (self.header.len() as u16)
            .saturating_add(rows_height)
            .saturating_add(u16::from(!self.rows.is_empty()))
            .saturating_add(u16::from(footer_height > 0 && !self.compact))
            .saturating_add(footer_height)
            .saturating_add(if self.compact {
                0
            } else {
                menu_surface_padding_height()
            })
            .max(1)
    }

    fn footer_lines(
        &self,
        width: u16,
        position: Option<&str>,
        theme: &ThemePalette,
    ) -> Vec<Line<'static>> {
        let Some(footer_hint) = self.footer_hint.as_ref() else {
            return Vec::new();
        };
        menu_footer_lines(
            footer_hint,
            position,
            width.saturating_sub(self.footer_inset()),
            theme,
        )
    }

    pub(crate) fn render(
        &self,
        area: Rect,
        buf: &mut Buffer,
        theme: &ThemePalette,
        empty_message: &str,
    ) {
        if area.is_empty() {
            return;
        }
        let vertical_padding = !self.compact && area.height >= self.desired_height(area.width);
        let area = render_menu_surface(area, buf, theme, vertical_padding);
        if area.is_empty() {
            return;
        }
        let mut y = area.y;
        let bottom = area.y.saturating_add(area.height);
        for line in &self.header {
            if y >= bottom {
                return;
            }
            line.clone().render(
                Rect {
                    x: area.x,
                    y,
                    width: area.width,
                    height: 1,
                },
                buf,
            );
            y = y.saturating_add(1);
        }
        if !self.rows.is_empty() && y < bottom {
            y = y.saturating_add(1);
        }
        if y >= bottom {
            return;
        }
        let mut footer_lines = self.footer_lines(area.width, None, theme);
        let mut footer_height = footer_lines.len() as u16;
        let footer_gap = u16::from(footer_height > 0 && !self.compact);
        let mut rows_area = Rect {
            x: area.x,
            y,
            width: area.width,
            height: bottom
                .saturating_sub(y)
                .saturating_sub(footer_height)
                .saturating_sub(footer_gap),
        };
        let (_, visible_with_footer) = wrapped_row_viewport(
            &self.rows,
            &self.state,
            MAX_POPUP_ROWS,
            area.width,
            rows_area.height,
        );
        let (_, visible_without_footer) = wrapped_row_viewport(
            &self.rows,
            &self.state,
            MAX_POPUP_ROWS,
            area.width,
            bottom.saturating_sub(y),
        );
        let footer_suppressed =
            visible_with_footer < self.rows.len() && visible_without_footer >= self.rows.len();
        if footer_suppressed {
            footer_lines.clear();
            footer_height = 0;
            rows_area.height = bottom.saturating_sub(y);
        }
        let (mut start, mut visible_items) = wrapped_row_viewport(
            &self.rows,
            &self.state,
            MAX_POPUP_ROWS,
            rows_area.width,
            rows_area.height,
        );
        if !footer_suppressed {
            let mut position = scroll_position(start, visible_items, self.rows.len());
            footer_lines = self.footer_lines(area.width, position.as_deref(), theme);
            if footer_lines.len() as u16 != footer_height {
                footer_height = footer_lines.len() as u16;
                rows_area.height = bottom
                    .saturating_sub(y)
                    .saturating_sub(footer_height)
                    .saturating_sub(u16::from(footer_height > 0 && !self.compact));
                (start, visible_items) = wrapped_row_viewport(
                    &self.rows,
                    &self.state,
                    MAX_POPUP_ROWS,
                    rows_area.width,
                    rows_area.height,
                );
                position = scroll_position(start, visible_items, self.rows.len());
                footer_lines = self.footer_lines(area.width, position.as_deref(), theme);
            }
        }
        render_rows(
            rows_area,
            buf,
            &self.rows,
            &self.state,
            MAX_POPUP_ROWS,
            empty_message,
            theme,
        );
        for (index, footer_hint) in footer_lines.into_iter().enumerate() {
            let footer_y = bottom
                .saturating_sub(footer_height)
                .saturating_add(index as u16);
            footer_hint.render(
                Rect {
                    x: area.x.saturating_add(self.footer_inset()),
                    y: footer_y,
                    width: area.width.saturating_sub(self.footer_inset()),
                    height: 1,
                },
                buf,
            );
        }
    }
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct SelectionAction {
    pub(crate) kind: String,
    pub(crate) text: String,
    pub(crate) command_name: String,
    pub(crate) source: String,
    pub(crate) command_kind: String,
    pub(crate) payload: Value,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct SelectionViewRow {
    pub(crate) resume_entry: Value,
    pub(crate) id: String,
    pub(crate) name: String,
    pub(crate) description: String,
    pub(crate) selected_description: String,
    pub(crate) is_current: bool,
    pub(crate) is_default: bool,
    pub(crate) is_disabled: bool,
    pub(crate) disabled_reason: Option<String>,
    pub(crate) dismiss_on_select: bool,
    pub(crate) action: Option<SelectionAction>,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct SelectionViewState {
    pub(crate) resume_browser: Option<super::resume_browser::ResumeBrowser>,
    pub(crate) view_id: String,
    pub(crate) title: String,
    pub(crate) subtitle: Option<String>,
    pub(crate) rows: Vec<SelectionViewRow>,
    pub(crate) footer_hint: Option<String>,
    pub(crate) cancel_action: Option<SelectionAction>,
    footer_aligned_to_header: bool,
    pub(crate) preserve_selected_description: bool,
    state: ScrollState,
}

impl SelectionViewState {
    pub(crate) fn enable_resume_browser(&mut self) {
        self.resume_browser = Some(super::resume_browser::ResumeBrowser {
            all: self.rows.clone(), ..Default::default()
        });
        self.apply_browser_filter();
    }

    fn apply_browser_filter(&mut self) {
        let selected = self.selected_row().map(|r| r.id.clone());
        if let Some(browser) = &self.resume_browser {
            self.rows = browser.rows();
        }
        self.state.selected_idx = selected.and_then(|id| self.rows.iter().position(|r| r.id == id))
            .or_else(|| (!self.rows.is_empty()).then_some(0));
        self.state.scroll_top = 0;
    }

    pub(crate) fn browser_key(&mut self, key: crossterm::event::KeyEvent) -> bool {
        if self.resume_browser.as_mut().is_some_and(|browser| browser.key(key)) {
            self.apply_browser_filter();
            return true;
        }
        false
    }

    pub(crate) fn browser_paste(&mut self, text: &str) -> bool {
        if let Some(browser) = &mut self.resume_browser {
            browser.query.extend(text.chars().filter_map(|ch| {
                if ch.is_whitespace() { Some(' ') } else if ch.is_control() { None } else { Some(ch) }
            }));
            self.apply_browser_filter();
            return true;
        }
        false
    }

    pub(crate) fn details_action(&self) -> Option<SelectionAction> {
        let payload = self.selected_row()?.resume_entry.get("details_action")?.clone();
        Some(SelectionAction { kind: "request".into(), payload, ..Default::default() })
    }

    pub(crate) fn refresh_from(&mut self, mut next: Self) {
        let selected = self.selected_row().map(|row| row.id.clone());
        if let (Some(old), Some(new)) = (&self.resume_browser, &mut next.resume_browser) {
            new.query = old.query.clone();
            new.all_projects = old.all_projects;
            new.status = old.status;
            new.created = old.created;
            next.apply_browser_filter();
        }
        if let Some(index) = selected.and_then(|id| next.rows.iter().position(|row| row.id == id)) {
            next.state = self.state;
            next.state.selected_idx = Some(index);
            next.state.ensure_visible(next.rows.len(), MAX_POPUP_ROWS);
        }
        *self = next;
    }

    pub(crate) fn new(
        view_id: String,
        title: String,
        subtitle: Option<String>,
        rows: Vec<SelectionViewRow>,
        selected_row_id: Option<String>,
        footer_hint: Option<String>,
        cancel_action: Option<SelectionAction>,
    ) -> Self {
        let selected_idx = selected_row_id
            .as_deref()
            .and_then(|id| rows.iter().position(|row| row.id == id))
            .or_else(|| rows.iter().position(|row| row.is_current))
            .unwrap_or(0);
        let mut state = ScrollState {
            selected_idx: (!rows.is_empty()).then_some(selected_idx),
            scroll_top: 0,
        };
        state.ensure_visible(rows.len(), MAX_POPUP_ROWS);
        Self {
            view_id,
            resume_browser: None,
            title,
            subtitle,
            rows,
            footer_hint,
            cancel_action,
            footer_aligned_to_header: false,
            preserve_selected_description: false,
            state,
        }
    }

    pub(crate) fn with_footer_aligned_to_header(mut self) -> Self {
        self.footer_aligned_to_header = true;
        self
    }

    pub(crate) fn is_visible(&self) -> bool {
        self.resume_browser.is_some() || !self.rows.is_empty()
    }

    pub(crate) fn move_up(&mut self) {
        self.state.selected_idx = previous_enabled_row(&self.rows, self.state.selected_idx);
        self.state.ensure_visible(self.rows.len(), MAX_POPUP_ROWS);
    }

    pub(crate) fn move_down(&mut self) {
        self.state.selected_idx = next_enabled_row(&self.rows, self.state.selected_idx);
        self.state.ensure_visible(self.rows.len(), MAX_POPUP_ROWS);
    }

    pub(crate) fn page_up(&mut self) {
        self.state.page_up_clamped(self.rows.len(), MAX_POPUP_ROWS);
        self.state.selected_idx = enabled_at_or_before(&self.rows, self.state.selected_idx)
            .or_else(|| first_enabled_row(&self.rows));
        self.state.ensure_visible(self.rows.len(), MAX_POPUP_ROWS);
    }

    pub(crate) fn page_down(&mut self) {
        self.state
            .page_down_clamped(self.rows.len(), MAX_POPUP_ROWS);
        self.state.selected_idx = enabled_at_or_after(&self.rows, self.state.selected_idx)
            .or_else(|| last_enabled_row(&self.rows));
        self.state.ensure_visible(self.rows.len(), MAX_POPUP_ROWS);
    }

    pub(crate) fn jump_top(&mut self) {
        self.state.selected_idx = self
            .rows
            .iter()
            .position(|row| !row.is_disabled && row.disabled_reason.is_none());
        self.state.ensure_visible(self.rows.len(), MAX_POPUP_ROWS);
    }

    pub(crate) fn jump_bottom(&mut self) {
        self.state.selected_idx = self
            .rows
            .iter()
            .rposition(|row| !row.is_disabled && row.disabled_reason.is_none());
        self.state.ensure_visible(self.rows.len(), MAX_POPUP_ROWS);
    }

    pub(crate) fn select_enabled_number(&mut self, number: usize) -> bool {
        if number == 0 {
            return false;
        }
        let Some((idx, _row)) = self
            .rows
            .iter()
            .enumerate()
            .filter(|(_, row)| !row.is_disabled && row.disabled_reason.is_none())
            .nth(number - 1)
        else {
            return false;
        };
        self.state.selected_idx = Some(idx);
        self.state.ensure_visible(self.rows.len(), MAX_POPUP_ROWS);
        true
    }

    pub(crate) fn selected_row(&self) -> Option<&SelectionViewRow> {
        self.state.selected_idx.and_then(|idx| self.rows.get(idx))
    }

    pub(crate) fn set_preview(
        &mut self,
        session_id: Option<&str>,
        title: Option<&str>,
        lines: &[String],
    ) {
        if self.view_id != "session.resume" {
            return;
        }
        if let Some(session_id) = session_id {
            if self.selected_row().is_none_or(|row| row.id != session_id) {
                return;
            }
        }
        let mut preview = title
            .filter(|value| !value.trim().is_empty())
            .map(str::to_string)
            .into_iter()
            .collect::<Vec<_>>();
        preview.extend(
            lines
                .iter()
                .map(|line| line.trim())
                .filter(|line| !line.is_empty())
                .take(3)
                .map(str::to_string),
        );
        if !preview.is_empty() {
            self.subtitle = Some(preview.join(" · "));
        }
    }

    pub(crate) fn accept_action(&self) -> Option<SelectionAction> {
        let row = self.selected_row()?;
        if row.is_disabled || row.disabled_reason.is_some() {
            return None;
        }
        row.action.clone()
    }

    pub(crate) fn selected_row_dismisses_on_select(&self) -> bool {
        self.selected_row()
            .map(|row| !row.is_disabled && row.disabled_reason.is_none() && row.dismiss_on_select)
            .unwrap_or(self.resume_browser.is_none())
    }

    pub(crate) fn set_feedback(&mut self, message: impl Into<String>) {
        self.footer_hint = Some(message.into());
    }

    pub(crate) fn desired_height(&self, width: u16) -> u16 {
        if let Some(browser) = &self.resume_browser {
            return browser.desired_height(width, self.rows.len());
        }
        if !self.is_visible() {
            return 0;
        }
        let content_width = menu_surface_content_width(width);
        let compact = self.use_compact_rows(content_width);
        let rows = self.display_rows(compact);
        self.layout_height(content_width, &rows).clamp(5, 16)
    }

    pub(crate) fn render(&self, area: Rect, buf: &mut Buffer, theme: &ThemePalette) {
        if let Some(browser) = &self.resume_browser {
            browser.render(&self.rows, self.state.selected_idx, area, buf, theme);
            return;
        }
        if !self.is_visible() || area.is_empty() {
            return;
        }
        let content_width = menu_surface_content_width(area.width);
        let header =
            wrapped_selection_header(&self.title, self.subtitle.as_deref(), content_width, theme);
        let expanded_rows = self.display_rows(false);
        let compact = self.use_compact_rows(content_width)
            || area.height < self.layout_height(content_width, &expanded_rows);
        let rows = self.display_rows(compact);
        let footer = self
            .footer_hint
            .as_ref()
            .filter(|value| !value.trim().is_empty())
            .map(|value| {
                Line::from(Span::styled(
                    value.clone(),
                    Style::default().fg(theme.text_muted),
                ))
            });
        let mut view =
            ListSelectionView::new(header, rows, self.state.selected_idx.unwrap_or(0), footer);
        if self.footer_aligned_to_header {
            view = view.with_footer_aligned_to_header();
        }
        view.state = self.state;
        view.render(area, buf, theme, "no options");
    }

    fn use_compact_rows(&self, content_width: u16) -> bool {
        if content_width < 28 {
            return true;
        }
        let expanded_rows = self.display_rows(false);
        self.layout_height(content_width, &expanded_rows) > 16
    }

    fn layout_height(&self, content_width: u16, rows: &[DisplayRow]) -> u16 {
        let header_rows = wrapped_selection_header(
            &self.title,
            self.subtitle.as_deref(),
            content_width,
            &ThemePalette::default(),
        )
        .len() as u16;
        let footer_rows = self
            .footer_hint
            .as_ref()
            .filter(|value| !value.trim().is_empty())
            .map(|value| {
                let inset = if self.footer_aligned_to_header { 0 } else { 2 };
                selection_footer_height(value, content_width.saturating_sub(inset))
            })
            .unwrap_or(0);
        header_rows
            .saturating_add(u16::from(!rows.is_empty()))
            .saturating_add(measure_rows_height(
                rows,
                &self.state,
                MAX_POPUP_ROWS,
                content_width,
            ))
            .saturating_add(u16::from(footer_rows > 0))
            .saturating_add(footer_rows)
            .saturating_add(menu_surface_padding_height())
    }

    fn display_rows(&self, compact: bool) -> Vec<DisplayRow> {
        let enabled_count = self
            .rows
            .iter()
            .filter(|row| !row.is_disabled && row.disabled_reason.is_none())
            .count()
            .max(1);
        let row_number_width = enabled_count.to_string().len();
        let mut enabled_row_number = 0usize;
        self.rows
            .iter()
            .enumerate()
            .map(|(idx, row)| {
                let selected = self.state.selected_idx == Some(idx);
                let indicator = if selected {
                    crate::style::GLYPH_CURSOR_CHAR
                } else {
                    ' '
                };
                let is_disabled = row.is_disabled || row.disabled_reason.is_some();
                let row_prefix = if is_disabled {
                    format!("{indicator} {}", " ".repeat(row_number_width + 2))
                } else {
                    enabled_row_number += 1;
                    format!(
                        "{indicator} {:>width$}. ",
                        enabled_row_number,
                        width = row_number_width
                    )
                };
                let marker = match (row.is_current, row.is_default) {
                    (true, true) => " (current, default)",
                    (true, false) => " (current)",
                    (false, true) => " (default)",
                    (false, false) => "",
                };
                SelectionItem {
                    name: format!("{}{}", row.name, marker),
                    name_prefix: row_prefix,
                    description: if compact {
                        String::new()
                    } else {
                        row.description.clone()
                    },
                    selected_description: if compact && !self.preserve_selected_description {
                        String::new()
                    } else {
                        row.selected_description.clone()
                    },
                    is_disabled: row.is_disabled,
                    disabled_reason: row.disabled_reason.clone(),
                    ..SelectionItem::default()
                }
                .to_display_row(selected)
            })
            .collect()
    }
}

fn previous_enabled_row(rows: &[SelectionViewRow], selected: Option<usize>) -> Option<usize> {
    let start = selected.unwrap_or(rows.len()).min(rows.len());
    (0..start)
        .rev()
        .find(|idx| !rows[*idx].is_disabled && rows[*idx].disabled_reason.is_none())
        .or_else(|| last_enabled_row(rows))
}

fn next_enabled_row(rows: &[SelectionViewRow], selected: Option<usize>) -> Option<usize> {
    let start = selected.map_or(0, |idx| idx.saturating_add(1));
    (start..rows.len())
        .find(|idx| !rows[*idx].is_disabled && rows[*idx].disabled_reason.is_none())
        .or_else(|| first_enabled_row(rows))
}

fn first_enabled_row(rows: &[SelectionViewRow]) -> Option<usize> {
    rows.iter()
        .position(|row| !row.is_disabled && row.disabled_reason.is_none())
}

fn last_enabled_row(rows: &[SelectionViewRow]) -> Option<usize> {
    rows.iter()
        .rposition(|row| !row.is_disabled && row.disabled_reason.is_none())
}

fn enabled_at_or_before(rows: &[SelectionViewRow], selected: Option<usize>) -> Option<usize> {
    let selected = selected?.min(rows.len().saturating_sub(1));
    (0..=selected)
        .rev()
        .find(|index| !rows[*index].is_disabled && rows[*index].disabled_reason.is_none())
}

fn enabled_at_or_after(rows: &[SelectionViewRow], selected: Option<usize>) -> Option<usize> {
    let selected = selected?.min(rows.len().saturating_sub(1));
    (selected..rows.len())
        .find(|index| !rows[*index].is_disabled && rows[*index].disabled_reason.is_none())
}

fn selection_footer_height(value: &str, width: u16) -> u16 {
    let line = Line::from(value.to_string());
    crate::wrapping::adaptive_wrap_line(
        &line,
        crate::wrapping::RtOptions::new(usize::from(width).max(1)),
    )
    .len() as u16
}

#[derive(Clone, Debug)]
pub(crate) struct DisplayRow {
    pub(crate) name: String,
    pub(crate) description: String,
    pub(crate) match_indices: Option<Vec<usize>>,
    pub(crate) selection_index: Option<usize>,
    pub(crate) is_section_header: bool,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct SelectionItem {
    pub(crate) name: String,
    pub(crate) name_prefix: String,
    pub(crate) description: String,
    pub(crate) selected_description: String,
    pub(crate) display_shortcut: Option<String>,
    pub(crate) is_disabled: bool,
    pub(crate) disabled_reason: Option<String>,
    pub(crate) tab_id: Option<String>,
    pub(crate) side_content: Option<String>,
    pub(crate) match_indices: Option<Vec<usize>>,
}

impl SelectionItem {
    pub(crate) fn display_description(&self, selected: bool) -> String {
        if self.is_disabled {
            if let Some(reason) = self
                .disabled_reason
                .as_ref()
                .filter(|value| !value.is_empty())
            {
                return reason.clone();
            }
        }
        if selected && !self.selected_description.is_empty() {
            return self.selected_description.clone();
        }
        self.description.clone()
    }

    pub(crate) fn to_display_row(&self, selected: bool) -> DisplayRow {
        let mut description = self.display_description(selected);
        if let Some(shortcut) = self
            .display_shortcut
            .as_ref()
            .filter(|shortcut| !shortcut.trim().is_empty())
        {
            description = if description.is_empty() {
                shortcut.clone()
            } else {
                format!("{description}  {shortcut}")
            };
        }
        if let Some(side_content) = self
            .side_content
            .as_ref()
            .filter(|side_content| !side_content.trim().is_empty())
        {
            description = if description.is_empty() {
                side_content.clone()
            } else {
                format!("{description}  {side_content}")
            };
        }
        DisplayRow {
            name: format!("{}{}", self.name_prefix, self.name),
            description,
            match_indices: self.match_indices.clone(),
            selection_index: None,
            is_section_header: false,
        }
    }
}

#[cfg(test)]
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct SelectionTab {
    pub(crate) id: String,
    pub(crate) title: String,
    pub(crate) items: Vec<SelectionItem>,
}

pub(crate) fn measure_rows_height(
    rows: &[DisplayRow],
    state: &ScrollState,
    max_rows: usize,
    width: u16,
) -> u16 {
    if rows.is_empty() {
        return 1;
    }
    let visible_items = max_rows.min(rows.len());
    let start = viewport_start(rows, state, visible_items);
    let desc_col = description_column(rows, start, visible_items, width);
    rows.iter()
        .skip(start)
        .take(visible_items)
        .map(|row| wrap_row(row, desc_col, width).len() as u16)
        .fold(0u16, u16::saturating_add)
        .max(1)
}

pub(crate) fn wrapped_row_viewport(
    rows: &[DisplayRow],
    state: &ScrollState,
    max_rows: usize,
    width: u16,
    available_height: u16,
) -> (usize, usize) {
    if rows.is_empty() || max_rows == 0 || available_height == 0 {
        return (0, 0);
    }
    let selected = selected_display_index(rows, state).map(|index| index.min(rows.len() - 1));
    let mut start = state.scroll_top.min(rows.len() - 1);
    if selected.is_some_and(|index| index < start) {
        start = selected.unwrap_or(start);
    }

    loop {
        let max_items = max_rows.min(rows.len().saturating_sub(start));
        let mut visible_items = 0usize;
        for candidate in 1..=max_items {
            if measure_row_window_height(rows, start, candidate, width) <= available_height {
                visible_items = candidate;
            }
        }
        if visible_items == 0 {
            visible_items = 1;
        }
        if selected.is_none_or(|index| index < start.saturating_add(visible_items)) {
            return (start, visible_items);
        }
        let selected = selected.unwrap_or(start);
        if start >= selected {
            return (start, visible_items);
        }
        start = start.saturating_add(1);
    }
}

pub(crate) fn single_line_row_viewport(
    rows: &[DisplayRow],
    state: &ScrollState,
    max_rows: usize,
    available_height: u16,
) -> (usize, usize) {
    let visible_items = max_rows.min(rows.len()).min(usize::from(available_height));
    (viewport_start(rows, state, visible_items), visible_items)
}

fn measure_row_window_height(
    rows: &[DisplayRow],
    start: usize,
    visible_items: usize,
    width: u16,
) -> u16 {
    if visible_items == 0 {
        return 0;
    }
    let desc_col = description_column(rows, start, visible_items, width);
    rows.iter()
        .skip(start)
        .take(visible_items)
        .map(|row| wrap_row(row, desc_col, width).len() as u16)
        .fold(0u16, u16::saturating_add)
}

pub(crate) fn render_rows(
    area: Rect,
    buf: &mut Buffer,
    rows: &[DisplayRow],
    state: &ScrollState,
    max_rows: usize,
    empty_message: &str,
    theme: &ThemePalette,
) {
    if area.is_empty() {
        return;
    }
    if rows.is_empty() {
        Line::from(Span::styled(
            empty_message.to_string(),
            Style::default()
                .fg(theme.text_muted)
                .add_modifier(Modifier::ITALIC),
        ))
        .render(area, buf);
        return;
    }

    let (start, visible_items) =
        wrapped_row_viewport(rows, state, max_rows, area.width, area.height);
    let desc_col = description_column(rows, start, visible_items, area.width);
    let mut y = area.y;

    for (idx, row) in rows.iter().enumerate().skip(start).take(visible_items) {
        if y >= area.y.saturating_add(area.height) {
            break;
        }
        let selected = row_is_selected(row, idx, rows, state);
        for mut line in wrap_row(row, desc_col, area.width) {
            if y >= area.y.saturating_add(area.height) {
                break;
            }
            if row.is_section_header {
                for span in &mut line.spans {
                    span.style = span.style.fg(theme.text_muted).add_modifier(Modifier::BOLD);
                }
            } else if selected {
                for span in &mut line.spans {
                    span.style = span.style.fg(theme.accent).add_modifier(Modifier::BOLD);
                }
            }
            line.render(
                Rect {
                    x: area.x,
                    y,
                    width: area.width,
                    height: 1,
                },
                buf,
            );
            y = y.saturating_add(1);
        }
    }
}

pub(crate) fn render_rows_single_line(
    area: Rect,
    buf: &mut Buffer,
    rows: &[DisplayRow],
    state: &ScrollState,
    max_rows: usize,
    empty_message: &str,
    theme: &ThemePalette,
) {
    if area.is_empty() {
        return;
    }
    if rows.is_empty() {
        Line::from(Span::styled(
            empty_message.to_string(),
            Style::default()
                .fg(theme.text_muted)
                .add_modifier(Modifier::ITALIC),
        ))
        .render(area, buf);
        return;
    }

    let (start, visible_items) = single_line_row_viewport(rows, state, max_rows, area.height);
    let desc_col = single_line_description_column(rows, start, visible_items, area.width);
    for (offset, (idx, row)) in rows
        .iter()
        .enumerate()
        .skip(start)
        .take(visible_items)
        .enumerate()
    {
        let y = area.y.saturating_add(offset as u16);
        if y >= area.y.saturating_add(area.height) {
            break;
        }
        let selected = row_is_selected(row, idx, rows, state);
        let mut line = single_line_row(row, desc_col, area.width);
        if row.is_section_header {
            for span in &mut line.spans {
                span.style = span.style.fg(theme.text_muted).add_modifier(Modifier::BOLD);
            }
        } else if selected {
            for span in &mut line.spans {
                span.style = span.style.fg(theme.accent).add_modifier(Modifier::BOLD);
            }
        }
        line.render(
            Rect {
                x: area.x,
                y,
                width: area.width,
                height: 1,
            },
            buf,
        );
    }
}

fn single_line_row(row: &DisplayRow, desc_col: usize, width: u16) -> Line<'static> {
    let mut name = Line::from(styled_name(row));
    if !row.description.is_empty() {
        name = truncate_line(name, desc_col.saturating_sub(2).max(1));
    }
    let name_width = name.width();
    let mut spans = name.spans;
    if !row.description.is_empty() && desc_col > name_width {
        spans.push(Span::raw(" ".repeat(desc_col - name_width)));
        spans.push(Span::styled(
            row.description.clone(),
            Style::default().fg(crate::style::POPUP_DESCRIPTION_COLOR),
        ));
    }
    truncate_line(Line::from(spans), width.max(1) as usize)
}

fn single_line_description_column(
    rows: &[DisplayRow],
    _start: usize,
    _visible_items: usize,
    width: u16,
) -> usize {
    let width = usize::from(width.max(1));
    let max_name_width = rows
        .iter()
        .map(|row| UnicodeWidthStr::width(row.name.as_str()))
        .max()
        .unwrap_or(0);
    max_name_width
        .saturating_add(2)
        .min(width.saturating_mul(7) / 10)
        .min(width)
}

pub(crate) fn truncate_line(line: Line<'static>, width: usize) -> Line<'static> {
    if line.width() <= width {
        return line;
    }
    let content_width = width.saturating_sub(1);
    let mut used = 0usize;
    let mut spans = Vec::new();
    let mut ellipsis_style = Style::default();
    'outer: for span in line.spans {
        ellipsis_style = span.style;
        for ch in span.content.chars() {
            let char_width = UnicodeWidthChar::width(ch).unwrap_or(0);
            if used.saturating_add(char_width) > content_width {
                break 'outer;
            }
            spans.push(Span::styled(ch.to_string(), span.style));
            used = used.saturating_add(char_width);
        }
    }
    spans.push(Span::styled("…", ellipsis_style));
    Line::from(spans)
}

fn viewport_start(rows: &[DisplayRow], state: &ScrollState, visible_items: usize) -> usize {
    let len = rows.len();
    if len == 0 || visible_items == 0 {
        return 0;
    }
    let uses_selection_indices = rows.iter().any(|row| row.selection_index.is_some());
    let mut start = if uses_selection_indices {
        rows.iter()
            .position(|row| {
                row.selection_index
                    .is_some_and(|index| index >= state.scroll_top)
            })
            .unwrap_or_else(|| len.saturating_sub(1))
    } else {
        state.scroll_top.min(len.saturating_sub(1))
    };
    if start > 0 && rows[start - 1].is_section_header {
        start -= 1;
    }
    if let Some(selected) = selected_display_index(rows, state).map(|idx| idx.min(len - 1)) {
        if selected < start {
            start = selected;
        } else {
            let bottom = start + visible_items - 1;
            if selected > bottom {
                start = selected + 1 - visible_items;
            }
        }
    }
    start.min(len.saturating_sub(visible_items))
}

fn selected_display_index(rows: &[DisplayRow], state: &ScrollState) -> Option<usize> {
    let selected = state.selected_idx?;
    if rows.iter().any(|row| row.selection_index.is_some()) {
        return rows
            .iter()
            .position(|row| row.selection_index == Some(selected));
    }
    Some(selected.min(rows.len().saturating_sub(1)))
}

fn row_is_selected(
    row: &DisplayRow,
    display_idx: usize,
    rows: &[DisplayRow],
    state: &ScrollState,
) -> bool {
    let Some(selected) = state.selected_idx else {
        return false;
    };
    if rows.iter().any(|row| row.selection_index.is_some()) {
        return row.selection_index == Some(selected);
    }
    display_idx == selected
}

fn description_column(
    rows: &[DisplayRow],
    start: usize,
    visible_items: usize,
    width: u16,
) -> usize {
    let max_desc_col = width.saturating_sub(1) as usize;
    if max_desc_col == 0 {
        return 0;
    }
    // Codex auto-width behavior caps the name column at 70% so descriptions
    // retain useful room even when one label is unusually long.
    let auto_cap = max_desc_col.min((usize::from(width) * 7 / 10).max(1));
    let max_name_width = rows
        .iter()
        .skip(start)
        .take(visible_items)
        .map(|row| UnicodeWidthStr::width(row.name.as_str()))
        .max()
        .unwrap_or(0);
    max_name_width.saturating_add(2).min(auto_cap)
}

fn wrap_row(row: &DisplayRow, desc_col: usize, width: u16) -> Vec<Line<'static>> {
    let width = width.max(1) as usize;
    if !row.description.is_empty() && width.saturating_sub(desc_col) < 16 {
        let mut lines = crate::wrapping::adaptive_wrap_text(row.name.as_str(), width)
            .into_iter()
            .map(Line::from)
            .collect::<Vec<_>>();
        let indent = "    ";
        let options = crate::wrapping::RtOptions::new(width)
            .initial_indent(Line::from(indent))
            .subsequent_indent(Line::from(indent))
            .wrap_algorithm(textwrap::WrapAlgorithm::FirstFit);
        lines.extend(
            crate::wrapping::adaptive_wrap_text_with(row.description.as_str(), options)
                .into_iter()
                .map(Line::from),
        );
        return lines;
    }
    if !row.description.is_empty()
        && row.match_indices.is_none()
        && !row.is_section_header
        && desc_col > 2
    {
        return wrap_two_column_row(row, desc_col, width);
    }
    let mut spans = styled_name(row);
    let name_width = UnicodeWidthStr::width(row.name.as_str());
    if !row.description.is_empty() && desc_col > 0 {
        let gap = desc_col.saturating_sub(name_width).max(2);
        spans.push(Span::raw(" ".repeat(gap)));
        spans.push(Span::styled(
            row.description.clone(),
            Style::default().fg(crate::style::POPUP_DESCRIPTION_COLOR),
        ));
    }
    let plain = Line::from(spans)
        .spans
        .iter()
        .map(|span| span.content.as_ref())
        .collect::<String>();
    let indent = if row.description.is_empty() {
        row.name
            .find(". ")
            .map(|index| UnicodeWidthStr::width(&row.name[..index + 2]))
            .unwrap_or(0)
            .min(width.saturating_sub(1))
    } else {
        desc_col.min(width.saturating_sub(1))
    };
    let subsequent_indent = " ".repeat(indent);
    let options = crate::wrapping::RtOptions::new(width)
        .initial_indent(Line::default())
        .subsequent_indent(Line::from(subsequent_indent))
        .wrap_algorithm(textwrap::WrapAlgorithm::FirstFit);
    crate::wrapping::adaptive_wrap_text_with(&plain, options)
        .into_iter()
        .map(Line::from)
        .collect()
}

fn wrap_two_column_row(row: &DisplayRow, desc_col: usize, width: usize) -> Vec<Line<'static>> {
    let desc_col = desc_col.clamp(1, width.saturating_sub(1));
    let left_width = desc_col.saturating_sub(2).max(1);
    let right_width = width.saturating_sub(desc_col).max(1);
    let prefix_width = row
        .name
        .find(". ")
        .map(|index| UnicodeWidthStr::width(&row.name[..index + 2]))
        .unwrap_or(0)
        .min(left_width.saturating_sub(1));
    let name_options = crate::wrapping::RtOptions::new(left_width)
        .initial_indent(Line::default())
        .subsequent_indent(Line::from(" ".repeat(prefix_width)))
        .wrap_algorithm(textwrap::WrapAlgorithm::FirstFit);
    let name_lines = crate::wrapping::adaptive_wrap_text_with(&row.name, name_options);
    let description_lines = crate::wrapping::adaptive_wrap_text(&row.description, right_width);
    let line_count = name_lines.len().max(description_lines.len()).max(1);

    (0..line_count)
        .map(|index| {
            let mut spans = Vec::new();
            let left = name_lines.get(index).cloned().unwrap_or_default();
            let left_width = UnicodeWidthStr::width(left.as_str());
            if !left.is_empty() {
                spans.push(Span::raw(left));
            }
            if let Some(description) = description_lines.get(index) {
                spans.push(Span::raw(" ".repeat(desc_col.saturating_sub(left_width))));
                spans.push(Span::styled(
                    description.clone(),
                    Style::default().fg(crate::style::POPUP_DESCRIPTION_COLOR),
                ));
            }
            Line::from(spans)
        })
        .collect()
}

fn wrapped_selection_header(
    title: &str,
    subtitle: Option<&str>,
    width: u16,
    theme: &ThemePalette,
) -> Vec<Line<'static>> {
    let width = width.max(1) as usize;
    let mut lines = crate::wrapping::adaptive_wrap_text(title, width)
        .into_iter()
        .map(|line| {
            Line::from(Span::styled(
                line,
                Style::default()
                    .fg(theme.bottom_pane().input_accent)
                    .add_modifier(Modifier::BOLD),
            ))
        })
        .collect::<Vec<_>>();
    if let Some(subtitle) = subtitle.filter(|value| !value.trim().is_empty()) {
        lines.extend(
            crate::wrapping::adaptive_wrap_text(subtitle, width)
                .into_iter()
                .map(|line| Line::from(Span::styled(line, Style::default().fg(theme.text_muted)))),
        );
    }
    lines
}

fn styled_name(row: &DisplayRow) -> Vec<Span<'static>> {
    let Some(indices) = row.match_indices.as_ref() else {
        return vec![Span::raw(row.name.clone())];
    };
    let mut spans = Vec::new();
    for (idx, ch) in row.name.chars().enumerate() {
        let style = if indices.contains(&idx) {
            Style::default().add_modifier(Modifier::BOLD)
        } else {
            Style::default()
        };
        spans.push(Span::styled(ch.to_string(), style));
    }
    spans
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn compact_process_selection_keeps_only_selected_state_evidence() {
        let mut view = SelectionViewState::new(
            "processes.unresolved".into(),
            "Background processes".into(),
            None,
            (0..12)
                .map(|n| SelectionViewRow {
                    id: n.to_string(),
                    name: format!("Command unavailable #{n}"),
                    selected_description:
                        "Last recorded: running\n2026-11-05T05:38:28Z\nCurrent state: unverified"
                            .into(),
                    ..Default::default()
                })
                .collect(),
            None,
            None,
            None,
        );
        assert!(view
            .display_rows(true)
            .iter()
            .all(|row| row.description.is_empty()));
        view.preserve_selected_description = true;
        for selected in [0, 5, 11] {
            view.state.selected_idx = Some(selected);
            let rows = view.display_rows(true);
            assert!(rows[selected]
                .description
                .contains("Current state: unverified"));
            assert!(rows[selected].description.contains("05:38:28Z"));
            assert_eq!(
                rows.iter()
                    .filter(|row| !row.description.is_empty())
                    .count(),
                1
            );
        }
    }

    #[test]
    fn match_indices_are_character_offsets_for_unicode_names() {
        let row = DisplayRow {
            name: "数据/main.rs".to_string(),
            description: String::new(),
            match_indices: Some(vec![0, 1]),
            selection_index: None,
            is_section_header: false,
        };

        let spans = styled_name(&row);

        assert_eq!(
            spans
                .iter()
                .map(|span| span.content.as_ref())
                .collect::<Vec<_>>(),
            vec!["数", "据", "/", "m", "a", "i", "n", ".", "r", "s"]
        );
        assert!(spans[0].style.add_modifier.contains(Modifier::BOLD));
        assert!(spans[1].style.add_modifier.contains(Modifier::BOLD));
        assert!(!spans[2].style.add_modifier.contains(Modifier::BOLD));
    }

    #[test]
    fn selection_item_carries_codex_style_row_metadata() {
        let item = SelectionItem {
            name: "model".to_string(),
            name_prefix: "/".to_string(),
            description: "Pick model".to_string(),
            selected_description: "Pick model and reasoning".to_string(),
            display_shortcut: Some("Tab".to_string()),
            is_disabled: false,
            disabled_reason: None,
            tab_id: Some("commands".to_string()),
            side_content: Some("gpt-5.5".to_string()),
            match_indices: Some(vec![1, 2]),
        };

        let row = item.to_display_row(true);

        assert_eq!(row.name, "/model");
        assert!(row.description.contains("Pick model and reasoning"));
        assert!(row.description.contains("Tab"));
        assert!(row.description.contains("gpt-5.5"));
        assert_eq!(row.match_indices, Some(vec![1, 2]));
    }

    #[test]
    fn disabled_selection_item_prefers_disabled_reason() {
        let item = SelectionItem {
            name: "sessions".to_string(),
            description: "Open sessions".to_string(),
            is_disabled: true,
            disabled_reason: Some("Unavailable while running".to_string()),
            ..SelectionItem::default()
        };

        let row = item.to_display_row(true);

        assert_eq!(row.description, "Unavailable while running");
    }

    #[test]
    fn selection_tab_groups_selection_items() {
        let tab = SelectionTab {
            id: "commands".to_string(),
            title: "Commands".to_string(),
            items: vec![SelectionItem {
                name: "status".to_string(),
                description: "Show status".to_string(),
                ..SelectionItem::default()
            }],
        };

        assert_eq!(tab.id, "commands");
        assert_eq!(tab.title, "Commands");
        assert_eq!(tab.items[0].name, "status");
    }

    #[test]
    fn selection_description_keeps_long_url_intact() {
        let url = "https://example.com/a-very-long-path-name";
        let row = DisplayRow {
            name: "  1. Open".to_string(),
            description: url.to_string(),
            match_indices: None,
            selection_index: Some(0),
            is_section_header: false,
        };

        let lines = wrap_row(&row, 10, 12);
        let description = lines
            .iter()
            .skip(1)
            .map(crate::wrapping::line_plain_text)
            .collect::<Vec<_>>();
        assert_eq!(description.len(), 1);
        assert_eq!(description[0].trim(), url);
    }

    #[test]
    fn wrapped_viewport_counts_items_that_fit_and_keeps_selection_visible() {
        let rows = (0..14)
            .map(|index| DisplayRow {
                name: format!("model-{index}"),
                description: format!("Model {index} description"),
                match_indices: None,
                selection_index: Some(index),
                is_section_header: false,
            })
            .collect::<Vec<_>>();
        let mut state = ScrollState {
            selected_idx: Some(0),
            scroll_top: 0,
        };

        let (start, visible) = wrapped_row_viewport(&rows, &state, MAX_POPUP_ROWS, 16, 8);
        assert_eq!((start, visible), (0, 2));
        assert_eq!(
            scroll_position(start, visible, rows.len()).as_deref(),
            Some("1-2/14")
        );

        state.selected_idx = Some(10);
        state.scroll_top = 3;
        let (start, visible) = wrapped_row_viewport(&rows, &state, MAX_POPUP_ROWS, 16, 8);
        assert!(
            start <= 10 && 10 < start + visible,
            "start={start} visible={visible}"
        );
        assert!(visible < MAX_POPUP_ROWS);
    }

    #[test]
    fn long_names_do_not_starve_the_description_column() {
        let rows = vec![DisplayRow {
            name: "› 1. A deliberately very long option label that must wrap".to_string(),
            description: "Description remains independently readable in its own column."
                .to_string(),
            match_indices: None,
            selection_index: Some(0),
            is_section_header: false,
        }];

        let desc_col = description_column(&rows, 0, 1, 80);
        let lines = wrap_row(&rows[0], desc_col, 80);

        assert!(desc_col <= 56, "desc_col={desc_col}");
        assert!(lines.len() >= 2);
        assert!(lines
            .iter()
            .map(crate::wrapping::line_plain_text)
            .any(|line| line.contains("Description remains")));
    }
}
