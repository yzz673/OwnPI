//! Central style constants for the Graphite Signal visual language.
//! Glyph literals and semantic colors live here so restyles touch one file.
//!
//! Phase 0 note: every value below reproduces the previously inline literal
//! byte-for-byte. Later phases change values here, not at call sites.

use ratatui::style::{Color, Modifier, Style};
use unicode_width::UnicodeWidthStr;

use crate::status::StatusKind;
use crate::theme::ThemePalette;

/// Top-level output marker shared by successful and running messages.
pub(crate) const GLYPH_ACTION: &str = "⦁ ";
/// Agent answers use the same compact solid marker as other output messages.
pub(crate) const GLYPH_AGENT_BULLET: &str = "⦁ ";
/// Detail lines carry no glyph: pure indent, graphite style.
pub(crate) const GLYPH_DETAIL_FIRST: &str = "  ";
/// Detail line continuation prefix.
pub(crate) const GLYPH_DETAIL_CONT: &str = "  ";
/// Selection cursor in menus/overlays.
pub(crate) const GLYPH_CURSOR: &str = "▸";
/// char-typed mirror of GLYPH_CURSOR for char-based prefix assembly
/// (selection_popup row indicators). Keep in sync with GLYPH_CURSOR.
pub(crate) const GLYPH_CURSOR_CHAR: char = '▸';
/// exec exit-code glyphs.
pub(crate) const GLYPH_SUCCESS: &str = "✓";
pub(crate) const GLYPH_FAILURE: &str = "✗";
pub(crate) const GLYPH_FAILURE_PREFIX: &str = "✗ ";
/// Notice prefix used by status detail lines ("  ↳ ").
pub(crate) const GLYPH_NOTICE: &str = "  ↳ ";
/// Heavy rule used by separators/banners.
pub(crate) const GLYPH_RULE: &str = "─";
/// Markdown table row prefix ("│ ...").
pub(crate) const GLYPH_MD_TABLE_PREFIX: &str = "│ ";
/// Markdown blockquote bar (repeated per quote depth).
pub(crate) const GLYPH_MD_QUOTE_PREFIX: &str = "> ";

/// Recognition set, not rendering: wrapping.rs treats a token made of one of
/// these markers as decoration (list/tree/quote leads) when classifying
/// URL-like tokens. Not used to paint anything; do not restyle blindly.
pub(crate) const GLYPH_WRAP_LEAD_CHARS: &[&str] = &[
    "-", "*", "+", "•", "◦", "▪", "⦁", "▶", ">", "|", "│", "┆", "└", "├", "┌", "┐", "┘", "┼", "╭",
    "╮", "╰", "╯",
];

/// Fallback user-message background when the theme has no surface color.
pub(crate) const FALLBACK_BG: Color = Color::Rgb(31, 34, 43);
/// Fallback user-message foreground on dark themes.
pub(crate) const FALLBACK_FG: Color = Color::Rgb(245, 247, 255);

/// Description-column color in selection popups (Phase 1 remaps to theme).
pub(crate) const POPUP_DESCRIPTION_COLOR: Color = Color::DarkGray;

/// Truncate a string to a display width, appending an ellipsis when cut.
pub(crate) fn truncate_for_width(value: &str, width: usize) -> String {
    if width == 0 {
        return String::new();
    }
    let mut rendered = String::new();
    let mut used = 0usize;
    for ch in value.chars() {
        let ch_width = unicode_width::UnicodeWidthChar::width(ch).unwrap_or(0);
        if used.saturating_add(ch_width) > width {
            rendered.push('…');
            return rendered;
        }
        rendered.push(ch);
        used = used.saturating_add(ch_width);
    }
    rendered
}

/// Build the "─ Worked for {duration} ─────" separator text padded to `width`.
/// Reproduces the exact format previously inlined in history_cell/mod.rs and
/// transcript/mod.rs: label plus at least one trailing rule glyph.
pub(crate) fn worked_for_line(duration: &str, width: usize) -> String {
    let line_width = width.max(1);
    if duration.trim().is_empty() {
        return GLYPH_RULE.repeat(line_width);
    }
    let label = format!("{GLYPH_RULE} Worked for {} ", duration.trim());
    let label_width = UnicodeWidthStr::width(label.as_str());
    let fill_width = line_width.saturating_sub(label_width).max(1);
    format!("{label}{}", GLYPH_RULE.repeat(fill_width))
}

