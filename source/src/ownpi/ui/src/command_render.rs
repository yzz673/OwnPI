use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};

use crate::theme::ThemePalette;
use crate::wrapping::{adaptive_wrap_line, line_to_static, RtOptions};

/// Render a shell command with the same two-layer ownership as Codex:
/// semantic status/title colors come from the caller, while Bash tokens come
/// from the syntax highlighter. Prefixes are styled spans, never raw ANSI.
pub(crate) fn render_shell_command(
    command: &str,
    width: usize,
    first_prefix: Line<'static>,
    continuation_prefix: Line<'static>,
    theme: &ThemePalette,
) -> Vec<Line<'static>> {
    let highlighted = crate::syntax_highlight::highlight_code_to_lines(
        command,
        "bash",
        theme.is_light_background(),
    )
    .unwrap_or_else(|| {
        command
            .lines()
            .map(|line| {
                Line::from(Span::styled(
                    line.to_string(),
                    Style::default().fg(theme.text),
                ))
            })
            .collect()
    });
    let highlighted = if highlighted.is_empty() {
        vec![Line::default()]
    } else {
        highlighted
    };
    let mut rendered = Vec::new();
    for (index, line) in highlighted.iter().enumerate() {
        let initial_indent = if index == 0 {
            first_prefix.clone()
        } else {
            continuation_prefix.clone()
        };
        rendered.extend(
            adaptive_wrap_line(
                line,
                RtOptions::new(width.max(1))
                    .initial_indent(initial_indent)
                    .subsequent_indent(continuation_prefix.clone())
                    .word_splitter(textwrap::WordSplitter::NoHyphenation),
            )
            .iter()
            .map(line_to_static),
        );
    }
    rendered
}

/// Render a rich command block using Codex's exec tree layout.
///
/// The first command row shares the activity header. Every additional command
/// row is owned by the `│` branch, while the optional body starts at `└` and
/// continues beneath it. Callers provide body rows at the inner width so this
/// function can remain the single owner of tree prefixes.
pub(crate) fn render_exec_command_block(
    command: &str,
    width: usize,
    header_prefix: Line<'static>,
    body: Vec<Line<'static>>,
    occurrence_label: Option<&str>,
    duration_label: Option<&str>,
    theme: &ThemePalette,
) -> Vec<Line<'static>> {
    let tree_style = Style::default().fg(theme.text_muted);
    let command_continuation = Line::from(Span::styled("  │ ", tree_style));
    let mut lines = render_shell_command(
        command,
        width,
        header_prefix,
        command_continuation.clone(),
        theme,
    );
    const MAX_COMMAND_PREVIEW_ROWS: usize = 5;
    if lines.len() > MAX_COMMAND_PREVIEW_ROWS {
        let omitted = lines.len() - MAX_COMMAND_PREVIEW_ROWS;
        lines.truncate(MAX_COMMAND_PREVIEW_ROWS);
        lines.extend(
            adaptive_wrap_line(
                &Line::from(Span::styled(
                    format!("… +{omitted} command rows (ctrl + t to view transcript)"),
                    tree_style,
                )),
                RtOptions::new(width.max(1))
                    .initial_indent(command_continuation.clone())
                    .subsequent_indent(command_continuation.clone()),
            )
            .iter()
            .map(line_to_static),
        );
    }

    append_command_metadata(
        &mut lines,
        width,
        occurrence_label,
        duration_label,
        command_continuation,
        theme,
    );

    for (index, mut line) in body.into_iter().enumerate() {
        let prefix = if index == 0 { "  └ " } else { "    " };
        line.spans.insert(0, Span::styled(prefix, tree_style));
        lines.push(line);
    }
    lines
}

