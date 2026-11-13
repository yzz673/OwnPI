use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Paragraph, Widget};
use serde_json::{json, Value};
use std::sync::atomic::{AtomicU64, Ordering};

use crate::bridge::protocol::SkillCatalogEntry;
use crate::theme::ThemePalette;

use super::menu_surface::{
    menu_footer_lines, menu_surface_content_width, menu_surface_padding_height,
    render_menu_surface, scroll_position,
};
use super::selection_popup::{render_rows_single_line, DisplayRow};
use super::ScrollState;

const MAX_HEIGHT: u16 = 22;
const NO_REMOVABLE_SKILLS_MESSAGE: &str = "No user-installed skills are available to remove.";

#[derive(Clone, Debug, PartialEq, Eq)]
struct PendingToggle {
    request_id: String,
    action: &'static str,
    path: String,
    previous_enabled: Option<bool>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum SkillsViewMode {
    Toggle,
    Remove,
    Install,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum SkillInstallSource {
    Local,
    GitHubUrl,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct SkillsToggleViewState {
    skills: Vec<SkillCatalogEntry>,
    query: String,
    state: ScrollState,
    pending: Option<PendingToggle>,
    error: Option<String>,
    mode: SkillsViewMode,
    install_source: SkillInstallSource,
}

impl SkillsToggleViewState {
    pub(crate) fn new(skills: Vec<SkillCatalogEntry>) -> Self {
        let state = ScrollState {
            selected_idx: (!skills.is_empty()).then_some(0),
            scroll_top: 0,
        };
        Self {
            skills,
            query: String::new(),
            state,
            pending: None,
            error: None,
            mode: SkillsViewMode::Toggle,
            install_source: SkillInstallSource::Local,
        }
    }

    pub(crate) fn new_remove(skills: Vec<SkillCatalogEntry>) -> Self {
        let removable = skills
            .into_iter()
            .filter(|skill| skill.removable)
            .collect::<Vec<_>>();
        let error = removable
            .is_empty()
            .then(|| NO_REMOVABLE_SKILLS_MESSAGE.to_string());
        Self {
            mode: SkillsViewMode::Remove,
            error,
            ..Self::new(removable)
        }
    }

    pub(crate) fn new_install() -> Self {
        Self {
            mode: SkillsViewMode::Install,
            ..Self::new(Vec::new())
        }
    }

    pub(crate) fn is_visible(&self) -> bool {
        true
    }

    pub(crate) fn has_pending_action(&self) -> bool {
        self.pending.is_some()
    }

    pub(crate) fn desired_height(&self, width: u16) -> u16 {
        let content_width = menu_surface_content_width(width);
        if self.mode == SkillsViewMode::Install {
            let theme = ThemePalette::default();
            let footer = menu_footer_lines(
                &self.footer_hint(&theme, content_width),
                None,
                content_width,
                &theme,
            )
            .len() as u16;
            let value_lines = self.install_value_lines(content_width, &theme).len() as u16;
            return 6_u16
                .saturating_add(value_lines)
                .saturating_add(footer)
                .saturating_add(menu_surface_padding_height())
                .min(MAX_HEIGHT);
        }
        let total = self.filtered_indices().len();
        let visible = total.clamp(1, 12);
        let position = scroll_position(self.state.scroll_top, visible, total);
        let footer = menu_footer_lines(
            &self.footer_hint(&ThemePalette::default(), content_width),
            position.as_deref(),
            content_width,
            &ThemePalette::default(),
        )
        .len() as u16;
        (visible as u16)
            .saturating_add(7)
            .saturating_add(footer)
            .saturating_add(menu_surface_padding_height())
            .min(MAX_HEIGHT)
    }

    pub(crate) fn handle_key(&mut self, key: KeyEvent) -> SkillsToggleInput {
        if self.pending.is_some() {
            return SkillsToggleInput::None;
        }
        if self.mode == SkillsViewMode::Install {
            return self.handle_install_key(key);
        }
        match key {
            KeyEvent {
                code: KeyCode::Esc, ..
            } => SkillsToggleInput::Close,
            KeyEvent {
                code: KeyCode::Up, ..
            }
            | KeyEvent {
                code: KeyCode::Char('p'),
                modifiers: KeyModifiers::CONTROL,
                ..
            } => {
                self.move_up();
                SkillsToggleInput::Edited
            }
            KeyEvent {
                code: KeyCode::Down,
                ..
            }
            | KeyEvent {
                code: KeyCode::Char('n'),
                modifiers: KeyModifiers::CONTROL,
                ..
            } => {
                self.move_down();
                SkillsToggleInput::Edited
            }
            KeyEvent {
                code: KeyCode::PageUp,
                ..
            } => {
                self.page_up();
                SkillsToggleInput::Edited
            }
            KeyEvent {
                code: KeyCode::PageDown,
                ..
            } => {
                self.page_down();
                SkillsToggleInput::Edited
            }
            KeyEvent {
                code: KeyCode::Home,
                ..
            } => {
                self.jump_top();
                SkillsToggleInput::Edited
            }
            KeyEvent {
                code: KeyCode::End, ..
            } => {
                self.jump_bottom();
                SkillsToggleInput::Edited
            }
            KeyEvent {
                code: KeyCode::Enter | KeyCode::Char(' '),
                ..
            } => self
                .begin_selected_toggle()
                .map(SkillsToggleInput::Action)
                .unwrap_or(SkillsToggleInput::None),
            KeyEvent {
                code: KeyCode::Backspace,
                ..
            } => {
                self.query.pop();
                self.reset_filtered_selection();
                self.error = None;
                SkillsToggleInput::Edited
            }
            KeyEvent {
                code: KeyCode::Char(ch),
                modifiers,
                ..
            } if modifiers.is_empty() || modifiers == KeyModifiers::SHIFT => {
                self.query.push(ch);
                self.reset_filtered_selection();
                self.error = None;
                SkillsToggleInput::Edited
            }
            _ => SkillsToggleInput::None,
        }
    }

    pub(crate) fn insert_text(&mut self, text: &str) -> bool {
        if self.mode != SkillsViewMode::Install || self.pending.is_some() || text.is_empty() {
            return false;
        }
        self.query.push_str(text);
        self.error = None;
        true
    }

    pub(crate) fn refresh(&mut self, skills: Vec<SkillCatalogEntry>) {
        let selected_path = self.selected_skill().map(|skill| skill.path.clone());
        self.skills = match self.mode {
            SkillsViewMode::Toggle => skills,
            SkillsViewMode::Remove => skills.into_iter().filter(|skill| skill.removable).collect(),
            SkillsViewMode::Install => Vec::new(),
        };
        if self.mode == SkillsViewMode::Remove {
            if self.skills.is_empty() {
                self.error = Some(NO_REMOVABLE_SKILLS_MESSAGE.to_string());
            } else if self.error.as_deref() == Some(NO_REMOVABLE_SKILLS_MESSAGE) {
                self.error = None;
            }
        }
        let filtered = self.filtered_indices();
        self.state.selected_idx = selected_path
            .and_then(|path| {
                filtered
                    .iter()
                    .position(|index| self.skills[*index].path == path)
            })
            .or_else(|| {
                self.state
                    .selected_idx
                    .map(|selected| selected.min(filtered.len().saturating_sub(1)))
            })
            .filter(|_| !filtered.is_empty());
        self.state
            .ensure_visible(filtered.len(), filtered.len().min(12));
    }

    pub(crate) fn apply_action_result(
        &mut self,
        request_id: &str,
        action: &str,
        ok: bool,
        message: &str,
    ) -> bool {
        let Some(pending) = self.pending.take() else {
            return false;
        };
        if pending.request_id != request_id || pending.action != action {
            self.pending = Some(pending);
            return false;
        }
        if !ok {
            if let Some(previous_enabled) = pending.previous_enabled {
                if let Some(skill) = self
                    .skills
                    .iter_mut()
                    .find(|skill| skill.path == pending.path)
                {
                    skill.enabled = previous_enabled;
                }
            }
            self.error = Some(message.to_string());
        } else {
            if self.mode == SkillsViewMode::Remove {
                self.skills.retain(|skill| skill.path != pending.path);
                let len = self.filtered_indices().len();
                self.state.clamp_selection(len);
                self.state.ensure_visible(len, len.min(12));
            }
            if self.mode == SkillsViewMode::Install {
                self.query.clear();
            }
            self.error = None;
        }
        true
    }

    pub(crate) fn copy_source(&self) -> String {
        if self.mode == SkillsViewMode::Install {
            return format!(
                "Install skill\nSource: {}\nInput: {}",
                self.install_source_label(),
                self.query
            );
        }
        let mut lines = vec!["Skills".to_string()];
        for skill in &self.skills {
            lines.push(format!(
                "{} {} - {} ({})",
                if skill.enabled { "[x]" } else { "[ ]" },
                skill.display_name,
                skill.description,
                skill.path
            ));
        }
        lines.join("\n")
    }

    pub(crate) fn render(&self, area: Rect, buf: &mut Buffer, theme: &ThemePalette) {
        if area.is_empty() {
            return;
        }
        let vertical_padding = area.height >= self.desired_height(area.width);
        let inner = render_menu_surface(area, buf, theme, vertical_padding);
        if inner.is_empty() {
            return;
        }
        let palette = theme.bottom_pane();
        let narrow = inner.width < 28;
        let title = match (self.mode, narrow) {
            (SkillsViewMode::Toggle | SkillsViewMode::Remove, true) => "Skills",
            (SkillsViewMode::Install, true) => "Install",
            (SkillsViewMode::Toggle, false) => "Enable/Disable Skills",
            (SkillsViewMode::Remove, false) => "Remove Skills",
            (SkillsViewMode::Install, false) => "Install Skill",
        };
        let subtitle = match (self.mode, narrow) {
            (_, true) => "",
            (SkillsViewMode::Toggle, false) => "Changes are saved automatically.",
            (SkillsViewMode::Remove, false) => "Remove a user-installed skill.",
            (SkillsViewMode::Install, false) => "Install from a local path or GitHub URL.",
        };
        Paragraph::new(Line::from(Span::styled(
            title,
            Style::default()
                .fg(palette.input_accent)
                .add_modifier(Modifier::BOLD),
        )))
        .render(Rect::new(inner.x, inner.y, inner.width, 1), buf);
        Paragraph::new(Line::from(Span::styled(
            subtitle,
            Style::default().fg(theme.text_muted),
        )))
        .render(
            Rect::new(inner.x, inner.y.saturating_add(1), inner.width, 1),
            buf,
        );
        let content = Rect::new(
            inner.x,
            inner.y.saturating_add(3),
            inner.width,
            inner.height.saturating_sub(3),
        );

        if self.mode == SkillsViewMode::Install {
            self.render_install(content, buf, theme);
            return;
        }

        let search_prompt = if content.width < 22 {
            "Search skills"
        } else {
            "Type to search skills"
        };
        Paragraph::new(Line::from(Span::styled(
            search_prompt,
            Style::default().fg(theme.text_muted),
        )))
        .render(Rect::new(content.x, content.y, content.width, 1), buf);
        Paragraph::new(Line::from(vec![
            Span::styled("> ", Style::default().fg(theme.text_muted)),
            Span::styled(self.query.clone(), Style::default().fg(theme.text)),
        ]))
        .render(
            Rect::new(content.x, content.y.saturating_add(1), content.width, 1),
            buf,
        );

        let filtered = self.filtered_indices();
        let hint = self.footer_hint(theme, content.width);
        let base_footer = menu_footer_lines(&hint, None, content.width, theme);
        let mut footer_height = base_footer.len() as u16;
        let mut list_height = content
            .height
            .saturating_sub(3)
            .saturating_sub(footer_height)
            .min(12);
        let mut start = viewport_start(
            self.state.selected_idx.unwrap_or(0),
            filtered.len(),
            list_height,
        );
        let mut position = scroll_position(
            start,
            usize::from(list_height).min(filtered.len()),
            filtered.len(),
        );
        let mut footer_lines = menu_footer_lines(&hint, position.as_deref(), content.width, theme);
        if footer_lines.len() as u16 != footer_height {
            footer_height = footer_lines.len() as u16;
            list_height = content
                .height
                .saturating_sub(3)
                .saturating_sub(footer_height)
                .min(12);
            start = viewport_start(
                self.state.selected_idx.unwrap_or(0),
                filtered.len(),
                list_height,
            );
            position = scroll_position(
                start,
                usize::from(list_height).min(filtered.len()),
                filtered.len(),
            );
            footer_lines = menu_footer_lines(&hint, position.as_deref(), content.width, theme);
        }
        let rows = self.display_rows(&filtered);
        let state = ScrollState {
            selected_idx: self.state.selected_idx,
            scroll_top: start,
        };
        render_rows_single_line(
            Rect::new(
                content.x,
                content.y.saturating_add(3),
                content.width,
                list_height,
            ),
            buf,
            &rows,
            &state,
            usize::from(list_height),
            "no matches",
            theme,
        );
        let footer_y = content
            .y
            .saturating_add(content.height.saturating_sub(footer_height));
        for (offset, line) in footer_lines.into_iter().enumerate() {
            line.render(
                Rect::new(
                    content.x,
                    footer_y.saturating_add(offset as u16),
                    content.width,
                    1,
                ),
                buf,
            );
        }
    }

    fn display_rows(&self, filtered: &[usize]) -> Vec<DisplayRow> {
        filtered
            .iter()
            .enumerate()
            .filter_map(|(visible_index, actual_index)| {
                self.skills.get(*actual_index).map(|skill| {
                    let pending = self
                        .pending
                        .as_ref()
                        .is_some_and(|pending| pending.path == skill.path);
                    let marker = if pending {
                        "[~]"
                    } else if self.mode == SkillsViewMode::Remove {
                        "[-]"
                    } else if skill.enabled {
                        "[x]"
                    } else {
                        "[ ]"
                    };
                    DisplayRow {
                        name: format!(
                            "{} {marker} {}",
                            if self.state.selected_idx == Some(visible_index) {
                                '›'
                            } else {
                                ' '
                            },
                            skill.display_name
                        ),
                        description: skill.description.clone(),
                        match_indices: None,
                        selection_index: Some(visible_index),
                        is_section_header: false,
                    }
                })
            })
            .collect()
    }

    fn footer_hint(&self, theme: &ThemePalette, width: u16) -> Line<'static> {
        let text = if let Some(error) = self.error.as_deref() {
            return Line::from(Span::styled(
                error.to_string(),
                Style::default().fg(theme.error),
            ));
        } else if self.pending.is_some() {
            "Working..."
        } else if self.mode == SkillsViewMode::Install {
            if width >= 40 {
                "Tab source  Enter install  Esc back"
            } else {
                "Tab Enter Esc"
            }
        } else if self.mode == SkillsViewMode::Remove && width >= 48 {
            "Up/Down or Ctrl+P/Ctrl+N move  Enter remove  Esc close"
        } else if self.mode == SkillsViewMode::Remove {
            "Up/Dn Enter Esc"
        } else if width >= 48 {
            "Up/Down or Ctrl+P/Ctrl+N move  Space/Enter toggle  Esc close"
        } else {
            "Up/Dn Space Esc"
        };
        Line::from(Span::styled(text, Style::default().fg(theme.text_muted)))
    }

    fn filtered_indices(&self) -> Vec<usize> {
        let query = self.query.trim().to_ascii_lowercase();
        self.skills
            .iter()
            .enumerate()
            .filter_map(|(index, skill)| {
                (query.is_empty()
                    || skill.name.to_ascii_lowercase().contains(&query)
                    || skill.display_name.to_ascii_lowercase().contains(&query)
                    || skill.description.to_ascii_lowercase().contains(&query)
                    || skill.path.to_ascii_lowercase().contains(&query))
                .then_some(index)
            })
            .collect()
    }

    fn selected_skill(&self) -> Option<&SkillCatalogEntry> {
        let filtered = self.filtered_indices();
        filtered
            .get(self.state.selected_idx?)
            .and_then(|index| self.skills.get(*index))
    }

    fn move_up(&mut self) {
        let len = self.filtered_indices().len();
        self.state.move_up_wrap(len);
        self.state.ensure_visible(len, len.min(12));
    }

    fn move_down(&mut self) {
        let len = self.filtered_indices().len();
        self.state.move_down_wrap(len);
        self.state.ensure_visible(len, len.min(12));
    }

    fn page_up(&mut self) {
        let len = self.filtered_indices().len();
        self.state.page_up_clamped(len, 12);
    }

    fn page_down(&mut self) {
        let len = self.filtered_indices().len();
        self.state.page_down_clamped(len, 12);
    }

    fn jump_top(&mut self) {
        let len = self.filtered_indices().len();
        self.state.jump_top(len, 12);
    }

    fn jump_bottom(&mut self) {
        let len = self.filtered_indices().len();
        self.state.jump_bottom(len, 12);
    }

    fn reset_filtered_selection(&mut self) {
        let len = self.filtered_indices().len();
        self.state.reset();
        self.state.clamp_selection(len);
    }

    fn begin_selected_toggle(&mut self) -> Option<Value> {
        let skill = self.selected_skill()?.clone();
        if self.mode == SkillsViewMode::Remove {
            let request_id = next_request_id("skills-remove", &skill.path);
            self.pending = Some(PendingToggle {
                request_id: request_id.clone(),
                action: "remove",
                path: skill.path.clone(),
                previous_enabled: None,
            });
            self.error = None;
            return Some(json!({
                "type": "ui_command_action",
                "payload": {
                    "schema_version": 1,
                    "request_id": request_id,
                    "presenter": "skills",
                    "action": "remove",
                    "arguments": {"path": skill.path},
                }
            }));
        }
        let enabled = !skill.enabled;
        let request_id = next_request_id("skills-toggle", &skill.path);
        if let Some(item) = self.skills.iter_mut().find(|item| item.path == skill.path) {
            item.enabled = enabled;
        }
        self.pending = Some(PendingToggle {
            request_id: request_id.clone(),
            action: "set_enabled",
            path: skill.path.clone(),
            previous_enabled: Some(skill.enabled),
        });
        self.error = None;
        Some(json!({
            "type": "ui_command_action",
            "payload": {
                "schema_version": 1,
                "request_id": request_id,
                "presenter": "skills",
                "action": "set_enabled",
                "arguments": {"path": skill.path, "enabled": enabled},
            }
        }))
    }

    fn handle_install_key(&mut self, key: KeyEvent) -> SkillsToggleInput {
        match key {
            KeyEvent {
                code: KeyCode::Esc, ..
            } => SkillsToggleInput::Close,
            KeyEvent {
                code: KeyCode::Tab | KeyCode::BackTab,
                ..
            } => {
                self.install_source = match self.install_source {
                    SkillInstallSource::Local => SkillInstallSource::GitHubUrl,
                    SkillInstallSource::GitHubUrl => SkillInstallSource::Local,
                };
                self.error = None;
                SkillsToggleInput::Edited
            }
            KeyEvent {
                code: KeyCode::Enter,
                ..
            } => self
                .begin_install()
                .map(SkillsToggleInput::Action)
                .unwrap_or(SkillsToggleInput::Edited),
            KeyEvent {
                code: KeyCode::Backspace,
                ..
            } => {
                self.query.pop();
                self.error = None;
                SkillsToggleInput::Edited
            }
            KeyEvent {
                code: KeyCode::Char(ch),
                modifiers,
                ..
            } if modifiers.is_empty() || modifiers == KeyModifiers::SHIFT => {
                self.query.push(ch);
                self.error = None;
                SkillsToggleInput::Edited
            }
            _ => SkillsToggleInput::None,
        }
    }

    fn begin_install(&mut self) -> Option<Value> {
        let source = self.query.trim().to_string();
        if source.is_empty() {
            self.error = Some(
                match self.install_source {
                    SkillInstallSource::Local => "Enter a local skill directory path.",
                    SkillInstallSource::GitHubUrl => "Enter a GitHub skill URL.",
                }
                .to_string(),
            );
            return None;
        }
        let source_type = match self.install_source {
            SkillInstallSource::Local => "local",
            SkillInstallSource::GitHubUrl => "github_url",
        };
        let request_id = next_request_id("skills-install", &format!("{source_type}:{source}"));
        self.pending = Some(PendingToggle {
            request_id: request_id.clone(),
            action: "install",
            path: source.clone(),
            previous_enabled: None,
        });
        self.error = None;
        Some(json!({
            "type": "ui_command_action",
            "payload": {
                "schema_version": 1,
                "request_id": request_id,
                "presenter": "skills",
                "action": "install",
                "arguments": {"source_type": source_type, "source": source},
            }
        }))
    }

    fn install_source_label(&self) -> &'static str {
        match self.install_source {
            SkillInstallSource::Local => "Local path",
            SkillInstallSource::GitHubUrl => "GitHub URL",
        }
    }

    fn install_value_lines(&self, width: u16, theme: &ThemePalette) -> Vec<Line<'static>> {
        let placeholder = match self.install_source {
            SkillInstallSource::Local => "/path/to/skill",
            SkillInstallSource::GitHubUrl => {
                "https://github.com/owner/repo/tree/main/path/to/skill"
            }
        };
        let value = if self.query.is_empty() {
            placeholder
        } else {
            self.query.as_str()
        };
        let line = Line::from(vec![
            Span::styled(
                format!("{}: ", self.install_source_label()),
                Style::default().fg(theme.text_muted),
            ),
            Span::styled(value.to_string(), Style::default().fg(theme.text)),
        ]);
        crate::wrapping::word_wrap_line(
            &line,
            crate::wrapping::RtOptions::new(usize::from(width.max(1))),
        )
        .iter()
        .map(crate::wrapping::line_to_static)
        .collect()
    }

