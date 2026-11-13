#[cfg(test)]
mod agent_provenance_tests;
pub(crate) mod audit_region;
#[cfg(test)]
mod audit_region_tests;
mod base;
mod diff_render;
mod line_background;
mod messages;
mod request_user_input;
#[cfg(test)]
mod review_background_tests;
mod review_cycle;
mod review_feedback;
mod session;
mod typed_thread_item;
mod workflow_boundary;

use std::any::Any;

use ratatui::style::{Modifier, Style};
use ratatui::text::Line;
use ratatui::text::Span;
use ratatui::text::Text;
use ratatui::widgets::{Paragraph, Wrap};

use crate::terminal_hyperlinks::{
    annotate_web_urls, plain_hyperlink_lines, prefix_hyperlink_lines, visible_lines, HyperlinkLine,
};
use crate::theme::ThemePalette;

pub(crate) use diff_render::PatchHistoryCell;
pub(crate) use line_background::render_styled_lines;
#[cfg(test)]
pub(crate) use messages::render_agent_live_response_with_cwd;
#[cfg(test)]
pub(crate) use messages::render_agent_response;
pub(crate) use messages::{render_agent_response_with_cwd, render_user_prompt};
pub(crate) use request_user_input::{
    render_clarification_result, render_permission_result, ClarificationAnswerSummary,
};
#[cfg(test)]
pub(crate) use review_cycle::render_review_cycle_card;
pub(crate) use session::{
    render_session_surface, SessionSurface, WelcomeHistoryCell, WelcomeSurface,
};
pub(crate) use typed_thread_item::TypedThreadItemHistoryCell;
pub(crate) use workflow_boundary::{
    render_mode_banner, render_router_navigation_banner, render_stage_chapter_banner,
    render_stage_outcome_banner, render_workflow_summary_banner, RouterBannerModel,
    StageBannerModel, StageOutcomeBannerModel, WorkflowSummaryBannerModel,
};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum HistoryCellSpacingKind {
    User,
    TaskHeader,
    LlmLifecycle,
    FinalSeparator,
    ClarificationResult,
    PermissionResult,
    Other,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub(crate) enum HistoryRenderMode {
    #[default]
    Rich,
    Raw,
}

pub(crate) fn raw_lines_from_source(source: &str) -> Vec<Line<'static>> {
    if source.is_empty() {
        return Vec::new();
    }

    let mut parts = source.split('\n').collect::<Vec<_>>();
    if source.ends_with('\n') {
        parts.pop();
    }
    parts
        .into_iter()
        .map(|line| Line::from(line.to_string()))
        .collect()
}

fn prepend_decorative_rail(line: &mut Line<'static>, color: ratatui::style::Color) {
    // The rail is visual decoration only; source-backed text remains available
    // through `raw_lines()`/`raw_text()` and is never reconstructed from it.
    line.spans.insert(0, Span::raw(" "));
    line.spans
        .insert(0, Span::styled("▏", Style::default().fg(color)));
}

pub(crate) fn lines_with_decorative_rail(
    lines: Vec<Line<'static>>,
    color: ratatui::style::Color,
) -> Vec<Line<'static>> {
    lines
        .into_iter()
        .map(|mut line| {
            prepend_decorative_rail(&mut line, color);
            line
        })
        .collect()
}

