//! Presentation-only subagent navigation and batch cards. No execution authority.
use crate::display_item::AuditContext;
use crate::history_cell::HistoryCell;
use crate::theme::ThemePalette;
use crate::transcript_pager::TranscriptPagerState;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use serde_json::Value;
use std::collections::HashMap;
use std::sync::Arc;
use unicode_segmentation::UnicodeSegmentation;
use unicode_width::UnicodeWidthStr;

pub(crate) fn text(value: &Value, key: &str) -> String {
    value[key].as_str().unwrap_or_default().to_string()
}
pub(crate) fn task_key(batch: &Value, task: &Value) -> String {
    format!(
        "{}:{}",
        text(batch, "item_id"),
        task["task_index"].as_u64().unwrap_or(0)
    )
}
pub(crate) fn same_agent(a: &Value, b: &Value) -> bool {
    a.is_object()
        && b.is_object()
        && ["run_id", "conversation_id", "actor_id"]
            .iter()
            .all(|key| a[*key].as_str().is_some() && a[*key] == b[*key])
}
pub(crate) fn preview(value: &str, max: usize) -> String {
    let line = value
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .chars()
        .filter(|ch| !ch.is_control())
        .collect::<String>();
    if UnicodeWidthStr::width(line.as_str()) <= max {
        return line;
    }
    let mut out = String::new();
    let mut used = 0;
    for grapheme in line.graphemes(true) {
        let width = UnicodeWidthStr::width(grapheme);
        if used + width > max.saturating_sub(1) {
            break;
        }
        out.push_str(grapheme);
        used += width;
    }
    if max > 0 {
        out.push('…');
    }
    out
}

/// One classification shared by the activity row and batch cards.
#[derive(Default)]
pub(crate) struct TaskCounts {
    pub running: usize,
    pub done: usize,
    pub failed: usize,
    pub cancelled: usize,
    pub waiting: usize,
    pub unconfirmed: usize,
}
impl TaskCounts {
    pub fn observe(&mut self, task: &Value) {
        match text(task, "terminal_status").as_str() {
            "failed" => self.failed += 1,
            "cancelled" => self.cancelled += 1,
            "completed" => self.done += 1,
            _ if task["completion_committed"] == true => self.done += 1,
            _ if task["display_status"] == "State unconfirmed"
                || task["status"] == "unconfirmed" =>
            {
                self.unconfirmed += 1
            }
            _ if task["display_status"] == "Running"
                || (text(task, "display_status").is_empty() && task["status"] == "running") =>
            {
                self.running += 1
            }
            _ if matches!(
                text(task, "display_status").as_str(),
                "Pending" | "Queued" | "Waiting" | "Needs input" | "Needs approval"
            ) || matches!(
                text(task, "status").as_str(),
                "pending" | "created" | "waiting"
            ) =>
            {
                self.waiting += 1
            }
            _ => self.unconfirmed += 1,
        }
    }
    pub fn summaries(&self) -> Vec<String> {
        [
            (self.running, "running"),
            (self.failed, "failed"),
            (self.cancelled, "cancelled"),
            (self.done, "finished"),
            (self.waiting, "waiting"),
            (self.unconfirmed, "unconfirmed"),
        ]
        .into_iter()
        .filter(|(n, _)| *n > 0)
        .map(|(n, label)| format!("{n} {label}"))
        .collect()
    }
}

pub(crate) fn activity_text(task: &Value) -> String {
    let activity = &task["activity"];
    if text(activity, "label").is_empty() {
        return String::new();
    }
    let description = format!("{} {}", text(activity, "label"), text(activity, "detail"))
        .trim()
        .to_string();
    let at = activity_timestamp(&text(activity, "timestamp"));
    if at.is_empty() {
        description
    } else {
        format!("{description} · at {at}")
    }
}

