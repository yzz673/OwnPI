use super::*;
use crate::multi_agents::SubagentBatchCell;
fn evt(kind: &str, data: Value) -> BridgeEvent {
    BridgeEvent {
        schema: crate::bridge::SCHEMA.into(),
        seq: 1,
        r#type: kind.into(),
        request_id: String::new(),
        identity: json!({}),
        data,
    }
}
fn reference(n: usize) -> Value {
    json!({"run_id":"run-a","conversation_id":format!("conversation-{n}"),"actor_id":if n==0{"core".to_string()}else{format!("child-{n}")},"actor_kind":if n==0{"core"}else{"subagent"},"parent_conversation_id":if n==0{""}else{"conversation-0"}})
}
fn item(id: &str, n: usize, content: &str) -> Value {
    json!({"schema_version":"ownpi.tui.item.v1","visibility":"user","item_id":id,"kind":"agent_message","status":"completed","agent_ref":reference(n),"scope":{"actor_kind":if n==0{"core"}else{"subagent"},"actor_id":if n==0{"core".to_string()}else{format!("child-{n}")}},"payload":{"text":content}})
}
fn data() -> Value {
    let tasks=(1..=2).map(|n|json!({"task_index":n-1,"task_name":format!("verify-{n}"),"task_message":format!("Task instructions {n}"),"child_id":format!("child-{n}"),"agent_ref":reference(n),"parent_ref":reference(0),"run_id":"run-a","stage":"literature_grounding","display_status":if n==1{"Running"}else{"Feedback submitted"},"status":if n==1{"running"}else{"completed"},"terminal_status":if n==1{""}else{"completed"},"completion_committed":n==2,"completion_content":if n==2{"Verified sources"}else{""},"reclaimed":n==2,"request_ids":[]})).collect::<Vec<_>>();
    json!({"items":[item("root",0,"ROOT_TEXT"),{"schema_version":"ownpi.tui.item.v1","visibility":"user","item_id":"batch","kind":"sub_agent_activity","tool_name":"run_subagents","status":"pending","agent_ref":reference(0),"payload":{"title":"Subagent tasks"},"raw_source":"EXACT_BATCH_SOURCE"},item("child1",1,"CHILD_ONE_TEXT"),item("child2",2,"CHILD_TWO_TEXT")],
        "subagents":[{"item_id":"batch","run_id":"run-a","stage":"literature_grounding","parent_ref":reference(0),"tasks":tasks}],"status":{"status":"in_progress","owner_active":true},"worker_active":true,"interactions":[]})
}
fn key(app: &mut AppState, code: KeyCode) -> Vec<Value> {
    app.handle_key(KeyEvent::new(code, KeyModifiers::NONE))
}
fn plain(app: &AppState) -> String {
    app.render_scrollback_tail(100)
        .iter()
        .map(|v| v.line.to_string())
        .collect::<Vec<_>>()
        .join("\n")
}
fn details(app: &AppState) -> String {
    app.agents
        .pager
        .render_lines(1000)
        .0
        .iter()
        .map(|v| v.to_string())
        .collect::<Vec<_>>()
        .join("\n")
}
fn open_first(app: &mut AppState) {
    key(app, KeyCode::F(2));
    key(app, KeyCode::Down);
    key(app, KeyCode::Enter);
}

