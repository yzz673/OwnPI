use pulldown_cmark::Alignment;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};

use crate::terminal_hyperlinks::{word_wrap_hyperlink_line, HyperlinkLine, TerminalHyperlink};
use crate::theme::ThemePalette;
use crate::wrapping::RtOptions;

/// Cells keep their inline spans until layout; formatting must not leak into
/// a separate paragraph or disappear when column widths are calculated.
#[derive(Debug, Default)]
pub(super) struct MarkdownTable {
    alignments: Vec<Alignment>,
    rows: Vec<Vec<HyperlinkLine>>,
    cells: Vec<HyperlinkLine>,
    current: HyperlinkLine,
}

impl MarkdownTable {
    pub(super) fn new(alignments: Vec<Alignment>) -> Self {
        Self {
            alignments,
            ..Self::default()
        }
    }

    pub(super) fn push(&mut self, text: String, style: Style, destination: Option<&str>) {
        self.current
            .push_span(Span::styled(text.replace('\t', "    "), style), destination);
    }

    pub(super) fn end_cell(&mut self) {
        self.cells
            .push(std::mem::take(&mut self.current).with_web_urls());
    }

    pub(super) fn end_row(&mut self) {
        if !self.cells.is_empty() {
            self.rows.push(std::mem::take(&mut self.cells));
        }
    }

    pub(super) fn render(mut self, width: usize, theme: &ThemePalette) -> Vec<HyperlinkLine> {
        self.end_row();
        let columns = self.alignments.len();
        if columns == 0 || self.rows.is_empty() {
            return Vec::new();
        }
        let width = width.max(1);
        let natural = (0..columns)
            .map(|col| {
                self.rows
                    .iter()
                    .filter_map(|row| row.get(col))
                    .flat_map(cell_source_lines)
                    .map(|line| line.line.width())
                    .max()
                    .unwrap_or(1)
                    .max(1)
            })
            .collect::<Vec<_>>();
        let mut sizes = (0..columns)
            .map(|col| {
                self.rows[0]
                    .get(col)
                    .map(|line| line.line.width())
                    .unwrap_or(0)
                    .clamp(8, 14)
                    .min(natural[col])
            })
            .collect::<Vec<_>>();
        let overhead = columns.saturating_mul(3).saturating_add(1);
        if overhead.saturating_add(sizes.iter().sum::<usize>()) > width {
            return self.stacked(width, theme);
        }
        let mut spare = width - overhead - sizes.iter().sum::<usize>();
        while spare > 0 {
            let mut grew = false;
            for col in 0..columns {
                if spare > 0 && sizes[col] < natural[col] {
                    sizes[col] += 1;
                    spare -= 1;
                    grew = true;
                }
            }
            if !grew {
                break;
            }
        }
        let border = Style::default().fg(theme.border);
        let rule = |left: char, middle: char, right: char| {
            let pieces = sizes
                .iter()
                .map(|size| "─".repeat(size + 2))
                .collect::<Vec<_>>();
            Line::from(Span::styled(
                format!("{left}{}{right}", pieces.join(&middle.to_string())),
                border,
            ))
        };
        let mut lines = vec![HyperlinkLine::new(rule('┌', '┬', '┐'))];
        for (row_index, row) in self.rows.iter().enumerate() {
            let cells = (0..columns)
                .map(|col| {
                    row.get(col)
                        .map(|cell| wrap_cell(cell, sizes[col]))
                        .unwrap_or_else(|| vec![HyperlinkLine::default()])
                })
                .collect::<Vec<_>>();
            let height = cells.iter().map(Vec::len).max().unwrap_or(1);
            for index in 0..height {
                let mut output = HyperlinkLine::new(Line::from(Span::styled(
                    crate::style::GLYPH_MD_TABLE_PREFIX,
                    border,
                )));
                for col in 0..columns {
                    let mut cell = cells[col].get(index).cloned().unwrap_or_default();
                    let padding = sizes[col].saturating_sub(cell.line.width());
                    let before = match self.alignments[col] {
                        Alignment::Right => padding,
                        Alignment::Center => padding / 2,
                        _ => 0,
                    };
                    output.push_span(Span::raw(" ".repeat(before)), None);
                    if row_index == 0 {
                        for span in &mut cell.line.spans {
                            span.style = span.style.add_modifier(Modifier::BOLD);
                        }
                    }
                    output.append(cell);
                    output.push_span(Span::raw(" ".repeat(padding - before)), None);
                    output.push_span(
                        Span::styled(if col + 1 == columns { " │" } else { " │ " }, border),
                        None,
                    );
                }
                lines.push(output);
            }
            if row_index + 1 < self.rows.len() {
                lines.push(HyperlinkLine::new(rule('├', '┼', '┤')));
            }
        }
        lines.push(HyperlinkLine::new(rule('└', '┴', '┘')));
        lines
    }

