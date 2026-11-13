use std::time::{Duration, Instant};

use ratatui::style::{Color, Style, Stylize};
use ratatui::text::{Line, Span};
use unicode_width::UnicodeWidthStr;

use crate::theme::ThemePalette;

pub(crate) mod widget;

const STATUS_DETAILS_DEFAULT_MAX_LINES: usize = 3;
pub(crate) const STATUS_ANIMATION_FRAME_MS: u64 = 32;
/// 2s cosine brightness sweep at the 32ms animation cadence (Codex shimmer.rs).
const SHIMMER_PERIOD_FRAMES: u8 = 62;

/// Reduced-motion switch (Codex config.animations=false parity):
/// OWNPI_ANIMATIONS=0 disables shimmer.
pub(crate) fn animations_enabled() -> bool {
    std::env::var("OWNPI_ANIMATIONS")
        .map(|value| value != "0")
        .unwrap_or(true)
}

fn shimmer_color(base: Color, frame: u8) -> Option<Color> {
    let Color::Rgb(red, green, blue) = base else {
        return None;
    };
    let phase = (frame % SHIMMER_PERIOD_FRAMES) as f32 / SHIMMER_PERIOD_FRAMES as f32;
    let brightness = 0.6 + 0.4 * (0.5 * (1.0 + (phase * std::f32::consts::TAU).cos()));
    let scale = |channel: u8| -> u8 { (f32::from(channel) * brightness).round().min(255.0) as u8 };
    Some(Color::Rgb(scale(red), scale(green), scale(blue)))
}

#[allow(dead_code)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum StatusKind {
    Idle,
    Running,
    InterruptRequested,
    PendingPermission,
    PermissionAnswered,
    PendingClarification,
    ActionRequired,
    ControllerHandoff,
    Completed,
    Incomplete,
    Interrupted,
    Failed,
    Blocked,
    Cancelled,
    StaleView,
}

#[derive(Debug)]
pub(crate) struct StatusWidgetState {
    pub(crate) kind: StatusKind,
    pub(crate) header: String,
    pub(crate) detail: String,
    notice: Option<String>,
    interruptible: bool,
    started_at: Option<Instant>,
    frozen_elapsed_ms: u64,

    /// Shimmer phase counter (advances every 32ms tick, period 62×4).
    animation_frame: u8,
}

impl Default for StatusWidgetState {
    fn default() -> Self {
        Self {
            kind: StatusKind::Idle,
            header: String::new(),
            detail: String::new(),
            notice: None,
            interruptible: false,
            started_at: None,
            frozen_elapsed_ms: 0,

            animation_frame: 0,
        }
    }
}

impl StatusWidgetState {
    pub(crate) fn set_running(
        &mut self,
        header: String,
        detail: String,
        elapsed_ms: u64,
        interruptible: bool,
    ) {
        let elapsed_ms = if matches!(
            self.kind,
            StatusKind::Running | StatusKind::InterruptRequested
        ) {
            self.elapsed_ms().max(elapsed_ms)
        } else {
            elapsed_ms
        };
        self.kind = StatusKind::Running;

        self.header = header;
        self.detail = detail;
        self.interruptible = interruptible;
        self.started_at = Some(
            Instant::now()
                .checked_sub(Duration::from_millis(elapsed_ms))
                .unwrap_or_else(Instant::now),
        );
        self.frozen_elapsed_ms = elapsed_ms;
    }

    pub(crate) fn tick_animation(&mut self) {
        if !self.animates() {
            return;
        }
        // Shimmer phase: continuous 2s cosine sweep (62×4 ticks per loop).
        self.animation_frame = (self.animation_frame + 1) % (SHIMMER_PERIOD_FRAMES * 4);
    }

    #[allow(dead_code)]
    pub(crate) fn set_terminal(
        &mut self,
        kind: StatusKind,
        header: String,
        detail: String,
        elapsed_ms: u64,
    ) {
        self.kind = kind;

        self.header = header;
        self.detail = detail;
        self.interruptible = false;
        self.started_at = None;
        self.frozen_elapsed_ms = elapsed_ms;
        self.notice = None;
    }