pub(crate) trait HistoryCell: std::fmt::Debug + Send + Sync + Any {
    fn audit_context(&self) -> Option<&crate::display_item::AuditContext> {
        None
    }
    fn audit_complete(&self) -> bool {
        false
    }
    fn audit_terminal_label(&self) -> Option<&'static str> {
        self.audit_complete().then_some("Review complete")
    }
    fn audit_boundary(&self) -> Option<&'static str> {
        None
    }
    fn display_lines(&self, width: usize) -> Vec<Line<'static>>;

    fn effective_render_mode(&self, requested: HistoryRenderMode) -> HistoryRenderMode {
        requested
    }

    /// Optional color for the thin left rail used by rich terminal views.
    fn edge_color(&self) -> Option<ratatui::style::Color> {
        None
    }

    /// Rich display lines with a one-cell glyph rail plus one-cell inset.
    #[allow(dead_code)]
    fn display_lines_edged(&self, width: usize) -> Vec<Line<'static>> {
        let Some(color) = self.edge_color() else {
            return self.display_lines(width);
        };
        let inner = width.saturating_sub(2).max(1);
        lines_with_decorative_rail(self.display_lines(inner), color)
    }

    /// Transcript display lines with the rich rail applied (respects per-cell
    /// transcript overrides; used by transcript rebuild/reflow paths).
    fn transcript_lines_edged(&self, width: usize) -> Vec<Line<'static>> {
        let Some(color) = self.edge_color() else {
            return self.transcript_lines(width);
        };
        let inner = width.saturating_sub(2).max(1);
        lines_with_decorative_rail(self.transcript_lines(inner), color)
    }

    /// Rich hyperlink-aware lines with the same visual rail used by physical
    /// terminal scrollback.
    fn display_hyperlink_lines_edged(&self, width: usize) -> Vec<HyperlinkLine> {
        let Some(color) = self.edge_color() else {
            return self.display_hyperlink_lines(width);
        };
        let inner = width.saturating_sub(2).max(1);
        prefix_hyperlink_lines(
            self.display_hyperlink_lines(inner),
            Span::styled("▏ ", Style::default().fg(color)),
            Span::styled("▏ ", Style::default().fg(color)),
        )
    }

    #[allow(dead_code)]
    fn desired_height(&self, width: usize) -> u16 {
        self.desired_height_for_mode(width, HistoryRenderMode::Rich)
    }

    #[allow(dead_code)]
    fn desired_height_for_mode(&self, width: usize, mode: HistoryRenderMode) -> u16 {
        measured_line_count(self.display_lines_for_mode(width, mode), width)
    }

    fn transcript_lines(&self, width: usize) -> Vec<Line<'static>> {
        self.display_lines(width)
    }

    #[allow(dead_code)]
    fn transcript_hyperlink_lines(&self, width: usize) -> Vec<HyperlinkLine> {
        plain_hyperlink_lines(self.transcript_lines(width))
    }

    fn raw_lines(&self) -> Vec<Line<'static>>;

    #[allow(dead_code)]
    fn display_lines_for_mode(&self, width: usize, mode: HistoryRenderMode) -> Vec<Line<'static>> {
        match self.effective_render_mode(mode) {
            HistoryRenderMode::Rich => visible_lines(self.display_hyperlink_lines(width)),
            HistoryRenderMode::Raw => self.raw_lines(),
        }
    }

    fn display_hyperlink_lines(&self, width: usize) -> Vec<HyperlinkLine> {
        annotate_web_urls(self.display_lines(width))
    }

    #[allow(dead_code)]
    fn display_hyperlink_lines_for_mode(
        &self,
        width: usize,
        mode: HistoryRenderMode,
    ) -> Vec<HyperlinkLine> {
        match self.effective_render_mode(mode) {
            HistoryRenderMode::Rich => self.display_hyperlink_lines(width),
            HistoryRenderMode::Raw => plain_hyperlink_lines(self.raw_lines()),
        }
    }

    #[allow(dead_code)]
    fn raw_text(&self) -> Option<String> {
        let text = lines_to_plain_text(self.raw_lines());
        (!text.trim().is_empty()).then_some(text)
    }

    #[allow(dead_code)]
    fn desired_transcript_height(&self, width: usize) -> u16 {
        let lines = self.transcript_lines(width);
        if let [line] = &lines[..] {
            if line
                .spans
                .iter()
                .all(|span| span.content.chars().all(char::is_whitespace))
            {
                return 1;
            }
        }
        measured_line_count(lines, width)
    }

    #[allow(dead_code)]
    fn copy_text(&self) -> Option<String> {
        self.raw_text()
    }

    #[allow(dead_code)]
    fn is_stream_continuation(&self) -> bool {
        false
    }

    #[allow(dead_code)]
    fn transcript_animation_tick(&self) -> Option<u64> {
        None
    }

    fn is_scrollback_history_cell(&self) -> bool {
        true
    }

    fn spacing_kind(&self) -> HistoryCellSpacingKind {
        HistoryCellSpacingKind::Other
    }

    fn as_any(&self) -> &dyn Any;
}

#[allow(dead_code)]
fn measured_line_count(lines: Vec<Line<'static>>, width: usize) -> u16 {
    let width = width.max(1);
    Paragraph::new(Text::from(lines))
        .wrap(Wrap { trim: false })
        .line_count(width.try_into().unwrap_or(u16::MAX))
        .try_into()
        .unwrap_or(u16::MAX)
}

