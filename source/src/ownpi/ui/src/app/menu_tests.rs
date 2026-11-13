use super::*;

fn key(app: &mut AppState, code: KeyCode) -> Vec<Value> {
    app.handle_key(KeyEvent::new(code, KeyModifiers::NONE))
}
fn view(id: &str, rows: Value) -> Value {
    json!({"presenter":"selection","view_id":id,"title":id,"rows":rows,"footer_hint":"Enter open   Esc back"})
}
fn next(id: &str, text: &str) -> Value {
    json!({"id":id,"name":id,"dismiss_on_select":false,"action":{"method":"command","params":{"text":text,"menu_navigation":"push"}}})
}
fn response(app: &mut AppState, command: &Value, mut value: Value) {
    value["source_view"] = command["params"]["source_view"].clone();
    value["source_epoch"] = command["params"]["source_epoch"].clone();
    value["navigation"] = command["params"]["menu_navigation"].clone();
    app.panel(&value);
    app.result(command["request_id"].as_str().unwrap(), &json!({"ok":true}));
}

fn resume_view() -> Value {
    json!({"presenter":"selection","view_id":"resume","resume_picker":true,
        "title":"Resume a previous session","rows":[
        {"id":"run-one","name":"脑电 2026","resume_entry":{"same_project":true,"ready":true,
          "updated":20,"created":10,"updated_label":"10m ago","status":"stopped",
          "details_action":{"method":"command","params":{"text":"/run_actions run-one","menu_navigation":"push"}}},
         "action":{"method":"command","params":{"text":"/resume run-one","close_menus":true}}},
        {"id":"run-two","name":"Other project","resume_entry":{"same_project":false,"ready":true,"updated":10,"created":20},
         "action":{"method":"command","params":{"text":"/resume run-two","close_menus":true}}}]})
}

#[test]
fn resume_picker_search_digits_does_not_execute_and_enter_resumes_selected_run() {
    let mut app = AppState::default();
    app.panel(&resume_view());
    for c in "2026".chars() { assert!(key(&mut app, KeyCode::Char(c)).is_empty()); }
    assert_eq!(app.bottom_pane.selected_selection_row(), Some("脑电 2026"));
    let action = key(&mut app, KeyCode::Enter).remove(0);
    assert_eq!(action["params"]["text"], "/resume run-one");
    assert!(!app.bottom_pane.selection_view_is_visible());
}

#[test]
fn resume_picker_details_back_preserves_search_and_empty_filter_can_be_cleared() {
    let mut app = AppState::default();
    app.panel(&resume_view());
    key(&mut app, KeyCode::Char('脑'));
    let command = app.handle_key(KeyEvent::new(KeyCode::Char('o'), KeyModifiers::CONTROL)).remove(0);
    assert_eq!(command["params"]["text"], "/run_actions run-one");
    response(&mut app, &command, view("runs.actions", json!([next("history", "/inspect_run run-one")])));
    key(&mut app, KeyCode::Esc);
    assert_eq!(app.bottom_pane.selected_selection_row(), Some("脑电 2026"));
    key(&mut app, KeyCode::Char('x'));
    assert!(app.bottom_pane.selection_view_is_visible());
    assert!(key(&mut app, KeyCode::Enter).is_empty());
    key(&mut app, KeyCode::Esc);
    assert_eq!(app.bottom_pane.selected_selection_row(), Some("脑电 2026"));
}

#[test]
fn resume_picker_sort_shortcut_and_paste_do_not_touch_the_composer() {
    let mut app = AppState::default();
    app.panel(&resume_view());
    key(&mut app, KeyCode::Tab);
    app.handle_key(KeyEvent::new(KeyCode::Char('s'), KeyModifiers::CONTROL));
    key(&mut app, KeyCode::Home);
    assert_eq!(app.bottom_pane.selected_selection_row(), Some("Other project"));
    app.bottom_pane.insert_paste("脑电\n2026");
    assert_eq!(app.bottom_pane.selected_selection_row(), Some("脑电 2026"));
    assert!(app.bottom_pane.composer().is_empty());
}

