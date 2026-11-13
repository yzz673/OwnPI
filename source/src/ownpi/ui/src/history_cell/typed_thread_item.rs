use ratatui::style::{Modifier, Style, Stylize};
use ratatui::text::{Line, Span};

use super::review_cycle::display_review_path;
use super::{HistoryCell, HistoryCellSpacingKind};
use crate::display_item::{
    ActionRequiredPayload, ArtifactActivityPayload, BatchOutcomePayload, CommandExecutionPayload,
    DisplayItem, DisplayItemCommon, InteractionResultPayload, ModelActivityPayload,
    PermissionActivityPayload, PlanPayload, PlanStepStatus, ProtocolActivityPayload,
    ReviewCyclePayload, RouteDecisionPayload, StagePayload, TaskHeaderPayload, TaskOutcomePayload,
};
use crate::theme::ThemePalette;
use crate::wrapping::{adaptive_wrap_line, line_to_static, RtOptions};

#[derive(Clone, Debug)]
struct ExplorationEntry {
    item: DisplayItem,
    item_ids: Vec<String>,
    occurrences: usize,
    failed_occurrences: usize,
    deferred_occurrences: usize,
    error_summaries: Vec<String>,
    transcripts: Vec<String>,
    full_read_occurrences: usize,
    explicit_page_ranges: Vec<(u32, u32)>,
    document_page_count: Option<u32>,
    read_windows: Vec<crate::display_item::ReadWindowPayload>,
}

#[derive(Clone, Debug)]
pub(crate) struct TypedThreadItemHistoryCell {
    show_raw_reasoning: bool,
    citation_sources: crate::citation_sources::CitationSources,
    cwd: Option<String>,
    item: DisplayItem,
    theme: ThemePalette,
    exploration: Vec<ExplorationEntry>,
    stage_appends: Vec<DisplayItem>,
    permission_activities: Vec<DisplayItem>,
    resource_occurrences: usize,
    resource_transcripts: Vec<String>,
}

impl TypedThreadItemHistoryCell {
    pub(crate) fn with_citation_sources(
        mut self,
        sources: crate::citation_sources::CitationSources,
    ) -> Self {
        self.citation_sources = sources;
        self
    }

    pub(crate) fn with_cwd(mut self, cwd: Option<String>) -> Self {
        self.cwd = cwd;
        self
    }

    fn patch_cell(&self) -> Option<super::PatchHistoryCell> {
        let DisplayItem::FileChange(common, payload) = &self.item else {
            return None;
        };
        Some(
            super::PatchHistoryCell::new(crate::file_change::FileChangeCell {
                event_id: common.item_id.clone(),
                tool_name: common.tool_name.clone(),
                status: common.status.clone(),
                cwd: payload.cwd.clone(),
                changes: payload.changes.clone(),
                stdout: Some(payload.stdout.clone()),
                stderr: Some(payload.stderr.clone()),
                error: Some(payload.error.clone()),
                auto_approved: None,
            })
            .with_compact_preview(payload.compact_preview),
        )
    }

    pub(crate) fn is_model_activity(&self) -> bool {
        matches!(self.item, DisplayItem::ModelActivity(_, _))
    }
    pub(crate) fn new(item: DisplayItem, theme: ThemePalette) -> Self {
        let exploration = if is_exploration_item(&item) {
            let (full_read_occurrences, explicit_page_ranges, document_page_count) =
                pdf_read_scope(&item);
            vec![ExplorationEntry {
                item_ids: vec![item.common().item_id.clone()],
                item: item.clone(),
                occurrences: 1,
                failed_occurrences: usize::from(item.common().status == "failed"),
                deferred_occurrences: usize::from(item.common().status == "incomplete"),
                error_summaries: item_error_summary(&item).into_iter().collect(),
                transcripts: nonempty_transcript(&item).into_iter().collect(),
                full_read_occurrences,
                explicit_page_ranges,
                document_page_count,
                read_windows: command_read_windows(&item),
            }]
        } else {
            Vec::new()
        };
        let is_resource = paper_resource_key_for_item(&item).is_some();
        let stage_appends = if is_stage_append_item(&item) {
            vec![item.clone()]
        } else {
            Vec::new()
        };
        let resource_transcripts = if is_resource {
            nonempty_transcript(&item).into_iter().collect()
        } else {
            Vec::new()
        };
        let permission_activities = if matches!(&item, DisplayItem::PermissionActivity(_, _)) {
            vec![item.clone()]
        } else {
            Vec::new()
        };
        Self {
            show_raw_reasoning: false,
            citation_sources: Default::default(),
            cwd: None,
            item,
            theme,
            exploration,
            stage_appends,
            permission_activities,
            resource_occurrences: usize::from(is_resource),
            resource_transcripts,
        }
    }

    pub(crate) fn merge_adjacent_display(&mut self, next: &Self) -> bool {
        if self.item.common().audit_context != next.item.common().audit_context
            || self.item.common().status != "completed"
            || next.item.common().status != "completed"
        {
            return false;
        }
        if self.is_exploration() && next.is_exploration() {
            self.exploration.extend(next.exploration.clone());
            return true;
        }
        if self.is_stage_append() && next.is_stage_append() {
            self.stage_appends.extend(next.stage_appends.clone());
            return true;
        }
        false
    }

    pub(crate) fn is_terminal_permission_activity(&self) -> bool {
        matches!(
            &self.item,
            DisplayItem::PermissionActivity(common, payload)
                if common.status != "running" && payload.lifecycle != "awaiting_approval"
        )
    }

    /// The semantic execution batch closed by this terminal tool item.

    /// Batch metadata is independent of whether two tools share a renderer.

    /// One execution owns one history cell. Enrichment of its evidence is
    /// not a second execution; contradictory conclusions require an explicit
    /// Bridge correction with its own identity.

    pub(crate) fn is_stage_append(&self) -> bool {
        !self.stage_appends.is_empty()
    }

    fn finalized_workflow_stages(&self) -> Option<Vec<String>> {
        let DisplayItem::CommandExecution(common, payload) = &self.item else {
            return None;
        };
        if common.tool_name != "workflow_plan" || common.status != "completed" {
            return None;
        }
        let stages = payload
            .workflow_stages
            .iter()
            .map(|stage| stage.trim().to_string())
            .filter(|stage| !stage.is_empty())
            .collect::<Vec<_>>();
        if !stages.is_empty() {
            return Some(stages);
        }
        None
    }

    pub(crate) fn is_exploration(&self) -> bool {
        !self.exploration.is_empty()
    }

    pub(crate) fn live_mutable_is_terminal(&self) -> bool {
        if self.is_exploration() {
            return matches!(
                semantic_group_state_for_item(&self.item),
                Some("completed" | "partial")
            );
        }
        match &self.item {
            DisplayItem::BatchOutcome(common, _) => common.status != "running",
            DisplayItem::Reasoning(common, _) => common.status != "running",
            DisplayItem::ReviewCycle(common, payload) => {
                common.status != "running"
                    && (matches!(
                        payload.phase.as_str(),
                        "accepted"
                            | "completed"
                            | "feedback_received"
                            | "residual"
                            | "failed"
                            | "cancelled"
                            | "interrupted"
                    ) || matches!(
                        payload.outcome.as_str(),
                        "accepted"
                            | "completed"
                            | "findings"
                            | "no_findings"
                            | "residual_findings"
                            | "failed"
                    ))
            }
            DisplayItem::ProtocolActivity(common, _) => common.status != "running",
            // Awaiting requests are mutable overlays; resolved decisions are
            // committed immediately as chronological receipts.
            DisplayItem::PermissionActivity(_, _) => self.is_terminal_permission_activity(),
            DisplayItem::Stage(common, payload) => {
                common.status != "running"
                    && matches!(
                        payload.event.as_str(),
                        "completed" | "failed" | "paused" | "blocked" | "cancelled"
                    )
            }
            DisplayItem::CommandExecution(common, _) if !self.is_exploration() => {
                common.status != "running" && !self.is_stage_append()
            }
            DisplayItem::FileChange(common, _)
            | DisplayItem::McpToolCall(common, _)
            | DisplayItem::WebSearch(common, _)
            | DisplayItem::ImageView(common, _)
            | DisplayItem::ImageGeneration(common, _)
            | DisplayItem::BackgroundTerminalInteraction(common, _) => common.status != "running",
            DisplayItem::RouteDecision(common, _) => common.status != "running",
            DisplayItem::PlanUpdate(common, _) => common.status != "running",
            _ => false,
        }
    }

    /// Whether this update is a history boundary for independently settled
    /// live cells. Failed exploration calls are terminal even when their
    /// producer omitted a semantic group state; legacy review/stage cells may
    /// still receive a same-id replacement before the next real boundary.

    pub(crate) fn is_terminal_review_cycle(&self) -> bool {
        matches!(self.item, DisplayItem::ReviewCycle(_, _)) && self.live_mutable_is_terminal()
    }

    /// Whether this live cell still owns an in-flight mutable update.
    ///
    /// Live history also contains settled aggregation cells and stage banners,
    /// so `is_live_mutable` is intentionally broader than this predicate.  A
    /// narration or an unrelated tool completion may seal settled cells, but
    /// must never freeze a running command into physical scrollback.

    /// Whether a completed cell still owns an open display aggregation.
    /// Group state must never be confused with an individual call lifecycle:
    /// only mergeable, non-failed group summaries stay live.

    fn display_exploration_entries(&self) -> Vec<ExplorationEntry> {
        let mut display: Vec<ExplorationEntry> = Vec::new();
        for next in &self.exploration {
            let key = aggregation_key_for_item(&next.item);
            let existing = display.iter_mut().find(|entry| {
                key.is_some_and(|key| {
                    aggregation_key_for_item(&entry.item) == Some(key)
                        && aggregation_batches_compatible(&entry.item, &next.item)
                })
            });
            let Some(existing) = existing else {
                display.push(next.clone());
                continue;
            };
            existing.item = merge_page_ranges(&existing.item, &next.item);
            existing.occurrences = existing.occurrences.saturating_add(next.occurrences);
            existing.failed_occurrences = existing
                .failed_occurrences
                .saturating_add(next.failed_occurrences);
            existing.deferred_occurrences = existing
                .deferred_occurrences
                .saturating_add(next.deferred_occurrences);
            existing.full_read_occurrences = existing
                .full_read_occurrences
                .saturating_add(next.full_read_occurrences);
            existing.explicit_page_ranges = normalize_page_ranges(
                existing
                    .explicit_page_ranges
                    .iter()
                    .chain(next.explicit_page_ranges.iter())
                    .copied(),
            );
            existing.document_page_count = existing
                .document_page_count
                .into_iter()
                .chain(next.document_page_count)
                .max();
            existing.read_windows = normalize_read_windows(
                existing
                    .read_windows
                    .iter()
                    .chain(next.read_windows.iter())
                    .cloned(),
            );
            for error in &next.error_summaries {
                if !existing.error_summaries.contains(error) {
                    existing.error_summaries.push(error.clone());
                }
            }
        }
        display
    }

    fn exploration_all_failed(&self) -> bool {
        !self.exploration.is_empty()
            && self
                .exploration
                .iter()
                .all(|entry| entry.failed_occurrences == entry.occurrences)
    }

    fn exploration_all_deferred(&self) -> bool {
        !self.exploration.is_empty()
            && self
                .exploration
                .iter()
                .all(|entry| entry.deferred_occurrences == entry.occurrences)
    }

    fn status_failed(&self) -> bool {
        if self.is_exploration() {
            self.exploration_all_failed()
        } else {
            matches!(
                self.item.common().status.as_str(),
                "failed" | "error" | "blocked" | "cancelled" | "canceled" | "interrupted"
            )
        }
    }

    fn marker_status(&self) -> &str {
        if self.is_exploration() {
            if self.exploration_all_failed() {
                "failed"
            } else if self.exploration_all_deferred() {
                "incomplete"
            } else if self
                .exploration
                .iter()
                .any(|entry| entry.item.common().status == "running")
            {
                "running"
            } else {
                "completed"
            }
        } else {
            self.item.common().status.as_str()
        }
    }