    fn render_install(&self, area: Rect, buf: &mut Buffer, theme: &ThemePalette) {
        let palette = theme.bottom_pane();
        let local_style = if self.install_source == SkillInstallSource::Local {
            Style::default()
                .fg(palette.input_accent)
                .add_modifier(Modifier::BOLD)
        } else {
            Style::default().fg(theme.text_muted)
        };
        let github_style = if self.install_source == SkillInstallSource::GitHubUrl {
            Style::default()
                .fg(palette.input_accent)
                .add_modifier(Modifier::BOLD)
        } else {
            Style::default().fg(theme.text_muted)
        };
        let (local_label, github_label) = if area.width >= 28 {
            ("[Local path]", "[GitHub URL]")
        } else {
            ("[Local]", "[GitHub]")
        };
        let gap = if area.width <= 16 { " " } else { "  " };
        Paragraph::new(Line::from(vec![
            Span::styled(local_label, local_style),
            Span::raw(gap),
            Span::styled(github_label, github_style),
        ]))
        .render(Rect::new(area.x, area.y, area.width, 1), buf);

        let value_lines = self.install_value_lines(area.width, theme);
        let hint = self.footer_hint(theme, area.width);
        let mut footer_lines = menu_footer_lines(&hint, None, area.width, theme);
        let mut footer_height = footer_lines.len() as u16;
        let mut visible_value_lines =
            usize::from(area.height.saturating_sub(footer_height).saturating_sub(3))
                .min(value_lines.len());
        let mut value_start = value_lines.len().saturating_sub(visible_value_lines);
        let mut position = scroll_position(value_start, visible_value_lines, value_lines.len());
        footer_lines = menu_footer_lines(&hint, position.as_deref(), area.width, theme);
        if footer_lines.len() as u16 != footer_height {
            footer_height = footer_lines.len() as u16;
            visible_value_lines =
                usize::from(area.height.saturating_sub(footer_height).saturating_sub(3))
                    .min(value_lines.len());
            value_start = value_lines.len().saturating_sub(visible_value_lines);
            position = scroll_position(value_start, visible_value_lines, value_lines.len());
            footer_lines = menu_footer_lines(&hint, position.as_deref(), area.width, theme);
        }
        for (offset, line) in value_lines
            .into_iter()
            .skip(value_start)
            .take(visible_value_lines)
            .enumerate()
        {
            line.render(
                Rect::new(
                    area.x,
                    area.y.saturating_add(2).saturating_add(offset as u16),
                    area.width,
                    1,
                ),
                buf,
            );
        }

        let footer_y = area
            .y
            .saturating_add(area.height.saturating_sub(footer_height));
        for (offset, line) in footer_lines.into_iter().enumerate() {
            line.render(
                Rect::new(
                    area.x,
                    footer_y.saturating_add(offset as u16),
                    area.width,
                    1,
                ),
                buf,
            );
        }
    }
}