#[test]
fn subagents_main_has_one_compact_batch_without_child_tool_or_message_interleaving() {
    let mut app = AppState::default();
    app.event(evt("snapshot", data()));
    let main = plain(&app);
    assert_eq!(main.matches("≡ 2 Agents").count(), 1, "{main}");
    assert!(
        main.contains("1 running") && main.contains("1 finished") && main.contains("ROOT_TEXT")
    );
    assert!(!main.contains("CHILD_ONE_TEXT") && !main.contains("CHILD_TWO_TEXT"));
    assert!(app.canonical.iter().any(|i| i["item_id"] == "child1"));
}
#[test]
fn subagents_read_only_navigation_keeps_main_draft_and_uses_scoped_messages() {
    let mut app = AppState::default();
    app.event(evt("snapshot", data()));
    app.bottom_pane.set_composer("中文 Main 草稿");
    let main = plain(&app);
    open_first(&mut app);
    assert!(details(&app).contains("CHILD_ONE_TEXT") && !details(&app).contains("CHILD_TWO_TEXT"));
    assert!(key(&mut app, KeyCode::Char('x')).is_empty());
    assert!(key(&mut app, KeyCode::Enter).is_empty());
    assert_eq!(app.bottom_pane.composer(), "中文 Main 草稿");
    app.handle_key(KeyEvent::new(KeyCode::Right, KeyModifiers::ALT));
    assert!(details(&app).contains("CHILD_TWO_TEXT"));
    key(&mut app, KeyCode::Esc);
    assert!(app.agents.active.is_none());
    assert_eq!(app.bottom_pane.composer(), "中文 Main 草稿");
    assert_eq!(plain(&app), main);
    app.handle_key(KeyEvent::new(KeyCode::Right, KeyModifiers::ALT));
    assert!(
        app.agents.active.is_none(),
        "Word motion must not switch while editing a draft"
    );
}
#[test]
fn subagents_popup_keeps_selected_identity_while_status_changes() {
    let mut app = AppState::default();
    app.event(evt("snapshot", data()));
    key(&mut app, KeyCode::F(2));
    key(&mut app, KeyCode::Down);
    let selected = app.agents.popup.as_ref().unwrap().selected.clone();
    let mut update = data();
    update["subagents"][0]["tasks"][0]["display_status"] = json!("Needs approval");
    app.event(evt("snapshot", update));
    assert_eq!(app.agents.popup.as_ref().unwrap().selected, selected);
    key(&mut app, KeyCode::Enter);
    assert_eq!(app.agents.active.as_deref(), Some("batch:0"));
}
#[test]
fn subagents_live_deltas_are_scoped_and_persistence_retires_the_original_stream() {
    let mut app = AppState::default();
    app.event(evt("snapshot", data()));
    open_first(&mut app);
    let stream = json!({"canonical_event_type":"assistant_text_delta","item_id":"stream-1","agent_ref":reference(1),"scope":{"actor_kind":"subagent","actor_id":"child-1"},"stream_key":["run-a","child-1",1,"stream-1"],"delta":"LIVE_CHILD_ONE"});
    app.event(evt("stream", stream));
    assert!(!plain(&app).contains("LIVE_CHILD_ONE"));
    assert!(details(&app).contains("LIVE_CHILD_ONE"));
    let mut update = data();
    let mut message = item("canonical-live", 1, "LIVE_CHILD_ONE");
    message["stream_key"] = json!(["run-a", "child-1", 1, "stream-1"]);
    update["items"].as_array_mut().unwrap().push(message);
    app.event(evt("snapshot", update));
    assert!(app.live.is_empty());
    assert_eq!(details(&app).matches("LIVE_CHILD_ONE").count(), 1);
}
#[test]
fn subagents_background_growth_preserves_reading_position_and_cross_run_identity() {
    let mut app = AppState::default();
    let mut initial = data();
    initial["items"].as_array_mut().unwrap().push(item(
        "long",
        1,
        &(0..60).map(|n| format!("line-{n}\n")).collect::<String>(),
    ));
    let mut wrong = item("other-run", 1, "OTHER_RUN_MUST_NOT_APPEAR");
    wrong["agent_ref"]["run_id"] = json!("other-run");
    initial["items"].as_array_mut().unwrap().push(wrong);
    app.event(evt("snapshot", initial.clone()));
    open_first(&mut app);
    key(&mut app, KeyCode::PageUp);
    let before = app.agents.pager.render_lines(12).0;
    initial["items"].as_array_mut().unwrap().push(item(
        "new-tail",
        1,
        "TAIL_ADD_ONE\nTAIL_ADD_TWO",
    ));
    app.event(evt("snapshot", initial));
    assert_eq!(app.agents.pager.render_lines(12).0, before);
    assert!(!details(&app).contains("OTHER_RUN_MUST_NOT_APPEAR"));
    app.select_agent("batch:1".into());
    app.select_agent("batch:0".into());
    assert!(app.agents.pager.scroll_for_test() > 0);
}
#[test]
fn subagents_pending_question_opens_automatically_without_switching_agent() {
    let mut app = AppState::default();
    app.event(evt("snapshot", data()));
    app.bottom_pane.set_composer("ROOT_DRAFT");
    open_first(&mut app);
    let active = app.agents.active.clone();
    let mut update = data();
    update["interactions"] = json!([{"kind":"human","run_id":"run-a","session_id":"question-child-two","actor_kind":"subagent","actor_id":"child-2","stage":"literature_grounding","questions":[{"question_id":"dataset","header":"数据集","question":"Which dataset?","required":true,"options":[{"label":"A","description":"A"}]}]}]);
    app.event(evt("snapshot", update));
    assert!(app.bottom_pane.human_question_bottom_view_is_visible());
    assert_eq!(app.agents.active, active);
    key(&mut app, KeyCode::F(4));
    key(&mut app, KeyCode::Enter);
    assert!(app.bottom_pane.human_question_bottom_view_is_visible());
    let commands = key(&mut app, KeyCode::Enter);
    assert_eq!(
        commands[0]["params"]["interaction_id"],
        "question-child-two"
    );
    assert_eq!(commands[0]["method"], "answer");
    assert!(key(&mut app, KeyCode::Enter).is_empty());
    assert_eq!(app.agents.active, active);
}
#[test]
fn subagents_escape_permission_is_local_and_denial_remains_explicit() {
    let mut app = AppState::default();
    let mut update = data();
    update["interactions"] = json!([{"kind":"permission","run_id":"run-a","session_id":"approval-2","actor_kind":"subagent","actor_id":"child-2","tool_name":"exec_command"}]);
    app.event(evt("snapshot", update));
    key(&mut app, KeyCode::F(4));
    key(&mut app, KeyCode::Enter);
    assert!(key(&mut app, KeyCode::Esc).is_empty());
    assert_eq!(app.agents.requests.len(), 1);
    key(&mut app, KeyCode::F(4));
    key(&mut app, KeyCode::Enter);
    let command = key(&mut app, KeyCode::Char('n'));
    assert_eq!(command[0]["params"]["interaction_id"], "approval-2");
    assert_eq!(command[0]["params"]["decision"], "deny");
}
#[test]
fn subagents_expired_question_keeps_draft_and_never_opens_the_next_request() {
    let mut app = AppState::default();
    let mut update = data();
    update["interactions"] = json!([{"kind":"human","session_id":"q","actor_kind":"subagent","actor_id":"child-1","questions":[{"question_id":"path","question":"路径","required":true,"options":[]}]}]);
    app.event(evt("snapshot", update));
    key(&mut app, KeyCode::F(4));
    key(&mut app, KeyCode::Enter);
    app.bottom_pane.insert_paste("保留草稿");
    app.event(evt("snapshot", data()));
    assert!(app.bottom_pane.pending_interaction().is_none());
    assert_eq!(app.drafts["q"].draft_for("path").free_text, "保留草稿");
    assert!(app.status.notice_text().unwrap().contains("draft retained"));
}
#[test]
fn subagents_stop_requires_whole_run_confirmation_and_exit_uses_existing_shutdown() {
    let mut app = AppState::default();
    app.event(evt("snapshot", data()));
    open_first(&mut app);
    assert!(app
        .handle_key(KeyEvent::new(KeyCode::Char('c'), KeyModifiers::CONTROL))
        .is_empty());
    let command = key(&mut app, KeyCode::Enter);
    assert_eq!(command[0]["method"], "stop");
    assert!(command[0]["params"].get("child_id").is_none());
    app.command("/exit");
    key(&mut app, KeyCode::Down);
    let command = key(&mut app, KeyCode::Enter);
    assert_eq!(command[0]["method"], "shutdown");
}
#[test]
fn subagents_raw_is_local_and_nested_breadcrumb_does_not_repeat_actor_on_every_cell() {
    let mut app = AppState::default();
    let mut update = data();
    let first = update["subagents"][0]["tasks"][0]["agent_ref"].clone();
    update["subagents"][0]["tasks"][1]["parent_ref"] = first;
    app.event(evt("snapshot", update));
    app.select_agent("batch:1".into());
    let title = app.agents.pager.title();
    assert!(title.contains("verify-1") && title.contains("verify-2"));
    app.command("/raw");
    assert!(app.agents.raw);
    assert!(!app.raw);
    app.select_agent("main".into());
    assert!(!app.raw);
}
#[test]
fn subagents_batch_caps_rows_keeps_original_source_and_handles_cjk_safely() {
    let mut batch = data()["subagents"][0].clone();
    let task = batch["tasks"][0].clone();
    batch["tasks"] = json!((0..10)
        .map(|n| {
            let mut t = task.clone();
            t["task_name"] = json!(format!("中文长任务名{n}"));
            t
        })
        .collect::<Vec<_>>());
    batch["tasks"][0]["task_name"] = json!("中文长任务名称".repeat(20));
    let source = data()["items"][1].clone();
    let cell = SubagentBatchCell::new(batch, source, ThemePalette::default());
    for width in [40, 80, 120] {
        let lines = cell.display_lines(width);
        assert!(lines.len() < 20);
        assert!(lines.iter().all(|line| line.width() <= width), "{lines:?}");
        assert!(lines.iter().any(|line| line.to_string().contains("4 more")));
        assert!(lines
            .iter()
            .any(|line| line.to_string().contains("Running")));
    }
    assert!(cell.raw_lines()[0]
        .to_string()
        .contains("EXACT_BATCH_SOURCE"));
}
#[test]
fn subagents_fullscreen_renders_cjk_and_requests_at_40_80_120_columns() {
    for width in [40, 80, 120] {
        let mut app = AppState::default();
        let mut update = data();
        update["subagents"][0]["tasks"][0]["task_name"] = json!("中文任务名称：跨被试脑信号解码");
        app.event(evt("snapshot", update));
        open_first(&mut app);
        app.note_layout_size(width, 30);
        app.refresh_agent_pager();
        let area = ratatui::layout::Rect::new(0, 0, width as u16, 30);
        let mut buffer = ratatui::buffer::Buffer::empty(area);
        crate::view::render_agent_view(&app, area, &mut buffer);
        assert!(buffer.content.iter().any(|cell| cell.symbol() == "中"));
        key(&mut app, KeyCode::F(4));
        crate::view::render_agent_view(&app, area, &mut buffer);
    }
}
#[test]
fn subagents_permissions_preserve_main_draft_and_request_ownership_checks_run() {
    let mut app = AppState::default();
    let mut update = data();
    let mut other = update["subagents"][0].clone();
    other["item_id"] = json!("other-batch");
    other["run_id"] = json!("run-b");
    for task in other["tasks"].as_array_mut().unwrap() {
        task["run_id"] = json!("run-b");
        task["task_name"] = json!("OTHER_OWNER");
    }
    update["subagents"].as_array_mut().unwrap().push(other);
    update["interactions"] = json!([{"kind":"permission","run_id":"run-a","session_id":"approval-1","actor_kind":"subagent","actor_id":"child-1","tool_name":"exec_command"}]);
    app.bottom_pane.set_composer("中文 Main 草稿");
    app.event(evt("snapshot", update));
    assert!(!app
        .agents
        .request_owner(&app.agents.requests[0])
        .contains("OTHER_OWNER"));
    let mut ambiguous = app.agents.requests[0].clone();
    ambiguous.as_object_mut().unwrap().remove("run_id");
    assert_eq!(app.agents.request_owner(&ambiguous), "Unverified agent");
    key(&mut app, KeyCode::F(4));
    key(&mut app, KeyCode::Enter);
    key(&mut app, KeyCode::Esc);
    assert_eq!(app.bottom_pane.composer(), "中文 Main 草稿");
    key(&mut app, KeyCode::F(4));
    key(&mut app, KeyCode::Enter);
    let commands = key(&mut app, KeyCode::Char('y'));
    assert_eq!(commands[0]["method"], "permission");
    app.event(evt(
        "interaction_accepted",
        json!({"interaction_id":"approval-1"}),
    ));
    assert_eq!(app.bottom_pane.composer(), "中文 Main 草稿");
}
#[test]
fn subagents_question_notes_escape_keeps_the_local_draft() {
    let mut app = AppState::default();
    let mut update = data();
    update["interactions"] = json!([{"kind":"human","session_id":"q-notes","actor_kind":"subagent","actor_id":"child-1","questions":[{"question_id":"path","question":"路径","required":true,"options":[{"label":"A","description":"A"}]}]}]);
    app.event(evt("snapshot", update));
    key(&mut app, KeyCode::F(4));
    key(&mut app, KeyCode::Enter);
    key(&mut app, KeyCode::Tab);
    app.bottom_pane.insert_paste("保留补充说明");
    assert!(key(&mut app, KeyCode::Esc).is_empty());
    assert!(app.bottom_pane.pending_interaction().is_none());
    assert!(app.drafts["q-notes"]
        .draft_for("path")
        .free_text
        .contains("保留补充说明"));
}