#[test]
fn nested_models_keep_selection_and_escape_returns_one_level() {
    let mut app = AppState::default();
    app.panel(&view(
        "model.model",
        json!([next("quick", "/reasoning"), next("all", "/model_all")]),
    ));
    key(&mut app, KeyCode::Down);
    let command = key(&mut app, KeyCode::Enter).remove(0);
    response(
        &mut app,
        &command,
        view("model.all", json!([next("model-a", "/reasoning")])),
    );
    let command = key(&mut app, KeyCode::Enter).remove(0);
    response(
        &mut app,
        &command,
        view("model.reasoning", json!([next("advanced", "/advanced")])),
    );
    let command = key(&mut app, KeyCode::Enter).remove(0);
    response(
        &mut app,
        &command,
        view("model.advanced", json!([next("ultra", "/apply")])),
    );
    assert_eq!(app.bottom_pane.active_view_stack_state().1, 4);
    assert!(key(&mut app, KeyCode::Esc).is_empty());
    assert_eq!(
        app.bottom_pane.menu_view_id().as_deref(),
        Some("model.reasoning")
    );
    key(&mut app, KeyCode::Esc);
    key(&mut app, KeyCode::Esc);
    assert_eq!(app.bottom_pane.selected_selection_row(), Some("all"));
}

#[test]
fn late_preview_does_not_reopen_a_cancelled_and_reopened_menu() {
    let mut app = AppState::default();
    let root = view("model.model", json!([next("all", "/model_all")]));
    app.panel(&root);
    let command = key(&mut app, KeyCode::Enter).remove(0);
    key(&mut app, KeyCode::Esc);
    app.panel(&root);
    response(
        &mut app,
        &command,
        view("model.all", json!([next("a", "/reasoning")])),
    );
    assert_eq!(
        app.bottom_pane.menu_view_id().as_deref(),
        Some("model.model")
    );
    assert_eq!(app.bottom_pane.active_view_stack_state().1, 1);
}

#[test]
fn saving_compaction_pops_child_and_preserves_parent_selection() {
    let mut app = AppState::default();
    app.panel(&view(
        "compaction",
        json!([
            next("toggle", "/compaction"),
            next("limit", "/compaction_field")
        ]),
    ));
    key(&mut app, KeyCode::Down);
    let command = key(&mut app, KeyCode::Enter).remove(0);
    response(
        &mut app,
        &command,
        view("compaction.limit", json!([next("default", "/apply")])),
    );
    let source = app.bottom_pane.menu_view_id().unwrap();
    let mut update = view(
        "compaction",
        json!([
            next("toggle", "/compaction"),
            next("limit", "/compaction_field")
        ]),
    );
    update["source_view"] = json!(source);
    update["source_epoch"] = json!(app.menu_epoch);
    update["navigation"] = json!("back");
    app.panel(&update);
    assert_eq!(app.bottom_pane.active_view_stack_state().1, 1);
    assert_eq!(app.bottom_pane.selected_selection_row(), Some("limit"));
}

#[test]
fn configuration_cannot_submit_twice_and_rejection_keeps_menu() {
    let mut app = AppState::default();
    let row = json!({"id":"high","name":"High","dismiss_on_select":false,"action":{"method":"configure","params":{"field":"model_selection","value":{},"menu_commit":true}}});
    app.panel(&view("model.reasoning", json!([row])));
    let command = key(&mut app, KeyCode::Enter).remove(0);
    assert!(key(&mut app, KeyCode::Enter).is_empty());
    app.result(
        command["request_id"].as_str().unwrap(),
        &json!({"ok":false,"message":"Configuration was rejected"}),
    );
    assert_eq!(
        app.bottom_pane.menu_view_id().as_deref(),
        Some("model.reasoning")
    );
    assert!(app.status.notice_text().unwrap().contains("rejected"));
    assert_eq!(key(&mut app, KeyCode::Enter).len(), 1);
}

#[test]
fn preference_menus_select_the_persisted_value() {
    let mut app = AppState::default();
    app.preferences =
        json!({"raw":true,"vim":false,"interface_mode":"developer","notifications":false});
    for (command, label) in [
        ("/raw", "Raw"),
        ("/vim", "Disabled"),
        ("/mode", "Developer"),
        ("/notifications", "Disabled"),
    ] {
        app.command(command);
        assert_eq!(app.bottom_pane.selected_selection_row(), Some(label));
    }
}