    fn marker(&self) -> Span<'static> {
        let status = self.marker_status();
        Span::styled(
            crate::style::activity_marker(status),
            crate::style::activity_marker_style(status, &self.theme),
        )
    }

    fn header(&self, title: &str) -> Line<'static> {
        Line::from(vec![self.marker(), title.to_string().bold()])
    }

    fn prefixed_body_lines(&self, lines: Vec<Line<'static>>) -> Vec<Line<'static>> {
        lines
            .into_iter()
            .enumerate()
            .map(|(index, line)| {
                let mut spans = vec![if index == 0 {
                    "  └ ".dim()
                } else {
                    "    ".into()
                }];
                spans.extend(line.spans);
                Line::from(spans)
            })
            .collect()
    }

    fn tree_line(&self, value: impl Into<String>) -> Line<'static> {
        Line::from(vec![
            "  └ ".dim(),
            Span::styled(value.into(), Style::default().fg(self.theme.text)),
        ])
    }

    fn detail_line(&self, value: impl Into<String>) -> Line<'static> {
        Line::from(vec![
            "    ".into(),
            Span::styled(value.into(), Style::default().fg(self.theme.text_muted)),
        ])
    }

    fn error_body_lines(&self, value: &str, width: usize) -> Vec<Line<'static>> {
        if value.trim().is_empty() {
            return Vec::new();
        }
        let line = Line::from(vec![
            "Error ".red().bold(),
            Span::styled(value.to_string(), Style::default().fg(self.theme.error)),
        ]);
        adaptive_wrap_line(
            &line,
            RtOptions::new(width.saturating_sub(4).max(1)).subsequent_indent(Line::from("      ")),
        )
        .iter()
        .map(line_to_static)
        .collect()
    }

    fn warning_body_lines(&self, value: &str, width: usize) -> Vec<Line<'static>> {
        if value.trim().is_empty() {
            return Vec::new();
        }
        let line = Line::from(vec![
            Span::styled("Reason ", Style::default().fg(self.theme.warning).bold()),
            Span::styled(value.to_string(), Style::default().fg(self.theme.warning)),
        ]);
        adaptive_wrap_line(
            &line,
            RtOptions::new(width.saturating_sub(4).max(1)).subsequent_indent(Line::from("       ")),
        )
        .iter()
        .map(line_to_static)
        .collect()
    }

    fn error_line(&self, value: &str) -> Option<Line<'static>> {
        (!value.trim().is_empty()).then(|| {
            Line::from(vec![
                "  └ Error ".red().bold(),
                Span::styled(
                    compact_legacy_error(value),
                    Style::default().fg(self.theme.error),
                ),
            ])
        })
    }

    fn raw_transcript(&self) -> String {
        if self.item.common().receipt_state == "not_run" {
            let common = self.item.common();
            return format!(
                "Not run · {}\nOperation {} before execution.\nCall ID: {}",
                common.tool_name,
                if common.not_run_reason == "denied" {
                    "declined"
                } else {
                    "cancelled"
                },
                common.call_id
            );
        }
        if self.is_exploration() {
            return self
                .exploration
                .iter()
                .flat_map(|entry| entry.transcripts.iter().cloned())
                .filter(|value| !value.trim().is_empty())
                .collect::<Vec<_>>()
                .join("\n\n");
        }
        if !self.resource_transcripts.is_empty() {
            return self.resource_transcripts.join("\n\n");
        }
        if self.is_stage_append() {
            return self
                .stage_appends
                .iter()
                .map(item_transcript)
                .filter(|value| !value.trim().is_empty())
                .collect::<Vec<_>>()
                .join("\n\n");
        }
        if self.permission_activities.len() > 1 {
            return self
                .permission_activities
                .iter()
                .map(item_transcript)
                .filter(|value| !value.trim().is_empty())
                .collect::<Vec<_>>()
                .join("\n\n");
        }
        item_transcript(&self.item)
    }

    fn render_stage_appends(&self, width: usize) -> Vec<Line<'static>> {
        let mut lines = vec![self.header("Append Stage")];
        let number_width = self
            .stage_appends
            .iter()
            .filter_map(|item| match item {
                DisplayItem::CommandExecution(_, payload) => payload.stage_ordinal,
                _ => None,
            })
            .map(|ordinal| ordinal.to_string().len() + 1)
            .max()
            .unwrap_or(1);
        for item in &self.stage_appends {
            let DisplayItem::CommandExecution(_, payload) = item else {
                continue;
            };
            let command = payload.command.trim();
            let stage = command
                .strip_prefix("Append Stage ·")
                .or_else(|| command.strip_prefix("Append stage:"))
                .map(str::trim)
                .unwrap_or(command);
            let marker = payload
                .stage_ordinal
                .map(|ordinal| format!("{ordinal}."))
                .unwrap_or_else(|| "·".into());
            let number_prefix = Line::from(Span::styled(
                format!("  {marker:>number_width$} "),
                Style::default()
                    .fg(self.theme.accent_soft)
                    .add_modifier(Modifier::BOLD),
            ));
            let continuation = Line::from(" ".repeat(number_prefix.width()));
            let stage_line = Line::from(Span::styled(
                stage.to_string(),
                Style::default().fg(self.theme.text),
            ));
            let wrapped = adaptive_wrap_line(
                &stage_line,
                RtOptions::new(width.max(1))
                    .initial_indent(number_prefix)
                    .subsequent_indent(continuation),
            );
            lines.extend(wrapped.iter().map(line_to_static));
        }
        lines
    }

    fn render_exploration(&self, width: usize) -> Vec<Line<'static>> {
        let running = self
            .exploration
            .iter()
            .any(|entry| entry.item.common().status == "running");
        let title = if self.item.common().status == "unverified" {
            "Result unavailable"
        } else if self.item.common().status == "pending" {
            "Pending exploration"
        } else if running {
            "Exploring"
        } else if self.exploration_all_failed() {
            "Exploration failed"
        } else if self.exploration_all_deferred() {
            "Deferred exploration"
        } else {
            "Explored"
        };
        let mut lines = vec![self.header(title)];
        let mut body = Vec::new();
        for entry in &self.display_exploration_entries() {
            let DisplayItem::CommandExecution(common, payload) = &entry.item else {
                continue;
            };
            let (action, detail) = exploration_action_detail(common, payload);
            let entry_all_failed = entry.failed_occurrences == entry.occurrences;
            let entry_all_deferred = entry.deferred_occurrences == entry.occurrences;
            let action_prefix = if entry_all_failed {
                Line::from(vec![
                    "✗ ".red().bold(),
                    Span::styled(action.clone(), Style::default().fg(self.theme.error)),
                    " ".into(),
                ])
            } else if entry_all_deferred {
                Line::from(vec![
                    Span::styled("◇ ", Style::default().fg(self.theme.warning).bold()),
                    Span::styled(action.clone(), Style::default().fg(self.theme.warning)),
                    " ".into(),
                ])
            } else {
                Line::from(vec![
                    Span::styled(
                        action.clone(),
                        Style::default().fg(ratatui::style::Color::Cyan),
                    ),
                    " ".into(),
                ])
            };
            let detail_width = width
                .saturating_sub(4)
                .saturating_sub(action_prefix.width())
                .max(1);
            let detail = compact_json_detail(&detail, detail_width);
            let mut detail_spans = vec![Span::styled(detail, Style::default().fg(self.theme.text))];
            if common.tool_name == "pdf_reader" {
                if entry.full_read_occurrences > 0 {
                    detail_spans.push(" ".into());
                    if entry.explicit_page_ranges.is_empty() {
                        if let Some(page_count) = entry.document_page_count {
                            detail_spans.push("Pages ".dim());
                            detail_spans.push(Span::styled(
                                format!("1-{page_count}"),
                                Style::default().fg(self.theme.text_muted),
                            ));
                        } else {
                            detail_spans.push("Full document".dim());
                        }
                    } else {
                        detail_spans.push("Full document".dim());
                    }
                }
                if !entry.explicit_page_ranges.is_empty() {
                    detail_spans.push(" ".into());
                    detail_spans.push(
                        if entry.full_read_occurrences > 0 {
                            "· focused Pages "
                        } else {
                            "Pages "
                        }
                        .dim(),
                    );
                    detail_spans.push(Span::styled(
                        format_page_ranges(&entry.explicit_page_ranges),
                        Style::default().fg(self.theme.text_muted),
                    ));
                }
            }
            if common.tool_name == "file_reader" && !entry.read_windows.is_empty() {
                detail_spans.push(" ".into());
                detail_spans.push("Lines ".dim());
                detail_spans.push(Span::styled(
                    format_read_windows(&entry.read_windows),
                    Style::default().fg(self.theme.text_muted),
                ));
                if let Some(total_lines) = entry
                    .read_windows
                    .iter()
                    .filter_map(|window| window.total_lines)
                    .max()
                {
                    detail_spans.push(format!(" of {total_lines}").dim());
                }
            }
            if entry.occurrences > 1 {
                detail_spans.push("  ".into());
                let noun = if common.tool_name == "pdf_reader" {
                    "reads"
                } else {
                    "calls"
                };
                detail_spans.push(format!("×{} {noun}", entry.occurrences).dim());
            }
            if entry.failed_occurrences > 0 && !entry_all_failed {
                detail_spans.push("  ".into());
                detail_spans.push(Span::styled(
                    format!("{} failed", entry.failed_occurrences),
                    Style::default().fg(self.theme.error),
                ));
            }
            let detail_line = Line::from(detail_spans);
            let action_prefix_width = action_prefix.width();
            let wrapped = adaptive_wrap_line(
                &detail_line,
                RtOptions::new(width.saturating_sub(4).max(1))
                    .initial_indent(action_prefix)
                    .subsequent_indent(Line::from(" ".repeat(action_prefix_width))),
            );
            body.extend(wrapped.iter().map(line_to_static));
            for error in &entry.error_summaries {
                if entry_all_deferred {
                    body.extend(self.warning_body_lines(error, width));
                } else {
                    body.extend(self.error_body_lines(error, width));
                }
            }
        }
        lines.extend(self.prefixed_body_lines(body));
        lines
    }

    fn render_command(&self, width: usize) -> Vec<Line<'static>> {
        if self.is_exploration() {
            return self.render_exploration(width);
        }
        if self.is_stage_append() {
            return self.render_stage_appends(width);
        }
        if let Some(stages) = self.finalized_workflow_stages() {
            return super::render_workflow_summary_banner(
                &super::WorkflowSummaryBannerModel { stages },
                width,
                &self.theme,
            );
        }
        match &self.item {
            DisplayItem::CommandExecution(common, payload) => {
                if common.tool_name == "citation_resolve" {
                    return self.render_citation(common, payload, width);
                }
                if common.tool_name == "citation_verify" {
                    return self.render_citation_verification(common, payload, width);
                }
                if common.tool_name == "paper_fetch" {
                    return self.render_paper_fetch(common, payload, width);
                }
                if payload.result_state == "no_matches" {
                    let command = if payload.effective_command.is_empty() {
                        &payload.command
                    } else {
                        &payload.effective_command
                    };
                    let mut lines = vec![Line::from(vec![
                        Span::styled(
                            crate::style::activity_marker("completed"),
                            crate::style::activity_marker_style("completed", &self.theme),
                        ),
                        Span::styled("No matches · exit 1", Style::default().bold()),
                    ])];
                    lines.extend(
                        self.prefixed_body_lines(
                            crate::wrapping::adaptive_wrap_line(
                                &Line::from(command.clone()),
                                RtOptions::new(width.saturating_sub(4).max(1)),
                            )
                            .iter()
                            .map(line_to_static)
                            .collect(),
                        ),
                    );
                    return lines;
                }
                let semantic_header = matches!(
                    common.action.as_str(),
                    "stage_control" | "execution_wait" | "evidence"
                );
                let title = if semantic_header {
                    ""
                } else if common.status == "unverified" {
                    "Result unavailable"
                } else if common.status == "pending" {
                    "Requested"
                } else if common.status == "running" {
                    "Running"
                } else if common.status == "unknown" {
                    "Status unconfirmed"
                } else if common.status == "blocked" {
                    "Blocked"
                } else if common.status == "incomplete" {
                    "Deferred"
                } else if self.status_failed() {
                    if payload.failure_class == "request_validation" {
                        "Invalid request"
                    } else {
                        "Failed"
                    }
                } else {
                    "Ran"
                };
                let command = if !payload.effective_command.trim().is_empty() {
                    payload.effective_command.clone()
                } else if payload.command.trim().is_empty() {
                    common.tool_name.clone()
                } else {
                    payload.command.clone()
                };
                let mut body = Vec::new();
                body.extend(
                    crate::command_render::render_command_output_preview_with_transcript(
                        &payload.output,
                        width.saturating_sub(4).max(1),
                        &self.theme,
                        payload.transcript_complete,
                    ),
                );
                if common.tool_name == "paper_fetch" {
                    body.extend(self.paper_metadata_lines(payload, width));
                } else {
                    body.extend(payload.metadata.iter().map(|(label, value)| {
                        Line::from(Span::styled(
                            format!("{label} {value}"),
                            Style::default().fg(self.theme.text_muted),
                        ))
                    }));
                }
                if self.status_failed() {
                    let mut facts = Vec::new();
                    if let Some(code) = payload.exit_code {
                        facts.push(format!("exit {code}"));
                    }
                    if !payload.failure_class.trim().is_empty() {
                        facts.push(payload.failure_class.trim().to_string());
                    }
                    if !facts.is_empty() {
                        body.extend(
                            adaptive_wrap_line(
                                &Line::from(Span::styled(
                                    facts.join(" · "),
                                    Style::default().fg(self.theme.text_muted),
                                )),
                                RtOptions::new(width.saturating_sub(4).max(1)),
                            )
                            .iter()
                            .map(line_to_static),
                        );
                    }
                }
                let error = preferred_command_error(payload);
                let visible = body
                    .iter()
                    .flat_map(|line| line.spans.iter().map(|span| span.content.as_ref()))
                    .collect::<Vec<_>>()
                    .join(" ")
                    .split_whitespace()
                    .collect::<Vec<_>>()
                    .join(" ");
                let error_words = error.split_whitespace().collect::<Vec<_>>().join(" ");
                let already_visible = !error_words.is_empty() && visible.contains(&error_words);
                if already_visible {
                    // The original error remains in the receipt/transcript.
                } else if common.status == "incomplete" && !error.trim().is_empty() {
                    body.push(Line::from(Span::styled(
                        error,
                        Style::default().fg(self.theme.warning),
                    )));
                } else {
                    body.extend(self.error_body_lines(&error, width));
                }
                if semantic_header {
                    let mut lines = adaptive_wrap_line(
                        &Line::from(Span::styled(
                            command.clone(),
                            Style::default().fg(self.theme.text),
                        )),
                        RtOptions::new(width.max(1))
                            .initial_indent(Line::from(self.marker()))
                            .subsequent_indent(Line::from("  ")),
                    )
                    .iter()
                    .map(line_to_static)
                    .collect::<Vec<_>>();
                    if self.resource_occurrences > 1 {
                        if let Some(last) = lines.last_mut() {
                            last.spans.push("  ".into());
                            last.spans
                                .push(format!("×{} calls", self.resource_occurrences).dim());
                        }
                    }
                    if let Some(duration) =
                        payload.duration_ms.and_then(visible_tool_duration_label)
                    {
                        if let Some(last) = lines.last_mut() {
                            last.spans.push("  · ".dim());
                            last.spans.push(duration.dim());
                        }
                    }
                    lines.extend(self.prefixed_body_lines(body));
                    return lines;
                }

                let occurrence_label = (self.resource_occurrences > 1)
                    .then(|| format!("×{} calls", self.resource_occurrences));
                let duration_label = payload.duration_ms.and_then(visible_tool_duration_label);
                crate::command_render::render_exec_command_block(
                    &command,
                    width,
                    Line::from(vec![self.marker(), title.to_string().bold(), " ".into()]),
                    body,
                    occurrence_label.as_deref(),
                    duration_label.as_deref(),
                    &self.theme,
                )
            }
            _ => Vec::new(),
        }
    }

    fn render_citation(
        &self,
        common: &DisplayItemCommon,
        payload: &crate::display_item::CommandExecutionPayload,
        width: usize,
    ) -> Vec<Line<'static>> {
        let (title, color) = match payload.result_state.as_str() {
            _ if common.status == "pending" => ("Citation lookup requested", self.theme.accent),
            _ if common.status == "unverified" => {
                ("Citation result unavailable", self.theme.warning)
            }
            "resolving" if common.status == "running" => ("Resolving citation", self.theme.accent),
            "identity_resolved" => ("Resolved citation identity", self.theme.accent),
            "identity_conflict" => ("Citation identity conflict", self.theme.error),
            "ambiguous" => ("Citation identity unconfirmed", self.theme.warning),
            "blocked_by_permission" => ("Citation lookup blocked", self.theme.error),
            "runtime_error" => ("Citation transport failed", self.theme.error),
            "not_found" => ("Citation not found", self.theme.error),
            _ if common.status == "running" => ("Resolving citation", self.theme.accent),
            _ if self.status_failed() => ("Citation lookup failed", self.theme.error),
            _ => ("Citation lookup completed", self.theme.accent),
        };
        let mut header = vec![
            self.marker(),
            Span::styled(title.to_string(), Style::default().fg(color).bold()),
        ];
        if let Some(duration) = payload.duration_ms.and_then(visible_tool_duration_label) {
            header.push("  · ".dim());
            header.push(duration.dim());
        }
        let mut lines = vec![Line::from(header)];
        let requested = if !payload.requested_title.trim().is_empty() {
            payload.requested_title.trim()
        } else if !payload.requested_identifier.trim().is_empty() {
            payload.requested_identifier.trim()
        } else {
            payload.requested_author.trim()
        };
        if !requested.is_empty() {
            lines.extend(
                self.prefixed_body_lines(
                    adaptive_wrap_line(
                        &Line::from(vec!["Requested ".dim(), requested.to_string().into()]),
                        RtOptions::new(width.saturating_sub(2).max(1)),
                    )
                    .iter()
                    .map(line_to_static)
                    .collect(),
                ),
            );
        }
        if payload.result_state == "ambiguous" {
            let mut details = Vec::new();
            let requested = [&payload.requested_author, &payload.requested_year]
                .into_iter()
                .filter(|value| !value.trim().is_empty())
                .map(|value| value.trim())
                .collect::<Vec<_>>()
                .join(" · ");
            if !requested.is_empty() {
                details.push(format!("Requested metadata · {requested}"));
            }
            details
                .push("Identity not confirmed; candidates are not verified citations.".to_string());
            for (index, candidate) in payload.citation_candidates.iter().enumerate() {
                details.push(format!("Candidate {} · {}", index + 1, candidate.title));
                let metadata = [candidate.authors.join(", "), candidate.year.clone()]
                    .into_iter()
                    .filter(|value| !value.trim().is_empty())
                    .collect::<Vec<_>>()
                    .join(" · ");
                if !metadata.is_empty() {
                    details.push(metadata);
                }
                if !candidate.doi.is_empty() {
                    details.push(format!("DOI {}", candidate.doi));
                }
            }
            details.extend(payload.diagnostics.iter().take(4).cloned());
            if !payload.citation_guidance.is_empty() {
                details.push(format!("Next · {}", payload.citation_guidance));
            }
            for detail in details {
                lines.extend(
                    self.prefixed_body_lines(
                        adaptive_wrap_line(
                            &Line::from(detail),
                            RtOptions::new(width.saturating_sub(2).max(1)),
                        )
                        .iter()
                        .map(line_to_static)
                        .collect(),
                    ),
                );
            }
            return lines;
        }
        if !payload.resolved_title.trim().is_empty() {
            lines.extend(
                self.prefixed_body_lines(
                    adaptive_wrap_line(
                        &Line::from(vec![
                            "Resolved ".dim(),
                            payload.resolved_title.trim().to_string().into(),
                        ]),
                        RtOptions::new(width.saturating_sub(2).max(1)),
                    )
                    .iter()
                    .map(line_to_static)
                    .collect(),
                ),
            );
        }
        if !payload.resolved_identifier.trim().is_empty() {
            let identifier = payload.resolved_identifier.trim();
            let label = if identifier.starts_with("10.") || identifier.starts_with("doi:") {
                "DOI"
            } else if identifier.starts_with("arxiv:") || identifier.starts_with("arXiv:") {
                "arXiv"
            } else if identifier.starts_with("https://") || identifier.starts_with("http://") {
                "URL"
            } else {
                "Identifier"
            };
            lines.push(self.detail_line(format!(
                    "{label} {}",
                    identifier
                        .trim_start_matches("doi:")
                        .trim_start_matches("arxiv:")
                        .trim_start_matches("arXiv:")
                )));
        }
        let error = preferred_command_error(payload);
        if !error.trim().is_empty() && self.status_failed() {
            lines.extend(self.prefixed_body_lines(self.error_body_lines(&error, width)));
        }
        lines
    }

    fn render_citation_verification(
        &self,
        common: &DisplayItemCommon,
        payload: &crate::display_item::CommandExecutionPayload,
        width: usize,
    ) -> Vec<Line<'static>> {
        let verification = &payload.citation_verification;
        let agent = verification.assessor == "agent";
        let (title, color) = match common.status.as_str() {
            "running" => ("Verifying citation claim", self.theme.accent),
            "cancelled" | "canceled" => ("Citation verification cancelled", self.theme.warning),
            "incomplete" | "paused_for_human" => {
                ("Citation verification deferred", self.theme.warning)
            }
            "failed" if payload.result_state == "request_invalid" => {
                ("Citation request invalid", self.theme.error)
            }
            "failed" => ("Citation verification failed", self.theme.error),
            _ => match verification.assessment.as_str() {
                "supported" if agent => ("Agent assessment · Supported", self.theme.accent),
                "unsupported" if agent => ("Agent assessment · Unsupported", self.theme.warning),
                "unresolved" if agent => ("Agent assessment · Unresolved", self.theme.warning),
                "supported" => ("Citation assessment · Supported", self.theme.accent),
                "unsupported" => ("Citation assessment · Unsupported", self.theme.warning),
                "unresolved" => ("Citation assessment · Unresolved", self.theme.warning),
                _ => ("Citation check completed", self.theme.accent),
            },
        };
        let mut header = vec![
            self.marker(),
            Span::styled(title.to_string(), Style::default().fg(color).bold()),
        ];
        if let Some(duration) = payload.duration_ms.and_then(visible_tool_duration_label) {
            header.push("  · ".dim());
            header.push(duration.dim());
        }
        let mut lines: Vec<_> =
            adaptive_wrap_line(&Line::from(header), RtOptions::new(width.max(1)))
                .iter()
                .map(line_to_static)
                .collect();
        let mut details = Vec::new();
        let mut detail = |label: &str, value: &str| {
            if !value.trim().is_empty() {
                details.extend(
                    adaptive_wrap_line(
                        &Line::from(vec![format!("{label} ").dim(), value.to_string().into()]),
                        RtOptions::new(width.saturating_sub(4).max(1)),
                    )
                    .iter()
                    .map(line_to_static),
                );
            }
        };
        let citation = if verification.bibtex_key.is_empty() {
            verification.citation_ref.as_str()
        } else {
            verification.bibtex_key.as_str()
        };
        detail(
            "Citation",
            if citation.is_empty() {
                "Not available"
            } else {
                citation
            },
        );
        detail("Claim", &verification.claim);
        if common.status == "completed" {
            if !agent {
                detail(
                    "Assessor",
                    if verification.assessor.is_empty() {
                        "Not recorded"
                    } else {
                        &verification.assessor
                    },
                );
            }
            if verification.assessment.is_empty() {
                detail("Assessment", "Not recorded");
            }
        }
        if !verification.content_levels.is_empty() || common.status == "completed" {
            let levels: Vec<_> = verification
                .content_levels
                .iter()
                .map(|level| match level.as_str() {
                    "abstract" => "Abstract",
                    "body" => "Body",
                    "metadata" | "metadata_only" => "Metadata only",
                    _ => "Unknown",
                })
                .collect();
            let evidence = if levels.is_empty() {
                "Not recorded".to_string()
            } else {
                levels.join(", ")
            };
            detail("Evidence", &evidence);
        }
        if !verification.evidence_binding.is_empty() {
            detail(
                "Binding",
                match verification.evidence_binding.as_str() {
                    "stored_source" => "Stored source",
                    "legacy_unbound" => "Evidence not bound to a stored source",
                    _ => "Not verified by UI",
                },
            );
        }
        if let Some(count) = verification.passage_count {
            detail("Passages", &count.to_string());
        } else {
            detail("Passages", "Not available");
        }
        detail("Receipt", &verification.assessment_ref);
        for diagnostic in &verification.diagnostics {
            detail("Note", diagnostic);
        }
        detail("Next", &verification.guidance);
        let error = preferred_command_error(payload);
        if !error.trim().is_empty() {
            details.extend(self.error_body_lines(&error, width));
        }
        lines.extend(self.prefixed_body_lines(details));
        lines
    }

    fn render_paper_fetch(
        &self,
        common: &DisplayItemCommon,
        payload: &crate::display_item::CommandExecutionPayload,
        width: usize,
    ) -> Vec<Line<'static>> {
        let metadata = |key: &str| {
            payload
                .metadata
                .iter()
                .find(|(label, _)| label == key)
                .map(|(_, value)| value.as_str())
                .unwrap_or("")
        };
        let unavailable =
            metadata("outcome") == "unavailable" || metadata("content_level") == "unavailable";
        let abstract_only = matches!(metadata("outcome"), "abstract_only")
            || matches!(metadata("content_level"), "abstract" | "abstract_only");
        let (title, color) = if common.status == "pending" {
            ("Paper retrieval requested", self.theme.accent)
        } else if common.status == "unverified" {
            ("Paper result unavailable", self.theme.warning)
        } else if common.status == "running" {
            ("Fetching paper", self.theme.accent)
        } else if common.status == "incomplete" {
            ("Paper fetch incomplete", self.theme.warning)
        } else if self.status_failed() {
            ("Paper fetch failed", self.theme.error)
        } else if unavailable {
            ("Paper content unavailable", self.theme.warning)
        } else if abstract_only {
            ("Retrieved abstract", self.theme.warning)
        } else {
            ("Fetched paper", self.theme.accent)
        };
        let mut header = vec![
            if common.status == "completed" && (unavailable || abstract_only) {
                Span::styled("! ", Style::default().fg(color))
            } else {
                self.marker()
            },
            Span::styled(title.to_string(), Style::default().fg(color).bold()),
        ];
        if self.resource_occurrences > 1 {
            header.push("  ".into());
            header.push(format!("×{} calls", self.resource_occurrences).dim());
        }
        if let Some(duration) = payload.duration_ms.and_then(visible_tool_duration_label) {
            header.push("  · ".dim());
            header.push(duration.dim());
        }
        let mut lines = vec![Line::from(header)];
        lines.extend(self.prefixed_body_lines(self.paper_metadata_lines(payload, width)));
        let error = preferred_command_error(payload);
        if !error.trim().is_empty() {
            lines.extend(self.prefixed_body_lines(self.error_body_lines(&error, width)));
        }
        lines
    }

    fn paper_metadata_lines(
        &self,
        payload: &crate::display_item::CommandExecutionPayload,
        width: usize,
    ) -> Vec<Line<'static>> {
        let value = |keys: &[&str]| {
            keys.iter().find_map(|key| {
                payload
                    .metadata
                    .iter()
                    .find(|(label, _)| label == key)
                    .map(|(_, value)| value.clone())
                    .filter(|value| !value.trim().is_empty())
            })
        };
        let readable = |value: Option<String>, category: &str| {
            value.map(|raw| match (category, raw.as_str()) {
                ("content", "body_available" | "content_available" | "complete") => {
                    "Available".into()
                }
                ("content", "abstract" | "abstract_only") => "Abstract only".into(),
                ("content", "unavailable") => "Unavailable".into(),
                ("coverage", "pdf_page") => "PDF page extraction".into(),
                ("coverage", "abstract_only" | "no_body") => "Abstract only".into(),
                ("coverage", "none") => "No content extracted".into(),
                ("source", "openalex") => "OpenAlex".into(),
                ("source", "crossref") => "Crossref".into(),
                ("source", "open_content") => "Open content".into(),
                _ => raw.replace('_', " "),
            })
        };
        let content = readable(value(&["content_level", "outcome"]), "content");
        let coverage = readable(value(&["extraction_coverage"]), "coverage").filter(|value| {
            Some(value) != content.as_ref()
                && !(value == "No content extracted" && content.as_deref() == Some("Unavailable"))
        });
        [
            ("Paper", value(&["title"])),
            ("DOI", value(&["doi"])),
            ("Input", value(&["identifier"])),
            ("Content", content),
            ("Coverage", coverage),
            (
                "Source",
                readable(value(&["selected_source", "source"]), "source"),
            ),
        ]
        .into_iter()
        .filter_map(|(label, value)| value.map(|value| (label, value)))
        .flat_map(|(label, value)| {
            let label_prefix = Line::from(vec!["• ".dim(), format!("{label} ").dim()]);
            adaptive_wrap_line(
                &Line::from(Span::styled(value, Style::default().fg(self.theme.text))),
                RtOptions::new(width.saturating_sub(4).max(1))
                    .initial_indent(label_prefix)
                    .subsequent_indent(Line::from("  ")),
            )
            .iter()
            .map(line_to_static)
            .collect::<Vec<_>>()
        })
        .collect()
    }

    fn render_mcp(&self) -> Vec<Line<'static>> {
        let DisplayItem::McpToolCall(common, payload) = &self.item else {
            return Vec::new();
        };
        let title = match common.status.as_str() {
            "pending" => "Requested",
            "unverified" => "Result unavailable",
            "running" => "Calling",
            "failed" | "error" | "cancelled" | "canceled" | "interrupted" => "Failed to call",
            _ => "Called",
        };
        let invocation = [payload.server.as_str(), payload.tool.as_str()]
            .into_iter()
            .filter(|value| !value.is_empty())
            .collect::<Vec<_>>()
            .join(".");
        let mut lines = vec![self.header(title), self.tree_line(invocation)];
        lines.extend(
            payload
                .output
                .iter()
                .take(5)
                .cloned()
                .map(|line| self.detail_line(line)),
        );
        if let Some(error) = self.error_line(&payload.error) {
            lines.push(error);
        }
        lines
    }

    fn render_web(&self, width: usize) -> Vec<Line<'static>> {
        let DisplayItem::WebSearch(common, payload) = &self.item else {
            return Vec::new();
        };
        if matches!(common.status.as_str(), "pending" | "unverified") {
            let label = if common.status == "pending" {
                "Web request pending"
            } else {
                "Web result unavailable"
            };
            return vec![
                self.header(label),
                self.tree_line(if payload.query.is_empty() {
                    payload.url.clone()
                } else {
                    payload.query.clone()
                }),
            ];
        }
        let running = common.status == "running";
        let failed = matches!(
            common.status.as_str(),
            "failed" | "error" | "cancelled" | "canceled" | "interrupted"
        );
        let deferred = common.status == "incomplete";
        let find_detail = match (payload.query.is_empty(), payload.url.is_empty()) {
            (false, false) => format!("\"{}\" · {}", payload.query, payload.url),
            (false, true) => format!("\"{}\"", payload.query),
            (true, _) => payload.url.clone(),
        };
        let (title, detail) = if payload.action == "find_in_page" {
            (
                if running {
                    "Searching page"
                } else if failed {
                    "Failed to search page"
                } else if deferred {
                    "Deferred page search"
                } else {
                    "Searched page"
                },
                find_detail.as_str(),
            )
        } else if payload.action == "open_page" {
            (
                if running {
                    "Opening page"
                } else if failed {
                    "Failed to open page"
                } else if deferred {
                    "Deferred page open"
                } else {
                    "Opened page"
                },
                if payload.display_url.trim().is_empty() {
                    payload.url.as_str()
                } else {
                    payload.display_url.as_str()
                },
            )
        } else {
            (
                if running {
                    "Searching the web"
                } else if failed {
                    "Failed to search the web"
                } else if deferred {
                    "Deferred web search"
                } else {
                    "Searched the web"
                },
                payload.query.as_str(),
            )
        };
        let mut line = vec![
            self.marker(),
            Span::styled(
                title.to_string(),
                Style::default().fg(self.theme.accent).bold(),
            ),
        ];
        if !detail.is_empty() {
            line.push(if failed && payload.action == "open_page" {
                " ".into()
            } else {
                " for ".into()
            });
            line.push(detail.to_string().into());
        }
        let mut lines = adaptive_wrap_line(
            &Line::from(line),
            RtOptions::new(width.max(1)).subsequent_indent(Line::from("  ")),
        )
        .iter()
        .map(line_to_static)
        .collect::<Vec<_>>();
        let error = preferred_error(&payload.error_summary, &payload.error);
        if !running && !failed && !deferred {
            if payload.action == "open_page" && detail.is_empty() {
                lines.push(self.tree_line("Target URL not recorded"));
            }
        }
        let error_lines = if deferred {
            self.warning_body_lines(&error, width)
        } else {
            self.error_body_lines(&error, width)
        };
        lines.extend(self.prefixed_body_lines(error_lines));
        lines
    }

    fn render_other(&self, width: usize) -> Vec<Line<'static>> {
        match &self.item {
            DisplayItem::BatchOutcome(_, payload) => self.render_batch_outcome(payload, width),
            DisplayItem::ImageView(_, payload) => {
                let mut lines = vec![
                    self.header("Viewed Image"),
                    self.tree_line(payload.path.clone()),
                ];
                if let Some(error) = self.error_line(&payload.error) {
                    lines.push(error);
                }
                lines
            }
            DisplayItem::ImageGeneration(common, payload) => {
                let title = if common.status == "running" {
                    "Generating Image"
                } else {
                    "Generated Image"
                };
                let detail = if payload.path.is_empty() {
                    &payload.prompt
                } else {
                    &payload.path
                };
                let mut lines = vec![self.header(title), self.tree_line(detail.clone())];
                if let Some(error) = self.error_line(&payload.error) {
                    lines.push(error);
                }
                lines
            }
            DisplayItem::PlanUpdate(_, payload) => self.render_plan_update(payload, width),
            DisplayItem::TaskOutcome(_, payload) => self.render_task_outcome(payload, width),
            DisplayItem::ActionRequired(_, payload) => self.render_action_required(payload, width),
            DisplayItem::BackgroundTerminalInteraction(_, payload) => {
                let mut lines = vec![self.header("Wrote input")];
                if !payload.session_id.is_empty() {
                    lines.push(self.tree_line(payload.session_id.clone()));
                }
                if !payload.input.is_empty() {
                    lines.push(self.detail_line(payload.input.clone()));
                }
                lines.extend(
                    payload
                        .output
                        .iter()
                        .take(5)
                        .cloned()
                        .map(|line| self.detail_line(line)),
                );
                if let Some(error) = self.error_line(&payload.error) {
                    lines.push(error);
                }
                lines
            }
            DisplayItem::SkillActivity(common, payload) => {
                let verb = if common.status == "running" {
                    "Using skill"
                } else {
                    "Used skill"
                };
                let mut lines = vec![Line::from(vec![
                    Span::styled(
                        crate::style::GLYPH_ACTION,
                        crate::style::activity_marker_style(&common.status, &self.theme),
                    ),
                    Span::styled(
                        format!("{verb}: "),
                        Style::default()
                            .fg(self.theme.text_title)
                            .add_modifier(Modifier::BOLD),
                    ),
                    Span::styled(
                        payload.skill_name.clone(),
                        Style::default().fg(self.theme.accent),
                    ),
                ])];
                if !payload.purpose.is_empty() {
                    let purpose = Line::from(vec![
                        Span::styled("Purpose", Style::default().fg(self.theme.accent)),
                        " ".into(),
                        Span::styled(
                            payload.purpose.clone(),
                            Style::default().fg(self.theme.text),
                        ),
                    ]);
                    lines.extend(
                        adaptive_wrap_line(
                            &purpose,
                            RtOptions::new(width.max(1))
                                .initial_indent(Line::from("  └ ".dim()))
                                .subsequent_indent(Line::from("    ")),
                        )
                        .iter()
                        .map(line_to_static),
                    );
                }
                if let Some(error) = self.error_line(&payload.error) {
                    lines.push(error);
                }
                lines
            }
            DisplayItem::ModelActivity(common, payload) => {
                let label = if payload.label.trim().is_empty() {
                    "Processed model request"
                } else {
                    payload.label.trim()
                };
                let actor = match common.action.as_str() {
                    "provider_retry" => "Provider",
                    "web_access" => "Access",
                    "provider_recovery" => "Recovery",
                    _ => "Agent",
                };
                let mut spans = vec![
                    Span::styled(
                        crate::style::GLYPH_ACTION,
                        crate::style::activity_marker_style(
                            if common.action == "provider_retry" && payload.outcome == "failed" {
                                "failed"
                            } else {
                                &common.status
                            },
                            &self.theme,
                        ),
                    ),
                    Span::styled(
                        actor,
                        Style::default()
                            .fg(self.theme.text_title)
                            .add_modifier(Modifier::BOLD),
                    ),
                    Span::styled(" · ", Style::default().fg(self.theme.text_muted)),
                    Span::styled(label.to_string(), Style::default().fg(self.theme.text)),
                ];
                if !matches!(common.action.as_str(), "web_access" | "provider_recovery") {
                    spans.push(Span::styled(
                        " · ",
                        Style::default().fg(self.theme.text_muted),
                    ));
                    spans.push(Span::styled(
                        format!("{:.1}s", payload.elapsed_ms as f64 / 1000.0),
                        Style::default().fg(self.theme.text_muted),
                    ));
                }
                let mut lines: Vec<_> = adaptive_wrap_line(
                    &Line::from(spans),
                    RtOptions::new(width.max(1)).subsequent_indent(Line::from("  ")),
                )
                .iter()
                .map(line_to_static)
                .collect();
                if common.action == "provider_retry" {
                    if let Some(context) = provider_retry_context(payload) {
                        let context = Line::from(Span::styled(
                            context,
                            Style::default().fg(self.theme.text_muted),
                        ));
                        lines.extend(
                            adaptive_wrap_line(
                                &context,
                                RtOptions::new(width.max(1))
                                    .initial_indent(Line::from("  └ ".dim()))
                                    .subsequent_indent(Line::from("    ")),
                            )
                            .iter()
                            .map(line_to_static),
                        );
                    }
                }
                lines
            }
            DisplayItem::Heartbeat(_, payload) => {
                let phase = payload.phase.trim();
                let mut line = vec![self.marker()];
                if phase == "heartbeat" {
                    line.push(format!("Heartbeat {:02}", payload.sequence.max(1)).bold());
                } else {
                    let title = if payload.title.is_empty() {
                        "LLM request"
                    } else {
                        &payload.title
                    };
                    line.push(title.to_string().bold());
                }
                if !phase.is_empty() {
                    line.push(" · ".dim());
                    line.push(phase.replace('_', " ").dim());
                }
                line.push(" · ".dim());
                line.push(format!("{:.1}s", payload.elapsed_ms as f64 / 1000.0).dim());
                if !payload.request.is_empty() {
                    line.push("  ".into());
                    line.push(payload.request.clone().into());
                }
                if payload.attempt_count > 1 {
                    line.push(format!(" · attempt {}", payload.attempt_count).dim());
                }
                let mut lines = vec![Line::from(line)];
                if let Some(error) = self.error_line(&payload.error) {
                    lines.push(error);
                }
                lines
            }
            DisplayItem::Review(_, payload) => {
                let mut line = vec![self.marker(), payload.title.clone().bold()];
                if !payload.text.is_empty() {
                    line.push(" · ".into());
                    line.push(payload.text.clone().into());
                }
                let mut lines = vec![Line::from(line)];
                if let Some(error) = self.error_line(&payload.error) {
                    lines.push(error);
                }
                lines
            }
            DisplayItem::LlmRequestFailure(_, payload) => {
                let title = if payload.title.is_empty() {
                    "Model request failed"
                } else {
                    &payload.title
                };
                let mut lines = vec![Line::from(vec![
                    self.marker(),
                    Span::styled(
                        title.to_string(),
                        Style::default()
                            .fg(self.theme.error)
                            .add_modifier(Modifier::BOLD),
                    ),
                ])];
                if let Some(error) = self.error_line(&payload.error) {
                    lines.push(error);
                }
                lines
            }
            DisplayItem::ReviewCycle(_, payload) => self.render_review_cycle(payload, width),
            DisplayItem::ProtocolActivity(_, payload) => {
                self.render_protocol_activity(payload, width)
            }
            DisplayItem::InteractionResult(_, payload) => {
                self.render_interaction_result(payload, width)
            }
            DisplayItem::PermissionActivity(_, _) => self.render_permission_activities(width),
            DisplayItem::ArtifactActivity(_, payload) => {
                self.render_artifact_activity(payload, width)
            }
            DisplayItem::RouteDecision(_, payload) => self.render_route_decision(payload, width),
            DisplayItem::TaskHeader(_, payload) => self.render_task_header(payload, width),
            DisplayItem::ContextCompaction(_, payload) => {
                let mut lines = vec![self.header(if payload.title.is_empty() {
                    "Compacted context"
                } else {
                    &payload.title
                })];
                if !payload.text.is_empty() {
                    lines.push(self.tree_line(payload.text.clone()));
                }
                if let Some(error) = self.error_line(&payload.error) {
                    lines.push(error);
                }
                lines
            }
            DisplayItem::Reasoning(_, payload) => {
                let summary = payload.summary.join("\n\n");
                let component = if self.item.common().audit_context.is_some() {
                    ""
                } else {
                    payload.component.as_str()
                };
                let label = match component.trim().to_ascii_lowercase().as_str() {
                    "" | "core" => "Thinking".to_string(),
                    component => {
                        format!("Thinking · {}", super::title_case_component(component))
                    }
                };
                let mut lines =
                    super::render_reasoning_summary(&summary, &label, width, &self.theme, Some(6));
                if self.show_raw_reasoning {
                    lines.extend(
                        super::ReasoningRawHistoryCell::new(
                            payload.content.join("\n"),
                            self.theme.clone(),
                        )
                        .display_lines(width),
                    );
                }
                lines
            }
            DisplayItem::AgentMessage(_, payload)
            | DisplayItem::ProposedPlan(_, payload)
            | DisplayItem::SubAgentActivity(_, payload) => {
                let title = if payload.title.is_empty() {
                    "Activity"
                } else {
                    &payload.title
                };
                let mut lines = vec![self.header(title)];
                if !payload.text.is_empty() {
                    lines.push(self.tree_line(payload.text.clone()));
                }
                if let Some(error) = self.error_line(&payload.error) {
                    lines.push(error);
                }
                lines
            }
            DisplayItem::CommandExecution(_, _)
            | DisplayItem::FileChange(_, _)
            | DisplayItem::McpToolCall(_, _)
            | DisplayItem::WebSearch(_, _) => Vec::new(),
            DisplayItem::Stage(_, _) => Vec::new(),
        }
    }

    fn render_batch_outcome(
        &self,
        payload: &BatchOutcomePayload,
        width: usize,
    ) -> Vec<Line<'static>> {
        let permission_deferred = payload.reason_code == "permission_pause_tool_mixed_batch";
        let noun = if payload.calls.len() == 1 {
            "call"
        } else {
            "calls"
        };
        let mut lines = vec![Line::from(vec![
            Span::styled(
                crate::style::GLYPH_ACTION,
                Style::default().fg(self.theme.warning),
            ),
            Span::styled(
                if permission_deferred {
                    "Deferred"
                } else {
                    "Not run"
                },
                Style::default()
                    .fg(self.theme.warning)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::styled(
                format!(" · {} tool {noun}", payload.calls.len()),
                Style::default().fg(self.theme.text_muted),
            ),
        ])];
        for call in &payload.calls {
            let target = if call.display_target.trim().is_empty() {
                call.target.trim()
            } else {
                call.display_target.trim()
            };
            let mut parts = vec![call.tool_name.clone()];
            if !target.is_empty() {
                parts.push(target.to_string());
            }
            parts.extend(call.qualifiers.iter().filter_map(|value| {
                let value = value.trim();
                (!value.is_empty()).then(|| value.to_string())
            }));
            let detail = parts.join(" · ");
            lines.extend(
                adaptive_wrap_line(
                    &Line::from(Span::styled(detail, Style::default().fg(self.theme.text))),
                    RtOptions::new(width.max(1))
                        .initial_indent(Line::from("  ├ ".dim()))
                        .subsequent_indent(Line::from("    ")),
                )
                .iter()
                .map(line_to_static),
            );
        }
        let visible_reason = if payload.display_reason.trim().is_empty() {
            payload.reason.trim()
        } else {
            payload.display_reason.trim()
        };
        if !visible_reason.is_empty() {
            lines.extend(
                adaptive_wrap_line(
                    &Line::from(Span::styled(
                        visible_reason.to_string(),
                        Style::default().fg(self.theme.warning),
                    )),
                    RtOptions::new(width.max(1))
                        .initial_indent(Line::from("  └ ".dim()))
                        .subsequent_indent(Line::from("    ")),
                )
                .iter()
                .map(line_to_static),
            );
        }
        if !payload.recovery.trim().is_empty() {
            lines.extend(
                adaptive_wrap_line(
                    &Line::from(Span::styled(
                        payload.recovery.clone(),
                        Style::default().fg(self.theme.text_muted),
                    )),
                    RtOptions::new(width.max(1))
                        .initial_indent(Line::from("    "))
                        .subsequent_indent(Line::from("    ")),
                )
                .iter()
                .map(line_to_static),
            );
        }
        lines
    }

    fn direct_outcome_lines(
        &self,
        payload: &TaskOutcomePayload,
        width: usize,
    ) -> Vec<crate::terminal_hyperlinks::HyperlinkLine> {
        let completed = payload.outcome == "idle";
        let missing = completed
            && matches!(
                payload.reply_observation.as_str(),
                "none" | "commentary_only"
            );
        let mut facts = Vec::new();
        if missing {
            facts.push((
                "Reply".into(),
                if payload.reply_observation == "commentary_only" {
                    "Only progress messages were received".into()
                } else {
                    "No assistant reply was received".into()
                },
            ));
        }
        if completed {
            facts.push(("Session".into(), "Ready for your next message".into()));
        }
        if !payload.failure_summary.is_empty() {
            facts.push(("Error".into(), payload.failure_summary.clone()));
        }
        facts.extend(payload.failure_facts.clone());
        for action in &payload.recovery_actions {
            facts.push(("Recovery".into(), action.clone()));
        }
        let banner = super::render_stage_outcome_banner(
            &super::StageOutcomeBannerModel {
                label: format!("REQUEST {}", payload.request_index),
                decision: if missing {
                    "no_final_reply".into()
                } else if completed {
                    "completed".into()
                } else {
                    payload.outcome.clone()
                },
                facts,
            },
            width,
            &self.theme,
        );
        let mut lines = crate::terminal_hyperlinks::plain_hyperlink_lines(banner);
        if !payload.recorded_files.is_empty() {
            lines.extend(crate::terminal_hyperlinks::plain_hyperlink_lines(
                crate::wrapping::adaptive_wrap_line(
                    &Line::from("Files from this request:"),
                    RtOptions::new(width.max(1)),
                )
                .iter()
                .map(line_to_static)
                .collect(),
            ));
            for file in &payload.recorded_files {
                let mut line = crate::terminal_hyperlinks::HyperlinkLine::new(Line::default());
                let destination = url::Url::from_file_path(&file.path)
                    .ok()
                    .map(|url| url.to_string());
                line.push_span(
                    Span::styled(
                        file.display_path.clone(),
                        Style::default().add_modifier(Modifier::UNDERLINED),
                    ),
                    destination.as_deref(),
                );
                lines.extend(crate::terminal_hyperlinks::adaptive_wrap_hyperlink_lines(
                    &[line],
                    RtOptions::new(width.max(1))
                        .initial_indent(Line::from("  "))
                        .subsequent_indent(Line::from("  ")),
                ));
                if file.internal_citation_count > 0 {
                    let notice = format!(
                        "Citations: {} unlinked {} in saved text",
                        file.internal_citation_count,
                        if file.internal_citation_count == 1 {
                            "marker"
                        } else {
                            "markers"
                        }
                    );
                    lines.extend(crate::terminal_hyperlinks::plain_hyperlink_lines(
                        crate::wrapping::adaptive_wrap_line(
                            &Line::from(Span::styled(
                                notice,
                                Style::default().fg(self.theme.warning),
                            )),
                            RtOptions::new(width.max(1))
                                .initial_indent(Line::from("  "))
                                .subsequent_indent(Line::from("  ")),
                        )
                        .iter()
                        .map(line_to_static)
                        .collect(),
                    ));
                }
            }
        }
        lines
    }

    fn render_task_outcome(
        &self,
        payload: &TaskOutcomePayload,
        width: usize,
    ) -> Vec<Line<'static>> {
        if payload.outcome == "unexpected_exit"
            && payload.outcome_source == "runtime_status_observation"
        {
            let mut facts = payload.failure_facts.clone();
            if facts.is_empty() {
                facts.push(("Cause".into(), payload.failure_summary.clone()));
            }
            if !payload.failure_detail.is_empty() {
                facts.push((
                    "Details".into(),
                    "ctrl+t for recorded error metadata".into(),
                ));
            }
            facts.extend(
                payload
                    .recovery_actions
                    .iter()
                    .map(|action| ("Next".into(), action.clone())),
            );
            return super::render_stage_outcome_banner(
                &super::StageOutcomeBannerModel {
                    label: "RUNTIME".into(),
                    decision: "unexpected_exit".into(),
                    facts,
                },
                width,
                &self.theme,
            );
        }
        if payload.request_index > 0 {
            return crate::terminal_hyperlinks::visible_lines(
                self.direct_outcome_lines(payload, width),
            );
        }
        let task_state = if payload.outcome.trim().is_empty() {
            if payload.task_success {
                "completed"
            } else {
                "failed"
            }
        } else {
            payload.outcome.trim()
        };
        let headline = if payload.verification_status == "conflict" {
            "CONFLICT"
        } else if payload.delivery_status == "failed"
            && task_state == "completed"
            && !payload.delivery_summary.is_empty()
        {
            "DELIVERY FAILED"
        } else {
            task_state
        };
        let mut facts = vec![(
            "Task".to_string(),
            task_state.replace('_', " ").to_uppercase(),
        )];
        let source = if payload.outcome_source.trim().is_empty()
            || payload.outcome_source.eq_ignore_ascii_case("runtime")
        {
            "runtime declaration"
        } else {
            payload.outcome_source.trim()
        };
        facts.push((
            "Source".to_string(),
            source.replace('_', " ").to_uppercase(),
        ));
        if !payload.delivery_status.trim().is_empty() {
            facts.push((
                "Delivery".to_string(),
                payload
                    .delivery_status
                    .trim()
                    .replace('_', " ")
                    .to_uppercase(),
            ));
        }
        if !payload.primary_artifacts.is_empty() {
            facts.push((
                "Artifacts".to_string(),
                payload
                    .primary_artifacts
                    .iter()
                    .map(|artifact| artifact.display_path.as_str())
                    .collect::<Vec<_>>()
                    .join(", "),
            ));
        }
        for conflict in &payload.status_conflicts {
            if !conflict.trim().is_empty() {
                facts.push(("Conflict".to_string(), conflict.trim().to_string()));
            }
        }
        for caveat in &payload.caveats {
            if !caveat.trim().is_empty() {
                facts.push(("Caveat".to_string(), caveat.trim().to_string()));
            }
        }
        if !payload.delivery_summary.is_empty() {
            facts.push(("Reason".into(), payload.delivery_summary.clone()));
        }
        for reason in payload
            .runtime_rationale
            .iter()
            .filter(|_| payload.delivery_summary.is_empty() && payload.failure_facts.is_empty())
        {
            if !reason.trim().is_empty() {
                facts.push(("Reason".to_string(), reason.trim().to_string()));
            }
        }
        // File-level failures come before aggregate transport/receipt warnings.
        for warning in payload
            .delivery_warnings
            .iter()
            .filter(|w| !w.path.is_empty())
        {
            facts.push((
                "File".into(),
                format!(
                    "{} · {}",
                    display_review_path(&warning.path, self.cwd.as_deref()),
                    warning.code.replace('_', " ")
                ),
            ));
        }
        let mut warning_counts = std::collections::BTreeMap::new();
        for warning in &payload.delivery_warnings {
            *warning_counts
                .entry(warning.code.as_str())
                .or_insert(0_usize) += 1;
        }
        for (code, count) in warning_counts {
            facts.push((
                "Diagnostics".into(),
                format!("{} ×{count}", code.replace('_', " ")),
            ));
        }
        if !payload.delivery_warnings.is_empty() {
            facts.push((
                "Details".into(),
                format!(
                    "{} {} · ctrl+t for full paths and request IDs",
                    payload.delivery_warnings.len(),
                    if payload.delivery_warnings.len() == 1 {
                        "diagnostic"
                    } else {
                        "diagnostics"
                    }
                ),
            ));
        }
        if !payload.failure_cause.trim().is_empty() || !payload.failure_facts.is_empty() {
            if !payload.failure_facts.is_empty() {
                facts.extend(payload.failure_facts.clone());
            } else {
                facts.push((
                    "Cause".to_string(),
                    payload.failure_cause.trim().to_string(),
                ));
            }
            if payload.provider_request_count > 0 && payload.provider_activity_scope == "audit" {
                facts.push((
                    "Review provider activity".to_string(),
                    format!(
                        "{} requests · {} retries",
                        payload.provider_request_count, payload.provider_retry_count
                    ),
                ));
            }
            if payload.final_request_attempt_count > 0 {
                facts.push((
                    "Final request".to_string(),
                    format!(
                        "{} {} · {} {}",
                        payload.final_request_attempt_count,
                        if payload.final_request_attempt_count == 1 {
                            "attempt"
                        } else {
                            "attempts"
                        },
                        payload.final_request_retry_count,
                        if payload.final_request_retry_count == 1 {
                            "retry"
                        } else {
                            "retries"
                        },
                    ),
                ));
            }
        } else {
            if !payload.failure_domain.trim().is_empty() {
                facts.push((
                    "Failure domain".to_string(),
                    payload.failure_domain.trim().replace('_', " "),
                ));
            }
            if !payload.failure_code.trim().is_empty() {
                facts.push((
                    "Failure code".to_string(),
                    payload.failure_code.trim().replace('_', " "),
                ));
            }
            if !payload.failure_summary.trim().is_empty() {
                facts.push((
                    "Reason".to_string(),
                    payload.failure_summary.trim().to_string(),
                ));
            }
        }
        let detail = payload.failure_detail.trim();
        if !detail.is_empty() {
            if payload.failure_facts.is_empty()
                && detail.chars().count() <= 160
                && !detail.contains('\n')
            {
                facts.push(("Detail".into(), detail.into()));
            } else {
                facts.push(("Details".into(), "ctrl+t for full diagnostics".into()));
            }
        }
        for action in &payload.recovery_actions {
            if !action.trim().is_empty() {
                facts.push(("Next".to_string(), action.trim().to_string()));
            }
        }
        super::render_stage_outcome_banner(
            &super::StageOutcomeBannerModel {
                label: "TASK".to_string(),
                decision: headline.to_string(),
                facts,
            },
            width,
            &self.theme,
        )
    }

    fn render_action_required(
        &self,
        payload: &ActionRequiredPayload,
        width: usize,
    ) -> Vec<Line<'static>> {
        let mut facts = Vec::new();
        if !payload.detail.trim().is_empty() {
            facts.push(("State".to_string(), payload.detail.trim().to_string()));
        }
        if !payload.reason_code.trim().is_empty() {
            facts.push((
                "Reason".to_string(),
                payload.reason_code.trim().replace('_', " "),
            ));
        }
        if !payload.run_dir.trim().is_empty() {
            facts.push(("Run".to_string(), payload.run_dir.trim().to_string()));
        }
        super::render_stage_outcome_banner(
            &super::StageOutcomeBannerModel {
                label: if payload.title.trim().is_empty() {
                    "ACTION REQUIRED".to_string()
                } else {
                    payload.title.trim().to_uppercase()
                },
                decision: "PROTOCOL ERROR".to_string(),
                facts,
            },
            width,
            &self.theme,
        )
    }

    fn render_task_header(&self, payload: &TaskHeaderPayload, width: usize) -> Vec<Line<'static>> {
        if payload.route_label.trim().is_empty() {
            let title = if payload.title.is_empty() {
                "Interpreted Task"
            } else {
                &payload.title
            };
            let mut lines = vec![Line::from(vec![
                Span::styled(
                    crate::style::GLYPH_ACTION,
                    Style::default().fg(self.theme.accent),
                ),
                Span::styled(
                    title.to_string(),
                    Style::default()
                        .fg(self.theme.text_title)
                        .add_modifier(Modifier::BOLD),
                ),
            ])];
            lines.extend(
                payload
                    .text
                    .lines()
                    .filter(|line| !line.trim().is_empty())
                    .map(|line| self.detail_line(line.to_string())),
            );
            return lines;
        }

        self.render_router_banner(payload.route_label.trim(), width)
    }

    fn render_route_decision(
        &self,
        payload: &RouteDecisionPayload,
        width: usize,
    ) -> Vec<Line<'static>> {
        if payload.route_kind == "runtime_profile" {
            return super::render_mode_banner(&payload.route_label, "MODE", width, &self.theme);
        }
        let status = self.item.common().status.as_str();
        let label = if status == "running" {
            "Selecting execution path…".to_string()
        } else if status == "failed" {
            "Routing failed".to_string()
        } else if payload.elapsed_ms > 0 {
            format!(
                "{}  ·  {:.1}s",
                payload.route_label.trim(),
                payload.elapsed_ms as f64 / 1000.0
            )
        } else {
            payload.route_label.trim().to_string()
        };
        self.render_router_banner(&label, width)
    }

    fn render_router_banner(&self, route_label: &str, width: usize) -> Vec<Line<'static>> {
        super::render_router_navigation_banner(
            &super::RouterBannerModel {
                label: route_label.to_string(),
            },
            width,
            &self.theme,
        )
    }

    fn render_review_cycle(
        &self,
        payload: &ReviewCyclePayload,
        width: usize,
    ) -> Vec<Line<'static>> {
        crate::terminal_hyperlinks::visible_lines(
            super::review_cycle::render_review_cycle_card_hyperlinks_with_sources(
                payload,
                width,
                &self.theme,
                self.cwd.as_deref(),
                &self.citation_sources,
            ),
        )
    }

    fn render_protocol_activity(
        &self,
        payload: &ProtocolActivityPayload,
        width: usize,
    ) -> Vec<Line<'static>> {
        let component = sentence_case_label(&payload.component);
        let mut header = vec![
            self.marker(),
            "Protocol".bold(),
            " · ".into(),
            sentence_case_label(&payload.stage).into(),
            " · attempt ".into(),
            payload.attempt.max(1).to_string().into(),
        ];
        if !component.is_empty() {
            header.push(" · ".into());
            header.push(component.into());
        }
        let mut lines = adaptive_wrap_line(&Line::from(header), RtOptions::new(width.max(1)))
            .iter()
            .map(line_to_static)
            .collect::<Vec<_>>();
        let mut body = Vec::new();
        for correction in &payload.corrections {
            let label = if correction.kind.trim().is_empty() {
                format!("Correction {}", correction.attempt.max(1))
            } else {
                format!(
                    "Correction {} · {}",
                    correction.attempt.max(1),
                    correction.kind.replace('_', " ")
                )
            };
            body.push(Line::from(Span::styled(
                label,
                Style::default().fg(self.theme.warning),
            )));
            if !correction.summary.trim().is_empty() {
                body.extend(
                    adaptive_wrap_line(
                        &Line::from(correction.summary.clone()),
                        RtOptions::new(width.saturating_sub(4).max(1)),
                    )
                    .iter()
                    .map(line_to_static),
                );
            }
        }
        if !payload.required_actions.is_empty() {
            body.push(Line::from(vec![
                "Required · ".dim(),
                payload.required_actions.join(" or ").into(),
            ]));
        }
        if self.item.common().status != "running" {
            let status = if payload.outcome == "recovered" {
                "Recovered"
            } else {
                "Failed"
            };
            let color = if payload.outcome == "recovered" {
                self.theme.success
            } else {
                self.theme.error
            };
            let status_code = payload.status_code.replace('_', " ");
            let duplicate = normalize_status_token(status) == normalize_status_token(&status_code);
            let mut spans = vec![Span::styled(
                status.to_string(),
                Style::default().fg(color).bold(),
            )];
            if !status_code.trim().is_empty() && !duplicate {
                spans.push(" · ".into());
                spans.push(status_code.into());
            }
            body.push(Line::from(spans));
        }
        lines.extend(self.prefixed_body_lines(body));
        lines
    }

    fn render_interaction_result(
        &self,
        payload: &InteractionResultPayload,
        width: usize,
    ) -> Vec<Line<'static>> {
        let title = if payload.title.trim().is_empty() {
            if payload.interaction_kind == "permission_request" {
                "Permission"
            } else {
                "Human question"
            }
        } else {
            payload.title.as_str()
        };
        if payload.interaction_kind == "permission_request" || !payload.decision.trim().is_empty() {
            return super::render_permission_result(
                title,
                &payload.request_summary,
                &payload.decision,
                &payload.file_changes,
                width,
                &self.theme,
            );
        }
        if payload.outcome == "dismissed_local" {
            let mut lines = super::render_clarification_result(title, &[], width, &self.theme);
            for text in [&payload.detail, &payload.resume_hint] {
                if !text.trim().is_empty() {
                    lines.extend(
                        self.prefixed_body_lines(
                            adaptive_wrap_line(
                                &Line::from(text.clone()),
                                RtOptions::new(width.saturating_sub(4).max(1)),
                            )
                            .iter()
                            .map(line_to_static)
                            .collect(),
                        ),
                    );
                }
            }
            return lines;
        }
        let answers = payload
            .answers
            .iter()
            .map(|answer| super::ClarificationAnswerSummary {
                question: answer.question.clone(),
                answer: (!answer.answer.trim().is_empty()).then(|| answer.answer.clone()),
                note: (!answer.note.trim().is_empty()).then(|| answer.note.clone()),
            })
            .collect::<Vec<_>>();
        super::render_clarification_result(title, &answers, width, &self.theme)
    }

    fn render_permission_activities(&self, width: usize) -> Vec<Line<'static>> {
        let items = if self.permission_activities.is_empty() {
            vec![&self.item]
        } else {
            self.permission_activities.iter().collect::<Vec<_>>()
        };
        let Some(DisplayItem::PermissionActivity(common, payload)) = items.last().copied() else {
            return Vec::new();
        };
        let (label, marker_status) = match payload.lifecycle.as_str() {
            "approved" => ("Approved", "approved"),
            "denied" => ("Denied", "failed"),
            "superseded" => ("Superseded", "cancelled"),
            "cancelled" | "expired" => ("Cancelled", "cancelled"),
            _ => ("Awaiting approval", "running"),
        };
        let tool_name = if common.tool_name.trim().is_empty() {
            "tool"
        } else {
            common.tool_name.trim()
        };
        let mut lines = vec![Line::from(vec![
            Span::styled(
                crate::style::activity_marker(marker_status),
                crate::style::activity_marker_style(marker_status, &self.theme),
            ),
            Span::styled(
                label,
                Style::default()
                    .fg(self.theme.text_title)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::styled(" · ", Style::default().fg(self.theme.text_muted)),
            Span::styled(tool_name.to_string(), Style::default().fg(self.theme.text)),
            if items.len() > 1 {
                Span::styled(
                    format!(" ×{}", items.len()),
                    Style::default().fg(self.theme.text_muted),
                )
            } else {
                Span::raw(String::new())
            },
        ])];
        for (index, item) in items.iter().enumerate() {
            let DisplayItem::PermissionActivity(item_common, payload) = item else {
                continue;
            };
            let Some(detail) =
                permission_activity_detail(payload, item_common.tool_name == "exec_command")
            else {
                continue;
            };
            let line = Line::from(Span::styled(
                detail,
                Style::default().fg(self.theme.text_muted),
            ));
            let branch = if index + 1 == items.len() {
                "  └ "
            } else {
                "  ├ "
            };
            lines.extend(
                adaptive_wrap_line(
                    &line,
                    RtOptions::new(width.max(1))
                        .initial_indent(Line::from(branch.dim()))
                        .subsequent_indent(Line::from("    ")),
                )
                .iter()
                .map(line_to_static),
            );
        }
        lines
    }

    fn render_artifact_activity(
        &self,
        payload: &ArtifactActivityPayload,
        width: usize,
    ) -> Vec<Line<'static>> {
        let kind = if payload.artifact_kind.trim().is_empty() {
            "run artifact"
        } else {
            payload.artifact_kind.as_str()
        };
        let mut lines = vec![self.header(&format!("Stored {kind}"))];
        for artifact in &payload.artifacts {
            let path = if artifact.display_path.trim().is_empty() {
                artifact.path.as_str()
            } else {
                artifact.display_path.as_str()
            };
            let mut detail = path.to_string();
            if artifact.line_count > 0 {
                detail.push_str(&format!(" · {} lines", artifact.line_count));
            } else if artifact.byte_count > 0 {
                detail.push_str(&format!(" · {} bytes", artifact.byte_count));
            }
            lines.extend(
                self.prefixed_body_lines(
                    adaptive_wrap_line(
                        &Line::from(detail),
                        RtOptions::new(width.saturating_sub(4).max(1)),
                    )
                    .iter()
                    .map(line_to_static)
                    .collect(),
                ),
            );
        }
        lines
    }

    fn render_plan_update(&self, payload: &PlanPayload, width: usize) -> Vec<Line<'static>> {
        let render_note = |text: &str| -> Vec<Line<'static>> {
            let wrap_width = width.saturating_sub(4).max(1);
            let note = Line::from(text.to_string().dim().italic());
            adaptive_wrap_line(&note, RtOptions::new(wrap_width))
                .iter()
                .map(line_to_static)
                .collect()
        };

        let render_step = |status: &PlanStepStatus, text: &str| -> Vec<Line<'static>> {
            let (box_str, step_style) = match status {
                PlanStepStatus::Completed => ("✔ ", Style::default().crossed_out().dim()),
                PlanStepStatus::InProgress => ("□ ", Style::default().cyan().bold()),
                PlanStepStatus::Pending => ("□ ", Style::default().dim()),
            };
            let options = RtOptions::new(width.saturating_sub(4).max(1))
                .initial_indent(Line::from(box_str))
                .subsequent_indent(Line::from("  "));
            let step = Line::from(Span::styled(text.to_string(), step_style));
            adaptive_wrap_line(&step, options)
                .iter()
                .map(line_to_static)
                .collect()
        };

        let mut lines = vec![self.header("Updated Plan")];
        let mut body = Vec::new();
        let note = payload.explanation.trim();
        if !note.is_empty() {
            body.extend(render_note(note));
        }
        if payload.plan.is_empty() {
            body.push(Line::from("(no steps provided)".dim().italic()));
        } else {
            for item in &payload.plan {
                body.extend(render_step(&item.status, &item.step));
            }
        }
        for (index, line) in body.into_iter().enumerate() {
            let mut spans = vec![if index == 0 {
                "  └ ".dim()
            } else {
                "    ".into()
            }];
            spans.extend(line.spans);
            lines.push(Line::from(spans));
        }
        lines
    }

    fn render_stage(&self, width: usize) -> Vec<Line<'static>> {
        let DisplayItem::Stage(common, payload) = &self.item else {
            return Vec::new();
        };
        let label = crate::display_item::canonical_stage_label(
            &payload.stage_label,
            payload.stage_ordinal,
            &payload.stage,
        )
        .replace('_', " ");
        let terminal = common.status != "running";
        if terminal {
            let workflow_action = if payload.resolved_workflow_action == "forced_forward"
                && payload.requested_workflow_action == "revisit"
                && payload.workflow_action_reason == "revisit_limit"
            {
                "forced_forward_revisit_limit"
            } else if !payload.resolved_workflow_action.trim().is_empty() {
                payload.resolved_workflow_action.trim()
            } else if !payload.requested_workflow_action.trim().is_empty() {
                payload.requested_workflow_action.trim()
            } else {
                ""
            };
            let decision = if !workflow_action.is_empty() {
                workflow_action
            } else if !payload.gate_decision.trim().is_empty() {
                payload.gate_decision.trim()
            } else if payload.decision.trim().is_empty() {
                if payload.status.trim().is_empty() {
                    payload.event.trim()
                } else {
                    payload.status.trim()
                }
            } else {
                payload.decision.trim()
            };
            if matches!(decision, "clarify_human" | "paused_for_human" | "paused") {
                return Vec::new();
            }
            return super::render_stage_outcome_banner(
                &super::StageOutcomeBannerModel {
                    label,
                    decision: decision.to_string(),
                    facts: stage_outcome_facts(payload, workflow_action),
                },
                width,
                &self.theme,
            );
        }
        super::render_stage_chapter_banner(
            &super::StageBannerModel {
                ordinal: payload
                    .stage_ordinal
                    .filter(|value| *value > 0)
                    .map(|value| format!("{value:02}")),
                label,
                attempt: u32::try_from(payload.attempt)
                    .ok()
                    .filter(|value| *value > 0),
                resumed: payload.event == "resumed",
                contract: payload.contract.clone(),
                expected_outputs: payload.expected_outputs.clone(),
                carry_in_review_record_ids: payload.carry_in_review_record_ids.clone(),
            },
            width,
            &self.theme,
        )
    }
}

