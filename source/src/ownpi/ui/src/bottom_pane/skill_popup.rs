use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Paragraph, Widget};

use crate::bridge::protocol::SkillCatalogEntry;
use crate::theme::ThemePalette;

use super::menu_surface::{
    menu_footer_lines, menu_surface_content_width, render_menu_surface, scroll_position,
};
use super::selection_popup::{render_rows_single_line, DisplayRow, MAX_POPUP_ROWS};
use super::ScrollState;

#[derive(Clone, Debug)]
pub(crate) struct SkillPopupState {
    skills: Vec<SkillCatalogEntry>,
    filter: String,
    dismissed_token: Option<String>,
    state: ScrollState,
}

impl SkillPopupState {
    pub(crate) fn new() -> Self {
        Self {
            skills: Vec::new(),
            filter: String::new(),
            dismissed_token: None,
            state: ScrollState::default(),
        }
    }

    pub(crate) fn set_skills(&mut self, skills: Vec<SkillCatalogEntry>) {
        self.skills = skills.into_iter().filter(|skill| skill.enabled).collect();
        self.sync_selection();
    }

    pub(crate) fn on_composer_change(&mut self, composer: &str) {
        let previous = self.filter.clone();
        let token = skill_token(composer);
        if self.dismissed_token.as_deref() != token {
            self.dismissed_token = None;
        }
        self.filter = token
            .map(|token| token.trim_start_matches('$').to_ascii_lowercase())
            .unwrap_or_default();
        if self.filter != previous {
            self.state.reset();
        }
        self.sync_selection();
    }

    pub(crate) fn is_visible(&self, composer: &str) -> bool {
        let token = skill_token(composer);
        token.is_some()
            && self.dismissed_token.as_deref() != token
            && !self.filtered_items().is_empty()
    }

    pub(crate) fn dismiss(&mut self, composer: &str) {
        self.dismissed_token = skill_token(composer).map(str::to_string);
        self.state.reset();
    }

    pub(crate) fn required_height(&self, width: u16) -> u16 {
        let content_width = menu_surface_content_width(width);
        let visible = self.filtered_len().min(MAX_POPUP_ROWS);
        let position = scroll_position(self.state.scroll_top, visible, self.filtered_len());
        let footer_height = menu_footer_lines(
            &skill_popup_hint(&ThemePalette::default(), content_width),
            position.as_deref(),
            content_width,
            &ThemePalette::default(),
        )
        .len() as u16;
        (self.filtered_len().min(MAX_POPUP_ROWS) as u16)
            .saturating_add(1)
            .saturating_add(footer_height)
    }

    pub(crate) fn selected_insert_text(&self) -> Option<String> {
        self.selected_item()
            .map(|skill| format!("${} ", skill.name))
    }

    pub(crate) fn selected_item(&self) -> Option<SkillCatalogEntry> {
        let items = self.filtered_items();
        self.state
            .selected_idx
            .and_then(|idx| items.get(idx).cloned())
    }

    pub(crate) fn move_up(&mut self) {
        let len = self.filtered_items().len();
        self.state.move_up(len);
        self.sync_selection();
    }

    pub(crate) fn move_down(&mut self) {
        let len = self.filtered_items().len();
        self.state.move_down(len);
        self.sync_selection();
    }

    pub(crate) fn filtered_len(&self) -> usize {
        self.filtered_items().len()
    }

    pub(crate) fn visible_rows(&self) -> usize {
        self.filtered_len().min(MAX_POPUP_ROWS)
    }

    #[cfg(test)]
    pub(crate) fn scroll_top(&self) -> usize {
        self.state.scroll_top
    }

    pub(crate) fn render(
        &self,
        area: Rect,
        buf: &mut ratatui::buffer::Buffer,
        theme: &ThemePalette,
    ) {
        if area.is_empty() {
            return;
        }
        let inner = render_menu_surface(area, buf, theme, false);
        let visible = self.filtered_len().min(MAX_POPUP_ROWS);
        let position = scroll_position(self.state.scroll_top, visible, self.filtered_len());
        let hint_lines = menu_footer_lines(
            &skill_popup_hint(theme, inner.width),
            position.as_deref(),
            inner.width,
            theme,
        );
        let hint_height = hint_lines.len() as u16;
        let chrome_height = hint_height.saturating_add(1);
        let (list_area, hint_area) = if inner.height > chrome_height {
            (
                Rect {
                    x: inner.x,
                    y: inner.y,
                    width: inner.width,
                    height: inner.height.saturating_sub(chrome_height),
                },
                Some(Rect {
                    x: inner.x,
                    y: inner
                        .y
                        .saturating_add(inner.height.saturating_sub(hint_height)),
                    width: inner.width,
                    height: hint_height,
                }),
            )
        } else {
            (inner, None)
        };
        render_rows_single_line(
            list_area,
            buf,
            &self.display_rows(),
            &self.state,
            MAX_POPUP_ROWS,
            "no matches",
            theme,
        );
        if let Some(hint_area) = hint_area {
            Paragraph::new(hint_lines).render(hint_area, buf);
        }
    }

