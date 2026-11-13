use std::cell::Cell;

use crossterm::event::{KeyCode, KeyEvent};
use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span, Text};
use ratatui::widgets::{Paragraph, Widget};

use crate::theme::ThemePalette;

use super::menu_surface::{
    menu_footer_lines, menu_surface_content_width, menu_surface_padding_height,
    render_menu_surface, scroll_position,
};

const MIN_VIEW_HEIGHT: u16 = 8;
const MAX_VIEW_HEIGHT: u16 = 20;

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct CommandDocumentViewState {
    pub(crate) view_kind: String,
    pub(crate) title: String,
    pub(crate) command: String,
    pub(crate) body_markdown: String,
    pub(crate) source: String,
    scroll_top: usize,
    last_total_rows: Cell<usize>,
    last_viewport_rows: Cell<usize>,
}

impl CommandDocumentViewState {
    pub(crate) fn new(
        view_kind: String,
        title: String,
        command: String,
        body_markdown: String,
        source: String,
    ) -> Self {
        Self {
            view_kind,
            title,
            command,
            body_markdown,
            source,
            scroll_top: 0,
            last_total_rows: Cell::new(0),
            last_viewport_rows: Cell::new(0),
        }
    }

    pub(crate) fn is_visible(&self) -> bool {
        !self.title.trim().is_empty() || !self.body_markdown.trim().is_empty()
    }

    pub(crate) fn desired_height(&self, width: u16, theme: &ThemePalette) -> u16 {
        let content_width = usize::from(menu_surface_content_width(width));
        let rows = crate::markdown::render_markdown(&self.display_body(), content_width, theme);
        let footer_rows = menu_footer_lines(
            &command_document_hint(theme, content_width as u16),
            None,
            content_width as u16,
            theme,
        )
        .len();
        rows.len()
            .saturating_add(4 + footer_rows + usize::from(menu_surface_padding_height()))
            .try_into()
            .unwrap_or(u16::MAX)
            .clamp(MIN_VIEW_HEIGHT, MAX_VIEW_HEIGHT)
    }

    pub(crate) fn handle_key(&mut self, key: KeyEvent) -> bool {
        let page = self.last_viewport_rows.get().max(1);
        let max_scroll = self
            .last_total_rows
            .get()
            .saturating_sub(self.last_viewport_rows.get());
        match key.code {
            KeyCode::Up => self.scroll_top = self.scroll_top.saturating_sub(1),
            KeyCode::Down => self.scroll_top = self.scroll_top.saturating_add(1).min(max_scroll),
            KeyCode::PageUp => self.scroll_top = self.scroll_top.saturating_sub(page),
            KeyCode::PageDown => {
                self.scroll_top = self.scroll_top.saturating_add(page).min(max_scroll)
            }
            KeyCode::Home => self.scroll_top = 0,
            KeyCode::End => self.scroll_top = max_scroll,
            _ => return false,
        }
        true
    }

    pub(crate) fn scroll_up(&mut self, rows: usize) {
        self.scroll_top = self.scroll_top.saturating_sub(rows);
    }

    pub(crate) fn scroll_down(&mut self, rows: usize) {
        let max_scroll = self
            .last_total_rows
            .get()
            .saturating_sub(self.last_viewport_rows.get());
        self.scroll_top = self.scroll_top.saturating_add(rows).min(max_scroll);
    }

