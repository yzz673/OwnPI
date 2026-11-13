//! Human-question bottom-view layout and rendering.
//!
//! Owns the layout math (`HumanQuestionLayout`), text wrapping, option-row building,
//! and the height/cursor/render methods for the dedicated human-question bottom view.
//! The free functions and the private `BottomPaneState` methods below must stay in one
//! module: the methods are the only callers of the (private) free functions.
use super::*;
use ratatui::widgets::{BorderType, Borders};
use unicode_width::UnicodeWidthStr;
#[derive(Clone, Debug)]
struct HumanQuestionLayout {
    progress_area: Rect,
    question_area: Rect,
    validation_area: Rect,
    options_area: Rect,
    notes_area: Rect,
    footer_rule_area: Rect,
    footer_area: Rect,
    question_lines: Vec<String>,
    option_visible_items: usize,
}

const HUMAN_QUESTION_TIP_SEPARATOR: &str = " | ";
const HUMAN_QUESTION_RULE_GLYPH: &str = "┄";
const HUMAN_QUESTION_FOOTER_CHROME_HEIGHT: u16 = 2;
const HUMAN_QUESTION_MIN_CONTENT_HEIGHT: u16 = 5;

#[derive(Clone, Debug)]
struct HumanQuestionFooterTip {
    text: String,
    highlighted: bool,
}

impl HumanQuestionFooterTip {
    fn new(text: impl Into<String>) -> Self {
        Self {
            text: text.into(),
            highlighted: false,
        }
    }

    fn highlighted(text: impl Into<String>) -> Self {
        Self {
            text: text.into(),
            highlighted: true,
        }
    }
}

fn human_question_footer_tips(interaction: &HumanInteractionState) -> Vec<HumanQuestionFooterTip> {
    if interaction.cancel_requested {
        return vec![HumanQuestionFooterTip::highlighted("cancelling…")];
    }
    if interaction.submit_requested {
        return vec![HumanQuestionFooterTip::highlighted("submitting…")];
    }
    let mut tips = Vec::new();
    let question_count = interaction.questions.len().max(1);
    let is_last_question = interaction.current_question_index.saturating_add(1) >= question_count;
    let has_options = interaction.current_question_has_options();
    let notes_visible = interaction.notes_visible_for_current_question();

    if has_options
        && !notes_visible
        && interaction
            .current_question()
            .is_some_and(|question| question.multi_select)
    {
        tips.push(HumanQuestionFooterTip::new("space to toggle"));
    }

    if has_options && !notes_visible {
        tips.push(HumanQuestionFooterTip::highlighted("tab to add notes"));
    } else if has_options && notes_visible {
        tips.push(HumanQuestionFooterTip::new("tab to hide notes"));
    }

    let submit_text = if question_count == 1 {
        "enter to submit answer"
    } else if is_last_question {
        "enter to submit all"
    } else {
        "enter to submit answer"
    };
    tips.push(if question_count == 1 || is_last_question {
        HumanQuestionFooterTip::highlighted(submit_text)
    } else {
        HumanQuestionFooterTip::new(submit_text)
    });

    if question_count > 1 {
        tips.push(HumanQuestionFooterTip::new(
            if has_options && !notes_visible {
                "←/→ to navigate questions"
            } else {
                "ctrl + p / ctrl + n change question"
            },
        ));
    }
    tips.push(HumanQuestionFooterTip::new("esc to close · draft retained"));
    tips
}