fn stage_outcome_facts(payload: &StagePayload, workflow_action: &str) -> Vec<(String, String)> {
    let mut facts = Vec::new();
    if payload.result_updated {
        facts.push((
            "Update".into(),
            "Stage result changed; previous result retained in history.".into(),
        ));
    }
    let has_structured_gate_context = !payload.reason_code.trim().is_empty()
        || !payload.basis_review_record_id.trim().is_empty()
        || !payload.residual_review_record_id.trim().is_empty()
        || payload.residual_finding_count > 0;
    if !payload.gate_decision.trim().is_empty() {
        facts.push((
            "Gate".to_string(),
            payload
                .gate_decision
                .trim()
                .replace('_', " ")
                .to_uppercase(),
        ));
    }
    if !payload.basis_review_record_id.trim().is_empty() {
        let role = if payload.basis_review_kind.trim().is_empty() {
            "review".to_string()
        } else {
            payload.basis_review_kind.trim().replace('_', " ")
        };
        let round = if payload.basis_review_round > 0 {
            format!(" · {role} round {}", payload.basis_review_round)
        } else {
            format!(" · {role}")
        };
        facts.push((
            "Basis".to_string(),
            format!("{}{}", payload.basis_review_record_id.trim(), round),
        ));
    }
    if has_structured_gate_context {
        facts.push((
            "Active findings".to_string(),
            payload.active_finding_count.to_string(),
        ));
    }
    if !payload.residual_review_record_id.trim().is_empty() || payload.residual_finding_count > 0 {
        let residual = if payload.residual_review_record_id.trim().is_empty() {
            payload.residual_finding_count.to_string()
        } else {
            format!(
                "{} · {}",
                payload.residual_finding_count,
                payload.residual_review_record_id.trim()
            )
        };
        facts.push(("Residual findings".to_string(), residual));
    }
    if has_structured_gate_context && !payload.next_stage.trim().is_empty() {
        facts.push((
            "Next stage".to_string(),
            payload.next_stage.trim().replace('_', " "),
        ));
    }
    if !workflow_action.is_empty() {
        facts.push((
            "Workflow".to_string(),
            workflow_action.replace('_', " ").to_uppercase(),
        ));
    }
    if !payload.reason_display.trim().is_empty() {
        facts.push((
            "Reason".to_string(),
            payload.reason_display.trim().to_string(),
        ));
    } else if !payload.reason_code.trim().is_empty() {
        // Unknown structured reason codes remain available in the transcript
        // for diagnostics, but machine vocabulary must not leak into the
        // primary user-facing outcome card.
    } else if !(payload.reason.trim().is_empty()
        || payload.workflow_action_reason.trim().is_empty()
            && payload.reason.trim() == payload.transcript.trim())
    {
        facts.push(("Reason".to_string(), payload.reason.trim().to_string()));
    } else if !payload.workflow_action_reason.trim().is_empty() {
        facts.push((
            "Reason".to_string(),
            payload.workflow_action_reason.trim().to_string(),
        ));
    }
    facts
}

