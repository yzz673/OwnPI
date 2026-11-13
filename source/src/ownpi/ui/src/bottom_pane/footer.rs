use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::Style;
use ratatui::text::{Line, Span};
use ratatui::widgets::{Paragraph, Widget};
use std::path::Path;
use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

use crate::theme::BottomPanePalette;

pub(crate) const FOOTER_ROWS: u16 = 2;

/// Only the six fields displayed in the two persistent status rows.
#[derive(Clone, Debug)]
pub(crate) struct FooterProps {
    pub(crate) model: String,
    pub(crate) directory: String,
    pub(crate) permission: String,
    pub(crate) mode: String,
    pub(crate) stage: String,
    pub(crate) context: Option<FooterContext>,
}

#[derive(Clone, Debug)]
pub(crate) struct FooterContext {
    pub(crate) percent_left: Option<u64>,
    pub(crate) tokens_used: Option<u64>,
    pub(crate) is_estimate: bool,
}

impl FooterContext {
    fn value(&self, compact: bool) -> String {
        let label = if compact { "Ctx" } else { "Context" };
        let marker = if self.is_estimate { "~" } else { "" };
        if let Some(percent) = self.percent_left {
            let suffix = if compact { "" } else { " left" };
            format!("{label} {marker}{percent}%{suffix}")
        } else if let Some(tokens) = self.tokens_used {
            let amount = if tokens >= 1_000_000 {
                format!("{:.1}M", tokens as f64 / 1_000_000.0)
            } else if tokens >= 1_000 {
                format!("{:.1}k", tokens as f64 / 1_000.0)
            } else {
                tokens.to_string()
            };
            let suffix = if compact { "" } else { " tokens" };
            format!("{label} {marker}{amount}{suffix}")
        } else {
            format!("{label} --")
        }
    }
}

pub(crate) fn footer_height_for_surface_popup(surface_popup_is_visible: bool) -> u16 {
    if surface_popup_is_visible {
        0
    } else {
        FOOTER_ROWS
    }
}

pub(crate) fn render_footer_from_props(
    area: Rect,
    buf: &mut Buffer,
    props: &FooterProps,
    palette: BottomPanePalette,
) {
    // One left gutter and one physical terminal column reserved against autowrap.
    let lines = footer_lines(props, palette, usize::from(area.width.saturating_sub(2)))
        .into_iter()
        .map(|line| {
            let mut prefixed = Line::from(" ");
            prefixed.spans.extend(line.spans);
            prefixed
        })
        .collect::<Vec<_>>();
    Paragraph::new(lines).render(area, buf);
}

fn footer_lines(
    props: &FooterProps,
    palette: BottomPanePalette,
    width: usize,
) -> [Line<'static>; 2] {
    let directory = home_relative_directory(&props.directory);
    let permission = format!("Permission: {}", props.permission);
    let mode = format!(
        "Mode: {}",
        if props.mode.is_empty() {
            "--"
        } else {
            &props.mode
        }
    );
    let stage = format!(
        "Stage: {}",
        if props.stage.is_empty() {
            "--"
        } else {
            &props.stage
        }
    );
    let context = props
        .context
        .as_ref()
        .map(|value| value.value(false))
        .unwrap_or_default();
    let context = if mode.width() + stage.width() + context.width() + 6 > width {
        props
            .context
            .as_ref()
            .map(|value| value.value(true))
            .unwrap_or_default()
    } else {
        context
    };
    [
        status_row(
            [&props.model, &permission, &directory],
            palette,
            width,
            true,
        ),
        status_row([&mode, &stage, &context], palette, width, false),
    ]
}

/// Keep each field on its assigned row. Shrink the path/stage, then the model/mode;
/// reserve the permission/context value, without adding rows or relying on clipping.
fn status_row(
    values: [&str; 3],
    palette: BottomPanePalette,
    width: usize,
    directory_row: bool,
) -> Line<'static> {
    let separator_width = values
        .iter()
        .filter(|v| !v.is_empty())
        .count()
        .saturating_sub(1)
        * 3;
    if width <= separator_width + 2 {
        return Line::from(Span::styled(
            truncate_end_to_width(&values.join(" · "), width),
            Style::default().fg(palette.input_muted),
        ));
    }
    let available = width - separator_width;
    let priority_index = if directory_row { 1 } else { 2 };
    let flexible_index = if directory_row { 2 } else { 1 };
    let priority = values[priority_index]
        .width()
        .min(available.saturating_sub(2));
    let remaining = available - priority;
    let first = values[0]
        .width()
        .min(remaining.saturating_sub(values[flexible_index].width().min(8)));
    let first = if first < values[0].width().min(8) {
        values[0].width().min(remaining.saturating_sub(1))
    } else {
        first
    };
    let mut budgets = [first, 0, 0];
    budgets[priority_index] = priority;
    budgets[flexible_index] = remaining - first;
    let mut spans = Vec::new();
    for (index, (value, budget)) in values.into_iter().zip(budgets).enumerate() {
        if value.is_empty() {
            continue;
        }
        if !spans.is_empty() {
            spans.push(Span::styled(
                " · ",
                Style::default().fg(palette.input_muted),
            ));
        }
        let text = if directory_row && index == 2 {
            truncate_middle_to_width(value, budget)
        } else {
            truncate_end_to_width(value, budget)
        };
        let color = if (directory_row && index == 2) || (!directory_row && index < 2) {
            palette.input_accent
        } else {
            palette.input_muted
        };
        spans.push(Span::styled(text, Style::default().fg(color)));
    }
    Line::from(spans)
}

