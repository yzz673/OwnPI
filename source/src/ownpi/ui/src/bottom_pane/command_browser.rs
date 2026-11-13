use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Paragraph, Widget};

use crate::theme::ThemePalette;

use super::menu_surface::{
    menu_footer_lines, menu_surface_content_width, menu_surface_padding_height,
    render_menu_surface, scroll_position,
};
use super::selection_popup::{render_rows_single_line, single_line_row_viewport};
use super::{DisplayRow, ScrollState, ServiceTierCommand};

const MAX_BROWSER_ROWS: usize = 12;

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct CommandBrowserViewState {
    commands: Vec<ServiceTierCommand>,
    query: String,
    state: ScrollState,
}

impl CommandBrowserViewState {
    pub(crate) fn new(commands: Vec<ServiceTierCommand>) -> Self {
        let mut view = Self {
            commands,
            query: String::new(),
            state: ScrollState::default(),
        };
        view.reset_filtered_selection();
        view
    }

    pub(crate) fn desired_height(&self, width: u16) -> u16 {
        let total = self.filtered_indices().len();
        let visible = total.clamp(1, MAX_BROWSER_ROWS);
        let position = scroll_position(self.state.scroll_top, visible, total);
        let footer_height = menu_footer_lines(
            &command_browser_hint(&ThemePalette::default(), menu_surface_content_width(width)),
            position.as_deref(),
            menu_surface_content_width(width),
            &ThemePalette::default(),
        )
        .len() as u16;
        (visible as u16)
            .saturating_add(4)
            .saturating_add(footer_height)
            .saturating_add(menu_surface_padding_height())
    }

    pub(crate) fn handle_key(&mut self, key: KeyEvent) -> CommandBrowserInput {
        match key {
            KeyEvent {
                code: KeyCode::Esc, ..
            } => CommandBrowserInput::Close,
            KeyEvent {
                code: KeyCode::Up, ..
            }
            | KeyEvent {
                code: KeyCode::Char('p'),
                modifiers: KeyModifiers::CONTROL,
                ..
            } => {
                self.move_up();
                CommandBrowserInput::Edited
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
                CommandBrowserInput::Edited
            }
            KeyEvent {
                code: KeyCode::PageUp,
                ..
            } => {
                self.state
                    .page_up_clamped(self.filtered_indices().len(), MAX_BROWSER_ROWS);
                self.skip_disabled(false);
                CommandBrowserInput::Edited
            }
            KeyEvent {
                code: KeyCode::PageDown,
                ..
            } => {
                self.state
                    .page_down_clamped(self.filtered_indices().len(), MAX_BROWSER_ROWS);
                self.skip_disabled(true);
                CommandBrowserInput::Edited
            }
            KeyEvent {
                code: KeyCode::Home,
                ..
            } => {
                self.state
                    .jump_top(self.filtered_indices().len(), MAX_BROWSER_ROWS);
                self.skip_disabled(true);
                CommandBrowserInput::Edited
            }
            KeyEvent {
                code: KeyCode::End, ..
            } => {
                self.state
                    .jump_bottom(self.filtered_indices().len(), MAX_BROWSER_ROWS);
                self.skip_disabled(false);
                CommandBrowserInput::Edited
            }
            KeyEvent {
                code: KeyCode::Enter,
                ..
            } => self
                .selected_command()
                .map(|command| {
                    CommandBrowserInput::Selected(format!(
                        "/{}",
                        command.name.trim_start_matches('/')
                    ))
                })
                .unwrap_or(CommandBrowserInput::None),
            KeyEvent {
                code: KeyCode::Tab, ..
            } => self
                .selected_command()
                .map(|command| {
                    CommandBrowserInput::Inserted(format!(
                        "/{} ",
                        command.name.trim_start_matches('/')
                    ))
                })
                .unwrap_or(CommandBrowserInput::None),
            KeyEvent {
                code: KeyCode::Backspace,
                ..
            } => {
                self.query.pop();
                self.reset_filtered_selection();
                CommandBrowserInput::Edited
            }
            KeyEvent {
                code: KeyCode::Char(ch),
                modifiers,
                ..
            } if modifiers.is_empty() || modifiers == KeyModifiers::SHIFT => {
                self.query.push(ch);
                self.reset_filtered_selection();
                CommandBrowserInput::Edited
            }
            _ => CommandBrowserInput::None,
        }
    }

    pub(crate) fn copy_source(&self) -> String {
        self.commands
            .iter()
            .map(|command| {
                format!(
                    "/{} - {}",
                    command.name.trim_start_matches('/'),
                    command.description
                )
            })
            .collect::<Vec<_>>()
            .join("\n")
    }