fn wrap_human_question_footer_tips(
    width: u16,
    tips: Vec<HumanQuestionFooterTip>,
) -> Vec<Vec<HumanQuestionFooterTip>> {
    let max_width = usize::from(width.max(1));
    let separator_width = UnicodeWidthStr::width(HUMAN_QUESTION_TIP_SEPARATOR);
    if tips.is_empty() {
        return vec![Vec::new()];
    }

    let mut lines = Vec::new();
    let mut current = Vec::new();
    let mut used = 0usize;
    for tip in tips {
        let tip_width = UnicodeWidthStr::width(tip.text.as_str()).min(max_width);
        let extra = if current.is_empty() {
            tip_width
        } else {
            separator_width.saturating_add(tip_width)
        };
        if !current.is_empty() && used.saturating_add(extra) > max_width {
            lines.push(current);
            current = Vec::new();
            used = 0;
        }
        used = if current.is_empty() {
            tip_width
        } else {
            used.saturating_add(separator_width)
                .saturating_add(tip_width)
        };
        current.push(tip);
    }
    if !current.is_empty() {
        lines.push(current);
    }
    lines
}

fn human_question_footer_required_height(interaction: &HumanInteractionState, width: u16) -> u16 {
    wrap_human_question_footer_tips(width, human_question_footer_tips(interaction)).len() as u16
}

fn human_question_footer_lines(
    interaction: &HumanInteractionState,
    theme: &ThemePalette,
    layout: &HumanQuestionLayout,
) -> Vec<Line<'static>> {
    wrap_human_question_footer_tips(
        layout.footer_area.width,
        human_question_footer_tips(interaction),
    )
    .into_iter()
    .map(|tips| {
        let mut spans = Vec::new();
        for (index, tip) in tips.into_iter().enumerate() {
            if index > 0 {
                spans.push(Span::styled(
                    HUMAN_QUESTION_TIP_SEPARATOR,
                    Style::default().fg(theme.text_muted),
                ));
            }
            let style = if tip.highlighted {
                Style::default()
                    .fg(theme.accent)
                    .add_modifier(Modifier::BOLD)
            } else {
                Style::default().fg(theme.text_muted)
            };
            spans.push(Span::styled(tip.text, style));
        }
        Line::from(spans)
    })
    .collect()
}

fn inset_human_question_area(area: Rect) -> Rect {
    Rect {
        // One column is the rounded border and one column is horizontal padding.
        x: area.x.saturating_add(2),
        // One row is the rounded border and one row is deliberate top whitespace.
        y: area.y.saturating_add(2),
        width: area.width.saturating_sub(4),
        // Preserve one blank row above and below the content inside the border.
        height: area.height.saturating_sub(4),
    }
}

fn human_question_composer_area(area: Rect) -> Rect {
    if area.width == 0 {
        return area;
    }
    Rect {
        // ChatComposer normally reserves an accent rail. The question panel
        // already owns that visual hierarchy, so compensate here to align the
        // answer text with the Question header without changing chat input.
        x: area
            .x
            .saturating_sub(super::chat_composer::COMPOSER_EDGE_COLS),
        width: area
            .width
            .saturating_add(super::chat_composer::COMPOSER_EDGE_COLS),
        ..area
    }
}

fn human_question_progress_text(interaction: &HumanInteractionState, width: u16) -> String {
    let total = interaction.questions.len().max(1);
    let current = interaction
        .current_question_index
        .saturating_add(1)
        .min(total);
    let compact = format!("{current}/{total}");
    let base = format!("Question {compact}");
    let required = interaction
        .current_question()
        .is_none_or(|question| question.required);
    let status = if required { "Required" } else { "Optional" };
    let unanswered = interaction.unanswered_count();
    if unanswered > 0 {
        let full = format!("{base} ({unanswered} unanswered) · {status}");
        if UnicodeWidthStr::width(full.as_str()) <= usize::from(width) {
            return full;
        }
    }
    let with_status = format!("{base} · {status}");
    if UnicodeWidthStr::width(with_status.as_str()) <= usize::from(width) {
        return with_status;
    }
    let compact_status = format!("{compact} · {}", if required { "R" } else { "O" });
    if UnicodeWidthStr::width(compact_status.as_str()) <= usize::from(width) {
        return compact_status;
    }
    compact
}