fn home_relative_directory(directory: &str) -> String {
    let path = Path::new(directory);
    let Some(home) = std::env::var_os("HOME") else {
        return directory.to_string();
    };
    let home = Path::new(&home);
    let Ok(relative) = path.strip_prefix(home) else {
        return directory.to_string();
    };
    if relative.as_os_str().is_empty() {
        "~".to_string()
    } else {
        format!("~/{}", relative.to_string_lossy())
    }
}

fn truncate_middle_to_width(value: &str, max_width: usize) -> String {
    if value.width() <= max_width {
        return value.to_string();
    }
    if max_width <= 1 {
        return "…".chars().take(max_width).collect();
    }
    let left_budget = max_width.saturating_sub(1).div_ceil(2);
    let right_budget = max_width.saturating_sub(1).saturating_sub(left_budget);
    let left = take_start_by_width(value, left_budget);
    let right = take_end_by_width(value, right_budget);
    format!("{left}…{right}")
}

fn truncate_end_to_width(value: &str, max_width: usize) -> String {
    if value.width() <= max_width {
        return value.to_string();
    }
    if max_width == 0 {
        return String::new();
    }
    if max_width == 1 {
        return "…".to_string();
    }
    format!("{}…", take_start_by_width(value, max_width - 1))
}

fn take_start_by_width(value: &str, max_width: usize) -> String {
    let mut width: usize = 0;
    value
        .chars()
        .take_while(|ch| {
            let next = ch.width().unwrap_or(0);
            if width.saturating_add(next) > max_width {
                false
            } else {
                width = width.saturating_add(next);
                true
            }
        })
        .collect()
}

fn take_end_by_width(value: &str, max_width: usize) -> String {
    let mut width: usize = 0;
    let mut chars = value
        .chars()
        .rev()
        .take_while(|ch| {
            let next = ch.width().unwrap_or(0);
            if width.saturating_add(next) > max_width {
                false
            } else {
                width = width.saturating_add(next);
                true
            }
        })
        .collect::<Vec<_>>();
    chars.reverse();
    chars.into_iter().collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::theme::ThemePalette;

    fn props() -> FooterProps {
        FooterProps {
            model: "gpt-5.6-terra".into(),
            directory: std::env::var("HOME").unwrap(),
            permission: "Full access".into(),
            mode: "Research".into(),
            stage: "literature grounding".into(),
            context: Some(FooterContext {
                percent_left: Some(95),
                tokens_used: None,
                is_estimate: false,
            }),
        }
    }
    fn plain(line: &Line<'_>) -> String {
        line.spans
            .iter()
            .map(|span| span.content.as_ref())
            .collect()
    }
    #[test]
    fn status_has_exactly_the_requested_rows_and_fields() {
        let lines = footer_lines(&props(), ThemePalette::default().bottom_pane(), 220);
        assert_eq!(
            plain(&lines[0]),
            "gpt-5.6-terra · Permission: Full access · ~"
        );
        assert_eq!(
            plain(&lines[1]),
            "Mode: Research · Stage: literature grounding · Context 95% left"
        );
        assert_eq!(footer_height_for_surface_popup(false), 2);
        assert_eq!(footer_height_for_surface_popup(true), 0);
    }
    #[test]
    fn narrow_unicode_and_long_paths_stay_in_two_rows_before_reserved_column() {
        let mut props = props();
        props.directory = format!(
            "{}/Research/跨被试语音解码/very-long-project-name/workspace",
            props.directory
        );
        props.stage = "implementation execution".into();
        for width in [1_u16, 2, 8, 24, 40, 60, 80, 120, 220] {
            let area = Rect::new(0, 0, width, FOOTER_ROWS);
            let mut buffer = Buffer::empty(area);
            render_footer_from_props(
                area,
                &mut buffer,
                &props,
                ThemePalette::default().bottom_pane(),
            );
            let lines = footer_lines(
                &props,
                ThemePalette::default().bottom_pane(),
                usize::from(width.saturating_sub(2)),
            );
            assert!(lines
                .iter()
                .all(|line| line.width() <= usize::from(width.saturating_sub(2))));
            assert!((0..2).all(|row| buffer.get(width - 1, row).symbol() == " "));
            if width >= 40 {
                assert!(plain(&lines[0]).contains("Permission: Full access"));
                assert!(plain(&lines[1]).contains("95%"));
            }
        }
    }
    #[test]
    fn context_keeps_unknown_capacity_and_estimate_boundaries() {
        let value = FooterContext {
            percent_left: None,
            tokens_used: Some(144585),
            is_estimate: false,
        };
        assert_eq!(value.value(false), "Context 144.6k tokens");
        assert_eq!(value.value(true), "Ctx 144.6k");
        let value = FooterContext {
            percent_left: Some(95),
            tokens_used: None,
            is_estimate: true,
        };
        assert_eq!(value.value(false), "Context ~95% left");
        let mut props = props();
        props.context = None;
        let lines = footer_lines(&props, ThemePalette::default().bottom_pane(), 120);
        assert_eq!(
            plain(&lines[1]),
            "Mode: Research · Stage: literature grounding"
        );
    }
    #[test]
    fn missing_stage_and_usage_are_explicit_without_adding_status_fields() {
        let mut props = props();
        props.mode.clear();
        props.stage.clear();
        props.context = Some(FooterContext {
            percent_left: None,
            tokens_used: None,
            is_estimate: false,
        });
        let lines = footer_lines(&props, ThemePalette::default().bottom_pane(), 120);
        assert_eq!(plain(&lines[1]), "Mode: -- · Stage: -- · Context --");
    }
}
