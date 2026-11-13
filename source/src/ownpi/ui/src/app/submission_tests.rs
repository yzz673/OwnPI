use super::*;

fn shown(app: &AppState) -> String {
    app.submission_lines(100)
        .iter()
        .map(Line::to_string)
        .collect::<Vec<_>>()
        .join("\n")
}
fn send(app: &mut AppState, prompt: &str) -> Value {
    app.bottom_pane.set_composer(prompt);
    app.input_result(InputResult::SubmittedText(prompt.into()))
        .remove(0)
}
fn snapshot(app: &mut AppState, request: &Value, items: Value) {
    app.snapshot(&json!({"items":items, "worker_active":true,
        "submission_receipts":[{"request_id":request["request_id"],"run_id":"run","item_id":"run:user"}]}));
}

#[test]
fn temporary_prompt_matches_canonical_appearance_in_every_delivery_state() {
    let prompt =
        "中文 Prompt with wrapping and a source https://example.org/paper\n第二行\n\n最后一行";
    for dark in [false, true] {
        let mut app = AppState::default();
        if dark {
            app.theme.surface_bg = Some(ratatui::style::Color::Rgb(16, 16, 16));
            app.theme.elevated_bg = Some(ratatui::style::Color::Rgb(24, 24, 24));
        }
        send(&mut app, prompt);
        for state in [
            SubmissionState::Sending,
            SubmissionState::Recorded("run:user".into()),
            SubmissionState::NotSent,
            SubmissionState::Unconfirmed,
        ] {
            let show_delivery_warning = matches!(
                state,
                SubmissionState::NotSent | SubmissionState::Unconfirmed
            );
            app.submission.as_mut().unwrap().state = state;
            for width in [20, 40, 80, 140] {
                let expected: Vec<_> = BasicHistoryCell::new(
                    BasicHistoryCellKind::User,
                    prompt.into(),
                    app.theme.clone(),
                )
                .display_hyperlink_lines_edged(width)
                .into_iter()
                .map(|line| line.line)
                .collect();
                let actual = app.submission_lines(width);
                assert_eq!(
                    &actual[..expected.len()],
                    expected.as_slice(),
                    "width={width}, dark={dark}"
                );
                assert!(actual[..expected.len()]
                    .iter()
                    .all(|line| line.to_string().starts_with('▏')));
                assert_eq!(actual.len() > expected.len(), show_delivery_warning);
            }
        }
    }
}

#[test]
fn acknowledged_prompt_keeps_appearance_without_duplicate_temporary_message() {
    let mut app = AppState::default();
    let prompt = "中文 Prompt\nwrapped second line for a narrow terminal";
    let request = send(&mut app, prompt);
    let before = app.submission_lines(20);
    snapshot(
        &mut app,
        &request,
        json!([
            {"kind":"user","item_id":"run:user","payload":{"text":prompt}}
        ]),
    );
    assert!(app.submission_lines(20).is_empty());
    let cells: Vec<_> = app
        .history
        .iter()
        .filter(|cell| cell.edge_color().is_some())
        .collect();
    assert_eq!(cells.len(), 1);
    let after: Vec<_> = cells[0]
        .display_hyperlink_lines_edged(20)
        .into_iter()
        .map(|line| line.line)
        .collect();
    assert_eq!(&before[..after.len()], after.as_slice());
}

#[test]
fn prompt_remains_visible_until_its_recorded_identity_arrives() {
    let mut app = AppState::default();
    let request = send(&mut app, "原始 Prompt");
    assert!(app.canonical.is_empty());
    assert!(app.bottom_pane.composer().is_empty());
    assert!(shown(&app).contains("原始 Prompt"));
    assert!(!shown(&app).contains("Sending"));
    let status = app
        .status
        .render_lines(100, &app.theme)
        .iter()
        .map(Line::to_string)
        .collect::<Vec<_>>()
        .join("\n");
    assert!(status.contains("Working"));
    assert!(matches!(
        app.submission.as_ref().unwrap().state,
        SubmissionState::Sending
    ));
    app.snapshot(&json!({"items":[],"worker_active":false}));
    assert!(app.active && shown(&app).contains("原始 Prompt"));
    snapshot(&mut app, &request, json!([]));
    assert!(shown(&app).contains("原始 Prompt"));
    assert!(!shown(&app).contains("Submitted"));
    assert!(matches!(
        app.submission.as_ref().unwrap().state,
        SubmissionState::Recorded(_)
    ));
    snapshot(
        &mut app,
        &request,
        json!([{"kind":"user","item_id":"run:user","payload":{"text":"原始 Prompt"}}]),
    );
    assert!(shown(&app).is_empty());
    assert_eq!(app.canonical.len(), 1);
}

#[test]
fn identical_text_and_foreign_receipts_never_acknowledge_a_submission() {
    let mut app = AppState::default();
    send(&mut app, "same");
    app.snapshot(
        &json!({"items":[{"kind":"user","item_id":"old:user","payload":{"text":"same"}}],
        "submission_receipts":[{"request_id":"other","item_id":"old:user"}]}),
    );
    assert!(shown(&app).contains("same"));
    assert!(matches!(
        app.submission.as_ref().unwrap().state,
        SubmissionState::Sending
    ));
}

#[test]
fn snapshot_can_acknowledge_before_result_without_duplicate_echo() {
    let mut app = AppState::default();
    let request = send(&mut app, "hello");
    snapshot(
        &mut app,
        &request,
        json!([{"kind":"user","item_id":"run:user","payload":{"text":"hello"}}]),
    );
    app.result(request["request_id"].as_str().unwrap(), &json!({"ok":true}));
    assert!(shown(&app).is_empty());
    app.snapshot(&json!({"items":app.canonical,"submission_receipts":[],"worker_active":false}));
    assert!(shown(&app).is_empty());
}

#[test]
fn failed_submission_restores_original_attachment_draft() {
    let mut app = AppState::default();
    app.bottom_pane.set_composer("look ");
    app.bottom_pane
        .attach_local_image("/tmp/example.png".into());
    let draft = app.bottom_pane.composer_draft_snapshot();
    let request = app
        .input_result(InputResult::SubmittedText(
            app.bottom_pane.composer_text_with_pending(),
        ))
        .remove(0);
    app.result(
        request["request_id"].as_str().unwrap(),
        &json!({"ok":false,"disposition":"rejected","message":"not admitted"}),
    );
    assert_eq!(app.bottom_pane.composer_draft_snapshot(), draft);
    assert_eq!(app.bottom_pane.take_local_images().len(), 1);
    assert!(shown(&app).contains("Not sent"));
}

#[test]
fn disconnect_keeps_an_unconfirmed_prompt_without_automatic_resubmission() {
    let mut app = AppState::default();
    send(&mut app, "must remain visible");
    app.disconnected("pipe lost");
    assert!(shown(&app).contains("must remain visible"));
    assert!(shown(&app).contains("unconfirmed"));
    assert!(app.bottom_pane.composer().is_empty());
    assert!(!app.active);
}

#[test]
fn configuration_does_not_override_running_permission_and_unknown_is_explicit() {
    let mut app = AppState::default();
    app.configuration(&json!({"permissions":"full_access"}));
    app.snapshot(
        &json!({"items":[],"worker_active":true,"execution_permissions":"ask_for_approval"}),
    );
    assert_eq!(app.current_permission_label(), "Ask for approval");
    app.snapshot(&json!({"items":[],"worker_active":true}));
    assert_eq!(app.current_permission_label(), "Unknown");
    app.snapshot(&json!({"items":[],"worker_active":false}));
    assert_eq!(app.current_permission_label(), "Full access");
}
