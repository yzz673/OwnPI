use super::audit_region::{AuditBoundaryCell, AuditRegionProjector};
use super::*;
use serde_json::json;
use std::sync::Arc;

fn audit_item(
    role: &str,
    id: &str,
    kind: &str,
    payload: serde_json::Value,
) -> Arc<dyn HistoryCell> {
    Arc::new(TypedThreadItemHistoryCell::new(crate::display_item::DisplayItem::parse_value(&json!({
        "schema_version": "ownpi.tui.item.v1", "kind": kind, "item_id": id, "call_id": id,
        "tool_name": "test", "status": "completed", "visibility": "user",
        "audit_context": {"audit_id": format!("audit:{role}:1"), "run_id": "run-1", "role": role,
            "stage": "literature_grounding", "attempt": 1, "review_round": 1},
        "payload": payload,
    })).unwrap(), ThemePalette::default()))
}

fn message(role: &str, id: &str) -> Arc<dyn HistoryCell> {
    audit_item(
        role,
        id,
        "agent_message",
        json!({"role":"assistant", "text":"Checking sources. 中文换行与英文mixed text. https://example.org/long/source/path"}),
    )
}

fn result(role: &str) -> Arc<dyn HistoryCell> {
    audit_item(
        role,
        &format!("{role}-result"),
        "review_cycle",
        json!({
            "review_kind": role, "phase": "feedback_received", "outcome": "no_findings", "round": 1,
            "review_target": "run-1/core-round-002/trajectory.jsonl", "overall_assessment": "Scope remains limited.",
        }),
    )
}

fn failed_result(role: &str) -> Arc<dyn HistoryCell> {
    audit_item(
        role,
        &format!("{role}-failed"),
        "review_cycle",
        json!({
            "review_kind": role,
            "phase": "failed",
            "outcome": "failed",
            "round": 1,
            "max_rounds": 2,
            "terminal_source": "bridge_synthesized",
            "failure_cause": "Provider unavailable",
            "failure_detail": "Server overloaded",
            "provider_request_count": 4,
            "provider_retry_count": 8,
            "final_request_attempt_count": 1,
            "final_request_retry_count": 0,
            "transcript": "SSE explicit_provider_error",
        }),
    )
}

fn text(cells: &[Arc<dyn HistoryCell>], width: usize) -> String {
    cells
        .iter()
        .flat_map(|cell| cell.display_lines(width))
        .map(|line| line_plain_text(&line))
        .collect::<Vec<_>>()
        .join("\n")
}

#[test]
fn active_review_has_one_status_heading_and_keeps_standalone_card() {
    let started = audit_item(
        "reviewer",
        "started",
        "review_cycle",
        json!({
            "review_kind":"reviewer", "phase":"started", "round":2
        }),
    );
    for width in [40, 80, 120, 190] {
        let cells = AuditRegionProjector::project_all(
            &[started.clone(), message("reviewer", "message")],
            &ThemePalette::default(),
        );
        let rendered = text(&cells, width);
        assert_eq!(rendered.matches("REVIEWING").count(), 1, "{rendered}");
        assert!(rendered.contains("Checking sources."), "{rendered}");
        assert!(!rendered.contains("Review complete"), "{rendered}");
        let standalone = crate::display_item::ReviewCyclePayload {
            review_kind: "reviewer".into(),
            phase: "started".into(),
            ..Default::default()
        };
        let standalone = render_review_cycle_card(&standalone, width, &ThemePalette::default());
        assert!(standalone
            .iter()
            .any(|line| line_plain_text(line).contains("REVIEWING")));
    }
}

#[test]
fn audit_region_continues_across_cells_and_closes_only_on_result() {
    let theme = ThemePalette::default();
    let cells = vec![
        message("readiness", "a"),
        message("readiness", "b"),
        result("readiness"),
        message("reviewer", "c"),
        result("reviewer"),
    ];
    let projected = AuditRegionProjector::project_all(&cells, &theme);
    let rendered = text(&projected, 120);
    assert_eq!(rendered.matches("AUDIT / READINESS").count(), 1);
    assert_eq!(rendered.matches("AUDIT / REVIEWER").count(), 1);
    assert_eq!(rendered.matches("Review complete").count(), 2);
    assert!(!rendered.contains("Artifact coverage not verified by UI"));
    assert!(rendered.contains("Scope remains limited."));
    assert!(!rendered.contains("PASSED"));
    assert_eq!(projected[0].copy_text(), cells[0].copy_text());
    assert!(!history_cells_require_inter_cell_spacing(
        projected[0].as_ref(),
        projected[1].as_ref()
    ));
}