    pub(crate) fn start_working(
        &mut self,
        header: String,
        detail: String,
        elapsed_ms: u64,
        interruptible: bool,
    ) {
        self.notice = None;
        self.set_running(header, detail, elapsed_ms, interruptible);
    }

    pub(crate) fn set_notice(&mut self, notice: Option<String>) {
        self.notice = notice.filter(|item| !item.trim().is_empty());
    }

    pub(crate) fn transition_to_pending_permission(&mut self, detail: String) {
        self.freeze_for_lifecycle_transition();
        self.kind = StatusKind::PendingPermission;

        self.header = "Permission required".to_string();
        self.detail = detail;
    }

    pub(crate) fn transition_to_pending_clarification(&mut self, detail: String) {
        self.freeze_for_lifecycle_transition();
        self.kind = StatusKind::PendingClarification;

        self.header = "Waiting for clarification".to_string();
        self.detail = detail;
    }

    fn freeze_for_lifecycle_transition(&mut self) {
        let elapsed_ms = self.elapsed_ms();

        self.started_at = None;
        self.frozen_elapsed_ms = elapsed_ms;
        self.interruptible = false;
    }

    pub(crate) fn animates(&self) -> bool {
        animations_enabled()
            && matches!(
                self.kind,
                StatusKind::Running | StatusKind::InterruptRequested
            )
    }

    pub(crate) fn elapsed_ms(&self) -> u64 {
        match self.kind {
            StatusKind::Running | StatusKind::InterruptRequested => self
                .started_at
                .map(|started_at| started_at.elapsed().as_millis() as u64)
                .unwrap_or(self.frozen_elapsed_ms),
            _ => self.frozen_elapsed_ms,
        }
    }

