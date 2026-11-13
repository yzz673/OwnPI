use super::*;

impl AppState {
    pub(crate) fn handle_key(&mut self, key: KeyEvent) -> Vec<Value> {
        if let Some(commands) = self.agent_key(key) {
            return commands;
        }
        if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('g') {
            self.edit_draft = true;
            return Vec::new();
        }
        if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('l') {
            self.clear_terminal = true;
            self.hidden_history = self.history.len();
            self.pending_history.clear();
            self.rebuild = false;
            return Vec::new();
        }
        if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('c') {
            if self.active {
                return vec![self.request("stop", json!({}))];
            }
            return self.exit_panel();
        }
        if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('t') {
            if self.pager.is_some() {
                self.pager = None;
            } else {
                self.pager_document = None;
                self.pager = Some(TranscriptPagerState::new(Vec::new(), self.width));
                self.refresh_pager();
            }
            return Vec::new();
        }
        if let Some(pager) = &mut self.pager {
            match key.code {
                KeyCode::Esc => self.pager = None,
                KeyCode::Up => pager.scroll_up(1),
                KeyCode::Down => pager.scroll_down(1),
                KeyCode::PageUp => pager.scroll_up(12),
                KeyCode::PageDown => pager.scroll_down(12),
                KeyCode::Home => pager.scroll_to_top(),
                KeyCode::End => pager.scroll_to_bottom(),
                _ => {}
            }
            return Vec::new();
        }
        if key.code == KeyCode::Esc {
            self.menu_epoch += 1;
            let mut commands = self.escape();
            if commands.is_empty() {
                commands.extend(self.refresh_parent_menu());
            }
            return commands;
        }
        if self.bottom_pane.human_question_bottom_view_is_visible()
            && key.code == KeyCode::Enter
            && key.modifiers.is_empty()
        {
            return self.answer();
        }
        if self.bottom_pane.permission_overlay_is_visible() {
            match key.code {
                KeyCode::PageUp | KeyCode::PageDown => {
                    self.bottom_pane
                        .page_permission_details(key.code == KeyCode::PageDown);
                    return Vec::new();
                }
                KeyCode::Enter => {
                    if !key.modifiers.is_empty() {
                        return Vec::new();
                    }
                    if let Some(option) = self.bottom_pane.selected_permission_option_id() {
                        return self.permission(option);
                    }
                }
                KeyCode::Up => {
                    self.bottom_pane.move_overlay_up();
                    return Vec::new();
                }
                KeyCode::Down => {
                    self.bottom_pane.move_overlay_down();
                    return Vec::new();
                }
                KeyCode::Char(character) => {
                    if !key.modifiers.is_empty() && key.modifiers != KeyModifiers::SHIFT {
                        return Vec::new();
                    }
                    if let Some(option) = self
                        .bottom_pane
                        .permission_option_id_for_shortcut(character)
                    {
                        return self.permission(option);
                    }
                }
                _ => {}
            }
        }
        if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('s') {
            if self.bottom_pane.resume_browser_key(key) {
                return Vec::new();
            }
            return self.answer();
        }
        if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('v') {
            match crate::clipboard_image::paste_image_from_clipboard() {
                Ok(Some(path)) => self.bottom_pane.attach_local_image(path),
                Ok(None) => self
                    .status
                    .set_notice(Some("No image on the clipboard".into())),
                Err(error) => self.status.set_notice(Some(error)),
            }
            return Vec::new();
        }
        let input = self.bottom_pane.handle_key(key);
        self.input_result(input)
    }