#[test]
fn subagents_reviewer_batch_keeps_one_rail_in_a_40_column_main() {
    let mut app = AppState::default();
    let mut update = data();
    let audit = json!({"audit_id":"review-one","run_id":"run-a","role":"reviewer","stage":"literature_grounding","attempt":1,"review_round":1});
    update["items"][1]["audit_context"] = audit.clone();
    update["subagents"][0]["audit_context"] = audit;
    app.event(evt("snapshot", update));
    let lines = app.render_scrollback_tail(39);
    assert!(lines
        .iter()
        .any(|v| v.line.to_string().starts_with("│   ≡ 2 Agents")));
    assert!(lines
        .iter()
        .any(|v| v.line.to_string().starts_with("╭─ AUDIT")));
}

fn reviewer_context() -> Value {
    json!({"audit_id":"review-one","run_id":"run-a","role":"reviewer","stage":"literature_grounding","attempt":1,"review_round":1})
}
fn assert_unbroken_audit(lines: &[String]) {
    let start = lines
        .iter()
        .position(|line| line.starts_with("╭─ AUDIT"))
        .expect("audit heading");
    let end = lines
        .iter()
        .enumerate()
        .skip(start + 1)
        .find(|(_, line)| line.starts_with("╰─"))
        .map_or(lines.len(), |(index, _)| index);
    for line in &lines[start + 1..end] {
        assert!(
            line.starts_with('│'),
            "Unrailed row inside Reviewer region: {line:?}\n{}",
            lines.join("\n")
        );
    }
}
#[test]
fn subagents_main_rebuild_uses_the_same_audit_spacing_as_incremental_history() {
    let mut update = data();
    for index in [0, 1] {
        update["items"][index]["audit_context"] = reviewer_context();
    }
    update["subagents"][0]["audit_context"] = reviewer_context();
    let mut after = item(
        "review-message",
        0,
        "核查后的评审摘要。 https://example.org/evidence",
    );
    after["audit_context"] = reviewer_context();
    update["items"].as_array_mut().unwrap().push(after);
    update["items"]
        .as_array_mut()
        .unwrap()
        .push(item("after-review", 0, "Main continues"));
    let mut app = AppState::default();
    app.event(evt("snapshot", update));
    for width in [39, 79, 119] {
        let rendered = app.render_scrollback_tail(width);
        assert_unbroken_audit(
            &rendered
                .iter()
                .map(|row| row.line.to_string())
                .collect::<Vec<_>>(),
        );
        assert!(rendered.iter().any(|row| row
            .hyperlinks
            .iter()
            .any(|link| link.destination == "https://example.org/evidence")));
    }
}
#[test]
fn subagents_child_reader_does_not_insert_plain_blank_rows_between_reviewer_cells() {
    let mut update = data();
    update["items"][2]["audit_context"] = reviewer_context();
    let mut extra = item("another-child-message", 1, "继续核查原始证据。");
    extra["audit_context"] = reviewer_context();
    update["items"].as_array_mut().unwrap().push(extra);
    let mut app = AppState::default();
    app.event(evt("snapshot", update));
    open_first(&mut app);
    for width in [40, 80, 120] {
        app.note_layout_size(width, 42);
        app.refresh_agent_pager();
        assert_unbroken_audit(
            &details(&app)
                .lines()
                .map(str::to_string)
                .collect::<Vec<_>>(),
        );
    }
}
#[test]
fn subagents_reviewer_live_join_has_no_unrailed_boundary_gap() {
    let mut update = data();
    for index in [0, 1] {
        update["items"][index]["audit_context"] = reviewer_context();
    }
    update["subagents"][0]["audit_context"] = reviewer_context();
    let mut app = AppState::default();
    app.event(evt("snapshot", update));
    app.event(evt("stream",json!({"canonical_event_type":"assistant_text_delta","item_id":"live-review","stream_key":["run-a","core",1,"live-review"],"agent_ref":reference(0),"audit_context":reviewer_context(),"delta":"正在核对交付报告。"})));
    let lines = crate::chatwidget::chat_history_lines(&app, 79);
    assert!(
        lines
            .first()
            .is_some_and(|line| line.to_string().starts_with('│')),
        "{lines:?}"
    );
}

