use super::*;

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
fn delta(id: &str, response: &str, content: &str) -> Value {
    json!({"canonical_event_type":"assistant_text_delta","item_id":id,"response_id":response,
        "provider_attempt":1,"stream_key":["run","core",1,id],"delta":content,
        "conversation_id":"conversation","scope":{"actor_kind":"core","actor_id":"core"},
        "agent_ref":{"run_id":"run","actor_id":"core","actor_kind":"core","conversation_id":"conversation"}})
}
fn record(id: &str, content: &str) -> Value {
    json!({"schema_version":"ownpi.tui.item.v1","visibility":"user","kind":"agent_message",
        "item_id":format!("saved-{id}"),"status":"completed","stream_key":["run","core",1,id],
        "conversation_id":"conversation","agent_ref":{"run_id":"run","actor_id":"core","actor_kind":"core","conversation_id":"conversation"},
        "payload":{"text":content}})
}
fn snap(items: Vec<Value>, active: bool) -> Value {
    json!({"items":items,"worker_active":active,"subagents":[],"interactions":[],
        "status":{"run_id":"run","status":if active {"in_progress"} else {"stopped"}}})
}
fn visible(app: &AppState) -> String {
    app.active_agent_stream_lines(120)
        .iter()
        .map(crate::wrapping::line_plain_text)
        .collect::<Vec<_>>()
        .join("\n")
}

#[test]
fn regression_blank_record_preserves_labelled_preview_until_valid_commit() {
    let mut app = AppState::default();
    app.event(evt("stream", delta("m", "response-1", "OBSERVED_TEXT")));
    app.event(evt("snapshot", snap(vec![record("m", " ")], true)));
    assert!(visible(&app).contains("OBSERVED_TEXT"));
    assert!(visible(&app).contains("Output record mismatch"));
    app.event(evt(
        "snapshot",
        snap(vec![record("m", "Authoritative revision")], true),
    ));
    assert!(!visible(&app).contains("OBSERVED_TEXT"));
    assert!(app.live.is_empty());
    app.event(evt("stream", delta("m", "response-1", "LATE_DUPLICATE")));
    assert!(!visible(&app).contains("LATE_DUPLICATE"));
}

#[test]
fn regression_only_assistant_body_can_take_over_stream() {
    let mut app = AppState::default();
    app.event(evt("stream", delta("m", "response-1", "PREVIEW")));
    let mut unrelated = record("m", "Not an assistant body");
    unrelated["kind"] = json!("user");
    app.event(evt("snapshot", snap(vec![unrelated], true)));
    assert!(visible(&app).contains("PREVIEW"));
    let mut sources = record("m", "Source links");
    sources["payload"]["source_list"] = json!(true);
    app.event(evt("snapshot", snap(vec![sources], true)));
    assert!(visible(&app).contains("PREVIEW"));
}

#[test]
fn review_buffer_withdraws_a_preview_when_final_phase_or_held_record_arrives() {
    for use_record in [true, false] {
        let mut app = AppState::default();
        app.event(evt("stream", delta("m", "r", "PENDING_BODY")));
        if use_record {
            let mut held = record("m", "PENDING_BODY");
            held["visibility"] = json!("audit");
            held["payload"]["detail_only"] = json!(true);
            held["payload"]["review_withheld"] = json!(true);
            app.event(evt("snapshot", snap(vec![held], true)));
        } else {
            let mut final_phase = delta("m", "r", "");
            final_phase["message_purpose"] = json!("answer_candidate");
            app.event(evt("stream", final_phase));
        }
        assert!(!visible(&app).contains("PENDING_BODY"));
        assert!(app.live.is_empty());
    }
}

#[test]
fn regression_late_error_does_not_withdraw_new_response() {
    let mut app = AppState::default();
    app.event(evt("stream", delta("old", "response-1", "OLD_OUTPUT")));
    app.event(evt("stream", delta("new", "response-2", "NEW_OUTPUT")));
    app.event(evt(
        "stream",
        json!({"canonical_event_type":"attempt_discarded",
        "stream_key":["run","core",1,""],"response_id":"response-1","provider_attempt":1}),
    ));
    let rendered = visible(&app);
    assert!(rendered.contains("NEW_OUTPUT"));
    assert!(!rendered.contains("OLD_OUTPUT"));
    assert!(rendered.contains("Output withdrawn"));
}

#[test]
fn regression_stop_does_not_silently_erase_unrecorded_text() {
    let mut app = AppState::default();
    app.event(evt("stream", delta("m", "response-1", "UNRECORDED_TEXT")));
    app.event(evt("snapshot", snap(vec![], false)));
    assert!(visible(&app).contains("UNRECORDED_TEXT"));
    assert!(visible(&app).contains("Unconfirmed output"));
}

#[test]
fn regression_committed_record_precedes_delayed_stream_without_duplicate() {
    let mut app = AppState::default();
    app.event(evt("snapshot", snap(vec![record("m", "COMMITTED")], true)));
    app.event(evt("stream", delta("m", "response-1", "COMMITTED")));
    assert!(app.live.is_empty());
}

#[test]
fn regression_older_snapshot_cannot_erase_new_canonical_body() {
    let mut app = AppState::default();
    let mut newer = evt("snapshot", snap(vec![record("m", "LATEST")], true));
    newer.seq = 20;
    app.event(newer);
    let mut older = evt("snapshot", snap(vec![], false));
    older.seq = 19;
    app.event(older);
    assert_eq!(app.canonical[0]["payload"]["text"], "LATEST");
    assert!(app.active);
}

