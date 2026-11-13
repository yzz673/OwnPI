use super::*;

fn event(kind: &str, data: Value) -> BridgeEvent {
    BridgeEvent {
        schema: crate::bridge::SCHEMA.into(),
        seq: 1,
        r#type: kind.into(),
        request_id: String::new(),
        identity: json!({}),
        data,
    }
}

fn snapshot(items: Vec<Value>, active: bool, interactions: Vec<Value>) -> Value {
    json!({"items": items, "status": {"status": if active {"in_progress"} else {"completed"},
        "owner_active": active}, "worker_active": active, "interactions": interactions})
}

fn user(id: &str, content: &str) -> Value {
    json!({"item_id": id, "kind": "user", "payload": {"text": content}})
}

#[test]
fn running_input_is_rejected_locally_and_draft_is_retained() {
    let mut app = AppState::default();
    app.active = true;
    assert!(app
        .input_result(InputResult::SubmittedText("保留这个草稿".into()))
        .is_empty());
    assert_eq!(app.bottom_pane.composer(), "保留这个草稿");
    assert!(app.pending.is_empty());
}

#[test]
fn snapshot_is_idempotent_and_does_not_duplicate_history() {
    let mut app = AppState::default();
    let data = snapshot(vec![user("one", "Hello")], true, vec![]);
    app.event(event("snapshot", data.clone()));
    let first = app.history.len();
    app.drain_pending_history_cells();
    app.event(event("snapshot", data));
    assert_eq!(app.history.len(), first);
    assert!(app.pending_history.is_empty());
}

#[test]
fn reviewed_answer_is_hidden_until_release_and_renders_once_live_and_cold() {
    let mut held = json!({"schema_version":"ownpi.tui.item.v1","visibility":"audit",
        "item_id":"candidate","kind":"agent_message","status":"completed",
        "scope":{"actor_kind":"core","actor_id":"core"},
        "payload":{"text":"REVIEWED_BODY","detail_only":true,"review_withheld":true}});
    let mut app = AppState::default();
    app.preferences["interface_mode"] = json!("developer");
    app.event(event(
        "snapshot",
        snapshot(vec![held.clone()], true, vec![]),
    ));
    assert!(!repaired_log_plain(&app, 100).contains("REVIEWED_BODY"));
    let mut answer = held.clone();
    answer["visibility"] = json!("user");
    answer["item_id"] = json!("candidate:reviewed-answer:hash");
    answer["projection_kind"] = json!("reviewed_answer");
    answer["payload"] = json!({"text":"REVIEWED_BODY","phase":"final",
        "message_purpose":"published_answer","answer_id":"answer:1",
        "publication_id":"publication:1","revision":1,"content_sha256":"fixture-digest"});
    held["payload"]["buffer_state"] = json!("released");
    let data = snapshot(vec![held, answer], true, vec![]);
    app.event(event("snapshot", data.clone()));
    assert_eq!(
        repaired_log_plain(&app, 100)
            .matches("REVIEWED_BODY")
            .count(),
        1
    );
    app.drain_pending_history_cells();
    app.event(event("snapshot", data.clone()));
    assert!(app.pending_history.is_empty());
    assert_eq!(
        repaired_log_plain(&app, 100)
            .matches("REVIEWED_BODY")
            .count(),
        1
    );
    let mut cold = AppState::default();
    cold.event(event("snapshot", data));
    assert_eq!(
        repaired_log_plain(&cold, 100)
            .matches("REVIEWED_BODY")
            .count(),
        1
    );
}

#[test]
fn legacy_missing_answer_status_is_visible_once_without_becoming_an_agent_answer() {
    let status = json!({"schema_version":"ownpi.tui.item.v1","visibility":"user",
        "item_id":"review:missing","kind":"answer_buffer_status","status":"unavailable",
        "payload":{"text":"前端交付提示：审核已通过，但没有独立的答案正文。"}});
    let data = snapshot(vec![status], false, vec![]);
    let mut app = AppState::default();
    app.event(event("snapshot", data.clone()));
    assert_eq!(
        repaired_log_plain(&app, 100)
            .matches("前端交付提示")
            .count(),
        1
    );
    app.drain_pending_history_cells();
    app.event(event("snapshot", data.clone()));
    assert!(app.pending_history.is_empty());
    let mut cold = AppState::default();
    cold.event(event("snapshot", data));
    assert_eq!(
        repaired_log_plain(&cold, 100)
            .matches("前端交付提示")
            .count(),
        1
    );
}

#[test]
fn published_answers_preserve_prior_requests_and_copy_bytes() {
    let bodies = [
        "# FIRST_BODY\r\n\r\n    exact code\n",
        "# SECOND_BODY\n\nDone.  \n",
    ];
    let answer = |index: usize| {
        json!({"schema_version":"ownpi.tui.item.v1","visibility":"user",
            "item_id":format!("published-{index}"),"kind":"agent_message","status":"completed",
            "projection_kind":"reviewed_answer",
            "payload":{"text":bodies[index],"phase":"final","message_purpose":"published_answer",
                "answer_id":format!("answer:{index}"),"publication_id":format!("publication:{index}"),
                "revision":1,"content_sha256":"fixture-digest"}})
    };
    let held = json!({"schema_version":"ownpi.tui.item.v1","visibility":"audit",
        "item_id":"pending","kind":"agent_message","status":"completed",
        "payload":{"text":"SECOND_BODY","detail_only":true,"review_withheld":true}});
    let mut app = AppState::default();
    app.event(event(
        "snapshot",
        snapshot(vec![answer(0), held], true, vec![]),
    ));
    assert!(repaired_log_plain(&app, 100).contains("FIRST_BODY"));
    assert!(!repaired_log_plain(&app, 100).contains("SECOND_BODY"));
    let data = snapshot(vec![answer(0), answer(1)], false, vec![]);
    app.event(event("snapshot", data.clone()));
    app.drain_pending_history_cells();
    app.event(event("snapshot", data.clone()));
    assert!(app.pending_history.is_empty());
    let mut cold = AppState::default();
    cold.event(event("snapshot", data));
    for state in [&app, &cold] {
        let copies: Vec<_> = state
            .history
            .iter()
            .filter_map(|cell| cell.copy_text())
            .collect();
        for body in bodies {
            assert_eq!(
                copies
                    .iter()
                    .filter(|copy| copy.as_bytes() == body.as_bytes())
                    .count(),
                1
            );
        }
        let log = repaired_log_plain(state, 100);
        assert_eq!(log.matches("FIRST_BODY").count(), 1);
        assert_eq!(log.matches("SECOND_BODY").count(), 1);
    }
}

#[test]
fn reviewed_markdown_parts_keep_copy_bytes_and_separate_source_links() {
    let bodies = [
        "    indented\r\n\r\n```xml\n<answer>example</answer>\n",
        "# Conclusion\n\n[PDF](/tmp/paper.pdf)  \nDone.\n",
        "Sources:\n\n- [Evidence](https://example.org/paper?a=1&b=2)",
    ];
    let items = bodies
        .iter()
        .enumerate()
        .map(|(index, body)| {
            json!({
                "schema_version":"ownpi.tui.item.v1", "visibility":"user", "status":"completed",
                "item_id":format!("released-{index}"), "kind":"agent_message",
                "payload":{"text":body,"phase":"final","source_list":index == 2}
            })
        })
        .collect();
    let mut app = AppState::default();
    app.event(event("snapshot", snapshot(items, false, vec![])));
    let copies: Vec<_> = app
        .history
        .iter()
        .filter_map(|cell| cell.copy_text())
        .collect();
    for body in bodies {
        assert!(copies.iter().any(|copy| copy.as_bytes() == body.as_bytes()));
    }
    let destinations: Vec<_> = app
        .history
        .iter()
        .flat_map(|cell| cell.display_hyperlink_lines(100))
        .flat_map(|line| line.hyperlinks.into_iter().map(|link| link.destination))
        .collect();
    assert!(destinations
        .iter()
        .any(|url| url == "https://example.org/paper?a=1&b=2"));
    assert!(destinations
        .iter()
        .any(|url| url.ends_with("/tmp/paper.pdf")));
    let rendered = repaired_log_plain(&app, 100);
    assert!(rendered.contains("<answer>example</answer>"));
    assert!(rendered.contains("Conclusion"));
}

#[test]
fn terminal_duration_uses_the_recorded_run_and_survives_cold_load() {
    for warm in [false, true] {
        let mut app = AppState::default();
        if warm {
            app.status
                .start_working("Working".into(), String::new(), 900_000, true);
        }
        let data = json!({"items":[], "status":{"status":"failed", "run_id":"run-one"},
                          "timing":{"run_id":"run-one", "elapsed_ms":805_997}});
        app.event(event("snapshot", data.clone()));
        assert_eq!(app.status.elapsed_ms(), 805_997);
        assert_eq!(app.status.elapsed_label(), "13m 25s");
        app.event(event("snapshot", data));
        assert_eq!(app.status.elapsed_ms(), 805_997);
    }
}

