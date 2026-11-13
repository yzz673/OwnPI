use std::fmt;
use std::io;
use std::io::Write;

use crossterm::cursor::{MoveDown, MoveTo, MoveToColumn, RestorePosition, SavePosition};
use crossterm::queue;
use crossterm::style::{
    Attribute as CAttribute, Color as CColor, Colors, Print, SetAttribute, SetBackgroundColor,
    SetColors, SetForegroundColor,
};
use crossterm::terminal::{Clear, ClearType};
use crossterm::Command;
use ratatui::layout::{Position, Rect, Size};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use unicode_width::UnicodeWidthStr;

#[cfg(test)]
use crate::terminal_hyperlinks::plain_hyperlink_lines;
use crate::terminal_hyperlinks::{adaptive_wrap_hyperlink_lines, decorate_spans, HyperlinkLine};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum HistoryLineWrapPolicy {
    PreWrap,
    Terminal,
}

#[cfg(test)]
pub(crate) fn insert_history_lines<W: Write>(
    writer: &mut W,
    viewport_area: Rect,
    screen_size: Size,
    last_cursor_pos: Position,
    lines: Vec<Line<'static>>,
) -> io::Result<(Rect, u16)> {
    insert_history_hyperlink_lines(
        writer,
        viewport_area,
        screen_size,
        last_cursor_pos,
        plain_hyperlink_lines(lines),
    )
}

#[cfg(test)]
pub(crate) fn insert_history_hyperlink_lines<W: Write>(
    writer: &mut W,
    viewport_area: Rect,
    screen_size: Size,
    last_cursor_pos: Position,
    lines: Vec<HyperlinkLine>,
) -> io::Result<(Rect, u16)> {
    insert_history_hyperlink_lines_with_wrap_policy(
        writer,
        viewport_area,
        screen_size,
        last_cursor_pos,
        lines,
        HistoryLineWrapPolicy::PreWrap,
    )
}

/// The caller retires live pixels before insertion and redraws them afterwards.
/// Use ordinary full-screen scrolling in every terminal: partial scroll regions
/// may discard scrollback, and a zero/one-row history region is invalid.
pub(crate) fn insert_history_hyperlink_lines_with_wrap_policy<W: Write>(
    writer: &mut W,
    viewport_area: Rect,
    screen_size: Size,
    last_cursor_pos: Position,
    lines: Vec<HyperlinkLine>,
    wrap_policy: HistoryLineWrapPolicy,
) -> io::Result<(Rect, u16)> {
    insert_history_lines_with_wrap_policy(
        writer,
        viewport_area,
        screen_size,
        last_cursor_pos,
        lines,
        wrap_policy,
    )
}

pub(crate) fn clear_live_viewport_before_history_insert<W: Write>(
    writer: &mut W,
    viewport_area: Rect,
) -> io::Result<()> {
    if viewport_area.is_empty() {
        return Ok(());
    }
    queue!(
        writer,
        MoveTo(0, viewport_area.top()),
        Clear(ClearType::FromCursorDown)
    )
}

fn insert_history_lines_with_wrap_policy<W: Write>(
    writer: &mut W,
    viewport_area: Rect,
    screen_size: Size,
    last_cursor_pos: Position,
    lines: Vec<HyperlinkLine>,
    wrap_policy: HistoryLineWrapPolicy,
) -> io::Result<(Rect, u16)> {
    if lines.is_empty() || screen_size.width == 0 || screen_size.height == 0 {
        return Ok((viewport_area, 0));
    }

    let mut area = viewport_area;
    let wrap_width = area.width.max(1) as usize;
    let wrapped = wrap_hyperlink_lines(lines, wrap_width, wrap_policy);
    let wrapped_rows = wrapped
        .iter()
        .map(|line| line_width(&line.line).max(1).div_ceil(wrap_width))
        .sum::<usize>() as u16;

    if wrapped_rows == 0 {
        return Ok((area, 0));
    }

    queue!(writer, ResetScrollRegion, MoveTo(0, area.top()))?;
    for (index, line) in wrapped.iter().enumerate() {
        if index > 0 {
            queue!(writer, Print("\r\n"))?;
        }
        write_history_hyperlink_line(writer, line, wrap_width)?;
    }
    for _ in 0..area.height {
        queue!(writer, Print("\r\n"), Clear(ClearType::UntilNewLine))?;
    }
    queue!(writer, MoveTo(last_cursor_pos.x, last_cursor_pos.y))?;
    area.y = area
        .top()
        .saturating_add(wrapped_rows)
        .min(screen_size.height.saturating_sub(area.height));

    Ok((area, wrapped_rows))
}

