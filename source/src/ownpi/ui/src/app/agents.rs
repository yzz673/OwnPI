//! UI-only scoped views, requests and keyboard navigation.
use super::*;
use crate::multi_agents::{same_agent, AgentPopup, PopupKind};

impl AppState {
    pub(crate) fn open_agent_popup(&mut self, kind: PopupKind) {
        self.suspend_request();
        let selected = if kind == PopupKind::Agents {
            self.agents.active.clone().unwrap_or_else(|| "main".into())
        } else {
            self.agents
                .requests
                .first()
                .map(|v| text(v, "session_id"))
                .unwrap_or_default()
        };
        self.agents.popup = Some(AgentPopup { kind, selected });
    }

    fn suspend_request(&mut self) {
        if self.bottom_pane.pending_interaction().is_none() {
            self.bottom_pane.close_active_bottom_view();
            return;
        }
        self.bottom_pane.sync_input_to_overlay();
        if let Some(ActiveInteractionRef::Human(human)) = self.bottom_pane.pending_interaction() {
            self.drafts
                .insert(human.interaction_id.clone(), human.clone());
        }
        self.clear_request_overlay();
        self.agents.selected_request = None;
    }

    pub(crate) fn clear_request_overlay(&mut self) {
        if self.bottom_pane.pending_interaction().is_some() {
            self.bottom_pane.clear_overlay();
        }
        if let Some(draft) = self.agents.main_draft.take() {
            self.bottom_pane.set_composer(draft);
        }
    }

    pub(crate) fn request_owner_label(&self, request: &Value) -> String {
        self.agents.request_owner(request)
    }

    pub(crate) fn select_agent(&mut self, key: String) {
        self.agents.save_position();
        if key == "main" {
            self.agents.active = None;
            if let Some(pager) = self.agents.suspended_main_pager.take() {
                self.pager = Some(pager);
            }
        } else if self.agents.tasks().iter().any(|(id, _)| id == &key) {
            if self.agents.active.is_none() {
                self.agents.suspended_main_pager = self.pager.take();
            }
            self.agents.active = Some(key);
            self.agents.details = false;
            self.agents.raw = false;
            self.agents.restore_position();
            self.refresh_agent_pager();
        }
    }

