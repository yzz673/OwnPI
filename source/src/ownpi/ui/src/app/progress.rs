//! Transient display of observed activity. Local clocks never invent provider pulses.
use super::*;

#[derive(Clone, Default, PartialEq, serde::Deserialize)]
#[serde(default)]
struct Activity {
    stage: String,
    role: String,
    round: u64,
    attempt: u64,
    phase: String,
    source_item_id: String,
}

#[derive(Default, serde::Deserialize)]
#[serde(default)]
struct ViewHealth {
    state: String,
    error: String,
    last_success_at: String,
}

#[derive(Default)]
pub(super) struct RuntimeProgress {
    pub run_id: String,
    pub stopping: bool,
    last_activity: Option<(String, Instant)>,
    last_tick: Option<Instant>,
    activity: Activity,
    health: ViewHealth,
    stream_pending: u64,
    stream_dropped: u64,
}

impl RuntimeProgress {
    pub fn observe_activity(&mut self, value: &Value) {
        let activity = serde_json::from_value::<Activity>(value.clone()).unwrap_or_default();
        if self.activity != activity {
            self.activity = activity;
        }
    }

    pub fn observe_stream_health(&mut self, value: &Value) {
        self.stream_pending = value["pending"].as_u64().unwrap_or(0);
        self.stream_dropped = value["dropped"].as_u64().unwrap_or(0);
    }

    pub fn stream_notice(&self) -> Option<String> {
        if self.stream_dropped > 0 {
            Some(format!(
                "{} stream events could not be displayed · /audit",
                self.stream_dropped
            ))
        } else if self.stream_pending > 0 {
            Some(format!(
                "Waiting for message identity: {} events",
                self.stream_pending
            ))
        } else {
            None
        }
    }

    pub fn observe_health(&mut self, value: &Value) {
        if let Ok(health) = serde_json::from_value(value.clone()) {
            self.health = health;
        }
    }

    pub(super) fn stale(&self) -> bool {
        self.health.state == "stale"
    }

    fn health_message(&self) -> String {
        let last = if self.health.last_success_at.is_empty() {
            "none"
        } else {
            &self.health.last_success_at
        };
        format!(
            "Last successful refresh: {last}. Retrying automatically. {}",
            self.health.error
        )
    }

    pub fn clear_activity(&mut self) {
        self.last_activity = None;
        self.last_tick = None;
    }

    pub fn observe(&mut self, label: &str) {
        self.last_activity = Some((label.to_string(), Instant::now()));
    }

    pub fn observe_stream(&mut self, data: &Value) {
        let kind = text(data, "canonical_event_type");
        let label = match kind.as_str() {
            "response_started" => "Model request",
            "response_completed" => "Model response",
            "assistant_text_delta" => "Response streaming",
            "assistant_reasoning_delta" => "Model activity",
            "tool_call_arguments_delta" => "Preparing tool",
            "attempt_discarded" | "stream_error" => "Model request interrupted",
            _ if data["event_kind"] == "llm_status"
                && data["provider_inflight"].as_bool() == Some(false) =>
            {
                // Terminal telemetry carries the final heartbeat count too.
                // It confirms request end, without establishing success.
                "Model request ended"
            }
            _ if data["event_kind"] == "llm_status" => {
                if let Some(count) = data["heartbeat_count"].as_u64().filter(|n| *n > 0) {
                    self.observe(&format!("Model heartbeat {count}"));
                    return;
                }
                "Model activity"
            }
            _ => return,
        };
        self.observe(label);
    }
}

impl AppState {
    /// Called independently of animation so reduced motion still has a live clock.
    pub(crate) fn tick_status(&mut self) -> bool {
        if (!self.active && !self.progress.stale()) || self.connection_lost {
            return false;
        }
        if self
            .progress
            .last_tick
            .is_some_and(|tick| tick.elapsed().as_secs() == 0)
        {
            return false;
        }
        self.progress.last_tick = Some(Instant::now());
        self.refresh_running_status();
        true
    }