    pub(crate) fn render(&self, area: Rect, buf: &mut Buffer, theme: &ThemePalette) {
        if area.is_empty() {
            return;
        }
        let palette = theme.bottom_pane();
        let vertical_padding = area.height >= self.desired_height(area.width);
        let inner = render_menu_surface(area, buf, theme, vertical_padding);
        if inner.is_empty() {
            return;
        }

        Paragraph::new(Line::from(Span::styled(
            "Commands",
            Style::default()
                .fg(palette.input_accent)
                .add_modifier(Modifier::BOLD),
        )))
        .render(Rect::new(inner.x, inner.y, inner.width, 1), buf);
        let search_placeholder = if inner.width >= 20 {
            "Search commands"
        } else {
            "Search"
        };
        Paragraph::new(Line::from(vec![
            Span::styled("/ ", Style::default().fg(palette.input_accent)),
            Span::styled(
                if self.query.is_empty() {
                    search_placeholder
                } else {
                    &self.query
                },
                Style::default().fg(theme.text),
            ),
        ]))
        .render(
            Rect::new(inner.x, inner.y.saturating_add(1), inner.width, 1),
            buf,
        );

        let filtered = self.filtered_indices();
        let rows = self.display_rows(&filtered);
        let hint = command_browser_hint(theme, inner.width);
        let no_position_footer = menu_footer_lines(&hint, None, inner.width, theme);
        let mut footer_height = no_position_footer.len() as u16;
        let mut list_height = inner
            .height
            .saturating_sub(3)
            .saturating_sub(footer_height)
            .min(MAX_BROWSER_ROWS as u16);
        let (mut start, _) =
            single_line_row_viewport(&rows, &self.state, MAX_BROWSER_ROWS, list_height);
        let mut position = scroll_position(
            start,
            usize::from(list_height).min(filtered.len()),
            filtered.len(),
        );
        let mut footer_lines = menu_footer_lines(&hint, position.as_deref(), inner.width, theme);
        if footer_lines.len() as u16 != footer_height {
            footer_height = footer_lines.len() as u16;
            list_height = inner
                .height
                .saturating_sub(3)
                .saturating_sub(footer_height)
                .min(MAX_BROWSER_ROWS as u16);
            (start, _) =
                single_line_row_viewport(&rows, &self.state, MAX_BROWSER_ROWS, list_height);
            position = scroll_position(
                start,
                usize::from(list_height).min(filtered.len()),
                filtered.len(),
            );
            footer_lines = menu_footer_lines(&hint, position.as_deref(), inner.width, theme);
        }
        render_rows_single_line(
            Rect::new(inner.x, inner.y.saturating_add(3), inner.width, list_height),
            buf,
            &rows,
            &self.state,
            MAX_BROWSER_ROWS,
            "No matching commands",
            theme,
        );
        let footer_y = inner
            .y
            .saturating_add(inner.height.saturating_sub(footer_height));
        for (offset, line) in footer_lines.into_iter().enumerate() {
            line.render(
                Rect::new(
                    inner.x,
                    footer_y.saturating_add(offset as u16),
                    inner.width,
                    1,
                ),
                buf,
            );
        }
    }

    fn filtered_indices(&self) -> Vec<usize> {
        let query = self
            .query
            .trim()
            .trim_start_matches('/')
            .to_ascii_lowercase();
        self.commands
            .iter()
            .enumerate()
            .filter_map(|(index, command)| {
                let name = command.name.trim_start_matches('/').to_ascii_lowercase();
                (query.is_empty()
                    || name.contains(&query)
                    || command.description.to_ascii_lowercase().contains(&query))
                .then_some(index)
            })
            .collect()
    }

    fn selected_command(&self) -> Option<&ServiceTierCommand> {
        let filtered = self.filtered_indices();
        filtered
            .get(self.state.selected_idx?)
            .and_then(|index| self.commands.get(*index))
            .filter(|command| !command.disabled)
    }

    fn move_up(&mut self) {
        let len = self.filtered_indices().len();
        self.state.move_up_wrap(len);
        self.skip_disabled(false);
    }

    fn move_down(&mut self) {
        let len = self.filtered_indices().len();
        self.state.move_down_wrap(len);
        self.skip_disabled(true);
    }

    fn reset_filtered_selection(&mut self) {
        let len = self.filtered_indices().len();
        self.state.reset();
        self.state.clamp_selection(len);
        self.skip_disabled(true);
    }

    fn skip_disabled(&mut self, forward: bool) {
        let filtered = self.filtered_indices();
        let len = filtered.len();
        for _ in 0..len {
            let enabled = self
                .state
                .selected_idx
                .and_then(|index| filtered.get(index))
                .and_then(|index| self.commands.get(*index))
                .is_some_and(|command| !command.disabled);
            if enabled {
                break;
            }
            if forward {
                self.state.move_down_wrap(len);
            } else {
                self.state.move_up_wrap(len);
            }
        }
        self.state.ensure_visible(len, len.min(MAX_BROWSER_ROWS));
    }

    fn display_rows(&self, filtered: &[usize]) -> Vec<DisplayRow> {
        filtered
            .iter()
            .enumerate()
            .filter_map(|(position, index)| {
                self.commands.get(*index).map(|command| DisplayRow {
                    name: format!(
                        "{} /{}",
                        if self.state.selected_idx == Some(position) {
                            ">"
                        } else {
                            " "
                        },
                        command.name.trim_start_matches('/')
                    ),
                    description: command.description.clone(),
                    match_indices: None,
                    selection_index: Some(position),
                    is_section_header: false,
                })
            })
            .collect()
    }
}

fn command_browser_hint(theme: &ThemePalette, width: u16) -> Line<'static> {
    let text = if width >= 64 {
        "Up/Down or Ctrl+P/Ctrl+N move  Enter insert  Esc close"
    } else if width >= 32 {
        "Up/Dn  Ctrl+P/N  Enter  Esc"
    } else {
        "Up/Dn Enter Esc"
    };
    Line::from(Span::styled(text, Style::default().fg(theme.text_muted)))
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum CommandBrowserInput {
    None,
    Edited,
    Close,
    Selected(String),
    Inserted(String),
}