#[test]
fn audit_interruption_and_unknown_outcome_are_honest() {
    let core: Arc<dyn HistoryCell> = Arc::new(AgentMarkdownHistoryCell::new(
        "Core activity".into(),
        ThemePalette::default(),
    ));
    let cells = vec![
        message("readiness", "a"),
        core,
        message("readiness", "b"),
        Arc::new(AuditBoundaryCell("Outcome unavailable")),
    ];
    let projected = AuditRegionProjector::project_all(&cells, &ThemePalette::default());
    let rendered = text(&projected, 120);
    assert!(rendered.contains("╰─ Audit activity"));
    assert!(!rendered.contains("Control handed off"));
    assert!(!rendered.contains("RESUMED"));
    assert!(rendered.contains("Outcome unavailable"));
    assert!(!rendered.contains("Review complete"));
}

#[test]
fn failed_review_closes_region_with_failure_footer() {
    let cells = vec![message("reviewer", "a"), failed_result("reviewer")];
    let projected = AuditRegionProjector::project_all(&cells, &ThemePalette::default());
    let rendered = text(&projected, 120);
    assert_eq!(rendered.matches("AUDIT / REVIEWER").count(), 1);
    assert_eq!(rendered.matches("Review failed").count(), 1);
    assert!(!rendered.contains("Review complete"));
    assert!(rendered.contains("Provider unavailable"));
    assert!(rendered.contains("4 requests"));
    assert!(rendered.contains("8 retries"));
}

#[test]
fn provider_telemetry_does_not_break_or_steal_the_active_audit() {
    let unscoped: Arc<dyn HistoryCell> = Arc::new(TypedThreadItemHistoryCell::new(
        crate::display_item::DisplayItem::parse_value(&json!({
            "schema_version":"ownpi.tui.item.v1", "kind":"model_activity", "item_id":"provider-unscoped",
            "call_id":"provider-unscoped", "tool_name":"provider", "action":"provider_retry",
            "status":"completed", "visibility":"user", "payload":{"label":"Retry history", "retry_count":1,
                "transcript":"Unscoped provider retry"}
        })).unwrap(), ThemePalette::default()));
    let scoped = audit_item(
        "reviewer",
        "retry-scoped",
        "model_activity",
        json!({
        "status":"completed", "retry_count":2, "activity":"provider_retry"}),
    );
    let cells = AuditRegionProjector::project_all(
        &[
            message("reviewer", "a"),
            unscoped.clone(),
            scoped,
            message("reviewer", "b"),
            failed_result("reviewer"),
        ],
        &ThemePalette::default(),
    );
    let rendered = text(&cells, 120);
    assert_eq!(rendered.matches("AUDIT / REVIEWER").count(), 1);
    assert_eq!(rendered.matches("Review failed").count(), 1);
    assert!(!rendered.contains("RESUMED"));
    assert!(!rendered.contains("Audit paused"));
    assert!(cells[1].display_lines(120).is_empty());
    assert_eq!(cells[1].copy_text(), unscoped.copy_text());
}

#[test]
fn opaque_receipts_are_not_linked_as_local_files() {
    let receipt = "citation-support:source:receipt-123";
    let cell = audit_item(
        "reviewer",
        "opaque",
        "review_cycle",
        json!({
        "phase":"feedback_received", "outcome":"findings", "review_kind":"reviewer",
        "findings":[{"severity":"P1", "title":"Check evidence", "evidence_refs":[receipt]}]}),
    );
    let lines = cell.transcript_hyperlink_lines(120);
    assert!(lines
        .iter()
        .any(|line| line.line.to_string().contains(receipt)));
    assert!(lines.iter().all(|line| line.hyperlinks.is_empty()));
}