/// Format activity time only at the presentation boundary; keep event data intact.
fn activity_timestamp(value: &str) -> String {
    chrono::DateTime::parse_from_rfc3339(value)
        .map(|time| {
            time.with_timezone(&chrono::Local)
                .format("%Y-%m-%d %H:%M:%S")
                .to_string()
        })
        .unwrap_or_else(|_| value.to_string())
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum PopupKind {
    Agents,
    Requests,
}
#[derive(Debug)]
pub(crate) struct AgentPopup {
    pub kind: PopupKind,
    pub selected: String,
}
#[derive(Debug, Default)]
pub(crate) struct AgentUi {
    pub batches: Vec<Value>,
    pub requests: Vec<Value>,
    pub popup: Option<AgentPopup>,
    pub active: Option<String>,
    pub details: bool,
    pub raw: bool,
    pub pager: TranscriptPagerState,
    pub saved: HashMap<String, TranscriptPagerState>,
    pub suspended_main_pager: Option<TranscriptPagerState>,
    pub selected_request: Option<String>,
    pub main_draft: Option<String>,
}
impl AgentUi {
    pub fn tasks(&self) -> Vec<(String, Value)> {
        self.batches
            .iter()
            .flat_map(|batch| {
                batch["tasks"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .map(|task| (task_key(batch, task), task.clone()))
            })
            .collect()
    }
    pub fn task(&self) -> Option<Value> {
        let key = self.active.as_ref()?;
        self.tasks()
            .into_iter()
            .find(|(id, _)| id == key)
            .map(|(_, task)| task)
    }
    pub fn status(&self, task: &Value) -> String {
        text(task, "display_status")
    }
    pub fn label(&self, task: &Value) -> String {
        let name = text(task, "task_name");
        let duplicates = self
            .tasks()
            .iter()
            .filter(|(_, t)| text(t, "task_name") == name)
            .count()
            > 1;
        preview(
            &if duplicates {
                format!(
                    "{} · {} · {}",
                    name,
                    text(task, "stage").replace('_', " "),
                    text(task, "child_id").chars().take(8).collect::<String>()
                )
            } else {
                name
            },
            512,
        )
    }
    fn request_task(&self, request: &Value) -> Option<Value> {
        if text(request, "actor_kind") != "subagent" {
            return None;
        }
        let matches = self
            .tasks()
            .into_iter()
            .filter(|(_, task)| {
                !text(task, "child_id").is_empty()
                    && text(task, "child_id") == text(request, "actor_id")
                    && (text(request, "run_id").is_empty()
                        || text(task, "run_id") == text(request, "run_id"))
            })
            .collect::<Vec<_>>();
        (matches.len() == 1).then(|| matches[0].1.clone())
    }
    pub fn request_owner(&self, request: &Value) -> String {
        if let Some(task) = self.request_task(request) {
            return self.label(&task);
        }
        if text(request, "actor_kind") == "subagent" {
            "Unverified agent".into()
        } else {
            let actor = text(request, "actor_kind");
            if actor.is_empty() {
                "Main".into()
            } else {
                preview(&actor, 80)
            }
        }
    }
    fn group_label(&self, task: &Value) -> String {
        let parent = &task["parent_ref"];
        let owner = match text(parent, "actor_kind").as_str() {
            "subagent" => self
                .tasks()
                .iter()
                .find(|(_, t)| same_agent(&t["agent_ref"], parent))
                .map(|(_, t)| self.label(t))
                .unwrap_or_else(|| "Unverified parent".into()),
            "reviewer" => format!(
                "Reviewer · Round {}",
                task["audit_context"]["review_round"].as_u64().unwrap_or(1)
            ),
            _ => "Core".into(),
        };
        format!(
            "{owner} · {} · {}",
            text(task, "stage").replace('_', " "),
            text(task, "run_id").chars().take(16).collect::<String>()
        )
    }
    pub fn popup_rows(&self) -> Vec<(String, String, String)> {
        match self.popup.as_ref().map(|p| p.kind) {
            Some(PopupKind::Requests) => self
                .requests
                .iter()
                .map(|request| {
                    let id = text(request, "session_id");
                    let owner = self.request_owner(request);
                    let context = self
                        .request_task(request)
                        .map(|task| self.group_label(&task))
                        .unwrap_or_else(|| {
                            if text(request, "actor_kind") == "subagent" {
                                "Unverified request scope".into()
                            } else {
                                text(request, "stage").replace('_', " ")
                            }
                        });
                    (
                        id,
                        format!(
                            "{} · {}",
                            if text(request, "kind") == "permission" {
                                "Approval"
                            } else {
                                "Question"
                            },
                            owner
                        ),
                        format!("{} · {}", context, text(request, "tool_name")),
                    )
                })
                .collect(),
            _ => {
                // First-seen groups retain admission order within each group;
                // status updates never participate in ordering.
                let mut groups: Vec<(String, Vec<(String, Value)>)> = Vec::new();
                for (id, task) in self.tasks() {
                    let group = format!(
                        "{}:{}:{}:{}",
                        text(&task, "run_id"),
                        text(&task, "stage"),
                        task["parent_ref"],
                        task["audit_context"]["review_round"]
                    );
                    if let Some((_, rows)) = groups.iter_mut().find(|(key, _)| key == &group) {
                        rows.push((id, task));
                    } else {
                        groups.push((group, vec![(id, task)]));
                    }
                }
                std::iter::once((
                    "main".into(),
                    "Main [default]".into(),
                    "Return to the research timeline".into(),
                ))
                .chain(
                    groups
                        .into_iter()
                        .flat_map(|(_, rows)| rows)
                        .map(|(id, task)| (id, self.label(&task), self.group_label(&task))),
                )
                .collect()
            }
        }
    }
    pub fn save_position(&mut self) {
        if let Some(id) = &self.active {
            self.saved.insert(
                format!("{id}:{}:{}", self.details, self.raw),
                self.pager.clone(),
            );
        }
    }
    pub fn restore_position(&mut self) {
        self.pager = self
            .active
            .as_ref()
            .and_then(|id| {
                self.saved
                    .get(&format!("{id}:{}:{}", self.details, self.raw))
            })
            .cloned()
            .unwrap_or_default();
    }
}
#[derive(Debug)]
pub(crate) struct SubagentBatchCell {
    pub batch: Value,
    pub source: Value,
    pub theme: ThemePalette,
    pub audit: Option<AuditContext>,
}
impl SubagentBatchCell {
    pub fn new(batch: Value, source: Value, theme: ThemePalette) -> Arc<dyn HistoryCell> {
        let audit = crate::display_item::DisplayItem::parse_value(&source)
            .and_then(|i| i.common().audit_context.clone());
        Arc::new(Self {
            batch,
            source,
            theme,
            audit,
        })
    }
    fn state_color(&self, status: &str) -> ratatui::style::Color {
        match status {
            "Feedback submitted" => self.theme.success,
            "Failed" => self.theme.error,
            "Needs input" | "Needs approval" | "State unconfirmed" | "Cancelled" => {
                self.theme.warning
            }
            _ => self.theme.text_muted,
        }
    }
}
impl HistoryCell for SubagentBatchCell {
    fn audit_context(&self) -> Option<&AuditContext> {
        self.audit.as_ref()
    }
    fn display_lines(&self, width: usize) -> Vec<Line<'static>> {
        let width = width.max(1).min(120);
        let tasks = self.batch["tasks"].as_array().cloned().unwrap_or_default();
        if width < 8 {
            return vec![Line::from(preview(
                &format!("{} Agents", tasks.len()),
                width,
            ))];
        }
        let mut totals = TaskCounts::default();
        for task in &tasks {
            totals.observe(task);
        }
        let pending = tasks
            .iter()
            .filter_map(|task| task["request_ids"].as_array())
            .map(Vec::len)
            .sum::<usize>();
        let counts = totals.summaries();
        let color = if pending > 0 || totals.unconfirmed > 0 {
            self.theme.warning
        } else if tasks
            .iter()
            .any(|task| text(task, "terminal_status") == "failed")
        {
            self.theme.error
        } else {
            self.theme.success
        };
        // An enclosing review owns the rail; the batch only supplies its inset.
        let rail = || {
            Span::styled(
                if self.audit.is_some() { "  " } else { "│ " },
                Style::default().fg(color),
            )
        };
        let wrap = |text: String, style: Style| {
            crate::wrapping::word_wrap_line(
                &Line::from(Span::styled(text, style)),
                crate::wrapping::RtOptions::new(width)
                    .initial_indent(Line::from(rail()))
                    .subsequent_indent(Line::from(rail())),
            )
            .iter()
            .map(crate::wrapping::line_to_static)
            .collect::<Vec<_>>()
        };
        let mut lines = wrap(
            format!("≡ {} Agents ({})", tasks.len(), counts.join(" · ")),
            Style::default()
                .fg(self.theme.text)
                .add_modifier(Modifier::BOLD),
        );
        if pending > 0 {
            let hints = format!("F4 Requests ({pending})");
            if lines.len() == 1
                && lines[0].width() + 3 + UnicodeWidthStr::width(hints.as_str()) <= width
            {
                lines[0].spans.push(Span::styled(
                    format!(" · {hints}"),
                    Style::default().fg(self.theme.text_muted),
                ));
            } else {
                lines.extend(wrap(hints, Style::default().fg(self.theme.text_muted)));
            }
        }
        if !text(&self.batch, "error").is_empty() {
            lines.extend(wrap(
                "Source unavailable · /audit for details".into(),
                Style::default().fg(self.theme.warning),
            ));
        }
        // Core and Reviewer share the same task rows throughout the lifecycle.
        // Completion must not erase task identity; full records remain in F2.
        for (index, task) in tasks.iter().take(6).enumerate() {
            if index > 0 {
                lines.push(Line::from(vec![
                    rail(),
                    Span::styled(
                        "─".repeat(width.saturating_sub(2)),
                        Style::default().fg(self.theme.border),
                    ),
                ]));
            }
            let status = text(task, "display_status");
            let icon = match status.as_str() {
                "Running" => "○",
                "Feedback submitted" => "✓",
                "Failed" => "×",
                "Needs approval" | "Needs input" => "!",
                "State unconfirmed" => "?",
                "Execution completed" => "◌",
                _ => "·",
            };
            let label = text(&task["activity"], "label");
            let detail = if matches!(status.as_str(), "Failed" | "Cancelled") {
                status.clone()
            } else if task["completion_committed"] == true {
                "Feedback submitted".to_string()
            } else if matches!(
                status.as_str(),
                "Needs approval" | "Needs input" | "Failed" | "Cancelled" | "State unconfirmed"
            ) {
                status.clone()
            } else if status == "Execution completed" {
                "Execution completed · feedback pending".into()
            } else if !label.is_empty() {
                format!("{label} {}", text(&task["activity"], "detail"))
                    .trim()
                    .to_string()
            } else {
                status.clone()
            };
            let badge = preview(
                &text(task, "task_name"),
                width.saturating_sub(12).min(30).max(4),
            );
            let mut spans = vec![
                rail(),
                Span::styled(
                    format!("{icon} "),
                    Style::default().fg(self.state_color(&status)),
                ),
                Span::styled(
                    format!(" {badge} "),
                    Style::default().fg(self.theme.text).bg(self
                        .theme
                        .shadow_bg
                        .or(self.theme.elevated_bg)
                        .unwrap_or(ratatui::style::Color::Reset)),
                ),
            ];
            let used = spans.iter().map(Span::width).sum::<usize>();
            if used + 3 + UnicodeWidthStr::width(detail.as_str()) <= width {
                spans.push(Span::styled(
                    format!(" · {detail}"),
                    Style::default().fg(self.theme.text),
                ));
                lines.push(Line::from(spans));
            } else {
                lines.push(Line::from(spans));
                lines.push(Line::from(vec![
                    rail(),
                    Span::styled(
                        format!("    {}", preview(&detail, width.saturating_sub(6))),
                        Style::default().fg(self.theme.text),
                    ),
                ]));
            }
            let last_activity = activity_text(task);
            if !last_activity.is_empty() && status != "Running" && status != "Feedback submitted" {
                lines.extend(wrap(
                    format!("    Last activity: {last_activity}"),
                    Style::default().fg(self.theme.text_muted),
                ));
            } else if status == "Running" && !text(&task["activity"], "timestamp").is_empty() {
                lines.extend(wrap(
                    format!(
                        "    Observed at {}",
                        activity_timestamp(&text(&task["activity"], "timestamp"))
                    ),
                    Style::default().fg(self.theme.text_muted),
                ));
            }
            if width >= 12 {
                let summary = if task["completion_committed"] == true {
                    text(task, "completion_content")
                } else if text(task, "terminal_status") == "failed" {
                    text(task, "error")
                } else {
                    String::new()
                };
                if !summary.is_empty() {
                    lines.push(Line::from(vec![
                        rail(),
                        Span::styled(
                            format!("    {}", preview(&summary, width.saturating_sub(6))),
                            Style::default().fg(self.theme.text_muted),
                        ),
                    ]));
                }
            }
        }
        if tasks.len() > 6 {
            lines.extend(wrap(
                format!("… {} more tasks", tasks.len() - 6),
                Style::default().fg(self.theme.text_muted),
            ));
        }
        lines
    }
    fn transcript_lines(&self, width: usize) -> Vec<Line<'static>> {
        let mut lines = self.display_lines(width);
        for task in self.batch["tasks"].as_array().into_iter().flatten() {
            lines.extend(crate::markdown::render_markdown(
                &format!(
                    "\n## {}\n\n{}\n\n{}",
                    text(task, "task_name"),
                    text(task, "task_message"),
                    text(task, "completion_content")
                ),
                width,
                &self.theme,
            ));
        }
        lines
    }
    fn raw_lines(&self) -> Vec<Line<'static>> {
        text(&self.source, "raw_source")
            .lines()
            .map(|s| Line::from(s.to_string()))
            .collect()
    }
    fn as_any(&self) -> &dyn std::any::Any {
        self
    }
}

