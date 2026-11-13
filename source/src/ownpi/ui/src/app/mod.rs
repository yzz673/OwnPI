//! Presentation state only. Runtime facts arrive through a single Bridge protocol.

use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use std::time::Instant;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use ratatui::text::Line;
use serde_json::{json, Value};

use crate::bottom_pane::{
    ActiveInteractionRef, BottomPaneState, CommandDocumentViewState, EscapeAction,
    HumanInteractionState, HumanOverlaySubmitAction, InputResult, PendingInteractionState,
    PendingOption, PermissionInteractionState, SelectionAction, SelectionViewRow,
    SelectionViewState, ServiceTierCommand,
};
use crate::bridge::BridgeEvent;
use crate::display_item::DisplayItem;
use crate::history_cell::{
    BasicHistoryCell, BasicHistoryCellKind, HistoryCell, HistoryRenderMode, SessionSurface,
    TypedThreadItemHistoryCell, WelcomeHistoryCell, WelcomeSurface,
};
use crate::status::{StatusKind, StatusWidgetState};
use crate::theme::ThemePalette;
use crate::transcript_pager::TranscriptPagerState;

mod agents;
mod input;
mod menus;
mod progress;
mod stream_state;
mod surfaces;
use stream_state::StreamState;
mod permissions;
mod submission;
use permissions::PermissionPreset;
use submission::{Submission, SubmissionState};
#[cfg(test)]
mod agent_tests;
#[cfg(test)]
mod citation_tests;
#[cfg(test)]
mod menu_tests;
#[cfg(test)]
mod stream_tests;
#[cfg(test)]
mod submission_tests;
#[cfg(test)]
mod tests;

#[derive(Debug, Default)]
pub(crate) struct Workspace {
    pub(crate) session_id: Option<String>,
    pub(crate) project_root: Option<String>,
    pub(crate) model: Option<String>,
    pub(crate) reasoning_effort: Option<String>,
    pub(crate) runtime_profile: String,
    pub(crate) permissions: PermissionPreset,
    pub(crate) ready_for_real_llm: bool,
    pub(crate) is_mock: bool,
}

#[derive(Debug)]
pub(crate) struct TurnExecutionContext {
    pub(crate) mode_label: String,
    pub(crate) stage_label: String,
    pub(crate) stage_attempt: u64,
}

#[derive(Debug, Default)]
pub(crate) struct TokenUsage {
    pub(crate) is_estimate: bool,
    used: Option<u64>,
    limit: Option<u64>,
}

impl TokenUsage {
    pub(crate) fn used_tokens(&self) -> Option<u64> {
        self.used
    }

    pub(crate) fn percent_left(&self) -> Option<u64> {
        let limit = self.limit?;
        let used = self.used?;
        (limit > 0).then(|| 100u64.saturating_sub(used.saturating_mul(100) / limit))
    }
}

pub(crate) struct AppState {
    pub(crate) bottom_pane: BottomPaneState,
    pub(crate) theme: ThemePalette,
    pub(crate) terminal_theme: ThemePalette,
    pub(crate) status: StatusWidgetState,
    pub(crate) workspace: Workspace,
    pub(crate) session_surface: Option<SessionSurface>,
    pub(crate) turn_execution_context: Option<TurnExecutionContext>,
    pub(crate) should_quit: bool,
    pub(crate) quit_requested: bool,
    pub(crate) exit_summary: Option<crate::exit_summary::ExitSummary>,
    pub(crate) active: bool,
    pub(crate) connection_lost: bool,
    pub(crate) canonical: Vec<Value>,
    citation_catalog: crate::citation_sources::CitationCatalog,
    pub(crate) history: Vec<Arc<dyn HistoryCell>>,
    pub(crate) pending_history: Vec<Arc<dyn HistoryCell>>,
    pub(crate) rebuild: bool,
    pub(crate) pager: Option<TranscriptPagerState>,
    pub(crate) agents: crate::multi_agents::AgentUi,
    pub(crate) pager_document: Option<String>,
    pub(crate) command_catalog: Vec<ServiceTierCommand>,
    pub(crate) preferences: Value,
    pub(crate) skill_entries: Vec<crate::bridge::protocol::SkillCatalogEntry>,
    menu_navigation: Option<String>,
    menu_epoch: u64,
    pub(crate) editor_path: Option<std::path::PathBuf>,
    pub(crate) edit_draft: bool,
    pub(crate) clear_terminal: bool,
    pub(crate) hidden_history: usize,
    pub(crate) terminal_focused: bool,
    pub(crate) terminal_effects: Vec<String>,
    pub(crate) last_title: String,
    pub(crate) width: usize,
    live: StreamState,
    audit_projection: crate::history_cell::audit_region::AuditRegionProjector,
    pub(crate) request_counter: u64,
    pub(crate) pending: HashMap<String, (String, Value)>,
    pub(crate) dismissed: HashSet<String>,
    pub(crate) drafts: HashMap<String, HumanInteractionState>,
    pub(crate) raw: bool,
    pub(crate) usage: TokenUsage,
    pub(crate) context_meter_enabled: bool,
    pub(crate) file_search: crate::file_search::FileSearchManager,
    pub(crate) clipboard_lease: Option<crate::clipboard_copy::ClipboardLease>,
    progress: progress::RuntimeProgress,
    submission: Option<Submission>,
    execution_permissions: PermissionPreset,
    last_error: String,
    last_snapshot_seq: u64,
    welcome: WelcomeSurface,
}

impl Default for AppState {
    fn default() -> Self {
        Self::startup(String::new())
    }
}