fn human_question_body_lines(
    interaction: &HumanInteractionState,
    question: &ClarificationQuestion,
    width: usize,
) -> Vec<String> {
    let plan = interaction
        .is_plan_approval()
        .then(|| {
            interaction
                .plan_markdown
                .as_deref()
                .unwrap_or_default()
                .trim()
        })
        .filter(|plan| !plan.is_empty())
        .unwrap_or_default();
    let body = if plan.is_empty() {
        question.question.trim().to_string()
    } else if question.question.trim().is_empty() {
        plan.to_string()
    } else {
        format!("{plan}\n\n{}", question.question.trim())
    };
    if body.is_empty() {
        return Vec::new();
    }
    wrap_human_question_text(&body, width)
}

fn human_question_option_rows(
    interaction: &HumanInteractionState,
    question: &ClarificationQuestion,
) -> Vec<DisplayRow> {
    let cursor = interaction.option_cursor();
    let draft = interaction.draft_for(&question.question_id);
    (0..question.effective_option_count())
        .map(|index| {
            let label = question.option_label_at(index).unwrap_or_default();
            let cursor_marker = if index == cursor {
                crate::style::GLYPH_CURSOR
            } else {
                " "
            };
            let name = if question.multi_select {
                let checked = draft
                    .selected_labels
                    .iter()
                    .any(|selected| selected.eq_ignore_ascii_case(label));
                format!(
                    "{cursor_marker} [{}] {}. {label}",
                    if checked { "✓" } else { " " },
                    index + 1,
                )
            } else {
                format!("{cursor_marker} {}. {label}", index + 1)
            };
            DisplayRow {
                name,
                description: question
                    .option_description_at(index)
                    .unwrap_or_default()
                    .to_string(),
                match_indices: None,
                selection_index: Some(index),
                is_section_header: false,
            }
        })
        .collect()
}

fn ellipsize_human_question_line(line: &str, width: usize) -> String {
    if width == 0 {
        return String::new();
    }
    if width == 1 {
        return "…".to_string();
    }
    let budget = width - 1;
    let mut rendered = String::new();
    let mut used = 0usize;
    for ch in line.chars() {
        let char_width = unicode_width::UnicodeWidthChar::width(ch).unwrap_or(0);
        if used.saturating_add(char_width) > budget {
            break;
        }
        rendered.push(ch);
        used = used.saturating_add(char_width);
    }
    rendered.push('…');
    rendered
}

fn human_question_options_height(
    interaction: &HumanInteractionState,
    question: &ClarificationQuestion,
    width: u16,
) -> u16 {
    let rows = human_question_option_rows(interaction, question);
    if rows.is_empty() {
        return 0;
    }
    let mut state = ScrollState {
        selected_idx: Some(interaction.option_cursor()),
        scroll_top: 0,
    };
    state.ensure_visible(rows.len(), rows.len());
    measure_rows_height(&rows, &state, rows.len(), width)
}

fn human_question_visible_option_items(
    rows: &[DisplayRow],
    state: &ScrollState,
    width: u16,
    available_height: u16,
) -> usize {
    if rows.is_empty() || available_height == 0 {
        return 0;
    }
    let mut visible = 1usize;
    for candidate in 1..=rows.len() {
        let mut candidate_state = *state;
        candidate_state.ensure_visible(rows.len(), candidate);
        let measured = measure_rows_height(rows, &candidate_state, candidate, width);
        if measured > available_height {
            break;
        }
        visible = candidate;
    }
    visible
}

fn wrap_human_question_text(text: &str, width: usize) -> Vec<String> {
    let mut lines = Vec::new();
    for source_line in if text.is_empty() {
        vec![""]
    } else {
        text.lines().collect::<Vec<_>>()
    } {
        let wrapped = crate::wrapping::adaptive_wrap_text(source_line, width.max(1));
        if wrapped.is_empty() {
            lines.push(String::new());
        } else {
            lines.extend(wrapped);
        }
    }
    lines
}