    pub(super) fn refresh_running_status(&mut self) {
        if self.connection_lost {
            return;
        }
        if self.progress.stale() {
            self.status.set_notice(Some(self.progress.health_message()));
        }
        // Snapshot and stream traffic must not replace an unanswered interaction.
        match self.bottom_pane.pending_interaction() {
            Some(ActiveInteractionRef::Permission(_)) => {
                if self.status.kind != StatusKind::PendingPermission {
                    self.status
                        .transition_to_pending_permission("Approve or deny this request".into());
                }
                return;
            }
            Some(ActiveInteractionRef::Human(_)) => {
                if self.status.kind != StatusKind::PendingClarification {
                    self.status
                        .transition_to_pending_clarification("Answer the pending questions".into());
                }
                return;
            }
            None => {}
        }
        if self.progress.stale() {
            self.status.set_terminal(
                StatusKind::StaleView,
                "View out of date".into(),
                self.progress.health_message(),
                self.status.elapsed_ms(),
            );
            return;
        }
        if !self.active {
            return;
        }
        let mut details = Vec::new();
        let activity = &self.progress.activity;
        if !activity.stage.is_empty() {
            details.push(activity.stage.replace('_', " "));
            if activity.attempt > 0 {
                details.push(format!("Attempt {}", activity.attempt));
            }
        }
        if activity.phase == "applying_feedback" {
            details.push("Applying review feedback".into());
        }
        if let Some((label, received)) = &self.progress.last_activity {
            details.push(format!(
                "{label} · Last event {}s ago",
                received.elapsed().as_secs()
            ));
        }
        let tasks = self.agents.tasks();
        let current = tasks
            .iter()
            .filter(|(_, task)| {
                self.progress.run_id.is_empty() || task["run_id"] == self.progress.run_id
            })
            .collect::<Vec<_>>();
        if !current.is_empty() {
            let mut totals = crate::multi_agents::TaskCounts::default();
            for (_, task) in &current {
                totals.observe(task);
            }
            details.push(format!(
                "Subagents (run total): {} running · {}/{} done",
                totals.running,
                totals.done,
                current.len()
            ));
            for (n, label) in [
                (totals.failed, "failed"),
                (totals.cancelled, "cancelled"),
                (totals.waiting, "waiting"),
                (totals.unconfirmed, "unconfirmed"),
            ] {
                if n > 0 {
                    details.push(format!("{n} {label}"));
                }
            }
            if let Some((_, task)) = current
                .iter()
                .filter(|(_, task)| !text(&task["activity"], "label").is_empty())
                .max_by_key(|(_, task)| text(&task["activity"], "timestamp"))
            {
                details.push(format!(
                    "Latest subagent · {}: {}",
                    crate::multi_agents::preview(&text(task, "task_name"), 40),
                    crate::multi_agents::preview(&crate::multi_agents::activity_text(task), 180)
                ));
            }
        }
        if let Some(message) = self.progress.stream_notice() {
            details.push(message);
        }
        if !self.agents.requests.is_empty() {
            details.push(format!(
                "{} request(s) need input · F4",
                self.agents.requests.len()
            ));
        }
        let detail = details.join(" · ");
        let mut header = match activity.role.as_str() {
            "core" => "Core".to_string(),
            "reviewer" => "Reviewer".to_string(),
            _ => "Working".to_string(),
        };
        if activity.round > 0 {
            header.push_str(&format!(" · Round {}", activity.round));
        }
        if self.progress.stopping {
            header = "Stopping".into();
        }
        self.status
            .set_running(header, detail, self.status.elapsed_ms(), true);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    fn event(kind: &str, data: Value) -> BridgeEvent {
        BridgeEvent {
            schema: crate::bridge::SCHEMA.into(),
            seq: 1,
            r#type: kind.into(),
            request_id: String::new(),
            identity: json!({"run_id":"run-a"}),
            data,
        }
    }
    fn snapshot(active: bool, interactions: Value) -> Value {
        json!({"items":[],"subagents":[],"worker_active":active,
            "status":{"run_id":"run-a","status":if active{"in_progress"}else{"completed"}},
            "interactions":interactions})
    }
    fn rendered(app: &AppState) -> String {
        app.status
            .render_lines(100, &app.theme)
            .iter()
            .map(crate::wrapping::line_plain_text)
            .collect::<Vec<_>>()
            .join("\n")
    }
    fn running() -> AppState {
        let mut app = AppState::default();
        app.event(event("snapshot", snapshot(true, json!([]))));
        app
    }

    #[test]
    fn stage_role_round_and_feedback_phase_follow_typed_snapshot_activity() {
        let mut app = running();
        let mut data = snapshot(true, json!([]));
        data["activity"] = json!({"role":"reviewer", "stage":"literature_grounding",
            "attempt":2, "round":5, "phase":"started", "source_item_id":"audit-5"});
        app.event(event("snapshot", data.clone()));
        assert_eq!(app.status.header, "Reviewer · Round 5");
        assert!(app
            .status
            .detail
            .contains("literature grounding · Attempt 2"));
        assert!(!rendered(&app).contains("literature grounding · Attempt 2"));
        data["activity"]["role"] = json!("core");
        data["activity"]["phase"] = json!("applying_feedback");
        app.event(event("snapshot", data));
        assert_eq!(app.status.header, "Core · Round 5");
        assert!(app.status.detail.contains("Applying review feedback"));
        assert!(!rendered(&app).contains("Applying review feedback"));
    }

    #[test]
    fn stale_snapshot_stops_animation_keeps_data_and_recovers_from_snapshot() {
        let mut app = running();
        let data = snapshot(true, json!([]));
        let history = app.canonical.clone();
        app.event(event(
            "view_health",
            json!({"state":"retrying", "error":"read failed"}),
        ));
        assert_eq!(app.status.kind, StatusKind::Running);
        app.event(event("view_health", json!({"state":"stale", "error":"read failed", "last_success_at":"2026-11-06T07:00:00Z"})));
        assert_eq!(app.status.kind, StatusKind::StaleView);
        assert!(!app.status.animates());
        assert_eq!(app.canonical, history);
        assert!(rendered(&app).contains("2026-11-06T07:00:00Z"));
        assert!(rendered(&app).contains("read failed"));
        app.event(event("view_health", json!({"state":"fresh"})));
        app.event(event("snapshot", data));
        assert_eq!(app.status.kind, StatusKind::Running);
        assert!(!rendered(&app).contains("read failed"));
        app.event(event("snapshot", snapshot(false, json!([]))));
        app.event(event(
            "view_health",
            json!({"state":"stale", "error":"offline view"}),
        ));
        assert_eq!(app.status.kind, StatusKind::StaleView);
        app.event(event("view_health", json!({"state":"fresh"})));
        app.event(event("snapshot", snapshot(false, json!([]))));
        assert_eq!(app.status.kind, StatusKind::Completed);
    }

    #[test]
    fn stale_view_preserves_permission_prompt_and_clears_notice_on_recovery() {
        let mut app = running();
        let data = snapshot(
            true,
            json!([{"kind":"permission","session_id":"approval","command":"example"}]),
        );
        app.event(event("snapshot", data.clone()));
        app.event(event(
            "view_health",
            json!({"state":"stale", "error":"read failed"}),
        ));
        assert_eq!(app.status.kind, StatusKind::PendingPermission);
        assert!(rendered(&app).contains("read failed"));
        app.event(event("view_health", json!({"state":"fresh"})));
        app.event(event("snapshot", data));
        assert_eq!(app.status.kind, StatusKind::PendingPermission);
        assert!(!rendered(&app).contains("read failed"));
    }

    #[test]
    fn progress_stream_status_survives_unchanged_snapshots_and_has_a_local_clock() {
        let mut app = running();
        app.event(event(
            "stream",
            json!({"event_kind":"llm_status","component":"core",
            "provider_inflight":true,"heartbeat_count":0,"stream_key":["run-a","core",1,""]}),
        ));
        app.progress.last_activity.as_mut().unwrap().1 = Instant::now() - Duration::from_secs(20);
        app.event(event("snapshot", snapshot(true, json!([]))));
        assert!(!rendered(&app).contains("since update"));
        assert!(!rendered(&app).contains("heartbeat"));
        assert!(
            app.history.is_empty(),
            "observations must not invent durable messages"
        );
        // Clock refresh does not depend on the animation setting.
        assert!(app.tick_status());
        assert!(!app.tick_status());
    }

    #[test]
    fn progress_only_displays_heartbeat_count_when_received() {
        let mut app = running();
        app.event(event(
            "stream",
            json!({"event_kind":"llm_status","heartbeat_count":3}),
        ));
        assert!(app
            .progress
            .last_activity
            .as_ref()
            .unwrap()
            .0
            .contains("Model heartbeat 3"));
        assert!(!rendered(&app).contains("Model heartbeat 3"));
        app.event(event(
            "stream",
            json!({"canonical_event_type":"response_completed"}),
        ));
        assert_eq!(
            app.progress.last_activity.as_ref().unwrap().0,
            "Model response"
        );
        assert!(!rendered(&app).contains("heartbeat 3"));
    }

    #[test]
    fn terminal_telemetry_does_not_look_like_another_heartbeat() {
        let mut app = running();
        app.event(event(
            "stream",
            json!({"event_kind":"llm_status","heartbeat_count":3,"provider_inflight":true}),
        ));
        assert_eq!(
            app.progress.last_activity.as_ref().unwrap().0,
            "Model heartbeat 3"
        );
        app.event(event(
            "stream",
            json!({"event_kind":"llm_status","heartbeat_count":3,"provider_inflight":false}),
        ));
        assert_eq!(
            app.progress.last_activity.as_ref().unwrap().0,
            "Model request ended"
        );
        assert!(app.live.is_empty());
        app.event(event(
            "stream",
            json!({"event_kind":"llm_status","heartbeat_count":0,"provider_inflight":true}),
        ));
        assert_eq!(
            app.progress.last_activity.as_ref().unwrap().0,
            "Model activity"
        );
    }

    #[test]
    fn progress_preserves_pending_permission_through_snapshot_and_stream() {
        let mut app = running();
        let requests = json!([{"kind":"permission","session_id":"approval","command":"example"}]);
        app.event(event("snapshot", snapshot(true, requests.clone())));
        app.event(event("snapshot", snapshot(true, requests)));
        app.event(event(
            "stream",
            json!({"canonical_event_type":"response_started","operation":"core_round"}),
        ));
        assert_eq!(app.status.kind, StatusKind::PendingPermission);
        assert!(!app.status.animates());
        app.event(event("snapshot", snapshot(true, json!([]))));
        assert_eq!(app.status.kind, StatusKind::Running);
    }

    #[test]
    fn progress_stopping_survives_snapshots_then_terminal_clears_activity() {
        let mut app = running();
        app.event(event("execution", json!({"state":"stopping"})));
        app.event(event("snapshot", snapshot(true, json!([]))));
        assert_eq!(app.status.header, "Stopping");
        app.event(event("snapshot", snapshot(false, json!([]))));
        assert!(!app.tick_status());
        assert!(app.progress.last_activity.is_none());
        assert!(!app.progress.stopping);
    }

    #[test]
    fn progress_ignores_stale_run_streams_and_clears_on_disconnect() {
        let mut app = running();
        app.event(event(
            "stream",
            json!({"event_kind":"llm_status","heartbeat_count":9,
            "stream_key":["old-run","core",1,""]}),
        ));
        assert!(!rendered(&app).contains("heartbeat 9"));
        app.event(event(
            "stream",
            json!({"event_kind":"llm_status","heartbeat_count":3}),
        ));
        app.disconnected("test connection lost");
        app.event(event("snapshot", snapshot(true, json!([]))));
        assert!(app.progress.last_activity.is_none());
        assert!(!app.tick_status());
        assert_eq!(app.status.header, "Disconnected");
    }

    #[test]
    fn progress_subagent_summary_is_scoped_to_current_run_without_child_text() {
        let mut app = running();
        let mut data = snapshot(true, json!([]));
        data["subagents"] = json!([{"tasks":[
            {"run_id":"run-a","status":"running","task_message":"private child prompt"},
            {"run_id":"run-a","status":"completed","completion_committed":true},
            {"run_id":"old","status":"running"}
        ]}]);
        app.event(event("snapshot", data));
        let text = rendered(&app);
        assert!(
            app.status
                .detail
                .contains("Subagents (run total): 1 running · 1/2 done"),
            "{text}"
        );
        assert!(!text.contains("private child prompt"));
        assert!(!text.contains("Subagents (run total)"));
        app.status
            .set_notice(Some("1 source warnings · open /audit for details".into()));
        assert!(rendered(&app).contains("source warnings"));
        for width in [40, 80, 120] {
            let lines = app.status.render_lines(width, &app.theme);
            assert!(lines.iter().all(|line| line.width() <= usize::from(width)));
            let full = lines
                .iter()
                .map(crate::wrapping::line_plain_text)
                .collect::<Vec<_>>()
                .join(" ");
            assert!(!full.contains("1/2 done"), "{full}");
            assert!(
                !full.contains('…'),
                "the activity heading and notice should fit: {full}"
            );
        }
    }

    #[test]
    fn progress_session_change_does_not_reuse_old_activity() {
        let mut app = running();
        app.event(event(
            "stream",
            json!({"event_kind":"llm_status","heartbeat_count":7}),
        ));
        let mut next = event("snapshot", snapshot(true, json!([])));
        next.identity = json!({"session_id":"new-session","run_id":"run-b"});
        app.event(next);
        assert!(!rendered(&app).contains("heartbeat 7"));
        assert!(app.live.is_empty());
        let mut empty = event(
            "snapshot",
            json!({"items":[], "subagents":[], "interactions":[],
            "worker_active":false, "status":{"status":"idle"}}),
        );
        empty.identity = json!({"session_id":""});
        app.event(empty);
        assert!(app.progress.run_id.is_empty());
        assert!(app.progress.activity.role.is_empty());
        assert!(app.live.is_empty());
        assert_eq!(app.workspace.session_id.as_deref(), Some(""));
    }
    #[test]
    fn unexpected_exit_status_keeps_specific_error_and_readable_label() {
        let mut app = running();
        let mut snap = snapshot(false, json!([]));
        snap["status"]["status"] = json!("unexpected_exit");
        snap["status"]["message"] =
            json!("PoolTimeout: Timed out waiting for an available HTTP connection");
        for _ in 0..2 {
            app.event(event("snapshot", snap.clone()));
            let text = rendered(&app);
            assert!(
                text.contains("Unexpected exit") && text.contains("PoolTimeout"),
                "{text}"
            );
            assert!(!text.contains("unexpected_exit"), "{text}");
            assert!(!app.active);
        }
    }
}