fn compact_json_detail(value: &str, max_width: usize) -> String {
    if !value.ends_with(".json") || value.chars().count() <= max_width || max_width < 8 {
        return value.to_string();
    }
    let suffix_width = max_width.saturating_sub(1) / 2;
    let prefix_width = max_width.saturating_sub(1 + suffix_width);
    let prefix = value.chars().take(prefix_width).collect::<String>();
    let suffix = value
        .chars()
        .rev()
        .take(suffix_width)
        .collect::<String>()
        .chars()
        .rev()
        .collect::<String>();
    format!("{prefix}…{suffix}")
}

fn visible_tool_duration_label(duration_ms: u64) -> Option<String> {
    (duration_ms >= 100).then(|| crate::style::turn_elapsed_label(duration_ms))
}

fn exploration_action_detail(
    common: &DisplayItemCommon,
    payload: &CommandExecutionPayload,
) -> (String, String) {
    let action = match common.action.as_str() {
        "read" => "Read",
        "search" => "Search",
        "list" => "List",
        "check" => "Checked",
        _ => "Run",
    };
    let structured = match common.action.as_str() {
        "read" | "list" | "check" if !payload.target.trim().is_empty() => {
            if payload.display_target.trim().is_empty() {
                user_facing_exploration_target(&payload.target)
            } else {
                payload.display_target.trim().to_string()
            }
        }
        "search" if !payload.query.trim().is_empty() => {
            let query = serde_json::to_string(&payload.query)
                .unwrap_or_else(|_| format!("\"{}\"", payload.query));
            if payload.target.trim().is_empty() {
                query
            } else if payload.target.trim() == "tools" {
                return (
                    "Checked".to_string(),
                    format!("available tools for {query}"),
                );
            } else {
                format!(
                    "{query} in {}",
                    user_facing_exploration_target(&payload.target)
                )
            }
        }
        _ => String::new(),
    };
    if !structured.is_empty() {
        let detail = if common.action == "check" {
            match payload.result_state.as_str() {
                "not_found" => format!("{structured} · not found"),
                "found" => format!("{structured} · found"),
                "failed" => format!("{structured} · failed"),
                _ => structured,
            }
        } else {
            structured
        };
        return (action.to_string(), detail);
    }
    let command = payload.command.trim();
    let detail = command
        .strip_prefix(action)
        .map(str::trim_start)
        .filter(|value| !value.is_empty())
        .unwrap_or(command)
        .to_string();
    (action.to_string(), detail)
}

fn user_facing_exploration_target(value: &str) -> String {
    let trimmed = value.trim();
    if trimmed.starts_with("result:call_") || trimmed.contains("/tool_results/") {
        "tool result".to_string()
    } else {
        trimmed.to_string()
    }
}

fn preferred_error(summary: &str, error: &str) -> String {
    if summary.trim().is_empty() {
        compact_legacy_error(error)
    } else {
        summary.trim().to_string()
    }
}

fn item_error_summary(item: &DisplayItem) -> Option<String> {
    let DisplayItem::CommandExecution(_, payload) = item else {
        return None;
    };
    let error = preferred_error(&payload.error_summary, &payload.error);
    (!error.trim().is_empty()).then_some(error)
}

fn compact_legacy_error(value: &str) -> String {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return String::new();
    }
    let lowered = trimmed.to_ascii_lowercase();
    if lowered.contains("timed out")
        || lowered.contains("timeout")
        || lowered.contains("curl: (28)")
    {
        if let Some(after) = lowered.split("after ").nth(1) {
            let mut words = after.split_whitespace();
            if let (Some(count), Some(unit)) = (words.next(), words.next()) {
                // A curl duration is not a retry count. Preserve only units
                // explicitly present in the original error.
                if count.parse::<u64>().is_ok() {
                    let unit = unit.trim_matches(|ch: char| !ch.is_ascii_alphabetic());
                    let label = match unit {
                        "millisecond" | "milliseconds" | "ms" => Some("ms"),
                        "second" | "seconds" | "sec" | "secs" | "s" => Some("s"),
                        "minute" | "minutes" | "min" | "mins" => Some("min"),
                        "attempt" | "attempts" => Some("attempts"),
                        "retry" | "retries" => Some("retries"),
                        _ => None,
                    };
                    if let Some(label) = label {
                        return format!("Timed out after {count} {label}");
                    }
                }
            }
        }
        return "Timed out".to_string();
    }
    if let Some(index) = lowered.find("http ") {
        let status = trimmed[index + 5..].chars().take(3).collect::<String>();
        if status.chars().all(|ch| ch.is_ascii_digit()) {
            return if status == "429" || lowered.contains("rate limit") {
                format!("HTTP {status} · rate limited")
            } else {
                format!("HTTP {status}")
            };
        }
    }
    let first_line = trimmed
        .lines()
        .find(|line| !line.trim().is_empty())
        .unwrap_or("Tool failed");
    let compact = first_line.split_whitespace().collect::<Vec<_>>().join(" ");
    if compact.chars().count() <= 180 {
        compact
    } else {
        format!(
            "{}...",
            compact.chars().take(177).collect::<String>().trim_end()
        )
    }
}

fn is_exploration_item(item: &DisplayItem) -> bool {
    matches!(
        item,
        DisplayItem::CommandExecution(common, _)
            if matches!(common.action.as_str(), "read" | "search" | "list" | "check")
    )
}

fn aggregation_key_for_item(item: &DisplayItem) -> Option<&str> {
    match item {
        DisplayItem::CommandExecution(_, payload) if !payload.aggregation_key.trim().is_empty() => {
            Some(payload.aggregation_key.as_str())
        }
        _ => None,
    }
}

fn aggregation_batch_for_item(item: &DisplayItem) -> Option<&str> {
    let batch = item.common().activity_group.aggregation_batch.trim();
    (!batch.is_empty()).then_some(batch)
}

fn semantic_group_state_for_item(item: &DisplayItem) -> Option<&str> {
    let state = item.common().activity_group.semantic_group_state.trim();
    (!state.is_empty()).then_some(state)
}

fn aggregation_batches_compatible(previous: &DisplayItem, next: &DisplayItem) -> bool {
    match (
        aggregation_batch_for_item(previous),
        aggregation_batch_for_item(next),
    ) {
        (Some(previous), Some(next)) => previous == next,
        // Historical records have no batch. Preserve their former adjacent
        // aggregation behavior without allowing a new batched item to merge
        // into an old unbatched group.
        (None, None) => true,
        _ => false,
    }
}

fn normalize_status_token(value: &str) -> String {
    value
        .chars()
        .filter(|ch| ch.is_alphanumeric())
        .flat_map(char::to_lowercase)
        .collect()
}

fn pdf_read_scope(item: &DisplayItem) -> (usize, Vec<(u32, u32)>, Option<u32>) {
    let DisplayItem::CommandExecution(common, payload) = item else {
        return (0, Vec::new(), None);
    };
    if common.tool_name != "pdf_reader" {
        return (0, Vec::new(), None);
    }
    if payload.page_selection == "all" {
        return (1, Vec::new(), payload.document_page_count);
    }
    (
        0,
        normalize_page_ranges(payload.page_ranges.iter().copied()),
        payload.document_page_count,
    )
}

fn command_read_windows(item: &DisplayItem) -> Vec<crate::display_item::ReadWindowPayload> {
    match item {
        DisplayItem::CommandExecution(_, payload) => payload.read_windows.clone(),
        _ => Vec::new(),
    }
}

fn format_read_windows(windows: &[crate::display_item::ReadWindowPayload]) -> String {
    windows
        .iter()
        .map(|window| {
            if window.start_line == window.end_line {
                window.start_line.to_string()
            } else {
                format!("{}-{}", window.start_line, window.end_line)
            }
        })
        .collect::<Vec<_>>()
        .join(", ")
}

fn normalize_read_windows(
    windows: impl Iterator<Item = crate::display_item::ReadWindowPayload>,
) -> Vec<crate::display_item::ReadWindowPayload> {
    let mut normalized = windows.collect::<Vec<_>>();
    normalized.sort_by_key(|window| (window.start_line, window.end_line));
    normalized.dedup_by(|current, previous| {
        current.start_line == previous.start_line
            && current.end_line == previous.end_line
            && current.total_lines == previous.total_lines
    });
    normalized
}

fn paper_resource_key_for_item(item: &DisplayItem) -> Option<&str> {
    match item {
        DisplayItem::CommandExecution(common, payload)
            if common.tool_name == "paper_fetch"
                && common.status == "completed"
                && !payload.aggregation_key.trim().is_empty() =>
        {
            Some(payload.aggregation_key.as_str())
        }
        _ => None,
    }
}

fn merge_page_ranges(previous: &DisplayItem, next: &DisplayItem) -> DisplayItem {
    let mut merged = next.clone();
    let (
        DisplayItem::CommandExecution(_, previous_payload),
        DisplayItem::CommandExecution(_, next_payload),
    ) = (previous, &mut merged)
    else {
        return merged;
    };
    next_payload.page_ranges = normalize_page_ranges(
        previous_payload
            .page_ranges
            .iter()
            .chain(next_payload.page_ranges.iter())
            .copied(),
    );
    merged
}

fn normalize_page_ranges(ranges: impl IntoIterator<Item = (u32, u32)>) -> Vec<(u32, u32)> {
    let mut ranges = ranges
        .into_iter()
        .filter(|(start, end)| *start > 0 && end >= start)
        .collect::<Vec<_>>();
    ranges.sort_unstable();
    let mut merged: Vec<(u32, u32)> = Vec::new();
    for (start, end) in ranges {
        if let Some((_, previous_end)) = merged.last_mut() {
            if start <= previous_end.saturating_add(1) {
                *previous_end = (*previous_end).max(end);
                continue;
            }
        }
        merged.push((start, end));
    }
    merged
}

fn format_page_ranges(ranges: &[(u32, u32)]) -> String {
    normalize_page_ranges(ranges.iter().copied())
        .into_iter()
        .map(|(start, end)| {
            if start == end {
                start.to_string()
            } else {
                format!("{start}-{end}")
            }
        })
        .collect::<Vec<_>>()
        .join(", ")
}

fn provider_retry_context(payload: &ModelActivityPayload) -> Option<String> {
    let identity = format!("{} {}", payload.component, payload.operation).to_ascii_lowercase();
    let authoring_turn = identity.contains("task_spec_authoring")
        || identity.contains("author_task_spec")
        || identity.contains("stage_selection")
        || identity.contains("select_workflow_stages")
        || identity.contains("stage_semantic_preparation")
        || identity.contains("prepare_stage_semantics");
    let stage = if identity.contains("stage_selection")
        || identity.contains("select_workflow_stages")
    {
        "Workflow selection".to_string()
    } else if identity.contains("task_spec_authoring") || identity.contains("author_task_spec") {
        "Task specification".to_string()
    } else if identity.contains("stage_semantic_preparation")
        || identity.contains("prepare_stage_semantics")
    {
        let stage = sentence_case_label(&payload.stage);
        if stage.is_empty() || stage.eq_ignore_ascii_case("stage semantic preparation") {
            "Stage semantics".to_string()
        } else {
            format!("{stage} semantics")
        }
    } else {
        sentence_case_label(&payload.stage)
    };
    let mut parts = Vec::new();
    if !stage.is_empty() {
        parts.push(stage);
    }
    if payload.attempt > 0 {
        parts.push(format!("attempt {}", payload.attempt));
    }
    if payload.continuation_index > 0 {
        parts.push(format!("continuation {}", payload.continuation_index));
    }
    if payload.turn_index > 0 {
        parts.push(format!(
            "{} {}",
            if authoring_turn {
                "authoring turn"
            } else {
                "Core round"
            },
            payload.turn_index
        ));
    }
    (!parts.is_empty()).then(|| parts.join(" · "))
}

fn sentence_case_label(value: &str) -> String {
    let normalized = value.trim().replace('_', " ");
    let mut chars = normalized.chars();
    let Some(first) = chars.next() else {
        return String::new();
    };
    first.to_uppercase().chain(chars).collect()
}

fn nonempty_transcript(item: &DisplayItem) -> Option<String> {
    let transcript = item_transcript(item);
    (!transcript.trim().is_empty()).then_some(transcript)
}

fn is_stage_append_item(item: &DisplayItem) -> bool {
    matches!(
        item,
        DisplayItem::CommandExecution(common, _)
            if common.action == "stage_control" && common.tool_name == "append_stage"
    )
}

fn item_transcript(item: &DisplayItem) -> String {
    match item {
        DisplayItem::BatchOutcome(_, payload) => {
            let mut lines = vec![format!(
                "Batch not run: {}; batch: {}; reason_code: {}; reason: {}",
                payload.execution_state, payload.batch_id, payload.reason_code, payload.reason
            )];
            if !payload.diagnostic.trim().is_empty() && payload.diagnostic != payload.reason {
                lines.push(format!("diagnostic: {}", payload.diagnostic));
            }
            if !payload.recovery.trim().is_empty() {
                lines.push(format!("recovery: {}", payload.recovery));
            }
            lines.extend(payload.calls.iter().map(|call| {
                format!(
                    "- call_id={} tool={} action={} target={} display_target={} qualifiers={}",
                    call.call_id,
                    call.tool_name,
                    call.action,
                    call.target,
                    call.display_target,
                    call.qualifiers.join(" | ")
                )
            }));
            lines.join("\n")
        }
        DisplayItem::CommandExecution(common, payload) => {
            let transcript = if common.tool_name == "paper_fetch"
                || matches!(common.action.as_str(), "stage_control" | "execution_wait")
            {
                semantic_tool_audit_transcript(payload)
            } else {
                command_audit_transcript(payload)
            };
            tool_audit_transcript(common, &transcript)
        }
        DisplayItem::FileChange(common, payload) => {
            tool_audit_transcript(common, &payload.transcript)
        }
        DisplayItem::McpToolCall(common, payload) => {
            tool_audit_transcript(common, &payload.transcript)
        }
        DisplayItem::WebSearch(common, payload) => {
            tool_audit_transcript(common, &payload.transcript)
        }
        DisplayItem::ImageView(common, payload) | DisplayItem::ImageGeneration(common, payload) => {
            tool_audit_transcript(common, &payload.transcript)
        }
        DisplayItem::PlanUpdate(_, payload) => plan_update_transcript(payload),
        DisplayItem::BackgroundTerminalInteraction(common, payload) => {
            tool_audit_transcript(common, &payload.transcript)
        }
        DisplayItem::SkillActivity(common, payload) => {
            tool_audit_transcript(common, &payload.transcript)
        }
        DisplayItem::Reasoning(_, payload) => payload.transcript.clone(),
        DisplayItem::AgentMessage(_, payload)
        | DisplayItem::ProposedPlan(_, payload)
        | DisplayItem::ContextCompaction(_, payload)
        | DisplayItem::SubAgentActivity(_, payload)
        | DisplayItem::Review(_, payload)
        | DisplayItem::LlmRequestFailure(_, payload) => payload.transcript.clone(),
        DisplayItem::ModelActivity(_, payload) => payload.transcript.clone(),
        DisplayItem::RouteDecision(_, payload) => payload.transcript.clone(),
        DisplayItem::TaskHeader(_, payload) => payload.transcript.clone(),
        DisplayItem::TaskOutcome(_, payload) => {
            let artifacts = payload
                .primary_artifacts
                .iter()
                .map(|artifact| artifact.path.as_str())
                .collect::<Vec<_>>()
                .join(", ");
            let caveats = payload.caveats.join("; ");
            let mut text = format!(
                "Task outcome: {}; delivery: {}; artifacts: {}; caveats: {}; failure: {} {}; recovery: {}",
                payload.outcome,
                payload.delivery_status,
                artifacts,
                caveats,
                payload.failure_code,
                payload.failure_summary,
                payload.recovery_actions.join("; ")
            );
            text.push_str(&format!(
                "\n{}\nRuntime rationale: {}\nOriginal final text: {}",
                payload.delivery_summary,
                payload.runtime_rationale.join("; "),
                payload.raw_final_text
            ));
            for (label, value) in &payload.failure_facts {
                text.push_str(&format!("\n{label}: {value}"));
            }
            if !payload.failure_cause.is_empty() {
                text.push_str(&format!("\nCause: {}", payload.failure_cause));
            }
            if !payload.failure_detail.is_empty() {
                text.push_str(&format!(
                    "\nFull failure diagnostics:\n{}",
                    payload.failure_detail
                ));
            }
            for warning in &payload.delivery_warnings {
                text.push_str(&format!(
                    "\nDiagnostic: {} | {} | {} | {} | {}",
                    warning.code,
                    warning.path,
                    warning.request_id,
                    warning.source_kind,
                    warning.source_tool
                ));
            }
            text
        }
        DisplayItem::ActionRequired(_, payload) => format!(
            "Action required: {}; reason: {}; run: {}",
            payload.detail, payload.reason_code, payload.run_dir
        ),
        DisplayItem::ReviewCycle(_, payload) => review_cycle_transcript(payload),
        DisplayItem::ProtocolActivity(_, payload) => format!(
            "Protocol activity: {}; outcome: {}; required actions: {}; {}",
            payload.status_code,
            payload.outcome,
            payload.required_actions.join(", "),
            payload.summary
        ),
        DisplayItem::Heartbeat(_, payload) => payload.transcript.clone(),
        DisplayItem::Stage(_, payload) => payload.transcript.clone(),
        DisplayItem::InteractionResult(_, payload) => interaction_result_transcript(payload),
        DisplayItem::PermissionActivity(common, payload) => format!(
            "Permission {}: {}\n{}",
            payload.lifecycle,
            common.tool_name,
            permission_activity_detail(payload, false).unwrap_or_default()
        ),
        DisplayItem::ArtifactActivity(_, _) => String::new(),
    }
}

fn permission_activity_detail(
    payload: &PermissionActivityPayload,
    compact_command: bool,
) -> Option<String> {
    if !payload.subject.primary.trim().is_empty() {
        let primary = payload.subject.primary.trim();
        let line_count = primary.lines().count();
        let summary = if compact_command && line_count > 1 {
            format!(
                "{} … +{} command lines (ctrl + t)",
                primary.lines().next().unwrap_or_default(),
                line_count - 1
            )
        } else {
            primary.to_string()
        };
        let mut parts = vec![summary];
        if !payload.subject.secondary.trim().is_empty() {
            parts.push(payload.subject.secondary.trim().to_string());
        }
        parts.extend(
            payload
                .subject
                .qualifiers
                .iter()
                .map(|value| value.trim())
                .filter(|value| !value.is_empty())
                .map(str::to_string),
        );
        return Some(parts.join(" · "));
    }
    payload
        .file_changes
        .first()
        .map(|change| {
            if change.destination.trim().is_empty() {
                change.path.clone()
            } else {
                format!("{} → {}", change.path, change.destination)
            }
        })
        .filter(|value| !value.trim().is_empty())
        .or_else(|| {
            (!payload.request_summary.trim().is_empty())
                .then(|| payload.request_summary.trim().to_string())
        })
        .or_else(|| {
            (!payload.decision.trim().is_empty()).then(|| payload.decision.trim().to_string())
        })
}

fn interaction_result_transcript(payload: &InteractionResultPayload) -> String {
    let title = if payload.title.trim().is_empty() {
        if payload.interaction_kind == "permission_request" {
            "Permission"
        } else {
            "Human question"
        }
    } else {
        payload.title.trim()
    };
    let mut lines = vec![format!("{}{}", crate::style::GLYPH_ACTION, title)];
    if payload.interaction_kind == "permission_request" || !payload.decision.trim().is_empty() {
        if !payload.request_summary.trim().is_empty() {
            lines.push(format!("  └ {}", payload.request_summary.trim()));
        }
        for change in &payload.file_changes {
            let target = if change.destination.trim().is_empty() {
                change.path.clone()
            } else {
                format!("{} → {}", change.path, change.destination)
            };
            let kind = if change.change_kind.trim().is_empty() {
                "modify"
            } else {
                change.change_kind.trim()
            };
            lines.push(format!("  ◇ {kind} · {target}"));
        }
        if !payload.decision.trim().is_empty() {
            lines.push(format!("  ↳ {}", payload.decision.trim()));
        }
        return lines.join("\n");
    }
    for answer in &payload.answers {
        lines.push(format!("  • {}", answer.question));
        if !answer.answer.trim().is_empty() {
            lines.push(format!("    answer: {}", answer.answer));
        } else if answer.note.trim().is_empty() {
            lines.push("    answer: (unanswered)".to_string());
        }
        if !answer.note.trim().is_empty() {
            lines.push(format!("    note: {}", answer.note));
        }
    }
    if !payload.detail.trim().is_empty() {
        lines.push(format!("  └ {}", payload.detail.trim()));
    }
    if !payload.resume_hint.trim().is_empty() {
        lines.push(format!("  ↳ {}", payload.resume_hint.trim()));
    }
    lines.join("\n")
}

fn semantic_tool_audit_transcript(payload: &CommandExecutionPayload) -> String {
    let mut lines = Vec::new();
    if !payload.command.trim().is_empty() {
        lines.push(payload.command.trim().to_string());
    }
    if !payload.transcript.trim().is_empty() && payload.transcript.trim() != payload.command.trim()
    {
        lines.push(payload.transcript.trim().to_string());
    }
    lines.extend(payload.metadata.iter().map(|(label, value)| {
        if label.trim().is_empty() {
            value.trim().to_string()
        } else {
            format!("{} {}", label.trim(), value.trim())
        }
    }));
    let error = preferred_error(&payload.error_summary, &payload.error);
    let existing = lines.join("\n");
    if !error.trim().is_empty() && !existing.contains(error.trim()) {
        lines.push(format!("Error: {}", error.trim()));
    }
    lines.join("\n")
}

fn tool_audit_transcript(common: &DisplayItemCommon, transcript: &str) -> String {
    let identity = if common.call_id.trim().is_empty() {
        common.item_id.trim()
    } else {
        common.call_id.trim()
    };
    match (identity.is_empty(), transcript.trim().is_empty()) {
        (false, false) => format!("Call ID: {identity}\n{}", transcript.trim()),
        (false, true) => format!("Call ID: {identity}"),
        (true, false) => transcript.trim().to_string(),
        (true, true) => String::new(),
    }
}

fn command_audit_transcript(payload: &CommandExecutionPayload) -> String {
    let mut lines = Vec::new();
    let command = if !payload.effective_command.trim().is_empty() {
        payload.effective_command.trim()
    } else {
        payload.command.trim()
    };
    if !command.is_empty() {
        lines.push(format!("$ {command}"));
    }
    if !payload.transcript.trim().is_empty() {
        lines.push(payload.transcript.trim().to_string());
    } else {
        lines.extend(
            payload
                .output
                .iter()
                .map(|line| line.trim_end().to_string()),
        );
    }
    lines.extend(payload.metadata.iter().map(|(label, value)| {
        if label.trim().is_empty() {
            value.trim().to_string()
        } else {
            format!("{} {}", label.trim(), value.trim())
        }
    }));
    let error = preferred_error(&payload.error_summary, &payload.error);
    let existing = lines.join("\n");
    if !error.trim().is_empty() && !existing.contains(error.trim()) {
        lines.push(format!("Error: {}", error.trim()));
    }
    if let Some(exit_code) = payload.exit_code {
        let status = if exit_code == 0 { "✓" } else { "✗" };
        let duration = match payload.duration_ms {
            Some(duration_ms) => visible_tool_duration_label(duration_ms)
                .map(|value| format!(" · {value}"))
                .unwrap_or_default(),
            None => " · unknown".to_string(),
        };
        lines.push(format!("{status} exit {exit_code}{duration}"));
    }
    lines.join("\n")
}