#[test]
fn late_result_from_closed_audit_does_not_break_another_active_rail() {
    let late = message("readiness", "late-readiness");
    let cells = AuditRegionProjector::project_all(
        &[
            result("readiness"),
            message("reviewer", "reviewer-start"),
            late.clone(),
            message("reviewer", "reviewer-next"),
            result("reviewer"),
        ],
        &ThemePalette::default(),
    );
    let rendered = text(&cells, 120);
    assert_eq!(rendered.matches("AUDIT / READINESS").count(), 1);
    assert_eq!(rendered.matches("AUDIT / REVIEWER").count(), 1);
    assert_eq!(rendered.matches("Review complete").count(), 2);
    assert!(rendered.contains("Background result · READINESS"));
    assert!(!rendered.contains("RESUMED"));
    assert!(!rendered.contains("Audit paused"));
    assert_eq!(cells[2].copy_text(), late.copy_text());
    assert!(cells[2]
        .display_lines(120)
        .iter()
        .all(|line| line.to_string().starts_with('│')));
}

#[test]
fn audit_regions_wrap_preserve_links_and_snapshot() {
    let cells = vec![
        message("readiness", "a"),
        result("readiness"),
        message("reviewer", "b"),
        result("reviewer"),
    ];
    let mut snapshot = String::new();
    for width in [20, 40, 80, 120, 220] {
        let projected = AuditRegionProjector::project_all(&cells, &ThemePalette::default());
        for cell in &projected {
            let lines = cell.display_hyperlink_lines(width);
            assert!(
                lines.iter().all(|line| line.line.width() <= width),
                "width={width}: {lines:?}"
            );
        }
        let linked = projected[0].display_hyperlink_lines(width);
        // Rewrapping must not alter destinations even when link text spans rows.
        assert!(linked
            .iter()
            .flat_map(|line| &line.hyperlinks)
            .all(|link| link.destination.starts_with("https://example.org/")));
        snapshot.push_str(&format!("WIDTH {width}\n{}\n\n", text(&projected, width)));
    }
    let path =
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/snapshots/audit_regions.txt");
    if std::env::var_os("UPDATE_TUI_SNAPSHOTS").is_some() {
        std::fs::write(&path, &snapshot).unwrap();
    }
    assert_eq!(std::fs::read_to_string(path).unwrap(), snapshot);
}

#[test]
fn projector_replay_and_incremental_batches_match() {
    let cells = vec![
        message("readiness", "a"),
        message("readiness", "b"),
        result("readiness"),
    ];
    let theme = ThemePalette::default();
    let replay = AuditRegionProjector::project_all(&cells, &theme);
    let mut live = AuditRegionProjector::default();
    let incremental = cells
        .iter()
        .map(|cell| live.project(cell.clone(), &theme))
        .collect::<Vec<_>>();
    assert_eq!(text(&replay, 80), text(&incremental, 80));
}

#[test]
fn tool_failure_is_not_an_audit_outcome_and_late_activity_cannot_reopen_it() {
    let failed = audit_item(
        "readiness",
        "tool-failed",
        "command_execution",
        json!({"command":"rg missing", "error":"not found", "exit_code":1}),
    );
    let cells = vec![
        message("readiness", "a"),
        failed,
        result("readiness"),
        message("readiness", "late"),
    ];
    let projected = AuditRegionProjector::project_all(&cells, &ThemePalette::default());
    let rendered = text(&projected, 120);
    assert_eq!(rendered.matches("Review complete").count(), 1);
    assert!(text(&projected[3..], 120).contains("Late result"));
    assert!(!text(&projected[3..], 120).contains("AUDIT /"));
}

#[test]
fn role_colors_remain_distinct_in_light_dark_and_unset_backgrounds() {
    for background in [
        Some(ratatui::style::Color::White),
        Some(ratatui::style::Color::Black),
        None,
    ] {
        let mut theme = ThemePalette::default();
        theme.surface_bg = background;
        theme.elevated_bg = background;
        let cells = AuditRegionProjector::project_all(
            &[message("readiness", "a"), message("reviewer", "b")],
            &theme,
        );
        let readiness = cells[0].display_lines(120)[0].spans[0].style.fg;
        let reviewer = cells[1]
            .display_lines(120)
            .into_iter()
            .find(|line| line.to_string().contains("AUDIT / REVIEWER"))
            .unwrap()
            .spans[0]
            .style
            .fg;
        assert_ne!(readiness, reviewer);
        // Stripping every style still leaves the role and activity state unambiguous.
        assert!(text(&cells, 120).contains("AUDIT / READINESS · CHECKING"));
        assert!(text(&cells, 120).contains("AUDIT / REVIEWER · REVIEWING"));
    }
}

