use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span, Text};
use std::cell::Cell;
use unicode_width::UnicodeWidthStr;

use crate::markdown::render_markdown;
use crate::theme::ThemePalette;

use super::{
    DisplayRow, ListSelectionView, PendingOption, PermissionFileChange, PermissionInteractionState,
};

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct ApprovalOverlayState {
    permission: PermissionInteractionState,
    selected_option: usize,
    submission_error: Option<String>,
    detail_scroll: Cell<usize>,
    detail_page_rows: Cell<usize>,
}

impl ApprovalOverlayState {
    pub(crate) fn new(permission: PermissionInteractionState) -> Self {
        Self {
            permission,
            selected_option: 0,
            submission_error: None,
            detail_scroll: Cell::new(0),
            detail_page_rows: Cell::new(1),
        }
    }

    pub(crate) fn permission(&self) -> &PermissionInteractionState {
        &self.permission
    }

    pub(crate) fn permission_mut(&mut self) -> &mut PermissionInteractionState {
        &mut self.permission
    }

    pub(crate) fn set_submission_error(&mut self, error: Option<String>) {
        self.submission_error = error.filter(|value| !value.trim().is_empty());
    }

    pub(crate) fn move_up(&mut self) {
        let option_count = self.option_count();
        if option_count == 0 {
            self.selected_option = 0;
            return;
        }
        self.selected_option = if self.selected_option == 0 {
            option_count - 1
        } else {
            self.selected_option - 1
        };
    }

    pub(crate) fn move_down(&mut self) {
        let option_count = self.option_count();
        if option_count == 0 {
            self.selected_option = 0;
            return;
        }
        self.selected_option = (self.selected_option + 1) % option_count;
    }

    pub(crate) fn selected_permission_option_id(&self) -> Option<String> {
        self.permission
            .options
            .get(self.selected_option)
            .map(|option| option.id.clone())
    }

    pub(crate) fn permission_cancel_option_id(&self) -> Option<String> {
        self.permission
            .options
            .iter()
            .find(|option| option.id == "deny")
            .map(|option| option.id.clone())
    }

    pub(crate) fn permission_option_id_for_shortcut(&self, shortcut: char) -> Option<String> {
        let interaction = &self.permission;
        let shortcut = shortcut.to_ascii_lowercase();
        interaction
            .options
            .iter()
            .find(|option| option_shortcut(&option.id, &option.label) == Some(shortcut))
            .map(|option| option.id.clone())
    }

    pub(crate) fn render_text(&self, theme: &ThemePalette, width: u16) -> Text<'static> {
        // InlineTerminal reserves the last column to avoid terminal autowrap.
        // Measure the complete frame inside that printable width as well.
        let width = width.saturating_sub(1).max(1);
        let mut text =
            render_permission_overlay(&self.permission, self.selected_option, theme, width);
        if self.permission.role_label.is_empty()
            && self.permission.saved_options.is_empty()
            && is_file_edit_approval(&self.permission)
            && self.permission.title.starts_with("Permission · ")
        {
            let mut context =
                crate::wrapping::adaptive_wrap_text(&self.permission.title, width.max(1) as usize)
                    .into_iter()
                    .map(|line| {
                        Line::from(Span::styled(
                            line,
                            Style::default()
                                .fg(theme.accent)
                                .add_modifier(Modifier::BOLD),
                        ))
                    })
                    .collect::<Vec<_>>();
            context.append(&mut text.lines);
            text.lines = context;
        }
        if let Some(error) = &self.submission_error {
            let mut lines: Vec<Line<'static>> = wrap_plain(
                &format!("Action rejected: {error}"),
                width.saturating_sub(4).max(1) as usize,
            )
            .into_iter()
            .map(|line| {
                Line::from(Span::styled(
                    format!("  {line}"),
                    Style::default().fg(theme.text).add_modifier(Modifier::BOLD),
                ))
            })
            .collect();
            lines.push(Line::from(String::new()));
            lines.append(&mut text.lines);
            text.lines = lines;
        }
        text
    }

    pub(crate) fn page_details(&self, down: bool) {
        let step = self.detail_page_rows.get().saturating_sub(1).max(1);
        let current = self.detail_scroll.get();
        self.detail_scroll.set(if down {
            current.saturating_add(step)
        } else {
            current.saturating_sub(step)
        });
    }

    pub(crate) fn render_text_in_height(
        &self,
        theme: &ThemePalette,
        width: u16,
        height: u16,
    ) -> Text<'static> {
        let mut text = self.render_text(theme, width);
        let card_width = usize::from(width.saturating_sub(1).max(1));
        let height = usize::from(height);
        let bordered = self.permission.approval_kind != "provider_recovery"
            && (!self.permission.role_label.is_empty()
                || (is_file_edit_approval(&self.permission)
                    && self.permission.saved_options.is_empty()));
        if !bordered || card_width < 18 || text.lines.len() <= height {
            self.detail_scroll.set(0);
            return text;
        }
        // Reuse the exact action layout so viewport sizing and drawing cannot disagree.
        let actions =
            approval_card_action_lines(&self.permission, self.selected_option, theme, card_width);
        if height <= actions.len() + 2 {
            return text;
        }
        let body_end = text.lines.len().saturating_sub(actions.len());
        let page_rows = height - actions.len() - 2; // pinned first row + paging hint
        let body = &text.lines[1..body_end];
        let offset = self
            .detail_scroll
            .get()
            .min(body.len().saturating_sub(page_rows));
        self.detail_scroll.set(offset);
        self.detail_page_rows.set(page_rows);
        let end = (offset + page_rows).min(body.len());
        let mut visible = vec![text.lines[0].clone()];
        visible.extend_from_slice(&body[offset..end]);
        visible.push(file_edit_card_row(
            vec![
                Span::raw("  "),
                Span::styled(
                    format!("PgUp/PgDn: {}-{}/{}", offset + 1, end, body.len()),
                    Style::default().fg(theme.text_muted),
                ),
            ],
            card_width,
            theme,
        ));
        visible.extend(actions);
        text.lines = visible;
        text
    }

    pub(crate) fn desired_height(&self, theme: &ThemePalette, width: u16) -> u16 {
        self.render_text(theme, width).lines.len() as u16
    }

    fn option_count(&self) -> usize {
        self.permission.options.len()
    }
}

fn render_permission_overlay(
    interaction: &PermissionInteractionState,
    selected: usize,
    theme: &ThemePalette,
    width: u16,
) -> Text<'static> {
    if interaction.approval_kind == "provider_recovery" {
        return render_recovery_overlay(interaction, selected, theme, width);
    }
    if !interaction.role_label.is_empty() {
        return render_approval_card(interaction, selected, theme, width);
    }
    if !interaction.saved_options.is_empty() || !interaction.approval_kind.trim().is_empty() {
        return render_typed_permission_overlay(interaction, selected, theme, width);
    }
    render_legacy_permission_overlay(interaction, selected, theme, width)
}