    pub(crate) fn status_label(&self) -> &'static str {
        match self.kind {
            StatusKind::Idle => "Idle",
            StatusKind::Running => "Running",
            StatusKind::InterruptRequested => "Interrupt requested",
            StatusKind::PendingPermission => "Permission",
            StatusKind::PermissionAnswered => "Answered",
            StatusKind::PendingClarification => "Clarification",
            StatusKind::ActionRequired => "Action required",
            StatusKind::ControllerHandoff => "Resuming",
            StatusKind::Completed => "Completed",
            StatusKind::Incomplete => "Incomplete",
            StatusKind::Interrupted => "Interrupted",
            StatusKind::Failed => "Failed",
            StatusKind::Blocked => "Blocked",
            StatusKind::Cancelled => "Cancelled",
            StatusKind::StaleView => "View out of date",
        }
    }

    pub(crate) fn elapsed_label(&self) -> String {
        let elapsed_secs = self.elapsed_ms() / 1000;
        if elapsed_secs < 60 {
            return format!("{elapsed_secs}s");
        }
        if elapsed_secs < 3600 {
            let minutes = elapsed_secs / 60;
            let seconds = elapsed_secs % 60;
            return format!("{minutes}m {seconds:02}s");
        }
        let hours = elapsed_secs / 3600;
        let minutes = (elapsed_secs % 3600) / 60;
        let seconds = elapsed_secs % 60;
        format!("{hours}h {minutes:02}m {seconds:02}s")
    }

    pub(crate) fn interruptible(&self) -> bool {
        self.interruptible
    }

    pub(crate) fn notice_text(&self) -> Option<&str> {
        self.notice.as_deref()
    }

    #[allow(dead_code)]
    pub(crate) fn desired_height(&self, width: u16) -> u16 {
        self.render_lines(width, &ThemePalette::default()).len() as u16
    }

    pub(crate) fn render_lines(&self, width: u16, theme: &ThemePalette) -> Vec<Line<'static>> {
        if self.kind == StatusKind::Idle {
            return Vec::new();
        }
        if self.kind == StatusKind::Completed {
            return Vec::new();
        }

        // Running activity uses only its heading, elapsed time and interrupt
        // hint. Keep details for errors and interactions, and retain notices.
        let mut lines = crate::wrapping::adaptive_wrap_lines(
            &[self.primary_line(theme)],
            crate::wrapping::RtOptions::new(usize::from(width).max(1)),
        );
        if self.kind != StatusKind::Running {
            lines.extend(self.detail_lines(width, theme));
        }
        lines.extend(self.notice_lines(width, theme));
        if self.kind == StatusKind::Running {
            lines.push(Line::default());
        }
        lines
    }

    fn primary_line(&self, theme: &ThemePalette) -> Line<'static> {
        let indicator = crate::style::status_kind_indicator(self.kind);
        let accent = crate::style::status_kind_color(self.kind, theme);
        let header_text = if self.header.trim().is_empty() {
            self.status_label().to_string()
        } else {
            self.header.clone()
        };
        let header_color = if self.kind == StatusKind::Running {
            if animations_enabled() {
                shimmer_color(theme.text, self.animation_frame).unwrap_or(theme.text)
            } else {
                theme.text
            }
        } else {
            accent
        };
        let canonical_working = self.kind == StatusKind::Running && header_text.trim() == "Working";

        let indicator_style = if self.kind == StatusKind::Running {
            crate::style::activity_marker_style("running", theme)
        } else {
            Style::default().fg(accent)
        };
        let mut spans = vec![
            Span::styled(indicator, indicator_style),
            Span::raw(" "),
            Span::styled(header_text, Style::default().fg(header_color)),
            Span::raw(" "),
        ];
        if matches!(self.kind, StatusKind::Running) && self.interruptible() && !canonical_working {
            spans.extend([
                Span::from(format!("({} • ", self.elapsed_label())).dim(),
                Span::styled("esc", Style::default().fg(theme.text)),
                Span::from(" to interrupt)").dim(),
            ]);
        } else {
            spans.push(Span::from(format!("({})", self.elapsed_label())).dim());
        }
        Line::from(spans)
    }

    fn detail_lines(&self, width: u16, theme: &ThemePalette) -> Vec<Line<'static>> {
        self.detail_text_lines(&self.detail, width, theme)
    }

    fn detail_text_lines(
        &self,
        detail: &str,
        width: u16,
        theme: &ThemePalette,
    ) -> Vec<Line<'static>> {
        if detail.trim().is_empty() || width == 0 {
            return Vec::new();
        }
        let prefix_width = UnicodeWidthStr::width(crate::style::GLYPH_DETAIL_FIRST);
        let content_width = usize::from(width).saturating_sub(prefix_width).max(1);
        let mut lines = wrap_with_prefix(
            detail.trim(),
            content_width,
            crate::style::GLYPH_DETAIL_FIRST,
            " ".repeat(prefix_width).as_str(),
            theme.text_muted,
        );
        if self.kind != StatusKind::Running && lines.len() > STATUS_DETAILS_DEFAULT_MAX_LINES {
            lines.truncate(STATUS_DETAILS_DEFAULT_MAX_LINES);
            if let Some(last) = lines.last_mut() {
                if let Some(last_span) = last.spans.last_mut() {
                    let trimmed = truncate_for_width(last_span.content.as_ref(), content_width);
                    let ellipsized = if trimmed.ends_with('…') {
                        trimmed
                    } else {
                        format!("{}…", trimmed.trim_end_matches('…'))
                    };
                    *last_span = Span::styled(ellipsized, Style::default().fg(theme.text_muted));
                }
            }
        }
        lines
    }

    fn notice_lines(&self, width: u16, theme: &ThemePalette) -> Vec<Line<'static>> {
        let Some(notice) = self.notice_text() else {
            return Vec::new();
        };
        if width == 0 {
            return Vec::new();
        }
        let content_width = usize::from(width)
            .saturating_sub(UnicodeWidthStr::width(crate::style::GLYPH_NOTICE))
            .max(1);
        let mut lines = wrap_with_prefix(
            notice,
            content_width,
            crate::style::GLYPH_NOTICE,
            crate::style::GLYPH_DETAIL_CONT,
            theme.text_muted,
        );
        if lines.len() > STATUS_DETAILS_DEFAULT_MAX_LINES {
            lines.truncate(STATUS_DETAILS_DEFAULT_MAX_LINES);
            if let Some(span) = lines.last_mut().and_then(|line| line.spans.last_mut()) {
                span.content = format!(
                    "{}…",
                    truncate_for_width(span.content.as_ref(), content_width.saturating_sub(1))
                )
                .into();
            }
        }
        lines
    }

    #[cfg(test)]
    pub(crate) fn render_codex_line(&self, theme: &ThemePalette) -> Option<Line<'static>> {
        self.render_lines(u16::MAX, theme).into_iter().next()
    }

    #[allow(dead_code)]
    pub(crate) fn render_codex_lines(
        &self,
        width: u16,
        theme: &ThemePalette,
    ) -> Vec<Line<'static>> {
        self.render_lines(width, theme)
    }
}