#[test]
fn subagents_canonical_reviewer_snapshot_contract() {
    // The normal gate is self-contained. Acceptance can supply an actual
    // read-only Bridge snapshot without committing private research records.
    let snapshot = std::env::var("OWNPI_UI_ACCEPTANCE_SNAPSHOT")
        .ok()
        .map(|path| serde_json::from_str::<Value>(&std::fs::read_to_string(path).unwrap()).unwrap())
        .unwrap_or_else(|| {
            let mut value = data();
            for index in [0, 1] {
                value["items"][index]["audit_context"] = reviewer_context();
            }
            value["subagents"][0]["audit_context"] = reviewer_context();
            value
        });
    let mut app = AppState::default();
    app.event(evt("snapshot", snapshot));
    for width in [39, 79, 119] {
        let lines = app
            .render_scrollback_tail(width)
            .iter()
            .map(|row| row.line.to_string())
            .collect::<Vec<_>>();
        let mut open = false;
        let mut regions = 0;
        for line in &lines {
            if line.starts_with("╭─ AUDIT") {
                open = true;
                regions += 1;
            } else if line.starts_with("╰─") {
                open = false;
            } else if open {
                assert!(
                    line.starts_with('│') || line.starts_with('├'),
                    "Unrailed row: {line:?}"
                );
            }
        }
        assert!(regions > 0);
        let output = lines.join("\n");
        assert!(output.contains("Agents ("));
        for obsolete in [
            "⦁ Subagent tasks",
            "⦁ Child Admitted",
            "⦁ Child Execution Started",
            "feedback unavailable",
            "Subagent · review-perception-eeg",
        ] {
            assert!(
                !output.contains(obsolete),
                "Old Main presentation remained: {obsolete}"
            );
        }
        if let Ok(folder) = std::env::var("OWNPI_UI_ACCEPTANCE_OUTPUT") {
            std::fs::write(
                std::path::Path::new(&folder).join(format!("actual-main-{}.txt", width + 1)),
                output,
            )
            .unwrap();
        }
    }
    let selected = app
        .agents
        .tasks()
        .into_iter()
        .find(|(_, task)| text(task, "task_name") == "review-invasive-evidence")
        .or_else(|| app.agents.tasks().into_iter().next())
        .unwrap()
        .0;
    app.select_agent(selected);
    for width in [40, 80, 120] {
        app.note_layout_size(width, 42);
        app.refresh_agent_pager();
        let lines = app.agents.pager.render_lines(u16::MAX).0;
        for line in &lines {
            assert!(
                line.width() <= width,
                "Overlong child row at {width}: {} / {}",
                line.width(),
                line.to_string()
            );
        }
        if let Ok(folder) = std::env::var("OWNPI_UI_ACCEPTANCE_OUTPUT") {
            std::fs::write(
                std::path::Path::new(&folder).join(format!("actual-child-{width}.txt")),
                lines
                    .iter()
                    .map(|line| line.to_string())
                    .collect::<Vec<_>>()
                    .join("\n"),
            )
            .unwrap();
        }
    }
}