/// Format cumulative user-turn duration for the completion separator.
/// Short direct-chat turns retain useful sub-second feedback; longer turns use
/// the same compact minute/hour shape as the live status surface.
pub(crate) fn turn_elapsed_label(elapsed_ms: u64) -> String {
    if elapsed_ms < 10_000 {
        return format!("{:.1}s", elapsed_ms as f64 / 1000.0);
    }
    let elapsed_secs = elapsed_ms / 1000;
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

pub(crate) fn activity_marker(status: &str) -> &'static str {
    if matches!(
        status.trim(),
        "failed" | "error" | "blocked" | "cancelled" | "canceled"
    ) {
        GLYPH_FAILURE_PREFIX
    } else if matches!(
        status.trim(),
        "partial_success" | "partial" | "warning" | "failed_with_partial"
    ) {
        "! "
    } else {
        GLYPH_ACTION
    }
}

pub(crate) fn activity_marker_color(status: &str, theme: &ThemePalette) -> Color {
    match status.trim() {
        "failed" | "error" | "blocked" | "cancelled" | "canceled" => theme.error,
        "incomplete" | "partial_success" | "partial" | "warning" | "failed_with_partial" => {
            theme.warning
        }
        "running" | "exploring" | "started" | "in_progress" => theme.accent,
        "completed" | "complete" | "success" | "succeeded" | "ran" | "explored" | "accepted"
        | "approved" => theme.success,
        _ => theme.accent,
    }
}

/// Complete marker styling for history activity cells.
///
/// Call sites use this instead of composing only a color so the running-state
/// animation cannot drift between typed tools, exec cells, and file changes.
pub(crate) fn activity_marker_style(status: &str, theme: &ThemePalette) -> Style {
    let style = Style::default().fg(activity_marker_color(status, theme));
    if matches!(
        status.trim(),
        "running" | "exploring" | "started" | "in_progress"
    ) {
        style.add_modifier(Modifier::SLOW_BLINK)
    } else {
        style
    }
}

/// Static per-kind status indicator. Running and successful states use a
/// circle; terminal unsuccessful states use a cross.
pub(crate) fn status_kind_indicator(kind: StatusKind) -> &'static str {
    match kind {
        StatusKind::Running
        | StatusKind::InterruptRequested
        | StatusKind::Interrupted
        | StatusKind::PendingPermission
        | StatusKind::PermissionAnswered
        | StatusKind::PendingClarification
        | StatusKind::ActionRequired
        | StatusKind::ControllerHandoff
        | StatusKind::Completed => "⦁",
        StatusKind::Incomplete | StatusKind::StaleView => "!",
        StatusKind::Failed | StatusKind::Blocked | StatusKind::Cancelled => GLYPH_FAILURE,
        StatusKind::Idle => " ",
    }
}

/// Semantic color per StatusKind. Active work uses signal orange, successful
/// terminal state uses green, and warnings/failures retain dedicated colors.
pub(crate) fn status_kind_color(kind: StatusKind, theme: &ThemePalette) -> Color {
    match kind {
        StatusKind::Running | StatusKind::InterruptRequested | StatusKind::Interrupted => {
            theme.accent
        }
        StatusKind::PendingPermission => theme.accent,
        StatusKind::PermissionAnswered | StatusKind::Completed => theme.success,
        StatusKind::Incomplete | StatusKind::ActionRequired | StatusKind::StaleView => {
            theme.warning
        }
        StatusKind::PendingClarification | StatusKind::ControllerHandoff => theme.accent,
        StatusKind::Failed | StatusKind::Blocked | StatusKind::Cancelled => theme.error,
        StatusKind::Idle => theme.text_muted,
    }
}

#[cfg(test)]
mod marker_tests {
    use super::*;