fn human_question_error_marker(line: &str) -> Option<usize> {
    let lower = line.to_ascii_lowercase();
    ["not found", "failed", "error:", "invalid "]
        .iter()
        .filter_map(|marker| lower.find(marker))
        .min()
}

fn human_question_body_line(
    line: &str,
    theme: &ThemePalette,
    _answered: bool,
    error_tail: bool,
) -> (Line<'static>, bool) {
    if let Some(marker) = human_question_error_marker(line) {
        return (
            Line::from(vec![
                Span::styled(line[..marker].to_string(), Style::default().fg(theme.text)),
                Span::styled(
                    line[marker..].to_string(),
                    Style::default()
                        .fg(theme.error)
                        .add_modifier(Modifier::BOLD),
                ),
            ]),
            true,
        );
    }
    if error_tail {
        return (
            Line::from(Span::styled(
                line.to_string(),
                Style::default().fg(theme.text_muted),
            )),
            true,
        );
    }
    (
        Line::from(Span::styled(
            line.to_string(),
            Style::default().fg(theme.text),
        )),
        false,
    )
}

impl BottomPaneState {
    fn human_question_composer(&self) -> &ChatComposer {
        self.human_question_view()
            .map(HumanQuestionViewState::composer)
            .unwrap_or(&self.composer)
    }

    fn human_question_content_area(&self, interaction: &HumanInteractionState, area: Rect) -> Rect {
        Rect {
            height: area
                .height
                .min(self.human_question_bottom_view_height(interaction, area.width)),
            ..area
        }
    }

    pub(crate) fn bottom_view_height(&self, width: u16, theme: &ThemePalette) -> Option<u16> {
        if let Some(view) = self.view_stack.active_as::<CommandBrowserViewState>() {
            return Some(view.desired_height(width));
        }
        if let Some(view) = self.view_stack.active_as::<SkillsToggleViewState>() {
            return Some(view.desired_height(width));
        }
        if self.command_document_is_visible() {
            let view = self.view_stack.active_as::<CommandDocumentViewState>()?;
            return Some(view.desired_height(width, theme));
        }
        if let Some(selection_view) = self.view_stack.active_as::<SelectionViewState>() {
            return Some(selection_view.desired_height(width));
        }
        if let Some(picker) = self.view_stack.active_as::<OptionPickerState>() {
            return Some(picker.desired_height(width));
        }
        if let Some(interaction) = self
            .human_question_view()
            .map(HumanQuestionViewState::human)
        {
            return Some(self.human_question_bottom_view_height(interaction, width));
        }
        if let Some(overlay) = self.approval_overlay() {
            return Some(overlay.desired_height(theme, width));
        }
        None
    }

    pub(crate) fn bottom_view_cursor_position(&self, area: Rect) -> Option<(u16, u16)> {
        let view = self.human_question_view()?;
        let interaction = view.human();
        if interaction.cancel_requested {
            return None;
        }
        if !matches!(interaction.focus, HumanQuestionFocus::Notes) {
            return None;
        }
        if interaction.current_question_has_options()
            && !interaction.notes_visible_for_current_question()
        {
            return None;
        }
        let content_area = self.human_question_content_area(interaction, area);
        view.composer()
            .cursor_pos(self.human_question_notes_area(interaction, content_area))
    }