#[test]
fn stale_timing_from_another_run_does_not_replace_the_active_clock() {
    let mut app = AppState::default();
    app.status
        .start_working("Working".into(), String::new(), 60_000, true);
    app.event(event(
        "snapshot",
        json!({"items":[], "status":{"status":"failed", "run_id":"new"},
                          "timing":{"run_id":"old", "elapsed_ms":1}}),
    ));
    assert!(app.status.elapsed_ms() >= 60_000);
}

#[test]
fn canonical_tool_update_rebuilds_existing_cell_instead_of_appending() {
    let mut app = AppState::default();
    let mut tool = json!({"schema_version": "ownpi.tui.item.v1", "visibility": "user",
        "item_id": "source-call", "kind": "command_execution", "tool_name": "bash",
        "call_id": "call-one", "status": "pending", "payload": {"command": "echo hello"}});
    app.event(event(
        "snapshot",
        snapshot(vec![user("request", "Run"), tool.clone()], true, vec![]),
    ));
    tool["status"] = json!("completed");
    tool["payload"]["output"] = json!(["hello"]);
    app.event(event(
        "snapshot",
        snapshot(vec![user("request", "Run"), tool], true, vec![]),
    ));
    assert_eq!(app.history.len(), 3);
    assert!(app.rebuild);
}

#[test]
fn transport_request_ids_are_unique_and_versioned() {
    let mut app = AppState::default();
    let first = app.request("inspect", json!({}));
    let second = app.request("inspect", json!({}));
    assert_ne!(first["request_id"], second["request_id"]);
    assert_eq!(first["schema"], "ownpi.tui.v1");
}

#[test]
fn question_answer_uses_backend_question_ids() {
    let mut app = AppState::default();
    let pending = json!({"kind": "human", "session_id": "q-session", "questions": [{
        "question_id": "dataset_path", "header": "Dataset", "question": "Which dataset?",
        "required": true, "multi_select": false, "options": [
            {"label": "A", "description": "First"}, {"label": "B", "description": "Second"}]}]});
    app.event(event("snapshot", snapshot(vec![], false, vec![pending])));
    open_first_request(&mut app);
    assert!(app.bottom_pane.human_question_bottom_view_is_visible());
    let commands = app.handle_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0]["method"], "answer");
    assert_eq!(commands[0]["params"]["interaction_id"], "q-session");
    assert_eq!(
        commands[0]["params"]["answers"]["dataset_path"]["selected_labels"],
        json!(["A"])
    );
}

#[test]
fn permission_escape_closes_without_answering_the_current_request() {
    let mut app = AppState::default();
    let pending = json!({"kind": "permission", "session_id": "approval-one", "tool_name": "bash", "command": "touch file"});
    app.event(event("snapshot", snapshot(vec![], true, vec![pending])));
    open_first_request(&mut app);
    let commands = app.handle_key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE));
    assert!(commands.is_empty());
    assert!(!app.bottom_pane.permission_overlay_is_visible());
    assert_eq!(app.agents.requests.len(), 1);
}

#[test]
fn denied_submission_keeps_the_permission_panel() {
    let mut app = AppState::default();
    app.event(event(
        "snapshot",
        snapshot(
            vec![],
            true,
            vec![json!({"kind": "permission", "session_id": "approval-one"})],
        ),
    ));
    open_first_request(&mut app);
    app.result(
        "request",
        &json!({"ok": false, "message": "stale", "disposition": "rejected"}),
    );
    assert!(app.bottom_pane.permission_overlay_is_visible());
    assert!(!app.bottom_pane.interaction_submission_requested());
}

#[test]
fn pending_shutdown_does_not_exit() {
    let mut app = AppState::default();
    let request = app.request("shutdown", json!({}));
    app.result(
        request["request_id"].as_str().unwrap(),
        &json!({"ok": true, "disposition": "pending"}),
    );
    assert!(!app.should_quit);
    app.result(
        request["request_id"].as_str().unwrap(),
        &json!({"ok": true, "shutdown": true, "resume_command": "ownpi-tui --resume r"}),
    );
    assert!(app.should_quit);
    assert!(app
        .exit_summary
        .as_ref()
        .unwrap()
        .render()
        .contains("ownpi-tui --resume r"));
}

#[test]
fn backend_rejection_restores_unsent_request() {
    let mut app = AppState::default();
    let request = app.request("submit", json!({"text": "保留请求"}));
    app.result(
        request["request_id"].as_str().unwrap(),
        &json!({"ok": false, "message": "busy", "disposition": "rejected"}),
    );
    assert_eq!(app.bottom_pane.composer(), "保留请求");
}

#[test]
fn rapid_snapshots_keep_all_unflushed_history() {
    let mut app = AppState::default();
    app.event(event(
        "snapshot",
        snapshot(vec![user("one", "First")], true, vec![]),
    ));
    app.event(event(
        "snapshot",
        snapshot(
            vec![user("one", "First"), user("two", "Second")],
            true,
            vec![],
        ),
    ));
    assert!(app.rebuild || app.pending_history.len() == app.history.len());
}

#[test]
fn submitting_clears_the_composer_without_queueing_the_request() {
    let mut app = AppState::default();
    app.bottom_pane.set_composer("提交后清空");
    let commands = app.input_result(InputResult::SubmittedText("提交后清空".into()));
    assert_eq!(commands.len(), 1);
    assert!(app.bottom_pane.composer().is_empty());
}

#[test]
fn persisted_provider_identity_retires_live_text_without_content_matching() {
    let mut app = AppState::default();
    app.stream(&json!({"canonical_event_type":"assistant_text_delta","item_id":"provider-id","stream_key":["r","core",1,"provider-id"],"delta":"Preview"}));
    app.event(event("snapshot", snapshot(vec![json!({"kind": "agent_message", "status":"completed", "item_id": "one", "provider_item_id": "provider-id", "stream_key": ["r", "core", 1, "provider-id"], "payload": {"text": "Different text"}})], true, vec![])));
    assert!(app.live.is_empty());
}

#[test]
fn raw_reasoning_is_not_promoted_to_a_summary() {
    let value = json!({"schema_version": "ownpi.tui.item.v1", "visibility": "user", "item_id": "r",
        "kind": "reasoning", "status": "completed", "payload": {"text": "raw-only", "content": ["raw-only"]}});
    let DisplayItem::Reasoning(_, payload) = DisplayItem::parse_value(&value).unwrap() else {
        panic!()
    };
    assert!(payload.summary.is_empty());
}

#[test]
fn permission_keyboard_allows_and_denies_once_without_repeating() {
    for (down, expected) in [(0, "approve"), (1, "deny_command"), (2, "deny")] {
        let mut app = AppState::default();
        app.event(event(
            "snapshot",
            snapshot(
                vec![],
                true,
                vec![json!({"kind":"permission", "session_id":"p"})],
            ),
        ));
        open_first_request(&mut app);
        for _ in 0..down {
            app.handle_key(KeyEvent::new(KeyCode::Down, KeyModifiers::NONE));
        }
        let commands = app.handle_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
        assert_eq!(commands[0]["params"]["decision"], expected);
        assert_eq!(commands[0]["params"]["scope"], "once");
        assert!(app
            .handle_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE))
            .is_empty());
    }
}

#[test]
fn stream_discard_only_withdraws_the_affected_actor() {
    let mut app = AppState::default();
    for actor in ["core", "reviewer"] {
        app.event(event("stream", json!({"canonical_event_type":"assistant_text_delta", "item_id":"same-provider-id",
            "response_id":format!("response-{actor}"),
            "presentation_class":"user_text", "delta":"hello", "stream_key":["run",actor,1,"same-provider-id"]})));
    }
    assert_eq!(app.live.len(), 2);
    app.event(event("stream", json!({"canonical_event_type":"attempt_discarded", "response_id":"response-reviewer", "stream_key":["run","reviewer",1,""]})));
    assert_eq!(app.live.len(), 2);
    let rendered = app
        .active_agent_stream_lines(100)
        .iter()
        .map(crate::wrapping::line_plain_text)
        .collect::<Vec<_>>()
        .join("\n");
    assert_eq!(rendered.matches("hello").count(), 1);
    assert!(rendered.contains("Output withdrawn"));
}

#[test]
fn canonical_text_streams_even_with_generic_protocol_label_but_reasoning_stays_private() {
    let mut app = AppState::default();
    for (kind, id, delta) in [
        ("assistant_text_delta", "visible", "中文流式内容"),
        ("assistant_reasoning_delta", "private", "private reasoning"),
    ] {
        app.event(event(
            "stream",
            json!({"canonical_event_type":kind, "item_id":id,
            "presentation_class":"protocol", "delta":delta, "stream_key":["run","core",1,id]}),
        ));
    }
    let visible = app
        .active_agent_stream_lines(40)
        .iter()
        .map(|line| {
            line.spans
                .iter()
                .map(|span| span.content.as_ref())
                .collect::<String>()
        })
        .collect::<Vec<_>>()
        .join("\n");
    assert!(visible.contains("中文流式内容"));
    assert!(!visible.contains("private reasoning"));
}