#[test]
fn clear_and_exit_cancel_by_default_and_keep_composer_draft() {
    let mut app = AppState::default();
    app.bottom_pane.set_composer("保留这份草稿");
    for command in ["/clear", "/exit"] {
        app.command(command);
        assert_eq!(app.bottom_pane.selected_selection_row(), Some("Cancel"));
        assert!(key(&mut app, KeyCode::Enter).is_empty());
        assert_eq!(app.bottom_pane.composer(), "保留这份草稿");
        assert!(app.pending.is_empty());
    }
    app.command("/exit");
    key(&mut app, KeyCode::Down);
    assert_eq!(key(&mut app, KeyCode::Enter)[0]["method"], "shutdown");
}

#[test]
fn native_skills_wire_request_identity_and_rejection_keeps_search() {
    let mut app = AppState::default();
    app.skill_catalog(&json!([{"name":"alpha","display_name":"alpha","path":"/fixture/alpha/SKILL.md","enabled":true,"removable":true}]));
    app.panel(&view("skills", json!([next("manage", "/unused")])));
    app.command("/skills_manage");
    key(&mut app, KeyCode::Char('a'));
    let command = key(&mut app, KeyCode::Enter).remove(0);
    assert_eq!(command["method"], "command");
    assert_eq!(
        command["params"]["skill_action"]["arguments"]["path"],
        "/fixture/alpha/SKILL.md"
    );
    assert!(key(&mut app, KeyCode::Enter).is_empty());
    app.result(
        command["request_id"].as_str().unwrap(),
        &json!({"ok":false,"message":"Skill no longer visible"}),
    );
    let content = app.bottom_pane.skills_toggle_copy_source().unwrap();
    assert!(content.contains("[x] alpha"));
    key(&mut app, KeyCode::Esc);
    assert_eq!(app.bottom_pane.menu_view_id().as_deref(), Some("skills"));
}

#[test]
fn successful_commit_returns_to_composer_from_every_parent_depth() {
    for nested in [false, true] {
        let mut app = AppState::default();
        app.bottom_pane.set_composer("retained draft");
        if nested {
            app.panel(&view("settings", json!([next("model", "/model")])));
        }
        let row = json!({"id":"save","name":"Save","dismiss_on_select":false,
            "action":{"method":"configure","params":{"field":"permissions","value":"ask_for_approval","menu_commit":true}}});
        let mut menu = view("permissions", json!([row]));
        if nested {
            menu["navigation"] = json!("push");
        }
        app.panel(&menu);
        let request = key(&mut app, KeyCode::Enter).remove(0);
        assert!(app.bottom_pane.menu_view_id().is_some());
        app.result(request["request_id"].as_str().unwrap(), &json!({"ok":true}));
        assert!(app.bottom_pane.menu_view_id().is_none());
        assert_eq!(app.bottom_pane.active_view_stack_state().1, 0);
        assert_eq!(app.bottom_pane.composer(), "retained draft");
    }
}

#[test]
fn late_commit_does_not_close_a_new_menu_even_with_the_same_id() {
    let mut app = AppState::default();
    let menu = view(
        "permissions",
        json!([{"id":"save","name":"Save","dismiss_on_select":false,
        "action":{"method":"configure","params":{"field":"permissions","value":"full_access","menu_commit":true}}}]),
    );
    app.panel(&menu);
    let request = key(&mut app, KeyCode::Enter).remove(0);
    key(&mut app, KeyCode::Esc);
    app.panel(&menu);
    app.result(request["request_id"].as_str().unwrap(), &json!({"ok":true}));
    assert_eq!(
        app.bottom_pane.menu_view_id().as_deref(),
        Some("permissions")
    );
}

#[test]
fn local_preference_saves_once_and_closes_after_success() {
    let mut app = AppState::default();
    app.command("/notifications");
    let request = key(&mut app, KeyCode::Enter).remove(0);
    assert!(key(&mut app, KeyCode::Enter).is_empty());
    app.result(request["request_id"].as_str().unwrap(), &json!({"ok":true}));
    assert!(app.bottom_pane.menu_view_id().is_none());
}
