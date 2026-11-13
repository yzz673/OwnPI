//! Menu navigation is local view state; backend facts and execution stay in Bridge.
use super::*;

impl AppState {
    pub(super) fn panel(&mut self, value: &Value) {
        if value["source_epoch"]
            .as_u64()
            .is_some_and(|epoch| epoch != self.menu_epoch)
        {
            return;
        }
        let source = text(value, "source_view");
        // A delayed preview or save response must not reopen a dismissed menu.
        if !source.is_empty() && self.bottom_pane.menu_view_id().as_deref() != Some(source.as_str())
        {
            return;
        }
        let navigation = self
            .menu_navigation
            .take()
            .unwrap_or_else(|| text(value, "navigation"));
        let presenter = text(value, "presenter");
        match presenter.as_str() {
            "subagents" => {
                self.open_agent_popup(crate::multi_agents::PopupKind::Agents);
                return;
            }
            "requests" => {
                self.open_agent_popup(crate::multi_agents::PopupKind::Requests);
                return;
            }
            "editor" => {
                self.editor_path = Some(text(value, "path").into());
                self.edit_draft = false;
                return;
            }
            _ => {}
        }
        if navigation == "back" {
            self.bottom_pane.pop_menu_view();
        }
        if matches!(navigation.as_str(), "" | "root" | "push" | "back") {
            self.menu_epoch += 1;
        }
        let title = text(value, "title");
        if presenter == "commands" {
            let view =
                crate::bottom_pane::CommandBrowserViewState::new(self.command_catalog.clone());
            if navigation == "push" {
                self.bottom_pane.push_command_browser(view);
            } else {
                self.bottom_pane.set_command_browser(Some(view));
            }
            return;
        }
        if presenter != "selection" {
            let view = CommandDocumentViewState::new(
                text(value, "view_id"),
                title,
                String::new(),
                text(value, "body"),
                "tui".into(),
            );
            if navigation == "push" {
                self.bottom_pane.push_command_document(view);
            } else {
                self.bottom_pane.set_command_document(Some(view));
            }
            return;
        }
        let rows = value["rows"]
            .as_array()
            .map(|items| {
                items
                    .iter()
                    .map(|item| {
                        let description = text(item, "description");
                        SelectionViewRow {
                            resume_entry: item["resume_entry"].clone(),
                            id: text(item, "id"),
                            name: text(item, "name"),
                            description: description.clone(),
                            selected_description: item["selected_description"]
                                .as_str()
                                .unwrap_or(&description)
                                .to_string(),
                            is_current: item["is_current"] == true,
                            is_default: item["is_default"] == true,
                            is_disabled: item["disabled"] == true,
                            disabled_reason: item["disabled_reason"].as_str().map(str::to_string),
                            dismiss_on_select: item["dismiss_on_select"].as_bool().unwrap_or(true),
                            action: Some(SelectionAction {
                                kind: match text(&item["action"], "method").as_str() {
                                    "local" => "local",
                                    "draft" => "draft",
                                    _ => "request",
                                }
                                .into(),
                                text: text(&item["action"]["params"], "text"),
                                command_name: String::new(),
                                source: "tui".into(),
                                command_kind: "local".into(),
                                payload: item["action"].clone(),
                            }),
                        }
                    })
                    .collect()
            })
            .unwrap_or_default();
        let id = value["view_id"].as_str().unwrap_or(&title).to_string();
        let mut view = SelectionViewState::new(
            id,
            title,
            Some(text(value, "subtitle")),
            rows,
            value["selected_row_id"].as_str().map(str::to_string),
            Some(
                value["footer_hint"]
                    .as_str()
                    .unwrap_or("Enter confirm   Esc cancel")
                    .into(),
            ),
            None,
        );
        view.preserve_selected_description = value["preserve_selected_description"] == true;
        if value["resume_picker"] == true {
            view.enable_resume_browser();
        }
        match navigation.as_str() {
            "push" => self.bottom_pane.push_selection_view(view),
            "refresh" | "back" => {
                self.bottom_pane.refresh_selection_view(view);
            }
            _ => self.bottom_pane.set_selection_view(Some(view)),
        }
    }

    pub(super) fn refresh_parent_menu(&mut self) -> Vec<Value> {
        let Some(id) = self.bottom_pane.menu_view_id() else {
            return Vec::new();
        };
        if matches!(
            id.as_str(),
            "settings" | "permissions" | "compaction" | "extensions"
        ) {
            return vec![self.request(
                "command",
                json!({"text":format!("/{id}"),"menu_navigation":"refresh"}),
            )];
        }
        Vec::new()
    }

    pub(super) fn native_skill_action(&mut self, payload: Value) -> Vec<Value> {
        let native = payload["payload"].clone();
        if native["presenter"] != "skills" {
            return Vec::new();
        }
        vec![self.request("command", json!({"text":"/skills","skill_action":native}))]
    }
}