fn viewport_start(selected: usize, total: usize, height: u16) -> usize {
    let visible = usize::from(height).max(1).min(total.max(1));
    selected
        .saturating_sub(visible.saturating_sub(1))
        .min(total.saturating_sub(visible))
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum SkillsToggleInput {
    None,
    Edited,
    Close,
    Action(Value),
}

fn stable_request_suffix(path: &str) -> String {
    let mut hash = 0xcbf29ce484222325_u64;
    for byte in path.as_bytes() {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x100000001b3);
    }
    format!("{hash:016x}")
}

static ACTION_REQUEST_SEQUENCE: AtomicU64 = AtomicU64::new(1);

fn next_request_id(prefix: &str, target: &str) -> String {
    let sequence = ACTION_REQUEST_SEQUENCE.fetch_add(1, Ordering::Relaxed);
    format!("{prefix}-{}-{sequence}", stable_request_suffix(target))
}

#[cfg(test)]
mod install_layout_tests {
    use super::*;
    #[test]
    fn narrow_install_forms_keep_the_entire_url_and_unicode_path() {
        let theme = ThemePalette::default();
        for source in [
            "https://github.com/owner/repository/tree/main/skills/long-skill-name",
            "/tmp/中文项目/很长的技能目录/安装来源",
        ] {
            let mut view = SkillsToggleViewState::new_install();
            view.query = source.into();
            view.install_source = if source.starts_with("https:") {
                SkillInstallSource::GitHubUrl
            } else {
                SkillInstallSource::Local
            };
            let lines = view.install_value_lines(32, &theme);
            assert!(lines.iter().all(|line| line.width() <= 32));
            let complete = lines
                .iter()
                .map(crate::wrapping::line_plain_text)
                .collect::<Vec<_>>()
                .join("");
            assert!(complete.contains(source), "{complete}");
        }
    }
}
