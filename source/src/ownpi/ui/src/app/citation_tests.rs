use super::*;

#[test]
fn a_late_source_rebuilds_the_existing_message_instead_of_only_appending() {
    let owner = json!({"run_id":"late-run","conversation_id":"reviewer"});
    let message = json!({"schema_version":"ownpi.tui.item.v1","kind":"agent_message","item_id":"m", "visibility":"user", "status":"completed",
        "agent_ref":owner,"payload":{"text":"Resultciteturn1search0"}});
    let source = json!({"schema_version":"ownpi.tui.item.v1","kind":"web_search","item_id":"s", "visibility":"user", "status":"completed",
        "agent_ref":owner,"payload":{"detail_only":true},"raw_source":json!({"sources":[{"ref_id":"turn1search0","title":"Late source","url":"https://example.org/late"}]}).to_string()});
    let mut app = AppState::default();
    for (seq, items) in [(1, vec![message.clone()]), (2, vec![message, source])] {
        app.rebuild = false;
        app.event(BridgeEvent {
            schema: crate::bridge::SCHEMA.into(),
            seq,
            r#type: "snapshot".into(),
            request_id: String::new(),
            identity: json!({}),
            data: json!({"items":items,"status":{"status":"idle"},"capabilities":{"mode":"none"}}),
        });
        assert!(app.rebuild);
    }
    let rendered = app
        .history
        .iter()
        .flat_map(|cell| cell.display_lines(120))
        .map(|l| l.to_string())
        .collect::<Vec<_>>()
        .join("\n");
    assert!(rendered.contains("[1] Late source"), "{rendered}");
}

#[test]
fn snapshot_binds_review_and_message_citations_and_preserves_original_records() {
    let audit = json!({"run_id":"run-a","audit_id":"audit-a","role":"reviewer", "stage":"literature_grounding", "attempt":1,"review_round":1});
    let agent = json!({"run_id":"run-a","conversation_id":"reviewer-a","actor_kind":"reviewer"});
    let mut source = json!({"schema_version":"ownpi.tui.item.v1","item_id":"source","kind":"web_search", "visibility":"user", "status":"completed",
        "agent_ref":agent,"audit_context":audit,
        "payload":{"detail_only":true},"raw_source":json!({"action":{"sources":[
            {"ref_id":"turn1search0","title":"Known EEG source","url":"https://example.org/paper?a=1&b=2"}
        ]}}).to_string()});
    let message = json!({"schema_version":"ownpi.tui.item.v1","item_id":"message","kind":"agent_message", "visibility":"user","status":"completed",
        "agent_ref":agent,"audit_context":audit,"payload":{"text":"Resultciteturn1search0"}});
    let review = json!({"schema_version":"ownpi.tui.item.v1","item_id":"review","kind":"review_cycle", "visibility":"user","status":"completed",
        "audit_context":audit,"payload":{"phase":"completed","outcome":"submitted","review_kind":"reviewer", "summary":"[P2] Example — `report.md:3-5, 8`\nResultciteturn1search0"}});
    let mut other = source.clone();
    other["agent_ref"]["conversation_id"] = json!("unrelated");
    other["audit_context"]["audit_id"] = json!("unrelated");
    other["raw_source"] = json!(json!({"sources":[{"ref_id":"turn1search0","title":"Wrong","url":"https://example.org/wrong"}]}).to_string());
    let original = vec![source.clone(), other, message.clone(), review];
    let mut app = AppState::default();
    app.event(BridgeEvent {schema:crate::bridge::SCHEMA.into(),seq:1,r#type:"snapshot".into(),request_id:String::new(),identity:json!({}),
        data:json!({"items":original,"status":{"status":"idle"},"capabilities":{"mode":"research"}})});
    let rendered = app
        .history
        .iter()
        .flat_map(|cell| cell.display_lines(120))
        .map(|l| l.to_string())
        .collect::<Vec<_>>()
        .join("\n");
    assert_eq!(
        rendered.matches("Known EEG source").count(),
        1,
        "{rendered}"
    );
    let detailed = app
        .history
        .iter()
        .flat_map(|cell| cell.transcript_lines(120))
        .map(|l| l.to_string())
        .collect::<Vec<_>>()
        .join("\n");
    assert_eq!(
        detailed.matches("Known EEG source").count(),
        2,
        "{detailed}"
    );
    assert!(rendered.contains("Result[1]"), "{rendered}");
    assert!(!rendered.contains("example.org/wrong"), "{rendered}");
    assert_eq!(app.canonical, original);
    let cell = app.agent_cell_with_context(&message, false);
    assert_eq!(
        cell.copy_text().as_deref(),
        Some("Resultciteturn1search0")
    );
    let links = cell.display_hyperlink_lines(100);
    assert!(links
        .iter()
        .flat_map(|l| &l.hyperlinks)
        .any(|h| h.destination == "https://example.org/paper?a=1&b=2"));
    source["agent_ref"]["run_id"] = json!("different-run");
    source["audit_context"]["run_id"] = json!("different-run");
    app.event(BridgeEvent {schema:crate::bridge::SCHEMA.into(),seq:2,r#type:"snapshot".into(),request_id:String::new(),identity:json!({}),
        data:json!({"items":[source,message],"status":{"status":"idle"},"capabilities":{"mode":"research"}})});
    let rendered = app
        .history
        .iter()
        .flat_map(|cell| cell.display_lines(120))
        .map(|l| l.to_string())
        .collect::<Vec<_>>()
        .join("\n");
    assert!(!rendered.contains("Known EEG source"), "{rendered}");
}