#[test]
fn subagents_screenshot_layout_has_status_rail_badges_and_scoped_current_actions() {
    let mut app = AppState::default();
    let mut snapshot = data();
    snapshot["subagents"][0]["tasks"][0]["activity"] = json!({"label":"Read","detail":"evaluation_protocol.md","item_id":"child-read","status":"running"});
    app.event(evt("snapshot", snapshot));
    let cell = app
        .history
        .iter()
        .find(|c| {
            c.display_lines(100)
                .iter()
                .any(|l| l.to_string().contains("Agents"))
        })
        .unwrap();
    for width in [40, 80, 120] {
        let lines = cell.display_lines(width);
        let text = lines
            .iter()
            .map(|l| l.to_string())
            .collect::<Vec<_>>()
            .join("\n");
        assert!(
            text.contains("2 Agents") && text.contains("1 running"),
            "{text}"
        );
        assert!(
            text.contains("Read") && text.contains("evaluation_protocol"),
            "{text}"
        );
        assert!(
            lines.iter().any(|line| line
                .spans
                .iter()
                .any(|span| span.content.contains("verify-1") && span.style.bg.is_some())),
            "Task name should have a badge"
        );
        assert!(lines.iter().all(|line| line.width() <= width));
        assert!(!text.contains("CHILD_ONE_TEXT") && !text.contains("Thinking"));
    }
}