#[cfg(test)]
fn line_plain_text(line: &Line<'_>) -> String {
    line.spans
        .iter()
        .map(|span| span.content.as_ref())
        .collect::<String>()
}

#[allow(dead_code)]
fn lines_to_plain_text(lines: Vec<Line<'static>>) -> String {
    lines
        .into_iter()
        .map(|line| {
            line.spans
                .into_iter()
                .map(|span| span.content.into_owned())
                .collect::<String>()
        })
        .collect::<Vec<_>>()
        .join("\n")
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct AgentMarkdownHistoryCell {
    markdown: String,
    citation_sources: crate::citation_sources::CitationSources,
    theme: ThemePalette,
    cwd: Option<String>,
    agent_context: Option<crate::agent_provenance::AgentMessageContext>,
    audit: Option<crate::display_item::AuditContext>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct ReasoningRawHistoryCell {
    content: String,
    theme: ThemePalette,
}

impl ReasoningRawHistoryCell {
    pub(crate) fn new(content: String, theme: ThemePalette) -> Self {
        Self { content, theme }
    }
}

fn render_reasoning_summary(
    summary: &str,
    label: &str,
    width: usize,
    theme: &ThemePalette,
    max_body_lines: Option<usize>,
) -> Vec<Line<'static>> {
    if summary.trim().is_empty() {
        return Vec::new();
    }
    let marker_style = crate::style::activity_marker_style("completed", theme);
    let content_width = width.saturating_sub(4).max(1);
    let content_lines = crate::markdown::render_markdown(summary, content_width, theme)
        .into_iter()
        .filter(|line| {
            !line
                .spans
                .iter()
                .all(|span| span.content.as_ref().trim().is_empty())
        })
        .map(|line| {
            Line::from(
                line.spans
                    .into_iter()
                    .map(|span| {
                        let mut style = span.style.fg(theme.text_muted);
                        style.add_modifier.remove(Modifier::BOLD);
                        style = style.remove_modifier(Modifier::BOLD);
                        Span::styled(span.content.into_owned(), style)
                    })
                    .collect::<Vec<_>>(),
            )
        })
        .collect::<Vec<_>>();

    if content_lines.is_empty() {
        return vec![Line::from(vec![
            Span::styled(crate::style::GLYPH_AGENT_BULLET, marker_style),
            Span::styled(label.to_string(), Style::default().fg(theme.text_title)),
        ])];
    }

    let thinking_label = || {
        vec![
            Span::styled(crate::style::GLYPH_AGENT_BULLET, marker_style),
            Span::styled(
                label.to_string(),
                Style::default()
                    .fg(theme.text_title)
                    .add_modifier(Modifier::BOLD),
            ),
        ]
    };
    if content_lines.len() == 1 && max_body_lines.is_none_or(|limit| limit >= 1) {
        let mut spans = thinking_label();
        spans.push(Span::styled(" • ", Style::default().fg(theme.text_muted)));
        spans.extend(content_lines[0].spans.clone());
        let inline = Line::from(spans);
        if inline.width() <= width.max(1) {
            return vec![inline];
        }
    }

    let omitted = max_body_lines
        .map(|limit| content_lines.len().saturating_sub(limit))
        .unwrap_or(0);
    let visible_count = max_body_lines
        .map(|limit| content_lines.len().min(limit))
        .unwrap_or(content_lines.len());
    let mut rendered = vec![Line::from(thinking_label())];
    for (index, line) in content_lines.into_iter().take(visible_count).enumerate() {
        let prefix = if omitted == 0 && index + 1 == visible_count {
            "  └ "
        } else {
            "  ├ "
        };
        let mut spans = Vec::with_capacity(line.spans.len() + 1);
        spans.push(Span::styled(prefix, Style::default().fg(theme.text_muted)));
        spans.extend(line.spans);
        rendered.push(Line::from(spans));
    }
    if omitted > 0 {
        rendered.push(Line::from(vec![
            Span::styled("  └ ", Style::default().fg(theme.text_muted)),
            Span::styled(
                format!("… {omitted} more"),
                Style::default().fg(theme.text_muted),
            ),
        ]));
    }
    rendered
}

fn title_case_component(value: &str) -> String {
    value
        .split(['_', '-', ' '])
        .filter(|part| !part.is_empty())
        .map(|part| {
            let mut chars = part.chars();
            chars
                .next()
                .map(|first| first.to_uppercase().collect::<String>() + chars.as_str())
                .unwrap_or_default()
        })
        .collect::<Vec<_>>()
        .join(" ")
}

impl HistoryCell for ReasoningRawHistoryCell {
    fn display_lines(&self, width: usize) -> Vec<Line<'static>> {
        if self.content.trim().is_empty() {
            return Vec::new();
        }
        let content_width = width.saturating_sub(2).max(1);
        crate::markdown::render_markdown(&self.content, content_width, &self.theme)
            .into_iter()
            .map(|line| {
                let mut spans = Vec::with_capacity(line.spans.len() + 1);
                spans.push(Span::raw("  "));
                spans.extend(line.spans.into_iter().map(|span| {
                    Span::styled(
                        span.content.into_owned(),
                        span.style
                            .fg(self.theme.text_muted)
                            .add_modifier(Modifier::DIM),
                    )
                }));
                Line::from(spans)
            })
            .collect()
    }

    fn transcript_lines(&self, width: usize) -> Vec<Line<'static>> {
        self.display_lines(width)
    }

    fn raw_lines(&self) -> Vec<Line<'static>> {
        self.content
            .lines()
            .map(|line| Line::from(line.to_string()))
            .collect()
    }

    fn as_any(&self) -> &dyn Any {
        self
    }
}