fn append_command_metadata(
    lines: &mut Vec<Line<'static>>,
    width: usize,
    occurrence_label: Option<&str>,
    duration_label: Option<&str>,
    continuation_prefix: Line<'static>,
    theme: &ThemePalette,
) {
    let occurrence_label = occurrence_label.filter(|value| !value.trim().is_empty());
    let duration_label = duration_label.filter(|value| !value.trim().is_empty());
    if occurrence_label.is_none() && duration_label.is_none() {
        return;
    }

    let muted = Style::default().fg(theme.text_muted);
    let mut inline = Line::default();
    let mut standalone = Line::default();
    if let Some(label) = occurrence_label {
        inline.spans.push(Span::styled("  ", muted));
        inline.spans.push(Span::styled(label.to_string(), muted));
        standalone
            .spans
            .push(Span::styled(label.to_string(), muted));
    }
    if let Some(label) = duration_label {
        inline.spans.push(Span::styled("  · ", muted));
        inline.spans.push(Span::styled(label.to_string(), muted));
        if occurrence_label.is_some() {
            standalone.spans.push(Span::styled(" · ", muted));
        } else {
            standalone.spans.push(Span::styled("· ", muted));
        }
        standalone
            .spans
            .push(Span::styled(label.to_string(), muted));
    }

    if let Some(last) = lines.last_mut() {
        if last.width().saturating_add(inline.width()) <= width.max(1) {
            last.spans.extend(inline.spans);
            return;
        }
    }

    lines.extend(
        adaptive_wrap_line(
            &standalone,
            RtOptions::new(width.max(1))
                .initial_indent(continuation_prefix.clone())
                .subsequent_indent(continuation_prefix)
                .word_splitter(textwrap::WordSplitter::NoHyphenation),
        )
        .iter()
        .map(line_to_static),
    );
}

/// Preserve ANSI foreground colors while applying the subdued output layer.
pub(crate) fn render_command_output_line(text: &str, theme: &ThemePalette) -> Line<'static> {
    expand_output_tabs(command_output_line(text, theme))
}

// Textwrap and Ratatui measure literal tabs differently. Expand them after ANSI
// parsing, before wrapping, so colors do not count toward the tab stops and a
// second wrap in the pager cannot detach text from its audit/tree prefix.
fn expand_output_tabs(mut line: Line<'static>) -> Line<'static> {
    use unicode_segmentation::UnicodeSegmentation;
    use unicode_width::UnicodeWidthStr;
    if !line.spans.iter().any(|span| span.content.contains('\t')) {
        return line;
    }
    let mut column = 0;
    for span in &mut line.spans {
        let mut visible = String::new();
        for grapheme in span.content.graphemes(true) {
            if grapheme == "\t" {
                let count = 8 - column % 8;
                visible.push_str(&" ".repeat(count));
                column += count;
            } else {
                visible.push_str(grapheme);
                column += UnicodeWidthStr::width(grapheme);
            }
        }
        span.content = visible.into();
    }
    line
}

fn command_output_line(text: &str, theme: &ThemePalette) -> Line<'static> {
    let base = Style::default().fg(theme.text).add_modifier(Modifier::DIM);
    if let Some((prefix, rest, color)) = git_status_prefix(text, theme) {
        return Line::from(vec![
            Span::styled(
                prefix,
                Style::default().fg(color).add_modifier(Modifier::BOLD),
            ),
            Span::styled(rest, base),
        ]);
    }
    for (prefix, color) in [("+ ", theme.success), ("- ", theme.error)] {
        if let Some(rest) = text.strip_prefix(prefix) {
            return Line::from(vec![
                Span::styled(prefix.to_string(), Style::default().fg(color)),
                Span::styled(rest.to_string(), base),
            ]);
        }
    }
    crate::ansi::ansi_escape_line(text, base)
}

fn git_status_prefix(text: &str, theme: &ThemePalette) -> Option<(String, String, Color)> {
    let mut chars = text.chars();
    let left = chars.next()?;
    let right = chars.next()?;
    if chars.next()? != ' ' {
        return None;
    }
    let code = format!("{left}{right}");
    if !code
        .chars()
        .all(|value| matches!(value, ' ' | '?' | 'A' | 'C' | 'D' | 'M' | 'R' | 'T' | 'U'))
    {
        return None;
    }
    let color = if code.contains('?') || code.contains('R') {
        theme.accent
    } else if code.contains('D') || code.contains('U') {
        theme.error
    } else if code.contains('A') || code.contains('C') {
        theme.success
    } else {
        theme.warning
    };
    Some((format!("{code} "), chars.collect(), color))
}

pub(crate) fn render_command_output_preview(
    output: &[String],
    width: usize,
    theme: &ThemePalette,
) -> Vec<Line<'static>> {
    render_command_output_preview_with_transcript(output, width, theme, true)
}