    pub(crate) fn input_result(&mut self, input: InputResult) -> Vec<Value> {
        match input {
            InputResult::SubmittedText(text) => self.submit(text),
            InputResult::Command(submission) | InputResult::CommandWithArgs(submission) => {
                self.bottom_pane.record_submission(&submission.text);
                self.bottom_pane.clear_composer();
                self.command(&submission.text)
            }
            InputResult::UnknownSlash(text) => {
                self.status
                    .set_notice(Some(format!("Unknown command: {text}")));
                Vec::new()
            }
            InputResult::CommandAction(payload) => self.native_skill_action(payload),
            InputResult::SelectionPicked(action) => {
                if action.kind == "close" {
                    return Vec::new();
                }
                let source = self.bottom_pane.menu_view_id();
                if source.is_some()
                    && self.pending.values().any(|(kind, params)| {
                        matches!(kind.as_str(), "command" | "configure")
                            && params["source_view"].as_str() == source.as_deref()
                            && params["source_epoch"].as_u64() == Some(self.menu_epoch)
                    })
                {
                    self.bottom_pane
                        .set_selection_feedback("Waiting for the current menu action…");
                    return Vec::new();
                }

                if action.kind == "local" {
                    self.menu_navigation = action.payload["params"]["menu_navigation"]
                        .as_str()
                        .map(str::to_string);
                    return self.command(&action.text);
                }
                if action.kind == "draft" {
                    self.bottom_pane.set_composer(action.text);
                    return Vec::new();
                }
                if action.kind == "request" {
                    let method = text(&action.payload, "method");
                    if method == "submit" {
                        return self.submit(text(&action.payload["params"], "text"));
                    }
                    if method == "resume" && self.active {
                        self.status.set_notice(Some(
                            "Stop active work before resuming another run.".into(),
                        ));
                        return Vec::new();
                    }
                    if method == "shutdown" {
                        self.quit_requested = true;
                    }
                    if action.payload["params"]["close_menus"] == true {
                        self.bottom_pane.close_active_bottom_view();
                    }
                    return vec![self.request(&method, action.payload["params"].clone())];
                }
                Vec::new()
            }
            InputResult::OptionPicked(option) => {
                if self.bottom_pane.permission_overlay_is_visible() {
                    self.permission(option)
                } else {
                    self.command(&option)
                }
            }
            InputResult::HumanQuestionAdvanceOrSubmit => self.answer(),
            InputResult::ComposerReplace(text) => {
                self.bottom_pane.set_composer(text);
                Vec::new()
            }
            _ => Vec::new(),
        }
    }

    fn submit(&mut self, mut text: String) -> Vec<Value> {
        if self.connection_lost {
            self.bottom_pane.set_composer(text);
            self.status.set_notice(Some(
                "Disconnected. Draft kept; restart OwnPI and inspect the run before retrying."
                    .into(),
            ));
            return Vec::new();
        }
        if self.agents.active.is_some() {
            self.status.set_notice(Some(
                "Subagent view is read-only. Return to Main to submit a task.".into(),
            ));
            return Vec::new();
        }
        if self.active || self.quit_requested {
            self.bottom_pane.set_composer(text);
            self.status.set_notice(Some(
                "OwnPI is running. Use Ctrl-C to stop; answer requests in their panel.".into(),
            ));
            return Vec::new();
        }
        self.active = true;
        let mut draft = self.bottom_pane.composer_draft_snapshot();
        if draft.text.is_empty() {
            draft.text = text.clone();
        }
        let local_images = self.bottom_pane.take_local_images();
        let remote_images = self.bottom_pane.take_remote_images();
        for image in &local_images {
            text = text.replace(
                &image.placeholder,
                &format!("[Attached image: {}]", image.path.display()),
            );
        }
        for image in &remote_images {
            text = text.replace(
                &image.placeholder,
                &format!("[Attached image: {}]", image.url),
            );
        }
        self.bottom_pane.record_submission(&text);
        self.bottom_pane.clear_composer();
        self.status
            .start_working("Working".into(), String::new(), 0, true);
        let request = self.request("submit", json!({"text": text}));
        self.submission = Some(Submission {
            request_id: request["request_id"].as_str().unwrap().into(),
            text,
            draft,
            local_images,
            remote_images,
            state: SubmissionState::Sending,
        });
        vec![request]
    }

    fn answer(&mut self) -> Vec<Value> {
        if self.bottom_pane.interaction_submission_requested() {
            return Vec::new();
        }
        if let HumanOverlaySubmitAction::Submit {
            interaction_id,
            answers_json,
        } = self.bottom_pane.human_advance_or_submit()
        {
            self.bottom_pane
                .mark_interaction_submitting(&interaction_id);
            return vec![self.request(
                "answer",
                json!({"interaction_id": interaction_id, "answers": answers_json}),
            )];
        }
        Vec::new()
    }