    pub(crate) fn render_bottom_view(
        &self,
        area: Rect,
        buf: &mut ratatui::buffer::Buffer,
        theme: &ThemePalette,
    ) {
        if let Some(view) = self.view_stack.active_as::<CommandBrowserViewState>() {
            view.render(area, buf, theme);
            return;
        }
        if let Some(view) = self.view_stack.active_as::<SkillsToggleViewState>() {
            view.render(area, buf, theme);
            return;
        }
        if self.command_document_is_visible() {
            if let Some(view) = self.view_stack.active_as::<CommandDocumentViewState>() {
                view.render(area, buf, theme);
                return;
            }
        }
        if let Some(selection_view) = self.view_stack.active_as::<SelectionViewState>() {
            selection_view.render(area, buf, theme);
            return;
        }
        if let Some(picker) = self.view_stack.active_as::<OptionPickerState>() {
            picker.render(area, buf, theme);
            return;
        }
        if let Some(view) = self.human_question_view() {
            let interaction = view.human();
            let content_area = self.human_question_content_area(interaction, area);
            self.render_human_question_bottom_view(
                content_area,
                buf,
                theme,
                interaction,
                view.composer(),
            );
            return;
        }
        if let Some(overlay) = self.approval_overlay() {
            // Graphite Signal: the approval panel must read as one continuous
            // surface — blank rows get the panel background too, not the raw
            // terminal background.
            let palette = theme.bottom_pane();
            let widget = ratatui::widgets::Paragraph::new(overlay.render_text_in_height(
                theme,
                area.width.max(1),
                area.height,
            ))
            .style(
                ratatui::style::Style::default()
                    .fg(theme.text)
                    .bg(palette.input_bg),
            );
            // ApprovalOverlayState already laid out and measured physical
            // rows at this exact width. Do not wrap its padded cell rows again.
            ratatui::widgets::Widget::render(widget, area, buf);
        }
    }

    pub(crate) fn human_question_bottom_view_height(
        &self,
        interaction: &HumanInteractionState,
        width: u16,
    ) -> u16 {
        if interaction.confirm_unanswered {
            return 10;
        }
        // Rounded border (2), horizontal padding (2), and deliberate blank rows
        // above/below the content (2) are measured exactly as rendered.
        let inner_width = usize::from(width.saturating_sub(4)).max(1);
        let question_lines = interaction
            .current_question()
            .map(|question| {
                human_question_body_lines(interaction, question, inner_width).len() as u16
            })
            .unwrap_or(1);
        let options_height = interaction
            .current_question()
            .filter(|question| !question.options.is_empty())
            .map(|question| {
                human_question_options_height(interaction, question, inner_width as u16)
            })
            .unwrap_or(0);
        let notes_height = if interaction.notes_visible_for_current_question() {
            self.human_question_composer().desired_height(width).max(3)
        } else {
            0
        };
        let validation_height = u16::from(interaction.current_question_show_required_error());
        let footer_height =
            human_question_footer_required_height(interaction, inner_width as u16).max(1);
        7u16.saturating_add(HUMAN_QUESTION_FOOTER_CHROME_HEIGHT)
            .saturating_add(question_lines)
            .saturating_add(validation_height)
            .saturating_add(options_height)
            .saturating_add(if notes_height > 0 {
                1 + notes_height
            } else {
                0
            })
            .saturating_add(footer_height)
            .max(10)
    }

    fn human_question_notes_area(&self, interaction: &HumanInteractionState, area: Rect) -> Rect {
        if interaction.confirm_unanswered || !interaction.notes_visible_for_current_question() {
            return Rect::new(area.x, area.y, 0, 0);
        }
        human_question_composer_area(self.human_question_layout(interaction, area).notes_area)
    }