impl AppState {
    pub(crate) fn startup(project_root: String) -> Self {
        let welcome = WelcomeSurface {
            app_name: "OwnPI".into(),
            model: String::new(),
            reasoning: String::new(),
            workspace_path: project_root.clone(),
            session_id: None,
            resumed_session: None,
        };
        let session_surface = Some(SessionSurface::Welcome(welcome.clone()));
        let mut file_search = crate::file_search::FileSearchManager::default();
        file_search.set_root(Some(project_root.clone()));
        let mut bottom_pane = BottomPaneState::default_with_commands();
        bottom_pane.set_file_search_root(Some(project_root.clone()));
        Self {
            bottom_pane,
            theme: ThemePalette::default(),
            terminal_theme: ThemePalette::default(),
            status: StatusWidgetState::default(),
            workspace: Workspace {
                project_root: Some(project_root),
                ..Default::default()
            },
            session_surface,
            turn_execution_context: None,
            should_quit: false,
            quit_requested: false,
            exit_summary: None,
            active: false,
            connection_lost: false,
            canonical: Vec::new(),
            citation_catalog: Default::default(),
            history: Vec::new(),
            pending_history: Vec::new(),
            rebuild: false,
            pager: None,
            agents: Default::default(),
            pager_document: None,
            command_catalog: Vec::new(),
            preferences: json!({}),
            skill_entries: Vec::new(),
            menu_navigation: None,
            menu_epoch: 0,
            editor_path: None,
            edit_draft: false,
            clear_terminal: false,
            hidden_history: 0,
            terminal_focused: true,
            terminal_effects: Vec::new(),
            last_title: String::new(),
            width: 80,
            live: StreamState::default(),
            audit_projection: Default::default(),
            request_counter: 0,
            pending: HashMap::new(),
            dismissed: HashSet::new(),
            drafts: HashMap::new(),
            raw: false,
            usage: TokenUsage::default(),
            context_meter_enabled: true,
            file_search,
            clipboard_lease: None,
            progress: Default::default(),
            submission: None,
            execution_permissions: PermissionPreset::Unknown,
            last_error: String::new(),
            last_snapshot_seq: 0,
            welcome,
        }
    }

    pub(crate) fn request(&mut self, method: &str, mut params: Value) -> Value {
        if matches!(method, "command" | "configure")
            && (params.get("menu_navigation").is_some() || params["menu_commit"] == true)
        {
            if let Some(id) = self.bottom_pane.menu_view_id() {
                params["source_view"] = json!(id);
                params["source_epoch"] = json!(self.menu_epoch);
            }
        }
        self.request_counter += 1;
        let id = format!("tui-{}", self.request_counter);
        self.pending
            .insert(id.clone(), (method.into(), params.clone()));
        json!({"schema": crate::bridge::SCHEMA, "request_id": id, "method": method, "params": params})
    }

