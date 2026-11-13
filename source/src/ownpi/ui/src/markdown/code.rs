use ratatui::style::Style;
use ratatui::text::{Line, Span};
use unicode_segmentation::UnicodeSegmentation;
use unicode_width::UnicodeWidthStr;

/// Code wraps at grapheme boundaries. Keep every source space rather than
/// treating indentation and spaces between tokens as prose delimiters.
pub(super) fn wrap_row(
    line: Line<'static>,
    width: usize,
    gutter_style: Style,
) -> Vec<Line<'static>> {
    let gutter = " ".repeat(if width >= 4 { 2 } else { 0 });
    let available = width.saturating_sub(gutter.len()).max(1);
    let new_row = || Line::from(Span::styled(gutter.clone(), gutter_style));
    let mut rows = Vec::new();
    let mut row = new_row();
    let mut used = 0;
    for span in line.spans {
        let mut text = String::new();
        for grapheme in span.content.graphemes(true) {
            let size = grapheme.width();
            if used + size > available && used > 0 {
                if !text.is_empty() {
                    row.spans
                        .push(Span::styled(std::mem::take(&mut text), span.style));
                }
                rows.push(row);
                row = new_row();
                used = 0;
            }
            text.push_str(grapheme);
            used += size;
        }
        if !text.is_empty() {
            row.spans.push(Span::styled(text, span.style));
        }
    }
    rows.push(row);
    rows
}