#[test]
fn execution_failure_does_not_restore_a_submitted_request() {
    let mut app = AppState::default();
    let command = app
        .input_result(InputResult::SubmittedText("已经执行".into()))
        .remove(0);
    app.result(
        command["request_id"].as_str().unwrap(),
        &json!({"ok":false,"disposition":"completed","message":"provider failed"}),
    );
    assert!(app.bottom_pane.composer().is_empty());
    app.event(event(
        "snapshot",
        json!({"items":[], "status":{"status":"failed"}, "interactions":[]}),
    ));
    assert_eq!(app.activity_state_label(), Some("failed"));
    assert_eq!(app.status.detail, "provider failed");
}

#[test]
fn cancelling_question_retains_draft_and_status_reopens_the_backend_request() {
    let mut app = AppState::default();
    let data = snapshot(
        vec![],
        false,
        vec![json!({"kind":"human", "session_id":"q", "questions":[{
        "question_id":"path", "header":"路径", "question":"输入路径", "required":true, "options":[]} ]})],
    );
    app.event(event("snapshot", data.clone()));
    open_first_request(&mut app);
    app.bottom_pane.insert_paste("中文草稿");
    assert!(app
        .handle_key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE))
        .is_empty());
    assert!(app.dismissed.contains("q"));
    assert_eq!(app.drafts["q"].draft_for("path").free_text, "中文草稿");
    let commands = app.command("/status");
    assert_eq!(commands[0]["method"], "inspect");
    app.event(event("snapshot", data));
    open_first_request(&mut app);
    assert!(app.bottom_pane.human_question_bottom_view_is_visible());
    assert!(app.drafts.is_empty());
}

#[test]
fn slash_submission_clears_its_text_before_the_next_command() {
    let mut app = AppState::default();
    app.bottom_pane.set_composer("/status");
    let command = crate::bottom_pane::ServiceTierCommand {
        id: "status".into(),
        name: "/status".into(),
        description: String::new(),
        source: "tui".into(),
        kind: "local".into(),
        owner: "tui".into(),
        panel_id: String::new(),
        aliases: vec![],
        argument_hint: String::new(),
        disabled: false,
        disabled_reason: String::new(),
        shortcut: String::new(),
        tab: String::new(),
        side_content: String::new(),
    };
    let result = app.input_result(InputResult::Command(
        crate::bottom_pane::SlashCommandSubmission {
            text: "/status".into(),
            command: crate::bottom_pane::SlashCommandItem::ServiceTier(command),
            args: None,
        },
    ));
    assert_eq!(result[0]["method"], "command");
    assert!(app.bottom_pane.composer().is_empty());
}

#[test]
fn native_tool_surfaces_keep_old_read_and_diff_granularity() {
    let mut app = AppState::default();
    let read = json!({"schema_version":"ownpi.tui.item.v1", "visibility":"user", "item_id":"read",
        "kind":"command_execution", "action":"read", "tool_name":"file_reader", "status":"completed",
        "raw_source":"original receipt and internal fingerprint",
        "payload":{"target":"run/research_state.md", "display_target":"run/research_state.md", "aggregation_key":"read:state",
            "read_windows":[{"start_line":1,"end_line":20,"exact":true}], "output":[], "transcript":"complete source file content"}});
    let patch = json!({"schema_version":"ownpi.tui.item.v1", "visibility":"user", "item_id":"patch",
        "kind":"file_change", "tool_name":"patch_apply", "status":"completed", "payload":{
            "changes":[{"path":"report.md","kind":"add","content":"# 中文标题\n实际内容", "body_complete":true}]}});
    app.event(event(
        "snapshot",
        snapshot(vec![read, patch], false, vec![]),
    ));
    let plain = app
        .history
        .iter()
        .flat_map(|cell| cell.display_lines(80))
        .map(|line| {
            line.spans
                .iter()
                .map(|span| span.content.as_ref())
                .collect::<String>()
        })
        .collect::<Vec<_>>()
        .join("\n");
    assert!(plain.contains("Explored"), "{plain}");
    assert!(plain.contains("Read run/research_state.md"), "{plain}");
    assert!(plain.contains("Lines 1-20"), "{plain}");
    assert!(
        plain.contains("report.md") && plain.contains("中文标题"),
        "{plain}"
    );
    assert!(!plain.contains("fingerprint") && !plain.contains("OwnPI.file_reader"));
    assert!(app.history[1]
        .raw_lines()
        .iter()
        .any(|line| line.to_string().contains("original receipt")));
    let patch_transcript = app.history[2]
        .transcript_lines(80)
        .iter()
        .map(|line| line.to_string())
        .collect::<Vec<_>>()
        .join("\n");
    assert!(
        patch_transcript.contains("中文标题") && patch_transcript.contains("实际内容"),
        "{patch_transcript}"
    );
}

#[test]
fn reviewer_messages_and_tools_stay_inside_one_source_bound_audit_region() {
    let audit = json!({"audit_id":"review-one","run_id":"run-one","role":"reviewer",
        "stage":"output_synthesis","attempt":1,"review_round":1});
    let message = json!({"schema_version":"ownpi.tui.item.v1", "visibility":"user", "item_id":"reviewer-message",
        "kind":"agent_message", "status":"completed", "audit_context":audit, "scope":{"actor_id":"reviewer"},
        "payload":{"text":"核对阶段产物。"}});
    let read = json!({"schema_version":"ownpi.tui.item.v1", "visibility":"user", "item_id":"reviewer-read",
        "kind":"command_execution", "action":"read", "tool_name":"file_reader", "status":"completed",
        "audit_context":audit, "payload":{"target":"report.md", "aggregation_key":"read:report"}});
    let review = json!({"schema_version":"ownpi.tui.item.v1", "visibility":"user", "item_id":"reviewer-submitted",
        "kind":"review_cycle", "status":"completed", "audit_context":audit,
        "payload":{"review_kind":"reviewer", "stage":"output_synthesis", "attempt":1,"round":1,
            "phase":"completed", "outcome":"submitted","summary":"No findings."}});
    let mut app = AppState::default();
    app.event(event(
        "snapshot",
        snapshot(vec![message, read, review], false, vec![]),
    ));
    let plain = app
        .history
        .iter()
        .flat_map(|cell| cell.display_lines(80))
        .map(|line| line.to_string())
        .collect::<Vec<_>>()
        .join("\n");
    assert_eq!(plain.matches("AUDIT / REVIEWER").count(), 1, "{plain}");
    assert!(
        plain.contains("核对阶段产物") && plain.contains("Read report.md"),
        "{plain}"
    );
    assert!(
        plain.contains("SUBMITTED") && !plain.contains("PASSED"),
        "{plain}"
    );
}

#[test]
fn migrated_pager_refreshes_canonical_live_text_and_width_without_losing_reader_position() {
    let mut app = AppState::default();
    app.event(event(
        "snapshot",
        snapshot(vec![user("one", "已有历史")], true, vec![]),
    ));
    app.handle_key(KeyEvent::new(KeyCode::Char('t'), KeyModifiers::CONTROL));
    app.stream(
        &json!({"canonical_event_type":"assistant_text_delta","item_id":"m",
        "stream_key":["run","core",1,"m"],"delta":"中文实时输出"}),
    );
    app.refresh_pager();
    assert!(app
        .pager
        .as_ref()
        .unwrap()
        .render_lines(100)
        .0
        .iter()
        .any(|line| line.to_string().contains("中文实时输出")));
    app.event(event(
        "snapshot",
        snapshot(
            vec![user("one", "已有历史"), user("two", "新增记录")],
            true,
            vec![],
        ),
    ));
    assert!(app
        .pager
        .as_ref()
        .unwrap()
        .render_lines(100)
        .0
        .iter()
        .any(|line| line.to_string().contains("新增记录")));
    app.note_layout_size(40, 20);
    assert_eq!(app.pager.as_ref().unwrap().width, 40);
}

#[test]
fn migrated_context_count_uses_provider_fields_and_unknown_never_equals_zero() {
    let mut app = AppState::default();
    app.usage.limit = Some(100);
    assert_eq!(app.usage.percent_left(), None);
    let mut data = snapshot(vec![], true, vec![]);
    data["usage"] =
        json!({"provider_prompt_tokens":80,"context_window_limit":100,"is_estimate":false});
    app.event(event("snapshot", data));
    assert_eq!(app.usage.percent_left(), Some(20));
    assert!(!app.usage.is_estimate);
    app.event(event("snapshot", snapshot(vec![], true, vec![])));
    assert_eq!(app.usage.percent_left(), None);
}

#[test]
fn migrated_approval_preserves_patch_context_without_details_shortcut() {
    let mut app = AppState::default();
    app.event(event("snapshot",snapshot(vec![],true,vec![json!({"kind":"permission","session_id":"original",
        "approval_kind":"apply_patch","permission_rule":"workspace_write","file_changes":[{"path":"a.py","change_kind":"modify"}],
        "body_markdown":"```diff\n-old\n+new\n```"})])));
    open_first_request(&mut app);
    let Some(ActiveInteractionRef::Permission(value)) = app.bottom_pane.pending_interaction()
    else {
        panic!()
    };
    assert_eq!(value.approval_kind, "apply_patch");
    assert_eq!(value.file_changes.len(), 1);
    app.handle_key(KeyEvent::new(KeyCode::Char('f'), KeyModifiers::NONE));
    assert!(app.pager.is_none());
    assert!(app.bottom_pane.permission_overlay_is_visible());
    let answer = app.handle_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
    assert_eq!(answer[0]["params"]["interaction_id"], "original");
}