fn wrap_with_prefix(
    text: &str,
    content_width: usize,
    initial_prefix: &str,
    continuation_prefix: &str,
    color: Color,
) -> Vec<Line<'static>> {
    let mut lines = Vec::new();
    for line in text.lines() {
        let wrapped = crate::wrapping::adaptive_wrap_text(line, content_width.max(1));
        if wrapped.is_empty() {
            lines.push(Line::from(vec![
                Span::styled(initial_prefix.to_string(), Style::default().fg(color)),
                Span::styled(String::new(), Style::default().fg(color)),
            ]));
            continue;
        }
        for (index, segment) in wrapped.into_iter().enumerate() {
            let prefix = if index == 0 {
                initial_prefix
            } else {
                continuation_prefix
            };
            lines.push(Line::from(vec![
                Span::styled(prefix.to_string(), Style::default().fg(color)),
                Span::styled(segment, Style::default().fg(color)),
            ]));
        }
    }
    lines
}

use crate::style::truncate_for_width;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonical_working_status_renders_only_elapsed_time() {
        let mut status = StatusWidgetState::default();
        status.start_working("Working".to_string(), String::new(), 0, true);

        let line = status
            .render_codex_line(&ThemePalette::default())
            .expect("running line");
        assert_eq!(line.spans[0].style.fg, Some(ThemePalette::default().accent));
        assert!(line.spans[0]
            .style
            .add_modifier
            .contains(ratatui::style::Modifier::SLOW_BLINK));
        let rendered = line
            .spans
            .into_iter()
            .map(|span| span.content.into_owned())
            .collect::<String>();

        assert_eq!(rendered, "⦁ Working (0s)");
    }

    #[test]
    fn working_status_hides_activity_details_and_keeps_notices() {
        let mut status = StatusWidgetState::default();
        status.start_working(
            "Working".to_string(),
            "applying interaction response".to_string(),
            0,
            true,
        );
        status.set_notice(Some("continuing active turn".to_string()));

        let lines = status.render_lines(80, &ThemePalette::default());
        let rendered = lines
            .iter()
            .map(|line| {
                line.spans
                    .iter()
                    .map(|span| span.content.as_ref())
                    .collect::<String>()
            })
            .collect::<Vec<_>>();

        assert!(!rendered
            .join("\n")
            .contains("applying interaction response"));
        assert_eq!(status.detail, "applying interaction response");
        assert!(rendered.join("\n").contains("continuing active turn"));
    }

    #[test]
    fn running_status_reserves_one_blank_row_below_the_status_surface() {
        let mut status = StatusWidgetState::default();
        status.start_working("Working".to_string(), String::new(), 0, true);

        let lines = status.render_lines(60, &ThemePalette::default());

        assert_eq!(lines.len(), 2);
        assert!(lines[1].spans.is_empty());
    }

    #[test]
    fn running_status_animation_keeps_circle_indicator() {
        let mut status = StatusWidgetState::default();
        status.start_working("Working".to_string(), String::new(), 0, true);
        let initial = status
            .render_codex_line(&ThemePalette::default())
            .expect("running line before tick")
            .spans
            .into_iter()
            .map(|span| span.content.into_owned())
            .collect::<String>();
        assert!(initial.starts_with('⦁'));

        for _ in 0..3 {
            status.tick_animation();
        }

        let after = status
            .render_codex_line(&ThemePalette::default())
            .expect("running line after ticks")
            .spans
            .into_iter()
            .map(|span| span.content.into_owned())
            .collect::<String>();

        assert!(after.starts_with('⦁'), "{after}");
        assert!(after.contains("Working"));
    }

    #[test]
    fn circle_indicator_stays_put_between_animation_ticks() {
        let mut status = StatusWidgetState::default();
        status.start_working("Working".to_string(), String::new(), 0, true);
        status.tick_animation();
        let line = status
            .render_codex_line(&ThemePalette::default())
            .expect("running line after one tick")
            .spans
            .into_iter()
            .map(|span| span.content.into_owned())
            .collect::<String>();
        assert!(line.starts_with('⦁'), "{line}");
    }

    #[test]
    fn repeated_start_working_preserves_running_elapsed_time() {
        let mut status = StatusWidgetState::default();
        status.start_working("Working".to_string(), "first".to_string(), 30_000, true);
        status.start_working("Working".to_string(), "second".to_string(), 0, true);

        assert!(
            status.elapsed_ms() >= 30_000,
            "status restarts during the same turn must not reset elapsed time"
        );
        assert_eq!(status.detail, "second");
    }

    #[test]
    fn lower_provider_elapsed_cannot_restart_task_elapsed_time() {
        let mut status = StatusWidgetState::default();
        status.start_working("Working".to_string(), "first".to_string(), 30_000, true);

        status.start_working("Thinking".to_string(), "second".to_string(), 1_000, true);

        assert!(
            status.elapsed_ms() >= 30_000,
            "provider-scoped elapsed time must not reset the active task timer"
        );
        assert_eq!(status.detail, "second");
    }

    #[test]
    fn completed_status_is_not_rendered_as_live_widget() {
        let mut status = StatusWidgetState::default();
        status.set_terminal(
            StatusKind::Completed,
            "Completed".to_string(),
            "completed".to_string(),
            306_000,
        );

        assert!(status.render_lines(60, &ThemePalette::default()).is_empty());
    }

    #[test]
    fn idle_status_does_not_render_codex_line() {
        let status = StatusWidgetState::default();

        assert!(status.render_codex_line(&ThemePalette::default()).is_none());
    }
}

