use ratatui::style::Style;
use ratatui::text::{Line, Span};
use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

use crate::theme::ThemePalette;

pub(crate) fn bordered_lines(
    body: Vec<Line<'static>>,
    max_width: usize,
    theme: &ThemePalette,
) -> Vec<Line<'static>> {
    let inner_width = body
        .iter()
        .map(line_width)
        .max()
        .unwrap_or(1)
        .min(max_width.saturating_sub(4).max(1));
    let border_style = Style::default().fg(theme.border);
    let mut lines = Vec::with_capacity(body.len() + 2);
    // Keep every bordered history surface on the shared rounded-box contract.
    lines.push(Line::from(Span::styled(
        format!("╭{}╮", "─".repeat(inner_width + 2)),
        border_style,
    )));
    for line in body {
        let rendered_width = line_width(&line);
        let padding = " ".repeat(inner_width.saturating_sub(rendered_width));
        let mut spans = vec![Span::styled("│ ", border_style)];
        spans.extend(line.spans);
        spans.push(Span::raw(padding));
        spans.push(Span::styled(" │", border_style));
        lines.push(Line::from(spans));
    }
    lines.push(Line::from(Span::styled(
        format!("╰{}╯", "─".repeat(inner_width + 2)),
        border_style,
    )));
    lines
}

pub(crate) fn line_width(line: &Line<'static>) -> usize {
    line.spans
        .iter()
        .map(|span| span.content.width())
        .sum::<usize>()
}

pub(crate) fn wrap_for_width(value: &str, width: usize) -> Vec<String> {
    crate::wrapping::adaptive_wrap_text(value, width.max(1))
}

pub(crate) fn truncate_middle(value: &str, max_width: usize) -> String {
    if value.width() <= max_width {
        return value.to_string();
    }
    if max_width <= 1 {
        return "…".to_string();
    }
    let left_width = max_width.saturating_sub(1) / 2;
    let right_width = max_width.saturating_sub(1 + left_width);
    let left = take_width_prefix(value, left_width);
    let right = take_width_suffix(value, right_width);
    format!("{left}…{right}")
}

fn take_width_prefix(value: &str, max_width: usize) -> String {
    let mut out = String::new();
    let mut used = 0usize;
    for ch in value.chars() {
        let ch_width = ch.width().unwrap_or(0);
        if used.saturating_add(ch_width) > max_width {
            break;
        }
        out.push(ch);
        used = used.saturating_add(ch_width);
    }
    out
}

fn take_width_suffix(value: &str, max_width: usize) -> String {
    let mut chars = Vec::new();
    let mut used = 0usize;
    for ch in value.chars().rev() {
        let ch_width = ch.width().unwrap_or(0);
        if used.saturating_add(ch_width) > max_width {
            break;
        }
        chars.push(ch);
        used = used.saturating_add(ch_width);
    }
    chars.into_iter().rev().collect()
}