    /// When columns cannot remain legible, keep the header/value association
    /// and every cell instead of truncating or overflowing the terminal.
    fn stacked(&self, width: usize, theme: &ThemePalette) -> Vec<HyperlinkLine> {
        let mut lines = Vec::new();
        let headers = &self.rows[0];
        if self.rows.len() == 1 {
            for header in headers {
                lines.extend(wrap_cell(header, width));
            }
            return lines;
        }
        for (row_index, row) in self.rows.iter().skip(1).enumerate() {
            if row_index > 0 {
                lines.push(HyperlinkLine::new(Line::from(Span::styled(
                    "─".repeat(width.min(40)),
                    Style::default().fg(theme.border),
                ))));
            }
            for col in 0..self.alignments.len() {
                let mut label = headers.get(col).cloned().unwrap_or_default();
                if label
                    .line
                    .spans
                    .iter()
                    .all(|span| span.content.trim().is_empty())
                {
                    label = HyperlinkLine::new(Line::from(format!("Column {}", col + 1)));
                }
                for span in &mut label.line.spans {
                    span.style = span.style.add_modifier(Modifier::BOLD);
                }
                label.push_span(
                    Span::styled(": ", Style::default().fg(theme.text_muted)),
                    None,
                );
                let separate = label.line.width() > width / 2;
                let indent = if separate {
                    lines.extend(wrap_cell(&label, width));
                    HyperlinkLine::new(Line::from(" ".repeat(if width >= 4 { 2 } else { 0 })))
                } else {
                    label
                };
                let value = row.get(col).cloned().unwrap_or_default();
                for (index, cell) in cell_source_lines(&value).into_iter().enumerate() {
                    let mut combined = if index == 0 {
                        indent.clone()
                    } else {
                        HyperlinkLine::new(Line::from(" ".repeat(indent.line.width())))
                    };
                    combined.append(cell);
                    lines.extend(word_wrap_hyperlink_line(
                        &combined,
                        RtOptions::new(width)
                            .subsequent_indent(Line::from(" ".repeat(indent.line.width()))),
                    ));
                }
            }
        }
        lines
    }
}

fn cell_source_lines(cell: &HyperlinkLine) -> Vec<HyperlinkLine> {
    let mut lines = vec![Line::default()];
    for span in &cell.line.spans {
        for (index, part) in span.content.split('\n').enumerate() {
            if index > 0 {
                lines.push(Line::default());
            }
            if !part.is_empty() {
                lines
                    .last_mut()
                    .unwrap()
                    .spans
                    .push(Span::styled(part.to_string(), span.style));
            }
        }
    }
    let mut offset = 0;
    lines
        .into_iter()
        .map(|line| {
            let end = offset + line.width();
            let hyperlinks = cell
                .hyperlinks
                .iter()
                .filter_map(|link| {
                    let start = link.columns.start.max(offset);
                    let stop = link.columns.end.min(end);
                    (start < stop).then(|| TerminalHyperlink {
                        columns: start - offset..stop - offset,
                        destination: link.destination.clone(),
                    })
                })
                .collect();
            offset = end;
            HyperlinkLine { line, hyperlinks }
        })
        .collect()
}

fn wrap_cell(cell: &HyperlinkLine, width: usize) -> Vec<HyperlinkLine> {
    let lines = cell_source_lines(cell)
        .iter()
        .flat_map(|line| word_wrap_hyperlink_line(line, RtOptions::new(width.max(1))))
        .collect::<Vec<_>>();
    if lines.is_empty() {
        vec![HyperlinkLine::default()]
    } else {
        lines
    }
}