fn preferred_command_error(payload: &CommandExecutionPayload) -> String {
    preferred_error(&payload.error_summary, &payload.error)
}

fn review_cycle_transcript(payload: &ReviewCyclePayload) -> String {
    let kind = if payload.review_kind.is_empty() {
        "review"
    } else {
        payload.review_kind.as_str()
    };
    let mut text = format!(
        "Review cycle · {kind} · Round {} · {}",
        payload.round, payload.phase
    );
    if payload.phase == "failed" || payload.outcome == "failed" {
        text.push_str(&format!(
            "\nCause: {}\nDetail: {}",
            payload.failure_cause, payload.failure_detail
        ));
        let heading = if payload.failure_domain == "provider_transport" {
            "Provider error:"
        } else {
            "Protocol error:"
        };
        text.push_str(&format!("\n{heading}\n{}", payload.transcript));
    }
    if !payload.review_target.is_empty() {
        text.push_str(&format!("\nRecorded target: {}", payload.review_target));
    }
    if !payload.review_record_id.trim().is_empty() {
        text.push_str(&format!("\nRecord: {}", payload.review_record_id.trim()));
    }
    if !payload.gate_disposition.trim().is_empty() {
        text.push_str(&format!(
            "\nGate disposition: {}",
            payload.gate_disposition.trim()
        ));
    }
    if !payload.gate_decision.trim().is_empty() {
        text.push_str(&format!(
            "\nGate decision: {}",
            payload.gate_decision.trim()
        ));
    }
    if payload.feedback_format == "structured_v2" && !payload.findings.is_empty() {
        for finding in &payload.findings {
            text.push_str(&format!(
                "\n\n[{}] {} - {}\n\n{}\n\nRemediation: {}",
                finding.severity,
                finding.title,
                finding.evidence_refs.join("; "),
                finding.description,
                finding.remediation,
            ));
        }
        if !payload.overall_assessment.is_empty() {
            text.push_str("\n\n## Overall Assessment\n\n");
            text.push_str(&payload.overall_assessment);
        }
    } else if !payload.summary.is_empty() {
        text.push('\n');
        text.push_str(&payload.summary);
    }
    if payload.findings.is_empty() && !payload.overall_assessment.trim().is_empty() {
        text.push_str("\n\nOverall Assessment\n");
        text.push_str(&payload.overall_assessment);
    }
    text
}

fn plan_update_transcript(payload: &PlanPayload) -> String {
    let mut lines = vec!["Updated Plan".to_string()];
    if !payload.explanation.trim().is_empty() {
        lines.push(payload.explanation.trim().to_string());
    }
    for item in &payload.plan {
        let marker = match item.status {
            PlanStepStatus::Completed => "[x]",
            PlanStepStatus::InProgress => "[ ] (in progress)",
            PlanStepStatus::Pending => "[ ]",
        };
        lines.push(format!("{marker} {}", item.step));
    }
    lines.join("\n")
}

impl TypedThreadItemHistoryCell {
    fn review_transcript_hyperlinks(
        &self,
        payload: &ReviewCyclePayload,
        width: usize,
    ) -> Vec<crate::terminal_hyperlinks::HyperlinkLine> {
        use super::review_cycle::{
            render_embedded_review_hyperlinks_with_sources_detail,
            render_review_cycle_card_hyperlinks_with_sources_detail, ReviewDetail,
        };
        let render = if self.audit_context().is_some() {
            render_embedded_review_hyperlinks_with_sources_detail
        } else {
            render_review_cycle_card_hyperlinks_with_sources_detail
        };
        let mut lines = render(
            payload,
            width,
            &self.theme,
            self.cwd.as_deref(),
            &self.citation_sources,
            ReviewDetail::Full,
        );
        lines.push(crate::terminal_hyperlinks::HyperlinkLine::default());
        let mut details = vec![
            "── Recorded details".into(),
            format!("Record: {}", payload.review_record_id),
            format!("Recorded target: {}", payload.review_target),
            format!("Feedback hash: {}", payload.feedback_content_hash),
            format!("Source sequence: {}", payload.source_sequence),
        ];
        for finding in &payload.findings {
            for reference in &finding.evidence_refs {
                details.push(format!("Evidence: {reference}"));
            }
        }
        if !payload.transcript.is_empty() {
            details.push(payload.transcript.clone());
        }
        let details = details
            .iter()
            .flat_map(|value| value.lines())
            .flat_map(|source| {
                adaptive_wrap_line(
                    &Line::from(source.to_string()),
                    RtOptions::new(width.max(1)),
                )
                .iter()
                .map(line_to_static)
                .collect::<Vec<_>>()
            })
            .collect();
        lines.extend(crate::terminal_hyperlinks::annotate_web_urls(details));
        lines
    }

    fn display_content_lines(&self, width: usize) -> Vec<Line<'static>> {
        if let Some(patch) = self.patch_cell() {
            return patch.display_lines(width, &self.theme);
        }
        if self.item.common().receipt_state == "not_run" {
            let common = self.item.common();
            let text = format!(
                "Not run · {}\nOperation {} before execution.",
                common.tool_name,
                if common.not_run_reason == "denied" {
                    "declined"
                } else {
                    "cancelled"
                }
            );
            return text
                .lines()
                .flat_map(|line| {
                    crate::wrapping::adaptive_wrap_line(
                        &Line::from(line.to_string()),
                        crate::wrapping::RtOptions::new(width.max(1)),
                    )
                    .iter()
                    .map(crate::wrapping::line_to_static)
                    .collect::<Vec<_>>()
                })
                .collect();
        }
        if self.audit_context().is_some() {
            match &self.item {
                DisplayItem::ReviewCycle(_, _) => {
                    return crate::terminal_hyperlinks::visible_lines(
                        self.display_hyperlink_lines(width),
                    );
                }
                DisplayItem::AgentMessage(_, payload) => {
                    return super::render_agent_response_with_cwd(
                        &payload.text,
                        width,
                        &self.theme,
                        None,
                    );
                }
                _ => {}
            }
        }
        let mut lines = match self.item {
            DisplayItem::CommandExecution(_, _) => self.render_command(width),
            DisplayItem::FileChange(_, _) => Vec::new(),
            DisplayItem::McpToolCall(_, _) => self.render_mcp(),
            DisplayItem::WebSearch(_, _) => self.render_web(width),
            DisplayItem::Stage(_, _) => self.render_stage(width),
            _ => self.render_other(width),
        };
        if !self.item.common().correction_of.is_empty() {
            lines.insert(0, Line::from("Correction to earlier tool result".dim()));
        }
        lines
    }
}

impl HistoryCell for TypedThreadItemHistoryCell {
    fn display_hyperlink_lines(
        &self,
        width: usize,
    ) -> Vec<crate::terminal_hyperlinks::HyperlinkLine> {
        if let DisplayItem::TaskOutcome(_, payload) = &self.item {
            if payload.request_index > 0 {
                return self.direct_outcome_lines(payload, width);
            }
        }
        if let DisplayItem::ReviewCycle(_, payload) = &self.item {
            if self.audit_context().is_some() {
                return super::review_cycle::render_embedded_review_hyperlinks_with_sources(
                    payload,
                    width,
                    &self.theme,
                    self.cwd.as_deref(),
                    &self.citation_sources,
                );
            }
            return super::review_cycle::render_review_cycle_card_hyperlinks_with_sources(
                payload,
                width,
                &self.theme,
                self.cwd.as_deref(),
                &self.citation_sources,
            );
        }
        if let DisplayItem::AgentMessage(_, payload) = &self.item {
            if self.audit_context().is_some() {
                return super::messages::render_agent_markdown_hyperlinks(
                    &payload.text,
                    width,
                    &self.theme,
                    None,
                    crate::style::activity_marker_style("completed", &self.theme),
                );
            }
        }
        crate::terminal_hyperlinks::annotate_web_urls(self.display_lines(width))
    }

    fn transcript_hyperlink_lines(
        &self,
        width: usize,
    ) -> Vec<crate::terminal_hyperlinks::HyperlinkLine> {
        if let DisplayItem::ReviewCycle(_, payload) = &self.item {
            return self.review_transcript_hyperlinks(payload, width);
        }
        crate::terminal_hyperlinks::annotate_web_urls(self.transcript_lines(width))
    }
    fn audit_context(&self) -> Option<&crate::display_item::AuditContext> {
        self.item.common().audit_context.as_ref()
    }

    fn audit_complete(&self) -> bool {
        matches!(&self.item, DisplayItem::ReviewCycle(common, payload)
            if common.status != "running" && matches!(payload.phase.as_str(), "feedback_received" | "completed" | "accepted" | "failed" | "residual" | "cancelled" | "canceled" | "interrupted"))
    }