    pub(crate) fn refresh_agent_pager(&mut self) {
        let Some(task) = self.agents.task() else {
            return;
        };
        let content_width = self.width.saturating_sub(1).max(1);
        let selected = &task["agent_ref"];
        let values = self
            .canonical
            .iter()
            .filter(|value| {
                same_agent(&value["agent_ref"], selected)
                    && (self.agents.details
                        || self.agents.raw
                        || task["completion_committed"] != true
                        || text(value, "tool_name") != "complete_subagent")
            })
            .cloned()
            .collect::<Vec<_>>();
        let (cells, projection) = self.history_cells(&values, false);
        let mut body = crate::markdown::render_markdown(
            &format!(
                "{} · {}{}\n\n{}",
                text(&task, "display_status"),
                text(&task, "stage").replace('_', " "),
                if task["reclaimed"] == true {
                    " · Worker reclaimed"
                } else {
                    ""
                },
                text(&task, "task_message")
            ),
            content_width,
            &self.theme,
        );
        let outcome = text(&task, "terminal_status");
        let recorded = text(&task, "last_recorded_status");
        let state = if outcome.is_empty() {
            if recorded.is_empty() {
                text(&task, "status")
            } else {
                recorded
            }
        } else {
            outcome
        };
        body.splice(
            1..1,
            crate::wrapping::adaptive_wrap_text(
                &format!(
                    "Execution: {} · Feedback: {} · Worker: {}",
                    state,
                    if task["completion_committed"] == true {
                        "submitted"
                    } else {
                        "not submitted"
                    },
                    if task["reclaimed"] == true {
                        "reclaimed"
                    } else {
                        "not reclaimed"
                    }
                ),
                content_width,
            )
            .into_iter()
            .map(Line::from),
        );
        if !text(&task, "error").is_empty() {
            body.extend(crate::markdown::render_markdown(
                &format!("\nSource / execution diagnostic: {}", text(&task, "error")),
                content_width,
                &self.theme,
            ));
        }
        if !selected.is_object() {
            body.push(Line::from("Conversation is not available yet."));
        }
        let mut previous: Option<Arc<dyn HistoryCell>> = None;
        for cell in cells {
            let mut lines = if self.agents.raw {
                cell.raw_lines()
            } else if self.agents.details {
                cell.transcript_lines_edged(content_width)
            } else {
                cell.display_lines_edged(content_width)
            };
            if !lines.is_empty() {
                if previous.as_ref().is_none_or(|previous| {
                    crate::history_cell::history_cells_require_inter_cell_spacing(
                        previous.as_ref(),
                        cell.as_ref(),
                    )
                }) {
                    body.push(Line::default());
                }
                body.append(&mut lines);
                previous = Some(cell);
            }
        }
        if task["completion_committed"] == true && !text(&task, "completion_content").is_empty() {
            body.extend(crate::markdown::render_markdown(
                &format!(
                    "\n### Submitted feedback\n\n{}",
                    text(&task, "completion_content")
                ),
                content_width,
                &self.theme,
            ));
        }
        let live = self.agent_stream_lines(content_width, Some(selected), projection);
        let live_count = live.len();
        body.extend(live);
        self.agents.pager.update_lines(body, self.width, live_count);
        let mut parents = Vec::new();
        let mut parent = task["parent_ref"].clone();
        let tasks = self.agents.tasks();
        let mut visited = HashSet::new();
        while text(&parent, "actor_kind") == "subagent" && visited.insert(parent.to_string()) {
            let Some((_, ancestor)) = tasks
                .iter()
                .find(|(_, t)| same_agent(&t["agent_ref"], &parent))
            else {
                break;
            };
            parents.push(self.agents.label(ancestor));
            parent = ancestor["parent_ref"].clone();
        }
        parents.reverse();
        let mut path = vec!["Main".to_string()];
        if let Some(round) = task["audit_context"]["review_round"].as_u64() {
            path.push(format!("Reviewer · Round {round}"));
        }
        path.extend(parents);
        path.push(self.agents.label(&task));
        self.agents.pager.title_override = Some(if self.width < 80 {
            format!(
                "{} · {}",
                crate::multi_agents::preview(
                    &self.agents.label(&task),
                    self.width.saturating_sub(18)
                ),
                text(&task, "child_id").chars().take(8).collect::<String>()
            )
        } else {
            path.join(" / ")
        });
        self.agents.pager.footer_override = Some(if self.width < 70 {
            format!("F4 Requests ({}) · Esc Main", self.agents.requests.len())
        } else {
            format!(
                "Esc Main · Alt←/→ · Ctrl+T {} · F4 Requests ({})",
                if self.agents.details {
                    "Log"
                } else {
                    "Details"
                },
                self.agents.requests.len()
            )
        });
    }