impl AgentMarkdownHistoryCell {
    pub(crate) fn with_audit_context(
        mut self,
        audit: Option<crate::display_item::AuditContext>,
    ) -> Self {
        self.audit = audit;
        self
    }

    pub(crate) fn new(markdown: String, theme: ThemePalette) -> Self {
        Self {
            markdown,
            citation_sources: Default::default(),
            theme,
            cwd: None,
            agent_context: None,
            audit: None,
        }
    }

    pub(crate) fn with_cwd(mut self, cwd: Option<String>) -> Self {
        self.cwd = cwd;
        self
    }

    pub(crate) fn with_citation_sources(
        mut self,
        sources: crate::citation_sources::CitationSources,
    ) -> Self {
        self.citation_sources = sources;
        self
    }

    pub(crate) fn with_agent_context(
        mut self,
        context: Option<crate::agent_provenance::AgentMessageContext>,
    ) -> Self {
        self.agent_context = context.filter(|context| {
            !context.component.trim().is_empty() && !context.component.eq_ignore_ascii_case("core")
        });
        self
    }

    fn provenance_label(&self) -> Option<String> {
        let context = self.agent_context.as_ref()?;
        let mut parts = vec![title_case_component(&context.component)];
        if !context.stage.trim().is_empty() {
            parts.push(context.stage.replace('_', " "));
        } else if !context.operation.trim().is_empty() {
            parts.push(context.operation.replace('_', " "));
        }
        if context.attempt > 0 {
            parts.push(format!("attempt {}", context.attempt));
        }
        if !context.round_id.trim().is_empty() {
            parts.push(context.round_id.clone());
        }
        Some(parts.join(" · "))
    }
}

/// Untyped cells cover user input, elapsed time and frontend-only notices.
/// Runtime activity, errors and interaction receipts use TypedThreadItemHistoryCell.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum BasicHistoryCellKind {
    User,
    FinalSeparator,
    FrontendNotice,
    /// Minimal fixture for exercising default HistoryCell trait behavior.
    #[cfg(test)]
    Plain,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct BasicHistoryCell {
    kind: BasicHistoryCellKind,
    text: String,
    theme: ThemePalette,
}

impl BasicHistoryCell {
    pub(crate) fn new(kind: BasicHistoryCellKind, text: String, theme: ThemePalette) -> Self {
        Self { kind, text, theme }
    }

    #[cfg(test)]
    pub(crate) fn text(&self) -> &str {
        &self.text
    }
}

