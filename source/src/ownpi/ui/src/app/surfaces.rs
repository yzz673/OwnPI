//! Pure UI surfaces and local preferences. No execution or recovery authority.
use super::*;

impl AppState {
    pub(crate) fn refresh_pager(&mut self) {
        if self.pager.is_none() {
            return;
        }
        // Leave the column reserved by InlineTerminal out of source layouts,
        // so frames and rules do not wrap again during physical pagination.
        let content_width = self.width.saturating_sub(1).max(1);
        if self
            .pager
            .as_ref()
            .is_some_and(|pager| pager.static_content)
        {
            if let Some(body) = &self.pager_document {
                let lines = crate::markdown::render_markdown(body, content_width, &self.theme);
                if let Some(pager) = &mut self.pager {
                    pager.update_lines(lines, self.width, 0);
                }
            }
            return;
        }
        let mut lines = self
            .history
            .iter()
            .flat_map(|cell| cell.transcript_lines_edged(content_width))
            .collect::<Vec<_>>();
        let live = self.active_agent_stream_lines(content_width);
        let live_len = live.len();
        lines.extend(live);
        if self.preferences["interface_mode"] != "developer" {
            for item in self
                .canonical
                .iter()
                .filter(|item| text(item, "kind") == "model_activity")
            {
                lines.extend(crate::markdown::render_markdown(
                    &format!("\n```json\n{}\n```", text(&item["payload"], "transcript")),
                    content_width,
                    &self.theme,
                ));
            }
        }
        if let Some(pager) = &mut self.pager {
            if pager.transcript_is_stale(&lines, self.width) {
                pager.update_lines(lines, self.width, live_len);
            }
        }
    }
}

impl AppState {
    pub(super) fn local_panel(&mut self, title: &str, rows: Vec<(String, String, String)>) {
        let rows = rows
            .into_iter()
            .map(|(name, text, description)| {
                json!({"id":text,"name":name,"description":description,
            "action":{"method":"local","params":{"text":text}}})
            })
            .collect::<Vec<_>>();
        let footer = if title == "Review scope" {
            "Enter start review   Esc cancel"
        } else if title == "Copy latest response" {
            "Enter copy   Esc cancel"
        } else {
            "Enter confirm   Esc cancel"
        };
        let subtitle = if title == "Review scope" {
            "Choose what the review agent should inspect."
        } else {
            ""
        };
        self.panel(&json!({"presenter":"selection","view_id":title,"title":title,"subtitle":subtitle,"rows":rows,"footer_hint":footer}));
    }

    pub(super) fn preference_panel(&mut self, command: &str) {
        let (title, field, subtitle, options): (&str, &str, &str, Vec<(&str, &str, &str)>) =
            match command {
                "/raw" => (
                    "Transcript rendering",
                    "raw",
                    "Choose how runtime activity appears in history.",
                    vec![
                        ("Rich", "false", "Structured OwnPI history cells."),
                        ("Raw", "true", "Undecorated terminal transcript."),
                    ],
                ),
                "/vim" => (
                    "Vim editing",
                    "vim",
                    "Choose composer editing behavior.",
                    vec![
                        ("Enabled", "true", "Use Vim-style composer editing."),
                        ("Disabled", "false", "Use standard composer editing."),
                    ],
                ),
                "/mode" => (
                    "Interface mode",
                    "interface_mode",
                    "Applied after the setting is persisted.",
                    vec![
                        ("User", "user", "Hide developer-only diagnostics."),
                        (
                            "Developer",
                            "developer",
                            "Show developer diagnostics and audit detail.",
                        ),
                    ],
                ),
                _ => (
                    "Terminal notifications",
                    "notifications",
                    "Notify when work needs attention outside this window.",
                    vec![
                        (
                            "Enabled",
                            "true",
                            "Notify when execution ends or requests need attention.",
                        ),
                        ("Disabled", "false", "Keep terminal notifications disabled."),
                    ],
                ),
            };
        let current = if field == "interface_mode" {
            text(&self.preferences, field)
        } else {
            (self.preferences[field] == true).to_string()
        };
        let rows=options.into_iter().map(|(name,value,description)|json!({"id":value,"name":name,"description":description,
            "is_current":value==current,"dismiss_on_select":false,"action":{"method":"local","params":{"text":format!("{command}_apply {value}")}}})).collect::<Vec<_>>();
        self.panel(&json!({"presenter":"selection","view_id":command,"title":title,"subtitle":subtitle,"rows":rows,"footer_hint":"Enter apply   Esc back"}));
    }

    pub(super) fn syntax_panel(&mut self) {
        let current = text(&self.preferences, "syntax_theme");
        let mut rows = vec![
            json!({"id":"default","name":"Terminal default", "is_current":current == "default",
            "dismiss_on_select":false,"action":{"method":"configure","params":{"field":"syntax_theme","value":"default","menu_commit":true}}}),
        ];
        for name in crate::syntax_highlight::available_theme_names() {
            rows.push(json!({"id":name,"name":name,"is_current":name == current,
                "dismiss_on_select":false,"action":{"method":"configure","params":{"field":"syntax_theme","value":name,"menu_commit":true}}}));
        }
        rows.push(json!({"id":"appearance","name":"Terminal appearance", "description":"Auto / light / dark",
            "dismiss_on_select":false,"action":{"method":"command","params":{"text":"/appearance","menu_navigation":"push"}}}));
        self.panel(&json!({"presenter":"selection","view_id":"syntax_theme","title":"Syntax theme","subtitle":"Applied after the setting is persisted.","rows":rows,"footer_hint":"Enter apply   Esc back"}));
    }

