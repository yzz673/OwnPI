use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::Widget;

use crate::bottom_pane::{
    measure_rows_height, menu_footer_lines, menu_surface_content_width,
    menu_surface_padding_height, render_menu_surface, render_rows, scroll_position,
    wrapped_row_viewport, DisplayRow, ScrollState, MAX_POPUP_ROWS,
};
use crate::theme::ThemePalette;

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct OptionPickerState {
    pub(crate) picker_kind: String,
    pub(crate) title: String,
    pub(crate) subtitle: Option<String>,
    pub(crate) options: Vec<String>,
    pub(crate) option_values: Vec<String>,
    pub(crate) option_descriptions: Vec<String>,
    pub(crate) current_value: Option<String>,
    pub(crate) submit_command: String,
    state: ScrollState,
}

impl OptionPickerState {
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn new(
        picker_kind: String,
        title: String,
        subtitle: Option<String>,
        options: Vec<String>,
        option_values: Vec<String>,
        option_descriptions: Vec<String>,
        current_value: Option<String>,
        submit_command: String,
    ) -> Self {
        let selected = current_value
            .as_ref()
            .and_then(|value| options.iter().position(|item| item == value))
            .unwrap_or(0);
        let selected_idx = (!options.is_empty()).then_some(selected);
        Self {
            picker_kind,
            title,
            subtitle,
            options,
            option_values,
            option_descriptions,
            current_value,
            submit_command,
            state: ScrollState {
                selected_idx,
                scroll_top: 0,
            },
        }
    }

    pub(crate) fn is_visible(&self) -> bool {
        !self.options.is_empty()
    }

    pub(crate) fn move_up(&mut self) {
        self.state.move_up_wrap(self.options.len());
        self.state
            .ensure_visible(self.options.len(), self.options.len().min(MAX_POPUP_ROWS));
    }

    pub(crate) fn move_down(&mut self) {
        self.state.move_down_wrap(self.options.len());
        self.state
            .ensure_visible(self.options.len(), self.options.len().min(MAX_POPUP_ROWS));
    }

    pub(crate) fn page_up(&mut self) {
        self.state
            .page_up_clamped(self.options.len(), MAX_POPUP_ROWS);
    }

    pub(crate) fn page_down(&mut self) {
        self.state
            .page_down_clamped(self.options.len(), MAX_POPUP_ROWS);
    }

    pub(crate) fn jump_top(&mut self) {
        self.state.jump_top(self.options.len(), MAX_POPUP_ROWS);
    }

    pub(crate) fn jump_bottom(&mut self) {
        self.state.jump_bottom(self.options.len(), MAX_POPUP_ROWS);
    }

    /// Codex list_selection_view.rs:894-918 parity: number keys jump to the
    /// Nth option before accepting.
    pub(crate) fn select_index(&mut self, idx: usize) {
        if idx < self.options.len() {
            self.state.selected_idx = Some(idx);
            self.state
                .ensure_visible(self.options.len(), self.options.len().min(MAX_POPUP_ROWS));
        }
    }

    pub(crate) fn submission_text(&self) -> Option<String> {
        self.state.selected_idx.and_then(|idx| {
            let value = self
                .option_values
                .get(idx)
                .filter(|value| !value.trim().is_empty())
                .or_else(|| self.options.get(idx))?;
            Some(format!("{} {}", self.submit_command, value))
        })
    }

    pub(crate) fn desired_height(&self, width: u16) -> u16 {
        if !self.is_visible() {
            return 0;
        }
        let content_width = menu_surface_content_width(width);
        let rows = self.display_rows();
        let header_rows = self
            .header_lines(content_width, &ThemePalette::default())
            .len() as u16;
        let rows_height = measure_rows_height(&rows, &self.state, MAX_POPUP_ROWS, content_width);
        let (start, visible_items) = wrapped_row_viewport(
            &rows,
            &self.state,
            MAX_POPUP_ROWS,
            content_width,
            rows_height,
        );
        let position = scroll_position(start, visible_items, self.options.len());
        let footer_rows = menu_footer_lines(
            &option_picker_hint(&ThemePalette::default(), content_width),
            position.as_deref(),
            content_width,
            &ThemePalette::default(),
        )
        .len() as u16;
        header_rows
            .saturating_add(1)
            .saturating_add(rows_height)
            .saturating_add(1)
            .saturating_add(footer_rows)
            .saturating_add(menu_surface_padding_height())
            .clamp(5, 18)
    }