    #[test]
    fn activity_markers_encode_running_success_and_failure() {
        let theme = ThemePalette::default();

        assert_eq!(activity_marker("running"), "⦁ ");
        assert_eq!(UnicodeWidthStr::width("⦁"), 1);
        assert_eq!(activity_marker_color("running", &theme), theme.accent);
        assert!(activity_marker_style("running", &theme)
            .add_modifier
            .contains(Modifier::SLOW_BLINK));
        assert_eq!(activity_marker("completed"), "⦁ ");
        assert_eq!(activity_marker_color("completed", &theme), theme.success);
        assert!(!activity_marker_style("completed", &theme)
            .add_modifier
            .contains(Modifier::SLOW_BLINK));
        assert_eq!(activity_marker_color("ran", &theme), theme.success);
        assert_eq!(activity_marker_color("explored", &theme), theme.success);
        assert_eq!(activity_marker_color("exploring", &theme), theme.accent);
        assert!(activity_marker_style("exploring", &theme)
            .add_modifier
            .contains(Modifier::SLOW_BLINK));
        assert_eq!(activity_marker("interrupted"), "⦁ ");
        assert_eq!(activity_marker_color("interrupted", &theme), theme.accent);
        assert_eq!(activity_marker("failed"), "✗ ");
        assert_eq!(activity_marker_color("failed", &theme), theme.error);
        assert_eq!(activity_marker("blocked"), "✗ ");
        assert_eq!(activity_marker_color("cancelled", &theme), theme.error);
        assert_eq!(activity_marker("partial_success"), "! ");
        assert_eq!(
            activity_marker_color("failed_with_partial", &theme),
            theme.warning
        );
        assert_eq!(activity_marker("unknown"), "⦁ ");
        assert_eq!(activity_marker_color("unknown", &theme), theme.accent);

        assert_eq!(status_kind_indicator(StatusKind::Running), "⦁");
        assert_eq!(status_kind_color(StatusKind::Running, &theme), theme.accent);
        assert_eq!(status_kind_indicator(StatusKind::Completed), "⦁");
        assert_eq!(
            status_kind_color(StatusKind::Completed, &theme),
            theme.success
        );
        assert_eq!(status_kind_indicator(StatusKind::Failed), "✗");
        assert_eq!(status_kind_color(StatusKind::Failed, &theme), theme.error);
    }

    #[test]
    fn empty_work_duration_renders_plain_rule() {
        assert_eq!(worked_for_line("", 12), "─".repeat(12));
        assert!(worked_for_line("5s", 40).starts_with("─ Worked for 5s "));
    }

    #[test]
    fn turn_elapsed_uses_subsecond_precision_then_compact_units() {
        assert_eq!(turn_elapsed_label(0), "0.0s");
        assert_eq!(turn_elapsed_label(450), "0.5s");
        assert_eq!(turn_elapsed_label(9_940), "9.9s");
        assert_eq!(turn_elapsed_label(12_400), "12s");
        assert_eq!(turn_elapsed_label(64_000), "1m 04s");
        assert_eq!(turn_elapsed_label(3_664_000), "1h 01m 04s");
    }
}

/// Markdown inline code: quiet graphite, no signal color.
pub(crate) fn md_inline_code_color(theme: &ThemePalette) -> Color {
    theme.text_muted
}

/// Markdown ordered-list marker: plain text color.
pub(crate) fn md_ordered_marker_color(theme: &ThemePalette) -> Color {
    theme.text
}

/// Markdown link: signal accent (links are actionable).
pub(crate) fn md_link_color(theme: &ThemePalette) -> Color {
    theme.accent
}

/// Markdown blockquote bar: border gray.
pub(crate) fn md_quote_color(theme: &ThemePalette) -> Color {
    theme.border
}

/// Diff added-line row background (light/dark aware; Phase 1 remaps to theme).
pub(crate) fn diff_added_bg(theme: &ThemePalette) -> Color {
    if theme.is_light_background() {
        Color::Rgb(218, 251, 225)
    } else {
        Color::Rgb(33, 58, 43)
    }
}

/// Diff removed-line row background (light/dark aware; Phase 1 remaps to theme).
pub(crate) fn diff_removed_bg(theme: &ThemePalette) -> Color {
    if theme.is_light_background() {
        Color::Rgb(255, 235, 233)
    } else {
        Color::Rgb(74, 34, 29)
    }
}