    // Some means the scoped view consumed the key, including keys that would
    // otherwise edit or submit Main's retained composer draft.
    pub(crate) fn agent_key(&mut self, key: KeyEvent) -> Option<Vec<Value>> {
        if matches!(key.code, KeyCode::F(2) | KeyCode::F(4)) {
            self.open_agent_popup(if key.code == KeyCode::F(2) {
                PopupKind::Agents
            } else {
                PopupKind::Requests
            });
            return Some(Vec::new());
        }
        if self.agents.popup.is_some() {
            let rows = self.agents.popup_rows();
            let popup = self.agents.popup.as_ref().unwrap();
            let index = rows
                .iter()
                .position(|(id, _, _)| *id == popup.selected)
                .unwrap_or(0);
            match key.code {
                KeyCode::Esc => self.agents.popup = None,
                KeyCode::Up | KeyCode::Down | KeyCode::Home | KeyCode::End => {
                    if !rows.is_empty() {
                        let next = match key.code {
                            KeyCode::Up => index.saturating_sub(1),
                            KeyCode::Home => 0,
                            KeyCode::End => rows.len() - 1,
                            _ => (index + 1).min(rows.len() - 1),
                        };
                        self.agents.popup.as_mut().unwrap().selected = rows[next].0.clone();
                    }
                }
                KeyCode::Enter => {
                    if let Some((id, _, _)) = rows.get(index) {
                        let id = id.clone();
                        let kind = popup.kind;
                        self.agents.popup = None;
                        if kind == PopupKind::Agents {
                            self.select_agent(id);
                        } else {
                            self.dismissed.remove(&id);
                            self.agents.selected_request = Some(id);
                            self.sync_interactions(&self.agents.requests.clone());
                        }
                    }
                }
                _ => {}
            }
            return Some(Vec::new());
        }
        if self.agents.active.is_some()
            && key.modifiers.contains(KeyModifiers::CONTROL)
            && key.code == KeyCode::Char('c')
            && self.pager.is_none()
        {
            self.suspend_request();
            self.panel(&json!({"presenter":"selection","title":"Stop the entire run?","subtitle":"This stops all agents, not only the viewed subagent.",
                "rows":[{"id":"stop","name":"Stop entire run","action":{"method":"stop","params":{}}}]}));
            return Some(Vec::new());
        }
        let has_overlay = self.bottom_pane.active_bottom_view_is_visible();
        let navigable = !has_overlay
            && self.pager.is_none()
            && (self.agents.active.is_some() || self.bottom_pane.composer().is_empty());
        let alt = key.modifiers.contains(KeyModifiers::ALT);
        let direction = if alt
            && (key.code == KeyCode::Left
                || (cfg!(target_os = "macos") && key.code == KeyCode::Char('b')))
        {
            -1
        } else if alt
            && (key.code == KeyCode::Right
                || (cfg!(target_os = "macos") && key.code == KeyCode::Char('f')))
        {
            1
        } else {
            0
        };
        if navigable && direction != 0 {
            let mut keys = vec!["main".to_string()];
            keys.extend(self.agents.tasks().into_iter().map(|(id, _)| id));
            let current = self.agents.active.as_deref().unwrap_or("main");
            let index = keys.iter().position(|id| id == current).unwrap_or(0);
            let next = (index as isize + direction).rem_euclid(keys.len() as isize) as usize;
            self.select_agent(keys[next].clone());
            return Some(Vec::new());
        }
        if self.agents.active.is_none() || has_overlay || self.pager.is_some() {
            return None;
        }
        if key.modifiers.contains(KeyModifiers::CONTROL) {
            match key.code {
                KeyCode::Char('t') => {
                    self.agents.save_position();
                    self.agents.raw = false;
                    self.agents.details = !self.agents.details;
                    self.agents.restore_position();
                    self.refresh_agent_pager();
                }
                KeyCode::Char('y') => self.copy_panel(),
                _ => {}
            }
            return Some(Vec::new());
        }
        match key.code {
            KeyCode::Esc=>{if self.agents.details || self.agents.raw {self.agents.save_position();self.agents.details=false;self.agents.raw=false;self.agents.restore_position();self.refresh_agent_pager();}else{self.select_agent("main".into());}},
            KeyCode::Up=>self.agents.pager.scroll_up(1),KeyCode::Down=>self.agents.pager.scroll_down(1),
            KeyCode::PageUp=>self.agents.pager.scroll_up(12),KeyCode::PageDown=>self.agents.pager.scroll_down(12),
            KeyCode::Home=>self.agents.pager.scroll_to_top(),KeyCode::End=>self.agents.pager.scroll_to_bottom(),
            // Read-only command shortcuts; no hidden composer or command queue.
            KeyCode::Char('/')=>self.panel(&json!({"presenter":"selection","title":"Subagent view","rows":[
                {"id":"copy","name":"/copy","action":{"method":"local","params":{"text":"/copy"}}},
                {"id":"raw","name":"/raw","action":{"method":"local","params":{"text":"/raw"}}},
                {"id":"agents","name":"/subagents","action":{"method":"local","params":{"text":"/subagents"}}},
                {"id":"requests","name":"/requests","action":{"method":"local","params":{"text":"/requests"}}},
                {"id":"exit","name":"/exit","action":{"method":"local","params":{"text":"/exit"}}}]})),
            _=>{}
        }
        Some(Vec::new())
    }
}