    pub(crate) fn render(&self, area: Rect, buf: &mut Buffer, theme: &ThemePalette) {
        if !self.is_visible() || area.is_empty() {
            return;
        }
        let vertical_padding = area.height >= self.desired_height(area.width);
        let area = render_menu_surface(area, buf, theme, vertical_padding);
        if area.is_empty() {
            return;
        }
        let rows = self.display_rows();
        let header_lines = self.header_lines(area.width, theme);
        let mut y = area.y;
        let bottom = area.y.saturating_add(area.height);
        for line in header_lines {
            if y >= bottom {
                return;
            }
            line.render(Rect::new(area.x, y, area.width, 1), buf);
            y = y.saturating_add(1);
        }
        y = y.saturating_add(1);

        let hint = option_picker_hint(theme, area.width);
        let mut footer_lines = menu_footer_lines(&hint, None, area.width, theme);
        let mut footer_height = footer_lines.len() as u16;
        let mut rows_area = Rect::new(
            area.x,
            y,
            area.width,
            bottom
                .saturating_sub(y)
                .saturating_sub(footer_height)
                .saturating_sub(1),
        );
        let (mut start, mut visible_items) = wrapped_row_viewport(
            &rows,
            &self.state,
            MAX_POPUP_ROWS,
            rows_area.width,
            rows_area.height,
        );
        let mut position = scroll_position(start, visible_items, self.options.len());
        footer_lines = menu_footer_lines(&hint, position.as_deref(), area.width, theme);
        if footer_lines.len() as u16 != footer_height {
            footer_height = footer_lines.len() as u16;
            rows_area.height = bottom
                .saturating_sub(y)
                .saturating_sub(footer_height)
                .saturating_sub(1);
            (start, visible_items) = wrapped_row_viewport(
                &rows,
                &self.state,
                MAX_POPUP_ROWS,
                rows_area.width,
                rows_area.height,
            );
            position = scroll_position(start, visible_items, self.options.len());
            footer_lines = menu_footer_lines(&hint, position.as_deref(), area.width, theme);
        }
        render_rows(
            rows_area,
            buf,
            &rows,
            &self.state,
            MAX_POPUP_ROWS,
            "no options",
            theme,
        );
        let footer_y = bottom.saturating_sub(footer_height);
        for (offset, line) in footer_lines.into_iter().enumerate() {
            line.render(
                Rect::new(
                    area.x,
                    footer_y.saturating_add(offset as u16),
                    area.width,
                    1,
                ),
                buf,
            );
        }
    }

    fn header_lines(&self, width: u16, theme: &ThemePalette) -> Vec<Line<'static>> {
        let mut lines = crate::wrapping::adaptive_wrap_text(&self.title, usize::from(width.max(1)))
            .into_iter()
            .map(|text| {
                Line::from(Span::styled(
                    text,
                    Style::default()
                        .fg(theme.bottom_pane().input_accent)
                        .add_modifier(Modifier::BOLD),
                ))
            })
            .collect::<Vec<_>>();
        if let Some(subtitle) = self
            .subtitle
            .as_deref()
            .filter(|value| !value.trim().is_empty())
        {
            lines.extend(
                crate::wrapping::adaptive_wrap_text(subtitle, usize::from(width.max(1)))
                    .into_iter()
                    .map(|text| {
                        Line::from(Span::styled(text, Style::default().fg(theme.text_muted)))
                    }),
            );
        }
        lines
    }

    fn display_rows(&self) -> Vec<DisplayRow> {
        self.options
            .iter()
            .enumerate()
            .map(|(idx, option)| {
                let mut name = option.clone();
                if self.current_value.as_deref() == Some(option.as_str()) {
                    name.push_str(" (current)");
                }
                DisplayRow {
                    name,
                    description: self
                        .option_descriptions
                        .get(idx)
                        .cloned()
                        .unwrap_or_default(),
                    match_indices: None,
                    selection_index: None,
                    is_section_header: false,
                }
            })
            .collect()
    }
}

fn option_picker_hint(theme: &ThemePalette, width: u16) -> Line<'static> {
    let text = if width >= 44 {
        "Up/Down move  Enter select  Esc close"
    } else {
        "Up/Dn Enter Esc"
    };
    Line::from(Span::styled(text, Style::default().fg(theme.text_muted)))
}