fn render_recovery_overlay(
    interaction: &PermissionInteractionState,
    selected: usize,
    theme: &ThemePalette,
    width: u16,
) -> Text<'static> {
    let content_width = super::menu_surface::menu_surface_content_width(width) as usize;
    let wrap = |text: &str, style: Style| {
        crate::wrapping::adaptive_wrap_text_with(
            text,
            crate::wrapping::RtOptions::new(content_width),
        )
        .into_iter()
        .map(|row| Line::from(Span::styled(row, style)))
        .collect::<Vec<_>>()
    };
    let title = if interaction.title.trim().is_empty() {
        "Provider request failed"
    } else {
        interaction.title.trim()
    };
    let mut header = wrap(
        &format!("Recovery · {title}"),
        Style::default()
            .fg(theme.text_title)
            .add_modifier(Modifier::BOLD),
    );
    for text in [&interaction.request_summary, &interaction.reason] {
        if !text.trim().is_empty() {
            header.push(Line::default());
            header.extend(wrap(text.trim(), Style::default().fg(theme.text)));
        }
    }
    let rows = interaction
        .options
        .iter()
        .enumerate()
        .map(|(index, option)| DisplayRow {
            name: format!(
                "{}{}. {}",
                if index == selected { "› " } else { "  " },
                index + 1,
                option.label
            ),
            description: option.description.clone(),
            match_indices: None,
            selection_index: Some(index),
            is_section_header: false,
        })
        .collect();
    let footer = if interaction.submission.blocked() {
        interaction.submission.label()
    } else {
        "Enter confirm  ↑/↓ select  Esc close".to_string()
    };
    let view = ListSelectionView::new(
        header,
        rows,
        selected,
        Some(Line::from(Span::styled(
            footer,
            Style::default().fg(theme.text_muted),
        ))),
    )
    .with_footer_aligned_to_header();
    render_list_selection_text(view, width, theme, "  No recovery options")
}

fn render_typed_permission_overlay(
    interaction: &PermissionInteractionState,
    selected: usize,
    theme: &ThemePalette,
    width: u16,
) -> Text<'static> {
    if is_file_edit_approval(interaction) && interaction.saved_options.is_empty() {
        return render_approval_card(interaction, selected, theme, width);
    }
    let inner_width = width.saturating_sub(6).max(1) as usize;
    let mut header = Vec::new();
    let is_mcp_elicitation = interaction.approval_kind.trim() == "mcp_elicitation";
    if is_mcp_elicitation {
        // Codex MCP elicitation overlay parity: "{server} needs your approval."
        let server = if interaction.environment_id.trim().is_empty() {
            "MCP server".to_string()
        } else {
            interaction.environment_id.trim().to_string()
        };
        header.push(Line::from(Span::styled(
            format!("{server} needs your approval."),
            Style::default()
                .fg(theme.text_title)
                .add_modifier(Modifier::BOLD),
        )));
        header.push(Line::from(String::new()));
        let message = if !interaction.body_markdown.trim().is_empty() {
            interaction.body_markdown.trim().to_string()
        } else {
            interaction.reason.trim().to_string()
        };
        if !message.is_empty() {
            header.push(Line::from(Span::styled(
                message,
                Style::default().fg(theme.text),
            )));
            header.push(Line::from(String::new()));
        }
    } else {
        header.push(Line::from(Span::styled(
            interaction.title.clone(),
            Style::default()
                .fg(theme.text_title)
                .add_modifier(Modifier::BOLD),
        )));
        header.push(Line::from(String::new()));
    }
    if !interaction.environment_id.trim().is_empty() {
        header.push(Line::from(vec![
            Span::styled("Environment: ", Style::default().fg(theme.text_muted)),
            Span::styled(
                interaction.environment_id.clone(),
                Style::default().fg(theme.text_title),
            ),
        ]));
        header.push(Line::from(String::new()));
    }
    if !interaction.reason.trim().is_empty()
        && !interaction
            .request_summary
            .lines()
            .any(|line| line.trim().starts_with("Reason: "))
    {
        header.push(Line::from(vec![
            Span::styled("Reason: ", Style::default().fg(theme.text_muted)),
            Span::styled(
                interaction.reason.clone(),
                Style::default()
                    .fg(theme.text)
                    .add_modifier(Modifier::ITALIC),
            ),
        ]));
        header.push(Line::from(String::new()));
    }
    if !interaction.request_summary.trim().is_empty() {
        for wrapped in wrap_plain(
            interaction.request_summary.trim(),
            inner_width.saturating_sub(2),
        ) {
            header.push(Line::from(Span::styled(
                wrapped,
                Style::default().fg(theme.text),
            )));
        }
        header.push(Line::from(String::new()));
    }
    let permission_rule = interaction
        .permission_rule
        .trim()
        .is_empty()
        .then_some(interaction.permission_class.trim())
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| interaction.permission_rule.trim().to_string());
    if !permission_rule.trim().is_empty() {
        header.push(Line::from(vec![
            Span::styled("Permission rule: ", Style::default().fg(theme.text_muted)),
            Span::styled(permission_rule, Style::default().fg(theme.accent)),
        ]));
        header.push(Line::from(String::new()));
    }
    if interaction.approval_kind != "tool"
        && interaction.network_host.trim().is_empty()
        && !interaction.command.trim().is_empty()
    {
        for wrapped in wrap_plain(
            &format!("$ {}", interaction.command.trim()),
            inner_width.saturating_sub(2),
        ) {
            header.push(Line::from(Span::styled(
                wrapped,
                Style::default().fg(theme.text),
            )));
        }
        header.push(Line::from(String::new()));
    }
    // ListSelectionView owns the single gap between its header and option rows.
    // Keep section-internal spacing above, but do not pass a trailing blank row
    // across that component boundary or the two gaps stack.
    while header.last().is_some_and(|line| line.width() == 0) {
        header.pop();
    }
    let rows = interaction
        .options
        .iter()
        .enumerate()
        .map(|(index, option)| {
            let prefix = if index == selected.min(interaction.options.len().saturating_sub(1)) {
                "› "
            } else {
                "  "
            };
            let shortcut = option_shortcut(&option.id, &option.label)
                .map(|key| format!(" ({key})"))
                .unwrap_or_default();
            DisplayRow {
                name: format!("{prefix}{}. {}{}", index + 1, option.label, shortcut),
                description: String::new(),
                match_indices: None,
                selection_index: Some(index),
                is_section_header: false,
            }
        })
        .collect::<Vec<_>>();
    let footer = if interaction.submission.blocked() {
        interaction.submission.label()
    } else {
        if interaction.role_label.is_empty() {
            "Press enter to confirm · Esc close".to_string()
        } else if !interaction.saved_options.is_empty() {
            "Enter confirm · Esc back".into()
        } else {
            "Enter confirm · Esc dismiss".into()
        }
    };
    let view = ListSelectionView::new(
        header,
        rows,
        selected,
        Some(Line::from(Span::styled(
            footer,
            Style::default().fg(theme.text_muted),
        ))),
    );
    render_list_selection_text(
        view.with_footer_aligned_to_header(),
        width,
        theme,
        "  No approval options",
    )
}