#[test]
fn embedded_findings_keep_evidence_fix_and_assessment() {
    let finding = audit_item(
        "reviewer",
        "finding",
        "review_cycle",
        json!({
            "review_kind":"reviewer", "phase":"feedback_received", "outcome":"findings", "feedback_format":"structured_v2",
            "overall_assessment":"Needs source verification", "findings":[{"severity":"P1", "title":"Missing evidence",
                "description":"The claim has no source", "remediation":"Read the source", "evidence_refs":["report.md:12"]}]
        }),
    );
    for width in [20, 40, 80, 120, 220] {
        let cells = AuditRegionProjector::project_all(&[finding.clone()], &ThemePalette::default());
        let summary = text(&cells, width);
        assert!(summary.contains("P1"));
        for hidden in ["report.md:12", "Evidence", "Why", "Fix"] {
            assert!(!summary.contains(hidden), "{summary}");
        }
        let rendered = cells[0]
            .transcript_lines(width)
            .iter()
            .map(ToString::to_string)
            .collect::<Vec<_>>()
            .join("\n");
        assert!(rendered.contains("report.md:12"));
        assert!(rendered.contains("P1"));
        assert!(rendered.contains("Evidence"));
        assert!(rendered.contains("Fix"));
        assert!(!rendered.contains("NO FINDINGS"));
    }
}

#[test]
fn grouped_findings_have_responsive_layout_and_intact_link_destinations() {
    let source = "https://example.org/papers/very-long-review-source-with-version-and-detailed-locator#section-3";
    let result = audit_item(
        "readiness",
        "grouped-result",
        "review_cycle",
        json!({
            "phase":"feedback_received", "outcome":"findings", "review_kind":"readiness", "feedback_format":"structured_v2",
            "declared_artifact_path":"manuscript.md", "declared_artifact_hash":"c530ce34bbbbbbbb",
            "artifact_binding":"declared", "review_target":"opaque-target-hash-not-for-main-display",
            "overall_assessment":"One attribution correction and one missing source remain. 保留原始审核内容。",
            "findings":[
                {"severity":"P2", "title":"摘要把预印本连续语音识别证据表述为已发表研究",
                 "description":"This is preliminary evidence, not a peer-reviewed performance claim. 该结果不能与正式发表结果合并表述。",
                 "remediation":"Separate the publication states in the abstract and preserve the language-model attribution limit.",
                 "evidence_refs":["research/eeg-speech-decoding-review/manuscript.md:7",source]},
                {"severity":"P1", "title":"Missing source for the main comparison",
                 "description":"The comparison cites no verifiable passage.", "remediation":"Add a source passage or remove the comparison.",
                 "evidence_refs":["research/evidence.md:12"]}]
        }),
    );
    let mut snapshot = String::new();
    for width in [32, 40, 60, 80, 120, 220] {
        let cells = AuditRegionProjector::project_all(&[result.clone()], &ThemePalette::default());
        let lines = cells[0].display_hyperlink_lines(width);
        assert!(
            lines.iter().all(|line| line.line.width() <= width),
            "width={width}: {lines:?}"
        );
        let rendered = text(&cells, width);
        assert!(!rendered.contains("opaque-target"));
        assert!(!rendered.contains("P0 0"));
        assert!(rendered.find("P1  Missing").unwrap() < rendered.find("P2  ").unwrap());
        let details = cells[0].transcript_hyperlink_lines(width);
        assert!(details.iter().all(|line| line.line.width() <= width));
        assert!(details
            .iter()
            .flat_map(|line| &line.hyperlinks)
            .any(|link| link.destination == source));
        let expected_column = if width >= 40 { 6 } else { 4 };
        for line in details
            .iter()
            .take_while(|line| !line.line.to_string().contains("── Recorded details"))
        {
            for link in &line.hyperlinks {
                if link.destination == source {
                    assert_eq!(link.columns.start, expected_column, "width={width}");
                }
            }
        }
        assert!(details
            .iter()
            .flat_map(|line| &line.hyperlinks)
            .any(|link| link.destination.ends_with("/research/evidence.md#L12")));
        if width >= 40 {
            assert!(!rendered.lines().any(|line| line.is_empty()));
            assert!(rendered.contains("\n├─ OVERALL\n│ \n│     One attribution"));
        } else {
            assert!(rendered.contains("\n OVERALL\n\n  One attribution"));
        }
        snapshot.push_str(&format!("WIDTH {width}\n{rendered}\n\n"));
    }
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/snapshots/review_findings.txt");
    if std::env::var_os("UPDATE_TUI_SNAPSHOTS").is_some() {
        std::fs::write(&path, &snapshot).unwrap();
    }
    assert_eq!(std::fs::read_to_string(path).unwrap(), snapshot);
}