    fn human_question_layout(
        &self,
        interaction: &HumanInteractionState,
        area: Rect,
    ) -> HumanQuestionLayout {
        let inner = inset_human_question_area(area);
        let empty = Rect::new(inner.x, inner.y, 0, 0);
        if inner.width == 0 || inner.height == 0 || area.height == 0 {
            return HumanQuestionLayout {
                progress_area: empty,
                question_area: empty,
                validation_area: empty,
                options_area: empty,
                notes_area: empty,
                footer_rule_area: empty,
                footer_area: empty,
                question_lines: Vec::new(),
                option_visible_items: 0,
            };
        }

        let footer_height = human_question_footer_required_height(interaction, inner.width)
            .max(1)
            .min(inner.height);
        let footer_y = inner
            .y
            .saturating_add(inner.height.saturating_sub(footer_height));
        let footer_area = Rect {
            x: inner.x,
            y: footer_y,
            width: inner.width,
            height: footer_height,
        };
        // Keep the shortcut footer visually separate from the question body.
        // At constrained heights decorative rows collapse first so progress,
        // the prompt, and the selected option remain usable.
        let footer_chrome_height = HUMAN_QUESTION_FOOTER_CHROME_HEIGHT.min(
            footer_y
                .saturating_sub(inner.y)
                .saturating_sub(HUMAN_QUESTION_MIN_CONTENT_HEIGHT),
        );
        let content_bottom_y = footer_y.saturating_sub(footer_chrome_height);
        let footer_rule_area = if footer_chrome_height > 0 {
            Rect {
                x: inner.x,
                y: footer_y.saturating_sub(1),
                width: inner.width,
                height: 1,
            }
        } else {
            empty
        };
        let mut y = inner.y;
        let progress_area = if y < content_bottom_y {
            let rect = Rect {
                x: inner.x,
                y,
                width: inner.width,
                height: 1,
            };
            y = y.saturating_add(1);
            rect
        } else {
            empty
        };
        // A full blank row separates metadata from the actual question.
        if y < content_bottom_y {
            y = y.saturating_add(1);
        }

        let Some(question) = interaction.current_question() else {
            return HumanQuestionLayout {
                progress_area,
                question_area: empty,
                validation_area: empty,
                options_area: empty,
                notes_area: empty,
                footer_rule_area,
                footer_area,
                question_lines: Vec::new(),
                option_visible_items: 0,
            };
        };

        let has_options = !question.options.is_empty();
        let notes_visible = interaction.notes_visible_for_current_question();
        let validation_visible = interaction.current_question_show_required_error();
        let reserved_after_prompt = 1u16
            .saturating_add(u16::from(has_options))
            .saturating_add(u16::from(notes_visible))
            .saturating_add(u16::from(validation_visible));
        let prompt_capacity = content_bottom_y
            .saturating_sub(y)
            .saturating_sub(reserved_after_prompt)
            .max(u16::from(content_bottom_y > y));
        let mut question_lines =
            human_question_body_lines(interaction, question, inner.width as usize);
        let question_count = question_lines.len().min(prompt_capacity as usize);
        let question_truncated = question_count < question_lines.len();
        question_lines.truncate(question_count);
        if question_truncated {
            if let Some(last) = question_lines.last_mut() {
                *last = ellipsize_human_question_line(last, inner.width as usize);
            }
        }

        let question_area = Rect {
            x: inner.x,
            y,
            width: inner.width,
            height: question_lines.len() as u16,
        };
        y = y.saturating_add(question_area.height);
        // The question is visually isolated from validation, choices and notes.
        if y < content_bottom_y {
            y = y.saturating_add(1);
        }
        let validation_area = if validation_visible && y < content_bottom_y {
            let rect = Rect {
                x: inner.x,
                y,
                width: inner.width,
                height: 1,
            };
            y = y.saturating_add(1);
            rect
        } else {
            empty
        };
        let mut options_area = empty;
        let mut notes_area = empty;
        let mut option_visible_items = 0usize;

        if has_options && y < content_bottom_y {
            let rows = human_question_option_rows(interaction, question);
            let mut state = ScrollState {
                selected_idx: Some(interaction.option_cursor()),
                scroll_top: 0,
            };
            let remaining = content_bottom_y.saturating_sub(y);
            let notes_reserve = if notes_visible {
                self.human_question_composer()
                    .desired_height(area.width)
                    .max(3)
                    .min(remaining.saturating_sub(1))
            } else {
                0
            };
            let available_options_height = remaining.saturating_sub(notes_reserve);
            if available_options_height > 0 {
                let full_height = measure_rows_height(&rows, &state, rows.len(), inner.width);
                let height = full_height.min(available_options_height);
                option_visible_items =
                    human_question_visible_option_items(&rows, &state, inner.width, height);
                state.ensure_visible(rows.len(), option_visible_items);
                options_area = Rect {
                    x: inner.x,
                    y,
                    width: inner.width,
                    height,
                };
                y = y.saturating_add(height);
            }
        }

        if notes_visible && y < content_bottom_y {
            // Reserve a separator row for the composer's top rule so it never
            // overwrites validation text or the final option row.
            y = y.saturating_add(1);
        }
        if notes_visible && y < content_bottom_y {
            notes_area = Rect {
                x: inner.x,
                y,
                width: inner.width,
                height: content_bottom_y.saturating_sub(y),
            };
        }

        HumanQuestionLayout {
            progress_area,
            question_area,
            validation_area,
            options_area,
            notes_area,
            footer_rule_area,
            footer_area,
            question_lines,
            option_visible_items,
        }
    }