#[test]
fn migrated_saved_prompts_are_available_in_composer_and_vim_escape_is_local() {
    let mut app = AppState::default();
    app.event(event(
        "snapshot",
        snapshot(vec![user("one", "历史输入")], false, vec![]),
    ));
    app.handle_key(KeyEvent::new(KeyCode::Up, KeyModifiers::NONE));
    assert_eq!(app.bottom_pane.composer(), "历史输入");
    app.bottom_pane.set_vim_enabled(true);
    app.handle_key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE));
    assert!(!app.bottom_pane.vim_insert_mode_active());
    assert!(!app.bottom_pane.selection_view_is_visible());
}

#[test]
fn migrated_settings_current_choice_and_skills_catalog_reach_production_widgets() {
    let mut app = AppState::default();
    app.event(event(
        "bootstrap",
        json!({"config":{},"commands":[],"skills":[{
        "name":"demo-skill","display_name":"demo-skill","description":"demo","enabled":true}]}),
    ));
    app.bottom_pane.set_composer("$demo");
    assert_eq!(
        app.bottom_pane.selected_skill_name(),
        Some("demo-skill".into())
    );
    app.bottom_pane.clear_composer();
    app.panel(&json!({"presenter":"selection","title":"Theme","rows":[
        {"id":"auto","name":"Auto"},{"id":"dark","name":"Dark","is_current":true}]}));
    assert_eq!(app.bottom_pane.selected_selection_row(), Some("Dark"));
}

#[test]
fn migrated_review_choices_start_only_selected_scope_and_running_input_is_not_queued() {
    let mut app = AppState::default();
    app.command("/review");
    assert_eq!(
        app.bottom_pane.selected_selection_row(),
        Some("Working tree")
    );
    app.handle_key(KeyEvent::new(KeyCode::Down, KeyModifiers::NONE));
    assert_eq!(
        app.bottom_pane.selected_selection_row(),
        Some("Staged changes")
    );
    let command = app.handle_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
    assert_eq!(command[0]["method"], "submit");
    assert!(command[0]["params"]["text"]
        .as_str()
        .unwrap()
        .contains("git diff --cached"));
    assert!(app
        .input_result(InputResult::SubmittedText("later".into()))
        .is_empty());
    assert_eq!(app.bottom_pane.composer(), "later");
}

#[test]
fn migrated_terminal_notifications_require_focus_loss_and_escape_bytes_are_removed() {
    let mut app = AppState::default();
    app.notify("Permission required");
    assert!(app.terminal_effects.is_empty());
    app.terminal_focused = false;
    app.notify("Permission\x1b]bad\x07");
    assert_eq!(app.terminal_effects.len(), 1);
    assert!(!super::surfaces::terminal_text("name\x1b\n").contains('\x1b'));
}

#[test]
fn migrated_raw_preference_is_applied_only_after_backend_confirms_it() {
    let mut app = AppState::default();
    let outgoing = app.command("/raw_apply true");
    assert_eq!(outgoing[0]["method"], "configure");
    assert!(!app.raw);
    app.event(event(
        "configuration",
        json!({"config":{"preferences":{"raw":true,"vim":false,"syntax_theme":"default"}}}),
    ));
    assert!(app.raw);
}

fn command_approval() -> Value {
    json!({"kind":"permission","session_id":"one","actor_kind":"core","stage":"literature_grounding",
        "approval_kind":"command","command":"pwd && rg --files -g '!*.pdf' | head -50",
        "cwd":"/Users/test/tryout/workspace","permission_rule":"destructive",
        "reason":"command requires conservative policy review: glob_expansion",
        "body_markdown":"Original reason: glob_expansion; rule: destructive",
        "remember_scope":"Scope: This run\nMatch: Same command and execution settings · same tool/action\nAccess: Current sandbox only",
        "approval_request":{"normalized_input_hash":"exact-input","requested_overlay":{"file_system_entries":[],"network_enabled":null}}})
}

#[test]
fn modified_approval_shortcuts_never_submit_or_change_approval_scope() {
    for modifiers in [
        KeyModifiers::CONTROL,
        KeyModifiers::ALT,
        KeyModifiers::CONTROL | KeyModifiers::SHIFT,
    ] {
        for code in [
            KeyCode::Char('y'),
            KeyCode::Char('s'),
            KeyCode::Char('d'),
            KeyCode::Char('n'),
            KeyCode::Enter,
        ] {
            let mut app = AppState::default();
            app.event(event(
                "snapshot",
                snapshot(vec![], true, vec![command_approval()]),
            ));
            let commands = app.handle_key(KeyEvent::new(code, modifiers));
            assert!(
                commands.is_empty(),
                "{code:?} + {modifiers:?} submitted {commands:?}"
            );
            assert!(app.bottom_pane.permission_overlay_is_visible());
            let Some(ActiveInteractionRef::Permission(permission)) =
                app.bottom_pane.pending_interaction()
            else {
                panic!()
            };
            assert!(
                permission.saved_options.is_empty(),
                "modified shortcut changed the approval scope"
            );
        }
    }
    for (code, modifiers) in [
        (KeyCode::Char('y'), KeyModifiers::NONE),
        (KeyCode::Char('Y'), KeyModifiers::SHIFT),
        (KeyCode::Enter, KeyModifiers::NONE),
    ] {
        let mut app = AppState::default();
        app.event(event(
            "snapshot",
            snapshot(vec![], true, vec![command_approval()]),
        ));
        let commands = app.handle_key(KeyEvent::new(code, modifiers));
        assert_eq!(commands.len(), 1);
        assert_eq!(
            commands[0]["params"],
            json!({"interaction_id":"one", "decision":"approve", "scope":"once"})
        );
    }
}

#[test]
fn remember_approval_requires_explicit_scope_confirmation() {
    let mut app = AppState::default();
    let data = snapshot(vec![], true, vec![command_approval()]);
    app.event(event("snapshot", data.clone()));
    assert!(app
        .handle_key(KeyEvent::new(KeyCode::Char('s'), KeyModifiers::NONE))
        .is_empty());
    let Some(ActiveInteractionRef::Permission(p)) = app.bottom_pane.pending_interaction() else {
        panic!()
    };
    assert!(p
        .remember_scope
        .contains("Same command and execution settings"));
    assert_eq!(p.options[0].id, "remember_confirm");
    // Background updates cannot skip the confirmation.
    app.event(event("snapshot", data));
    let commands = app.handle_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
    assert_eq!(commands.len(), 1);
    assert_eq!(
        commands[0]["params"],
        json!({"interaction_id":"one","decision":"approve","scope":"session"})
    );
    assert!(app
        .handle_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE))
        .is_empty());
}

#[test]
fn remember_back_and_escape_never_submit_or_dismiss_request() {
    for back in [false, true] {
        let mut app = AppState::default();
        app.event(event(
            "snapshot",
            snapshot(vec![], true, vec![command_approval()]),
        ));
        app.handle_key(KeyEvent::new(KeyCode::Down, KeyModifiers::NONE));
        assert!(app
            .handle_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE))
            .is_empty());
        // The selected second row remains Back, not Confirm.
        let key = if back { KeyCode::Enter } else { KeyCode::Esc };
        assert!(app
            .handle_key(KeyEvent::new(key, KeyModifiers::NONE))
            .is_empty());
        let Some(ActiveInteractionRef::Permission(p)) = app.bottom_pane.pending_interaction()
        else {
            panic!()
        };
        assert!(p.saved_options.is_empty());
        assert_eq!(p.options[1].id, "approve_session");
        assert!(app
            .handle_key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE))
            .is_empty());
        assert!(!app.bottom_pane.permission_overlay_is_visible());
    }
}

#[test]
fn unavailable_grant_scope_hides_remember_but_keeps_deny() {
    let mut app = AppState::default();
    app.event(event(
        "snapshot",
        snapshot(
            vec![],
            true,
            vec![json!({"kind":"permission","session_id":"two"})],
        ),
    ));
    assert!(app
        .handle_key(KeyEvent::new(KeyCode::Char('s'), KeyModifiers::NONE))
        .is_empty());
    let commands = app.handle_key(KeyEvent::new(KeyCode::Char('n'), KeyModifiers::NONE));
    assert_eq!(
        commands[0]["params"],
        json!({"interaction_id":"two","decision":"deny","scope":"once"})
    );
}

