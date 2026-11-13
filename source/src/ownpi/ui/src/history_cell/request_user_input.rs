use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};

use crate::bottom_pane::PermissionFileChange;
use crate::history_cell::base::wrap_for_width;
use crate::theme::ThemePalette;

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct ClarificationAnswerSummary {
    pub(crate) question: String,
    pub(crate) answer: Option<String>,
    pub(crate) note: Option<String>,
}

pub(crate) fn render_clarification_result(
    title: &str,
    answers: &[ClarificationAnswerSummary],
    width: usize,
    theme: &ThemePalette,
) -> Vec<Line<'static>> {
    let mut lines = vec![Line::from(vec![
        Span::styled(
            crate::style::GLYPH_ACTION,
            Style::default().fg(theme.accent),
        ),
        Span::styled(
            title.to_string(),
            Style::default()
                .fg(theme.text_title)
                .add_modifier(Modifier::BOLD),
        ),
    ])];

    for item in answers {
        let question_prefix = "  • ";
        for (index, segment) in wrap_for_width(
            &item.question,
            width.saturating_sub(question_prefix.len()).max(1),
        )
        .into_iter()
        .enumerate()
        {
            let prefix = if index == 0 { question_prefix } else { "    " };
            lines.push(Line::from(vec![
                Span::styled(prefix, Style::default().fg(theme.text_muted)),
                Span::styled(segment, Style::default().fg(theme.text)),
            ]));
        }

        if let Some(answer_text) = item.answer.clone() {
            let answer_prefix = "    answer: ";
            if width < 28 {
                lines.push(Line::from(Span::styled(
                    answer_prefix.trim_end().to_string(),
                    Style::default().fg(theme.text_muted),
                )));
                for segment in wrap_for_width(&answer_text, width.saturating_sub(4).max(1)) {
                    lines.push(Line::from(vec![
                        Span::styled("    ", Style::default().fg(theme.text_muted)),
                        Span::styled(segment, Style::default().fg(theme.accent_soft)),
                    ]));
                }
            } else {
                for (index, segment) in wrap_for_width(
                    &answer_text,
                    width.saturating_sub(answer_prefix.len()).max(1),
                )
                .into_iter()
                .enumerate()
                {
                    let prefix = if index == 0 {
                        answer_prefix
                    } else {
                        "            "
                    };
                    lines.push(Line::from(vec![
                        Span::styled(prefix, Style::default().fg(theme.text_muted)),
                        Span::styled(segment, Style::default().fg(theme.accent_soft)),
                    ]));
                }
            }
        }

        if let Some(note_text) = item.note.clone().filter(|value| !value.trim().is_empty()) {
            let note_prefix = "    note: ";
            if width < 28 {
                lines.push(Line::from(Span::styled(
                    note_prefix.trim_end().to_string(),
                    Style::default().fg(theme.text_muted),
                )));
                for segment in wrap_for_width(&note_text, width.saturating_sub(4).max(1)) {
                    lines.push(Line::from(vec![
                        Span::styled("    ", Style::default().fg(theme.text_muted)),
                        Span::styled(segment, Style::default().fg(theme.accent_soft)),
                    ]));
                }
            } else {
                for (index, segment) in
                    wrap_for_width(&note_text, width.saturating_sub(note_prefix.len()).max(1))
                        .into_iter()
                        .enumerate()
                {
                    let prefix = if index == 0 {
                        note_prefix
                    } else {
                        "          "
                    };
                    lines.push(Line::from(vec![
                        Span::styled(prefix, Style::default().fg(theme.text_muted)),
                        Span::styled(segment, Style::default().fg(theme.accent_soft)),
                    ]));
                }
            }
        }

        if item.answer.is_none() && item.note.is_none() {
            lines.push(Line::from(vec![
                Span::styled("    answer: ", Style::default().fg(theme.text_muted)),
                Span::styled("(unanswered)", Style::default().fg(theme.accent_soft)),
            ]));
        }
    }

    lines
}

pub(crate) fn render_permission_result(
    title: &str,
    request_summary: &str,
    decision: &str,
    file_changes: &[PermissionFileChange],
    width: usize,
    theme: &ThemePalette,
) -> Vec<Line<'static>> {
    let mut lines = vec![Line::from(vec![
        Span::styled(
            crate::style::GLYPH_ACTION,
            Style::default().fg(theme.accent),
        ),
        Span::styled(
            title.to_string(),
            Style::default()
                .fg(theme.text_title)
                .add_modifier(Modifier::BOLD),
        ),
    ])];
    if !request_summary.trim().is_empty() {
        let prefix = "  └ ";
        for (index, segment) in wrap_for_width(
            request_summary.trim(),
            width.saturating_sub(prefix.len()).max(1),
        )
        .into_iter()
        .enumerate()
        {
            let current_prefix = if index == 0 { prefix } else { "    " };
            lines.push(Line::from(vec![
                Span::styled(current_prefix, Style::default().fg(theme.text_muted)),
                Span::styled(segment, Style::default().fg(theme.text)),
            ]));
        }
    }
    for change in file_changes {
        let target = if change.destination.trim().is_empty() {
            change.path.clone()
        } else {
            format!("{} → {}", change.path, change.destination)
        };
        let kind = if change.change_kind.trim().is_empty() {
            "modify"
        } else {
            change.change_kind.as_str()
        };
        let detail = format!("{kind} · {target}");
        for (index, segment) in wrap_for_width(&detail, width.saturating_sub(4).max(1))
            .into_iter()
            .enumerate()
        {
            lines.push(Line::from(vec![
                Span::styled(
                    if index == 0 { "  ◇ " } else { "    " },
                    Style::default().fg(theme.text_muted),
                ),
                Span::styled(segment, Style::default().fg(theme.text)),
            ]));
        }
    }
    let prefix = "  ↳ ";
    for (index, segment) in wrap_for_width(decision, width.saturating_sub(prefix.len()).max(1))
        .into_iter()
        .enumerate()
    {
        let current_prefix = if index == 0 { prefix } else { "    " };
        lines.push(Line::from(vec![
            Span::styled(current_prefix, Style::default().fg(theme.text_muted)),
            Span::styled(segment, Style::default().fg(theme.accent_soft)),
        ]));
    }
    lines
}