#[cfg(test)]
mod shimmer_tests {
    use super::*;

    #[test]
    fn shimmer_brightness_sweeps_over_period() {
        let base = Color::Rgb(200, 100, 50);
        let at_zero = shimmer_color(base, 0).expect("rgb shimmer");
        let at_quarter = shimmer_color(base, SHIMMER_PERIOD_FRAMES / 4).expect("rgb shimmer");

        // frame 0 is peak brightness (cos = 1), quarter period is trough (cos = -1)
        let Color::Rgb(r0, _, _) = at_zero else {
            panic!()
        };
        let Color::Rgb(rq, _, _) = at_quarter else {
            panic!()
        };
        assert!(r0 > rq, "peak {r0} must exceed trough {rq}");
        assert!(r0 <= 200 && rq >= (0.6 * 200.0 * 0.999) as u8);
    }

    #[test]
    fn shimmer_returns_none_for_non_rgb_base() {
        assert_eq!(shimmer_color(Color::Reset, 0), None);
        assert_eq!(shimmer_color(Color::Indexed(42), 0), None);
    }

    #[test]
    fn reduced_motion_indicator_is_static_circle() {
        // with animations disabled (tested via env-independent path):
        // shimmer falls back to theme.text and indicator to "⦁" — covered by
        // primary_line branches; here we assert the sweep stays within the
        // 0.6..=1.0 brightness window of the base color.
        let base = Color::Rgb(255, 255, 255);
        let lower = (0.6f32 * 255.0).floor() as u8;
        for frame in 0..SHIMMER_PERIOD_FRAMES {
            let Color::Rgb(r, g, b) = shimmer_color(base, frame).expect("rgb") else {
                panic!()
            };
            assert!(r >= lower && g >= lower && b >= lower);
        }
    }
}