#[test]
fn subagents_show_agent_navigation_and_actionable_requests_above_task_rows() {
    let mut batch = data()["subagents"][0].clone();
    batch["tasks"][0]["request_ids"] = json!(["approval-one", "question-two"]);
    let cell = SubagentBatchCell::new(batch, data()["items"][1].clone(), ThemePalette::default());
    for width in [40, 60, 80, 120] {
        let lines = cell.display_lines(width);
        let text = lines.iter().map(Line::to_string).collect::<Vec<_>>();
        let hints = text
            .iter()
            .position(|line| line.contains("F4 Requests (2)"))
            .expect("pending request navigation hint");
        let first_task = text
            .iter()
            .position(|line| line.contains("verify-1"))
            .expect("first task row");
        assert!(!text.iter().any(|line| line.contains("F2")), "{text:?}");
        assert!(
            hints < first_task,
            "Navigation belongs to the header: {text:?}"
        );
        assert_eq!(
            text.iter()
                .filter(|line| line.contains("F4 Requests"))
                .count(),
            1
        );
        assert!(lines.iter().all(|line| line.width() <= width));
        if width >= 80 {
            assert_eq!(
                hints, 0,
                "Wide header must keep both hints inline: {text:?}"
            );
        }
    }
}

#[test]
fn delivered_core_and_reviewer_batches_keep_the_same_task_rows_and_navigation() {
    let mut snapshot = data();
    for task in snapshot["subagents"][0]["tasks"].as_array_mut().unwrap() {
        task["terminal_status"] = json!("completed");
        task["completion_committed"] = json!(true);
        task["display_status"] = json!("Feedback submitted");
        task["completion_content"] = json!("COMPLETE_FEEDBACK_BODY");
    }
    let cell = SubagentBatchCell::new(
        snapshot["subagents"][0].clone(),
        snapshot["items"][1].clone(),
        ThemePalette::default(),
    );
    let mut reviewer_source = snapshot["items"][1].clone();
    reviewer_source["audit_context"] = reviewer_context();
    let reviewer = SubagentBatchCell::new(
        snapshot["subagents"][0].clone(),
        reviewer_source,
        ThemePalette::default(),
    );
    for width in [40, 80, 120] {
        let lines = cell.display_lines(width);
        let text = lines
            .iter()
            .map(Line::to_string)
            .collect::<Vec<_>>()
            .join("\n");
        assert!(lines.iter().all(|line| line.width() <= width), "{text}");
        assert!(text.contains("2 finished"));
        assert!(!text.contains("F2") && !text.contains("F4"));
        assert!(text.contains("verify-1") && text.contains("verify-2"));
        assert_eq!(text.matches("Feedback submitted").count(), 2);
        assert_eq!(text.matches("COMPLETE_FEEDBACK_BODY").count(), 2);
        let normalize = |lines: Vec<Line<'static>>| {
            lines
                .iter()
                .map(|line| line.to_string().trim_start_matches(['│', ' ']).to_string())
                .collect::<Vec<_>>()
        };
        let review_lines = reviewer.display_lines(width);
        assert!(review_lines.iter().all(|line| line.width() <= width));
        assert_eq!(normalize(lines), normalize(review_lines));
        let transcript = cell
            .transcript_lines(width)
            .iter()
            .map(Line::to_string)
            .collect::<Vec<_>>()
            .join("\n");
        assert!(transcript.contains("COMPLETE_FEEDBACK_BODY") && transcript.contains("verify-1"));
    }
    let mut app = AppState::default();
    app.event(evt("snapshot", snapshot));
    open_first(&mut app);
    assert!(details(&app).contains("CHILD_ONE_TEXT"));
}