pub(crate) fn render_command_output_preview_with_transcript(
    output: &[String],
    width: usize,
    theme: &ThemePalette,
    transcript_complete: bool,
) -> Vec<Line<'static>> {
    const MAX_VISUAL_ROWS: usize = 5;
    if output.is_empty() || output.iter().all(|line| line.trim().is_empty()) {
        return Vec::new();
    }
    let mut visual_rows = Vec::new();
    for (logical_index, text) in output.iter().enumerate() {
        let line = render_command_output_line(text, theme);
        let wrapped = adaptive_wrap_line(&line, RtOptions::new(width.max(1)));
        for row in wrapped.iter().map(line_to_static) {
            visual_rows.push((logical_index, row));
        }
    }
    if visual_rows.len() <= MAX_VISUAL_ROWS {
        return visual_rows.into_iter().map(|(_, line)| line).collect();
    }

    let head_rows = 2;
    let tail_rows = 2;
    let mut rendered = visual_rows[..head_rows]
        .iter()
        .map(|(_, line)| line.clone())
        .collect::<Vec<_>>();
    let first_tail_index = visual_rows.len().saturating_sub(tail_rows);
    let omitted_logical = output
        .iter()
        .enumerate()
        .filter(|(index, _)| {
            !visual_rows[..head_rows]
                .iter()
                .chain(visual_rows[first_tail_index..].iter())
                .any(|(kept, _)| kept == index)
        })
        .count();
    let omitted_visual = visual_rows.len().saturating_sub(head_rows + tail_rows);
    let omitted = omitted_logical.max(omitted_visual).max(1);
    rendered.push(Line::from(Span::styled(
        if transcript_complete {
            format!("… +{omitted} lines (ctrl + t to view transcript)")
        } else {
            format!("… +{omitted} lines (output incomplete)")
        },
        Style::default().fg(theme.text_muted),
    )));
    rendered.extend(
        visual_rows[first_tail_index..]
            .iter()
            .map(|(_, line)| line.clone()),
    );
    rendered
}

#[cfg(test)]
mod tests {
    use super::*;
    use unicode_width::UnicodeWidthStr;

    fn rendered_text(lines: Vec<Line<'static>>) -> Vec<String> {
        lines
            .into_iter()
            .map(|line| {
                line.spans
                    .into_iter()
                    .map(|span| span.content.into_owned())
                    .collect::<String>()
            })
            .collect()
    }

    #[test]
    fn numbered_cjk_tool_output_expands_tabs_before_layout_and_preserves_ansi_color() {
        let theme = ThemePalette::default();
        let source = "\x1b[31m     41\t### 3.2 侵入式言语产生：共享神经映射与跨患者对齐\x1b[0m";
        let rendered = render_command_output_line(source, &theme);
        assert!(rendered.to_string().starts_with("     41 ###"));
        assert!(!rendered.to_string().contains('\t'));
        assert!(rendered
            .spans
            .iter()
            .any(|span| span.style.fg == Some(Color::Red)));
        for width in [36, 76, 116] {
            let lines = render_command_output_preview(&[source.to_string()], width, &theme);
            assert!(lines.iter().all(|line| line.width() <= width), "{lines:?}");
        }
    }

    #[test]
    fn shell_command_contains_multiple_syntax_styles() {
        let theme = ThemePalette::default();
        let lines = render_shell_command(
            "pdftotext -layout 'paper.pdf' - | grep -n 'Methods' | sed -n '1,20p'",
            120,
            Line::from("Ran "),
            Line::from("  "),
            &theme,
        );
        let colors = lines
            .iter()
            .flat_map(|line| line.spans.iter().filter_map(|span| span.style.fg))
            .fold(Vec::new(), |mut colors, color| {
                if !colors.contains(&color) {
                    colors.push(color);
                }
                colors
            });
        assert!(colors.len() > 1, "expected Bash syntax colors: {lines:?}");
    }

    #[test]
    fn ansi_output_retains_foreground_and_dim_modifier() {
        let line = render_command_output_line("plain \x1b[31mred\x1b[0m", &ThemePalette::default());
        assert!(line
            .spans
            .iter()
            .any(|span| span.style.fg == Some(Color::Red)));
        assert!(line
            .spans
            .iter()
            .all(|span| span.style.add_modifier.contains(Modifier::DIM)));
    }

    #[test]
    fn command_preview_keeps_head_and_tail_with_omission_marker() {
        let output = (1..=8)
            .map(|value| format!("line {value}"))
            .collect::<Vec<_>>();
        let lines = render_command_output_preview(&output, 80, &ThemePalette::default());
        let text = lines
            .iter()
            .map(|line| {
                line.spans
                    .iter()
                    .map(|span| span.content.as_ref())
                    .collect::<String>()
            })
            .collect::<Vec<_>>();
        assert_eq!(text.len(), 5);
        assert_eq!(text[0], "line 1");
        assert_eq!(text[1], "line 2");
        assert!(text[2].contains("+4 lines"));
        assert_eq!(text[3], "line 7");
        assert_eq!(text[4], "line 8");
    }

