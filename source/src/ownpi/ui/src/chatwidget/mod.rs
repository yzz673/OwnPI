use ratatui::buffer::Buffer;
use ratatui::layout::{Constraint, Direction, Layout, Rect};
use ratatui::style::Style;
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Clear};

use crate::app::AppState;
use crate::bottom_pane::footer::{render_footer_from_props, FooterProps};
use crate::status::widget::render_status_widget;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct ChatLayout {
    pub(crate) history: Rect,
    pub(crate) status_gap: Option<Rect>,
    pub(crate) status: Option<Rect>,
    pub(crate) composer: Rect,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct ChatWidgetRenderResult {
    pub(crate) history_area: Rect,
    pub(crate) composer_area: Rect,
    pub(crate) popup_area: Option<Rect>,
    pub(crate) cursor: Option<(u16, u16)>,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
struct StatusSurfaceLayout {
    leading_gap: u16,
    status_rows: u16,
}

impl StatusSurfaceLayout {
    fn measure(app: &AppState, width: u16, transcript_precedes_status: bool) -> Self {
        let status_rows = status_height_for_layout(app, width);
        let leading_gap = u16::from(
            status_rows > 0 && transcript_precedes_status && status_needs_boundary_gap(app),
        );
        Self {
            leading_gap,
            status_rows,
        }
    }

    fn total_rows(self) -> u16 {
        self.leading_gap.saturating_add(self.status_rows)
    }
}

pub(crate) struct ChatWidget<'a> {
    app: &'a AppState,
    footer_props: FooterProps,
    footer_rows: u16,
}

impl<'a> ChatWidget<'a> {
    pub(crate) fn new(app: &'a AppState, footer_props: FooterProps, footer_rows: u16) -> Self {
        Self {
            app,
            footer_props,
            footer_rows,
        }
    }

    pub(crate) fn layout(&self, area: Rect) -> ChatLayout {
        chat_layout(self.app, area, self.footer_rows)
    }

    pub(crate) fn render(&self, area: Rect, buf: &mut Buffer) -> ChatWidgetRenderResult {
        let layout = self.layout(area);
        self.render_history(layout.history, buf);
        if let Some(status_gap) = layout.status_gap {
            ratatui::widgets::Widget::render(Clear, status_gap, buf);
        }
        if let Some(status_area) = layout.status {
            self.render_status(status_area, buf);
        }
        let (cursor, popup_area) = self.render_bottom_pane(layout.composer, buf);
        ChatWidgetRenderResult {
            history_area: layout.history,
            composer_area: layout.composer,
            popup_area,
            cursor,
        }
    }

    fn render_history(&self, area: Rect, buf: &mut Buffer) {
        ratatui::widgets::Widget::render(Clear, area, buf);
        let all_lines = chat_history_lines(self.app, area.width.saturating_sub(1) as usize);
        let overflow = all_lines.len().saturating_sub(area.height as usize);
        let y = if should_top_anchor_history(self.app) {
            0
        } else {
            overflow
        };
        crate::history_cell::render_styled_lines(
            all_lines,
            area,
            buf,
            Style::default(),
            y.try_into().unwrap_or(u16::MAX),
            None,
        );
    }

    fn render_status(&self, area: Rect, buf: &mut Buffer) {
        ratatui::widgets::Widget::render(Clear, area, buf);
        render_status_widget(&self.app.status, area, buf, &self.app.theme);
    }

    fn render_bottom_pane(
        &self,
        area: Rect,
        buf: &mut Buffer,
    ) -> (Option<(u16, u16)>, Option<Rect>) {
        ratatui::widgets::Widget::render(Clear, area, buf);
        let bottom_pane_palette = self.app.theme.bottom_pane();
        let active_bottom_view = self.app.bottom_pane.active_bottom_view_is_visible();
        let preview_rows = 0;
        let composer_width = area.width;
        // Codex parity: measure the bottom view at the exact width it will
        // render at (height and content from one width). Previously the
        // height was measured at `area.width - 4` while the view rendered at
        // `area.width - 2*gutter - 4`, under-allocating wrapped overlays.
        let horizontal_gutter: u16 = if active_bottom_view
            && self.app.bottom_pane.human_question_bottom_view_is_visible()
            && area.width > 2
        {
            1
        } else {
            0
        };
        let bottom_view_width = area
            .width
            .saturating_sub(horizontal_gutter.saturating_mul(2));
        let input_rows = self
            .app
            .bottom_pane
            .bottom_view_height(bottom_view_width, &self.app.theme)
            .unwrap_or_else(|| self.app.bottom_pane.composer_height(composer_width));
        let input_block_rows = if active_bottom_view {
            input_rows.saturating_add(1)
        } else {
            input_rows.max(1).saturating_add(2)
        };
        let popup_rows = if active_bottom_view {
            0
        } else {
            self.app
                .bottom_pane
                .active_popup_height(area.width.saturating_sub(4))
        };
        let footer_rows = self.footer_rows;
        let chunks = Layout::default()
            .direction(Direction::Vertical)
            .constraints([
                Constraint::Length(preview_rows),
                Constraint::Length(u16::from(preview_rows > 0)),
                Constraint::Length(input_block_rows),
                Constraint::Length(popup_rows),
                Constraint::Length(footer_rows),
            ])
            .split(area);
        if active_bottom_view {
            ratatui::widgets::Widget::render(Clear, chunks[2], buf);
            let bottom_view_area = Rect {
                x: chunks[2].x.saturating_add(horizontal_gutter),
                y: chunks[2].y.saturating_add(1),
                width: chunks[2]
                    .width
                    .saturating_sub(horizontal_gutter.saturating_mul(2)),
                height: chunks[2].height.saturating_sub(1),
            };
            self.app
                .bottom_pane
                .render_bottom_view(bottom_view_area, buf, &self.app.theme);
            let cursor = self.bottom_view_cursor_position(bottom_view_area);
            if footer_rows > 0 {
                render_footer_from_props(chunks[4], buf, &self.footer_props, bottom_pane_palette);
            }
            return (cursor, None);
        }
        let composer_block = Block::default().style(
            Style::default()
                .fg(bottom_pane_palette.input_text)
                .bg(bottom_pane_palette.input_bg),
        );
        ratatui::widgets::Widget::render(composer_block, chunks[2], buf);
        // Keep the accent rail visually continuous across the composer.
        let edge = Span::styled(
            "▏",
            Style::default()
                .fg(bottom_pane_palette.input_accent)
                .bg(bottom_pane_palette.input_bg),
        );
        for y in chunks[2].y..chunks[2].y.saturating_add(chunks[2].height) {
            buf.set_span(chunks[2].x, y, &edge, 1);
        }
        let input_content_area = vertically_centered_area(chunks[2], input_rows);
        self.app.bottom_pane.render_composer(
            input_content_area,
            buf,
            &self.app.theme,
            bottom_pane_palette,
        );
        let popup_area = if popup_rows > 0 {
            self.app
                .bottom_pane
                .render_active_popup(chunks[3], buf, &self.app.theme);
            Some(chunks[3])
        } else {
            None
        };
        if footer_rows > 0 {
            render_footer_from_props(chunks[4], buf, &self.footer_props, bottom_pane_palette);
        }
        let cursor = self
            .app
            .bottom_pane
            .composer_cursor_position(input_content_area)
            .or(Some((input_content_area.x, input_content_area.y)));
        (cursor, popup_area)
    }

    fn bottom_view_cursor_position(&self, area: Rect) -> Option<(u16, u16)> {
        self.app.bottom_pane.bottom_view_cursor_position(area)
    }
}

pub(crate) fn chat_layout(app: &AppState, area: Rect, footer_rows: u16) -> ChatLayout {
    let history_lines = chat_history_lines(app, area.width.saturating_sub(1) as usize);
    let status_surface = StatusSurfaceLayout::measure(
        app,
        area.width,
        transcript_precedes_status(app, !history_lines.is_empty()),
    );
    let reserve_status_row = status_surface.status_rows > 0 && area.height > 1;
    let desired_status_gap = if reserve_status_row {
        status_surface.leading_gap
    } else {
        0
    };
    let reserved_live_rows = u16::from(reserve_status_row).saturating_add(desired_status_gap);
    let max_composer_height = area.height.saturating_sub(reserved_live_rows);
    let composer_height =
        bottom_stack_height(app, area.width, footer_rows).min(max_composer_height);
    let status_gap_height = desired_status_gap.min(area.height.saturating_sub(composer_height));
    let status_height = status_surface.status_rows.min(
        area.height
            .saturating_sub(composer_height)
            .saturating_sub(status_gap_height),
    );
    let available_history_height = area
        .height
        .saturating_sub(status_gap_height)
        .saturating_sub(status_height)
        .saturating_sub(composer_height);
    // Keep the composer directly after short history in every conversation
    // state. Once history exceeds the viewport this naturally saturates at the
    // available height and scrolls, without a first-message layout-mode jump.
    let history_height = history_lines
        .len()
        .try_into()
        .unwrap_or(u16::MAX)
        .min(available_history_height);
    let history = Rect {
        x: area.x,
        y: area.y,
        width: area.width,
        height: history_height,
    };
    let status_gap = Rect {
        x: area.x,
        y: history.y.saturating_add(history.height),
        width: area.width,
        height: status_gap_height,
    };
    let status = Rect {
        x: area.x,
        y: status_gap.y.saturating_add(status_gap.height),
        width: area.width,
        height: status_height,
    };
    let composer = Rect {
        x: area.x,
        y: status.y.saturating_add(status.height),
        width: area.width,
        height: composer_height,
    };
    ChatLayout {
        history,
        status_gap: (status_gap_height > 0).then_some(status_gap),
        status: (status_height > 0).then_some(status),
        composer,
    }
}

fn bottom_stack_height(app: &AppState, width: u16, footer_rows: u16) -> u16 {
    let active_bottom_view = app.bottom_pane.active_bottom_view_is_visible();
    let preview_height: u16 = 0;
    let preview_gap: u16 = 0;
    let input_height = if active_bottom_view {
        app.bottom_pane
            .bottom_view_height(width, &app.theme)
            .unwrap_or(8)
            .saturating_add(1)
    } else {
        app.bottom_pane
            .composer_height(width)
            .max(1)
            .saturating_add(2)
    };
    let popup_height = if active_bottom_view {
        0
    } else {
        app.bottom_pane.active_popup_height(width.saturating_sub(4))
    };
    preview_height
        .saturating_add(preview_gap)
        .saturating_add(input_height)
        .saturating_add(popup_height)
        .saturating_add(footer_rows)
}

pub(crate) fn chat_desired_height(app: &AppState, width: u16, footer_rows: u16) -> u16 {
    let visible_history_height =
        chat_history_lines(app, width.saturating_sub(1) as usize).len() as u16;
    let history_height = visible_history_height;
    let status_surface = StatusSurfaceLayout::measure(
        app,
        width,
        transcript_precedes_status(app, visible_history_height > 0),
    );
    let composer_height = bottom_stack_height(app, width, footer_rows);
    status_surface
        .total_rows()
        .saturating_add(composer_height)
        .saturating_add(history_height)
        .max(composer_height)
}

fn status_height_for_layout(app: &AppState, width: u16) -> u16 {
    if app.bottom_pane.permission_overlay_is_visible()
        || app.bottom_pane.suppress_human_question_status()
    {
        return 0;
    }
    // The status row (spinner + elapsed) stays visible while answer lines
    // stream: hiding it per Codex chatwidget.rs:4266-4274 made the elapsed
    // timer vanish mid-turn and jump on reappear (reported by users).
    app.status.render_lines(width, &app.theme).len() as u16
}

fn status_needs_boundary_gap(app: &AppState) -> bool {
    matches!(
        app.status.kind,
        crate::status::StatusKind::Running
            | crate::status::StatusKind::InterruptRequested
            | crate::status::StatusKind::PendingPermission
            | crate::status::StatusKind::PermissionAnswered
            | crate::status::StatusKind::PendingClarification
            | crate::status::StatusKind::ActionRequired
            | crate::status::StatusKind::ControllerHandoff
    )
}

fn transcript_precedes_status(app: &AppState, viewport_has_history: bool) -> bool {
    viewport_has_history || app.has_visible_history_or_session_surface()
}

fn should_top_anchor_history(app: &AppState) -> bool {
    !app.has_committed_history_before_live() && app.session_surface.is_some()
}

pub(crate) fn chat_history_lines(app: &AppState, width: usize) -> Vec<Line<'static>> {
    let has_committed_history = app.has_committed_history_before_live();
    let mut lines = if has_committed_history {
        Vec::new()
    } else {
        app.session_surface
            .as_ref()
            .map(|surface| crate::history_cell::render_session_surface(surface, width, &app.theme))
            .unwrap_or_default()
    };
    lines.extend(app.submission_lines(width));
    let stream_lines = app.active_agent_stream_lines(width);
    if !stream_lines.is_empty() {
        let has_previous_content =
            has_committed_history || lines.iter().any(|line| !line_is_plain_blank(line));
        let already_has_boundary = lines.last().is_some_and(line_is_plain_blank);
        if has_previous_content && !already_has_boundary && !app.live_joins_audit_region() {
            lines.push(Line::from(String::new()));
        }
        lines.extend(stream_lines);
    }
    lines
}

fn line_is_plain_blank(line: &Line<'_>) -> bool {
    line.spans
        .iter()
        .all(|span| span.content.as_ref().trim().is_empty())
}

pub(crate) fn vertically_centered_area(area: Rect, content_height: u16) -> Rect {
    let height = content_height.max(1).min(area.height.max(1));
    let top_padding = area.height.saturating_sub(height) / 2;
    Rect {
        x: area.x,
        y: area.y.saturating_add(top_padding),
        width: area.width,
        height,
    }
}