    pub(super) fn review_panel(&mut self) {
        self.local_panel(
            "Review scope",
            vec![
                (
                    "Working tree".into(),
                    "/review_apply working".into(),
                    "All staged and unstaged changes".into(),
                ),
                (
                    "Staged changes".into(),
                    "/review_apply staged".into(),
                    "Only git diff --cached".into(),
                ),
                (
                    "Against base".into(),
                    "/review_apply base".into(),
                    "Branch against its upstream merge base".into(),
                ),
            ],
        );
    }

    fn latest_response(&self) -> Option<String> {
        if let Some(task) = self.agents.task() {
            return self
                .canonical
                .iter()
                .rev()
                .find(|value| {
                    text(value, "kind") == "agent_message"
                        && value["payload"]["source_list"] != true
                        && crate::multi_agents::same_agent(&value["agent_ref"], &task["agent_ref"])
                })
                .map(|value| text(&value["payload"], "text"))
                .or_else(|| {
                    (task["completion_committed"] == true)
                        .then(|| text(&task, "completion_content"))
                })
                .filter(|value| !value.trim().is_empty());
        }
        self.canonical
            .iter()
            .rev()
            .find(|item| {
                text(item, "kind") == "agent_message"
                    && item["payload"]["source_list"] != true
                    && !matches!(
                        item["scope"]["actor_kind"].as_str(),
                        Some("reviewer" | "subagent")
                    )
            })
            .map(|item| text(&item["payload"], "text"))
            .filter(|value| !value.trim().is_empty())
    }

    pub(super) fn copy_panel(&mut self) {
        let Some(source) = self.latest_response() else {
            self.status
                .set_notice(Some("No assistant response to copy yet".into()));
            return;
        };
        let preview = source
            .lines()
            .take(3)
            .collect::<Vec<_>>()
            .join(" ")
            .chars()
            .take(180)
            .collect::<String>();
        self.panel(&json!({"presenter":"selection","view_id":"copy.preview","title":"Copy latest response","subtitle":preview,
            "footer_hint":"Enter copy   Esc cancel","rows":[
                {"id":"markdown","name":"Markdown","description":"Copy the original assistant Markdown.","action":{"method":"local","params":{"text":"/copy_apply markdown"}}},
                {"id":"plain","name":"Plain text","description":"Copy a terminal-safe plain-text representation.","action":{"method":"local","params":{"text":"/copy_apply plain"}}}]}));
    }

    pub(super) fn copy_response(&mut self, plain: bool) -> Vec<Value> {
        let Some(source) = self.latest_response() else {
            self.status
                .set_notice(Some("No assistant response to copy yet".into()));
            return Vec::new();
        };
        let source = if plain {
            crate::markdown::render_markdown(&source, 10000, &self.theme)
                .iter()
                .map(crate::wrapping::line_plain_text)
                .collect::<Vec<_>>()
                .join("\n")
        } else {
            source
        };
        match crate::clipboard_copy::copy_to_clipboard(&source) {
            Ok(lease) => {
                self.clipboard_lease = lease;
                self.status.set_notice(Some("Response copied".into()));
            }
            Err(error) => self.status.set_notice(Some(format!("Clipboard: {error}"))),
        }
        Vec::new()
    }

    pub(super) fn skill_catalog(&mut self, values: &Value) {
        let rows: Vec<crate::bridge::protocol::SkillCatalogEntry> = values
            .as_array()
            .map(|rows| {
                rows.iter()
                    .map(|row| crate::bridge::protocol::SkillCatalogEntry {
                        name: text(row, "name"),
                        display_name: text(row, "display_name"),
                        description: text(row, "description"),
                        path: text(row, "path"),
                        enabled: row["enabled"] == true,
                        removable: row["removable"] == true,
                    })
                    .collect()
            })
            .unwrap_or_default();
        self.skill_entries = rows.clone();
        self.bottom_pane.set_skill_catalog(rows);
    }

    pub(super) fn notify(&mut self, message: &str) {
        if self.terminal_focused || self.preferences["notifications"] == false {
            return;
        }
        self.terminal_effects
            .push(format!("\x07\x1b]9;{}\x1b\\", terminal_text(message)));
    }

    pub(super) fn sync_terminal_title(&mut self) {
        let project = self
            .workspace
            .project_root
            .as_deref()
            .and_then(|path| std::path::Path::new(path).file_name())
            .map(|part| part.to_string_lossy().to_string())
            .unwrap_or_default();
        let title = format!(
            "OwnPI · {project} · {}",
            self.activity_state_label().unwrap_or("ready")
        );
        if title != self.last_title {
            self.terminal_effects
                .push(format!("\x1b]0;{}\x1b\\", terminal_text(&title)));
            self.last_title = title;
        }
    }
}

pub(crate) fn terminal_text(value: &str) -> String {
    value
        .chars()
        .filter(|ch| !ch.is_control())
        .take(160)
        .collect()
}

#[cfg(test)]
mod source_list_tests {
    use super::*;

    #[test]
    fn copy_selects_the_answer_for_main_and_subagent() {
        let agent = json!({"run_id":"r", "conversation_id":"c", "actor_id":"child"});
        let mut app = AppState::default();
        app.canonical = vec![
            json!({"kind":"agent_message", "agent_ref":agent, "payload":{"text":"original answer"}}),
            json!({"kind":"agent_message", "agent_ref":agent, "payload":{"text":"source list", "source_list":true}}),
        ];
        assert_eq!(app.latest_response().as_deref(), Some("original answer"));
        app.agents.batches =
            vec![json!({"item_id":"batch", "tasks":[{"task_index":0,"agent_ref":agent}]})];
        app.agents.active = Some("batch:0".into());
        assert_eq!(app.latest_response().as_deref(), Some("original answer"));
    }
}
