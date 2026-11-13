use std::cell::Cell;

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span, Text};
use ratatui::widgets::{Block, BorderType, Borders, Clear, Paragraph, Wrap};

use crate::theme::ThemePalette;
use unicode_width::UnicodeWidthStr;

#[derive(Clone, Debug, Default)]
pub(crate) struct TranscriptPagerState {
    lines: Vec<Line<'static>>,
    scroll: usize,
    visible_height: Cell<usize>,
    pub(crate) width: usize,
    pub(crate) committed_len: usize,
    pub(crate) live_tail_len: usize,
    /// Static content (e.g. /diff) is never replaced by the live-tail refresh.
    pub(crate) static_content: bool,
    pub(crate) title_override: Option<String>,
    pub(crate) footer_override: Option<String>,
}

impl TranscriptPagerState {
    pub(crate) fn new(lines: Vec<Line<'static>>, width: usize) -> Self {
        let lines = physical_rows(&lines, width);
        Self {
            committed_len: lines.len(),
            live_tail_len: 0,
            lines,
            scroll: 0,
            visible_height: Cell::new(0),
            width,
            static_content: false,
            title_override: None,
            footer_override: None,
        }
    }

    pub(crate) fn title(&self) -> &str {
        self.title_override.as_deref().unwrap_or("Transcript")
    }