    pub(crate) fn event(&mut self, event: BridgeEvent) {
        // Buffered snapshots cannot revive a connection whose transport failed.
        if self.connection_lost {
            return;
        }
        if event.r#type == "snapshot" && event.seq > 0 {
            if event.seq < self.last_snapshot_seq {
                return;
            }
            self.last_snapshot_seq = event.seq;
        }
        let was_active = self.active;
        if let Some(session) = event.identity.get("session_id").and_then(Value::as_str) {
            if self.workspace.session_id.as_deref() != Some(session) {
                self.hidden_history = 0;
                self.agents = Default::default();
                self.progress = Default::default();
                self.live.clear();
                if !self.pending.values().any(|(method, _)| method == "submit") {
                    self.submission = None;
                }
            }
            self.workspace.session_id = Some(session.into());
        }
        let run_id = text(&event.identity, "run_id");
        if !run_id.is_empty() && run_id != self.progress.run_id {
            self.progress = Default::default();
            self.progress.run_id = run_id;
            self.live.clear();
        }
        match event.r#type.as_str() {
            "bootstrap" => {
                self.configuration(&event.data["config"]);
                let commands = event.data["commands"]
                    .as_array()
                    .map(|rows| {
                        rows.iter()
                            .map(|row| ServiceTierCommand {
                                id: text(row, "name"),
                                name: text(row, "name"),
                                description: text(row, "description"),
                                source: "tui".into(),
                                kind: "local".into(),
                                owner: "tui".into(),
                                panel_id: String::new(),
                                aliases: Vec::new(),
                                argument_hint: String::new(),
                                disabled: false,
                                disabled_reason: String::new(),
                                shortcut: String::new(),
                                tab: String::new(),
                                side_content: String::new(),
                            })
                            .collect()
                    })
                    .unwrap_or_default();
                self.command_catalog = commands;
                self.bottom_pane
                    .set_command_catalog(self.command_catalog.clone());
                self.skill_catalog(&event.data["skills"]);
            }
            "skills" => self.skill_catalog(&event.data["skills"]),
            "configuration" => self.configuration(&event.data["config"]),
            "snapshot" => self.snapshot(&event.data),
            "stream" => self.stream(&event.data),
            "stream_health" => {
                self.progress.observe_stream_health(&event.data);
                if !self.active {
                    self.status.set_notice(self.progress.stream_notice());
                }
            }
            "view_health" => {
                if event.data["state"] == "fresh" && self.progress.stale() {
                    self.status.set_notice(None);
                }
                self.progress.observe_health(&event.data);
            }
            "panel" => {
                let mut panel = event.data.clone();
                if panel["title"] == "Provider audit" {
                    panel["body"] = json!(format!("{}{}", text(&panel, "body"), self.live.audit()));
                }
                self.panel(&panel);
            }
            "execution" => {
                let state = text(&event.data, "state");
                if state == "starting" {
                    self.execution_permissions = PermissionPreset::Unknown;
                    self.last_error.clear();
                    self.progress.clear_activity();
                    self.progress.stopping = false;
                }
                if text(&event.data, "action") == "resume" {
                    self.dismissed.clear();
                }
                self.active = true;
                self.progress.stopping = state == "stopping";
                self.status.start_working(
                    if state == "stopping" {
                        "Stopping"
                    } else {
                        "Working"
                    }
                    .into(),
                    text(&event.data, "message"),
                    0,
                    true,
                );
            }
            "interaction_accepted" => {
                self.accept_interaction(&text(&event.data, "interaction_id"));
            }
            "notice" => self.status.set_notice(Some(text(&event.data, "message"))),
            "result" => self.result(&event.request_id, &event.data),
            "execution_result" => { /* Backend reply is projected onto its canonical result card by the Bridge. */
            }
            _ => self.disconnected("Unsupported Bridge event"),
        }
        self.refresh_running_status();
        if was_active && !self.active {
            self.notify("Execution stopped · inspect the result");
        }
        self.sync_terminal_title();
        self.refresh_pager();
        self.refresh_agent_pager();
    }

    fn configuration(&mut self, value: &Value) {
        let previous_root = self.workspace.project_root.clone();
        if value["preferences"].is_object() {
            self.preferences = value["preferences"].clone();
            self.raw = self.preferences["raw"] == true;
            self.bottom_pane
                .set_vim_enabled(self.preferences["vim"] == true);
            let syntax = text(&self.preferences, "syntax_theme");
            crate::syntax_highlight::select_theme(&syntax);
        }
        self.workspace.permissions = PermissionPreset::parse(value["permissions"].as_str());
        self.workspace.model = Some(text(value, "model"));
        self.workspace.reasoning_effort = Some(text(value, "reasoning_effort"));
        self.workspace.runtime_profile = text(value, "runtime_profile");
        self.context_meter_enabled = value["context_meter_enabled"].as_bool().unwrap_or(true);
        self.workspace.project_root = Some(text(value, "project_root"));
        if previous_root != self.workspace.project_root {
            self.file_search
                .set_root(self.workspace.project_root.clone());
            self.bottom_pane
                .set_file_search_root(self.workspace.project_root.clone());
        }
        self.workspace.ready_for_real_llm = value["ready"].as_bool().unwrap_or(false);
        self.workspace.is_mock = value["is_mock"].as_bool().unwrap_or(false);
        match text(value, "theme").as_str() {
            "light" => {
                self.theme = ThemePalette::from_terminal_colors(
                    Some(ratatui::style::Color::Rgb(17, 17, 17)),
                    Some(ratatui::style::Color::Rgb(255, 255, 255)),
                )
            }
            "dark" => {
                self.theme = ThemePalette::from_terminal_colors(
                    Some(ratatui::style::Color::Rgb(238, 238, 238)),
                    Some(ratatui::style::Color::Rgb(17, 17, 17)),
                )
            }
            _ => self.theme = self.terminal_theme.clone(),
        }
        self.welcome.model = self.workspace.model.clone().unwrap_or_default();
        self.welcome.reasoning = self.workspace.reasoning_effort.clone().unwrap_or_default();
        self.welcome.workspace_path = self.workspace.project_root.clone().unwrap_or_default();
        if self.canonical.is_empty() {
            self.session_surface = Some(SessionSurface::Welcome(self.welcome.clone()));
        } else {
            self.replace_history();
        }
    }

    fn snapshot(&mut self, data: &Value) {
        self.reconcile_submission(&data["submission_receipts"]);
        self.execution_permissions =
            PermissionPreset::parse(data["execution_permissions"].as_str());
        self.progress.observe_activity(&data["activity"]);
        let incoming = data["items"].as_array().cloned().unwrap_or_default();
        let batches = data["subagents"].as_array().cloned().unwrap_or_default();
        let batches_changed = batches != self.agents.batches;
        self.agents.batches = batches;
        if self
            .agents
            .active
            .as_ref()
            .is_some_and(|key| !self.agents.tasks().iter().any(|(id, _)| id == key))
        {
            self.select_agent("main".into());
        }
        if incoming != self.canonical || batches_changed {
            self.progress.observe("Task activity updated");
            let appended = incoming.starts_with(&self.canonical);
            let previous_len = self.history.len();
            let rebuild_was_pending = self.rebuild;
            self.canonical = incoming;
            let citation_catalog = crate::citation_sources::CitationCatalog::new(&self.canonical);
            let citations_changed = citation_catalog != self.citation_catalog;
            self.citation_catalog = citation_catalog;
            let prompts = self
                .canonical
                .iter()
                .filter(|item| text(item, "kind") == "user")
                .map(|item| text(&item["payload"], "text"))
                .collect::<Vec<_>>();
            self.bottom_pane.set_persistent_history(prompts, 1);
            self.replace_history();
            if appended
                && !citations_changed
                && !batches_changed
                && !rebuild_was_pending
                && self.history.len() >= previous_len
                && !self.canonical.iter().any(|item| {
                    matches!(
                        text(item, "kind").as_str(),
                        "command_execution" | "task_outcome"
                    )
                })
            {
                self.pending_history
                    .extend_from_slice(&self.history[previous_len..]);
                self.rebuild = false;
            }
        }
        self.live.reconcile(&self.canonical);
        self.active = data["worker_active"].as_bool().unwrap_or(false)
            || data["status"]["owner_active"].as_bool().unwrap_or(false)
            || self
                .submission
                .as_ref()
                .is_some_and(|s| s.state == SubmissionState::Sending);
        let status = text(&data["status"], "status");
        let direct = text(&data["capabilities"], "mode") == "direct";
        let stage = if direct {
            String::new()
        } else {
            text(&data["status"], "current_stage")
        };
        self.turn_execution_context = (direct || !stage.is_empty()).then(|| TurnExecutionContext {
            mode_label: self
                .canonical
                .iter()
                .rev()
                .find(|item| text(item, "kind") == "route_decision")
                .map(|item| text(&item["payload"], "route_label"))
                .filter(|label| !label.is_empty())
                .unwrap_or_else(|| match data["capabilities"]["mode"].as_str() {
                    Some("direct") => "Task".into(),
                    Some("research") => "Research".into(),
                    _ => String::new(),
                }),
            stage_label: stage.clone(),
            stage_attempt: data["status"]["stage_attempt"].as_u64().unwrap_or(0),
        });
        if !self.active {
            self.progress.clear_activity();
            self.progress.stopping = false;
            self.live.finish();
            let kind = match status.as_str() {
                "completed" => StatusKind::Completed,
                "failed" | "unexpected_exit" => StatusKind::Failed,
                "paused_for_human" => StatusKind::PendingClarification,
                "stopped" => StatusKind::Interrupted,
                _ => StatusKind::Idle,
            };
            let detail = text(&data["status"], "message");
            let elapsed_ms = (data["status"]["run_id"]
                .as_str()
                .is_some_and(|id| !id.is_empty())
                && data["timing"]["run_id"] == data["status"]["run_id"])
                .then(|| data["timing"]["elapsed_ms"].as_u64())
                .flatten()
                .unwrap_or_else(|| self.status.elapsed_ms());
            self.status.set_terminal(
                kind,
                if status == "unexpected_exit" {
                    "Unexpected exit".into()
                } else {
                    status.clone()
                },
                if detail.is_empty() {
                    self.last_error.clone()
                } else {
                    detail
                },
                elapsed_ms,
            );
        }
        self.usage.used = data["usage"]["provider_prompt_tokens"].as_u64();
        self.usage.is_estimate = data["usage"]["is_estimate"].as_bool().unwrap_or(true);
        // Capacity belongs to this recorded conversation, not the model selected
        // for a future run or a previously viewed conversation.
        self.usage.limit = data["usage"]["context_window_limit"]
            .as_u64()
            .filter(|limit| *limit > 0);
        self.sync_interactions(
            data["interactions"]
                .as_array()
                .map(Vec::as_slice)
                .unwrap_or(&[]),
        );
        if let Some(warnings) = data["warnings"].as_array() {
            if !warnings.is_empty() {
                self.status.set_notice(Some(format!(
                    "{} source warnings · open /audit for details",
                    warnings.len()
                )));
            } else if self
                .status
                .notice_text()
                .is_some_and(|notice| notice.contains("source warnings · open /audit"))
            {
                self.status.set_notice(None);
            }
        }
    }

    fn replace_history(&mut self) {
        self.session_surface = self
            .canonical
            .is_empty()
            .then(|| SessionSurface::Welcome(self.welcome.clone()));
        let values = self
            .canonical
            .iter()
            .filter(|value| Self::is_main_item(value))
            .cloned()
            .collect::<Vec<_>>();
        let (history, projector) = self.history_cells(&values, true);
        self.history = history;
        self.audit_projection = projector;
        self.rebuild = true;
    }

    fn is_main_item(value: &Value) -> bool {
        text(&value["agent_ref"], "actor_kind") != "subagent"
            && text(&value["scope"], "actor_kind") != "subagent"
    }

    fn history_cells(
        &self,
        values: &[Value],
        welcome: bool,
    ) -> (
        Vec<Arc<dyn HistoryCell>>,
        crate::history_cell::audit_region::AuditRegionProjector,
    ) {
        let mut history: Vec<Arc<dyn HistoryCell>> = Vec::new();
        if welcome && !values.is_empty() {
            history.push(Arc::new(WelcomeHistoryCell::new(
                self.welcome.clone(),
                self.theme.clone(),
            )));
        }
        let mut grouped: Option<(Value, TypedThreadItemHistoryCell)> = None;
        for value in values {
            // Hidden source records must not split adjacent visible tool
            // groups. Reasoning is retained canonically, never user-facing.
            if value["payload"]["detail_only"] == true
                || text(value, "kind") == "reasoning"
                || (value["visibility"] == "audit"
                    && self.preferences["interface_mode"] != "developer")
            {
                continue;
            }
            // Whitespace-only provider messages have no visible content.
            // Ignore them before grouping; canonical history stays intact.
            if text(value, "kind") == "agent_message"
                && text(&value["payload"], "text").trim().is_empty()
            {
                continue;
            }
            if text(value, "kind") != "command_execution" {
                if let Some((_, cell)) = grouped.take() {
                    history.push(Arc::new(cell));
                }
            }
            let mut visible = value.clone();
            visible["visibility"] = json!("user");
            if !welcome {
                // The sticky title already identifies the viewed agent.
                // Keep raw_source and canonical identity untouched.
                if let Some(scope) = visible["scope"].as_object_mut() {
                    scope.insert("actor_label".into(), json!(""));
                }
            }
            if text(value, "kind") == "user" {
                history.push(Arc::new(BasicHistoryCell::new(
                    BasicHistoryCellKind::User,
                    text(&value["payload"], "text"),
                    self.theme.clone(),
                )));
            } else if text(value, "kind") == "answer_buffer_status" {
                // Frontend delivery diagnostics are not Core-authored answers.
                history.push(Arc::new(BasicHistoryCell::new(
                    BasicHistoryCellKind::FrontendNotice,
                    text(&value["payload"], "text"),
                    self.theme.clone(),
                )));
            } else if text(value, "kind") == "agent_message" {
                history.push(self.agent_cell_with_context(value, welcome));
            } else if let Some(batch) = self
                .agents
                .batches
                .iter()
                .find(|batch| batch["item_id"] == value["item_id"])
            {
                history.push(crate::multi_agents::SubagentBatchCell::new(
                    batch.clone(),
                    value.clone(),
                    self.theme.clone(),
                ));
            } else if let Some(item) = DisplayItem::parse_value(&visible) {
                let cell = TypedThreadItemHistoryCell::new(item, self.theme.clone())
                    .with_cwd(self.workspace.project_root.clone())
                    .with_citation_sources(self.citation_catalog.for_item(value));
                if text(value, "kind") == "command_execution" {
                    if let Some((scope, previous)) = &mut grouped {
                        if *scope == value["scope"] && previous.merge_adjacent_display(&cell) {
                            continue;
                        }
                    }
                    if let Some((_, previous)) = grouped.take() {
                        history.push(Arc::new(previous));
                    }
                    grouped = Some((value["scope"].clone(), cell));
                } else {
                    history.push(Arc::new(cell));
                    if text(value, "kind") == "task_outcome"
                        && !text(&value["payload"], "elapsed_display").is_empty()
                    {
                        history.push(Arc::new(BasicHistoryCell::new(
                            BasicHistoryCellKind::FinalSeparator,
                            text(&value["payload"], "elapsed_display"),
                            self.theme.clone(),
                        )));
                    }
                }
            }
        }
        if let Some((_, cell)) = grouped {
            history.push(Arc::new(cell));
        }

        let mut projector = crate::history_cell::audit_region::AuditRegionProjector::default();
        history = history
            .iter()
            .map(|cell| projector.project(cell.clone(), &self.theme))
            .collect();
        (history, projector)
    }

    fn stream(&mut self, data: &Value) {
        let kind = text(data, "canonical_event_type");
        if data["unresolved_agent"] == true {
            return;
        }
        let stream_run = data["stream_key"][0].as_str().unwrap_or_default();
        if !self.progress.run_id.is_empty()
            && !stream_run.is_empty()
            && stream_run != self.progress.run_id
        {
            return;
        }
        if self.active {
            self.progress.observe_stream(data);
        }
        self.live.observe(data);
        self.live.reconcile(&self.canonical);
        if kind == "attempt_discarded" || kind == "stream_error" {
            self.status.set_notice(Some(format!(
                "Provider {} · open /audit for recorded details",
                kind.replace('_', " ")
            )));
        }
        self.refresh_running_status();
    }

    // A control reply can arrive after a snapshot has opened another request.
    // Only retire the exact interaction that the backend accepted.
    fn accept_interaction(&mut self, id: &str) {
        if id.is_empty() {
            return;
        }
        self.dismissed.insert(id.to_owned());
        self.drafts.remove(id);
        if self
            .bottom_pane
            .pending_interaction()
            .is_some_and(|item| item.interaction_id() == id)
        {
            self.clear_request_overlay();
        }
        if self.agents.selected_request.as_deref() == Some(id) {
            self.agents.selected_request = None;
        }
        let remaining = self
            .agents
            .requests
            .iter()
            .filter(|item| text(item, "session_id") != id)
            .cloned()
            .collect::<Vec<_>>();
        self.sync_interactions(&remaining);
    }

    fn result(&mut self, request: &str, data: &Value) {
        if text(data, "disposition") == "pending" {
            return;
        }
        let pending = self.pending.remove(request);
        if let Some((_, params)) = &pending {
            if params["skill_action"].is_object() {
                let native = &params["skill_action"];
                self.bottom_pane.apply_command_action_result(
                    &text(native, "request_id"),
                    &text(native, "action"),
                    data["ok"] != false,
                    &text(data, "message"),
                );
            }
            if data["ok"] == true
                && params["menu_commit"] == true
                && self.bottom_pane.menu_view_id().as_deref() == params["source_view"].as_str()
                && params["source_epoch"].as_u64() == Some(self.menu_epoch)
            {
                self.bottom_pane.close_active_bottom_view();
                self.menu_epoch += 1;
                self.status.set_notice(Some("Setting saved".into()));
            }
        }
        if data["ok"].as_bool() == Some(false) {
            self.last_error = text(data, "message");
            if data["can_exit_without_resume"].as_bool() == Some(true) {
                self.quit_requested = false;
                self.panel(&json!({"presenter": "selection", "title": if data["recovery_unconfirmed"] == true { "Execution ended unexpectedly" } else { "Recovery unavailable" },
                    "subtitle": text(data, "message"), "rows": [{"id": "exit", "name": "Exit and keep the stopped records",
                    "description": "Keep records without claiming completion or a saved recovery point.", "action": {"method": "shutdown", "params": {"allow_unrecoverable": true}}}]}));
                return;
            }
            self.status.set_notice(Some(text(data, "message")));
            if let Some((method, params)) = &pending {
                if method == "permission" || method == "answer" {
                    self.bottom_pane
                        .clear_interaction_submitting(Some(&text(params, "interaction_id")));
                }
            }
            if let Some((method, params)) = pending {
                if method == "submit" && text(data, "disposition") == "rejected" {
                    // No new snapshot is guaranteed after an admission failure.
                    // Stop the optimistic spinner and expose the rejection now.
                    let recorded = self
                        .submission
                        .as_ref()
                        .is_some_and(|s| matches!(s.state, SubmissionState::Recorded(_)));
                    self.status.set_terminal(
                        StatusKind::Failed,
                        if recorded {
                            "Execution could not start"
                        } else {
                            "Request not sent"
                        }
                        .into(),
                        self.last_error.clone(),
                        0,
                    );
                    if !self.submission_failed(request, true) {
                        self.restore_unsent_draft(&params);
                    }
                    self.active = false;
                }
                if method == "submit" && text(data, "disposition") != "rejected" {
                    self.submission_failed(request, false);
                }
                if method == "shutdown" {
                    self.quit_requested = false;
                }
            }
            return;
        }
        if data["shutdown"].as_bool() == Some(true) {
            self.exit_summary = Some(crate::exit_summary::ExitSummary::confirmed(data));
            self.should_quit = true;
        }
        if data["action"] == "stop" && !text(data, "message").is_empty() {
            self.status.set_notice(Some(text(data, "message")));
        }
        if pending
            .as_ref()
            .is_some_and(|(_, params)| params["open_requests"] == true)
        {
            self.open_agent_popup(crate::multi_agents::PopupKind::Requests);
        }
        if let Some((method, params)) = pending {
            if method == "permission" || method == "answer" {
                self.accept_interaction(&text(&params, "interaction_id"));
            }
            if method == "submit" {
                self.submission_failed(request, false);
            }
            if method == "submit" || method == "resume" {
                self.live.finish();
            }
        }
    }

    fn sync_interactions(&mut self, values: &[Value]) {
        let new_requests = values.iter().any(|value| {
            !self
                .agents
                .requests
                .iter()
                .any(|old| old["session_id"] == value["session_id"])
        });
        self.agents.requests = values.to_vec();
        if new_requests {
            self.notify(&format!("{} pending requests · F4 to review", values.len()));
        }
        let current = self
            .bottom_pane
            .pending_interaction()
            .map(|item| item.interaction_id().to_string());
        if let Some(id) = current.as_deref() {
            if values.iter().any(|value| text(value, "session_id") == id) {
                return;
            }
            self.bottom_pane.sync_input_to_overlay();
            if let Some(ActiveInteractionRef::Human(human)) = self.bottom_pane.pending_interaction()
            {
                self.drafts
                    .insert(human.interaction_id.clone(), human.clone());
            }
            self.clear_request_overlay();
            self.agents.selected_request = None;
            let awaiting_result = self.pending.values().any(|(method, params)| {
                matches!(method.as_str(), "permission" | "answer")
                    && text(params, "interaction_id") == id
            });
            if !awaiting_result {
                self.status.set_notice(Some(
                    if self.drafts.contains_key(id) {
                        "Question is no longer pending; local draft retained."
                    } else {
                        "Approval request is no longer pending."
                    }
                    .into(),
                ));
            }
        }
        // Keep an active answer stable; otherwise present the next pending
        // request. Explicitly dismissed or accepted requests stay closed.
        if self.agents.selected_request.is_none() && self.agents.popup.is_none() {
            self.agents.selected_request = values
                .iter()
                .filter(|value| matches!(text(value, "kind").as_str(), "human" | "permission"))
                .map(|value| text(value, "session_id"))
                .find(|id| !id.is_empty() && !self.dismissed.contains(id));
        }
        for value in values {
            let id = text(value, "session_id");
            if self.agents.selected_request.as_deref() != Some(id.as_str())
                || self.dismissed.contains(&id)
            {
                continue;
            }
            let run_dir = String::new();
            if self.agents.main_draft.is_none() {
                self.agents.main_draft = Some(self.bottom_pane.composer().to_string());
            }
            if text(value, "kind") == "human" {
                let mut human = self
                    .drafts
                    .remove(&id)
                    .unwrap_or_else(|| HumanInteractionState {
                        interaction_id: id.clone(),
                        title: format!(
                            "Question · {} · {}",
                            self.request_owner_label(value),
                            text(value, "stage").replace('_', " ")
                        ),
                        questions: value["questions"]
                            .as_array()
                            .map(|items| {
                                items
                                    .iter()
                                    .filter_map(crate::bottom_pane::parse_question)
                                    .collect()
                            })
                            .unwrap_or_default(),
                        ..Default::default()
                    });
                human.run_dir = run_dir;
                self.notify("OwnPI needs your input");
                self.bottom_pane
                    .set_overlay(Some(PendingInteractionState::Human(human)));
                self.status
                    .transition_to_pending_clarification("Answer the pending questions".into());
            } else {
                let request = &value["approval_request"];
                let overlay = &request["requested_overlay"];
                let remember_scope = text(value, "remember_scope");
                let mut permission = PermissionInteractionState {
                    role_label: match text(value, "actor_kind").as_str() {
                        "core" => "Core".into(),
                        "reviewer" => "Reviewer".into(),
                        "subagent" => format!("Subagent · {}", self.request_owner_label(value)),
                        _ => self.request_owner_label(value),
                    },
                    stage_label: text(value, "stage").replace('_', " "),
                    remember_scope,

                    interaction_id: id,
                    run_dir,
                    approval_kind: text(value, "approval_kind"),
                    call_id: text(value, "call_id"),
                    approval_id: text(value, "approval_id"),
                    permission_rule: text(value, "permission_rule"),
                    network_host: text(value, "network_host"),
                    file_changes: value["file_changes"]
                        .as_array()
                        .map(|rows| {
                            rows.iter()
                                .map(|row| crate::bottom_pane::PermissionFileChange {
                                    path: text(row, "path"),
                                    destination: text(row, "destination"),
                                    change_kind: text(row, "change_kind"),
                                    added_lines: row["added_lines"].as_u64(),
                                    removed_lines: row["removed_lines"].as_u64(),
                                    size_bytes: row["size_bytes"].as_u64(),
                                })
                                .collect()
                        })
                        .unwrap_or_default(),
                    title: format!(
                        "Permission · {} · {}",
                        self.request_owner_label(value),
                        text(value, "stage").replace('_', " ")
                    ),
                    tool_name: text(value, "tool_name"),
                    command: text(value, "command"),
                    reason: text(value, "reason"),
                    request_summary: text(value, "request_summary"),
                    operation_details: text(value, "operation_details"),
                    cwd: text(value, "cwd"),
                    body_markdown: text(value, "body_markdown"),
                    permission_class: text(value, "permission_class"),
                    options: vec![
                        PendingOption {
                            id: "approve".into(),
                            label: "Allow once".into(),
                            description: String::new(),
                        },
                        PendingOption {
                            id: "approve_session".into(),
                            label: "Remember for this run".into(),
                            description: String::new(),
                        },
                        PendingOption {
                            id: "deny_command".into(),
                            label: "Deny operation and continue".into(),
                            description: String::new(),
                        },
                        PendingOption {
                            id: "deny".into(),
                            label: "Deny this request".into(),
                            description: String::new(),
                        },
                    ],
                    ..Default::default()
                };
                if permission.remember_scope.is_empty() {
                    permission
                        .options
                        .retain(|option| option.id != "approve_session");
                }
                if permission.body_markdown.is_empty() {
                    permission.body_markdown = format!(
                        "{}\n\nRule: {}\n\nCommand: {}",
                        permission.reason, permission.permission_rule, permission.command
                    );
                }
                permission.body_markdown.push_str(&format!(
                    "\n\n## Requested access\n```json\n{}\n```",
                    serde_json::to_string_pretty(overlay).unwrap_or_default()
                ));
                self.notify("Permission required");
                self.bottom_pane
                    .set_overlay(Some(PendingInteractionState::Permission(permission)));
                self.status
                    .transition_to_pending_permission("Approve or deny this request".into());
            }
            break;
        }
    }

    pub(crate) fn document(&mut self, title: String, body: String) {
        self.bottom_pane
            .set_command_document(Some(CommandDocumentViewState::new(
                "document".into(),
                title,
                String::new(),
                body,
                "tui".into(),
            )));
    }

    fn restore_unsent_draft(&mut self, params: &Value) {
        if self.bottom_pane.composer_text_with_pending().is_empty() {
            self.bottom_pane.set_composer(text(params, "text"));
        } else {
            self.status.set_notice(Some(format!(
                "{} · Current draft kept; previous request is in input history.",
                self.last_error
            )));
        }
    }

    // Only a queue rejection proves the request was not sent. A broken pipe
    // has an uncertain outcome and must never cause an automatic retry.
    pub(crate) fn command_not_sent(&mut self, command: &Value) {
        if let Some((method, params)) = self.pending.remove(&text(command, "request_id")) {
            if method == "submit" && !self.submission_failed(&text(command, "request_id"), true) {
                self.restore_unsent_draft(&params);
            }
        }
    }

    pub(crate) fn disconnected(&mut self, message: &str) {
        self.progress.clear_activity();
        self.progress.stopping = false;
        if let Some(submission) = &mut self.submission {
            if submission.state == SubmissionState::Sending {
                submission.state = SubmissionState::Unconfirmed;
            }
        }
        self.connection_lost = true;
        self.active = false;
        self.live.finish();
        self.status.set_terminal(
            StatusKind::Failed,
            "Disconnected".into(),
            message.into(),
            self.status.elapsed_ms(),
        );
        self.sync_terminal_title();
    }

    fn agent_cell_with_context(&self, value: &Value, show_context: bool) -> Arc<dyn HistoryCell> {
        let scope = &value["scope"];
        let stage = text(scope, "stage");
        let round = scope["critic_round"].as_u64().unwrap_or(0);
        let suffix = format!("__reviewer_round_{round:03}");
        let display_stage = if text(scope, "actor_kind") == "reviewer" {
            stage.strip_suffix(&suffix).unwrap_or(&stage).to_string()
        } else {
            stage
        };
        Arc::new(
            crate::history_cell::AgentMarkdownHistoryCell::new(
                text(&value["payload"], "text"),
                self.theme.clone(),
            )
            .with_cwd(self.workspace.project_root.clone())
            .with_citation_sources(self.citation_catalog.for_item(value))
            .with_audit_context(
                DisplayItem::parse_value(value)
                    .and_then(|item| item.common().audit_context.clone()),
            )
            .with_agent_context(show_context.then(|| {
                crate::agent_provenance::AgentMessageContext {
                    stage: display_stage,
                    attempt: scope["attempt"].as_i64().unwrap_or(0),
                    component: if matches!(
                        text(scope, "actor_id").as_str(),
                        "stage_selection" | "task_spec_authoring" | "task_authoring"
                    ) {
                        // Pre-execution narration uses the normal assistant surface;
                        // explicit Router/Workflow cards carry the phase facts.
                        String::new()
                    } else if text(scope, "actor_kind") == "subagent"
                        && !text(scope, "actor_label").is_empty()
                    {
                        text(scope, "actor_label")
                    } else {
                        text(scope, "actor_id")
                    },
                    operation: String::new(),
                    round_id: (round > 0)
                        .then(|| format!("round {round}"))
                        .unwrap_or_default(),
                }
            })),
        )
    }

    pub(crate) fn active_agent_stream_lines(&self, width: usize) -> Vec<Line<'static>> {
        self.agent_stream_lines(width, None, self.audit_projection.clone())
    }

    pub(crate) fn live_joins_audit_region(&self) -> bool {
        // The audit renderer supplies either a continuing rail or its closing
        // boundary before the next owner's output. No extra plain row belongs
        // between the physical history and that renderer-owned boundary.
        self.audit_projection.has_active_region()
    }

    fn agent_stream_lines(
        &self,
        width: usize,
        selected: Option<&Value>,
        mut projector: crate::history_cell::audit_region::AuditRegionProjector,
    ) -> Vec<Line<'static>> {
        let mut live: Vec<_> = self.live.values().collect();
        live.sort_by_key(|item| item.order);
        let mut lines = Vec::new();
        if self.live.evicted > 0 {
            lines.push(Line::from(format!(
                "{} stream records exceeded display capacity · /audit",
                self.live.evicted
            )));
        }
        for item in live {
            if item.content.trim().is_empty() {
                continue;
            }
            if let Some(selected) = selected {
                if !crate::multi_agents::same_agent(&item.context["agent_ref"], selected) {
                    continue;
                }
            } else if !Self::is_main_item(&item.context) {
                continue;
            }
            let mut value = item.context.clone();
            value["schema_version"] = json!("ownpi.tui.item.v1");
            value["visibility"] = json!("user");
            value["status"] = json!("running");
            value["item_id"] = json!(format!("live-{}", item.order));
            value["kind"] = json!("agent_message");
            value["payload"] = json!({"text":item.display_text(self.pager.is_some() || self.raw || self.agents.raw || self.agents.details)});
            let cell = self.agent_cell_with_context(&value, selected.is_none());
            lines.extend(projector.project(cell, &self.theme).display_lines(width));
        }
        lines
    }

    pub(crate) fn history_render_mode(&self) -> HistoryRenderMode {
        if self.raw {
            HistoryRenderMode::Raw
        } else {
            HistoryRenderMode::Rich
        }
    }
    pub(crate) fn poll_file_search(&mut self) -> bool {
        if let Some(query) = self.bottom_pane.take_file_search_request() {
            self.file_search.on_user_query(query);
        }
        let mut changed = false;
        for result in self.file_search.drain_results() {
            changed |= self
                .bottom_pane
                .apply_file_search_result(&result.query, result.matches);
        }
        changed
    }
    pub(crate) fn has_committed_history_before_live(&self) -> bool {
        !self.history.is_empty()
    }
    pub(crate) fn has_visible_history_or_session_surface(&self) -> bool {
        !self.history.is_empty() || self.session_surface.is_some()
    }
    pub(crate) fn token_usage(&self) -> Option<&TokenUsage> {
        Some(&self.usage)
    }
    pub(crate) fn activity_state_label(&self) -> Option<&str> {
        match self.status.kind {
            StatusKind::PendingPermission => Some("permission required"),
            StatusKind::PendingClarification => Some("answer required"),
            StatusKind::Failed => Some("failed"),
            StatusKind::Interrupted => Some("stopped"),
            StatusKind::Completed => Some("completed"),
            StatusKind::Cancelled => Some("cancelled"),
            _ if self.active => Some("working"),
            _ => None,
        }
    }
    pub(crate) fn note_layout_size(&mut self, width: usize, _height: usize) {
        if self.width != width {
            self.rebuild = true;
            self.width = width;
            self.refresh_pager();
            self.refresh_agent_pager();
        }
    }
    pub(crate) fn drain_pending_history_cells(&mut self) -> Vec<Arc<dyn HistoryCell>> {
        std::mem::take(&mut self.pending_history)
    }
    pub(crate) fn last_committed_scrollback_cell(&self) -> Option<Arc<dyn HistoryCell>> {
        self.history
            .iter()
            .skip(self.hidden_history)
            .last()
            .cloned()
    }
    pub(crate) fn render_scrollback_tail(
        &self,
        width: usize,
    ) -> Vec<crate::terminal_hyperlinks::HyperlinkLine> {
        let mut lines = Vec::new();
        let mut previous: Option<&dyn HistoryCell> = None;
        for cell in self.history.iter().skip(self.hidden_history) {
            let rendered = match self.history_render_mode() {
                HistoryRenderMode::Rich => cell.display_hyperlink_lines_edged(width),
                HistoryRenderMode::Raw => {
                    cell.display_hyperlink_lines_for_mode(width, HistoryRenderMode::Raw)
                }
            };
            if rendered.is_empty() {
                continue;
            }
            if previous.is_some_and(|previous| {
                crate::history_cell::history_cells_require_inter_cell_spacing(
                    previous,
                    cell.as_ref(),
                )
            }) {
                lines.extend(crate::terminal_hyperlinks::plain_hyperlink_lines(vec![
                    Line::from(""),
                ]));
            }
            lines.extend(rendered);
            previous = Some(cell.as_ref());
        }
        lines
    }
    pub(crate) fn take_due_transcript_reflow(&mut self, _now: Instant) -> bool {
        std::mem::take(&mut self.rebuild)
    }
    pub(crate) fn finish_resize_reflow(&mut self, _width: usize) {}
    pub(crate) fn transcript_pager(&self) -> Option<&TranscriptPagerState> {
        self.pager.as_ref()
    }
}

pub(crate) fn text(value: &Value, key: &str) -> String {
    value[key].as_str().unwrap_or_default().to_string()
}