    fn permission(&mut self, option: String) -> Vec<Value> {
        if let Some(crate::bottom_pane::ActiveInteractionMut::Permission(p)) =
            self.bottom_pane.pending_interaction_mut()
        {
            if option == "remember_back" {
                p.options = std::mem::take(&mut p.saved_options);
                return Vec::new();
            }
            if option == "approve_session" {
                if p.remember_scope.is_empty() || p.submission.blocked() {
                    return Vec::new();
                }
                p.saved_options = std::mem::take(&mut p.options);
                p.options = vec![
                    PendingOption {
                        id: "remember_confirm".into(),
                        label: "Confirm".into(),
                        description: String::new(),
                    },
                    PendingOption {
                        id: "remember_back".into(),
                        label: "Back".into(),
                        description: String::new(),
                    },
                ];
                return Vec::new();
            }
            if option == "remember_confirm" && p.saved_options.is_empty() {
                return Vec::new();
            }
        }
        let Some(ActiveInteractionRef::Permission(permission)) =
            self.bottom_pane.pending_interaction()
        else {
            return Vec::new();
        };
        let id = permission.interaction_id.clone();
        let (decision, scope) = match option.as_str() {
            "approve" => ("approve", "once"),
            "remember_confirm" => ("approve", "session"),
            "deny_command" => ("deny_command", "once"),
            "deny" => ("deny", "once"),
            _ => return Vec::new(),
        };
        if !self.bottom_pane.mark_interaction_submitting(&id) {
            return Vec::new();
        }
        vec![self.request(
            "permission",
            json!({"interaction_id": id, "decision": decision, "scope": scope}),
        )]
    }

    fn escape(&mut self) -> Vec<Value> {
        if let Some(crate::bottom_pane::ActiveInteractionMut::Permission(p)) =
            self.bottom_pane.pending_interaction_mut()
        {
            if !p.saved_options.is_empty() {
                p.options = std::mem::take(&mut p.saved_options);
                return Vec::new();
            }
        }
        if self.bottom_pane.human_question_bottom_view_is_visible() {
            self.bottom_pane.sync_input_to_overlay();
            if let Some(ActiveInteractionRef::Human(human)) = self.bottom_pane.pending_interaction()
            {
                self.drafts
                    .insert(human.interaction_id.clone(), human.clone());
                self.dismissed.insert(human.interaction_id.clone());
            }
            self.agents.selected_request = None;
            self.clear_request_overlay();
            self.status.set_notice(Some(
                "Question draft retained. Use F4 or /requests to reopen.".into(),
            ));
            return Vec::new();
        }
        if self.bottom_pane.vim_insert_mode_active() {
            self.bottom_pane.vim_enter_normal_mode();
            return Vec::new();
        }
        match self.bottom_pane.handle_escape() {
            EscapeAction::CancelPermission { .. } => {
                if let Some(id) = self.agents.selected_request.clone() {
                    self.dismissed.insert(id);
                }
                self.agents.selected_request = None;
                self.clear_request_overlay();
                Vec::new()
            }
            EscapeAction::InterruptHumanInteraction => {
                self.bottom_pane.sync_input_to_overlay();
                if let Some(ActiveInteractionRef::Human(human)) =
                    self.bottom_pane.pending_interaction()
                {
                    self.drafts
                        .insert(human.interaction_id.clone(), human.clone());
                    self.dismissed.insert(human.interaction_id.clone());
                }
                self.clear_request_overlay();
                self.status.set_notice(Some(
                    "Question draft retained. Use F4 or /requests to reopen.".into(),
                ));
                Vec::new()
            }
            EscapeAction::None if self.active => vec![self.request("stop", json!({}))],
            EscapeAction::None if !self.bottom_pane.composer().is_empty() => {
                self.bottom_pane.clear_composer();
                Vec::new()
            }
            EscapeAction::None => self.exit_panel(),
            _ => Vec::new(),
        }
    }