    #[test]
    fn incomplete_command_preview_never_promises_ctrl_t_full_output() {
        let output = (1..=8)
            .map(|value| format!("line {value}"))
            .collect::<Vec<_>>();
        let text = render_command_output_preview_with_transcript(
            &output,
            80,
            &ThemePalette::default(),
            false,
        )
        .iter()
        .map(|line| {
            line.spans
                .iter()
                .map(|span| span.content.as_ref())
                .collect::<String>()
        })
        .collect::<Vec<_>>()
        .join("\n");

        assert!(text.contains("output incomplete"), "{text}");
        assert!(!text.contains("ctrl + t"), "{text}");
    }

    #[test]
    fn git_porcelain_status_uses_semantic_colors() {
        let theme = ThemePalette::default();
        for (row, expected) in [
            (" M edited.py", theme.warning),
            ("A  added.py", theme.success),
            ("D  deleted.py", theme.error),
            ("?? untracked.py", theme.accent),
        ] {
            let rendered = render_command_output_line(row, &theme);
            assert_eq!(rendered.spans[0].style.fg, Some(expected), "{row}");
        }
    }

    #[test]
    fn exec_block_uses_codex_command_and_output_gutters() {
        let theme = ThemePalette::default();
        let lines = rendered_text(render_exec_command_block(
            "git status --short --branch; rg -n 'cargo test' Makefile README.md\nfind src -name Cargo.toml -o -name Cargo.lock",
            200,
            Line::from("⦁ Ran "),
            vec![Line::from("## feat/exec-layout"), Line::from("M src/ui.rs")],
            None,
            Some("0.0s"),
            &theme,
        ));

        assert_eq!(
            lines,
            vec![
                "⦁ Ran git status --short --branch; rg -n 'cargo test' Makefile README.md",
                "  │ find src -name Cargo.toml -o -name Cargo.lock  · 0.0s",
                "  └ ## feat/exec-layout",
                "    M src/ui.rs",
            ]
        );
    }

    #[test]
    fn exec_block_keeps_every_wrapped_command_row() {
        let theme = ThemePalette::default();
        let command = (1..=8)
            .map(|index| format!("segment-{index}"))
            .collect::<Vec<_>>()
            .join(" ");
        let lines = rendered_text(render_exec_command_block(
            &command,
            24,
            Line::from("⦁ Ran "),
            Vec::new(),
            None,
            None,
            &theme,
        ));

        assert!(
            lines.len() > 3,
            "command rows must not be capped: {lines:?}"
        );
        assert!(
            lines.iter().skip(1).all(|line| line.starts_with("  │ ")),
            "all continuation rows must retain the branch: {lines:?}"
        );
        assert!(
            lines.iter().all(|line| line.width() <= 24),
            "every row must fit the viewport: {lines:?}"
        );
    }

    #[test]
    fn exec_block_moves_overflowing_duration_to_command_branch() {
        let theme = ThemePalette::default();
        let lines = rendered_text(render_exec_command_block(
            "cargo test --workspace",
            28,
            Line::from("⦁ Ran "),
            Vec::new(),
            None,
            Some("123m 45.6s"),
            &theme,
        ));

        assert_eq!(lines.last().map(String::as_str), Some("  │ · 123m 45.6s"));
        assert!(lines.iter().all(|line| line.width() <= 28));
    }

    #[test]
    fn exec_block_omits_empty_output_branch() {
        let theme = ThemePalette::default();
        let lines = rendered_text(render_exec_command_block(
            "true",
            80,
            Line::from("⦁ Ran "),
            Vec::new(),
            None,
            None,
            &theme,
        ));

        assert_eq!(lines, vec!["⦁ Ran true"]);
        assert!(!lines.iter().any(|line| line.contains("└")));
        assert!(!lines.iter().any(|line| line.contains("no output")));
    }

    #[test]
    fn whitespace_only_command_output_has_no_preview_rows() {
        let output = vec![String::new(), "   ".to_string(), "\t".to_string()];
        assert!(render_command_output_preview(&output, 80, &ThemePalette::default()).is_empty());
    }
}