    pub(crate) fn render(&self, area: Rect, buf: &mut Buffer, theme: &ThemePalette) {
        if area.is_empty() {
            return;
        }
        let vertical_padding = area.height >= self.desired_height(area.width, theme);
        let inner = render_menu_surface(area, buf, theme, vertical_padding);
        if inner.is_empty() {
            return;
        }
        let palette = theme.bottom_pane();
        Paragraph::new(Line::from(Span::styled(
            self.title.trim().to_string(),
            Style::default()
                .fg(palette.input_accent)
                .add_modifier(Modifier::BOLD),
        )))
        .render(Rect::new(inner.x, inner.y, inner.width, 1), buf);

        let command_area = Rect::new(
            inner.x,
            inner.y.saturating_add(1),
            inner.width,
            u16::from(inner.height > 1),
        );
        let mut command_spans = vec![Span::styled(
            self.command.clone(),
            Style::default().fg(theme.text_muted),
        )];
        if command_area.width >= 32 {
            command_spans.push(Span::styled(
                command_kind_label(&self.view_kind),
                Style::default().fg(palette.input_muted),
            ));
        }
        Paragraph::new(Line::from(command_spans)).render(command_area, buf);

        let body_lines = crate::markdown::render_markdown(
            &self.display_body(),
            usize::from(inner.width.max(1)),
            theme,
        );
        let hint = command_document_hint(theme, inner.width);
        let mut footer_lines = menu_footer_lines(&hint, None, inner.width, theme);
        let mut footer_height = footer_lines.len() as u16;
        let body_y = inner.y.saturating_add(3);
        let mut body_height = inner.height.saturating_sub(4).saturating_sub(footer_height);
        let mut viewport_rows = usize::from(body_height);
        let mut max_scroll = body_lines.len().saturating_sub(viewport_rows);
        let mut scroll_top = self.scroll_top.min(max_scroll);
        let mut position = scroll_position(scroll_top, viewport_rows, body_lines.len());
        footer_lines = menu_footer_lines(&hint, position.as_deref(), inner.width, theme);
        if footer_lines.len() as u16 != footer_height {
            footer_height = footer_lines.len() as u16;
            body_height = inner.height.saturating_sub(4).saturating_sub(footer_height);
            viewport_rows = usize::from(body_height);
            max_scroll = body_lines.len().saturating_sub(viewport_rows);
            scroll_top = self.scroll_top.min(max_scroll);
            position = scroll_position(scroll_top, viewport_rows, body_lines.len());
            footer_lines = menu_footer_lines(&hint, position.as_deref(), inner.width, theme);
        }
        let body_area = Rect::new(inner.x, body_y, inner.width, body_height);
        self.last_total_rows.set(body_lines.len());
        self.last_viewport_rows.set(viewport_rows);
        Paragraph::new(Text::from(body_lines))
            .scroll((scroll_top.try_into().unwrap_or(u16::MAX), 0))
            .render(body_area, buf);

        let footer_y = inner
            .y
            .saturating_add(inner.height.saturating_sub(footer_height));
        for (offset, line) in footer_lines.into_iter().enumerate() {
            line.render(
                Rect::new(
                    inner.x,
                    footer_y.saturating_add(offset as u16),
                    inner.width,
                    1,
                ),
                buf,
            );
        }
    }

    fn display_body(&self) -> String {
        let mut lines = self.body_markdown.lines();
        let Some(first) = lines.next() else {
            return String::new();
        };
        let heading = first.trim().strip_prefix('#').map(str::trim);
        if heading.is_some_and(|heading| heading.eq_ignore_ascii_case(self.title.trim())) {
            return lines
                .collect::<Vec<_>>()
                .join("\n")
                .trim_start()
                .to_string();
        }
        self.body_markdown.clone()
    }
}

fn command_document_hint(theme: &ThemePalette, width: u16) -> Line<'static> {
    let text = if width >= 64 {
        "Up/Down scroll  PgUp/PgDn page  Esc close"
    } else if width >= 32 {
        "Up/Dn  PgUp/PgDn  Esc"
    } else {
        "Up/Dn Esc"
    };
    Line::from(Span::styled(text, Style::default().fg(theme.text_muted)))
}

fn command_kind_label(view_kind: &str) -> &'static str {
    match view_kind {
        "command_catalog" => "  Command reference",
        "runtime_inspector" => "  Runtime inspector",
        "capability_catalog" => "  Capability catalog",
        "configuration" => "  Configuration",
        "status" => "  Session status",
        "session_browser" => "  Session browser",
        "action_result" => "  Command result",
        "task_header" => "  Interpreted task",
        "task_mode" => "  Task mode",
        "run_browser" => "  Workflow runs",
        "context_inspector" => "  Context inspector",
        "memory_browser" => "  Memory",
        "run_inspector" => "  Run inspector",
        "candidate_browser" => "  Candidates",
        "extensions" => "  Extensions",
        "hooks" => "  Hooks",
        "model_picker" => "  Models",
        "reasoning_picker" => "  Reasoning",
        _ => "  Command output",
    }
}