#[test]
fn command_approval_role_stage_and_details_at_terminal_widths() {
    for width in [40, 80, 120] {
        let mut app = AppState::default();
        app.event(event(
            "snapshot",
            snapshot(vec![], true, vec![command_approval()]),
        ));
        let area = ratatui::layout::Rect::new(0, 0, width, 45);
        let mut buffer = ratatui::buffer::Buffer::empty(area);
        app.bottom_pane
            .render_bottom_view(area, &mut buffer, &app.theme);
        let text = buffer
            .content
            .iter()
            .map(|c| c.symbol())
            .collect::<String>();
        for expected in [
            "Confirm command",
            "Role:   Core",
            "Stage:  literature grounding",
            "Allow once",
            "Remember for this run",
            "Deny this request",
        ] {
            assert!(
                text.contains(expected),
                "{width}: missing {expected}: {text}"
            );
        }
        for hidden in [
            "Access after approval",
            "Current sandbox only",
            "destructive",
            "glob_expansion",
        ] {
            assert!(!text.contains(hidden), "{width}: {text}");
        }
        assert!(!text.contains("Why approval is needed"));
        assert!(!text.to_lowercase().contains("f details"));
        app.handle_key(KeyEvent::new(KeyCode::Char('F'), KeyModifiers::NONE));
        assert!(app.pager.is_none(), "F no longer opens approval details");
    }
}

#[test]
fn long_approval_keeps_actions_visible_and_all_command_rows_reachable() {
    for width in [40, 80, 120] {
        for height in [24, 42, 62] {
            let mut app = AppState::default();
            let mut request = command_approval();
            let markers: Vec<_> = (0..100).map(|i| format!("COMMAND_ROW_{i:03}")).collect();
            request["command"] = json!(markers.join("\n"));
            app.event(event("snapshot", snapshot(vec![], true, vec![request])));
            let render = |app: &AppState| {
                let area = ratatui::layout::Rect::new(0, 0, width, height);
                let mut buffer = ratatui::buffer::Buffer::empty(area);
                app.bottom_pane
                    .render_bottom_view(area, &mut buffer, &app.theme);
                buffer
                    .content
                    .iter()
                    .map(|c| c.symbol())
                    .collect::<String>()
            };
            let mut seen = String::new();
            for _ in 0..110 {
                let text = render(&app);
                for label in ["ACTIONS", "Allow once", "Deny this request", "PgUp/PgDn"] {
                    assert!(
                        text.contains(label),
                        "{width}x{height}: missing {label}: {text}"
                    );
                }
                seen.push_str(&text);
                assert!(app
                    .handle_key(KeyEvent::new(KeyCode::PageDown, KeyModifiers::NONE))
                    .is_empty());
            }
            for marker in markers {
                assert!(seen.contains(&marker), "missing {marker}");
            }
            // Repeated paging at the bottom must not accumulate unreachable scroll debt.
            let at_bottom = render(&app);
            app.handle_key(KeyEvent::new(KeyCode::PageUp, KeyModifiers::NONE));
            assert_ne!(render(&app), at_bottom);
            assert_eq!(
                app.bottom_pane.selected_permission_option_id().as_deref(),
                Some("approve")
            );
            for _ in 0..110 {
                app.handle_key(KeyEvent::new(KeyCode::PageUp, KeyModifiers::NONE));
                render(&app);
            }
            assert!(render(&app).contains("COMMAND_ROW_000"));
        }
    }
}

fn repaired_log_plain(app: &AppState, width: usize) -> String {
    app.render_scrollback_tail(width)
        .into_iter()
        .map(|line| line.line.to_string())
        .collect::<Vec<_>>()
        .join("\n")
}
fn repaired_log_audit() -> Value {
    json!({"audit_id":"review-one","run_id":"run-one","role":"reviewer","stage":"output_synthesis","attempt":1,"review_round":1})
}
fn repaired_log_read(id: &str) -> Value {
    json!({"schema_version":"ownpi.tui.item.v1","visibility":"user","item_id":id,"kind":"command_execution",
        "tool_name":"file_reader","action":"read","status":"completed","audit_context":repaired_log_audit(),
        "payload":{"target":format!("{id}.md"),"aggregation_key":id}})
}

#[test]
fn log_repair_empty_reasoning_is_layout_neutral_and_does_not_split_exploration() {
    let reason = json!({"schema_version":"ownpi.tui.item.v1","visibility":"user","item_id":"empty",
        "kind":"reasoning","status":"completed","audit_context":repaired_log_audit(),"payload":{"summary":[],"content":[]}});
    let mut baseline = AppState::default();
    baseline.event(event(
        "snapshot",
        snapshot(
            vec![repaired_log_read("before"), repaired_log_read("after")],
            false,
            vec![],
        ),
    ));
    let mut actual = AppState::default();
    actual.event(event(
        "snapshot",
        snapshot(
            vec![
                repaired_log_read("before"),
                reason,
                repaired_log_read("after"),
            ],
            false,
            vec![],
        ),
    ));
    assert_eq!(
        repaired_log_plain(&baseline, 80),
        repaired_log_plain(&actual, 80)
    );
}

#[test]
fn log_repair_verified_child_and_live_reviewer_continue_same_audit_region() {
    let mut child = repaired_log_read("child");
    child["scope"] = json!({"actor_kind":"subagent","actor_id":"child-one","actor_label":"Subagent · evidence · child-on"});
    let mut app = AppState::default();
    app.event(event(
        "snapshot",
        snapshot(vec![repaired_log_read("parent"), child], true, vec![]),
    ));
    app.stream(&json!({"canonical_event_type":"assistant_text_delta","item_id":"m","delta":"检查中文证据",
        "stream_key":["r","reviewer",1,"m"],"audit_context":repaired_log_audit(),"scope":{"actor_id":"reviewer"}}));
    let history = repaired_log_plain(&app, 100);
    let live = app
        .active_agent_stream_lines(100)
        .iter()
        .map(|line| line.to_string())
        .collect::<Vec<_>>()
        .join("\n");
    assert_eq!(
        history.matches("AUDIT / REVIEWER").count() + live.matches("AUDIT / REVIEWER").count(),
        1,
        "{history}\n{live}"
    );
    assert!(
        !history.contains("child.md"),
        "Subagent detail must stay out of Main: {history}"
    );
    assert!(
        live.lines()
            .any(|line| line.starts_with('│') && line.contains("检查中文证据")),
        "{live}"
    );
    let canonical = json!({"schema_version":"ownpi.tui.item.v1","visibility":"user","item_id":"m","kind":"agent_message",
        "status":"completed","audit_context":repaired_log_audit(),"scope":{"actor_id":"reviewer"},
        "stream_key":["r","reviewer",1,"m"],"payload":{"text":"检查中文证据"}});
    app.event(event(
        "snapshot",
        snapshot(vec![repaired_log_read("parent"), canonical], true, vec![]),
    ));
    assert!(app.live.is_empty());
    let view = repaired_log_plain(&app, 100);
    assert_eq!(view.matches("AUDIT / REVIEWER").count(), 1, "{view}");
    assert!(
        view.lines()
            .any(|line| line.starts_with('│') && line.contains("检查中文证据")),
        "{view}"
    );
}

#[test]
fn provider_sources_replace_live_text_once_and_remain_clickable() {
    let mut app = AppState::default();
    let key = json!(["r", "core", 1, "provider-one"]);
    app.stream(
        &json!({"canonical_event_type":"assistant_text_delta", "item_id":"provider-one",
        "delta":"中文🙂 原文", "stream_key":key, "scope":{"actor_id":"core"}}),
    );
    assert_eq!(app.live.len(), 1);
    let body = json!({"schema_version":"ownpi.tui.item.v1", "visibility":"user", "item_id":"answer",
        "kind":"agent_message", "status":"completed", "stream_key":key,
        "scope":{"actor_id":"core"}, "payload":{"text":"中文🙂 原文"}});
    let source_text = "提供方附随此消息的链接（未逐条对应正文）：\n\n- [https://example.org/a?x=1&amp;y=2](<https://example.org/a?x=1&amp;y=2>)";
    let sources = json!({"schema_version":"ownpi.tui.item.v1", "visibility":"user", "item_id":"answer:sources",
        "kind":"agent_message", "status":"completed", "stream_key":key,
        "scope":{"actor_id":"core"}, "payload":{"text":source_text,"source_list":true}});
    app.event(event(
        "snapshot",
        snapshot(vec![body, sources], true, vec![]),
    ));
    assert!(app.live.is_empty());
    let view = repaired_log_plain(&app, 120);
    assert_eq!(view.matches("中文🙂 原文").count(), 1, "{view}");
    assert_eq!(
        view.matches("https://example.org/a?x=1&y=2").count(),
        1,
        "{view}"
    );
    let links = crate::markdown::render_markdown_hyperlinks(source_text, 120, &app.theme, None);
    assert!(links
        .iter()
        .flat_map(|line| &line.hyperlinks)
        .any(|link| link.destination == "https://example.org/a?x=1&y=2"));
    let unusual = "- [https://例子.测试/路径](<https://例子.测试/路径>)\n- [https://example.org/a\\_\\[b\\]\\*c](<https://example.org/a_[b]*c>)";
    let links = crate::markdown::render_markdown_hyperlinks(unusual, 120, &app.theme, None);
    let destinations = links
        .iter()
        .flat_map(|line| &line.hyperlinks)
        .map(|link| link.destination.as_str())
        .collect::<Vec<_>>();
    assert!(destinations.contains(&"https://xn--fsqu00a.xn--0zwm56d/%E8%B7%AF%E5%BE%84"));
    assert!(destinations.contains(&"https://example.org/a_[b]*c"));
}

