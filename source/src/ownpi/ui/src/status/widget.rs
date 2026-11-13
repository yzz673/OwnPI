use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::widgets::{Paragraph, Widget};

use crate::status::StatusWidgetState;
use crate::theme::ThemePalette;

pub(crate) fn render_status_widget(
    status: &StatusWidgetState,
    area: Rect,
    buf: &mut Buffer,
    theme: &ThemePalette,
) {
    let lines = status.render_lines(area.width, theme);
    if lines.is_empty() {
        return;
    }
    Paragraph::new(lines).render(area, buf);
}