fn is_file_edit_approval(interaction: &PermissionInteractionState) -> bool {
    if interaction.approval_kind.trim() == "apply_patch" || !interaction.file_changes.is_empty() {
        return true;
    }
    matches!(
        interaction.tool_name.trim(),
        "file_write"
            | "file_writer"
            | "file_edit"
            | "multi_file_edit"
            | "batch_apply"
            | "patch_apply"
            | "notebook_edit"
            | "file_move"
            | "file_rename"
            | "file_delete"
            | "delete_file"
            | "mkdir"
    )
}

fn render_approval_card(
    interaction: &PermissionInteractionState,
    selected: usize,
    theme: &ThemePalette,
    width: u16,
) -> Text<'static> {
    let card_width = usize::from(width.max(1));
    if card_width < 18 {
        return render_narrow_file_edit_permission_overlay(interaction, selected, theme, width);
    }
    let content_width = card_width.saturating_sub(6).max(1);
    let modern = !interaction.role_label.is_empty();
    let confirming = !interaction.saved_options.is_empty();
    let files = is_file_edit_approval(interaction);
    let title = if confirming {
        "Remember approval"
    } else if !modern {
        "FILE CHANGES"
    } else if files {
        "Confirm file changes"
    } else if !interaction.command.is_empty() {
        "Confirm command"
    } else {
        "Confirm operation"
    };
    let mut lines = vec![approval_card_top(title, card_width, theme)];
    let row = |text: &str| {
        file_edit_card_row(
            vec![
                Span::raw("  "),
                Span::styled(text.to_owned(), Style::default().fg(theme.text)),
            ],
            card_width,
            theme,
        )
    };
    if modern {
        lines.push(row(""));
        for (label, value, color) in [
            ("Role:   ", &interaction.role_label, theme.accent),
            ("Stage:  ", &interaction.stage_label, theme.text_title),
        ] {
            lines.extend(approval_styled_rows(
                vec![
                    Span::styled(label, Style::default().fg(theme.text_muted)),
                    Span::styled(
                        value.clone(),
                        Style::default().fg(color).add_modifier(Modifier::BOLD),
                    ),
                ],
                content_width,
                card_width,
                theme,
            ));
        }
        lines.push(row(""));
    }
    if modern && !confirming && !interaction.tool_name.is_empty() {
        lines.extend(approval_styled_rows(
            vec![
                Span::styled("Operation: ", Style::default().fg(theme.text_muted)),
                Span::styled(
                    interaction.tool_name.clone(),
                    Style::default()
                        .fg(theme.accent)
                        .add_modifier(Modifier::BOLD),
                ),
            ],
            content_width,
            card_width,
            theme,
        ));
    }
    if confirming {
        for text in interaction.remember_scope.lines() {
            if let Some((label, value)) = text.split_once(": ") {
                lines.extend(approval_styled_rows(
                    vec![
                        Span::styled(
                            format!("{:<8}", format!("{label}:")),
                            Style::default().fg(theme.text_muted),
                        ),
                        Span::styled(
                            value.to_owned(),
                            Style::default()
                                .fg(theme.text_title)
                                .add_modifier(Modifier::BOLD),
                        ),
                    ],
                    content_width,
                    card_width,
                    theme,
                ));
            } else {
                for segment in wrap_file_card(text, content_width) {
                    lines.push(row(&segment));
                }
            }
        }
    } else if !files {
        if !interaction.command.is_empty() {
            for (index, command_line) in interaction.command.lines().enumerate() {
                lines.extend(approval_styled_rows(
                    vec![
                        Span::styled(
                            if index == 0 { "$ " } else { "  " },
                            Style::default()
                                .fg(theme.accent)
                                .add_modifier(Modifier::BOLD),
                        ),
                        Span::styled(
                            command_line.to_owned(),
                            Style::default()
                                .fg(theme.text_title)
                                .add_modifier(Modifier::BOLD),
                        ),
                    ],
                    content_width,
                    card_width,
                    theme,
                ));
            }
        } else {
            let details = if interaction.operation_details.is_empty() {
                "Arguments unavailable"
            } else {
                &interaction.operation_details
            };
            for detail in details.lines() {
                let (label, value) = detail.split_once(": ").unwrap_or(("", detail));
                let label = if label == "query" { "Query" } else { label };
                lines.extend(approval_styled_rows(
                    vec![
                        Span::styled(
                            if label.is_empty() {
                                String::new()
                            } else {
                                format!("{label}: ")
                            },
                            Style::default().fg(theme.text_muted),
                        ),
                        Span::styled(value.to_owned(), Style::default().fg(theme.text_title)),
                    ],
                    content_width,
                    card_width,
                    theme,
                ));
            }
        }
        if !interaction.cwd.is_empty() {
            lines.extend(approval_styled_rows(
                vec![
                    Span::styled("In: ", Style::default().fg(theme.text_muted)),
                    Span::styled(
                        interaction.cwd.clone(),
                        Style::default().fg(theme.text_muted),
                    ),
                ],
                content_width,
                card_width,
                theme,
            ));
        }
    } else {
        let summary = file_edit_scope_summary(interaction);
        for (index, segment) in wrap_file_card(&summary, content_width)
            .into_iter()
            .enumerate()
        {
            lines.push(file_edit_card_row(
                vec![
                    Span::styled(
                        if index == 0 { "  ◆  " } else { "     " },
                        Style::default().fg(theme.accent),
                    ),
                    Span::styled(
                        segment,
                        Style::default()
                            .fg(theme.text_title)
                            .add_modifier(Modifier::BOLD),
                    ),
                ],
                card_width,
                theme,
            ));
        }

        if interaction.file_changes.is_empty() {
            if let Some(target) = file_edit_fallback_target(interaction) {
                for (index, segment) in wrap_file_card(&target, content_width)
                    .into_iter()
                    .enumerate()
                {
                    lines.push(file_edit_card_row(
                        vec![
                            Span::styled(
                                if index == 0 { "  ◇  " } else { "     " },
                                Style::default().fg(theme.text_muted),
                            ),
                            Span::styled(segment, Style::default().fg(theme.text)),
                        ],
                        card_width,
                        theme,
                    ));
                }
            }
        } else {
            for change in &interaction.file_changes {
                for detail in file_change_detail_lines(change, content_width, theme, true) {
                    let mut spans = vec![Span::raw("  ")];
                    spans.extend(detail.spans);
                    lines.push(file_edit_card_row(spans, card_width, theme));
                }
            }
        }
        if let Some(risk) = (!modern)
            .then(|| file_edit_risk_summary(interaction))
            .flatten()
        {
            for (index, segment) in wrap_file_card(&compact_file_edit_risk(&risk), content_width)
                .into_iter()
                .enumerate()
            {
                lines.push(file_edit_card_row(
                    vec![
                        Span::styled(
                            if index == 0 { "  !  " } else { "     " },
                            Style::default()
                                .fg(theme.warning)
                                .add_modifier(Modifier::BOLD),
                        ),
                        Span::styled(segment, Style::default().fg(theme.warning)),
                    ],
                    card_width,
                    theme,
                ));
            }
        }
    }

    lines.push(row(""));
    lines.extend(approval_card_action_lines(
        interaction,
        selected,
        theme,
        card_width,
    ));
    Text::from(lines)
}