#[test]
fn log_repair_long_source_warnings_have_bounded_status_at_forty_columns() {
    let warnings = (0..5)
        .map(|i| format!("append_stage identity mismatch {i} {}", "x".repeat(170)))
        .collect::<Vec<_>>();
    let mut app = AppState::default();
    let mut data = snapshot(vec![], false, vec![]);
    data["warnings"] = json!(warnings);
    app.event(event("snapshot", data));
    let rows = app.status.render_lines(40, &app.theme);
    assert!(rows.len() <= 4, "{} rows", rows.len());
    assert!(app
        .status
        .notice_text()
        .unwrap()
        .contains("5 source warnings"));
    assert!(app.status.notice_text().unwrap().contains("/audit"));
}

#[test]
fn log_repair_failed_command_shows_error_once_but_keeps_original_transcript() {
    let item = json!({"schema_version":"ownpi.tui.item.v1","visibility":"user","item_id":"failed","kind":"command_execution",
        "action":"run","status":"failed","payload":{"command":"python script.py","output":["command not found: python"],
        "error":"command not found: python","exit_code":127,"transcript":"original failure receipt: command not found: python"}});
    let cell = TypedThreadItemHistoryCell::new(
        DisplayItem::parse_value(&item).unwrap(),
        ThemePalette::default(),
    );
    let text = cell
        .display_lines(140)
        .iter()
        .map(|line| line.to_string())
        .collect::<Vec<_>>()
        .join("\n");
    assert_eq!(
        text.matches("command not found: python").count(),
        1,
        "{text}"
    );
    assert!(cell
        .raw_lines()
        .iter()
        .any(|line| line.to_string().contains("original failure receipt")));
}

#[test]
fn log_repair_multiline_command_is_compact_and_full_transcript_is_available() {
    let command = (0..29)
        .map(|i| format!("print('中文证据 row {i}')"))
        .collect::<Vec<_>>()
        .join("\n");
    let item = json!({"schema_version":"ownpi.tui.item.v1","visibility":"user","item_id":"long","kind":"command_execution",
        "action":"run","status":"completed","payload":{"command":command,"output":["done"],"exit_code":0,"transcript":command}});
    let cell = TypedThreadItemHistoryCell::new(
        DisplayItem::parse_value(&item).unwrap(),
        ThemePalette::default(),
    );
    for width in [40, 80, 140] {
        let rows = cell.display_lines(width);
        let text = rows
            .iter()
            .map(|line| line.to_string())
            .collect::<Vec<_>>()
            .join("\n");
        assert!(
            rows.len() < 16,
            "width={width}: {} rows\n{text}",
            rows.len()
        );
        assert!(text.contains("ctrl + t"), "{text}");
        let full = cell
            .transcript_lines(width)
            .iter()
            .map(|line| line.to_string())
            .collect::<Vec<_>>()
            .join("\n");
        assert!(full.contains("row 28"), "{full}");
    }
}

#[test]
fn fidelity_submitted_prompt_keeps_the_same_rail_in_live_view_and_scrollback() {
    let mut app = AppState::default();
    app.event(event(
        "snapshot",
        snapshot(
            vec![user("u", "中文输入 cross Subject Speech decoding")],
            true,
            vec![],
        ),
    ));
    for width in [20, 40, 120] {
        let cell = app
            .history
            .iter()
            .find(|cell| cell.edge_color().is_some())
            .unwrap();
        let expected = cell
            .display_hyperlink_lines_edged(width)
            .iter()
            .map(|line| line.line.to_string())
            .collect::<Vec<_>>()
            .join("\n");
        let visible = repaired_log_plain(&app, width);
        assert!(
            visible.contains(&expected),
            "width={width}: missing rail\n{visible}\nexpected:\n{expected}"
        );
        assert!(expected.lines().all(|line| line.starts_with('▏')));
        assert!(!cell
            .raw_lines()
            .iter()
            .any(|line| line.to_string().starts_with('▏')));
    }
}

#[test]
fn fidelity_stage_appends_ignore_hidden_receipts_and_keep_one_numbered_list() {
    let mut app = AppState::default();
    let mut items = Vec::new();
    for (i, stage) in [
        "literature grounding",
        "research framing",
        "analysis decision",
        "output synthesis",
    ]
    .iter()
    .enumerate()
    {
        items.push(json!({"schema_version":"ownpi.tui.item.v1","visibility":"user","item_id":format!("append-{i}"),
            "kind":"command_execution","tool_name":"append_stage","action":"stage_control","status":"completed",
            "scope":{"actor_id":"stage_selection"},"payload":{"command":format!("Append Stage · {stage}"),"stage_ordinal":i+1}}));
        items.push(json!({"schema_version":"ownpi.tui.item.v1","visibility":"audit","item_id":format!("receipt-{i}"),
            "kind":"model_activity","status":"completed","payload":{"label":"Model response"}}));
    }
    app.event(event("snapshot", snapshot(items, true, vec![])));
    let text = repaired_log_plain(&app, 120);
    assert_eq!(text.matches("Append Stage").count(), 1, "{text}");
    assert!(text.contains("4. output synthesis"), "{text}");
    assert!(!text.contains("Model response"), "{text}");
}

#[test]
fn stage_append_ordinals_survive_an_intervening_assistant_message() {
    let mut items = Vec::new();
    for (index, stage) in [
        "literature grounding",
        "research framing",
        "analysis decision",
        "output synthesis",
    ]
    .iter()
    .enumerate()
    {
        items.push(json!({"schema_version":"ownpi.tui.item.v1","visibility":"user",
            "item_id":format!("append-{index}"),"kind":"command_execution","tool_name":"append_stage",
            "action":"stage_control","status":"completed","scope":{"actor_id":"stage_selection"},
            "payload":{"command":format!("Append Stage · {stage}"),"stage_ordinal":index+1}}));
        if index == 0 {
            items.push(json!({"schema_version":"ownpi.tui.item.v1","visibility":"user",
                "item_id":"narration","kind":"agent_message","payload":{"text":"Keep the real message order."}}));
        }
    }
    let mut app = AppState::default();
    app.event(event("snapshot", snapshot(items, false, vec![])));
    for width in [40, 80, 120] {
        let text = repaired_log_plain(&app, width);
        for stage in [
            "1. literature grounding",
            "2. research framing",
            "3. analysis decision",
            "4. output synthesis",
        ] {
            assert!(text.contains(stage), "{text}");
        }
        assert!(!text.contains("1. research framing"), "{text}");
        assert!(text.find("1. literature grounding") < text.find("Keep the real message order."));
        assert!(text.find("Keep the real message order.") < text.find("2. research framing"));
    }
}

#[test]
fn stage_append_without_a_receipt_ordinal_does_not_claim_a_stage_number() {
    let mut app = AppState::default();
    let item = json!({"schema_version":"ownpi.tui.item.v1","visibility":"user",
        "item_id":"append-without-result-position","kind":"command_execution",
        "tool_name":"append_stage","action":"stage_control","status":"completed",
        "payload":{"command":"Append Stage · literature grounding"}});
    app.event(event("snapshot", snapshot(vec![item], false, vec![])));
    let text = repaired_log_plain(&app, 80);
    assert!(text.contains("· literature grounding"), "{text}");
    assert!(!text.contains("1. literature grounding"), "{text}");
}

#[test]
fn fidelity_stage_selection_narration_has_no_internal_actor_stage_attempt_prefix() {
    let mut app = AppState::default();
    let value = json!({"schema_version":"ownpi.tui.item.v1","visibility":"user","item_id":"selection-message",
        "kind":"agent_message","status":"completed","scope":{"actor_id":"stage_selection","actor_kind":"stage_selection","stage":"stage_selection","attempt":1},
        "payload":{"text":"我会先梳理代表论文与泛化难点。"}});
    app.event(event("snapshot", snapshot(vec![value], true, vec![])));
    let text = repaired_log_plain(&app, 120);
    assert!(text.contains("我会先梳理"));
    assert!(
        !text.contains("Stage Selection") && !text.contains("attempt 1"),
        "{text}"
    );
}

#[test]
fn fidelity_thinking_is_hidden_for_live_history_raw_mode_and_pager() {
    for raw in [false, true] {
        let mut app = AppState::default();
        app.raw = raw;
        let reasoning = json!({"schema_version":"ownpi.tui.item.v1","visibility":"user","item_id":"r","kind":"reasoning","status":"completed",
            "payload":{"summary":["Inspecting directory for files","THINKING_SENTINEL"],"content":["RAW_REASONING_SENTINEL"]}});
        app.event(event(
            "snapshot",
            snapshot(
                vec![user("u", "开始"), reasoning, user("v", "后续")],
                true,
                vec![],
            ),
        ));
        app.stream(&json!({"canonical_event_type":"assistant_reasoning_delta","item_id":"live-r","delta":"LIVE_REASONING_SENTINEL","stream_key":["run","core",1,"live-r"],"presentation_kind":"reasoning_summary"}));
        let text = repaired_log_plain(&app, 80);
        let live = app
            .active_agent_stream_lines(80)
            .iter()
            .map(|line| line.to_string())
            .collect::<Vec<_>>()
            .join("\n");
        assert!(
            !text.contains("Thinking") && !text.contains("SENTINEL"),
            "{text}"
        );
        assert!(!live.contains("SENTINEL"), "{live}");
        assert!(app.live.is_empty());
        app.handle_key(KeyEvent::new(KeyCode::Char('t'), KeyModifiers::CONTROL));
        let pager = app
            .pager
            .as_ref()
            .unwrap()
            .render_lines(80)
            .0
            .iter()
            .map(|line| line.to_string())
            .collect::<Vec<_>>()
            .join("\n");
        assert!(!pager.contains("SENTINEL"), "{pager}");
        assert!(app.canonical.iter().any(|v| v["kind"] == "reasoning"));
    }
}