    pub(crate) fn transcript_is_stale(&self, lines: &[Line<'static>], width: usize) -> bool {
        self.width != width || self.lines != physical_rows(lines, width)
    }

    pub(crate) fn update_lines(
        &mut self,
        lines: Vec<Line<'static>>,
        width: usize,
        live_tail_len: usize,
    ) {
        let split = lines.len().saturating_sub(live_tail_len);
        let mut committed = physical_rows(&lines[..split], width);
        let live = physical_rows(&lines[split..], width);
        let committed_len = committed.len();
        let live_tail_len = live.len();
        committed.extend(live);
        let lines = committed;
        let old_len = self.lines.len();
        let new_len = lines.len();
        if self.scroll > 0 {
            self.scroll = if new_len >= old_len {
                self.scroll.saturating_add(new_len - old_len)
            } else {
                self.scroll.saturating_sub(old_len - new_len)
            };
        }
        self.committed_len = committed_len;
        self.live_tail_len = live_tail_len;
        self.lines = lines;
        self.width = width;
    }

    pub(crate) fn scroll_up(&mut self, rows: u16) {
        self.scroll = self.scroll.saturating_add(rows as usize);
    }

    pub(crate) fn scroll_down(&mut self, rows: u16) {
        let overflow = self
            .lines
            .len()
            .saturating_sub(self.visible_height.get().max(1));
        self.scroll = self.scroll.min(overflow).saturating_sub(rows as usize);
    }

    pub(crate) fn scroll_to_top(&mut self) {
        self.scroll = usize::MAX;
    }

    pub(crate) fn scroll_to_bottom(&mut self) {
        self.scroll = 0;
    }

    pub(crate) fn render_lines(&self, height: u16) -> (Vec<Line<'static>>, usize) {
        let visible_height = height.max(1) as usize;
        self.visible_height.set(visible_height);
        let overflow = self.lines.len().saturating_sub(visible_height);
        let scroll = self.scroll.min(overflow);
        let start = overflow.saturating_sub(scroll);
        let end = start.saturating_add(visible_height).min(self.lines.len());
        (self.lines[start..end].to_vec(), scroll)
    }

    #[cfg(test)]
    pub(crate) fn scroll_for_test(&self) -> usize {
        self.scroll
    }
}

fn physical_rows(lines: &[Line<'static>], width: usize) -> Vec<Line<'static>> {
    // InlineTerminal leaves the final terminal column clear. Paginate the
    // printable rows, rather than silently dropping its last character or
    // counting one long diagnostic as a single scrollable row.
    let printable = width.saturating_sub(1).max(1);
    lines
        .iter()
        .flat_map(|line| {
            if line.width() <= printable {
                vec![line.clone()]
            } else {
                crate::wrapping::adaptive_wrap_line(
                    line,
                    crate::wrapping::RtOptions::new(printable),
                )
                .iter()
                .map(crate::wrapping::line_to_static)
                .collect()
            }
        })
        .collect()
}

pub(crate) fn render_transcript_pager(
    pager: &TranscriptPagerState,
    area: Rect,
    buf: &mut Buffer,
    theme: &ThemePalette,
) {
    ratatui::widgets::Widget::render(Clear, area, buf);
    if area.width == 0 || area.height == 0 {
        return;
    }

    let header = format!("/ {} ", pager.title().to_ascii_uppercase());
    let mut header_text = header.clone();
    while UnicodeWidthStr::width(header_text.as_str()) < usize::from(area.width) {
        header_text.push_str("/ ");
    }
    while UnicodeWidthStr::width(header_text.as_str()) > usize::from(area.width) {
        header_text.pop();
    }
    ratatui::widgets::Widget::render(
        Paragraph::new(Line::from(Span::styled(
            header_text,
            Style::default().fg(theme.text_muted),
        ))),
        Rect::new(area.x, area.y, area.width, 1),
        buf,
    );

    let chrome_height = 4u16.min(area.height);
    let content_height = area.height.saturating_sub(chrome_height);
    let content_area = Rect::new(area.x, area.y.saturating_add(1), area.width, content_height);
    let (lines, scroll) = pager.render_lines(content_height);
    let content = if lines.is_empty() {
        vec![Line::from(Span::styled(
            "No transcript yet",
            Style::default().fg(theme.text_muted),
        ))]
    } else {
        lines
    };
    crate::history_cell::render_styled_lines(
        content,
        content_area,
        buf,
        Style::default().fg(theme.text),
        0,
        Some(Wrap { trim: false }),
    );

    if area.height < 4 {
        return;
    }
    let total = pager.lines.len();
    let overflow = total.saturating_sub(content_height.max(1) as usize);
    let percent = if overflow == 0 {
        100usize
    } else {
        ((overflow - scroll) * 100) / overflow
    };
    let separator_y = area.bottom().saturating_sub(3);
    let percentage = format!(" {percent}% ");
    let percentage_width = percentage.chars().count().min(usize::from(area.width));
    let rule_width = usize::from(area.width).saturating_sub(percentage_width);
    let separator = format!("{}{}", "─".repeat(rule_width), percentage);
    let navigation = if area.width < 30 {
        " ↑/↓ · PgUp/PgDn"
    } else if area.width < 60 {
        " ↑/↓ scroll · PgUp/PgDn page"
    } else {
        " ↑/↓ to scroll   PgUp/PgDn to page   Home/End to jump"
    };
    let close = if area.width < 30 {
        " Esc close"
    } else {
        " Esc/Ctrl+T to close"
    };
    for (offset, text) in [
        (0u16, separator),
        (1, navigation.to_string()),
        (
            2,
            pager
                .footer_override
                .clone()
                .unwrap_or_else(|| close.to_string()),
        ),
    ] {
        ratatui::widgets::Widget::render(
            Paragraph::new(Line::from(Span::styled(
                text,
                Style::default().fg(theme.text_muted),
            ))),
            Rect::new(area.x, separator_y.saturating_add(offset), area.width, 1),
            buf,
        );
    }
}

/// Codex `/diff` parity (slash_dispatch.rs:317-333): build colored pager lines
/// from raw `git diff HEAD` text plus an untracked-file section.
#[cfg(test)]
pub(crate) fn build_diff_lines(
    diff: &str,
    untracked: &str,
    theme: &ThemePalette,
) -> Vec<Line<'static>> {
    let mut lines = Vec::new();
    for raw in diff.lines() {
        let line = if raw.starts_with("diff --git") || raw.starts_with("index ") {
            Line::from(Span::styled(
                raw.to_string(),
                Style::default()
                    .fg(theme.text_title)
                    .add_modifier(Modifier::BOLD),
            ))
        } else if raw.starts_with("@@") {
            Line::from(Span::styled(
                raw.to_string(),
                Style::default().fg(theme.accent),
            ))
        } else if raw.starts_with("---") || raw.starts_with("+++") {
            Line::from(Span::styled(
                raw.to_string(),
                Style::default()
                    .fg(theme.text_title)
                    .add_modifier(Modifier::BOLD),
            ))
        } else if raw.starts_with('+') {
            Line::from(Span::styled(
                raw.to_string(),
                Style::default().fg(theme.success),
            ))
        } else if raw.starts_with('-') {
            Line::from(Span::styled(
                raw.to_string(),
                Style::default().fg(theme.error),
            ))
        } else {
            Line::from(Span::styled(
                raw.to_string(),
                Style::default().fg(theme.text),
            ))
        };
        lines.push(line);
    }
    let untracked = untracked.trim();
    if !untracked.is_empty() {
        if !lines.is_empty() {
            lines.push(Line::from(String::new()));
        }
        lines.push(Line::from(Span::styled(
            "Untracked files:".to_string(),
            Style::default()
                .fg(theme.text_title)
                .add_modifier(Modifier::BOLD),
        )));
        for path in untracked.lines() {
            lines.push(Line::from(Span::styled(
                format!("  ? {path}"),
                Style::default().fg(theme.text_muted),
            )));
        }
    }
    lines
}

pub(crate) fn render_transcript_pager_with_title(
    pager: &TranscriptPagerState,
    title: &str,
    area: Rect,
    buf: &mut Buffer,
    theme: &ThemePalette,
) {
    ratatui::widgets::Widget::render(Clear, area, buf);
    let inner_height = area.height.saturating_sub(2).max(1);
    let (lines, scroll) = pager.render_lines(inner_height);
    let total = pager.lines.len();
    // Codex pager_overlay.rs:231-247 parity: relative position as a
    // percentage of the scrollable range.
    let overflow = total.saturating_sub(inner_height.max(1) as usize);
    let percent = if overflow == 0 {
        100usize
    } else {
        ((overflow - scroll) * 100) / overflow
    };
    let footer = format!(" {total} lines · {percent}% · Esc close · ↑/↓ PgUp/PgDn Home/End ",);
    let block = Block::default()
        .title(Span::styled(
            format!(" {title} "),
            Style::default().fg(theme.text).add_modifier(Modifier::BOLD),
        ))
        .title_bottom(Span::styled(footer, Style::default().fg(theme.text_muted)))
        .borders(Borders::ALL)
        .border_type(BorderType::Rounded)
        .border_style(Style::default().fg(theme.border));
    let text = if lines.is_empty() {
        Text::from(Line::from(Span::styled(
            "No transcript yet",
            Style::default().fg(theme.text_muted),
        )))
    } else {
        Text::from(lines)
    };
    let widget = Paragraph::new(text)
        .block(block)
        .style(Style::default().fg(theme.text))
        .wrap(Wrap { trim: false });
    ratatui::widgets::Widget::render(widget, area, buf);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rendered_buffer(buf: &Buffer) -> Vec<String> {
        (buf.area.y..buf.area.bottom())
            .map(|y| {
                (buf.area.x..buf.area.right())
                    .map(|x| buf.get(x, y).symbol())
                    .collect::<String>()
            })
            .collect()
    }

    #[test]
    fn down_and_page_down_work_after_home() {
        let mut pager = TranscriptPagerState::new(
            (0..50).map(|n| Line::from(format!("line {n}"))).collect(),
            40,
        );
        pager.scroll_to_top();
        assert_eq!(pager.render_lines(5).0[0].to_string(), "line 0");
        pager.scroll_down(1);
        assert_eq!(pager.render_lines(5).0[0].to_string(), "line 1");
        pager.scroll_down(12);
        assert_eq!(pager.render_lines(5).0[0].to_string(), "line 13");
        pager.scroll_to_bottom();
        assert_eq!(pager.render_lines(5).0[0].to_string(), "line 45");
    }

    #[test]
    fn long_diagnostics_fit_the_columns_actually_printed_by_inline_terminal() {
        let source = format!(
            "{}最后一段：diagnostics-complete",
            "0123456789abcdef".repeat(24)
        );
        for width in [40, 80, 120] {
            let original = vec![Line::from(source.clone())];
            let pager = TranscriptPagerState::new(original.clone(), width);
            let (rows, _) = pager.render_lines(u16::MAX);
            assert!(
                rows.iter().all(|row| row.width() < width),
                "{width}: {rows:?}"
            );
            let actual: String = rows
                .iter()
                .map(ToString::to_string)
                .collect::<String>()
                .chars()
                .filter(|c| !c.is_whitespace())
                .collect();
            assert_eq!(
                actual, source,
                "No character may enter the reserved final column"
            );
            assert!(!pager.transcript_is_stale(&original, width));
        }
    }

    #[test]
    fn wrapped_live_rows_preserve_the_reader_anchor_and_tail_counts() {
        let original = (0..20)
            .map(|n| Line::from(format!("line {n}")))
            .collect::<Vec<_>>();
        let mut pager = TranscriptPagerState::new(original.clone(), 40);
        pager.scroll_up(3);
        let mut grown = original;
        grown.push(Line::from("live diagnostic ".repeat(12)));
        pager.update_lines(grown, 40, 1);
        let (visible, _) = pager.render_lines(5);
        assert_eq!(visible[0].to_string(), "line 12");
        assert_eq!(pager.committed_len, 20);
        assert!(pager.live_tail_len > 1);
        assert_eq!(pager.committed_len + pager.live_tail_len, pager.lines.len());
    }

    #[test]
    fn build_diff_lines_colors_and_untracked() {
        let theme = ThemePalette::default();
        let diff = "diff --git a/a.rs b/a.rs\nindex 123..456 100644\n--- a/a.rs\n+++ b/a.rs\n@@ -1,2 +1,2 @@\n-old\n+new\n";
        let lines = build_diff_lines(diff, "notes.txt\n", &theme);
        let texts: Vec<String> = lines
            .iter()
            .map(|line| line.spans.iter().map(|s| s.content.to_string()).collect())
            .collect();
        assert!(texts[0].starts_with("diff --git"));
        assert!(texts.iter().any(|t| t == "-old"));
        assert!(texts.iter().any(|t| t == "+new"));
        assert!(texts.iter().any(|t| t == "Untracked files:"));
        assert!(texts.iter().any(|t| t == "  ? notes.txt"));
        let plus = lines
            .iter()
            .find(|line| line.spans[0].content == "+new")
            .expect("plus line");
        assert_eq!(plus.spans[0].style.fg, Some(theme.success));
        let minus = lines
            .iter()
            .find(|line| line.spans[0].content == "-old")
            .expect("minus line");
        assert_eq!(minus.spans[0].style.fg, Some(theme.error));
        let hunk = lines
            .iter()
            .find(|line| line.spans[0].content.starts_with("@@"))
            .expect("hunk header");
        assert_eq!(hunk.spans[0].style.fg, Some(theme.accent));
    }

    #[test]
    fn static_pager_keeps_title_and_lines() {
        let theme = ThemePalette::default();
        let lines = build_diff_lines("+x\n", "", &theme);
        let mut pager = TranscriptPagerState::new(lines, 80);
        pager.static_content = true;
        pager.title_override = Some(" D I F F ".to_string());
        assert_eq!(pager.title(), " D I F F ");
        assert!(pager.static_content);
        let default_pager = TranscriptPagerState::new(Vec::new(), 80);
        assert_eq!(default_pager.title(), "Transcript");
    }

    #[test]
    fn transcript_pager_matches_codex_borderless_overlay_structure() {
        let theme = ThemePalette::default();
        let pager = TranscriptPagerState::new(
            vec![
                Line::from("Tool core@direct_qa"),
                Line::from("Available dependency_probe"),
            ],
            60,
        );
        let area = Rect::new(0, 0, 60, 10);
        let mut buf = Buffer::empty(area);

        render_transcript_pager(&pager, area, &mut buf, &theme);

        let rendered = rendered_buffer(&buf);
        assert!(rendered[0].starts_with("/ TRANSCRIPT / "), "{rendered:?}");
        assert!(rendered
            .iter()
            .any(|line| line.contains("Available dependency_probe")));
        assert!(rendered[7].contains("100%"), "{rendered:?}");
        assert!(rendered[8].contains("PgUp/PgDn to page"), "{rendered:?}");
        assert!(rendered[9].contains("Esc/Ctrl+T to close"), "{rendered:?}");
        assert!(!rendered.iter().any(|line| {
            line.contains('┌') || line.contains('┐') || line.contains('╭') || line.contains('╮')
        }));
        assert!(!rendered.iter().any(|line| {
            line.contains('└') || line.contains('┘') || line.contains('╰') || line.contains('╯')
        }));
    }

    #[test]
    fn titled_transcript_pager_uses_rounded_corners() {
        let theme = ThemePalette::default();
        let pager = TranscriptPagerState::new(vec![Line::from("details")], 48);
        let area = Rect::new(0, 0, 48, 6);
        let mut buf = Buffer::empty(area);

        render_transcript_pager_with_title(&pager, "Details", area, &mut buf, &theme);

        let rendered = rendered_buffer(&buf);
        assert!(rendered[0].starts_with('╭'), "{rendered:?}");
        assert!(rendered[0].ends_with('╮'), "{rendered:?}");
        assert!(rendered[5].starts_with('╰'), "{rendered:?}");
        assert!(rendered[5].ends_with('╯'), "{rendered:?}");
    }

    #[test]
    fn pager_scrolls_with_bottom_origin() {
        let lines = (0..20)
            .map(|index| Line::from(format!("line {index}")))
            .collect::<Vec<_>>();
        let mut pager = TranscriptPagerState::new(lines, 80);

        let (bottom, _) = pager.render_lines(5);
        assert_eq!(
            bottom
                .iter()
                .map(|line| line.spans[0].content.as_ref())
                .collect::<Vec<_>>(),
            vec!["line 15", "line 16", "line 17", "line 18", "line 19"]
        );

        pager.scroll_up(3);
        let (scrolled, _) = pager.render_lines(5);
        assert_eq!(
            scrolled
                .iter()
                .map(|line| line.spans[0].content.as_ref())
                .collect::<Vec<_>>(),
            vec!["line 12", "line 13", "line 14", "line 15", "line 16"]
        );

        pager.scroll_to_top();
        let (top, _) = pager.render_lines(5);
        assert_eq!(
            top.iter()
                .map(|line| line.spans[0].content.as_ref())
                .collect::<Vec<_>>(),
            vec!["line 0", "line 1", "line 2", "line 3", "line 4"]
        );
    }

    #[test]
    fn live_tail_growth_preserves_reader_anchor_but_follows_when_pinned() {
        let original = (0..20)
            .map(|index| Line::from(format!("line {index}")))
            .collect::<Vec<_>>();
        let mut scrolled = TranscriptPagerState::new(original.clone(), 80);
        scrolled.scroll_up(3);
        let mut grown = original.clone();
        grown.push(Line::from("line 20"));
        grown.push(Line::from("line 21"));
        scrolled.update_lines(grown.clone(), 80, 2);
        let (visible, _) = scrolled.render_lines(5);
        assert_eq!(
            visible
                .iter()
                .map(|line| line.spans[0].content.as_ref())
                .collect::<Vec<_>>(),
            vec!["line 12", "line 13", "line 14", "line 15", "line 16"]
        );

        let mut pinned = TranscriptPagerState::new(original, 80);
        pinned.update_lines(grown, 80, 2);
        assert_eq!(pinned.scroll_for_test(), 0);
        let (visible, _) = pinned.render_lines(5);
        assert_eq!(visible.last().unwrap().spans[0].content, "line 21");
    }
}