#[test]
fn changed_diff_keeps_audit_rails_and_width_in_normal_and_detail_views() {
    let value: serde_json::Value =
        serde_json::from_str(include_str!("../../tests/fixtures/changed_patch.json")).unwrap();
    let cell = audit_item(
        "reviewer",
        "reported-patch",
        "file_change",
        value["payload"].clone(),
    );
    let projected = AuditRegionProjector::project_all(&[cell], &ThemePalette::default());
    for width in [40, 80, 120] {
        for transcript in [false, true] {
            let lines = if transcript {
                projected[0].transcript_lines(width)
            } else {
                projected[0].display_lines(width)
            };
            assert!(lines.iter().all(|line| line.width() <= width), "{lines:?}");
            assert!(
                lines
                    .iter()
                    .skip(1)
                    .all(|line| line.to_string().starts_with('│')),
                "{lines:?}"
            );
        }
    }
}

#[test]
fn actual_review_defaults_to_clear_issues_and_keeps_original_details() {
    let original = include_str!("../../tests/fixtures/review_summary.md");
    let theme = ThemePalette::default();
    let source = audit_item(
        "reviewer",
        "summary",
        "review_cycle",
        json!({
            "phase":"completed", "outcome":"submitted", "summary":original,
            "transcript":original,
        }),
    );
    let original_copy = source.copy_text();
    for width in [20, 40, 80, 120, 220] {
        let projected = AuditRegionProjector::project_all(&[source.clone()], &theme);
        let summary = projected[0].display_hyperlink_lines(width);
        let text = summary
            .iter()
            .map(|line| line.line.to_string())
            .collect::<Vec<_>>()
            .join("\n");
        let compact: String = text
            .chars()
            .filter(|c| !c.is_whitespace() && *c != '│')
            .collect();
        assert!(summary.iter().all(|line| line.line.width() <= width));
        for title in [
            "用户无法核验正文中的关键引用",
            "NSD的“982张所有人都看过”表述把研究子集误写成数据集事实",
            "“严格零校准”定义仍不够可操作",
        ] {
            assert!(compact.contains(title), "{width}: {text}");
        }
        for hidden in ["Evidence", "Why", "§3.A", "turn...", "因果在线适配"] {
            assert!(!text.contains(hidden), "{width}: {text}");
        }
        let overall = summary
            .iter()
            .flat_map(|line| &line.line.spans)
            .find(|s| s.content == " OVERALL")
            .unwrap();
        let priority = summary
            .iter()
            .flat_map(|line| &line.line.spans)
            .find(|s| s.content == "01  P1  ")
            .unwrap();
        assert_eq!(overall.style, priority.style);
        assert!(overall
            .style
            .add_modifier
            .contains(ratatui::style::Modifier::BOLD));
        let end = summary
            .iter()
            .position(|line| line.line.to_string().contains("Review complete"))
            .unwrap();
        assert!(summary[end - 1]
            .line
            .to_string()
            .trim_matches(['│', ' '])
            .is_empty());
        assert!(!summary[end - 2]
            .line
            .to_string()
            .trim_matches(['│', ' '])
            .is_empty());
        let detail_lines = projected[0].transcript_hyperlink_lines(width);
        assert!(detail_lines.iter().all(|line| line.line.width() <= width));
        let details: String = detail_lines
            .iter()
            .map(|line| line.line.to_string())
            .collect::<Vec<_>>()
            .join("\n")
            .chars()
            .filter(|c| !c.is_whitespace() && *c != '│')
            .collect();
        for expected in [
            "Evidence",
            "Why",
            "§3.A",
            "citeturn...",
            "因果在线适配",
            "Recorded details",
        ] {
            assert!(
                details.contains(&expected.replace(' ', "")),
                "{width}: {details}"
            );
        }
        assert!(detail_lines
            .iter()
            .flat_map(|line| &line.hyperlinks)
            .all(|link| !link.destination.contains("%C2%A7")));
    }
    assert_eq!(source.copy_text(), original_copy);
    assert!(source.copy_text().unwrap().ends_with(original));
}