fn open_first_request(app: &mut AppState) {
    app.handle_key(KeyEvent::new(KeyCode::F(4), KeyModifiers::NONE));
    app.handle_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
}

#[test]
fn context_capacity_never_leaks_between_runs_or_from_future_model_settings() {
    let mut app = AppState::default();
    let mut data = snapshot(vec![], false, vec![]);
    data["usage"] =
        json!({"provider_prompt_tokens":100,"context_window_limit":200,"is_estimate":false});
    app.event(event("snapshot", data.clone()));
    assert_eq!(app.usage.percent_left(), Some(50));
    app.event(event(
        "configuration",
        json!({"model":"next-model","context_window_limit":9999,"context_meter_enabled":true}),
    ));
    assert_eq!(app.usage.percent_left(), Some(50));
    data["usage"] =
        json!({"provider_prompt_tokens":144585,"context_window_limit":null,"is_estimate":false});
    app.event(event("snapshot", data.clone()));
    assert_eq!(app.usage.used_tokens(), Some(144585));
    assert_eq!(app.usage.percent_left(), None);
    data["usage"] = json!({"is_estimate":true});
    app.event(event("snapshot", data));
    assert_eq!(app.usage.used_tokens(), None);
    assert_eq!(app.usage.percent_left(), None);
}

#[test]
fn whitespace_messages_do_not_split_stage_appends_or_disappear_from_canonical() {
    let mut items = Vec::new();
    for (i, stage) in [
        "literature grounding",
        "research framing",
        "method design",
        "output synthesis",
    ]
    .iter()
    .enumerate()
    {
        items.push(json!({"schema_version":"ownpi.tui.item.v1","visibility":"user","item_id":format!("append-{i}"),
            "kind":"command_execution","tool_name":"append_stage","action":"stage_control","status":"completed",
            "scope":{"actor_id":"stage_selection"},"payload":{"command":format!("Append Stage · {stage}"),"stage_ordinal":i+1}}));
        items.push(json!({"schema_version":"ownpi.tui.item.v1","visibility":"user","item_id":format!("blank-{i}"),
            "kind":"agent_message","scope":{"actor_id":"stage_selection"},"payload":{"text":" \n\t　"}}));
    }
    let mut app = AppState::default();
    app.event(event("snapshot", snapshot(items.clone(), true, vec![])));
    for width in [40, 80, 120, 180] {
        let text = repaired_log_plain(&app, width);
        assert_eq!(text.matches("Append Stage").count(), 1, "{text}");
        assert!(
            text.contains("1. literature grounding") && text.contains("4. output synthesis"),
            "{text}"
        );
    }
    assert_eq!(app.canonical, items);
}

#[test]
fn task_authoring_narration_uses_normal_assistant_style_live_and_persisted() {
    for actor in ["task_authoring", "task_spec_authoring"] {
        let mut app = AppState::default();
        let value = json!({"schema_version":"ownpi.tui.item.v1","visibility":"user","item_id":"authoring-message",
            "kind":"agent_message","status":"completed","scope":{"actor_id":actor,"actor_kind":actor,"stage":actor,"attempt":1},
            "payload":{"text":"整理任务说明，保留用户范围。"}});
        app.event(event(
            "snapshot",
            snapshot(vec![value.clone()], true, vec![]),
        ));
        for width in [40, 80, 180] {
            let text = repaired_log_plain(&app, width);
            assert!(text.contains("整理任务说明"), "{text}");
            assert!(
                !text.contains("Authoring") && !text.contains("attempt 1"),
                "{text}"
            );
            let live = app
                .agent_cell_with_context(&value, true)
                .display_lines(width)
                .iter()
                .map(|line| line.to_string())
                .collect::<Vec<_>>()
                .join("\n");
            assert!(
                !live.contains("Authoring") && !live.contains("attempt 1"),
                "{live}"
            );
        }
    }
}

#[test]
fn blank_live_text_has_no_cell_but_later_content_keeps_original_whitespace() {
    let mut app = AppState::default();
    let context = json!({"canonical_event_type":"assistant_text_delta","item_id":"blank-live",
        "stream_key":["run","task_authoring",1,"blank-live"],
        "scope":{"actor_id":"task_authoring","stage":"task_authoring","attempt":1},"delta":" \n"});
    app.stream(&context);
    assert!(app.active_agent_stream_lines(80).is_empty());
    let mut update = context.clone();
    update["delta"] = json!("保留说明");
    app.stream(&update);
    let text = app
        .active_agent_stream_lines(80)
        .iter()
        .map(|line| line.to_string())
        .collect::<Vec<_>>()
        .join("\n");
    assert!(
        text.contains("保留说明") && !text.contains("Authoring"),
        "{text}"
    );
    assert_eq!(app.live.values().next().unwrap().content, " \n保留说明");
}

#[test]
fn remember_scope_displays_bridge_projection_without_reinterpreting_it() {
    let mut request = command_approval();
    request["actor_kind"] = json!("subagent");
    request["actor_id"] = json!("child-one");
    request["approval_request"]["requested_overlay"] = json!({
        "file_system_entries":[{"access":"write","path":{"kind":"special","value":"project_roots","subpath":"reports"},"skip_if_missing":true}],
        "network_enabled":null
    });
    request["remember_scope"] = json!("Scope: This subagent run\nMatch: Requests within access scope\nAccess: write: project_roots/reports (special) · if present");
    let mut app = AppState::default();
    app.event(event("snapshot", snapshot(vec![], true, vec![request])));
    let Some(ActiveInteractionRef::Permission(p)) = app.bottom_pane.pending_interaction() else {
        panic!()
    };
    assert!(p.remember_scope.contains("This subagent run"));
    assert!(p
        .remember_scope
        .contains("project_roots/reports (special) · if present"));
    assert!(p.remember_scope.contains("Requests within"));
}

#[test]
fn delayed_approval_result_never_closes_next_child_request() {
    for accepted in [true, false] {
        let mut app = AppState::default();
        let mut first = command_approval();
        first["session_id"] = json!("child-a-request");
        first["actor_kind"] = json!("subagent");
        first["actor_id"] = json!("child-a");
        let mut second = first.clone();
        second["session_id"] = json!("child-b-request");
        second["actor_id"] = json!("child-b");
        app.event(event(
            "snapshot",
            snapshot(vec![], true, vec![first, second.clone()]),
        ));
        let outgoing = app.handle_key(KeyEvent::new(KeyCode::Char('y'), KeyModifiers::NONE));
        app.event(event("snapshot", snapshot(vec![], true, vec![second])));
        app.result(outgoing[0]["request_id"].as_str().unwrap(),&json!({"ok":accepted,"disposition":if accepted {"completed"}else{"rejected"},"message":"late result"}));
        let Some(ActiveInteractionRef::Permission(p)) = app.bottom_pane.pending_interaction()
        else {
            panic!("late result closed next approval")
        };
        assert_eq!(p.interaction_id, "child-b-request");
    }
}

#[test]
fn accepted_event_for_old_child_preserves_next_child_and_draft() {
    let mut app = AppState::default();
    app.bottom_pane.set_composer("main draft");
    let mut first = command_approval();
    first["session_id"] = json!("a");
    let mut second = first.clone();
    second["session_id"] = json!("b");
    app.event(event(
        "snapshot",
        snapshot(vec![], true, vec![first, second.clone()]),
    ));
    app.event(event("snapshot", snapshot(vec![], true, vec![second])));
    app.event(event("interaction_accepted", json!({"interaction_id":"a"})));
    let Some(ActiveInteractionRef::Permission(p)) = app.bottom_pane.pending_interaction() else {
        panic!("accepted event closed unrelated request")
    };
    assert_eq!(p.interaction_id, "b");
    app.handle_key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE));
    assert_eq!(app.bottom_pane.composer(), "main draft");
}