    pub(crate) fn command(&mut self, value: &str) -> Vec<Value> {
        let command = value.split_whitespace().next().unwrap_or_default();
        match command {
            "/subagents" => {
                self.open_agent_popup(crate::multi_agents::PopupKind::Agents);
                Vec::new()
            }
            "/requests" => {
                self.open_agent_popup(crate::multi_agents::PopupKind::Requests);
                Vec::new()
            }
            "/status" if !self.dismissed.is_empty() => {
                self.dismissed.clear();
                vec![self.request("inspect", json!({}))]
            }
            "/exit" => self.exit_panel(),
            "/menu_back" => {
                self.menu_epoch += 1;
                self.bottom_pane.pop_menu_view();
                self.refresh_parent_menu()
            }
            "/skills_list" => {
                self.bottom_pane.close_active_bottom_view();
                self.bottom_pane.set_composer("$");
                Vec::new()
            }
            "/skills_manage" | "/skills_remove" | "/skills_install" => {
                use crate::bottom_pane::SkillsToggleViewState;
                let view = match command {
                    "/skills_manage" => SkillsToggleViewState::new(self.skill_entries.clone()),
                    "/skills_remove" => {
                        SkillsToggleViewState::new_remove(self.skill_entries.clone())
                    }
                    _ => SkillsToggleViewState::new_install(),
                };
                self.bottom_pane.push_skills_toggle(view);
                Vec::new()
            }
            "/clear" if !self.active => {
                self.panel(&json!({"presenter":"selection","view_id":"clear.confirm","title":"Start a new chat?",
                    "subtitle":"Start a new chat in the same project. Files are shared; use /project for a separate workspace.","selected_row_id":"cancel", "footer_hint":"Enter confirm   Esc cancel",
                    "rows":[{"id":"cancel","name":"Cancel","dismiss_on_select":false,"description":"Keep the current chat.","action":{"method":"local","params":{"text":"/menu_back"}}},
                    {"id":"clear","name":"Start new chat","description":"Preserve this session and clear the current surface.","action":{"method":"local","params":{"text":"/clear_apply"}}}]}));
                Vec::new()
            }
            "/clear_apply" if !self.active => {
                self.hidden_history = 0;
                vec![self.request("new_session", json!({}))]
            }
            "/clear" => {
                self.status.set_notice(Some(
                    "Stop active work before starting a new session.".into(),
                ));
                Vec::new()
            }
            "/theme" => {
                self.syntax_panel();
                Vec::new()
            }
            "/raw" if self.agents.active.is_some() => {
                self.agents.raw = !self.agents.raw;
                self.refresh_agent_pager();
                Vec::new()
            }
            "/raw" | "/vim" | "/mode" | "/notifications" => {
                self.preference_panel(command);
                Vec::new()
            }
            "/raw_apply" | "/vim_apply" | "/mode_apply" | "/notifications_apply" => {
                let field = match command {
                    "/mode_apply" => "interface_mode",
                    "/raw_apply" => "raw",
                    "/vim_apply" => "vim",
                    _ => "notifications",
                };
                let argument = value.split_whitespace().nth(1).unwrap_or("");
                let setting = if field == "interface_mode" {
                    json!(argument)
                } else {
                    json!(argument == "true")
                };
                vec![self.request(
                    "configure",
                    json!({"field":field,"value":setting,"menu_commit":true}),
                )]
            }
            "/copy" => {
                self.copy_panel();
                Vec::new()
            }
            "/copy_apply" => self.copy_response(value.ends_with(" plain")),
            "/diff" => {
                let root = self.workspace.project_root.as_deref().unwrap_or(".");
                self.document(
                    "Working tree diff".into(),
                    crate::command_diff::working_tree_diff(std::path::Path::new(root)),
                );
                Vec::new()
            }
            "/review" => {
                self.review_panel();
                Vec::new()
            }
            "/review_apply" => {
                let scope = value.split_whitespace().nth(1).unwrap_or("working");
                let scope = match scope { "staged" => "only the staged changes (git diff --cached)",
                    "base" => "this branch against its upstream merge base; determine that base from Git configuration", _ => "all staged and unstaged working-tree changes" };
                self.submit(format!("Review {scope} in this repository. Report actionable findings with file and line references. Do not modify files."))
            }
            "/commands" => {
                // The catalog is Bridge-owned; this command only requests its presentation.
                self.panel(&json!({"presenter":"commands","title":"Commands"}));
                Vec::new()
            }
            _ => vec![self.request("command", json!({"text": value}))],
        }
    }

    fn exit_panel(&mut self) -> Vec<Value> {
        self.panel(&json!({"presenter":"selection","view_id":"exit.confirm","title":"Exit OwnPI?",
            "subtitle":"Stop active work, then check the backend recovery state.", "selected_row_id":"cancel", "footer_hint":"Enter confirm   Esc cancel", "rows":[
                {"id":"cancel","name":"Cancel","dismiss_on_select":false,"description":"Return to the chat.","action":{"method":"local","params":{"text":"/menu_back"}}},
                {"id":"exit","name":"Stop and exit","description":"Keep the canonical run records.","action":{"method":"shutdown","params":{}}}]}));
        Vec::new()
    }
}