fn approval_card_action_lines(
    interaction: &PermissionInteractionState,
    selected: usize,
    theme: &ThemePalette,
    card_width: usize,
) -> Vec<Line<'static>> {
    let content_width = card_width.saturating_sub(6).max(1);
    let modern = !interaction.role_label.is_empty();
    let confirming = !interaction.saved_options.is_empty();
    let mut lines = Vec::new();
    lines.push(file_edit_card_divider("ACTIONS", card_width, theme));
    if interaction.options.is_empty() {
        lines.push(file_edit_card_row(
            vec![Span::styled(
                "  No approval options",
                Style::default()
                    .fg(theme.text_muted)
                    .add_modifier(Modifier::ITALIC),
            )],
            card_width,
            theme,
        ));
    } else {
        for (index, option) in interaction.options.iter().enumerate() {
            lines.extend(file_edit_action_rows(
                option,
                index == selected.min(interaction.options.len().saturating_sub(1)),
                content_width,
                card_width,
                theme,
                modern,
            ));
        }
    }
    lines.push(file_edit_card_divider("", card_width, theme));
    let footer = if interaction.submission.blocked() {
        interaction.submission.label()
    } else if confirming {
        "↑↓ Navigate   Enter Confirm   Esc Back".to_string()
    } else {
        "↑↓ Navigate   Enter Confirm   Esc Close".to_string()
    };
    for segment in wrap_file_card(&footer, content_width) {
        lines.push(file_edit_card_row(
            vec![
                Span::raw("  "),
                Span::styled(segment, Style::default().fg(theme.text_muted)),
            ],
            card_width,
            theme,
        ));
    }
    lines.push(file_edit_card_bottom(card_width, theme));
    lines
}

/// Wrap styled content before adding frame rails, preserving emphasis on continuation rows.
fn approval_styled_rows(
    spans: Vec<Span<'static>>,
    content_width: usize,
    card_width: usize,
    theme: &ThemePalette,
) -> Vec<Line<'static>> {
    let continuation = spans
        .first()
        .filter(|span| span.content.trim_end().ends_with(':'))
        .map(|span| " ".repeat(span.width()))
        .unwrap_or_default();
    let line = Line::from(spans);
    crate::wrapping::word_wrap_line(
        &line,
        crate::wrapping::RtOptions::new(content_width.max(1))
            .break_words(true)
            .subsequent_indent(Line::from(continuation)),
    )
    .into_iter()
    .map(|line| {
        let mut spans = vec![Span::raw("  ")];
        spans.extend(crate::wrapping::line_to_static(&line).spans);
        file_edit_card_row(spans, card_width, theme)
    })
    .collect()
}

fn render_narrow_file_edit_permission_overlay(
    interaction: &PermissionInteractionState,
    selected: usize,
    theme: &ThemePalette,
    width: u16,
) -> Text<'static> {
    let header = vec![Line::from(Span::styled(
        "File changes",
        Style::default()
            .fg(theme.text_title)
            .add_modifier(Modifier::BOLD),
    ))];
    let rows = interaction
        .options
        .iter()
        .enumerate()
        .map(|(index, option)| DisplayRow {
            name: format!(
                "{} [{}] {}",
                if index == selected.min(interaction.options.len().saturating_sub(1)) {
                    "›"
                } else {
                    " "
                },
                file_permission_shortcut(&option.id).to_ascii_uppercase(),
                compact_file_permission_label(&option.id, &option.label),
            ),
            description: String::new(),
            match_indices: None,
            selection_index: Some(index),
            is_section_header: false,
        })
        .collect::<Vec<_>>();
    render_list_selection_text(
        ListSelectionView::new_compact(header, rows, selected, None),
        width,
        theme,
        "No options",
    )
}

fn file_edit_scope_summary(interaction: &PermissionInteractionState) -> String {
    let file_count = interaction.file_changes.len();
    if file_count == 1 {
        return format!(
            "1 file · {}",
            file_change_kind_label(&interaction.file_changes[0].change_kind)
        );
    }
    if file_count > 1 {
        let mut kinds = interaction
            .file_changes
            .iter()
            .map(|change| file_change_kind_label(&change.change_kind))
            .collect::<Vec<_>>();
        kinds.sort_unstable();
        kinds.dedup();
        let scope = if kinds.len() == 1 {
            kinds[0]
        } else {
            "Mixed changes"
        };
        return format!("{file_count} files · {scope}");
    }
    match interaction.tool_name.trim().to_ascii_lowercase().as_str() {
        "multi_file_edit" => "Multiple file edits".to_string(),
        "batch_apply" | "patch_apply" | "apply_patch" => "Batch file update".to_string(),
        "notebook_edit" => "Notebook edit".to_string(),
        "file_write" | "file_writer" => "Write file".to_string(),
        "file_edit" => "Edit file".to_string(),
        "file_move" | "file_rename" => "Move file".to_string(),
        "file_delete" | "delete_file" => "Delete file".to_string(),
        "mkdir" => "Create directory".to_string(),
        _ => "File edit".to_string(),
    }
}