    fn render_human_question_bottom_view(
        &self,
        area: Rect,
        buf: &mut Buffer,
        theme: &ThemePalette,
        interaction: &HumanInteractionState,
        composer: &ChatComposer,
    ) {
        let palette = theme.bottom_pane();
        let block = Block::default()
            .title(Line::from(Span::styled(
                crate::multi_agents::preview(
                    &interaction.title,
                    area.width.saturating_sub(4) as usize,
                ),
                Style::default().fg(theme.accent),
            )))
            .borders(Borders::ALL)
            .border_type(BorderType::Rounded)
            .border_style(Style::default().fg(theme.border))
            .style(Style::default().fg(palette.input_text).bg(palette.input_bg));
        ratatui::widgets::Widget::render(block, area, buf);
        if interaction.confirm_unanswered {
            self.render_human_question_unanswered_confirmation(area, buf, theme, interaction);
            return;
        }

        let layout = self.human_question_layout(interaction, area);
        if layout.footer_area.width == 0 {
            return;
        }
        let header = Line::from(Span::styled(
            human_question_progress_text(interaction, layout.progress_area.width),
            Style::default().fg(theme.text_muted),
        ));
        Paragraph::new(header).render(layout.progress_area, buf);

        if let Some(question) = interaction.current_question() {
            let answered = interaction.draft_for(&question.question_id).has_value();
            if layout.validation_area.height > 0 {
                Paragraph::new(Line::from(Span::styled(
                    "This question is required.",
                    Style::default().fg(theme.error),
                )))
                .render(layout.validation_area, buf);
            }
            let mut error_tail = false;
            for (offset, wrapped) in layout.question_lines.iter().enumerate() {
                let (line, next_error_tail) =
                    human_question_body_line(wrapped, theme, answered, error_tail);
                Paragraph::new(line).render(
                    Rect {
                        x: layout.question_area.x,
                        y: layout.question_area.y.saturating_add(offset as u16),
                        width: layout.question_area.width,
                        height: 1,
                    },
                    buf,
                );
                error_tail = next_error_tail;
            }

            if !question.options.is_empty() {
                let rows = human_question_option_rows(interaction, question);
                let mut state = ScrollState {
                    selected_idx: Some(interaction.option_cursor()),
                    scroll_top: interaction.option_scroll_top(),
                };
                state.ensure_visible(rows.len(), layout.option_visible_items);
                render_rows(
                    layout.options_area,
                    buf,
                    &rows,
                    &state,
                    layout.option_visible_items,
                    "No options",
                    theme,
                );
            }
        }

        if interaction.notes_visible_for_current_question() && layout.notes_area.height > 0 {
            let placeholder = if interaction.current_question_has_options() {
                "Add notes"
            } else if interaction
                .current_question()
                .is_some_and(|question| question.required)
            {
                "Type your answer"
            } else {
                "Type your answer (optional)"
            };
            let rule = Rect {
                x: layout.notes_area.x,
                y: layout.notes_area.y.saturating_sub(1),
                width: layout.notes_area.width,
                height: 1,
            };
            Paragraph::new(HUMAN_QUESTION_RULE_GLYPH.repeat(rule.width as usize))
                .style(Style::default().fg(theme.border).bg(palette.input_bg))
                .render(rule, buf);
            let composer_area = human_question_composer_area(layout.notes_area);
            let secret = interaction
                .current_question()
                .is_some_and(|question| question.is_secret);
            if secret {
                composer.render_with_masked_placeholder(
                    composer_area,
                    buf,
                    palette,
                    placeholder,
                    '*',
                );
            } else {
                composer.render_with_placeholder(composer_area, buf, palette, placeholder);
            }
        }

        if layout.footer_rule_area.height > 0 {
            Paragraph::new(
                HUMAN_QUESTION_RULE_GLYPH.repeat(layout.footer_rule_area.width as usize),
            )
            .style(Style::default().fg(theme.border).bg(palette.input_bg))
            .render(layout.footer_rule_area, buf);
        }

        for (offset, footer) in human_question_footer_lines(interaction, theme, &layout)
            .into_iter()
            .take(layout.footer_area.height as usize)
            .enumerate()
        {
            Paragraph::new(footer).render(
                Rect {
                    x: layout.footer_area.x,
                    y: layout.footer_area.y.saturating_add(offset as u16),
                    width: layout.footer_area.width,
                    height: 1,
                },
                buf,
            );
        }
    }