#[test]
fn subagent_status_only_updates_redraw_core_and_reviewer_cards() {
    for reviewer in [false, true] {
        let mut snapshot = data();
        if reviewer {
            snapshot["items"][1]["audit_context"] = reviewer_context();
        }
        let mut app = AppState::default();
        app.event(evt("snapshot", snapshot.clone()));
        // The initial history has been drawn to the terminal.
        app.rebuild = false;
        app.pending_history.clear();
        for task in snapshot["subagents"][0]["tasks"].as_array_mut().unwrap() {
            task["terminal_status"] = json!("completed");
            task["completion_committed"] = json!(true);
            task["display_status"] = json!("Feedback submitted");
            task["completion_content"] = json!("FINAL_REVIEW_RESULT");
        }
        app.event(evt("snapshot", snapshot));
        assert!(
            app.rebuild,
            "A changed card must replace already drawn history"
        );
        assert!(plain(&app).contains("2 finished"));
        assert!(plain(&app).contains("FINAL_REVIEW_RESULT"));
    }
}

#[test]
fn terminal_exceptions_and_uncommitted_delivery_never_collapse_as_success() {
    for (terminal, display, committed) in [
        ("failed", "Failed", true),
        ("cancelled", "Cancelled", false),
        ("completed", "Execution completed", false),
    ] {
        let mut batch = data()["subagents"][0].clone();
        batch["tasks"][0]["terminal_status"] = json!(terminal);
        batch["tasks"][0]["display_status"] = json!(display);
        batch["tasks"][0]["completion_committed"] = json!(committed);
        let cell =
            SubagentBatchCell::new(batch, data()["items"][1].clone(), ThemePalette::default());
        for width in [40, 80, 120] {
            let lines = cell.display_lines(width);
            let text = lines
                .iter()
                .map(Line::to_string)
                .collect::<Vec<_>>()
                .join("\n");
            assert!(
                text.contains("verify-1") && text.contains(display),
                "{text}"
            );
            assert!(lines.iter().all(|line| line.width() <= width));
            assert!(!text.contains("F2") && !text.contains("Requests (0)"));
        }
    }
}