    fn filtered_items(&self) -> Vec<SkillCatalogEntry> {
        let mut items = self
            .skills
            .iter()
            .filter(|skill| {
                if self.filter.is_empty() {
                    return true;
                }
                let filter = self.filter.as_str();
                skill.name.to_ascii_lowercase().contains(filter)
                    || skill.display_name.to_ascii_lowercase().contains(filter)
                    || skill.description.to_ascii_lowercase().contains(filter)
            })
            .cloned()
            .collect::<Vec<_>>();
        items.sort_by(|a, b| {
            a.display_name
                .to_ascii_lowercase()
                .cmp(&b.display_name.to_ascii_lowercase())
                .then_with(|| a.name.cmp(&b.name))
        });
        items
    }

    fn display_rows(&self) -> Vec<DisplayRow> {
        self.filtered_items()
            .into_iter()
            .map(|skill| {
                let display = if skill.display_name.trim().is_empty() {
                    skill.name.clone()
                } else {
                    skill.display_name.clone()
                };
                let match_indices = (!self.filter.is_empty()).then(|| {
                    display
                        .to_ascii_lowercase()
                        .match_indices(&self.filter)
                        .flat_map(|(start, value)| start..start + value.len())
                        .collect::<Vec<_>>()
                });
                DisplayRow {
                    name: display,
                    description: skill.description,
                    match_indices,
                    selection_index: None,
                    is_section_header: false,
                }
            })
            .collect()
    }

    fn sync_selection(&mut self) {
        let len = self.filtered_items().len();
        self.state.clamp_selection(len);
        self.state.ensure_visible(len, self.visible_rows());
    }
}

#[cfg(test)]
pub(crate) fn skill_popup_hint_height(width: u16) -> u16 {
    menu_footer_lines(
        &skill_popup_hint(&ThemePalette::default(), width),
        None,
        width,
        &ThemePalette::default(),
    )
    .len() as u16
}

fn skill_popup_hint(theme: &ThemePalette, width: u16) -> Line<'static> {
    let palette = theme.bottom_pane();
    if width < 24 {
        return Line::from(vec![
            Span::styled(
                "Enter",
                Style::default()
                    .fg(palette.input_accent)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::styled(" insert  ", Style::default().fg(theme.text_muted)),
            Span::styled(
                "Esc",
                Style::default()
                    .fg(palette.input_accent)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::styled(" close", Style::default().fg(theme.text_muted)),
        ]);
    }
    let line = Line::from(vec![
        Span::styled("Press ", Style::default().fg(theme.text_muted)),
        Span::styled(
            "Enter",
            Style::default()
                .fg(palette.input_accent)
                .add_modifier(Modifier::BOLD),
        ),
        Span::styled(" to insert or ", Style::default().fg(theme.text_muted)),
        Span::styled(
            "Esc",
            Style::default()
                .fg(palette.input_accent)
                .add_modifier(Modifier::BOLD),
        ),
        Span::styled(" to close", Style::default().fg(theme.text_muted)),
    ]);
    line
}

fn skill_token(composer: &str) -> Option<&str> {
    // Codex chat_composer.rs:2371-2376 parity: a `$` token anywhere in the
    // composer triggers skill completion, not only at the start of the line.
    // Only the trailing token can be active; a completed token followed by
    // whitespace is done and no longer triggers completion.
    if composer.ends_with(char::is_whitespace) {
        return None;
    }
    let token = composer.split_whitespace().last().unwrap_or_default();
    if token.starts_with('$') {
        Some(token)
    } else {
        None
    }
}

#[cfg(test)]
mod inline_token_tests {
    use super::*;

    #[test]
    fn dollar_token_anywhere_in_composer() {
        assert_eq!(skill_token("$exp"), Some("$exp"));
        assert_eq!(skill_token("用 $exp"), Some("$exp"));
        assert_eq!(skill_token("summarize with $pdf"), Some("$pdf"));
        assert_eq!(skill_token("$"), Some("$"));
        assert_eq!(skill_token("plain text"), None);
        assert_eq!(skill_token(""), None);
    }

    #[test]
    fn completed_token_followed_by_space_is_inactive() {
        assert_eq!(skill_token("$expression-skill "), None);
        assert_eq!(skill_token("用 $pdf 总结"), None);
        assert_eq!(skill_token("用 $pdf 总结 "), None);
    }
}