impl HistoryCell for BasicHistoryCell {
    fn display_lines(&self, width: usize) -> Vec<Line<'static>> {
        match self.kind {
            BasicHistoryCellKind::User => render_user_prompt(&self.text, width, &self.theme),
            BasicHistoryCellKind::FinalSeparator => {
                render_final_separator(&self.text, width, &self.theme)
            }
            BasicHistoryCellKind::FrontendNotice => {
                messages::render_plain(&self.text, width, &self.theme)
            }
            #[cfg(test)]
            BasicHistoryCellKind::Plain => messages::render_plain(&self.text, width, &self.theme),
        }
    }

    fn edge_color(&self) -> Option<ratatui::style::Color> {
        matches!(self.kind, BasicHistoryCellKind::User).then_some(self.theme.accent)
    }

    fn raw_lines(&self) -> Vec<Line<'static>> {
        if self.text.trim().is_empty() {
            self.display_lines(usize::MAX / 4)
        } else {
            raw_lines_from_source(&self.text)
        }
    }

    fn raw_text(&self) -> Option<String> {
        #[cfg(test)]
        if self.kind == BasicHistoryCellKind::Plain {
            let text = lines_to_plain_text(self.raw_lines());
            return (!text.trim().is_empty()).then_some(text);
        }
        None
    }

    fn copy_text(&self) -> Option<String> {
        self.raw_text()
    }

    fn spacing_kind(&self) -> HistoryCellSpacingKind {
        match self.kind {
            BasicHistoryCellKind::User => HistoryCellSpacingKind::User,
            BasicHistoryCellKind::FinalSeparator => HistoryCellSpacingKind::FinalSeparator,
            BasicHistoryCellKind::FrontendNotice => HistoryCellSpacingKind::Other,
            #[cfg(test)]
            BasicHistoryCellKind::Plain => HistoryCellSpacingKind::Other,
        }
    }

    fn as_any(&self) -> &dyn Any {
        self
    }
}

fn render_final_separator(
    elapsed_label: &str,
    width: usize,
    theme: &ThemePalette,
) -> Vec<Line<'static>> {
    vec![
        Line::from(String::new()),
        Line::from(Span::styled(
            crate::style::worked_for_line(elapsed_label, width),
            Style::default().fg(theme.border),
        )),
    ]
}

impl AgentMarkdownHistoryCell {
    fn render_citations(&self, width: usize, details: bool) -> Vec<HyperlinkLine> {
        let mut citations =
            crate::citation_sources::CitationDocument::new(self.citation_sources.clone());
        let body = citations.body(&self.markdown);
        let mut lines = messages::render_agent_markdown_hyperlinks(
            &body,
            width,
            &self.theme,
            self.cwd.as_deref(),
            crate::style::activity_marker_style("completed", &self.theme),
        );
        let references = citations.references();
        if !references.is_empty() {
            lines.push(HyperlinkLine::default());
            lines.extend(
                crate::markdown::render_markdown_hyperlinks(
                    &references,
                    width.saturating_sub(2).max(1),
                    &self.theme,
                    self.cwd.as_deref(),
                )
                .into_iter()
                .map(|line| {
                    if line
                        .line
                        .spans
                        .iter()
                        .all(|span| span.content.trim().is_empty())
                    {
                        line
                    } else {
                        crate::terminal_hyperlinks::prefix_hyperlink_lines(
                            vec![line],
                            Span::raw("  "),
                            Span::raw("  "),
                        )
                        .remove(0)
                    }
                }),
            );
        }
        if details {
            let diagnostics = citations.diagnostics();
            if !diagnostics.is_empty() {
                lines.push(HyperlinkLine::default());
                lines.extend(crate::markdown::render_markdown_hyperlinks(
                    &diagnostics,
                    width.max(1),
                    &self.theme,
                    self.cwd.as_deref(),
                ));
            }
        }
        if let Some(label) = self.provenance_label().filter(|_| {
            self.audit.as_ref().is_none_or(|audit| {
                self.agent_context
                    .as_ref()
                    .is_some_and(|context| !context.component.eq_ignore_ascii_case(&audit.role))
            })
        }) {
            // The body has already been wrapped. Never insert a provenance
            // prefix into it afterwards: the terminal would wrap it again.
            let header = Line::from(Span::styled(
                label,
                Style::default().fg(self.theme.text_muted),
            ));
            let options = crate::wrapping::RtOptions::new(width.max(1))
                .initial_indent(Line::from(crate::style::GLYPH_AGENT_BULLET))
                .subsequent_indent(Line::from("  "));
            let labeled: Vec<Line<'static>> = crate::wrapping::word_wrap_line(&header, options)
                .into_iter()
                .map(|line| {
                    Line::from(
                        line.spans
                            .into_iter()
                            .map(|span| Span::styled(span.content.into_owned(), span.style))
                            .collect::<Vec<_>>(),
                    )
                })
                .collect();
            let mut labeled = plain_hyperlink_lines(labeled);
            if let Some(marker) = lines
                .iter_mut()
                .flat_map(|line| line.line.spans.iter_mut())
                .find(|span| span.content == crate::style::GLYPH_AGENT_BULLET)
            {
                marker.content = "  ".into();
            }
            labeled.extend(lines);
            return labeled;
        }
        lines
    }
}

