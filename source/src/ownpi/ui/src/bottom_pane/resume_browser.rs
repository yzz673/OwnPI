//! Local, read-only filtering of the Runtime-backed session picker.
use super::menu_surface::{
    menu_footer_lines, menu_surface_content_width, menu_surface_padding_height, render_menu_surface,
};
use super::selection_popup::{
    render_rows_single_line, truncate_line, DisplayRow, SelectionViewRow,
};
use super::ScrollState;
use crate::theme::ThemePalette;
use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use ratatui::{
    buffer::Buffer,
    layout::Rect,
    style::{Modifier, Style},
    text::{Line, Span},
    widgets::Widget,
};

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct ResumeBrowser {
    pub(crate) all: Vec<SelectionViewRow>,
    pub(crate) query: String,
    pub(crate) all_projects: bool,
    pub(crate) status: u8,
    pub(crate) created: bool,
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn browser() -> ResumeBrowser {
        ResumeBrowser { all: vec![
            SelectionViewRow { id: "one".into(), name: "脑电 speech 2026".into(),
                selected_description: "Updated 2026-11-12 14:00:00 +0800\n/项目/脑电\nrun-one\nContinue conversation".into(),
                resume_entry: json!({"same_project":true,"ready":true,"updated":30,"created":10,
                    "updated_label":"10m ago","created_label":"2d ago","status":"idle"}), ..Default::default() },
            SelectionViewRow { id: "two".into(), name: "EEG history".into(),
                resume_entry: json!({"same_project":false,"ready":false,"updated":20,"created":20,
                    "updated_label":"1h ago","status":"failed"}), ..Default::default() },
        ], ..Default::default() }
    }

    #[test]
    fn resume_search_scope_sort_and_status_are_read_only() {
        let mut b = browser();
        assert_eq!(b.rows().len(), 1);
        b.key(KeyEvent::new(KeyCode::Tab, KeyModifiers::NONE));
        assert_eq!(b.rows().len(), 2);
        b.key(KeyEvent::new(KeyCode::Char('s'), KeyModifiers::CONTROL));
        assert_eq!(b.rows()[0].id, "two");
        for c in "脑电 2026".chars() {
            b.key(KeyEvent::new(KeyCode::Char(c), KeyModifiers::NONE));
        }
        assert_eq!(b.rows()[0].id, "one");
        b.key(KeyEvent::new(KeyCode::Char('u'), KeyModifiers::CONTROL));
        b.key(KeyEvent::new(KeyCode::Char('f'), KeyModifiers::CONTROL));
        assert_eq!(b.rows()[0].id, "one");
        b.key(KeyEvent::new(KeyCode::Char('f'), KeyModifiers::CONTROL));
        assert_eq!(b.rows()[0].id, "two");
        assert_eq!(b.all.len(), 2);
    }

    #[test]
    fn resume_uses_shared_surface_padding_and_selection_styles() {
        let b = browser();
        let theme = ThemePalette::default();
        let area = Rect::new(3, 2, 100, b.desired_height(100, 1));
        let mut buf = Buffer::empty(area);
        b.render(&b.rows(), Some(0), area, &mut buf, &theme);
        let mut reference = Buffer::empty(area);
        let inner = render_menu_surface(area, &mut reference, &theme, true);
        // This must remain the same surface as /commands, not raw terminal lines.
        // Ratatui resets the continuation cell of a wide glyph; compare visible
        // cells instead of treating that bookkeeping cell as a painted space.
        for y in area.top()..area.bottom() {
            let mut x = area.left();
            while x < area.right() {
                let cell = buf.get(x, y);
                assert_eq!(cell.bg, theme.bottom_pane().input_bg, "at {x},{y}");
                x += unicode_width::UnicodeWidthStr::width(cell.symbol()).max(1) as u16;
            }
        }
        assert_eq!(buf.get(area.x, area.y), reference.get(area.x, area.y));
        assert_eq!(buf.get(inner.x, inner.y).symbol(), "R");
        assert_eq!(
            buf.get(inner.x, inner.y).fg,
            theme.bottom_pane().input_accent
        );
        assert_eq!(buf.get(inner.x, inner.y + 2).symbol(), "/");
        let selected = buf.get(inner.x, inner.y + 4);
        assert_eq!(selected.symbol(), "▸");
        assert_eq!(selected.fg, theme.accent);
        assert!(selected.modifier.contains(Modifier::BOLD));
        assert!(!selected.modifier.contains(Modifier::REVERSED));
        assert_eq!(buf.get(inner.x - 1, inner.y + 4).symbol(), " ");
    }

    #[test]
    fn resume_footer_wraps_and_selection_remains_visible() {
        let mut b = browser();
        b.all_projects = true;
        for width in [40, 80, 140] {
            let area = Rect::new(0, 0, width, b.desired_height(width, 2));
            let mut buf = Buffer::empty(area);
            b.render(&b.rows(), Some(1), area, &mut buf, &ThemePalette::default());
            let text: String = buf.content.iter().map(|c| c.symbol()).collect();
            assert!(text.contains("EEG history"));
            for key in ["Ctrl+O", "Ctrl+F", "Ctrl+S", "Ctrl+U", "Tab", "Esc"] {
                assert!(text.contains(key), "missing {key} at width {width}");
            }
        }
        // Extremely small and non-zero-origin viewports must not write outside their buffer.
        for width in 0..8 {
            for height in 0..8 {
                let area = Rect::new(3, 2, width, height);
                b.render(
                    &b.rows(),
                    Some(1),
                    area,
                    &mut Buffer::empty(area),
                    &ThemePalette::default(),
                );
            }
        }
    }

    #[test]
    fn resume_render_keeps_time_title_without_inline_details() {
        let mut b = browser();
        for width in [20, 40, 100, 140] {
            for height in [1, 5, 16, 24] {
                let area = Rect::new(0, 0, width, height);
                let mut buf = Buffer::empty(area);
                b.render(&b.rows(), Some(0), area, &mut buf, &ThemePalette::default());
                let text: String = buf.content.iter().map(|c| c.symbol()).collect();
                if width >= 100 && height >= 16 {
                    assert!(
                        text.contains("10m ago")
                            && text.contains("speech 2026")
                            && text.contains('脑')
                            && text.contains('电')
                    );
                    for hidden in [
                        "of 1 sessions",
                        "2026-11-12 14:00:00 +0800",
                        "run-one",
                        "Continue conversation",
                    ] {
                        assert!(!text.contains(hidden), "inline details leaked: {hidden}");
                    }
                    assert!(text.contains("Ctrl+O details"));
                }
            }
        }
        b.query = "nothing".into();
        let area = Rect::new(0, 0, 100, 16);
        let mut buf = Buffer::empty(area);
        b.render(&b.rows(), None, area, &mut buf, &ThemePalette::default());
        let text: String = buf.content.iter().map(|c| c.symbol()).collect();
        assert!(text.contains("No matching sessions"));
    }
}