fn file_edit_fallback_target(interaction: &PermissionInteractionState) -> Option<String> {
    interaction
        .request_summary
        .lines()
        .map(str::trim)
        .find(|line| {
            !line.is_empty()
                && !line.starts_with("Operation:")
                && !line.starts_with("Reason:")
                && !line.starts_with("Risk:")
        })
        .map(str::to_string)
}

fn compact_file_edit_risk(risk: &str) -> String {
    let risk = risk
        .trim()
        .strip_prefix("Risk:")
        .or_else(|| risk.trim().strip_prefix("Reason:"))
        .unwrap_or(risk.trim())
        .trim();
    let normalized = risk.to_ascii_lowercase();
    if normalized.contains("remove existing content") {
        return "Removes existing content".to_string();
    }
    if normalized.contains("overwrite") || normalized.contains("will be replaced") {
        return "Overwrites existing content".to_string();
    }
    if normalized.contains("delete") {
        return "Deletes existing content".to_string();
    }
    let mut characters = risk.chars();
    let Some(first) = characters.next() else {
        return "Review required".to_string();
    };
    format!("{}{}", first.to_uppercase(), characters.as_str())
        .trim_end_matches('.')
        .to_string()
}

fn approval_card_top(title: &str, width: usize, theme: &ThemePalette) -> Line<'static> {
    let label = format!(" {title} ");
    let rule_len = width.saturating_sub(label.width() + 3);
    Line::from(vec![
        Span::styled("╭─", Style::default().fg(theme.border)),
        Span::styled(
            label,
            Style::default()
                .fg(theme.accent)
                .add_modifier(Modifier::BOLD),
        ),
        Span::styled(
            format!("{}╮", "─".repeat(rule_len)),
            Style::default().fg(theme.border),
        ),
    ])
}

fn file_edit_card_divider(label: &str, width: usize, theme: &ThemePalette) -> Line<'static> {
    if label.is_empty() {
        return Line::from(Span::styled(
            format!("├{}┤", "─".repeat(width.saturating_sub(2))),
            Style::default().fg(theme.border),
        ));
    }
    let label = format!(" {label} ");
    let rule_len = width.saturating_sub(label.width() + 3);
    Line::from(vec![
        Span::styled("├─", Style::default().fg(theme.border)),
        Span::styled(
            label,
            Style::default()
                .fg(theme.accent_soft)
                .add_modifier(Modifier::BOLD),
        ),
        Span::styled(
            format!("{}┤", "─".repeat(rule_len)),
            Style::default().fg(theme.border),
        ),
    ])
}

fn file_edit_card_bottom(width: usize, theme: &ThemePalette) -> Line<'static> {
    Line::from(Span::styled(
        format!("╰{}╯", "─".repeat(width.saturating_sub(2))),
        Style::default().fg(theme.border),
    ))
}

fn file_edit_card_row(
    content: Vec<Span<'static>>,
    width: usize,
    theme: &ThemePalette,
) -> Line<'static> {
    let used = content
        .iter()
        .map(|span| span.content.as_ref().width())
        .sum::<usize>();
    let padding = width.saturating_sub(used + 2);
    let mut spans = Vec::with_capacity(content.len() + 3);
    spans.push(Span::styled("│", Style::default().fg(theme.border)));
    spans.extend(content);
    spans.push(Span::raw(" ".repeat(padding)));
    spans.push(Span::styled("│", Style::default().fg(theme.border)));
    Line::from(spans)
}

fn file_permission_shortcut(option_id: &str) -> char {
    match option_id.trim().to_ascii_lowercase().as_str() {
        "approve" | "approve_once" => 'y',
        "approve_tool" | "approve_tool_run" => 'p',
        "approve_session" => 's',
        "cancel" => 'c',
        "deny_command" => 'd',
        "deny" | "deny_once" | "decline" => 'n',
        _ => '?',
    }
}

fn file_edit_action_rows(
    option: &PendingOption,
    selected: bool,
    _content_width: usize,
    card_width: usize,
    theme: &ThemePalette,
    full_label: bool,
) -> Vec<Line<'static>> {
    let label = if full_label {
        option.label.clone()
    } else {
        compact_file_permission_label(&option.id, &option.label)
    };
    let shortcut = option_shortcut(&option.id, &option.label);
    let badge_width = if shortcut.is_some() { 4 } else { 0 };
    let label_width = card_width.saturating_sub(6 + badge_width).max(1);
    wrap_file_card(&label, label_width)
        .into_iter()
        .enumerate()
        .map(|(index, segment)| {
            let pointer = if index == 0 && selected {
                "  › "
            } else {
                "    "
            };
            let badge = if index == 0 {
                shortcut
                    .map(|key| format!("[{}] ", key.to_ascii_uppercase()))
                    .unwrap_or_default()
            } else {
                " ".repeat(badge_width)
            };
            let badge_style = if selected {
                Style::default()
                    .fg(theme.accent)
                    .add_modifier(Modifier::BOLD)
            } else {
                Style::default()
                    .fg(theme.text_muted)
                    .add_modifier(Modifier::BOLD)
            };
            let label_style = if selected {
                Style::default()
                    .fg(theme.text_title)
                    .add_modifier(Modifier::BOLD)
            } else {
                Style::default().fg(theme.text)
            };
            file_edit_card_row(
                vec![
                    Span::styled(pointer, Style::default().fg(theme.accent)),
                    Span::styled(badge, badge_style),
                    Span::styled(segment, label_style),
                ],
                card_width,
                theme,
            )
        })
        .collect()
}

fn file_edit_risk_summary(interaction: &PermissionInteractionState) -> Option<String> {
    let summary_reason = interaction
        .request_summary
        .lines()
        .find_map(|line| line.trim().strip_prefix("Reason:"))
        .map(str::trim)
        .filter(|reason| !reason.is_empty());
    if let Some(reason) = summary_reason {
        let normalized = reason.to_ascii_lowercase();
        return (!file_edit_reason_is_generic(&normalized)).then(|| reason.to_string());
    }
    let reason = interaction.reason.trim();
    let generic = reason.to_ascii_lowercase();
    if file_edit_reason_is_generic(&generic) {
        return None;
    }
    Some(reason.to_string())
}

fn file_edit_reason_is_generic(reason: &str) -> bool {
    reason.is_empty()
        || reason.contains("requires approval")
        || reason.contains("approval required")
        || reason.contains("human approval")
        || reason.starts_with("allow tool ")
        || reason == "replacement shortens existing content"
        || reason == "one or more edits remove existing content"
        || reason == "existing file will be replaced"
        || reason.starts_with("overwrite existing file:")
        || reason == "patch operations request destructive or potentially overwriting changes"
}