impl HistoryCell for AgentMarkdownHistoryCell {
    fn audit_context(&self) -> Option<&crate::display_item::AuditContext> {
        self.audit.as_ref()
    }

    fn display_lines(&self, width: usize) -> Vec<Line<'static>> {
        visible_lines(self.display_hyperlink_lines(width))
    }

    fn display_hyperlink_lines(&self, width: usize) -> Vec<HyperlinkLine> {
        self.render_citations(width, false)
    }

    fn transcript_hyperlink_lines(&self, width: usize) -> Vec<HyperlinkLine> {
        self.render_citations(width, true)
    }

    fn transcript_lines(&self, width: usize) -> Vec<Line<'static>> {
        visible_lines(self.transcript_hyperlink_lines(width))
    }

    fn copy_text(&self) -> Option<String> {
        (!self.markdown.trim().is_empty()).then(|| self.markdown.clone())
    }

    fn raw_lines(&self) -> Vec<Line<'static>> {
        raw_lines_from_source(&self.markdown)
    }

    fn as_any(&self) -> &dyn Any {
        self
    }
}

pub(crate) fn history_cells_require_inter_cell_spacing(
    previous: &dyn HistoryCell,
    current: &dyn HistoryCell,
) -> bool {
    if previous.audit_context().is_some() && current.audit_boundary().is_some() {
        return false;
    }
    if current.audit_context().is_some() && previous.audit_context() == current.audit_context() {
        return false;
    }
    match (previous.spacing_kind(), current.spacing_kind()) {
        (HistoryCellSpacingKind::User, HistoryCellSpacingKind::TaskHeader) => true,
        (HistoryCellSpacingKind::User, _)
        | (_, HistoryCellSpacingKind::User)
        | (_, HistoryCellSpacingKind::FinalSeparator) => false,
        _ => true,
    }
}