impl ResumeBrowser {
    fn footer_lines(&self, width: u16, theme: &ThemePalette) -> Vec<Line<'static>> {
        [
            "↑↓ select · Enter continue/view · Ctrl+O details · Esc back",
            "Tab scope · Ctrl+F status · Ctrl+S sort · Ctrl+U clear",
        ]
        .into_iter()
        .flat_map(|text| {
            menu_footer_lines(
                &Line::styled(text, Style::default().fg(theme.bottom_pane().input_muted)),
                None,
                width,
                theme,
            )
        })
        .collect()
    }

    pub(crate) fn desired_height(&self, width: u16, row_count: usize) -> u16 {
        let footer = self.footer_lines(menu_surface_content_width(width), &ThemePalette::default());
        row_count.clamp(1, 10) as u16 + 5 + footer.len() as u16 + menu_surface_padding_height()
    }

    pub(crate) fn key(&mut self, key: KeyEvent) -> bool {
        match (key.code, key.modifiers) {
            (KeyCode::Tab, KeyModifiers::NONE) => self.all_projects = !self.all_projects,
            (KeyCode::Char('s'), KeyModifiers::CONTROL) => self.created = !self.created,
            (KeyCode::Char('f'), KeyModifiers::CONTROL) => self.status = (self.status + 1) % 3,
            (KeyCode::Char('u'), KeyModifiers::CONTROL) => self.query.clear(),
            (KeyCode::Esc, _) if !self.query.is_empty() => self.query.clear(),
            (KeyCode::Backspace, _) => {
                self.query.pop();
            }
            (KeyCode::Char(ch), modifiers)
                if !modifiers.intersects(KeyModifiers::CONTROL | KeyModifiers::ALT) =>
            {
                self.query.push(ch)
            }
            _ => return false,
        }
        true
    }

    pub(crate) fn rows(&self) -> Vec<SelectionViewRow> {
        let query = self.query.to_lowercase();
        let words: Vec<_> = query.split_whitespace().collect();
        let mut rows: Vec<_> = self
            .all
            .iter()
            .filter(|r| {
                let meta = &r.resume_entry;
                let ready = meta["ready"] == true;
                let haystack = format!(
                    "{} {} {} {}",
                    r.name,
                    r.id,
                    r.selected_description,
                    meta["status"].as_str().unwrap_or("")
                )
                .to_lowercase();
                (self.all_projects || meta["same_project"] == true)
                    && (self.status == 0
                        || (self.status == 1 && ready)
                        || (self.status == 2 && !ready))
                    && words.iter().all(|w| haystack.contains(w))
            })
            .cloned()
            .collect();
        let field = if self.created { "created" } else { "updated" };
        rows.sort_by(|a, b| {
            b.resume_entry[field]
                .as_i64()
                .unwrap_or(0)
                .cmp(&a.resume_entry[field].as_i64().unwrap_or(0))
                .then_with(|| a.id.cmp(&b.id))
        });
        rows
    }

    pub(crate) fn render(
        &self,
        rows: &[SelectionViewRow],
        selected: Option<usize>,
        area: Rect,
        buf: &mut Buffer,
        theme: &ThemePalette,
    ) {
        if area.is_empty() {
            return;
        }
        let vertical_padding = area.height >= self.desired_height(area.width, rows.len());
        let area = render_menu_surface(area, buf, theme, vertical_padding);
        if area.is_empty() {
            return;
        }
        let palette = theme.bottom_pane();
        let normal = Style::default().fg(palette.input_text);
        let muted = Style::default().fg(palette.input_muted);
        let strong = Style::default()
            .fg(palette.input_accent)
            .add_modifier(Modifier::BOLD);
        let status = ["All", "Ready", "History"][self.status as usize];
        let lines = vec![
            Line::styled("Resume a previous session", strong),
            Line::styled(
                format!(
                    "Scope: {}   Status: {status}   Sort: {}",
                    if self.all_projects { "All" } else { "Cwd" },
                    if self.created { "Created" } else { "Updated" }
                ),
                muted,
            ),
            Line::from(vec![
                Span::styled("/ ", Style::default().fg(palette.input_accent)),
                Span::styled(
                    if self.query.is_empty() {
                        "Search sessions"
                    } else {
                        &self.query
                    }
                    .to_owned(),
                    normal,
                ),
            ]),
            Line::default(),
        ];
        let footer = self.footer_lines(area.width, theme);
        // In a short terminal, keep a session row ahead of secondary help.
        let footer_height = footer.len().min(area.height.saturating_sub(5) as usize);
        let body_height = (area.height as usize).saturating_sub(footer_height);
        let capacity = body_height.saturating_sub(5).clamp(1, 10);
        let selected = selected.unwrap_or(0).min(rows.len().saturating_sub(1));
        let start = selected.saturating_sub(capacity - 1);
        let end = (start + capacity).min(rows.len());
        let display_rows: Vec<_> = rows
            .iter()
            .enumerate()
            .map(|(index, row)| {
                let meta = &row.resume_entry;
                let time = meta[if self.created {
                    "created_label"
                } else {
                    "updated_label"
                }]
                .as_str()
                .unwrap_or("Unknown");
                let state = meta["status"].as_str().unwrap_or("unknown");
                let prefix = format!(
                    "{} {:<10} ",
                    if selected == index { "▸" } else { " " },
                    time
                );
                let description = format!(
                    "{state}{}",
                    if meta["ready"] == true {
                        ""
                    } else {
                        " · view"
                    }
                );
                DisplayRow {
                    name: format!("{prefix}{}", row.name.replace(['\n', '\r'], " ")),
                    description: if area.width >= 65 {
                        description
                    } else {
                        String::new()
                    },
                    selection_index: Some(index),
                    match_indices: None,
                    is_section_header: false,
                }
            })
            .collect();
        // Leave the rows to the same renderer used by the command browser.
        let row_y = lines.len();
        let row_height = if rows.is_empty() { 1 } else { end - start };
        for (offset, line) in lines.into_iter().take(body_height).enumerate() {
            truncate_line(line, area.width as usize).render(
                Rect::new(area.x, area.y + offset as u16, area.width, 1),
                buf,
            );
        }
        render_rows_single_line(
            Rect::new(
                area.x,
                area.y + row_y.min(body_height) as u16,
                area.width,
                row_height.min(body_height.saturating_sub(row_y)) as u16,
            ),
            buf,
            &display_rows,
            &ScrollState {
                selected_idx: (!rows.is_empty()).then_some(selected),
                scroll_top: start,
            },
            capacity,
            "No matching sessions. Tab: all projects; Ctrl+U: clear search.",
            theme,
        );
        for (index, line) in footer.into_iter().take(footer_height).enumerate() {
            line.render(
                Rect::new(
                    area.x,
                    area.y + body_height as u16 + index as u16,
                    area.width,
                    1,
                ),
                buf,
            );
        }
    }
}