    fn audit_terminal_label(&self) -> Option<&'static str> {
        let DisplayItem::ReviewCycle(common, payload) = &self.item else {
            return None;
        };
        if common.status == "running" {
            return None;
        }
        if payload.phase == "failed" || payload.outcome == "failed" || common.status == "failed" {
            return Some("Review failed");
        }
        if matches!(
            payload.phase.as_str(),
            "cancelled" | "canceled" | "interrupted"
        ) || matches!(common.status.as_str(), "cancelled" | "interrupted")
        {
            return Some("Review cancelled");
        }
        self.audit_complete().then_some("Review complete")
    }

    fn audit_boundary(&self) -> Option<&'static str> {
        match &self.item {
            DisplayItem::Stage(common, _) if common.status == "failed" => Some("Review failed"),
            DisplayItem::TaskOutcome(common, _) if common.status == "failed" => {
                Some("Audit stopped · Task failed")
            }
            DisplayItem::Stage(common, _) | DisplayItem::TaskOutcome(common, _)
                if matches!(common.status.as_str(), "cancelled" | "interrupted") =>
            {
                Some("Review cancelled")
            }
            DisplayItem::TaskOutcome(_, _) | DisplayItem::Stage(_, _) => {
                Some("Outcome unavailable")
            }
            _ => None,
        }
    }

    fn display_lines(&self, width: usize) -> Vec<Line<'static>> {
        let mut lines = self.display_content_lines(width);
        let label = &self.item.common().actor_label;
        if !label.is_empty() && !lines.is_empty() {
            let mut header = adaptive_wrap_line(
                &Line::from(Span::styled(
                    label.clone(),
                    Style::default().fg(self.theme.text_muted),
                )),
                RtOptions::new(width.max(1)),
            )
            .iter()
            .map(line_to_static)
            .collect::<Vec<_>>();
            header.append(&mut lines);
            return header;
        }
        lines
    }

    fn transcript_lines(&self, width: usize) -> Vec<Line<'static>> {
        if let Some(patch) = self.patch_cell() {
            return patch.detail_lines(width, &self.theme);
        }
        if let DisplayItem::ReviewCycle(_, payload) = &self.item {
            return crate::terminal_hyperlinks::visible_lines(
                self.review_transcript_hyperlinks(payload, width),
            );
        }
        if self.audit_context().is_some() {
            if let DisplayItem::Reasoning(_, payload) = &self.item {
                let mut lines = super::render_reasoning_summary(
                    &payload.summary.join("\n\n"),
                    "Thinking",
                    width,
                    &self.theme,
                    None,
                );
                if self.show_raw_reasoning {
                    lines.extend(
                        super::ReasoningRawHistoryCell::new(
                            payload.content.join("\n"),
                            self.theme.clone(),
                        )
                        .display_lines(width),
                    );
                }
                return lines;
            }
            if matches!(
                self.item,
                DisplayItem::ReviewCycle(_, _) | DisplayItem::AgentMessage(_, _)
            ) {
                return self.display_lines(width);
            }
        }
        if self.item.common().receipt_state == "not_run" {
            return self.display_lines(width);
        }
        if let DisplayItem::WebSearch(common, payload) = &self.item {
            let mut lines = self.display_lines(width);
            // Receipt coverage describes saved evidence, not whether the web
            // action ran. Keep it in details, independently of the action title.
            if common.status == "completed" {
                lines.extend(crate::wrapping::adaptive_wrap_lines(
                    &[self.tree_line(payload.evidence.label())],
                    RtOptions::new(width.max(1)).subsequent_indent(Line::from("    ")),
                ));
            }
            let transcript = self.raw_transcript();
            if !transcript.trim().is_empty() {
                lines.push(Line::default());
                lines.push(Line::from("── Recorded details"));
                for source in transcript.lines() {
                    lines.extend(
                        adaptive_wrap_line(
                            &Line::from(source.to_string()),
                            RtOptions::new(width.max(1)),
                        )
                        .iter()
                        .map(line_to_static),
                    );
                }
            }
            return lines;
        }
        let transcript = self.raw_transcript();
        let audit_identity_only =
            transcript.trim_start().starts_with("Call ID:") && !transcript.trim().contains('\n');
        if transcript.trim().is_empty() || audit_identity_only {
            return self.display_lines(width);
        }
        transcript
            .lines()
            .map(|line| Line::from(line.to_string()))
            .collect()
    }

    fn raw_lines(&self) -> Vec<Line<'static>> {
        let raw = &self.item.common().raw_source;
        let transcript = self.raw_transcript();
        crate::history_cell::raw_lines_from_source(if raw.is_empty() { &transcript } else { raw })
    }

    fn spacing_kind(&self) -> HistoryCellSpacingKind {
        if matches!(
            self.item,
            DisplayItem::Heartbeat(_, _) | DisplayItem::ModelActivity(_, _)
        ) {
            HistoryCellSpacingKind::LlmLifecycle
        } else {
            HistoryCellSpacingKind::Other
        }
    }

    fn as_any(&self) -> &dyn std::any::Any {
        self
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn plain(lines: Vec<Line<'static>>) -> String {
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

    #[test]
    fn provider_retry_context_distinguishes_core_and_authoring_turns() {
        let cases = [
            (
                serde_json::json!({
                    "label": "Reconnected after 1 retry",
                    "elapsed_ms": 191100,
                    "stage": "literature_grounding",
                    "attempt": 1,
                    "continuation_index": 0,
                    "turn_index": 2,
                    "component": "core",
                    "operation": "execute_stage",
                    "outcome": "completed",
                    "transcript": ""
                }),
                "Literature grounding · attempt 1 · Core round 2",
            ),
            (
                serde_json::json!({
                    "label": "Completed response after 2 retries",
                    "elapsed_ms": 353900,
                    "stage": "admission",
                    "attempt": 0,
                    "continuation_index": 0,
                    "turn_index": 2,
                    "component": "stage_selection",
                    "operation": "select_workflow_stages",
                    "outcome": "completed",
                    "transcript": ""
                }),
                "Workflow selection · authoring turn 2",
            ),
        ];
        for (payload, expected_context) in cases {
            let item = DisplayItem::parse_value(&serde_json::json!({
                "schema_version": "ownpi.tui.item.v1",
                "kind": "model_activity",
                "item_id": "provider-retry",
                "call_id": "provider-retry",
                "tool_name": "model",
                "status": "completed",
                "action": "provider_retry",
                "visibility": "user",
                "payload": payload
            }))
            .expect("provider retry item");
            let rendered = plain(
                TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(100),
            );
            assert!(rendered.contains(expected_context), "{rendered}");
            assert_eq!(rendered.lines().count(), 2, "{rendered}");
        }
    }

    #[test]
    fn typed_reasoning_replay_uses_the_same_single_line_as_live_history() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": crate::display_item::DISPLAY_ITEM_SCHEMA,
            "kind": "reasoning",
            "item_id": "reasoning-1",
            "call_id": "reasoning-1",
            "tool_name": "reasoning",
            "status": "completed",
            "action": "reason",
            "visibility": "user",
            "payload": {
                "summary": ["Planning path inspection strategy"]
            }
        }))
        .expect("reasoning item");

        let rendered =
            plain(TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(80));

        assert_eq!(rendered, "⦁ Thinking • Planning path inspection strategy");
    }

    #[test]
    fn typed_reasoning_replay_uses_the_same_multi_line_tree_as_live_history() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": crate::display_item::DISPLAY_ITEM_SCHEMA,
            "kind": "reasoning",
            "item_id": "reasoning-multi",
            "call_id": "reasoning-multi",
            "tool_name": "reasoning",
            "status": "completed",
            "action": "reason",
            "visibility": "user",
            "payload": {
                "summary": [
                    "Defining semantic doc scope and deliverables",
                    "Verifying single H1 usage"
                ]
            }
        }))
        .expect("reasoning item");

        let rendered =
            plain(TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(80));

        assert_eq!(
            rendered,
            "⦁ Thinking\n  ├ Defining semantic doc scope and deliverables\n  └ Verifying single H1 usage"
        );
    }

    #[test]
    fn model_activity_renders_one_semantic_compact_line() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": crate::display_item::DISPLAY_ITEM_SCHEMA,
            "kind": "model_activity",
            "item_id": "model-3",
            "call_id": "model-3",
            "tool_name": "model",
            "status": "completed",
            "action": "compose_response",
            "visibility": "user",
            "payload": {
                "label": "Integrated tool failure and composed response",
                "elapsed_ms": 46200,
                "stage": "direct_qa",
                "attempt": 1,
                "continuation_index": 0,
                "turn_index": 3,
                "outcome": "composed_result",
                "transcript": "Agent · Integrated tool failure and composed response · 46.2s"
            }
        }))
        .expect("model activity");
        let cell = TypedThreadItemHistoryCell::new(item, ThemePalette::default());

        assert_eq!(
            plain(cell.display_lines(100)),
            "⦁ Agent · Integrated tool failure and composed response · 46.2s"
        );
        assert_eq!(
            cell.raw_text().as_deref(),
            Some("Agent · Integrated tool failure and composed response · 46.2s")
        );
    }

    #[test]
    fn bridge_decision_activity_uses_semantic_actor_without_fake_duration() {
        for (action, actor, label) in [
            ("web_access", "Access", "Network enabled for this task"),
            (
                "provider_recovery",
                "Recovery",
                "Retrying literature grounding",
            ),
        ] {
            let item = DisplayItem::parse_value(&serde_json::json!({
                "schema_version": crate::display_item::DISPLAY_ITEM_SCHEMA,
                "kind": "model_activity",
                "item_id": format!("{action}-1"),
                "call_id": format!("{action}-1"),
                "tool_name": "model",
                "status": "completed",
                "action": action,
                "visibility": "user",
                "payload": {
                    "label": label,
                    "elapsed_ms": 0,
                    "outcome": "accepted",
                    "transcript": label
                }
            }))
            .expect("bridge decision activity");
            let rendered = plain(
                TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(100),
            );
            assert_eq!(rendered, format!("⦁ {actor} · {label}"));
            assert!(!rendered.contains("0.0s"));
        }
    }

    #[test]
    fn top_level_markers_follow_status_contract_without_gray_bullets() {
        let palette = ThemePalette::default();
        for (status, marker, color) in [
            ("completed", crate::style::GLYPH_ACTION, palette.success),
            ("running", crate::style::GLYPH_ACTION, palette.accent),
            (
                "paused_for_human",
                crate::style::GLYPH_ACTION,
                palette.accent,
            ),
            ("interrupted", crate::style::GLYPH_ACTION, palette.accent),
            ("unknown", crate::style::GLYPH_ACTION, palette.accent),
            ("failed", crate::style::GLYPH_FAILURE_PREFIX, palette.error),
            ("blocked", crate::style::GLYPH_FAILURE_PREFIX, palette.error),
            (
                "cancelled",
                crate::style::GLYPH_FAILURE_PREFIX,
                palette.error,
            ),
            ("partial_success", "! ", palette.warning),
            ("failed_with_partial", "! ", palette.warning),
        ] {
            let item = DisplayItem::parse_value(&serde_json::json!({
                "schema_version": "ownpi.tui.item.v1",
                "kind": "command_execution",
                "item_id": format!("command-{status}"),
                "call_id": format!("command-{status}"),
                "tool_name": "test_runner",
                "status": status,
                "action": "run",
                "visibility": "user",
                "payload": {"command": "pytest -q"}
            }))
            .expect("typed command item");
            let lines = TypedThreadItemHistoryCell::new(item, palette.clone()).display_lines(100);
            let top_level = lines.first().expect("top-level activity line");
            let marker_span = top_level.spans.first().expect("top-level marker span");

            assert_eq!(marker_span.content.as_ref(), marker, "status={status}");
            assert_eq!(marker_span.style.fg, Some(color), "status={status}");
            assert_eq!(
                marker_span
                    .style
                    .add_modifier
                    .contains(Modifier::SLOW_BLINK),
                status == "running",
                "status={status}"
            );
            assert!(!plain(lines).starts_with('•'), "status={status}");
        }
    }

    #[test]
    fn distinct_llm_lifecycle_receipts_receive_one_cell_gap() {
        let heartbeat = |item_id: &str, sequence: u64| {
            DisplayItem::parse_value(&serde_json::json!({
                "schema_version": "ownpi.tui.item.v1",
                "kind": "heartbeat",
                "item_id": item_id,
                "call_id": "req-1",
                "tool_name": "llm_request",
                "status": "completed",
                "action": "request",
                "visibility": "user",
                "payload": {
                    "title": "LLM request",
                    "request": "paper report",
                    "request_id": "req-1",
                    "sequence": sequence,
                    "phase": "heartbeat",
                    "elapsed_ms": sequence * 5_000,
                    "transcript": format!("Heartbeat {sequence}")
                }
            }))
            .expect("heartbeat")
        };
        let palette = ThemePalette::default();
        let first =
            TypedThreadItemHistoryCell::new(heartbeat("llm:req-1:heartbeat:1", 1), palette.clone());
        let second =
            TypedThreadItemHistoryCell::new(heartbeat("llm:req-1:heartbeat:2", 2), palette);

        assert!(super::super::history_cells_require_inter_cell_spacing(
            &first, &second
        ));
    }

    #[test]
    fn route_decision_is_compact_typed_and_resize_safe() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "route_decision",
            "item_id": "turn-1:route",
            "call_id": "turn-1:route",
            "tool_name": "chat_router",
            "status": "completed",
            "action": "route",
            "visibility": "user",
            "payload": {
                "route_kind": "structured",
                "route_label": "Research Workflow",
                "initial_stage_label": "Preparing",
                "transcript": "Router · Research Workflow"
            }
        }))
        .expect("typed route decision");
        let palette = ThemePalette::default();

        for width in [8, 12, 24, 40, 80, 120] {
            let lines =
                TypedThreadItemHistoryCell::new(item.clone(), palette.clone()).display_lines(width);
            let rendered = plain(lines.clone());
            assert!(lines.iter().all(|line| line.width() == width));
            if width < 12 {
                assert_eq!(lines.len(), 1);
                assert!(rendered.contains("ROUTER") || rendered.contains('…'));
            } else {
                assert_eq!(lines.len(), 3);
                assert!(rendered.starts_with("╭─ ROUTER ─"));
                assert!(rendered.contains("◆"));
                assert!(rendered.ends_with('╯'));
                if width >= 24 {
                    assert!(rendered.contains("RESEARCH WORKFLOW"));
                } else {
                    assert!(rendered.contains("RESE…"));
                }
            }
            assert!(!rendered.contains("literature review"));
            assert!(!rendered.contains("inputs"));
        }
    }

    #[test]
    fn route_banner_uses_graphite_signal_styles_on_light_and_dark_themes() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "route_decision",
            "item_id": "turn-style:route",
            "call_id": "turn-style:route",
            "tool_name": "chat_router",
            "status": "completed",
            "action": "route",
            "visibility": "user",
            "payload": {
                "route_kind": "direct",
                "route_label": "Direct Chat",
                "initial_stage_label": "Chat",
                "transcript": "Router · Direct Chat"
            }
        }))
        .expect("typed route decision");
        let themes = [
            ThemePalette::default(),
            ThemePalette::from_terminal_colors_for_level(
                Some(ratatui::style::Color::Rgb(0xee, 0xee, 0xee)),
                Some(ratatui::style::Color::Rgb(0x11, 0x11, 0x11)),
                crate::theme::TerminalColorLevel::TrueColor,
            ),
        ];

        for theme in themes {
            let lines =
                TypedThreadItemHistoryCell::new(item.clone(), theme.clone()).display_lines(48);
            let top = &lines[0];
            let route = &lines[1];

            assert_eq!(top.spans[0].style.fg, Some(theme.border));
            assert_eq!(top.spans[1].content.as_ref(), "ROUTER");
            assert_eq!(top.spans[1].style.fg, Some(theme.accent));
            assert!(top.spans[1].style.add_modifier.contains(Modifier::BOLD));
            assert!(route.spans[1].content.contains('◆'));
            assert_eq!(route.spans[1].style.fg, Some(theme.accent));
            assert_eq!(route.spans[2].content.as_ref(), "DIRECT CHAT");
            assert_eq!(route.spans[2].style.fg, Some(theme.text_title));
            assert!(route.spans[2].style.add_modifier.contains(Modifier::BOLD));
            for line in &lines {
                for span in &line.spans {
                    assert_eq!(span.style.bg, theme.elevated_bg.or(theme.surface_bg));
                }
            }
        }
    }

    #[test]
    fn route_banner_raw_transcript_remains_copy_friendly() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "route_decision",
            "item_id": "turn-raw:route",
            "call_id": "turn-raw:route",
            "tool_name": "chat_router",
            "status": "completed",
            "action": "route",
            "visibility": "user",
            "payload": {
                "route_kind": "structured",
                "route_label": "Task Workflow",
                "initial_stage_label": "Preparing",
                "transcript": "Router · Task Workflow"
            }
        }))
        .expect("typed route decision");
        let cell = TypedThreadItemHistoryCell::new(item, ThemePalette::default());

        assert_eq!(plain(cell.raw_lines()), "Router · Task Workflow");
    }

    #[test]
    fn explore_card_contains_only_producer_semantic_command() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "command_execution",
            "item_id": "search-1",
            "call_id": "search-1",
            "tool_name": "grep_search",
            "status": "completed",
            "action": "search",
            "visibility": "user",
            "payload": {
                "command": "Search \"page_index\" in run/tool_results",
                "output": []
            }
        }))
        .expect("typed search item");
        let rendered = plain(
            TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(100),
        );

        assert!(rendered.contains("Explored"));
        assert!(rendered.contains("Search \"page_index\" in run/tool_results"));
        assert!(!rendered.contains("search_root"));
        assert!(!rendered.contains("pattern="));
        assert!(!rendered.contains("result_ref"));
    }

    #[test]
    fn web_search_card_omits_results() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "web_search",
            "item_id": "web-1",
            "call_id": "web-1",
            "tool_name": "web_search",
            "status": "completed",
            "action": "search",
            "visibility": "user",
            "payload": {
                "query": "shared latent representations",
                "web_action": {"type": "search", "query": "shared latent representations"},
                "transcript": "result one\nresult two"
            }
        }))
        .expect("typed web item");
        let cell = TypedThreadItemHistoryCell::new(item, ThemePalette::default());
        let rendered = plain(cell.display_lines(100));

        assert_eq!(
            rendered,
            "⦁ Searched the web for shared latent representations"
        );
        assert!(!rendered.contains("result one"));
        assert!(plain(cell.transcript_lines(100)).contains("result one"));
    }

    #[test]
    fn incomplete_web_search_is_deferred_not_completed() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "web_search",
            "item_id": "web-deferred",
            "call_id": "web-deferred",
            "tool_name": "web_search",
            "status": "incomplete",
            "action": "search",
            "visibility": "user",
            "payload": {
                "query": "cross-patient speech decoding",
                "error_summary": "Earlier sibling call requires human input"
            }
        }))
        .expect("typed deferred web item");
        let rendered = plain(
            TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(100),
        );

        assert!(rendered.contains("Deferred web search"), "{rendered}");
        assert!(rendered.contains("Reason"), "{rendered}");
        assert!(!rendered.contains("Searched the web"), "{rendered}");
        assert!(!rendered.contains("Error "), "{rendered}");
    }

    #[test]
    fn incomplete_exploration_is_deferred_and_hides_internal_refs() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "command_execution",
            "item_id": "ref-deferred",
            "call_id": "ref-deferred",
            "tool_name": "conversation_ref_reader",
            "status": "incomplete",
            "action": "read",
            "visibility": "user",
            "payload": {
                "command": "Read result:call_internal",
                "target": "result:call_internal",
                "error_summary": "Earlier sibling call requires human input"
            }
        }))
        .expect("typed deferred exploration");
        let rendered = plain(
            TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(100),
        );

        assert!(rendered.contains("Deferred exploration"), "{rendered}");
        assert!(rendered.contains("Read tool result"), "{rendered}");
        assert!(!rendered.contains("Explored"), "{rendered}");
        assert!(!rendered.contains("call_internal"), "{rendered}");
    }

    #[test]
    fn skill_card_keeps_scope_payload_out_of_primary_view() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "skill_activity",
            "item_id": "skill-1",
            "call_id": "skill-1",
            "tool_name": "skill_invoke",
            "status": "completed",
            "action": "invoke",
            "visibility": "user",
            "payload": {
                "skill_name": "citation-verification",
                "purpose": "Verify citations for the paper report.",
                "transcript": "<stage_scope_note>internal scope</stage_scope_note>"
            }
        }))
        .expect("typed skill item");
        let palette = ThemePalette::default();
        let cell = TypedThreadItemHistoryCell::new(item, palette.clone());
        let lines = cell.display_lines(100);
        let rendered = plain(lines.clone());

        assert_eq!(
            rendered,
            "⦁ Used skill: citation-verification\n  └ Purpose Verify citations for the paper report."
        );
        assert_eq!(lines[0].spans[0].content, crate::style::GLYPH_ACTION);
        assert_eq!(lines[0].spans[0].style.fg, Some(palette.success));
        assert!(!lines[0].spans[0]
            .style
            .add_modifier
            .contains(Modifier::SLOW_BLINK));
        assert_eq!(lines[0].spans[2].content, "citation-verification");
        assert_eq!(lines[0].spans[2].style.fg, Some(palette.accent));
        assert_eq!(lines[1].spans[1].content, "Purpose");
        assert_eq!(lines[1].spans[1].style.fg, Some(palette.accent));
        assert_eq!(
            lines[1].spans[3].content,
            "Verify citations for the paper report."
        );
        assert_eq!(lines[1].spans[3].style.fg, Some(palette.text));
        assert_eq!(rendered.matches('└').count(), 1);
        assert!(!rendered.contains("stage_scope_note"));
        assert!(plain(cell.transcript_lines(100)).contains("stage_scope_note"));
    }

    #[test]
    fn running_skill_uses_blinking_accent_marker() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "skill_activity",
            "item_id": "skill-running",
            "call_id": "skill-running",
            "tool_name": "skill_invoke",
            "status": "running",
            "action": "invoke",
            "visibility": "user",
            "payload": {
                "skill_name": "daily-coding",
                "purpose": "Implement change."
            }
        }))
        .expect("running skill item");
        let palette = ThemePalette::default();
        let lines = TypedThreadItemHistoryCell::new(item, palette.clone()).display_lines(100);
        let marker = &lines[0].spans[0];

        assert_eq!(marker.content, crate::style::GLYPH_ACTION);
        assert_eq!(marker.style.fg, Some(palette.accent));
        assert!(marker.style.add_modifier.contains(Modifier::SLOW_BLINK));
    }

    #[test]
    fn task_header_uses_large_accent_marker() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "task_header",
            "item_id": "task-1",
            "call_id": "task-1",
            "tool_name": "task_header",
            "status": "completed",
            "action": "show",
            "visibility": "user",
            "payload": {
                "title": "Interpreted Task",
                "text": "- mode: literature_review"
            }
        }))
        .expect("typed task header");
        let palette = ThemePalette::default();
        let lines = TypedThreadItemHistoryCell::new(item, palette.clone()).display_lines(100);

        assert_eq!(
            plain(lines.clone()),
            "⦁ Interpreted Task\n    - mode: literature_review"
        );
        assert_eq!(lines[0].spans[0].content, crate::style::GLYPH_ACTION);
        assert_eq!(lines[0].spans[0].style.fg, Some(palette.accent));
    }

    #[test]
    fn adjacent_exploration_items_keep_one_header_per_call() {
        let parse = |item_id: &str, action: &str, command: &str, key: &str| {
            DisplayItem::parse_value(&serde_json::json!({
                "schema_version": "ownpi.tui.item.v1",
                "kind": "command_execution",
                "item_id": item_id,
                "call_id": item_id,
                "tool_name": "reader",
                "status": "completed",
                "action": action,
                "visibility": "user",
                "payload": {
                    "command": command,
                    "aggregation_key": key,
                    "output": []
                }
            }))
            .expect("typed exploration item")
        };
        let listing = TypedThreadItemHistoryCell::new(
            parse("list-1", "list", "List inputs", "list:inputs"),
            ThemePalette::default(),
        );
        let reading = TypedThreadItemHistoryCell::new(
            parse(
                "read-1",
                "read",
                "Read citation evidence",
                "read:citation evidence",
            ),
            ThemePalette::default(),
        );
        let rendered = format!(
            "{}\n{}",
            plain(listing.display_lines(100)),
            plain(reading.display_lines(100))
        );

        assert_eq!(rendered.matches("Explored").count(), 2);
        assert!(rendered.contains("List inputs"));
        assert!(rendered.contains("Read citation evidence"));
    }

    #[test]
    fn exploration_history_renders_every_independent_call_without_a_cap() {
        let parse = |index: usize| {
            DisplayItem::parse_value(&serde_json::json!({
                "schema_version": "ownpi.tui.item.v1",
                "kind": "command_execution",
                "item_id": format!("read-{index}"),
                "call_id": format!("read-{index}"),
                "tool_name": "reader",
                "status": "completed",
                "action": "read",
                "visibility": "user",
                "payload": {
                    "command": format!("Read input/paper-{index}.pdf"),
                    "target": format!("input/paper-{index}.pdf"),
                    "aggregation_key": format!("read:input/paper-{index}.pdf"),
                    "output": []
                }
            }))
            .expect("typed exploration item")
        };

        let rendered = (1..=12)
            .map(|index| {
                plain(
                    TypedThreadItemHistoryCell::new(parse(index), ThemePalette::default())
                        .display_lines(100),
                )
            })
            .collect::<Vec<_>>()
            .join("\n");

        assert_eq!(rendered.matches("input/paper-").count(), 12, "{rendered}");
        assert_eq!(rendered.matches("Explored").count(), 12, "{rendered}");
        for index in 1..=12 {
            assert!(
                rendered.contains(&format!("Read input/paper-{index}.pdf")),
                "{rendered}"
            );
        }
        assert!(!rendered.contains("more entries"), "{rendered}");
    }

    #[test]
    fn independent_exploration_calls_each_use_a_tree_prefix_and_cyan_action() {
        let parse = |item_id: &str, action: &str, payload: serde_json::Value| {
            DisplayItem::parse_value(&serde_json::json!({
                "schema_version": "ownpi.tui.item.v1",
                "kind": "command_execution",
                "item_id": item_id,
                "call_id": item_id,
                "tool_name": "reader",
                "status": "completed",
                "action": action,
                "visibility": "user",
                "payload": payload
            }))
            .expect("typed exploration item")
        };
        let search = TypedThreadItemHistoryCell::new(
            parse(
                "search-1",
                "search",
                serde_json::json!({
                    "command": "legacy command is not rendered",
                    "query": "grounding_report",
                    "target": "run/artifacts",
                    "aggregation_key": "search:grounding_report:run/artifacts"
                }),
            ),
            ThemePalette::default(),
        );
        let reading = TypedThreadItemHistoryCell::new(
            parse(
                "read-1",
                "read",
                serde_json::json!({
                    "command": "legacy command is not rendered",
                    "target": "run/artifact.json",
                    "aggregation_key": "read:run/artifact.json"
                }),
            ),
            ThemePalette::default(),
        );
        let lines = search
            .display_lines(100)
            .into_iter()
            .chain(reading.display_lines(100))
            .collect::<Vec<_>>();

        assert_eq!(
            plain(lines.clone()),
            concat!(
                "⦁ Explored\n",
                "  └ Search \"grounding_report\" in run/artifacts\n",
                "⦁ Explored\n",
                "  └ Read run/artifact.json"
            )
        );
        assert_eq!(plain(lines.clone()).matches('└').count(), 2);
        for action in ["Search", "Read"] {
            let span = lines
                .iter()
                .flat_map(|line| line.spans.iter())
                .find(|span| span.content == action)
                .expect("exploration action span");
            assert_eq!(span.style.fg, Some(ratatui::style::Color::Cyan));
        }
    }

    #[test]
    fn skill_resource_reads_remain_independent_and_hide_internal_paths() {
        let parse = |item_id: &str, target: &str, key: &str| {
            DisplayItem::parse_value(&serde_json::json!({
                "schema_version": "ownpi.tui.item.v1",
                "kind": "command_execution",
                "item_id": item_id,
                "call_id": item_id,
                "tool_name": "skills_read",
                "status": "completed",
                "action": "read",
                "visibility": "user",
                "payload": {
                    "command": format!("Read {target}"),
                    "target": target,
                    "aggregation_key": key,
                    "output": []
                }
            }))
            .expect("skill resource exploration item")
        };
        let root = TypedThreadItemHistoryCell::new(
            parse(
                "skill-read-1",
                "SKILL.md (planning-with-files skill)",
                "read:skill:planning-with-files:skill.md",
            ),
            ThemePalette::default(),
        );
        let reference = TypedThreadItemHistoryCell::new(
            parse(
                "skill-read-2",
                "stage plan protocol (planning-with-files skill)",
                "read:skill:planning-with-files:references/stage-plan-protocol.md",
            ),
            ThemePalette::default(),
        );
        let template = TypedThreadItemHistoryCell::new(
            parse(
                "skill-read-3",
                "stage plan template (planning-with-files skill)",
                "read:skill:planning-with-files:templates/stage-plan.md",
            ),
            ThemePalette::default(),
        );
        let rendered = [root, reference, template]
            .into_iter()
            .map(|cell| plain(cell.display_lines(120)))
            .collect::<Vec<_>>()
            .join("\n");

        assert_eq!(
            rendered,
            concat!(
                "⦁ Explored\n",
                "  └ Read SKILL.md (planning-with-files skill)\n",
                "⦁ Explored\n",
                "  └ Read stage plan protocol (planning-with-files skill)\n",
                "⦁ Explored\n",
                "  └ Read stage plan template (planning-with-files skill)"
            )
        );
        assert!(!rendered.contains("bundled://"));
        assert!(!rendered.contains("Purpose"));
        assert!(!rendered.contains("references/"));
        assert!(!rendered.contains("templates/"));
    }

    #[test]
    fn unavailable_paper_is_a_warning_and_preserves_requested_url() {
        for width in [40, 80, 120] {
            let item=DisplayItem::parse_value(&serde_json::json!({
                "schema_version":"ownpi.tui.item.v1","visibility":"user","item_id":"unavailable",
                "kind":"command_execution","tool_name":"paper_fetch","status":"completed",
                "payload":{"command":"paper_fetch","metadata":{"identifier":"https://arxiv.org/pdf/2208.12266",
                    "outcome":"unavailable","content_level":"unavailable","extraction_coverage":"none"}}
            })).unwrap();
            let lines =
                TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(width);
            let text = plain(lines.clone());
            assert!(
                text.contains("Paper content unavailable") && !text.contains("Fetched paper"),
                "{text}"
            );
            assert!(
                text.contains("2208.12266") && text.contains("Content Unavailable"),
                "{text}"
            );
            assert!(!text.contains("content_available") && !text.contains("pdf_page"));
            assert_eq!(
                lines[0].spans[0].style.fg,
                Some(ThemePalette::default().warning)
            );
        }
    }

    #[test]
    fn paper_fetch_uses_single_line_header_and_bulleted_metadata() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "command_execution",
            "item_id": "paper-1",
            "call_id": "paper-1",
            "tool_name": "paper_fetch",
            "status": "completed",
            "action": "run",
            "visibility": "user",
            "payload": {
                "command": "paper_fetch",
                "metadata": {
                    "title": "Shared latent representations of speech production",
                    "doi": "10.1101/2025.08.21.671516",
                    "source": "fallback",
                    "selected_source": "crossref",
                    "outcome": "complete"
                }
            }
        }))
        .expect("paper fetch item");
        let lines =
            TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(100);

        assert_eq!(
            plain(lines.clone()),
            concat!(
                "⦁ Fetched paper\n",
                "  └ • Paper Shared latent representations of speech production\n",
                "    • DOI 10.1101/2025.08.21.671516\n",
                "    • Content Available\n",
                "    • Source Crossref"
            )
        );
        assert_eq!(plain(lines.clone()).matches('└').count(), 1);
        let tool = lines[0]
            .spans
            .iter()
            .find(|span| span.content == "Fetched paper")
            .expect("paper result span");
        assert_eq!(tool.style.fg, Some(ThemePalette::default().accent));
    }

    #[test]
    fn paper_fetch_abstract_is_not_displayed_as_fulltext() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1", "kind": "command_execution",
            "item_id": "abstract-only", "call_id": "abstract-only", "tool_name": "paper_fetch",
            "status": "completed", "action": "run", "visibility": "user",
            "payload": {"command": "paper_fetch", "metadata": {
                "outcome": "partial", "content_level": "abstract", "extraction_coverage": "no_body"
            }}
        }))
        .expect("abstract result");
        let rendered =
            plain(TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(80));
        assert!(rendered.contains("Retrieved abstract"));
        assert!(rendered.contains("Content Abstract only"));
        assert!(!rendered.contains("Coverage Abstract only"));
        assert!(!rendered.contains("Retrieval complete"));
    }

    #[test]
    fn citation_verify_renders_claim_result_instead_of_raw_tool_name() {
        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../tests/fixtures/citation_verification.json"
        ))
        .expect("shared current-contract fixture");
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "command_execution",
            "item_id": "verify-1",
            "call_id": "verify-1",
            "tool_name": "citation_verify",
            "status": "completed",
            "action": "verify_citation_claim",
            "visibility": "user",
            "payload": {
                "command": "citation_verify",
                "result_state": "claim_supported",
                "citation_verification": fixture["expected"]
            }
        }))
        .expect("citation verify item");

        let rendered = plain(
            TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(100),
        );

        assert!(
            rendered.contains("Agent assessment · Supported"),
            "{rendered}"
        );
        assert!(rendered.contains("example2026attention"), "{rendered}");
        assert!(rendered.contains("Claim Single-trial EEG"), "{rendered}");
        assert!(rendered.contains("Evidence Abstract"), "{rendered}");
        assert!(rendered.contains("Binding Stored source"), "{rendered}");
        assert!(rendered.contains("Passages 1"), "{rendered}");
        assert!(rendered.contains("Receipt lit:assessment:"), "{rendered}");
        assert_eq!(rendered.matches('└').count(), 1, "{rendered}");
        assert!(!rendered.contains("Ran citation_verify"), "{rendered}");
    }

    #[test]
    fn citation_verify_preserves_lifecycle_and_evidence_boundaries() {
        for (status, state, assessment, title) in [
            (
                "running",
                "verifying",
                "supported",
                "Verifying citation claim",
            ),
            (
                "failed",
                "request_invalid",
                "supported",
                "Citation request invalid",
            ),
            (
                "failed",
                "verification_failed",
                "supported",
                "Citation verification failed",
            ),
            (
                "cancelled",
                "verification_not_completed",
                "supported",
                "Citation verification cancelled",
            ),
            (
                "completed",
                "claim_unsupported",
                "unsupported",
                "Agent assessment · Unsupported",
            ),
            (
                "completed",
                "claim_unresolved",
                "unresolved",
                "Agent assessment · Unresolved",
            ),
            (
                "completed",
                "verification_completed",
                "",
                "Citation check completed",
            ),
        ] {
            let item = DisplayItem::parse_value(&serde_json::json!({
                "schema_version": "ownpi.tui.item.v1", "kind": "command_execution",
                "item_id": "verify", "call_id": "verify", "tool_name": "citation_verify",
                "status": status, "action": "verify_citation_claim", "visibility": "user",
                "payload": {"command": "citation_verify", "result_state": state,
                    "citation_verification": {"citation_ref": "lit:citation:example",
                        "claim": "A bounded claim", "assessor": "agent", "assessment": assessment,
                        "passage_count": 0, "diagnostics": ["Check source context"]}}
            }))
            .expect("verification lifecycle item");
            let rendered = plain(
                TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(100),
            );
            assert!(rendered.contains(title), "{rendered}");
            assert!(
                !rendered.contains("Agent assessment · Supported"),
                "{rendered}"
            );
            assert!(
                rendered.contains("Citation lit:citation:example"),
                "{rendered}"
            );
            assert!(rendered.contains("Passages 0"), "{rendered}");
            assert!(rendered.contains("Note Check source context"), "{rendered}");
            if status == "completed" {
                assert!(rendered.contains("Evidence Not recorded"), "{rendered}");
            }
        }
    }

    #[test]
    fn unbound_citation_explains_binding_and_next_action() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version":"ownpi.tui.item.v1", "kind":"command_execution",
            "item_id":"unbound", "call_id":"unbound", "tool_name":"citation_verify",
            "status":"completed", "action":"verify_citation_claim", "visibility":"user",
            "payload":{"command":"citation_verify", "result_state":"claim_unresolved",
                "citation_verification":{"bibtex_key":"example2026", "assessment":"unresolved",
                    "evidence_binding":"legacy_unbound",
                    "guidance":"Use citation_ref and passage_refs from paper_fetch/paper_read for bound evidence."}}
        })).unwrap();
        let cell = TypedThreadItemHistoryCell::new(item, ThemePalette::default());
        for width in [40, 80, 120] {
            let lines = cell.display_lines(width);
            assert!(lines.iter().all(|line| line.width() <= width));
            let text = plain(lines);
            assert!(text.contains("Unresolved"));
            assert!(text.contains("Next Use citation_ref"));
            assert!(!text.contains("Not verified by UI"));
            assert!(!text.contains("Supported"));
        }
        assert!(plain(cell.display_lines(120))
            .contains("Binding Evidence not bound to a stored source"));
    }

    #[test]
    fn recovery_and_failed_request_labels_wrap_without_losing_scope() {
        for (action, label, outcome) in [
            (
                "provider_recovery",
                "Retry 2 · literature grounding",
                "accepted",
            ),
            (
                "provider_retry",
                "Core request #6 · failed after 4 retries · returned to controller",
                "failed",
            ),
        ] {
            let item = DisplayItem::parse_value(&serde_json::json!({
                "schema_version":"ownpi.tui.item.v1", "kind":"model_activity",
                "item_id":action, "call_id":action, "tool_name":"model",
                "action":action, "status":"completed", "visibility":"user",
                "payload":{"label":label, "outcome":outcome, "elapsed_ms":25300,
                    "stage":"literature_grounding", "attempt":1}
            }))
            .unwrap();
            let cell = TypedThreadItemHistoryCell::new(item, ThemePalette::default());
            for width in [32, 80, 120] {
                assert!(cell
                    .display_lines(width)
                    .iter()
                    .all(|line| line.width() <= width));
            }
            let text = cell
                .display_lines(120)
                .iter()
                .map(crate::wrapping::line_plain_text)
                .collect::<Vec<_>>()
                .join("\n");
            assert!(text.contains(label));
            if action == "provider_recovery" {
                assert!(!text.contains("25.3s"));
            }
        }
    }

    #[test]
    fn citation_verify_wraps_unicode_and_long_receipts_without_extra_tree_markers() {
        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../tests/fixtures/citation_verification.json"
        ))
        .expect("shared current-contract fixture");
        for width in [20, 40, 80, 120] {
            let mut verification = fixture["expected"].clone();
            verification["claim"] =
                serde_json::json!("只支持受控任务中的语音注意选择，不等同于自由语音转写。");
            let item = DisplayItem::parse_value(&serde_json::json!({
                "schema_version": "ownpi.tui.item.v1", "kind": "command_execution",
                "item_id": "verify", "call_id": "verify", "tool_name": "citation_verify",
                "status": "completed", "action": "verify_citation_claim", "visibility": "user",
                "payload": {"command": "citation_verify", "result_state": "claim_supported",
                    "citation_verification": verification}
            }))
            .expect("verification fixture");
            let lines =
                TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(width);
            assert!(
                lines.iter().all(|line| line.width() <= width as usize),
                "width={width}: {}",
                plain(lines.clone())
            );
            assert_eq!(plain(lines).matches('└').count(), 1);
        }
    }

    #[test]
    fn evidence_v2_keeps_state_visible_and_hides_only_sub_100ms_duration() {
        for duration in [0, 99, 100] {
            let item = DisplayItem::parse_value(&serde_json::json!({
                "schema_version": "ownpi.tui.item.v1", "kind": "command_execution",
                "item_id": "evidence-1", "call_id": "evidence-1", "tool_name": "source_fetch",
                "status": "completed", "action": "evidence", "visibility": "user",
                "payload": {"command": "Abstract only · formal evidence incomplete",
                    "result_state": "abstract_only", "duration_ms": duration,
                    "output": ["content level: abstract"], "transcript_complete": true}
            }))
            .expect("typed evidence result");
            let rendered = plain(
                TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(100),
            );
            assert!(rendered.contains("Abstract only"), "{rendered}");
            assert!(!rendered.contains("Ran source_fetch"), "{rendered}");
            assert_eq!(rendered.contains("0.1s"), duration == 100, "{rendered}");
            assert!(!rendered.contains("0.0s"), "{rendered}");
        }
    }

    #[test]
    fn evidence_binding_summary_and_full_details_survive_narrow_widths() {
        for width in [40, 80, 120] {
            let errors = (1..=5)
                .map(|i| format!("citation_binding_incomplete:{i}"))
                .collect::<Vec<_>>()
                .join("\n");
            let item = DisplayItem::parse_value(&serde_json::json!({
                "schema_version": "ownpi.tui.item.v1", "kind": "command_execution",
                "item_id": "registration-1", "call_id": "registration-1",
                "tool_name": "manuscript_register", "status": "completed",
                "action": "evidence", "visibility": "user",
                "payload": {
                    "command": "Manuscript registered · 5 citation issues remain",
                    "result_state": "manuscript_registered", "duration_ms": 40,
                    "output": ["Manuscript: paper.md", "citation_binding_incomplete:1",
                        "citation_binding_incomplete:2", "citation_binding_incomplete:3",
                        "2 more issues · expand details or read the full record"],
                    "transcript": errors, "transcript_complete": true
                }
            }))
            .expect("registration result");
            let cell = TypedThreadItemHistoryCell::new(item, ThemePalette::default());
            let display = plain(cell.display_lines(width));
            assert!(!display.contains("0.0s"), "{display}");
            assert!(
                !display.contains("Evidence and independent review passed"),
                "{display}"
            );
            let transcript = plain(cell.transcript_lines(width));
            for i in 1..=5 {
                assert!(
                    transcript.contains(&format!("citation_binding_incomplete:{i}")),
                    "{transcript}"
                );
            }
        }
    }

    #[test]
    fn direct_missing_reply_keeps_saved_file_links_without_claiming_delivery() {
        for width in [40usize, 120] {
            let item = DisplayItem::parse_value(&serde_json::json!({
                "schema_version":"ownpi.tui.item.v1", "item_id":"missing-reply",
                "kind":"task_outcome", "status":"completed", "visibility":"user",
                "payload":{"request_index":1,"outcome":"idle", "reply_observation":"commentary_only",
                    "recorded_files":[{"path":"/tmp/report 中文.md","display_path":"report 中文.md","internal_citation_count":19}]}
            })).unwrap();
            let cell = TypedThreadItemHistoryCell::new(item, ThemePalette::default());
            let lines = cell.display_hyperlink_lines(width);
            assert!(lines.iter().all(|line| line.line.width() <= width));
            assert!(lines
                .iter()
                .flat_map(|line| &line.hyperlinks)
                .any(|link| link.destination == "file:///tmp/report%20%E4%B8%AD%E6%96%87.md"));
            let text = plain(cell.display_lines(width));
            assert!(text.contains("Only progress messages"), "{text}");
            assert!(
                text.contains("NO FINAL REPLY") && !text.contains("COMPLETED"),
                "{text}"
            );
            assert!(text.contains("report 中文.md"), "{text}");
            assert!(text.contains("19 unlinked markers"), "{text}");
        }
    }

    #[test]
    fn no_matches_has_a_neutral_label_and_keeps_exit_code() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version":"ownpi.tui.item.v1", "item_id":"rg-empty",
            "kind":"command_execution", "tool_name":"exec_command", "status":"failed", "action":"run", "visibility":"user",
            "payload":{"command":"rg needle file", "result_state":"no_matches", "exit_code":1,"error":"process failed"}
        })).unwrap();
        let text =
            plain(TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(80));
        assert!(text.contains("No matches · exit 1"));
        assert!(!text.contains("Failed") && !text.contains("Error"));
    }

    #[test]
    fn direct_request_completion_does_not_claim_research_delivery() {
        for width in [40usize, 80, 120] {
            let item = DisplayItem::parse_value(&serde_json::json!({
                "schema_version":"ownpi.tui.item.v1", "item_id":"request-2",
                "kind":"task_outcome", "status":"completed", "visibility":"user",
                "payload":{"request_index":2,"outcome":"idle"}
            }))
            .unwrap();
            let lines =
                TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(width);
            assert!(lines.iter().all(|line| line.width() <= width));
            let text = plain(lines);
            assert!(text.contains("REQUEST 2"), "{text}");
            assert!(
                text.contains("Ready for your next") && text.contains("message"),
                "{text}"
            );
            assert!(!text.contains("Delivery"), "{text}");
        }
    }

    #[test]
    fn runtime_profile_is_a_mode_not_a_model_routing_decision() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version":"ownpi.tui.item.v1", "item_id":"mode",
            "kind":"route_decision", "status":"completed", "visibility":"user",
            "payload":{"route_kind":"runtime_profile","route_label":"Task · Direct"}
        }))
        .unwrap();
        let text =
            plain(TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(80));
        assert!(
            text.contains("MODE") && text.contains("TASK · DIRECT"),
            "{text}"
        );
        assert!(!text.contains("ROUTER"), "{text}");
    }

    #[test]
    fn command_text_cannot_create_a_finalized_workflow_fact() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "command_execution",
            "item_id": "finish-stages-1",
            "call_id": "finish-stages-1",
            "tool_name": "workflow_plan",
            "status": "completed",
            "action": "stage_control",
            "visibility": "user",
            "payload": {
                "command": concat!(
                    "Finalized 3 workflow stages: literature grounding → ",
                    "analysis decision → output synthesis"
                ),
                "output": [],
                "exit_code": 0
            }
        }))
        .expect("stage control item");
        let cell = TypedThreadItemHistoryCell::new(item, ThemePalette::default());
        let lines = cell.display_lines(120);
        let display = plain(lines.clone());

        assert!(!display.contains("◆  SELECTED"), "{display}");
        assert!(display.contains("Finalized 3 workflow stages"), "{display}");
    }

    #[test]
    fn structured_finalized_stage_control_does_not_depend_on_command_copy() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "command_execution",
            "item_id": "finish-stages-structured",
            "call_id": "finish-stages-structured",
            "tool_name": "workflow_plan",
            "status": "completed",
            "action": "stage_control",
            "visibility": "user",
            "payload": {
                "command": "Finalized workflow stages",
                "workflow_stages": ["literature_grounding", "output_synthesis"],
                "output": [],
                "exit_code": 0
            }
        }))
        .expect("structured finalized stages");
        let display =
            plain(TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(64));

        assert!(display.contains("◆  SELECTED  ·  2 STAGES"), "{display}");
        assert!(
            display.contains("literature grounding → output synthesis"),
            "{display}"
        );
    }

    #[test]
    fn failed_empty_and_malformed_finalization_keep_the_semantic_line() {
        for (item_id, status, command) in [
            (
                "finish-failed",
                "failed",
                "Finalized 2 workflow stages: one → two",
            ),
            ("finish-empty", "completed", "Finalized workflow stages"),
            (
                "finish-malformed",
                "completed",
                "Finalized 3 workflow stages: one → two",
            ),
        ] {
            let item = DisplayItem::parse_value(&serde_json::json!({
                "schema_version": "ownpi.tui.item.v1",
                "kind": "command_execution",
                "item_id": item_id,
                "call_id": item_id,
                "tool_name": "workflow_plan",
                "status": status,
                "action": "stage_control",
                "visibility": "user",
                "payload": {
                    "command": command,
                    "workflow_stages": [],
                    "output": [],
                    "exit_code": if status == "failed" { 1 } else { 0 }
                }
            }))
            .expect("fallback finalized stages");
            let display = plain(
                TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(80),
            );
            assert!(!display.contains("WORKFLOW"), "{display}");
            assert!(display.contains(command), "{display}");
        }
    }

    #[test]
    fn long_stage_append_wraps_under_the_stage_label_not_the_number() {
        let stage = "implementation execution with a deliberately long descriptive stage name";
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "command_execution",
            "item_id": "append-stage-long",
            "call_id": "append-stage-long",
            "tool_name": "append_stage",
            "status": "completed",
            "action": "stage_control",
            "visibility": "user",
            "payload": {
                "command": format!("Append Stage · {stage}"),
                "transcript": format!("Append Stage · {stage}"),
                "stage_ordinal": 1,
                "output": [],
                "exit_code": 0
            }
        }))
        .expect("append stage item");
        let lines =
            TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(24);
        let rendered = plain(lines.clone());

        assert!(lines.len() > 2, "{rendered}");
        assert!(rendered
            .lines()
            .nth(1)
            .is_some_and(|line| line.starts_with("  1. ")));
        assert!(rendered
            .lines()
            .skip(2)
            .all(|line| line.starts_with("     ")));
        assert!(!rendered.contains('└'), "{rendered}");
    }

    #[test]
    fn command_failure_keeps_exit_and_error_without_guessing_from_stdout() {
        for (error, class, code, expected) in [
            (
                "process failed",
                "command_failed",
                Some(1),
                "process failed",
            ),
            ("process timeout", "timeout", None, "Timed out"),
            ("process cancelled", "cancelled", None, "process cancelled"),
            (
                "unknown environment session: 42",
                "session_lost",
                None,
                "unknown environment session: 42",
            ),
        ] {
            let item = DisplayItem::parse_value(&serde_json::json!({
                "schema_version":"ownpi.tui.item.v1", "kind":"command_execution",
                "item_id":"failed-command", "tool_name":"exec_command", "status":"failed", "visibility":"user",
                "payload":{"command":"cat fetched-page; rg missing", "output":["[", "  {", "PNLPNLPNLPN LPNLPNL"],
                    "exit_code":code, "error":error, "failure_class":class}
            })).unwrap();
            let cell = TypedThreadItemHistoryCell::new(item, ThemePalette::default());
            for width in [40, 80, 120, 190] {
                let lines = cell.display_lines(width);
                let text = plain(lines.clone());
                let normalized = text.split_whitespace().collect::<Vec<_>>().join(" ");
                assert!(normalized.contains(expected), "{text}");
                if let Some(code) = code {
                    assert!(text.contains(&format!("exit {code}")), "{text}");
                }
                assert!(lines.iter().all(|line| line.width() <= width));
            }
        }
    }

    #[test]
    fn command_with_unconfirmed_exit_does_not_render_ran() {
        for status in ["unknown", "running", "blocked"] {
            let item = DisplayItem::parse_value(&serde_json::json!({
                "schema_version":"ownpi.tui.item.v1", "kind":"command_execution",
                "item_id":"async-command", "tool_name":"exec_command", "status":status, "visibility":"user",
                "payload":{"command":"long-command", "output":[], "terminal_status":status}
            })).unwrap();
            let cell = TypedThreadItemHistoryCell::new(item, ThemePalette::default());
            let text = plain(cell.display_lines(80));
            assert!(!text.contains("Ran"), "{text}");
            if status == "unknown" {
                assert!(text.contains("Status unconfirmed"), "{text}");
            }
        }
    }

    #[test]
    fn execution_wait_uses_semantic_single_line_without_command_chrome() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "command_execution",
            "item_id": "execution-wait-1",
            "call_id": "execution-wait-1",
            "tool_name": "wait_for_execution_update",
            "status": "completed",
            "action": "execution_wait",
            "visibility": "user",
            "payload": {
                "command": "Execution update: revision 12 → 13",
                "transcript": "Execution update: revision 12 → 13",
                "output": [],
                "exit_code": 0
            }
        }))
        .expect("execution wait item");
        let cell = TypedThreadItemHistoryCell::new(item, ThemePalette::default());
        let display = plain(cell.display_lines(120));

        assert_eq!(display, "⦁ Execution update: revision 12 → 13");
        assert!(!display.contains("Ran"));
        assert!(!display.contains('└'));
        let transcript = plain(cell.transcript_lines(120));
        assert_eq!(
            transcript
                .matches("Execution update: revision 12 → 13")
                .count(),
            1
        );
        assert!(!transcript.contains("$ "));
        assert!(!transcript.contains("execution_board"));
    }

    #[test]
    fn command_history_hides_environment_and_transcript_keeps_full_output() {
        let output = (1..=8)
            .map(|value| format!("line {value}"))
            .collect::<Vec<_>>();
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "command_execution",
            "item_id": "command-1",
            "call_id": "command-1",
            "tool_name": "exec_command",
            "status": "completed",
            "action": "run",
            "visibility": "user",
            "payload": {
                "command": "printf output",
                "effective_command": "zsh -lc 'printf output'",
                "output": output,
                "exit_code": 0,
                "duration_ms": 1200,
                "transcript": (1..=8)
                    .map(|value| format!("line {value}"))
                    .collect::<Vec<_>>()
                    .join("\n"),
                "transcript_complete": true,
                "environment": {
                    "provider": "host_local",
                    "name": "project",
                    "python": "/workspace/.venv/bin/python"
                }
            }
        }))
        .expect("typed command item");
        let cell = TypedThreadItemHistoryCell::new(item, ThemePalette::default());
        let display = plain(cell.display_lines(100));

        assert!(
            display.contains("Ran zsh -lc 'printf output'  · 1.2s"),
            "{display}"
        );
        assert!(display.contains("line 1"), "{display}");
        assert!(display.contains("line 8"), "{display}");
        assert!(
            display.contains("… +4 lines (ctrl + t to view transcript)"),
            "{display}"
        );
        assert!(!display.contains("line 4"), "{display}");
        assert!(!display.contains("Environment:"), "{display}");
        assert!(!display.contains(".venv/bin/python"), "{display}");

        let transcript = plain(cell.transcript_lines(100));
        assert!(
            transcript.contains("$ zsh -lc 'printf output'"),
            "{transcript}"
        );
        assert!(transcript.contains("line 4"), "{transcript}");
        assert!(transcript.contains("✓ exit 0 · 1.2s"), "{transcript}");
    }

    #[test]
    fn typed_multiline_command_uses_codex_exec_layout_without_a_row_cap() {
        let command = concat!(
            "find /tmp/run/stages/literature_grounding -type f\n",
            "-print | sed -n '1,120p'\n",
            "printf third\n",
            "printf fourth"
        );
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "command_execution",
            "item_id": "command-layout",
            "call_id": "command-layout",
            "tool_name": "exec_command",
            "status": "completed",
            "action": "run",
            "visibility": "user",
            "payload": {
                "command": command,
                "effective_command": command,
                "output": ["/tmp/run/stages/literature_grounding/stage_semantics.md"],
                "exit_code": 0,
                "duration_ms": 0,
                "transcript_complete": true
            }
        }))
        .expect("typed multiline command");
        let display = plain(
            TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(120),
        );
        let lines = display.lines().collect::<Vec<_>>();

        assert_eq!(
            lines[0],
            "⦁ Ran find /tmp/run/stages/literature_grounding -type f"
        );
        assert_eq!(lines[1], "  │ -print | sed -n '1,120p'");
        assert_eq!(lines[2], "  │ printf third");
        assert_eq!(lines[3], "  │ printf fourth");
        assert_eq!(
            lines[4],
            "  └ /tmp/run/stages/literature_grounding/stage_semantics.md"
        );
    }

    #[test]
    fn citation_resolution_uses_semantic_identity_card_and_hides_zero_duration() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "command_execution",
            "item_id": "citation-1",
            "call_id": "citation-1",
            "tool_name": "citation_resolve",
            "status": "failed",
            "action": "resolve_citation",
            "visibility": "user",
            "payload": {
                "command": "citation_resolve",
                "result_state": "identity_conflict",
                "requested_identifier": "10.1016/j.neunet.2009.05.013",
                "requested_author": "DaSalla",
                "resolved_identifier": "doi:10.1016/j.neunet.2009.05.013",
                "resolved_title": "Evolutionary artificial neural networks",
                "duration_ms": 0,
                "error_summary": "Resolved metadata conflicts with requested author",
                "transcript": "full raw citation receipt"
            }
        }))
        .expect("citation item");
        let cell = TypedThreadItemHistoryCell::new(item, ThemePalette::default());
        let display = plain(cell.display_lines(100));
        let transcript = plain(cell.transcript_lines(100));

        assert!(
            display.contains("✗ Citation identity conflict"),
            "{display}"
        );
        assert!(display.contains("Requested 10.1016/j.neunet.2009.05.013"));
        assert!(display.contains("Resolved Evolutionary artificial neural networks"));
        assert!(!display.contains("Ran citation_resolve"));
        assert!(!display.contains("0.0s"));
        assert!(transcript.contains("full raw citation receipt"));
    }

    #[test]
    fn sub_tenth_second_tool_duration_is_hidden() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "command_execution",
            "item_id": "command-fast",
            "call_id": "command-fast",
            "tool_name": "exec_command",
            "status": "completed",
            "action": "run",
            "visibility": "user",
            "payload": {
                "command": "true",
                "duration_ms": 42,
                "output": []
            }
        }))
        .expect("fast command item");
        let display =
            plain(TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(80));

        assert!(!display.contains("0.1s"), "{display}");
        assert!(!display.contains("42ms"), "{display}");
    }

    #[test]
    fn tenth_second_tool_duration_is_visible() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "command_execution",
            "item_id": "command-threshold",
            "call_id": "command-threshold",
            "tool_name": "exec_command",
            "status": "completed",
            "action": "run",
            "visibility": "user",
            "payload": {
                "command": "true",
                "duration_ms": 100,
                "output": []
            }
        }))
        .expect("threshold command item");
        let display =
            plain(TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(80));

        assert!(display.contains("· 0.1s"), "{display}");
    }

    #[test]
    fn failed_web_card_shows_summary_with_sanitized_transcript() {
        let raw = "web search failed after 2 backend attempt(s): duckduckgo_lite: curl: (28) timeout\nprovider_kind=host_local\nraw_result_ref: /private/run/result.json";
        let transcript =
            "web search failed after 2 backend attempt(s): duckduckgo_lite: curl: (28) timeout";
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "web_search",
            "item_id": "web-1",
            "call_id": "web-1",
            "tool_name": "web_search",
            "status": "failed",
            "action": "search",
            "visibility": "user",
            "payload": {
                "query": "Shared latent representations",
                "web_action": {"type": "search", "query": "Shared latent representations"},
                "error": raw,
                "error_summary": "Timed out after 2 attempts",
                "transcript": transcript
            }
        }))
        .expect("failed web item");
        let cell = TypedThreadItemHistoryCell::new(item, ThemePalette::default());
        let rendered = plain(cell.display_lines(100));

        assert_eq!(
            rendered,
            concat!(
                "✗ Failed to search the web for Shared latent representations\n",
                "  └ Error Timed out after 2 attempts"
            )
        );
        assert!(!rendered.contains("provider_kind"));
        let transcript = plain(cell.transcript_lines(100));
        assert!(!transcript.contains("provider_kind"));
        assert!(!transcript.contains("raw_result_ref"));
    }

    #[test]
    fn crossref_open_failure_keeps_query_identity_and_failure_class() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "web_search",
            "item_id": "web-crossref",
            "call_id": "web-crossref",
            "tool_name": "web_fetch",
            "status": "failed",
            "action": "open_page",
            "visibility": "user",
            "payload": {
                "web_action": {
                    "type": "open_page",
                    "url": "https://api.crossref.org/works?query.title=EEG"
                },
                "display_url": "api.crossref.org/works · query.title=\"EEG speech\"",
                "display_query": "api.crossref.org/works · query.title=\"EEG speech\"",
                "http_status": 429,
                "failure_kind": "rate_limited",
                "retryable": true,
                "error_summary": "Rate limited · retry later",
                "transcript": "HTTP 429 response"
            }
        }))
        .expect("crossref web item");
        let cell = TypedThreadItemHistoryCell::new(item, ThemePalette::default());
        let rendered = plain(cell.display_lines(120));

        assert!(rendered.contains("api.crossref.org/works · query.title=\"EEG speech\""));
        assert!(rendered.contains("Rate limited · retry later"));
        assert!(!rendered.contains("api_key"));
    }

    #[test]
    fn compact_json_resource_keeps_extension_intact_at_narrow_widths() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "command_execution",
            "item_id": "artifact-json-1",
            "call_id": "artifact-json-1",
            "tool_name": "artifact_reader",
            "status": "completed",
            "action": "read",
            "visibility": "user",
            "payload": {
                "command": "Read run/run-3d55a255/stage_literature_grounding_artifact_attempt_1.json",
                "target": "run/run-3d55a255/stage_literature_grounding_artifact_attempt_1.json",
                "aggregation_key": "read:artifact:grounding"
            }
        }))
        .expect("typed artifact item");
        let cell = TypedThreadItemHistoryCell::new(item, ThemePalette::default());

        for width in [40, 80] {
            let lines = cell.display_lines(width);
            let rendered = plain(lines.clone());
            assert!(lines.iter().all(|line| line.width() <= width));
            assert!(rendered.contains(".json"), "{rendered:?}");
            assert!(!rendered.contains(".jso\n"), "{rendered:?}");
            assert!(!rendered.contains("session-"), "{rendered:?}");
        }
    }

    #[test]
    fn stage_banner_uses_one_planner_ordinal() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "stage",
            "item_id": "stage-1",
            "call_id": "stage-1",
            "tool_name": "stage_lifecycle",
            "status": "running",
            "action": "started",
            "visibility": "user",
            "payload": {
                "stage": "literature_grounding",
                "stage_label": "01:literature_grounding",
                "stage_ordinal": 1,
                "attempt": 1,
                "event": "started",
                "status": "running",
                "contract": "literature_grounding",
                "expected_outputs": ["grounding_report"]
            }
        }))
        .expect("typed stage item");
        let rendered = plain(
            TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(100),
        );

        assert!(rendered.contains("STAGE 01"));
        assert!(rendered.contains("◇  LITERATURE GROUNDING  ·  attempt 1"));
        assert!(!rendered.contains("STAGE 01 01"));
    }

    #[test]
    fn stage_revisit_and_terminal_decision_hide_machine_reason_from_primary_history() {
        let revisit = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "stage",
            "item_id": "stage-revisit:start",
            "call_id": "stage-revisit:start",
            "tool_name": "stage_lifecycle",
            "status": "running",
            "action": "started",
            "visibility": "user",
            "payload": {
                "stage": "literature_grounding",
                "stage_label": "literature_grounding",
                "stage_ordinal": 1,
                "attempt": 2,
                "event": "started"
            }
        }))
        .expect("revisit stage");
        let revisit_rendered = plain(
            TypedThreadItemHistoryCell::new(revisit, ThemePalette::default()).display_lines(80),
        );
        assert!(revisit_rendered.contains("REVISIT"));
        assert!(revisit_rendered.contains("attempt 2"));
        assert!(!revisit_rendered.contains("STAGE 01"));

        let outcome = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "stage",
            "item_id": "stage-revisit:outcome",
            "call_id": "stage-revisit:outcome",
            "tool_name": "stage_lifecycle",
            "status": "completed",
            "action": "completed",
            "visibility": "user",
            "payload": {
                "stage": "literature_grounding",
                "stage_label": "literature_grounding",
                "attempt": 2,
                "event": "completed",
                "decision": "accept",
                "next_stage": "analysis_decision",
                "reason": "revisit_repair_return_to_analysis",
                "transcript": "revisit_repair_return_to_analysis"
            }
        }))
        .expect("stage outcome");
        let cell = TypedThreadItemHistoryCell::new(outcome, ThemePalette::default());
        let rendered = plain(cell.display_lines(80));
        assert!(rendered.starts_with("╭─ OUTCOME "), "{rendered}");
        assert!(rendered.contains("LITERATURE GROUNDING"), "{rendered}");
        assert!(rendered.contains("✓"), "{rendered}");
        assert!(rendered.contains("ACCEPTED"), "{rendered}");
        assert!(!rendered.contains("Next stage"), "{rendered}");
        assert!(!rendered.contains("analysis_decision"), "{rendered}");
        assert!(!rendered.contains("revisit_repair_return_to_analysis"));
        assert!(cell
            .copy_text()
            .expect("stage transcript")
            .contains("revisit_repair_return_to_analysis"));
    }

    #[test]
    fn unknown_structured_gate_reason_stays_out_of_primary_history() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "stage",
            "item_id": "stage:method_design:a1:outcome",
            "call_id": "stage:method_design:a1:outcome",
            "tool_name": "stage_lifecycle",
            "status": "completed",
            "action": "completed",
            "visibility": "user",
            "payload": {
                "stage": "method_design",
                "stage_label": "Method design",
                "attempt": 1,
                "event": "completed",
                "gate_decision": "accept",
                "reason": "internal_gate_reason_v9",
                "reason_code": "internal_gate_reason_v9",
                "reason_display": "",
                "transcript": "internal_gate_reason_v9"
            }
        }))
        .expect("stage outcome");
        let cell = TypedThreadItemHistoryCell::new(item, ThemePalette::default());
        let rendered = plain(cell.display_lines(80));

        assert!(rendered.contains("Gate · ACCEPT"), "{rendered}");
        assert!(rendered.contains("Active findings · 0"), "{rendered}");
        assert!(!rendered.contains("internal_gate_reason_v9"), "{rendered}");
        assert!(cell
            .copy_text()
            .expect("stage transcript")
            .contains("internal_gate_reason_v9"));
    }

    #[test]
    fn structured_stage_outcome_exposes_review_basis_and_residual_handoff() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "stage",
            "item_id": "stage:literature_grounding:a1:outcome",
            "call_id": "stage:literature_grounding:a1:outcome",
            "tool_name": "stage_lifecycle",
            "status": "completed",
            "action": "completed",
            "visibility": "user",
            "payload": {
                "stage": "literature_grounding",
                "stage_label": "Literature grounding",
                "attempt": 1,
                "event": "completed",
                "gate_decision": "advance_with_residual_findings",
                "next_stage": "research_framing",
                "reason_code": "review_round_limit_reached",
                "reason_display": "Review limit reached; 1 finding carried forward.",
                "basis_review_record_id": "feedback:literature:reviewer:r2",
                "basis_review_kind": "reviewer",
                "basis_review_round": 2,
                "active_finding_count": 1,
                "residual_review_record_id": "feedback:literature:reviewer:r2",
                "residual_finding_count": 1
            }
        }))
        .expect("stage outcome");
        let rendered = plain(
            TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(100),
        );

        for expected in [
            "Gate · ADVANCE WITH RESIDUAL FINDINGS",
            "Basis · feedback:literature:reviewer:r2 · reviewer round 2",
            "Active findings · 1",
            "Residual findings · 1 · feedback:literature:reviewer:r2",
            "Next stage · research framing",
            "Reason · Review limit reached; 1 finding carried forward.",
        ] {
            assert!(rendered.contains(expected), "{rendered}");
        }
        assert!(
            !rendered.contains("review_round_limit_reached"),
            "{rendered}"
        );
    }

    #[test]
    fn analysis_outcome_prefers_workflow_action_over_gate_acceptance() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "stage",
            "item_id": "analysis-a2:outcome",
            "call_id": "analysis-a2:outcome",
            "tool_name": "stage_lifecycle",
            "status": "completed",
            "action": "completed",
            "visibility": "user",
            "payload": {
                "stage": "analysis_decision",
                "stage_label": "analysis_decision",
                "attempt": 2,
                "event": "completed",
                "decision": "accept",
                "gate_decision": "accept",
                "requested_workflow_action": "revisit",
                "resolved_workflow_action": "forced_forward",
                "workflow_action_reason": "revisit_limit",
                "reason": "review budget exhausted"
            }
        }))
        .expect("analysis outcome");

        let rendered = plain(
            TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(100),
        );

        assert!(
            rendered.contains("FORCED FORWARD · REVISIT LIMIT"),
            "{rendered}"
        );
        assert!(rendered.contains("Gate · ACCEPT"), "{rendered}");
        assert!(
            rendered.contains("Workflow · FORCED FORWARD REVISIT LIMIT"),
            "{rendered}"
        );
        assert!(
            rendered.contains("Reason · review budget exhausted"),
            "{rendered}"
        );
    }

    #[test]
    fn task_outcome_keeps_task_delivery_artifacts_and_caveats_distinct() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "task_outcome",
            "item_id": "task-1:outcome",
            "call_id": "task-1:outcome",
            "tool_name": "task_outcome",
            "status": "completed",
            "action": "completed",
            "visibility": "user",
            "payload": {
                "task_id": "task-1",
                "delivery_status": "completed_with_caveats",
                "delivery_class": "user_deliverable_complete_with_claim_boundary",
                "outcome": "advance_with_residual_findings",
                "outcome_class": "successful_completion_with_caveats",
                "task_success": true,
                "primary_artifacts": [{
                    "path": "/private/task_delivery/files/report.md",
                    "display_path": "output/report.md"
                }],
                "caveats": ["One residual finding remains."],
                "outcome_source": "runtime",
                "verification_status": "runtime_declared",
                "runtime_rationale": ["final package accepted"],
                "delivery_warnings": [{
                    "code": "delivery_tool_result_hash_mismatch",
                    "request_id": "call-warning-1"
                }]
            }
        }))
        .expect("task outcome");

        let rendered = plain(
            TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(100),
        );

        assert!(rendered.starts_with("╭─ OUTCOME "), "{rendered}");
        assert!(rendered.contains("TASK"), "{rendered}");
        assert!(rendered.contains("COMPLETED WITH CAVEATS"), "{rendered}");
        assert!(
            rendered.contains("Source · RUNTIME DECLARATION"),
            "{rendered}"
        );
        assert!(
            rendered.contains("Task · ADVANCE WITH RESIDUAL FINDINGS"),
            "{rendered}"
        );
        assert!(
            rendered.contains("Delivery · COMPLETED WITH CAVEATS"),
            "{rendered}"
        );
        assert!(
            rendered.contains("Artifacts · output/report.md"),
            "{rendered}"
        );
        assert!(
            rendered.contains("Caveat · One residual finding remains."),
            "{rendered}"
        );
        assert!(
            rendered.contains("Reason · final package accepted"),
            "{rendered}"
        );
        assert!(
            rendered.contains("Diagnostics · delivery tool result hash mismatch ×1"),
            "{rendered}"
        );
    }

    #[test]
    fn paused_stage_item_defers_to_the_dedicated_interaction_panel() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "stage",
            "item_id": "stage-literature-grounding:paused",
            "call_id": "stage-literature-grounding:paused",
            "tool_name": "stage_lifecycle",
            "status": "paused_for_human",
            "action": "paused",
            "visibility": "user",
            "payload": {
                "stage": "literature_grounding",
                "stage_label": "literature_grounding",
                "stage_ordinal": 1,
                "attempt": 1,
                "event": "paused",
                "status": "paused_for_human"
            }
        }))
        .expect("paused stage item");

        let rendered =
            TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(80);
        assert!(rendered.is_empty());
    }

    #[test]
    fn long_legacy_review_is_complete_in_main_history_and_transcript() {
        let summary = (1..=12)
            .map(|index| {
                format!(
                    "Finding {index}: 请核对 /Users/example/project/output/report-{index}.md 中的证据链。"
                )
            })
            .collect::<Vec<_>>()
            .join("\n");
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "review_cycle",
            "item_id": "review-long",
            "call_id": "review-long",
            "tool_name": "stage_review",
            "status": "partial_success",
            "action": "review_cycle",
            "visibility": "user",
            "payload": {
                "stage": "literature_grounding",
                "attempt": 1,
                "review_kind": "reviewer",
                "round": 1,
                "max_rounds": 2,
                "phase": "feedback_received",
                "outcome": "findings",
                "summary": summary
            }
        }))
        .expect("long review finding");
        let cell = TypedThreadItemHistoryCell::new(item, ThemePalette::default());

        for width in [20, 40, 80, 120, 190] {
            let lines = cell.display_lines(width);
            assert!(lines.iter().all(|line| line.width() <= width));
            let rendered = plain(lines);
            assert!(
                !rendered.contains("Ctrl+T for complete review text"),
                "{rendered}"
            );
            assert!(rendered.contains("Finding 1"), "{rendered}");
            assert!(rendered.contains("Finding 12"), "{rendered}");
        }
        let transcript = cell.copy_text().expect("full review transcript");
        assert!(transcript.contains("Finding 12"), "{transcript}");
    }

    #[test]
    fn checked_path_preserves_not_found_state() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "command_execution",
            "item_id": "check-input",
            "call_id": "check-input",
            "tool_name": "inspect_user_path",
            "status": "completed",
            "action": "check",
            "visibility": "user",
            "payload": {
                "command": "Checked input · not found",
                "target": "input",
                "display_target": "input",
                "result_state": "not_found",
                "path_exists": false
            }
        }))
        .expect("checked path item");

        let rendered =
            plain(TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(80));
        assert!(rendered.contains("Checked input · not found"), "{rendered}");
    }

    #[test]
    fn permission_result_preserves_safe_targets_in_history_and_transcript() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "interaction_result",
            "item_id": "interaction:permission-1:result",
            "call_id": "interaction:permission-1:result",
            "tool_name": "human_interaction",
            "status": "completed",
            "action": "resolve",
            "visibility": "user",
            "payload": {
                "interaction_id": "permission-1",
                "interaction_kind": "permission_request",
                "title": "Edit file",
                "decision": "Approved once",
                "request_summary": "Apply requested edit",
                "file_changes": [{
                    "path": "docs/report.md",
                    "change_kind": "overwrite",
                    "size_bytes": 42
                }]
            }
        }))
        .expect("permission result");
        let cell = TypedThreadItemHistoryCell::new(item, ThemePalette::default());
        let rendered = plain(cell.display_lines(100));
        let transcript = cell.copy_text().expect("permission transcript");

        assert!(
            rendered.contains("overwrite · docs/report.md"),
            "{rendered}"
        );
        assert!(rendered.contains("Approved once"), "{rendered}");
        assert!(
            transcript.contains("overwrite · docs/report.md"),
            "{transcript}"
        );
        assert!(!transcript.contains("content"), "{transcript}");
    }

    #[test]
    fn command_approval_receipt_is_compact_but_keeps_complete_target_in_transcript() {
        let command = "python - <<'PY'\nfrom pathlib import Path\nprint('FIRST_BODY_LINE')\nprint('LAST_BODY_LINE')\nPY";
        for lifecycle in ["approved", "denied"] {
            let item = DisplayItem::parse_value(&serde_json::json!({
                "schema_version": "ownpi.tui.item.v1", "kind": "permission_activity",
                "item_id": "approval-real-shape", "tool_name": "exec_command",
                "status": "completed", "visibility": "user",
                "payload": {"lifecycle": lifecycle,
                    "subject": {"kind": "tool", "primary": command, "qualifiers": ["Core", "Once"]}}
            }))
            .expect("approval receipt");
            let cell = TypedThreadItemHistoryCell::new(item, ThemePalette::default());
            for width in [40, 80, 120] {
                let lines = cell.display_lines(width);
                assert!(lines.len() <= 4 && lines.iter().all(|line| line.width() <= width));
                let shown = plain(lines);
                assert!(
                    shown.contains("python - <<'PY'") && shown.contains("+4 command lines"),
                    "{shown}"
                );
                assert!(!shown.contains("LAST_BODY_LINE"), "{shown}");
                let transcript = plain(cell.transcript_lines(width));
                assert!(
                    transcript.contains(command) && transcript.contains("Core · Once"),
                    "{transcript}"
                );
            }
            assert!(cell
                .copy_text()
                .expect("copy complete receipt")
                .contains(command));
        }
    }

    #[test]
    fn protocol_activity_updates_one_visible_audit_panel() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "protocol_activity",
            "item_id": "protocol:run-1:literature_grounding:a1:core:round-3",
            "call_id": "protocol:run-1:literature_grounding:a1:core:round-3",
            "tool_name": "provider_protocol",
            "status": "failed",
            "action": "protocol_recovery",
            "visibility": "user",
            "payload": {
                "stage": "literature_grounding",
                "attempt": 1,
                "component": "core",
                "operation": "terminal_tool_protocol",
                "round_id": "core-round-003",
                "corrections": [
                    {"attempt": 1, "kind": "required_terminal_tool", "summary": "Return a terminal tool."},
                    {"attempt": 2, "kind": "required_terminal_tool", "summary": "Return a terminal tool."}
                ],
                "status_code": "required_terminal_tool_missing",
                "required_actions": ["request_re_review", "complete_feedback"],
                "outcome": "failed",
                "summary": "provider repeatedly finished without a required terminal tool"
            }
        }))
        .expect("protocol activity item");
        let cell = TypedThreadItemHistoryCell::new(item, ThemePalette::default());
        let rendered = plain(cell.display_lines(100));

        assert!(rendered.contains("Protocol · Literature grounding · attempt 1 · Core"));
        assert_eq!(rendered.matches("Correction ").count(), 2, "{rendered}");
        assert!(rendered.contains("request_re_review or complete_feedback"));
        assert!(rendered.contains("Failed · required terminal tool missing"));
        assert!(cell.live_mutable_is_terminal());
    }

    #[test]
    fn protocol_activity_deduplicates_equal_outcome_and_status_code() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "protocol_activity",
            "item_id": "protocol-recovered",
            "call_id": "protocol-recovered",
            "tool_name": "provider_protocol",
            "status": "completed",
            "action": "protocol_recovery",
            "visibility": "user",
            "payload": {
                "stage": "literature_grounding",
                "attempt": 1,
                "component": "core",
                "operation": "terminal_tool_protocol",
                "round_id": "core-round-003",
                "status_code": "recovered",
                "outcome": "recovered"
            }
        }))
        .expect("recovered protocol activity");
        let rendered = plain(
            TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(100),
        );

        assert!(rendered.contains("Recovered"), "{rendered}");
        assert!(!rendered.contains("Recovered · recovered"), "{rendered}");
    }

    #[test]
    fn provider_failure_card_is_compact_but_details_keep_original_diagnostics() {
        let detail = serde_json::json!({
            "failure_diagnostics": {"http_status":400, "error_code":"unsupported_parameter",
                "payload_sha256":"opaque-internal-hash", "error_message":"Upstream request failed"},
            "resumable":true, "auto_resume_safe":false,
            "step_results":[{"summary":"required terminal control tool missing"}]
        })
        .to_string();
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version":"ownpi.tui.item.v1", "kind":"task_outcome", "item_id":"failed-outcome",
            "status":"failed", "visibility":"user", "payload": {
                "outcome":"failed", "delivery_status":"failed", "outcome_source":"backend_reply",
                "failure_cause":"native tool loop returned terminal failure", "failure_detail":detail,
                "runtime_rationale":["generic internal failure explanation"],
                "failure_facts":[["Error","HTTP 400 · unsupported_parameter"],
                    ["Message","Upstream request failed"], ["Retryable","No"],
                    ["Recovery","Repair provider input before retry"],
                    ["Resume","Available · not started · backend requires checks"]]
            }
        })).unwrap();
        let cell = TypedThreadItemHistoryCell::new(item, ThemePalette::default());
        for width in [40, 80, 120] {
            let lines = cell.display_lines(width);
            assert!(
                lines.iter().all(|line| line.width() <= width),
                "{width}: {lines:?}"
            );
            let rendered = plain(lines);
            let compact: String = rendered
                .chars()
                .filter(|c| !c.is_whitespace() && *c != '│')
                .collect();
            assert!(
                compact.contains("HTTP400·unsupported_parameter"),
                "{rendered}"
            );
            assert!(compact.contains("backendrequireschecks"), "{rendered}");
            assert!(compact.contains("ctrl+t"), "{rendered}");
            assert!(
                !rendered.contains("opaque-internal-hash") && !rendered.contains("step_results"),
                "{rendered}"
            );
            assert!(
                !rendered.contains("generic internal failure explanation"),
                "{rendered}"
            );
            assert!(rendered.contains("FAILED"), "{rendered}");
        }
        let transcript = cell.copy_text().unwrap();
        assert!(transcript.contains(&detail), "{transcript}");
        assert!(transcript.contains("native tool loop returned terminal failure"));
        for width in [40, 80, 120] {
            let pager = crate::transcript_pager::TranscriptPagerState::new(
                cell.transcript_lines(width),
                width,
            );
            let (lines, _) = pager.render_lines(u16::MAX);
            assert!(lines.iter().all(|line| line.width() < width));
            let compact: String = plain(lines)
                .chars()
                .filter(|c| !c.is_whitespace())
                .collect();
            let original: String = transcript.chars().filter(|c| !c.is_whitespace()).collect();
            assert_eq!(compact, original, "Full diagnostics must survive wrapping");
        }
    }

    #[test]
    fn long_unstructured_failure_details_are_available_without_flooding_the_card() {
        let detail = "Original backend diagnostic. ".repeat(40);
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version":"ownpi.tui.item.v1", "kind":"task_outcome", "item_id":"detail-only",
            "status":"failed", "visibility":"user", "payload": {
                "outcome":"failed", "failure_detail":detail
            }
        }))
        .unwrap();
        let cell = TypedThreadItemHistoryCell::new(item, ThemePalette::default());
        let rendered = plain(cell.display_lines(80));
        assert!(rendered.contains("ctrl+t"), "{rendered}");
        assert!(
            !rendered.contains("Original backend diagnostic"),
            "{rendered}"
        );
        assert!(cell.copy_text().unwrap().contains(detail.trim_end()));
    }

    #[test]
    fn failed_task_headline_cannot_be_overridden_by_unknown_delivery() {
        for width in [40, 80, 120] {
            let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version":"ownpi.tui.item.v1", "kind":"task_outcome", "item_id":"cold-failure",
            "visibility":"user", "status":"failed", "payload":{
                "outcome":"failed", "delivery_status":"unknown", "outcome_source":"run_finalized",
                "failure_facts":[["Error","upstream_error"],["Resume safety","Not recorded"]],
                "final_request_attempt_count":6, "final_request_retry_count":5,
                "recovery_actions":["Open /resume; resuming requires your action."]
            }}))
            .unwrap();
            let lines =
                TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(width);
            assert!(lines.iter().all(|line| line.width() <= width));
            let rendered = plain(lines);
            let compact: String = rendered
                .chars()
                .filter(|c| !c.is_whitespace() && *c != '│')
                .collect();
            let headline = rendered.lines().take(3).collect::<String>();
            assert!(
                headline.contains("FAILED") && !headline.contains("UNKNOWN"),
                "{rendered}"
            );
            assert!(compact.contains("Delivery·UNKNOWN"), "{rendered}");
            assert!(compact.contains("6attempts·5retries"), "{rendered}");
            assert!(compact.contains("/resume"), "{rendered}");
        }
    }

    #[test]
    fn paper_fetch_input_and_citation_identifier_labels_preserve_their_type() {
        for width in [40, 80, 120] {
            let item = DisplayItem::parse_value(&serde_json::json!({
                "schema_version":"ownpi.tui.item.v1", "kind":"command_execution", "item_id":"fetch-failed",
                "tool_name":"paper_fetch", "visibility":"user", "status":"failed", "payload":{
                    "command":"paper_fetch", "metadata":{"identifier":"跨被试 EEG primary study"},
                    "error":"identifier must be a DOI, arXiv identifier, or HTTP(S) URL"
                }})).unwrap();
            let rendered = plain(
                TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(width),
            );
            assert!(rendered.contains("Input"), "{rendered}");
            assert!(!rendered.contains("• DOI"), "{rendered}");
            for (identifier, label) in [
                ("doi:10.1234/example", "DOI"),
                ("arxiv:2601.01234", "arXiv"),
                ("https://example.org/paper", "URL"),
            ] {
                let item = DisplayItem::parse_value(&serde_json::json!({
                    "schema_version":"ownpi.tui.item.v1", "kind":"command_execution", "item_id":"citation",
                    "tool_name":"citation_resolve", "visibility":"user", "status":"completed", "payload":{
                        "command":"citation_resolve", "resolved_identifier":identifier
                    }})).unwrap();
                let rendered = plain(
                    TypedThreadItemHistoryCell::new(item, ThemePalette::default())
                        .display_lines(width),
                );
                assert!(rendered.contains(label), "{rendered}");
                if label != "DOI" {
                    assert!(!rendered.contains("DOI "), "{rendered}");
                }
            }
        }
    }

    #[test]
    fn failed_task_outcome_explains_runtime_protocol_recovery() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "task_outcome",
            "item_id": "task-1:outcome",
            "call_id": "task-1:outcome",
            "tool_name": "run_outcome",
            "status": "failed",
            "action": "complete_task",
            "visibility": "user",
            "payload": {
                "task_id": "task-1",
                "delivery_status": "failed",
                "outcome": "runtime_provider_or_protocol_failure",
                "task_success": false,
                "outcome_source": "runtime",
                "failure_domain": "runtime_protocol",
                "failure_code": "required_terminal_tool_missing",
                "failure_summary": "provider repeatedly finished without a required terminal tool",
                "recovery_actions": ["request_re_review", "complete_feedback"]
            }
        }))
        .expect("failed task outcome");
        let rendered = plain(
            TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(100),
        );

        assert!(
            rendered.contains("Failure domain · runtime protocol"),
            "{rendered}"
        );
        assert!(
            rendered.contains("Failure code · required terminal tool missing"),
            "{rendered}"
        );
        assert!(
            rendered.contains("Reason · provider repeatedly finished"),
            "{rendered}"
        );
        assert_eq!(rendered.matches("Next ·").count(), 2, "{rendered}");
    }

    #[test]
    fn failed_task_outcome_prefers_readable_cause_over_machine_error_fields() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "task_outcome",
            "item_id": "task-provider:outcome",
            "call_id": "task-provider:outcome",
            "tool_name": "run_outcome",
            "status": "failed",
            "action": "complete_task",
            "visibility": "user",
            "payload": {
                "task_id": "task-provider",
                "delivery_status": "failed",
                "outcome": "runtime_provider_or_protocol_failure",
                "task_success": false,
                "outcome_source": "runtime",
                "failure_domain": "provider_transport",
                "failure_code": "server_is_overloaded",
                "failure_summary": "SSE explicit_provider_error",
                "failure_cause": "Provider unavailable",
                "failure_detail": "Server overloaded",
                "provider_request_count": 4,
                "provider_activity_scope": "audit",
                "provider_retry_count": 8,
                "final_request_attempt_count": 1,
                "final_request_retry_count": 0
            }
        }))
        .expect("failed provider task outcome");
        let cell = TypedThreadItemHistoryCell::new(item, ThemePalette::default());
        let rendered = plain(cell.display_lines(100));

        assert!(
            rendered.contains("Cause · Provider unavailable"),
            "{rendered}"
        );
        assert!(
            rendered.contains("Detail · Server overloaded"),
            "{rendered}"
        );
        assert!(rendered.contains("Review provider activity · 4 requests · 8 retries"));
        assert!(rendered.contains("Final request · 1 attempt · 0 retries"));
        assert!(!rendered.contains("Failure domain"), "{rendered}");
        assert!(!rendered.contains("Failure code"), "{rendered}");
        assert!(
            !rendered.contains("SSE explicit_provider_error"),
            "{rendered}"
        );
        let transcript = cell.copy_text().expect("task failure transcript");
        assert!(transcript.contains("server_is_overloaded"));
        assert!(transcript.contains("SSE explicit_provider_error"));
    }

    #[test]
    fn final_request_scope_does_not_pretend_to_be_total_provider_activity() {
        for scope in ["final_request", "", "unrecognized"] {
            let item = DisplayItem::parse_value(&serde_json::json!({
                "schema_version":"ownpi.tui.item.v1", "kind":"task_outcome",
                "item_id":"outcome", "status":"failed", "tool_name":"run_outcome",
                "call_id":"outcome", "action":"complete_task", "visibility":"user",
                "payload": {"failure_cause":"Provider unavailable", "provider_activity_scope":scope,
                    "provider_request_count":1, "provider_retry_count":0,
                    "final_request_attempt_count":1, "final_request_retry_count":0}
            }))
            .unwrap();
            let rendered = plain(
                TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(100),
            );
            assert!(
                !rendered.to_lowercase().contains("provider activity"),
                "{rendered}"
            );
            assert!(
                rendered.contains("Final request · 1 attempt · 0 retries"),
                "{rendered}"
            );
        }
    }

    #[test]
    fn ambiguous_citation_is_unconfirmed_and_shows_candidate_year_and_doi() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version":"ownpi.tui.item.v1", "kind":"command_execution",
            "item_id":"citation", "status":"completed", "tool_name":"citation_resolve",
            "call_id":"citation", "visibility":"user",
            "action":"resolve_citation", "payload": {
                "result_state":"ambiguous", "requested_title":"Speech tracking",
                "requested_author":"Mirkovic", "requested_year":"2016",
                "citation_candidates":[{"title":"Speech tracking", "authors":["Mirkovic"],
                    "year":"2015", "doi":"10.1088/1741-2560/12/4/046007"}],
                "diagnostics":["Identity requires confirmation"],
                "citation_guidance":"Confirm the candidate DOI."
            }
        }))
        .unwrap();
        for width in [40, 100, 220] {
            let rendered = plain(
                TypedThreadItemHistoryCell::new(item.clone(), ThemePalette::default())
                    .display_lines(width),
            );
            assert!(
                rendered.contains("Citation identity unconfirmed"),
                "{rendered}"
            );
            assert!(rendered.contains("Candidate"), "{rendered}");
            assert!(
                rendered.contains("2016") && rendered.contains("2015"),
                "{rendered}"
            );
            assert!(
                rendered.contains("10.1088/1741-2560/12/4/046007"),
                "{rendered}"
            );
            assert!(
                !rendered.contains("Resolved") && !rendered.contains("lookup completed"),
                "{rendered}"
            );
        }
    }

    #[test]
    fn failed_review_transcript_keeps_protocol_and_transport_distinct() {
        for (domain, cause, heading) in [
            (
                "runtime_protocol",
                "Review protocol failed",
                "Protocol error:",
            ),
            (
                "provider_transport",
                "Provider unavailable",
                "Provider error:",
            ),
        ] {
            let item = DisplayItem::parse_value(&serde_json::json!({
                "schema_version": "ownpi.tui.item.v1",
                "kind": "review_cycle",
                "item_id": "review-failed",
                "call_id": "review-failed",
                "tool_name": "stage_review",
                "status": "failed",
                "action": "review_cycle",
                "visibility": "user",
                "payload": {
                    "phase": "failed", "outcome": "failed",
                    "review_kind": "reviewer", "round": 1,
                    "terminal_source": "bridge_synthesized",
                    "failure_domain": domain, "failure_cause": cause,
                    "transcript": "Typed diagnostic evidence.",
                    "provider_request_count": 1,
                    "final_request_attempt_count": 1
                }
            }))
            .expect("failed review");
            let cell = TypedThreadItemHistoryCell::new(item, ThemePalette::default());
            let transcript = cell.copy_text().expect("review transcript");
            assert!(transcript.contains(heading), "{transcript}");
            assert!(transcript.contains(cause), "{transcript}");
            assert!(transcript.contains("Typed diagnostic evidence."));
            if domain == "runtime_protocol" {
                assert!(!transcript.contains("Provider error:"), "{transcript}");
            }
            assert_eq!(cell.audit_terminal_label(), Some("Review failed"));
        }
    }

    fn plan_update_item() -> DisplayItem {
        DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "plan_update",
            "item_id": "plan-1",
            "call_id": "plan-1",
            "tool_name": "update_plan",
            "status": "completed",
            "action": "update",
            "visibility": "user",
            "payload": {
                "explanation": "I’ll update Grafana call error handling by adding retries and clearer messages when the backend is unreachable.",
                "plan": [
                    {"step": "Investigate existing error paths and logging around HTTP timeouts", "status": "completed"},
                    {"step": "Harden Grafana client error handling with retry/backoff and user-friendly messages", "status": "in_progress"},
                    {"step": "Add tests for transient failure scenarios and surfacing to the UI", "status": "pending"}
                ],
                "transcript": "raw provider receipt that must stay out of copy text"
            }
        }))
        .expect("typed plan item")
    }

    #[test]
    fn plan_update_matches_codex_wrapping_at_width_32() {
        let cell = TypedThreadItemHistoryCell::new(plan_update_item(), ThemePalette::default());

        assert_eq!(
            plain(cell.display_lines(32)),
            concat!(
                "⦁ Updated Plan\n",
                "  └ I’ll update Grafana call\n",
                "    error handling by adding\n",
                "    retries and clearer messages\n",
                "    when the backend is\n",
                "    unreachable.\n",
                "    ✔ Investigate existing error\n",
                "      paths and logging around\n",
                "      HTTP timeouts\n",
                "    □ Harden Grafana client\n",
                "      error handling with retry/\n",
                "      backoff and user-friendly\n",
                "      messages\n",
                "    □ Add tests for transient\n",
                "      failure scenarios and\n",
                "      surfacing to the UI"
            )
        );
    }

    #[test]
    fn plan_update_matches_codex_wrapping_at_width_40_and_styles_statuses() {
        let cell = TypedThreadItemHistoryCell::new(plan_update_item(), ThemePalette::default());
        let lines = cell.display_lines(40);

        assert_eq!(
            plain(lines.clone()),
            concat!(
                "⦁ Updated Plan\n",
                "  └ I’ll update Grafana call error\n",
                "    handling by adding retries and\n",
                "    clearer messages when the backend is\n",
                "    unreachable.\n",
                "    ✔ Investigate existing error paths\n",
                "      and logging around HTTP timeouts\n",
                "    □ Harden Grafana client error\n",
                "      handling with retry/backoff and\n",
                "      user-friendly messages\n",
                "    □ Add tests for transient failure\n",
                "      scenarios and surfacing to the UI"
            )
        );
        let styled = lines
            .iter()
            .flat_map(|line| line.spans.iter())
            .filter(|span| !span.content.trim().is_empty())
            .collect::<Vec<_>>();
        let completed = styled
            .iter()
            .find(|span| span.content.contains("Investigate"))
            .expect("completed step span");
        assert!(completed.style.add_modifier.contains(Modifier::CROSSED_OUT));
        assert!(completed.style.add_modifier.contains(Modifier::DIM));
        let in_progress = styled
            .iter()
            .find(|span| span.content.contains("Harden"))
            .expect("in-progress step span");
        assert_eq!(in_progress.style.fg, Some(ratatui::style::Color::Cyan));
        assert!(in_progress.style.add_modifier.contains(Modifier::BOLD));
        let pending = styled
            .iter()
            .find(|span| span.content.contains("Add tests"))
            .expect("pending step span");
        assert!(pending.style.add_modifier.contains(Modifier::DIM));
        let copied = cell.copy_text().expect("semantic plan copy text");
        assert!(copied.contains("[x] Investigate"));
        assert!(!copied.contains("raw provider receipt"));
    }

    #[test]
    fn request_validation_failure_is_not_labeled_as_permission_or_execution_failure() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version": "ownpi.tui.item.v1",
            "kind": "command_execution",
            "item_id": "invalid-exec-1",
            "call_id": "invalid-exec-1",
            "tool_name": "exec_command",
            "status": "failed",
            "action": "execute",
            "visibility": "user",
            "payload": {
                "command": "rg --files input",
                "failure_class": "request_validation",
                "error": "prefix_rule requires sandbox_permissions=require_escalated"
            }
        }))
        .expect("typed validation failure");
        let rendered = plain(
            TypedThreadItemHistoryCell::new(item, ThemePalette::default()).display_lines(100),
        );

        assert!(
            rendered.contains("Invalid request rg --files input"),
            "{rendered}"
        );
        assert!(!rendered.contains("Failed rg --files input"), "{rendered}");
        assert!(!rendered.contains("permission denied"), "{rendered}");
    }
}