#[test]
fn regression_reused_item_id_stays_scoped_to_its_conversation() {
    let mut app = AppState::default();
    let first = delta("same-id", "r1", "FIRST_CHILD");
    let mut second = first.clone();
    second["conversation_id"] = json!("other-conversation");
    second["agent_ref"]["conversation_id"] = json!("other-conversation");
    second["response_id"] = json!("r2");
    second["delta"] = json!("SECOND_CHILD");
    app.event(evt("stream", first));
    app.event(evt("stream", second));
    app.event(evt(
        "snapshot",
        snap(vec![record("same-id", "FIRST_COMMITTED")], true),
    ));
    assert_eq!(app.live.len(), 1);
    assert!(visible(&app).contains("SECOND_CHILD"));
}

#[test]
fn regression_cancelled_card_preserves_last_activity_and_unknown_is_not_waiting() {
    let task = json!({"task_name":"evidence","status":"running","display_status":"State unconfirmed",
        "terminal_status":"","activity":{"label":"Read","detail":"paper.md","timestamp":"2026-11-06T10:00:00Z"}});
    let mut counts = crate::multi_agents::TaskCounts::default();
    counts.observe(&task);
    assert_eq!(counts.running, 0);
    assert_eq!(counts.waiting, 0);
    assert_eq!(counts.unconfirmed, 1);
    let cell = crate::multi_agents::SubagentBatchCell::new(
        json!({"tasks":[task]}),
        json!({}),
        Default::default(),
    );
    let rendered = cell
        .display_lines(80)
        .iter()
        .map(crate::wrapping::line_plain_text)
        .collect::<Vec<_>>()
        .join("\n");
    assert!(rendered.contains("State unconfirmed"));
    assert!(rendered.contains("Last activity: Read paper.md"));
    let expected_time = chrono::DateTime::parse_from_rfc3339("2026-11-06T10:00:00Z")
        .unwrap()
        .with_timezone(&chrono::Local)
        .format("%Y-%m-%d %H:%M:%S")
        .to_string();
    assert!(rendered.contains(&expected_time));
    assert!(!rendered.contains("2026-11-06T10:00:00Z"));
    assert!(!rendered.contains("1 waiting"));
}

#[test]
fn regression_stream_capacity_is_bounded_and_reports_evictions() {
    let mut app = AppState::default();
    for index in 0..140 {
        app.event(evt(
            "stream",
            delta(&format!("m{index}"), "response", "text"),
        ));
    }
    assert!(app.live.len() <= 128);
    assert!(app.live.evicted > 0);
    assert!(visible(&app).contains("display capacity"));
}

#[test]
fn regression_commit_keeps_observed_response_identity_on_later_snapshots() {
    let mut app = AppState::default();
    app.event(evt("stream", delta("reused", "r1", "FIRST")));
    app.event(evt(
        "snapshot",
        snap(vec![record("reused", "FINAL_FIRST")], true),
    ));
    app.event(evt("stream", delta("reused", "r2", "SECOND")));
    app.event(evt(
        "snapshot",
        snap(vec![record("reused", "FINAL_FIRST")], true),
    ));
    assert!(visible(&app).contains("SECOND"));
    app.event(evt("stream", delta("reused", "r1", "LATE_FIRST")));
    assert!(!visible(&app).contains("LATE_FIRST"));
}

#[test]
fn regression_terminal_without_retry_identity_cannot_withdraw_two_attempts() {
    let mut app = AppState::default();
    app.event(evt("stream", delta("first", "shared", "FIRST")));
    let mut second = delta("second", "shared", "SECOND");
    second["provider_attempt"] = json!(2);
    app.event(evt("stream", second));
    app.event(evt(
        "stream",
        json!({"canonical_event_type":"stream_error",
        "stream_key":["run","core",1,""],"response_id":"shared"}),
    ));
    assert!(visible(&app).contains("FIRST"));
    assert!(visible(&app).contains("SECOND"));
    assert!(app.live.audit().contains("ambiguous_terminal_not_applied"));
}

#[test]
fn regression_unscoped_old_error_cannot_withdraw_the_only_current_response() {
    let mut app = AppState::default();
    app.event(evt("stream", delta("new", "new-response", "CURRENT")));
    app.event(evt(
        "stream",
        json!({"canonical_event_type":"stream_error",
        "stream_key":["run","core",1,""]}),
    ));
    assert!(visible(&app).contains("CURRENT"));
    assert!(!visible(&app).contains("Output withdrawn"));
}

#[test]
fn candidate_never_becomes_public_preview_and_does_not_block_progress() {
    let mut app = AppState::default();
    let mut candidate = delta("candidate", "response-1", "UNREVIEWED_BODY");
    candidate["message_purpose"] = json!("answer_candidate");
    app.event(evt("stream", candidate));
    app.event(evt(
        "stream",
        delta("progress", "response-2", "LIVE_PROGRESS"),
    ));
    assert!(!visible(&app).contains("UNREVIEWED_BODY"));
    assert!(visible(&app).contains("LIVE_PROGRESS"));
    assert!(!app.live.audit().contains("UNREVIEWED_BODY"));
}
