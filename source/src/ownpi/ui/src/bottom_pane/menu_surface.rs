use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::Style;
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Clear, Widget};
use unicode_width::UnicodeWidthStr;

use crate::theme::ThemePalette;

pub(crate) const MENU_SURFACE_INSET_V: u16 = 1;
pub(crate) const MENU_SURFACE_INSET_H: u16 = 2;

pub(crate) fn menu_surface_content_width(width: u16) -> u16 {
    width.saturating_sub(MENU_SURFACE_INSET_H * 2).max(1)
}

pub(crate) const fn menu_surface_padding_height() -> u16 {
    MENU_SURFACE_INSET_V * 2
}

pub(crate) fn render_menu_surface(
    area: Rect,
    buf: &mut Buffer,
    theme: &ThemePalette,
    vertical_padding: bool,
) -> Rect {
    if area.is_empty() {
        return area;
    }
    Clear.render(area, buf);
    let palette = theme.bottom_pane();
    Block::default()
        .style(Style::default().fg(palette.input_text).bg(palette.input_bg))
        .render(area, buf);
    let vertical_inset = if vertical_padding {
        MENU_SURFACE_INSET_V
    } else {
        0
    };
    Rect {
        x: area.x.saturating_add(MENU_SURFACE_INSET_H),
        y: area.y.saturating_add(vertical_inset),
        width: area.width.saturating_sub(MENU_SURFACE_INSET_H * 2),
        height: area.height.saturating_sub(vertical_inset * 2),
    }
}

pub(crate) fn scroll_position(
    scroll_top: usize,
    visible_rows: usize,
    total_rows: usize,
) -> Option<String> {
    if total_rows <= visible_rows || total_rows == 0 || visible_rows == 0 {
        return None;
    }
    let start = scroll_top.min(total_rows.saturating_sub(1));
    let end = start.saturating_add(visible_rows).min(total_rows);
    Some(format!("{}-{end}/{total_rows}", start + 1))
}

pub(crate) fn menu_footer_lines(
    hint: &Line<'_>,
    position: Option<&str>,
    width: u16,
    theme: &ThemePalette,
) -> Vec<Line<'static>> {
    let width = usize::from(width.max(1));
    let mut hint_lines =
        crate::wrapping::adaptive_wrap_line(hint, crate::wrapping::RtOptions::new(width))
            .iter()
            .map(crate::wrapping::line_to_static)
            .collect::<Vec<_>>();
    let Some(position) = position.filter(|value| !value.is_empty()) else {
        return hint_lines;
    };
    let position_width = UnicodeWidthStr::width(position);
    if hint_lines.len() == 1 {
        let hint_width = hint_lines[0].width();
        if hint_width.saturating_add(2).saturating_add(position_width) <= width {
            let gap = width.saturating_sub(hint_width + position_width);
            hint_lines[0].spans.push(Span::raw(" ".repeat(gap)));
            hint_lines[0].spans.push(Span::styled(
                position.to_string(),
                Style::default().fg(theme.bottom_pane().input_muted),
            ));
            return hint_lines;
        }
    }
    let indent = width.saturating_sub(position_width);
    hint_lines.push(Line::from(vec![
        Span::raw(" ".repeat(indent)),
        Span::styled(
            position.to_string(),
            Style::default().fg(theme.bottom_pane().input_muted),
        ),
    ]));
    hint_lines
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scroll_position_only_appears_for_overflow() {
        assert_eq!(scroll_position(0, 8, 8), None);
        assert_eq!(scroll_position(0, 8, 12).as_deref(), Some("1-8/12"));
        assert_eq!(scroll_position(4, 8, 12).as_deref(), Some("5-12/12"));
    }

    #[test]
    fn narrow_footer_moves_position_to_a_separate_line() {
        let theme = ThemePalette::default();
        let lines = menu_footer_lines(
            &Line::from("Up/Down Enter Esc"),
            Some("5-12/12"),
            16,
            &theme,
        );
        assert_eq!(lines.len(), 3);
        assert_eq!(lines.last().map(Line::width), Some(16));
    }
}