#[test]
fn subagents_approval_footer_matches_escape_that_only_closes() {
    for kind in ["", "exec_command", "file_edit", "provider_recovery"] {
        let mut app = AppState::default();
        let mut snapshot = data();
        snapshot["interactions"] = json!([{
            "kind": "permission", "session_id": "approval-one", "run_id": "run-a",
            "actor_kind": "subagent", "actor_id": "child-1", "tool_name": "exec_command",
            "approval_kind": kind,
        }]);
        app.event(evt("snapshot", snapshot));
        key(&mut app, KeyCode::F(4));
        key(&mut app, KeyCode::Enter);
        let area = ratatui::layout::Rect::new(0, 0, 120, 40);
        let mut buffer = ratatui::buffer::Buffer::empty(area);
        app.bottom_pane
            .render_bottom_view(area, &mut buffer, &app.theme);
        let text = buffer
            .content
            .iter()
            .map(|cell| cell.symbol())
            .collect::<String>()
            .to_ascii_lowercase();
        assert!(
            text.contains("esc close") || text.contains("esc dismiss"),
            "{kind}: {text}"
        );
        assert!(!text.contains("esc deny") && !text.contains("esc end task"));
        assert!(!text.contains("1-9 quick select"));
        assert!(key(&mut app, KeyCode::Esc).is_empty());
        assert_eq!(app.agents.requests.len(), 1);
        assert!(!app.bottom_pane.permission_overlay_is_visible());
    }
}

#[test]
fn reviewer_subagent_summary_has_one_audit_rail_and_keeps_main_rail() {
    for width in [40, 80, 116, 120] {
        let mut snapshot = data();
        snapshot["items"][1]["audit_context"] = json!({"audit_id":"review-one","run_id":"run-a","role":"reviewer","stage":"literature_grounding","attempt":1,"review_round":1});
        let mut app = AppState::default();
        app.event(evt("snapshot", snapshot));
        let lines = app.render_scrollback_tail(width);
        let heading = lines
            .iter()
            .find(|row| row.line.to_string().contains("≡ 2 Agents"))
            .unwrap()
            .line
            .to_string();
        assert!(heading.starts_with("│   ≡"), "{heading}");
        for row in &lines {
            assert!(!row.line.to_string().starts_with("│ │"), "{}", row.line);
            assert!(row.line.width() <= width, "{}", row.line);
        }
        let mut main = AppState::default();
        main.event(evt("snapshot", data()));
        let lines = main.render_scrollback_tail(width);
        assert!(lines
            .iter()
            .any(|row| row.line.to_string().starts_with("│ ≡ 2 Agents")));
    }
}

#[test]
fn automatic_requests_queue_without_interrupting_answers_or_reopening_dismissed() {
    let mut app = AppState::default();
    let q = json!({"kind":"human","session_id":"q-auto","questions":[{"question_id":"path","question":"Path?","required":true,"options":[]}]});
    let p = json!({"kind":"permission","session_id":"p-auto","tool_name":"exec_command"});
    app.sync_interactions(&[q.clone()]);
    app.bottom_pane.insert_paste("保留答案");
    app.sync_interactions(&[p.clone(), q.clone()]);
    assert_eq!(app.agents.selected_request.as_deref(), Some("q-auto"));
    key(&mut app, KeyCode::Esc);
    app.sync_interactions(&[q.clone()]);
    assert!(app.bottom_pane.pending_interaction().is_none());
    assert_eq!(app.drafts["q-auto"].draft_for("path").free_text, "保留答案");
    app.sync_interactions(&[q.clone(), p.clone()]);
    assert!(app.bottom_pane.permission_overlay_is_visible());
    app.event(evt(
        "interaction_accepted",
        json!({"interaction_id":"p-auto"}),
    ));
    app.sync_interactions(&[q, p]);
    assert!(app.bottom_pane.pending_interaction().is_none());
}