fn file_change_detail_lines(
    change: &PermissionFileChange,
    width: usize,
    theme: &ThemePalette,
    show_marker: bool,
) -> Vec<Line<'static>> {
    let marker = if show_marker {
        format!("{}  ", file_change_marker(&change.change_kind))
    } else {
        String::new()
    };
    let mut detail = format!("{marker}{}", change.path.trim());
    if !change.destination.trim().is_empty() {
        detail.push_str(" → ");
        detail.push_str(change.destination.trim());
    }
    let metrics = file_change_metrics(change);
    if !metrics.is_empty() {
        detail.push_str(" · ");
        detail.push_str(&metrics);
    }
    wrap_file_card(&detail, width)
        .into_iter()
        .map(|line| {
            Line::from(Span::styled(
                line,
                Style::default().fg(file_change_color(change, theme)),
            ))
        })
        .collect()
}

fn compact_file_permission_label(option_id: &str, fallback: &str) -> String {
    match option_id.trim().to_ascii_lowercase().as_str() {
        "approve" | "approve_once" => "Allow once".to_string(),
        "approve_tool" | "approve_tool_run" => "Allow file edits for this run".to_string(),
        "deny_command" => "Deny operation and continue".to_string(),
        "cancel" => fallback.trim().to_string(),
        "deny" | "deny_once" | "decline" => "Deny request".to_string(),
        _ => fallback.trim().to_string(),
    }
}

fn file_change_kind_label(kind: &str) -> &'static str {
    match kind.trim().to_ascii_lowercase().as_str() {
        "create" | "add" => "Create",
        "write" => "Write",
        "overwrite" => "Overwrite",
        "replace" => "Replace (delete + create)",
        "move" | "rename" => "Move",
        "delete" => "Delete",
        "create_directory" | "mkdir" => "Create directory",
        _ => "Modify",
    }
}

fn file_change_marker(kind: &str) -> &'static str {
    match kind.trim().to_ascii_lowercase().as_str() {
        "create" | "add" | "create_directory" | "mkdir" => "A",
        "move" | "rename" => "R",
        "delete" => "D",
        _ => "M",
    }
}

fn file_change_color(change: &PermissionFileChange, theme: &ThemePalette) -> ratatui::style::Color {
    match change.change_kind.trim().to_ascii_lowercase().as_str() {
        "delete" | "overwrite" | "replace" => theme.warning,
        _ => theme.text,
    }
}

fn file_change_metrics(change: &PermissionFileChange) -> String {
    let mut parts = Vec::new();
    if change.added_lines.is_some() || change.removed_lines.is_some() {
        parts.push(format!(
            "+{} −{}",
            change.added_lines.unwrap_or(0),
            change.removed_lines.unwrap_or(0)
        ));
    }
    if let Some(size_bytes) = change.size_bytes {
        parts.push(format_file_size(size_bytes));
    }
    parts.join(" · ")
}

fn format_file_size(size_bytes: u64) -> String {
    if size_bytes < 1024 {
        return format!("{size_bytes} B");
    }
    let kib = size_bytes as f64 / 1024.0;
    if kib < 1024.0 {
        return format!("{kib:.1} KB");
    }
    format!("{:.1} MB", kib / 1024.0)
}

fn render_list_selection_text(
    view: ListSelectionView,
    width: u16,
    theme: &ThemePalette,
    empty_message: &str,
) -> Text<'static> {
    let mut buffer = ratatui::buffer::Buffer::empty(ratatui::layout::Rect {
        x: 0,
        y: 0,
        width,
        height: view.desired_height(width),
    });
    let area = buffer.area;
    view.render(area, &mut buffer, theme, empty_message);
    Text::from(
        (0..area.height)
            .map(|y| {
                let mut line = Line::from(String::new());
                for x in 0..area.width {
                    let cell = buffer.get(x, y);
                    line.spans
                        .push(Span::styled(cell.symbol().to_string(), cell.style()));
                }
                line
            })
            .collect::<Vec<_>>(),
    )
}

fn render_legacy_permission_overlay(
    interaction: &PermissionInteractionState,
    selected: usize,
    theme: &ThemePalette,
    width: u16,
) -> Text<'static> {
    let inner_width = width.saturating_sub(6).max(1) as usize;
    let mut header = section_title("Permission required", theme);
    header.push(Line::from(Span::styled(
        interaction.title.clone(),
        Style::default()
            .fg(theme.text_title)
            .add_modifier(Modifier::BOLD),
    )));
    header.push(divider(theme));
    header.extend(render_markdown(
        interaction.body_markdown.trim(),
        inner_width,
        theme,
    ));
    if !interaction.options.is_empty() {
        header.push(Line::from(String::new()));
        header.push(section_label("Options", theme));
    }
    let rows = interaction
        .options
        .iter()
        .enumerate()
        .map(|(index, option)| DisplayRow {
            name: format!(
                "{} {}. {}",
                if index == selected.min(interaction.options.len().saturating_sub(1)) {
                    crate::style::GLYPH_CURSOR
                } else {
                    " "
                },
                index + 1,
                option.label
            ),
            description: option.description.clone(),
            match_indices: None,
            selection_index: Some(index),
            is_section_header: false,
        })
        .collect::<Vec<_>>();
    let footer = Line::from(Span::styled(
        if interaction.submission.blocked() {
            interaction.submission.label()
        } else {
            "Enter confirm  |  Up/Down select  |  Esc close".to_string()
        },
        Style::default().fg(theme.text_muted),
    ));
    let view = ListSelectionView::new(header, rows, selected, Some(footer));
    render_list_selection_text(view, width, theme, "  No approval options")
}

fn option_shortcut(option_id: &str, label: &str) -> Option<char> {
    if option_id == "approve_session" {
        return Some('s');
    }
    if option_id == "deny" {
        return Some('n');
    }
    let option_id = option_id.trim().to_ascii_lowercase();
    let label = label.trim().to_ascii_lowercase();
    if option_id == "approve" {
        return Some('y');
    }
    if option_id == "cancel" {
        return Some('c');
    }
    if option_id == "approve_class" {
        return Some('a');
    }
    if option_id == "approve_tool" {
        return Some('p');
    }
    if option_id == "deny_command" {
        return Some('d');
    }
    if option_id == "deny" && label.contains("continue without") {
        return Some('n');
    }
    None
}

#[cfg(test)]
fn permission_decision_label(option_id: &str) -> Option<String> {
    let normalized = option_id.trim().to_ascii_lowercase().replace('-', "_");
    let label = match normalized.as_str() {
        "approve" | "approve_once" => "Approved once",
        "approve_tool" | "approve_tool_run" => "Approved for this tool in this run",
        "approve_class" | "approve_class_run" | "always" => "Approved for this command class",
        "deny" | "deny_once" | "cancel" | "decline" => "Denied once",
        "deny_command" => "Denied and requested changes",
        _ => return None,
    };
    Some(label.to_string())
}