    fn render_human_question_unanswered_confirmation(
        &self,
        area: Rect,
        buf: &mut Buffer,
        theme: &ThemePalette,
        interaction: &HumanInteractionState,
    ) {
        let inner = inset_human_question_area(area);
        if inner.width == 0 || inner.height == 0 {
            return;
        }
        let unanswered = interaction.unanswered_count();
        let mut y = inner.y;
        for line in [
            Line::from(Span::styled(
                "Submit with unanswered questions?",
                Style::default()
                    .fg(theme.text_title)
                    .add_modifier(Modifier::BOLD),
            )),
            Line::from(Span::styled(
                format!(
                    "{unanswered} unanswered question{}",
                    if unanswered == 1 { "" } else { "s" }
                ),
                Style::default().fg(theme.text_muted),
            )),
            Line::from(String::new()),
        ] {
            Paragraph::new(line).render(
                Rect {
                    x: inner.x,
                    y,
                    width: inner.width,
                    height: 1,
                },
                buf,
            );
            y = y.saturating_add(1);
        }
        let selected = interaction.confirm_unanswered_selection.min(1);
        let rows = vec![
            DisplayRow {
                name: format!("{} 1. Proceed", if selected == 0 { "›" } else { " " }),
                description: format!("Submit with {unanswered} unanswered questions."),
                match_indices: None,
                selection_index: Some(0),
                is_section_header: false,
            },
            DisplayRow {
                name: format!("{} 2. Go back", if selected == 1 { "›" } else { " " }),
                description: "Return to the first unanswered question.".to_string(),
                match_indices: None,
                selection_index: Some(1),
                is_section_header: false,
            },
        ];
        let state = ScrollState {
            selected_idx: Some(selected.min(1)),
            scroll_top: 0,
        };
        render_rows(
            Rect {
                x: inner.x,
                y,
                width: inner.width,
                height: 2,
            },
            buf,
            &rows,
            &state,
            rows.len(),
            "No choices",
            theme,
        );
        Paragraph::new(Line::from(Span::styled(
            "Press enter to confirm or esc to go back",
            Style::default().fg(theme.text_muted),
        )))
        .render(
            Rect {
                x: inner.x,
                y: area.y.saturating_add(area.height.saturating_sub(1)),
                width: inner.width,
                height: 1,
            },
            buf,
        );
    }
}