#[test]
fn thirteen_parallel_child_approvals_advance_once_in_snapshot_and_result_orders() {
    let mut app = AppState::default();
    let mut remaining = (0..13)
        .map(|i| {
            let mut p = command_approval();
            p["session_id"] = json!(format!("approval-{i}"));
            p["actor_kind"] = json!("subagent");
            p["actor_id"] = json!(format!("child-{}", i % 3));
            p
        })
        .collect::<Vec<_>>();
    app.event(event("snapshot", snapshot(vec![], true, remaining.clone())));
    for i in 0..13 {
        let Some(ActiveInteractionRef::Permission(p)) = app.bottom_pane.pending_interaction()
        else {
            panic!("queue stalled at {i}")
        };
        assert_eq!(p.interaction_id, format!("approval-{i}"));
        let commands = app.handle_key(KeyEvent::new(KeyCode::Char('y'), KeyModifiers::NONE));
        assert_eq!(commands.len(), 1);
        assert!(app
            .handle_key(KeyEvent::new(KeyCode::Char('y'), KeyModifiers::NONE))
            .is_empty());
        remaining.remove(0);
        if i % 2 == 0 {
            app.event(event("snapshot", snapshot(vec![], true, remaining.clone())));
        }
        app.event(event(
            "interaction_accepted",
            json!({"interaction_id":format!("approval-{i}")}),
        ));
        app.result(
            commands[0]["request_id"].as_str().unwrap(),
            &json!({"ok":true,"disposition":"completed"}),
        );
        app.event(event("snapshot", snapshot(vec![], true, remaining.clone())));
    }
    assert!(app.agents.requests.is_empty());
    assert!(!app.bottom_pane.permission_overlay_is_visible());
}

#[test]
fn direct_snapshot_keeps_mode_without_workflow_placeholder_stage() {
    let mut app = AppState::default();
    app.event(event("snapshot", json!({
        "items": [{"kind":"route_decision","item_id":"mode","payload":{"route_label":"Task · Direct"}}],
        "capabilities":{"mode":"direct"},
        "status":{"status":"in_progress","current_stage":"direct_qa","stage_attempt":1,"owner_active":true}
    })));
    let context = app.turn_execution_context.as_ref().unwrap();
    assert_eq!(context.mode_label, "Task · Direct");
    assert!(context.stage_label.is_empty());
    app.event(event("snapshot", json!({
        "items": [], "capabilities":{"mode":"research"},
        "status":{"status":"in_progress","current_stage":"literature_grounding","stage_attempt":2,"owner_active":true}
    })));
    let context = app.turn_execution_context.as_ref().unwrap();
    assert_eq!(context.stage_label, "literature_grounding");
    assert_eq!(context.stage_attempt, 2);
}

#[test]
fn permission_four_actions_dispatch_distinct_decisions_for_all_owners() {
    for actor in ["core", "reviewer", "subagent"] {
        for kind in ["command", "operation", "file_edit"] {
            for (key, decision, index) in [
                ('y', "approve", 0),
                ('d', "deny_command", 2),
                ('n', "deny", 3),
            ] {
                for keyboard in [true, false] {
                    let mut request = command_approval();
                    request["actor_kind"] = json!(actor);
                    request["actor_id"] = json!("child-one");
                    request["approval_kind"] = json!(kind);
                    let data = snapshot(vec![], true, vec![request]);
                    let mut app = AppState::default();
                    app.event(event("snapshot", data.clone()));
                    let Some(ActiveInteractionRef::Permission(p)) =
                        app.bottom_pane.pending_interaction()
                    else {
                        panic!()
                    };
                    assert_eq!(
                        p.options.iter().map(|o| o.id.as_str()).collect::<Vec<_>>(),
                        vec!["approve", "approve_session", "deny_command", "deny"]
                    );
                    let commands = if keyboard {
                        app.handle_key(KeyEvent::new(KeyCode::Char(key), KeyModifiers::NONE))
                    } else {
                        for _ in 0..index {
                            app.handle_key(KeyEvent::new(KeyCode::Down, KeyModifiers::NONE));
                        }
                        app.event(event("snapshot", data));
                        app.handle_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE))
                    };
                    assert_eq!(commands.len(), 1);
                    assert_eq!(
                        commands[0]["params"],
                        json!({"interaction_id":"one", "decision":decision, "scope":"once"})
                    );
                    assert!(app
                        .handle_key(KeyEvent::new(KeyCode::Char(key), KeyModifiers::NONE))
                        .is_empty());
                }
            }
        }
    }
}

#[test]
fn permission_deny_command_survives_missing_remember_scope_and_escape() {
    let mut app = AppState::default();
    app.event(event(
        "snapshot",
        snapshot(
            vec![],
            true,
            vec![json!({"kind":"permission", "session_id":"two"})],
        ),
    ));
    let Some(ActiveInteractionRef::Permission(p)) = app.bottom_pane.pending_interaction() else {
        panic!()
    };
    assert_eq!(
        p.options.iter().map(|o| o.id.as_str()).collect::<Vec<_>>(),
        vec!["approve", "deny_command", "deny"]
    );
    let commands = app.handle_key(KeyEvent::new(KeyCode::Char('D'), KeyModifiers::NONE));
    assert_eq!(
        commands[0]["params"],
        json!({"interaction_id":"two", "decision":"deny_command", "scope":"once"})
    );
    let mut app = AppState::default();
    app.event(event(
        "snapshot",
        snapshot(vec![], true, vec![command_approval()]),
    ));
    if let Some(crate::bottom_pane::ActiveInteractionMut::Permission(p)) =
        app.bottom_pane.pending_interaction_mut()
    {
        p.options[0].id = "unknown".into();
    }
    assert!(app
        .handle_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE))
        .is_empty());
    let Some(ActiveInteractionRef::Permission(p)) = app.bottom_pane.pending_interaction() else {
        panic!()
    };
    assert!(!p.submission.blocked());
    assert!(app
        .handle_key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE))
        .is_empty());
}

#[test]
fn backend_nonreusable_approval_never_recreates_remember_from_input_hash() {
    let mut request = command_approval();
    request["remember_scope"] = json!("");
    let mut app = AppState::default();
    app.event(event("snapshot", snapshot(vec![], true, vec![request])));
    let Some(ActiveInteractionRef::Permission(p)) = app.bottom_pane.pending_interaction() else {
        panic!()
    };
    assert_eq!(
        p.options.iter().map(|o| o.id.as_str()).collect::<Vec<_>>(),
        vec!["approve", "deny_command", "deny"]
    );
    assert!(app
        .handle_key(KeyEvent::new(KeyCode::Char('s'), KeyModifiers::NONE))
        .is_empty());
    let commands = app.handle_key(KeyEvent::new(KeyCode::Char('d'), KeyModifiers::NONE));
    assert_eq!(commands[0]["params"]["decision"], "deny_command");
}

#[test]
fn disconnected_submission_keeps_draft_and_stays_inactive() {
    let mut app = AppState::default();
    app.disconnected("Bridge unavailable");
    app.bottom_pane.set_composer("unsent draft");
    assert!(app
        .input_result(InputResult::SubmittedText("unsent draft".into()))
        .is_empty());
    assert!(!app.active);
    assert_eq!(app.bottom_pane.composer(), "unsent draft");
    app.event(event("snapshot", snapshot(vec![], true, vec![])));
    assert!(
        !app.active,
        "buffered snapshots must not revive a disconnected client"
    );
}

#[test]
fn rejected_submission_preserves_newer_draft() {
    let mut app = AppState::default();
    let commands = app.input_result(InputResult::SubmittedText("first request".into()));
    app.bottom_pane
        .set_composer("new draft typed while awaiting acknowledgment");
    app.result(
        commands[0]["request_id"].as_str().unwrap(),
        &json!({"ok":false,"disposition":"rejected","message":"configuration invalid"}),
    );
    assert_eq!(
        app.bottom_pane.composer(),
        "new draft typed while awaiting acknowledgment"
    );
    assert!(!app.status.animates());
    let rendered = app
        .status
        .render_lines(100, &app.theme)
        .iter()
        .map(crate::wrapping::line_plain_text)
        .collect::<Vec<_>>()
        .join("\n");
    assert!(
        rendered.contains("Request not sent") && rendered.contains("Current draft kept"),
        "{rendered}"
    );
}

#[test]
fn queue_failure_restores_only_an_empty_composer_and_never_retries() {
    for newer in ["", "newer draft"] {
        let mut app = AppState::default();
        let commands = app.input_result(InputResult::SubmittedText("not queued".into()));
        app.bottom_pane.set_composer(newer);
        app.command_not_sent(&commands[0]);
        app.disconnected("command queue unavailable");
        assert_eq!(
            app.bottom_pane.composer(),
            if newer.is_empty() {
                "not queued"
            } else {
                newer
            }
        );
        assert!(!app.active);
        assert!(app.pending.is_empty());
    }
}

#[test]
fn runtime_slash_commands_from_bridge_catalog_are_accepted_by_composer() {
    for command in ["/direct", "/research", "/runtime"] {
        let mut app = AppState::default();
        app.event(event(
            "bootstrap",
            json!({"config":{},"skills":[],"commands":[
            {"name":"/direct","description":"Direct"},
            {"name":"/research","description":"Research"},
            {"name":"/runtime","description":"Runtime level"}]}),
        ));
        app.bottom_pane.set_composer(command);
        let commands = app.handle_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
        assert_eq!(commands.len(), 1, "{command}: {commands:?}");
        assert_eq!(commands[0]["method"], "command");
        assert_eq!(commands[0]["params"]["text"], command);
        assert!(!app.active, "switching level must not submit a task");
        assert!(app.bottom_pane.composer().is_empty());
    }
}