#[cfg(test)]
mod outcome_count_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn failed_agents_are_not_counted_as_finished() {
        let tasks = (0..4)
            .map(|i| {
                json!({"task_name":format!("check_{i}"),
            "terminal_status":"failed", "display_status":"Failed", "completion_committed":true})
            })
            .collect::<Vec<_>>();
        let cell =
            SubagentBatchCell::new(json!({"tasks":tasks}), json!({}), ThemePalette::default());
        for width in [40, 120] {
            let text = cell
                .display_lines(width)
                .iter()
                .map(crate::wrapping::line_plain_text)
                .collect::<Vec<_>>()
                .join("\n");
            assert!(text.contains("4 failed"), "{text}");
            assert!(!text.contains("finished"), "{text}");
        }
    }

    #[test]
    fn mixed_terminal_counts_do_not_overlap() {
        let tasks = vec![
            json!({"terminal_status":"failed", "completion_committed":true}),
            json!({"terminal_status":"cancelled"}),
            json!({"terminal_status":"completed"}),
            json!({"display_status":"Running"}),
            json!({"display_status":"Needs approval"}),
        ];
        let cell =
            SubagentBatchCell::new(json!({"tasks":tasks}), json!({}), ThemePalette::default());
        let text = cell
            .display_lines(120)
            .iter()
            .map(crate::wrapping::line_plain_text)
            .collect::<Vec<_>>()
            .join("\n");
        for label in [
            "1 failed",
            "1 cancelled",
            "1 finished",
            "1 running",
            "1 waiting",
        ] {
            assert!(text.contains(label), "{text}");
        }
    }
}