fn wrap_hyperlink_lines(
    lines: Vec<HyperlinkLine>,
    wrap_width: usize,
    wrap_policy: HistoryLineWrapPolicy,
) -> Vec<HyperlinkLine> {
    let mut wrapped = Vec::new();
    for line in lines {
        let line_wrapped = match wrap_policy {
            HistoryLineWrapPolicy::Terminal => vec![line.clone()],
            HistoryLineWrapPolicy::PreWrap
                if crate::wrapping::line_contains_url_like(&line.line)
                    && !crate::wrapping::line_has_mixed_url_and_non_url_tokens(&line.line) =>
            {
                vec![line.clone()]
            }
            HistoryLineWrapPolicy::PreWrap => adaptive_wrap_hyperlink_lines(
                std::slice::from_ref(&line),
                crate::wrapping::RtOptions::new(wrap_width)
                    .subsequent_indent(leading_whitespace_prefix(&line.line)),
            ),
        };
        wrapped.extend(line_wrapped);
    }
    wrapped
}

fn leading_whitespace_prefix(line: &Line<'_>) -> Line<'static> {
    let mut spans = Vec::new();
    for span in &line.spans {
        let prefix_end = span
            .content
            .char_indices()
            .find_map(|(index, ch)| (!ch.is_whitespace()).then_some(index))
            .unwrap_or(span.content.len());
        if prefix_end > 0 {
            spans.push(Span::styled(
                span.content[..prefix_end].to_string(),
                span.style,
            ));
        }
        if prefix_end < span.content.len() {
            break;
        }
    }
    Line::from(spans).style(line.style)
}

fn write_history_hyperlink_line<W: Write>(
    writer: &mut W,
    line: &HyperlinkLine,
    wrap_width: usize,
) -> io::Result<()> {
    let physical_rows = line_width(&line.line).max(1).div_ceil(wrap_width) as u16;
    if physical_rows > 1 {
        queue!(writer, SavePosition)?;
        for _ in 1..physical_rows {
            queue!(writer, MoveDown(1), MoveToColumn(0))?;
            queue!(writer, Clear(ClearType::UntilNewLine))?;
        }
        queue!(writer, RestorePosition)?;
    }

    queue!(
        writer,
        SetColors(Colors::new(
            line.line
                .style
                .fg
                .map(color_to_crossterm)
                .unwrap_or(CColor::Reset),
            line.line
                .style
                .bg
                .map(color_to_crossterm)
                .unwrap_or(CColor::Reset),
        )),
        Clear(ClearType::UntilNewLine)
    )?;
    let visible_line = if should_pad_line_to_width(&line.line) {
        pad_line_to_width(line.line.clone(), printable_width(wrap_width))
    } else {
        line.line.clone()
    };
    let merged_spans = visible_line.spans.iter().map(|span| Span {
        style: span.style.patch(visible_line.style),
        content: span.content.clone(),
    });
    let decorated_line = HyperlinkLine {
        line: Line::from(merged_spans.collect::<Vec<_>>()),
        hyperlinks: line.hyperlinks.clone(),
    };
    let decorated = decorate_spans(&decorated_line);
    write_spans(writer, decorated)
}

#[cfg(test)]
fn write_history_line<W: Write>(
    writer: &mut W,
    line: &Line<'static>,
    wrap_width: usize,
) -> io::Result<()> {
    write_history_hyperlink_line(writer, &HyperlinkLine::new(line.clone()), wrap_width)
}

fn printable_width(wrap_width: usize) -> usize {
    wrap_width.saturating_sub(1).max(1)
}

fn pad_line_to_width(mut line: Line<'static>, width: usize) -> Line<'static> {
    let current_width = line_width(&line);
    if current_width < width {
        line.spans.push(Span::styled(
            " ".repeat(width - current_width),
            fill_style_for_line(&line),
        ));
    }
    line
}

fn should_pad_line_to_width(line: &Line<'_>) -> bool {
    // A row background explicitly owns the entire row. Span backgrounds belong
    // only to their glyphs (for example a bounded card or an inline highlight).
    line.style.bg.is_some()
}

fn fill_style_for_line(line: &Line<'_>) -> Style {
    let mut style = line.style;
    if style.bg.is_none() {
        style.bg = line.spans.iter().find_map(|span| span.style.bg);
    }
    if style.fg.is_none() {
        style.fg = line.spans.iter().find_map(|span| span.style.fg);
    }
    style
}

struct ModifierDiff {
    from: Modifier,
    to: Modifier,
}

impl ModifierDiff {
    fn queue<W: Write>(self, writer: &mut W) -> io::Result<()> {
        let removed = self.from - self.to;
        if removed.contains(Modifier::REVERSED) {
            queue!(writer, SetAttribute(CAttribute::NoReverse))?;
        }
        if removed.contains(Modifier::BOLD) {
            queue!(writer, SetAttribute(CAttribute::NormalIntensity))?;
            if self.to.contains(Modifier::DIM) {
                queue!(writer, SetAttribute(CAttribute::Dim))?;
            }
        }
        if removed.contains(Modifier::ITALIC) {
            queue!(writer, SetAttribute(CAttribute::NoItalic))?;
        }
        if removed.contains(Modifier::UNDERLINED) {
            queue!(writer, SetAttribute(CAttribute::NoUnderline))?;
        }
        if removed.contains(Modifier::DIM) {
            queue!(writer, SetAttribute(CAttribute::NormalIntensity))?;
        }

        let added = self.to - self.from;
        if added.contains(Modifier::REVERSED) {
            queue!(writer, SetAttribute(CAttribute::Reverse))?;
        }
        if added.contains(Modifier::BOLD) {
            queue!(writer, SetAttribute(CAttribute::Bold))?;
        }
        if added.contains(Modifier::ITALIC) {
            queue!(writer, SetAttribute(CAttribute::Italic))?;
        }
        if added.contains(Modifier::UNDERLINED) {
            queue!(writer, SetAttribute(CAttribute::Underlined))?;
        }
        if added.contains(Modifier::DIM) {
            queue!(writer, SetAttribute(CAttribute::Dim))?;
        }
        Ok(())
    }
}

fn write_spans<W, I>(writer: &mut W, content: I) -> io::Result<()>
where
    W: Write,
    I: IntoIterator<Item = Span<'static>>,
{
    let mut fg = Color::Reset;
    let mut bg = Color::Reset;
    let mut last_modifier = Modifier::empty();
    for span in content {
        let mut modifier = Modifier::empty();
        modifier.insert(span.style.add_modifier);
        modifier.remove(span.style.sub_modifier);
        if modifier != last_modifier {
            ModifierDiff {
                from: last_modifier,
                to: modifier,
            }
            .queue(writer)?;
            last_modifier = modifier;
        }
        let next_fg = span.style.fg.unwrap_or(Color::Reset);
        let next_bg = span.style.bg.unwrap_or(Color::Reset);
        if next_fg != fg || next_bg != bg {
            queue!(
                writer,
                SetColors(Colors::new(
                    color_to_crossterm(next_fg),
                    color_to_crossterm(next_bg)
                ))
            )?;
            fg = next_fg;
            bg = next_bg;
        }
        queue!(writer, Print(span.content))?;
    }
    queue!(
        writer,
        SetForegroundColor(CColor::Reset),
        SetBackgroundColor(CColor::Reset),
        SetAttribute(CAttribute::Reset),
    )
}

fn color_to_crossterm(color: Color) -> CColor {
    match color {
        Color::Reset => CColor::Reset,
        Color::Black => CColor::Black,
        Color::Red => CColor::DarkRed,
        Color::Green => CColor::DarkGreen,
        Color::Yellow => CColor::DarkYellow,
        Color::Blue => CColor::DarkBlue,
        Color::Magenta => CColor::DarkMagenta,
        Color::Cyan => CColor::DarkCyan,
        Color::Gray => CColor::Grey,
        Color::DarkGray => CColor::DarkGrey,
        Color::LightRed => CColor::Red,
        Color::LightGreen => CColor::Green,
        Color::LightYellow => CColor::Yellow,
        Color::LightBlue => CColor::Blue,
        Color::LightMagenta => CColor::Magenta,
        Color::LightCyan => CColor::Cyan,
        Color::White => CColor::White,
        Color::Rgb(r, g, b) => CColor::Rgb { r, g, b },
        Color::Indexed(index) => CColor::AnsiValue(index),
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
#[cfg(test)]
pub(crate) struct SetScrollRegion(pub(crate) std::ops::Range<u16>);

#[cfg(test)]
impl Command for SetScrollRegion {
    fn write_ansi(&self, f: &mut impl fmt::Write) -> fmt::Result {
        write!(f, "\x1b[{};{}r", self.0.start, self.0.end)
    }

    #[cfg(windows)]
    fn execute_winapi(&self) -> io::Result<()> {
        panic!("SetScrollRegion must be emitted as ANSI")
    }

    #[cfg(windows)]
    fn is_ansi_code_supported(&self) -> bool {
        true
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ResetScrollRegion;

impl Command for ResetScrollRegion {
    fn write_ansi(&self, f: &mut impl fmt::Write) -> fmt::Result {
        write!(f, "\x1b[r")
    }

    #[cfg(windows)]
    fn execute_winapi(&self) -> io::Result<()> {
        panic!("ResetScrollRegion must be emitted as ANSI")
    }

    #[cfg(windows)]
    fn is_ansi_code_supported(&self) -> bool {
        true
    }
}

fn line_width(line: &Line<'_>) -> usize {
    line.spans
        .iter()
        .map(|span| UnicodeWidthStr::width(span.content.as_ref()))
        .sum()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn set_scroll_region_emits_ansi_range() {
        let mut output = String::new();
        SetScrollRegion(1..24).write_ansi(&mut output).unwrap();
        assert_eq!(output, "\x1b[1;24r");
    }

    #[test]
    fn reset_scroll_region_emits_ansi_reset() {
        let mut output = String::new();
        ResetScrollRegion.write_ansi(&mut output).unwrap();
        assert_eq!(output, "\x1b[r");
    }

    #[test]
    fn insert_history_advances_viewport_by_inserted_rows_when_space_below() {
        let mut output = Vec::new();
        let (area, rows) = insert_history_lines(
            &mut output,
            Rect::new(0, 0, 80, 4),
            Size::new(80, 10),
            Position::new(0, 0),
            vec![Line::from("hello")],
        )
        .unwrap();

        assert_eq!(rows, 1);
        assert_eq!(area, Rect::new(0, 1, 80, 4));
        let text = String::from_utf8(output).unwrap();
        assert!(text.contains("hello"));
        assert!(
            !text.contains("\u{1b}[1;10r"),
            "top-of-screen short history should be written directly so the live viewport stays adjacent: {text:?}"
        );
        assert!(
            !text.contains("\u{1b}[J"),
            "history insertion should not clear the viewport while making room above it: {text:?}"
        );
    }

    #[test]
    fn short_history_insert_does_not_bottom_align_viewport() {
        let mut output = Vec::new();
        let (area, rows) = insert_history_lines(
            &mut output,
            Rect::new(0, 0, 80, 4),
            Size::new(80, 24),
            Position::new(0, 0),
            vec![Line::from("hello")],
        )
        .unwrap();

        assert_eq!(rows, 1);
        assert_eq!(
            area,
            Rect::new(0, 1, 80, 4),
            "one committed history row should move the live viewport by exactly one row, not pin it to the screen bottom"
        );
    }

    #[test]
    fn insert_history_keeps_bottom_aligned_viewport_at_bottom() {
        let mut output = Vec::new();
        let (area, rows) = insert_history_lines(
            &mut output,
            Rect::new(0, 6, 80, 4),
            Size::new(80, 10),
            Position::new(0, 6),
            vec![Line::from("hello")],
        )
        .unwrap();

        assert_eq!(rows, 1);
        assert_eq!(area, Rect::new(0, 6, 80, 4));
    }

    #[test]
    fn separate_history_flushes_keep_a_blank_row_and_first_column_boundary() {
        let width = 80;
        let height = 18;
        let screen = Size::new(width, height);
        let mut parser = vt100::Parser::new(height, width, 0);
        let viewport = Rect::new(0, 12, width, 4);
        let border = format!("╰{}╯", "─".repeat(width as usize - 3));

        let (viewport, first_rows) = insert_history_lines(
            &mut parser,
            viewport,
            screen,
            Position::new(0, viewport.y),
            vec![Line::from("⦁ Append Stage"), Line::from(border.clone())],
        )
        .unwrap();
        let (_viewport, second_rows) = insert_history_lines(
            &mut parser,
            viewport,
            screen,
            Position::new(0, viewport.y),
            vec![Line::from(""), Line::from("⦁ Not run · 4 tool calls")],
        )
        .unwrap();

        assert_eq!(first_rows, 2);
        assert_eq!(second_rows, 2);
        let rows = parser
            .screen()
            .rows(0, width)
            .map(|row| row.trim_end().to_string())
            .collect::<Vec<_>>();
        let border_row = rows
            .iter()
            .position(|row| row == &border)
            .expect("stage border row");
        let outcome_row = rows
            .iter()
            .position(|row| row == "⦁ Not run · 4 tool calls")
            .expect("batch outcome row");
        assert_eq!(outcome_row, border_row + 2, "{rows:?}");
        assert!(rows[border_row + 1].is_empty(), "{rows:?}");
        assert!(rows.iter().any(|row| row == "⦁ Append Stage"), "{rows:?}");
    }

    #[test]
    fn write_history_line_pads_styled_prompt_cells_to_row_width() {
        let prompt = Line::from(vec![
            Span::styled("› ", Style::default().bg(Color::Gray)),
            Span::styled("hello", Style::default().fg(Color::Black).bg(Color::Gray)),
        ])
        .style(Style::default().bg(Color::Gray));
        let mut output = Vec::new();

        write_history_line(&mut output, &prompt, 12).unwrap();

        let text = String::from_utf8(output).unwrap();
        assert!(text.contains("› "));
        assert!(text.contains("hello"));
        assert!(
            text.contains("hello    "),
            "expected trailing spaces to paint the full user prompt row: {text:?}",
        );
    }

    #[test]
    fn write_history_line_does_not_pad_plain_trace_rows() {
        let mut output = Vec::new();

        write_history_line(&mut output, &Line::from("• Ran pre-run task lock"), 32).unwrap();

        let text = String::from_utf8(output).unwrap();
        assert!(text.contains("• Ran pre-run task lock"));
        assert!(
            !text.contains("• Ran pre-run task lock          "),
            "plain trace rows must not emit tail padding that can wrap into the next row: {text:?}",
        );
    }

    #[test]
    fn write_history_line_does_not_pad_plain_blank_rows() {
        let mut output = Vec::new();

        write_history_line(&mut output, &Line::from(""), 32).unwrap();

        let text = String::from_utf8(output).unwrap();
        assert!(
            !text.contains("          "),
            "plain blank spacing rows should clear the row without printing filler spaces: {text:?}",
        );
    }

    #[test]
    fn insert_history_hyperlink_lines_emit_osc8_links() {
        let mut output = Vec::new();

        insert_history_hyperlink_lines(
            &mut output,
            Rect::new(0, 0, 80, 4),
            Size::new(80, 10),
            Position::new(0, 0),
            vec![HyperlinkLine {
                line: Line::from("https://example.com"),
                hyperlinks: vec![crate::terminal_hyperlinks::TerminalHyperlink {
                    columns: 0..19,
                    destination: "https://example.com".to_string(),
                }],
            }],
        )
        .unwrap();

        let text = String::from_utf8(output).unwrap();
        assert!(text.contains("\u{1b}]8;;https://example.com\u{7}"));
        assert!(text.contains("\u{1b}]8;;\u{7}"));
    }

    #[test]
    fn raw_history_uses_terminal_wrapping_without_inserting_source_newlines() {
        let mut output = Vec::new();
        let source = "abcdefghij";

        let (_area, rows) = insert_history_hyperlink_lines_with_wrap_policy(
            &mut output,
            Rect::new(0, 0, 4, 2),
            Size::new(4, 8),
            Position::new(0, 0),
            vec![HyperlinkLine::new(Line::from(source))],
            HistoryLineWrapPolicy::Terminal,
        )
        .unwrap();

        let emitted = String::from_utf8(output).unwrap();
        assert_eq!(rows, 3);
        assert!(emitted.contains(source));
        assert!(!emitted.contains("abcd\r\n"));
        assert!(!emitted.contains("efgh\r\n"));
    }

    #[test]
    fn history_insert_leaves_viewport_retirement_to_frame_owner() {
        let mut output = Vec::new();

        insert_history_lines(
            &mut output,
            Rect::new(0, 6, 80, 4),
            Size::new(80, 10),
            Position::new(0, 6),
            vec![Line::from("• Ran pre-run task lock")],
        )
        .unwrap();

        let text = String::from_utf8(output).unwrap();
        assert!(
            !text.contains("\u{1b}[J"),
            "history insertion must not redundantly clear the viewport: {text:?}",
        );
    }

    #[test]
    fn one_row_history_region_preserves_every_outcome_line() {
        let screen = Size::new(80, 12);
        let viewport = Rect::new(0, 1, 80, 11);
        let mut parser = vt100::Parser::new(12, 80, 100);
        queue!(parser, MoveTo(0, 0), Print("earlier history")).unwrap();
        clear_live_viewport_before_history_insert(&mut parser, viewport).unwrap();
        let (next, _) = insert_history_lines(
            &mut parser,
            viewport,
            screen,
            Position::new(0, 1),
            vec![Line::from("stage failure"), Line::from("task failure")],
        )
        .unwrap();
        queue!(
            parser,
            MoveTo(0, next.y),
            Print("RECOVERY"),
            MoveTo(0, next.y + 1),
            Print("Retry / End")
        )
        .unwrap();
        let mut captured = parser.screen().contents();
        parser.screen_mut().set_scrollback(100);
        captured.push_str(&parser.screen().contents());
        for expected in ["earlier history", "stage failure", "task failure"] {
            assert!(captured.contains(expected), "lost {expected}: {captured}");
        }
    }

    #[test]
    fn vt100_viewport_clear_before_history_insert_keeps_stale_live_panel_out_of_scrollback() {
        let width = 96;
        let height = 14;
        let screen = Size::new(width, height);
        let mut parser = vt100::Parser::new(height, width, 0);
        let mut viewport = Rect::new(0, 6, width, 5);

        queue!(
            parser,
            MoveTo(0, 0),
            Print("• Previous committed history"),
            MoveTo(0, viewport.y),
            Print("╭Question requested────────────────────────────╮"),
            MoveTo(0, viewport.y + 1),
            Print("│ 3. stale option copied from old bottom pane   │"),
            MoveTo(0, viewport.y + 2),
            Print("│ Enter to submit answer | Esc to interrupt     │"),
        )
        .unwrap();

        clear_live_viewport_before_history_insert(&mut parser, viewport).unwrap();
        let (next_viewport, rows) = insert_history_lines(
            &mut parser,
            viewport,
            screen,
            Position::new(0, viewport.y),
            vec![Line::from("• Ran human question tool")],
        )
        .unwrap();
        viewport = next_viewport;

        queue!(
            parser,
            MoveTo(0, viewport.y),
            Print("╭Question requested────────────────────────────╮"),
            MoveTo(0, viewport.y + 1),
            Print("│ 1. current option in active bottom pane       │"),
        )
        .unwrap();

        assert_eq!(rows, 1);
        let screen_rows = parser
            .screen()
            .rows(0, width)
            .map(|row| row.trim_end().to_string())
            .collect::<Vec<_>>();
        assert!(
            screen_rows
                .iter()
                .any(|row| row.contains("• Previous committed history")),
            "committed history above the viewport must remain visible: {screen_rows:?}"
        );
        assert!(
            screen_rows
                .iter()
                .any(|row| row.contains("• Ran human question tool")),
            "new committed history should be inserted before the active bottom pane: {screen_rows:?}"
        );
        assert!(
            screen_rows
                .iter()
                .any(|row| row.contains("1. current option")),
            "active bottom pane should be redrawn after history insertion: {screen_rows:?}"
        );
        assert!(
            !screen_rows.iter().any(|row| row.contains("stale option")),
            "old transient bottom-pane rows must not be moved into scrollback: {screen_rows:?}"
        );
    }

    #[test]
    fn follow_up_prompt_insert_does_not_clear_prior_answer_rows() {
        let mut output = Vec::new();
        let screen = Size::new(80, 12);
        let viewport = Rect::new(0, 8, 80, 4);
        let (viewport, first_rows) = insert_history_lines(
            &mut output,
            viewport,
            screen,
            Position::new(0, 8),
            vec![
                Line::from("• 你好"),
                Line::from(""),
                Line::from(
                    "─ Worked for 4s ─────────────────────────────────────────────────────────",
                ),
            ],
        )
        .unwrap();
        let (_viewport, second_rows) = insert_history_lines(
            &mut output,
            viewport,
            screen,
            Position::new(0, viewport.y),
            vec![Line::from("› 继续")],
        )
        .unwrap();

        assert_eq!(first_rows, 3);
        assert_eq!(second_rows, 1);
        let text = String::from_utf8(output).unwrap();
        assert!(
            !text.contains("\u{1b}[J"),
            "follow-up prompt history insertion must not clear prior answer rows: {text:?}"
        );
        assert!(text.contains("• 你好"));
        assert!(text.contains("Worked for 4s"));
        assert!(text.contains("› 继续"));
    }

    #[test]
    fn vt100_follow_up_prompt_after_bottom_viewport_growth_preserves_prior_answer_tail() {
        let width = 112;
        let height = 24;
        let screen = Size::new(width, height);
        let mut parser = vt100::Parser::new(height, width, 0);
        let mut viewport = Rect::new(0, 18, width, 4);

        let (next_viewport, rows) = insert_history_lines(
            &mut parser,
            viewport,
            screen,
            Position::new(0, viewport.y),
            vec![
                Line::from("• 你好"),
                Line::from(""),
                Line::from(
                    "─ Worked for 4s ─────────────────────────────────────────────────────────",
                ),
            ],
        )
        .unwrap();
        assert_eq!(rows, 3);
        viewport = next_viewport;
        assert_eq!(viewport.bottom(), height);

        let scroll_by = 2;
        queue!(parser, SetScrollRegion(1..viewport.top())).unwrap();
        queue!(parser, MoveTo(0, viewport.top().saturating_sub(1))).unwrap();
        for _ in 0..scroll_by {
            queue!(parser, Print("\n"), Clear(ClearType::UntilNewLine)).unwrap();
        }
        queue!(parser, ResetScrollRegion).unwrap();
        viewport.y = height.saturating_sub(6);
        queue!(
            parser,
            MoveTo(0, viewport.y),
            Clear(ClearType::FromCursorDown)
        )
        .unwrap();

        let (viewport, follow_up_rows) = insert_history_lines(
            &mut parser,
            viewport,
            screen,
            Position::new(0, viewport.y),
            vec![Line::from("› 继续")],
        )
        .unwrap();
        assert_eq!(follow_up_rows, 1);
        queue!(
            parser,
            MoveTo(0, viewport.y),
            Clear(ClearType::UntilNewLine),
            MoveTo(0, viewport.y + 1),
            Clear(ClearType::UntilNewLine),
            Print("• Working (0s • esc to interrupt)")
        )
        .unwrap();

        let screen_rows = parser
            .screen()
            .rows(0, width)
            .map(|row| row.trim_end().to_string())
            .collect::<Vec<_>>();

        let answer_index = screen_rows
            .iter()
            .position(|row| row.contains("• 你好"))
            .expect("answer row should survive viewport growth");
        let worked_index = screen_rows
            .iter()
            .position(|row| row.contains("─ Worked for 4s"))
            .expect("worked separator should survive viewport growth");
        let follow_up_index = screen_rows
            .iter()
            .position(|row| row.contains("› 继续"))
            .expect("follow-up prompt should be inserted after prior answer");
        let working_index = screen_rows
            .iter()
            .position(|row| row.contains("• Working"))
            .expect("working row should render after follow-up prompt");

        assert_eq!(worked_index, answer_index + 2, "{screen_rows:?}");
        assert_eq!(follow_up_index, worked_index + 1, "{screen_rows:?}");
        assert_eq!(working_index, follow_up_index + 2, "{screen_rows:?}");
    }

    #[test]
    fn vt100_short_history_does_not_leave_large_gap_before_viewport() {
        let width = 80;
        let height = 24;
        let mut parser = vt100::Parser::new(height, width, 0);
        let viewport = Rect::new(0, 0, width, 4);
        let (viewport, rows) = insert_history_lines(
            &mut parser,
            viewport,
            Size::new(width, height),
            Position::new(0, 0),
            vec![Line::from("• Task failed before producing a final answer.")],
        )
        .unwrap();

        assert_eq!(rows, 1);
        assert_eq!(
            viewport.y, 1,
            "short history must advance the live viewport by inserted rows only"
        );
        let screen_rows = parser
            .screen()
            .rows(0, width)
            .map(|row| row.trim_end().to_string())
            .collect::<Vec<_>>();
        assert!(
            screen_rows
                .first()
                .is_some_and(|row| row.contains("• Task failed before producing a final answer.")),
            "history should be written directly above the viewport: {screen_rows:?}"
        );
        let first_nonblank_after_history = screen_rows
            .iter()
            .enumerate()
            .skip(1)
            .find_map(|(index, row)| (!row.trim().is_empty()).then_some(index));
        assert!(
            first_nonblank_after_history.is_none_or(|index| index <= 2),
            "short history insertion must not create a large blank gap before live viewport rows: {screen_rows:?}"
        );
    }

    #[test]
    fn vt100_resume_rebuild_after_final_viewport_resize_preserves_history() {
        let width = 80;
        let height = 24;
        let screen = Size::new(width, height);
        let mut parser = vt100::Parser::new(height, width, 0);
        let final_live_viewport = Rect::new(0, 0, width, 5);
        let history = (0..28)
            .map(|index| Line::from(format!("resume-history-{index:02}")))
            .collect::<Vec<_>>();

        let (live_viewport, rows) = insert_history_lines(
            &mut parser,
            final_live_viewport,
            screen,
            Position::new(0, 0),
            history,
        )
        .unwrap();
        queue!(
            parser,
            MoveTo(0, live_viewport.y),
            Print("Message OwnPI"),
            MoveTo(0, live_viewport.y + 1),
            Print("ready")
        )
        .unwrap();

        assert_eq!(rows, 28);
        assert_eq!(live_viewport, Rect::new(0, 19, width, 5));
        let screen_rows = parser
            .screen()
            .rows(0, width)
            .map(|row| row.trim_end().to_string())
            .collect::<Vec<_>>();
        assert!(
            screen_rows
                .iter()
                .any(|row| row.contains("resume-history-27")),
            "the authoritative resume tail must remain above the live viewport: {screen_rows:?}"
        );
        assert_eq!(screen_rows[19], "Message OwnPI", "{screen_rows:?}");
        assert_eq!(screen_rows[20], "ready", "{screen_rows:?}");
    }

    #[test]
    fn vt100_repeated_history_inserts_do_not_preserve_live_blank_rows_between_cells() {
        let width = 80;
        let height = 24;
        let screen = Size::new(width, height);
        let mut parser = vt100::Parser::new(height, width, 0);
        let viewport = Rect::new(0, 0, width, 8);

        let (viewport, rows) = insert_history_lines(
            &mut parser,
            viewport,
            screen,
            Position::new(0, 0),
            vec![
                Line::from("• Ran pre-run task lock"),
                Line::from("  └ Output built shared task_lock artifact"),
            ],
        )
        .unwrap();
        assert_eq!(rows, 2);

        for row in viewport.y..viewport.bottom() {
            queue!(parser, MoveTo(0, row), Clear(ClearType::UntilNewLine)).unwrap();
        }

        let (_viewport, rows) = insert_history_lines(
            &mut parser,
            viewport,
            screen,
            Position::new(0, viewport.y),
            vec![
                Line::from("• Explored (2)"),
                Line::from("  └ Context for core@direct_answer: 39s/0m summary-only"),
            ],
        )
        .unwrap();
        assert_eq!(rows, 2);

        let screen_rows = parser
            .screen()
            .rows(0, width)
            .map(|row| row.trim_end().to_string())
            .collect::<Vec<_>>();
        let ran_index = screen_rows
            .iter()
            .position(|row| row.contains("• Ran pre-run task lock"))
            .expect("ran row should be present");
        let explored_index = screen_rows
            .iter()
            .position(|row| row.contains("• Explored (2)"))
            .expect("explored row should be present");
        let gap = screen_rows[ran_index + 1..explored_index]
            .iter()
            .filter(|row| row.trim().is_empty())
            .count();

        assert!(
            gap <= 1,
            "live viewport blank rows must not be preserved between committed history cells: gap={gap}, rows={screen_rows:?}"
        );
    }

    #[test]
    fn vt100_history_insert_after_live_status_keeps_next_status_adjacent() {
        let width = 112;
        let height = 24;
        let screen = Size::new(width, height);
        let mut parser = vt100::Parser::new(height, width, 0);
        let mut viewport = Rect::new(0, 0, width, 6);

        let (next_viewport, _rows) = insert_history_lines(
            &mut parser,
            viewport,
            screen,
            Position::new(0, 0),
            vec![
                Line::from("• Interpreted Task"),
                Line::from("  - mode: direct_qa"),
                Line::from("  - objective: 你好"),
            ],
        )
        .unwrap();
        viewport = next_viewport;

        queue!(
            parser,
            MoveTo(0, viewport.y),
            Clear(ClearType::UntilNewLine),
            MoveTo(0, viewport.y + 1),
            Clear(ClearType::UntilNewLine),
            Print("• Working (0s • esc to interrupt)"),
            MoveTo(0, viewport.y + 2),
            Clear(ClearType::UntilNewLine),
            Print("  └ pre-run"),
        )
        .unwrap();

        let (next_viewport, _rows) = insert_history_lines(
            &mut parser,
            viewport,
            screen,
            Position::new(0, viewport.y),
            vec![
                Line::from("• Ran pre-run task lock"),
                Line::from("  └ Output built shared task_lock artifact"),
            ],
        )
        .unwrap();
        viewport = next_viewport;

        queue!(
            parser,
            MoveTo(0, viewport.y),
            Clear(ClearType::UntilNewLine),
            MoveTo(0, viewport.y + 1),
            Clear(ClearType::UntilNewLine),
            Print("• Working (0s • esc to interrupt)"),
            MoveTo(0, viewport.y + 2),
            Clear(ClearType::UntilNewLine),
            Print("  └ agent response:direct answer"),
        )
        .unwrap();

        let screen_rows = parser
            .screen()
            .rows(0, width)
            .map(|row| row.trim_end().to_string())
            .collect::<Vec<_>>();
        let ran_index = screen_rows
            .iter()
            .position(|row| row.contains("• Ran pre-run task lock"))
            .expect("ran row should be present");
        let working_index = screen_rows
            .iter()
            .position(|row| row.contains("• Working"))
            .expect("working row should be present");
        let gap = screen_rows[ran_index + 2..working_index]
            .iter()
            .filter(|row| row.trim().is_empty())
            .count();

        assert_eq!(
            gap, 1,
            "live status should keep one boundary row after history flush: rows={screen_rows:?}"
        );
    }

    #[test]
    fn repeated_short_history_inserts_keep_viewport_adjacent_to_history_not_screen_bottom() {
        let width = 80;
        let height = 24;
        let screen = Size::new(width, height);
        let mut output = Vec::new();
        let mut viewport = Rect::new(0, 0, width, 4);
        let batches = [
            "• Interpreted Task",
            "• Ran pre-run task lock",
            "• Explored (2)",
            "• Failed (0s)",
            "• Task failed before producing a final answer.",
        ];

        for batch in batches {
            let (next_viewport, _rows) = insert_history_lines(
                &mut output,
                viewport,
                screen,
                Position::new(0, viewport.y),
                vec![Line::from(batch)],
            )
            .unwrap();
            viewport = next_viewport;
        }

        assert_eq!(
            viewport.y, batches.len() as u16,
            "short history batches should keep the live viewport directly below committed rows instead of bottom-aligning it"
        );
        assert!(
            viewport.bottom() < height,
            "short committed history must not force the composer to the bottom of the terminal"
        );
    }

    #[test]
    fn vt100_answer_history_then_prompt_has_no_bottom_gap() {
        let width = 112;
        let height = 24;
        let screen = Size::new(width, height);
        let mut parser = vt100::Parser::new(height, width, 0);
        let mut viewport = Rect::new(0, 0, width, 4);

        let (next_viewport, rows) = insert_history_lines(
            &mut parser,
            viewport,
            screen,
            Position::new(0, 0),
            vec![
                Line::from("• 你好"),
                Line::from(""),
                Line::from(
                    "─ Worked for 4s ─────────────────────────────────────────────────────────",
                ),
            ],
        )
        .unwrap();
        assert_eq!(rows, 3);
        viewport = next_viewport;

        queue!(
            parser,
            MoveTo(0, viewport.y),
            Clear(ClearType::UntilNewLine),
            Print("> Message OwnPI"),
        )
        .unwrap();

        let screen_rows = parser
            .screen()
            .rows(0, width)
            .map(|row| row.trim_end().to_string())
            .collect::<Vec<_>>();
        let worked_index = screen_rows
            .iter()
            .position(|row| row.contains("─ Worked for 4s"))
            .expect("worked separator should be present");
        let prompt_index = screen_rows
            .iter()
            .position(|row| row.contains("> Message OwnPI"))
            .expect("prompt row should be present");

        assert_eq!(
            prompt_index,
            worked_index + 1,
            "prompt should render immediately after worked separator in actual terminal rows: rows={screen_rows:?}"
        );
        assert!(
            prompt_index < (height as usize / 2),
            "prompt must not be bottom-pinned after a short completed turn: rows={screen_rows:?}"
        );
    }

    #[test]
    fn prewrap_preserves_three_line_user_prompt_cell_shape() {
        let style = Style::default().bg(Color::Gray);
        let lines = vec![
            Line::from(Span::styled("           ", style)).style(style),
            Line::from(vec![
                Span::styled("› ", style),
                Span::styled("hello", style),
                Span::styled("    ", style),
            ])
            .style(style),
            Line::from(Span::styled("           ", style)).style(style),
        ];

        let wrapped = wrap_hyperlink_lines(
            lines.into_iter().map(HyperlinkLine::plain).collect(),
            12,
            HistoryLineWrapPolicy::PreWrap,
        );
        let rendered = wrapped
            .iter()
            .map(|line| crate::wrapping::line_plain_text(&line.line))
            .collect::<Vec<_>>();

        assert_eq!(rendered.len(), 3);
        assert!(rendered[0].chars().all(|ch| ch == ' '));
        assert!(rendered[1].starts_with("› hello"));
        assert!(rendered[2].chars().all(|ch| ch == ' '));
    }
}
