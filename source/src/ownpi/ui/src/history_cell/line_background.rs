use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::Style;
use ratatui::text::{Line, Text};
use ratatui::widgets::{Paragraph, Widget, Wrap};

/// Paint explicit row backgrounds after Paragraph has drawn the text. Paragraph
/// styles graphemes only, leaving empty rows and row tails at the surface color.
pub(crate) fn render_styled_lines(
    lines: Vec<Line<'static>>,
    area: Rect,
    buf: &mut Buffer,
    style: Style,
    scroll: u16,
    wrap: Option<Wrap>,
) {
    if area.is_empty() {
        return;
    }
    let mut backgrounds = Vec::new();
    let mut row = 0usize;
    let start = usize::from(scroll);
    let end = start + usize::from(area.height);
    for line in &lines {
        let height = match wrap {
            Some(wrap) if line.width() > usize::from(area.width) => Paragraph::new(line.clone())
                .wrap(wrap)
                .line_count(area.width),
            _ => 1,
        };
        let next = row.saturating_add(height);
        if let Some(background) = line.style.bg {
            for visible in row.max(start)..next.min(end) {
                backgrounds.push(((visible - start) as u16, background));
            }
        }
        row = next;
        if row >= end {
            break;
        }
    }
    let mut paragraph = Paragraph::new(Text::from(lines))
        .style(style)
        .scroll((scroll, 0));
    if let Some(wrap) = wrap {
        paragraph = paragraph.wrap(wrap);
    }
    paragraph.render(area, buf);
    for (row, background) in backgrounds {
        // Background only: preserve glyphs, foregrounds, severity and emphasis.
        buf.set_style(
            Rect::new(area.x, area.y + row, area.width, 1),
            Style::default().bg(background),
        );
    }
}