#[cfg(test)]
mod observed_exit_tests {
    use super::*;
    #[test]
    fn observed_exit_exposes_specific_cause_without_claiming_task_completion() {
        let item = DisplayItem::parse_value(&serde_json::json!({
            "schema_version":"ownpi.tui.item.v1", "kind":"task_outcome", "item_id":"observed-exit",
            "status":"failed", "visibility":"user", "payload":{
                "outcome":"unexpected_exit", "outcome_source":"runtime_status_observation",
                "failure_facts":[["Exception","PoolTimeout"],["Cause","Timed out waiting for an available HTTP connection"]],
                "failure_detail":"{\"error_type\":\"PoolTimeout\"}",
                "recovery_actions":["Open /resume to inspect; nothing has been restarted."]
            }})).unwrap();
        let cell = TypedThreadItemHistoryCell::new(item, ThemePalette::default());
        for width in [40, 120] {
            let text = cell
                .display_lines(width)
                .iter()
                .map(crate::wrapping::line_plain_text)
                .collect::<Vec<_>>()
                .join("\n");
            assert!(
                text.contains("UNEXPECTED EXIT") && text.contains("PoolTimeout"),
                "{text}"
            );
            assert!(
                text.contains("/resume") && text.contains("ctrl+t"),
                "{text}"
            );
            assert!(
                !text.contains("COMPLETED") && !text.contains("runtime_status_observation"),
                "{text}"
            );
        }
    }
    #[test]
    fn timeout_summary_preserves_units_and_never_invents_attempts() {
        for (raw, expected) in [
            (
                "web fetch failed: curl: (28) Connection timed out after 40006 milliseconds",
                "Timed out after 40006 ms",
            ),
            (
                "Connection timed out after 40 seconds",
                "Timed out after 40 s",
            ),
            ("Timed out after 2 attempts", "Timed out after 2 attempts"),
            ("Timeout after 3 retries", "Timed out after 3 retries"),
            ("Timeout after 40006", "Timed out"),
            ("Timeout after 4 unknowns", "Timed out"),
        ] {
            assert_eq!(compact_legacy_error(raw), expected);
        }
    }

    #[test]
    fn hosted_find_page_displays_the_pattern_and_url_without_claiming_found() {
        for status in ["completed", "running", "failed"] {
            let item = DisplayItem::parse_value(&serde_json::json!({
                "schema_version":"ownpi.tui.item.v1", "item_id":"find-page", "visibility":"user",
                "kind":"web_search", "status":status, "payload":{
                    "query":"official", "web_action":{"type":"find_in_page", "pattern":"official",
                    "url":"https://example.com/paper"}}}))
            .unwrap();
            let cell = TypedThreadItemHistoryCell::new(item, ThemePalette::default());
            let text = cell
                .display_lines(120)
                .iter()
                .map(crate::wrapping::line_plain_text)
                .collect::<Vec<_>>()
                .join("\n");
            assert!(
                text.contains("official") && text.contains("https://example.com/paper"),
                "{text}"
            );
            assert!(
                !text.contains("Ran web_search_call") && !text.contains("Found"),
                "{text}"
            );
            assert!(
                text.contains(match status {
                    "running" => "Searching page",
                    "failed" => "Failed to search page",
                    _ => "Searched page",
                }),
                "{text}"
            );
        }
    }
}