#[cfg(test)]
pub(crate) fn collapse_consecutive_blank_lines(lines: Vec<Line<'static>>) -> Vec<Line<'static>> {
    let mut collapsed = Vec::with_capacity(lines.len());
    let mut previous_blank = false;
    let mut previous_styled_blank = false;
    for line in lines {
        let blank = line_is_blank(&line);
        let styled_blank = blank && line_has_background(&line);
        if blank && previous_blank && !styled_blank && !previous_styled_blank {
            continue;
        }
        previous_blank = blank;
        previous_styled_blank = styled_blank;
        collapsed.push(line);
    }
    collapsed
}

#[cfg(test)]
fn line_is_blank(line: &Line<'_>) -> bool {
    line.spans
        .iter()
        .all(|span| span.content.as_ref().trim().is_empty())
}

#[cfg(test)]
fn line_has_background(line: &Line<'_>) -> bool {
    line.style.bg.is_some() || line.spans.iter().any(|span| span.style.bg.is_some())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn text_lines(lines: Vec<Line<'static>>) -> Vec<String> {
        lines
            .into_iter()
            .map(|line| {
                line.spans
                    .into_iter()
                    .map(|span| span.content.into_owned())
                    .collect::<String>()
            })
            .collect()
    }

    #[test]
    fn agent_history_cell_keeps_raw_text_separate_from_display_lines() {
        let cell = AgentMarkdownHistoryCell::new("你好".to_string(), ThemePalette::default());

        let display = text_lines(cell.display_lines(80));

        assert_eq!(cell.raw_text().as_deref(), Some("你好"));
        assert_eq!(cell.copy_text().as_deref(), Some("你好"));
        assert!(display.iter().any(|line| line == "⦁ 你好"));
    }

    #[test]
    fn agent_history_cell_labels_non_core_component_from_structured_context() {
        let cell =
            AgentMarkdownHistoryCell::new("正在核对证据。".to_string(), ThemePalette::default())
                .with_agent_context(Some(crate::agent_provenance::AgentMessageContext {
                    stage: "literature_grounding".to_string(),
                    attempt: 1,
                    component: "reviewer".to_string(),
                    operation: "reviewer_round".to_string(),
                    round_id: "reviewer-round-001".to_string(),
                }));

        let display = text_lines(cell.display_lines(100)).join("\n");

        assert!(display.contains("Reviewer · literature grounding · attempt 1"));
        assert!(display.contains("reviewer-round-001"));
        assert!(display.contains("正在核对证据。"));
        assert_eq!(cell.copy_text().as_deref(), Some("正在核对证据。"));
    }

    #[test]
    fn agent_history_cell_reflows_mixed_cjk_ascii_without_changing_source() {
        let source = "在一个隔离的 direct_qa 阶段适合回答清晰直接的问题并继续执行";
        let cell = AgentMarkdownHistoryCell::new(source.to_string(), ThemePalette::default());

        let display = text_lines(cell.display_lines(32));

        assert_eq!(display[0], "⦁ 在一个隔离的 direct_qa 阶段适");
        assert_eq!(cell.raw_text().as_deref(), Some(source));
        assert_eq!(cell.copy_text().as_deref(), Some(source));
    }

    #[test]
    fn agent_history_cell_splits_multiline_raw_lines() {
        let source = "hello\nworld";
        let cell = AgentMarkdownHistoryCell::new(source.to_string(), ThemePalette::default());

        assert_eq!(text_lines(cell.raw_lines()), vec!["hello", "world"]);
        assert_eq!(cell.raw_text().as_deref(), Some(source));
    }

    #[test]
    fn history_cell_trait_default_measurement_methods_are_exercised() {
        let cell = BasicHistoryCell::new(
            BasicHistoryCellKind::Plain,
            "abcd efgh".to_string(),
            ThemePalette::default(),
        );

        assert!(cell.desired_height(4) >= 2);
        assert!(cell.desired_transcript_height(4) >= 2);
        assert_eq!(text_lines(cell.raw_lines()), vec!["abcd efgh"]);
        assert_eq!(cell.display_hyperlink_lines(80)[0].hyperlinks.len(), 0);
        assert_eq!(cell.transcript_hyperlink_lines(80)[0].hyperlinks.len(), 0);
        assert!(!cell.is_stream_continuation());
        assert_eq!(cell.transcript_animation_tick(), None);
    }

    #[test]
    fn user_history_cell_uses_a_one_cell_visual_rail() {
        let cell = BasicHistoryCell::new(
            BasicHistoryCellKind::User,
            "原始输入\n▎ 用户自己的符号".to_string(),
            ThemePalette::default(),
        );

        let plain = cell.display_lines(80);
        let edged = cell.display_lines_edged(80);
        assert_eq!(edged.len(), plain.len());
        for line in &edged {
            assert_eq!(line.spans[0].content.as_ref(), "▏");
            assert_eq!(line.spans[0].style.fg, Some(cell.theme.accent));
            assert_eq!(line.spans[1].content.as_ref(), " ");
        }
        assert_eq!(
            text_lines(cell.raw_lines()),
            vec!["原始输入", "▎ 用户自己的符号"]
        );
        assert_eq!(cell.text(), "原始输入\n▎ 用户自己的符号");
    }

    #[test]
    fn history_cell_preserves_url_hyperlink_metadata_for_display_and_transcript() {
        let cell = BasicHistoryCell::new(
            BasicHistoryCellKind::Plain,
            "See https://example.com/path?q=1 for details".to_string(),
            ThemePalette::default(),
        );

        assert_eq!(
            cell.display_hyperlink_lines(80)[0].hyperlinks[0].destination,
            "https://example.com/path?q=1"
        );
        assert!(cell.raw_text().is_some());
        assert_eq!(cell.transcript_hyperlink_lines(80)[0].hyperlinks.len(), 0);
    }

    #[test]
    fn history_cell_handles_cjk_table_and_long_tokens_without_deriving_copy_from_display() {
        let source = "| 列一 | column |\n| --- | --- |\n| 你好世界 | superlongtokenwithoutbreak |";
        let cell = AgentMarkdownHistoryCell::new(source.to_string(), ThemePalette::default());

        assert_eq!(cell.copy_text().as_deref(), Some(source));
        assert_eq!(cell.raw_text().as_deref(), Some(source));
        assert!(cell.desired_height(8) >= 3);
        assert!(cell
            .display_lines(12)
            .iter()
            .any(|line| line_plain_text(line).contains("你好")));
    }
}