fn section_title(title: &str, theme: &ThemePalette) -> Vec<Line<'static>> {
    vec![Line::from(Span::styled(
        title.to_string(),
        Style::default()
            .fg(theme.accent_soft)
            .add_modifier(Modifier::BOLD),
    ))]
}

fn section_label(label: &str, theme: &ThemePalette) -> Line<'static> {
    Line::from(Span::styled(
        label.to_string(),
        Style::default()
            .fg(theme.text_title)
            .add_modifier(Modifier::BOLD),
    ))
}

fn divider(theme: &ThemePalette) -> Line<'static> {
    Line::from(Span::styled(
        "─".repeat(48),
        Style::default().fg(theme.border),
    ))
}

fn wrap_plain(text: &str, width: usize) -> Vec<String> {
    let mut lines = Vec::new();
    for source_line in if text.is_empty() {
        vec![""]
    } else {
        text.lines().collect::<Vec<_>>()
    } {
        let wrapped = crate::wrapping::adaptive_wrap_text_with(
            source_line,
            crate::wrapping::RtOptions::new(width.max(1)).break_words(false),
        );
        if wrapped.is_empty() {
            lines.push(String::new());
        } else {
            lines.extend(wrapped);
        }
    }
    lines
}

fn wrap_file_card(text: &str, width: usize) -> Vec<String> {
    let mut lines = Vec::new();
    for source_line in if text.is_empty() {
        vec![""]
    } else {
        text.lines().collect::<Vec<_>>()
    } {
        let wrapped = crate::wrapping::adaptive_wrap_text_with(
            source_line,
            crate::wrapping::RtOptions::new(width.max(1)).break_words(true),
        );
        if wrapped.is_empty() {
            lines.push(String::new());
        } else {
            lines.extend(wrapped);
        }
    }
    lines
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn confirmation_actions_have_no_empty_shortcut_column() {
        let theme = ThemePalette::default();
        for width in [40usize, 80, 120] {
            let option = PendingOption {
                id: "remember_confirm".into(),
                label: "Confirm".into(),
                description: "".into(),
            };
            let lines = file_edit_action_rows(&option, true, width - 6, width, &theme, true);
            assert!(
                lines[0].to_string().starts_with("│  › Confirm"),
                "{}",
                lines[0]
            );
            assert!(lines[0].width() <= width);
        }
    }

    #[test]
    fn approval_emphasis_tracks_terminal_palette_and_hides_reasons() {
        use crate::theme::TerminalColorLevel;
        use ratatui::style::Color;
        for light in [true, false] {
            for level in [TerminalColorLevel::TrueColor, TerminalColorLevel::Ansi256] {
                let (fg, bg) = if light { (17, 255) } else { (238, 17) };
                let theme = ThemePalette::from_terminal_colors_for_level(
                    Some(Color::Rgb(fg, fg, fg)),
                    Some(Color::Rgb(bg, bg, bg)),
                    level,
                );
                for width in [40u16, 80, 120] {
                    for patch in [false, true] {
                        let state = PermissionInteractionState {
                            role_label: "Core".into(),
                            stage_label: "literature grounding".into(),
                            approval_kind: if patch { "apply_patch" } else { "command" }.into(),
                            command: if patch {
                                ""
                            } else {
                                "mkdir -p literature_grounding && find literature_grounding -type f"
                            }
                            .into(),
                            cwd: "/workspace/文献研究/tryout".into(),
                            reason: "REASON_SENTINEL".into(),
                            file_changes: if patch {
                                vec![PermissionFileChange {
                                    path: "plan.md".into(),
                                    change_kind: "create".into(),
                                    ..Default::default()
                                }]
                            } else {
                                vec![]
                            },
                            ..Default::default()
                        };
                        let text = ApprovalOverlayState::new(state).render_text(&theme, width);
                        let plain = text
                            .lines
                            .iter()
                            .map(Line::to_string)
                            .collect::<Vec<_>>()
                            .join("\n");
                        assert!(!plain.contains("REASON_SENTINEL"));
                        let spans = text
                            .lines
                            .iter()
                            .flat_map(|line| &line.spans)
                            .collect::<Vec<_>>();
                        assert!(spans.iter().any(|s| s.content == "Core"
                            && s.style.fg == Some(theme.accent)
                            && s.style.add_modifier.contains(Modifier::BOLD)));
                        assert!(spans
                            .iter()
                            .any(|s| s.content.contains("literature grounding")
                                && s.style.fg == Some(theme.text_title)
                                && s.style.add_modifier.contains(Modifier::BOLD)));
                        if !patch {
                            assert!(spans.iter().any(|s| s.content.contains("mkdir")
                                && s.style.add_modifier.contains(Modifier::BOLD)));
                            assert!(spans.iter().any(|s| s.content.contains("/workspace")
                                && s.style.fg == Some(theme.text_muted)
                                && !s.style.add_modifier.contains(Modifier::BOLD)));
                        }
                        for line in text.lines {
                            assert!(line.width() <= usize::from(width - 1));
                        }
                    }
                }
            }
        }
    }

    #[test]
    fn command_and_patch_share_bordered_card_and_complete_labels() {
        for width in [40u16, 80, 120] {
            for patch in [false, true] {
                let mut interaction = PermissionInteractionState {
                    role_label: "Core".into(),
                    stage_label: "literature grounding".into(),
                    approval_kind: if patch { "apply_patch" } else { "command" }.into(),
                    command: if patch { "" } else { "pwd && rg --files" }.into(),
                    file_changes: if patch {
                        vec![PermissionFileChange {
                            path: "/workspace/文献研究/literature_grounding/plan.md".into(),
                            change_kind: "create".into(),
                            ..Default::default()
                        }]
                    } else {
                        vec![]
                    },
                    options: vec![
                        PendingOption {
                            id: "approve".into(),
                            label: "Allow once".into(),
                            description: "".into(),
                        },
                        PendingOption {
                            id: "approve_session".into(),
                            label: "Remember for this run".into(),
                            description: "".into(),
                        },
                        PendingOption {
                            id: "deny".into(),
                            label: "Deny this request".into(),
                            description: "".into(),
                        },
                    ],
                    ..Default::default()
                };
                for confirming in [false, true] {
                    if confirming {
                        interaction.saved_options = interaction.options.clone();
                        interaction.options = vec![
                            PendingOption {
                                id: "remember_confirm".into(),
                                label: "Confirm".into(),
                                description: "".into(),
                            },
                            PendingOption {
                                id: "remember_back".into(),
                                label: "Back".into(),
                                description: "".into(),
                            },
                        ];
                        interaction.remember_scope = "Applies to: This run".into();
                    }
                    let rendered =
                        render_permission_overlay(&interaction, 0, &ThemePalette::default(), width);
                    let rows = rendered
                        .lines
                        .iter()
                        .map(Line::to_string)
                        .collect::<Vec<_>>();
                    assert!(rows[0].starts_with("╭─"));
                    assert_eq!(
                        rows[1].trim_matches(|c: char| c == '│' || c.is_whitespace()),
                        ""
                    );
                    let text = rows.join("\n");
                    for required in ["Role:   Core", "Stage:  literature grounding", "ACTIONS"] {
                        assert!(text.contains(required), "{text}");
                    }
                    if !confirming {
                        assert!(text.contains("Remember for this run"), "{text}");
                        assert!(text.contains(if patch {
                            "Confirm file changes"
                        } else {
                            "Confirm command"
                        }));
                        assert!(text.contains(if patch { "1 file · Create" } else { "$ pwd" }));
                    }
                    assert!(!text.contains('…') && !text.contains("[?]"), "{text}");
                    assert!(
                        !text.contains("Why approval is needed")
                            && !text.to_lowercase().contains("f details")
                    );
                    for row in rows {
                        assert!(row.width() <= usize::from(width), "{width}: {row}");
                    }
                    let overlay = ApprovalOverlayState::new(interaction.clone());
                    let physical = overlay.render_text(&ThemePalette::default(), width);
                    for row in physical.lines {
                        assert!(
                            row.width() <= usize::from(width - 1),
                            "Frame crosses reserved terminal column: {row}"
                        );
                        assert!(row.to_string().ends_with(['│', '╮', '╯', '┤']), "{row}");
                    }
                }
            }
        }
    }

    #[test]
    fn operation_card_shows_tool_and_full_wrapped_arguments() {
        let url = "https://arxiv.org/abs/2405.18765?query=cross-subject-neural-decoding";
        for width in [40u16, 80, 120] {
            let interaction = PermissionInteractionState {
                role_label: "Subagent · eeg-scan".into(),
                stage_label: "direct qa".into(),
                tool_name: "web_fetch".into(),
                request_summary: "web_fetch".into(),
                operation_details: format!(
                    "url: {url}\nquery: EEG motor imagery cross-subject Riemannian alignment"
                ),
                ..Default::default()
            };
            let rendered =
                render_permission_overlay(&interaction, 0, &ThemePalette::default(), width);
            let rows = rendered
                .lines
                .iter()
                .map(Line::to_string)
                .collect::<Vec<_>>();
            let text = rows.join("\n");
            assert!(text.contains("Operation: web_fetch"), "{text}");
            assert!(text.contains("Query: EEG"), "{text}");
            assert!(!text.contains("query: EEG"), "{text}");
            let actions = rows.iter().position(|row| row.contains("ACTIONS")).unwrap();
            assert!(rows[actions - 1]
                .trim_matches(|c: char| c == '│' || c.is_whitespace())
                .is_empty());
            let compact = text.replace('│', "").split_whitespace().collect::<String>();
            assert!(compact.contains(url), "{text}");
            assert!(!text.contains("Why approval") && !text.contains("f Details"));
            for row in rows {
                assert!(row.width() <= width as usize, "{row}");
            }
        }
    }

    #[test]
    fn replacement_label_keeps_destructive_operation_visible() {
        assert_eq!(
            file_change_kind_label("replace"),
            "Replace (delete + create)"
        );
        assert_eq!(file_change_marker("replace"), "M");
        let theme = ThemePalette::default();
        let change = PermissionFileChange {
            change_kind: "replace".to_string(),
            ..Default::default()
        };
        assert_eq!(file_change_color(&change, &theme), theme.warning);
        assert_eq!(file_change_kind_label("delete"), "Delete");
    }

    #[test]
    fn typed_permission_footer_aligns_with_header() {
        let mut interaction = PermissionInteractionState {
            approval_kind: "network".to_string(),
            title: "Allow network access for this task?".to_string(),
            reason: "Hosted web search needs network access before the task starts.".to_string(),
            request_summary: "Provider endpoint · bob-api.com".to_string(),
            permission_rule: "all network-class tools for this task".to_string(),
            options: vec![
                PendingOption {
                    id: "approve".to_string(),
                    label: "Allow for this task".to_string(),
                    description: String::new(),
                },
                PendingOption {
                    id: "deny".to_string(),
                    label: "Continue offline".to_string(),
                    description: String::new(),
                },
            ],
            ..PermissionInteractionState::default()
        };

        for width in [40, 80, 100] {
            for submitting in [false, true] {
                interaction.submission.set_requested(submitting);
                let rendered = render_typed_permission_overlay(
                    &interaction,
                    0,
                    &ThemePalette::default(),
                    width,
                );
                let lines = rendered
                    .lines
                    .iter()
                    .map(|line| {
                        line.spans
                            .iter()
                            .map(|span| span.content.as_ref())
                            .collect::<String>()
                    })
                    .collect::<Vec<_>>();
                for text in [
                    "Allow network access",
                    "Reason:",
                    "Provider endpoint",
                    "Permission rule:",
                    if submitting {
                        "Submitting…"
                    } else {
                        "Press enter"
                    },
                ] {
                    let column = lines.iter().find_map(|line| line.find(text));
                    assert_eq!(
                        column,
                        Some(2),
                        "width={width}, text={text}, lines={lines:#?}"
                    );
                }
            }
        }
    }

    #[test]
    fn approval_text_keeps_long_url_intact() {
        let url = "https://example.com/a-very-long-path-name";
        assert_eq!(wrap_plain(url, 12), vec![url.to_string()]);
    }

    #[test]
    fn permission_decisions_use_option_scope_not_button_copy() {
        assert_eq!(
            permission_decision_label("approve").as_deref(),
            Some("Approved once")
        );
        assert_eq!(
            permission_decision_label("approve_tool").as_deref(),
            Some("Approved for this tool in this run")
        );
        assert_eq!(
            permission_decision_label("approve_class").as_deref(),
            Some("Approved for this command class")
        );
        assert_eq!(
            permission_decision_label("deny").as_deref(),
            Some("Denied once")
        );
        assert_eq!(
            permission_decision_label("deny_command").as_deref(),
            Some("Denied and requested changes")
        );
        assert_eq!(permission_decision_label("legacy_custom"), None);
    }
}
