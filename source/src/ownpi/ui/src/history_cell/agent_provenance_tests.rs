use super::*;
use serde_json::json;

#[test]
fn citation_correction_displays_current_error_and_preserves_raw_diagnostics() {
    let current = "citation_resolve is not available in this turn.";
    let old = "Citation transport is missing its permission context";
    let item = crate::display_item::DisplayItem::parse_value(&serde_json::json!({
        "schema_version": "ownpi.tui.item.v1", "kind": "command_execution",
        "item_id": "tool:a:correction:2", "call_id": "a", "correction_of": "tool:a",
        "evidence_revision": 2, "tool_name": "citation_resolve", "status": "failed",
        "action": "resolve_citation", "visibility": "user",
        "payload": {"command": "citation_resolve", "result_state": "failed",
            "requested_title": "Example paper", "error_summary": current,
            "transcript": format!("{old}\n{current}")}
    }))
    .unwrap();
    let cell = TypedThreadItemHistoryCell::new(item, ThemePalette::default());
    let lines = cell
        .display_lines(100)
        .iter()
        .map(|line| {
            line.spans
                .iter()
                .map(|span| span.content.as_ref())
                .collect::<String>()
        })
        .collect::<Vec<_>>();
    assert_eq!(
        lines,
        vec![
            "Correction to earlier tool result",
            "✗ Citation lookup failed",
            "  └ Requested Example paper",
            "  └ Error citation_resolve is not available in this turn."
        ]
    );
    assert!(cell.raw_text().unwrap_or_default().contains(old));
}

#[test]
fn readiness_cjk_provenance_respects_width_and_replay() {
    let text = "已找到多个同主题文件以及独立的参考文献与审计材料。现在只读取本轮声明的主稿及其核验台账，确认稿件完整、引用闭合、来源可追溯。\n\n不会把历史草稿误当作交付物。";
    let cell = AgentMarkdownHistoryCell::new(text.to_string(), ThemePalette::default())
        .with_agent_context(Some(crate::agent_provenance::AgentMessageContext {
            stage: "literature_grounding".to_string(),
            attempt: 1,
            component: "readiness".to_string(),
            operation: String::new(),
            round_id: String::new(),
        }));
    for width in [80, 120, 220] {
        let lines = cell.display_lines(width);
        assert!(
            lines.iter().all(|line| line.width() <= width),
            "overflow at {width}: {lines:?}"
        );
        assert_eq!(lines, cell.transcript_lines(width));
        assert_eq!(cell.copy_text().as_deref(), Some(text));
        // Prefix chunks exercise streaming Markdown without mutating its source.
        for end in text
            .char_indices()
            .map(|(offset, _)| offset)
            .filter(|offset| *offset > 0)
        {
            let live = render_agent_live_response_with_cwd(
                &text[..end],
                width,
                &ThemePalette::default(),
                None,
            );
            assert!(live.iter().all(|line| line.width() <= width));
        }
        let actual = lines
            .iter()
            .map(|line| {
                line.spans
                    .iter()
                    .map(|span| span.content.as_ref())
                    .collect::<String>()
            })
            .collect::<Vec<_>>()
            .join("\n");
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join(format!("tests/snapshots/readiness_cjk_{width}.txt"));
        if std::env::var_os("UPDATE_UI_SNAPSHOTS").is_some() {
            std::fs::write(&path, &actual).unwrap();
        }
        assert_eq!(actual, std::fs::read_to_string(path).unwrap());
    }
}

#[test]
fn web_actions_keep_names_and_move_receipt_coverage_to_details() {
    for (action, title) in [
        ("search", "Searched the web"),
        ("open_page", "Opened page"),
        ("find_in_page", "Searched page"),
    ] {
        for (evidence, expected) in [
            ("metadata_only", "Page text not included in receipt"),
            (
                "sources",
                "Source links included · page text not included in receipt",
            ),
            ("excerpt", "Page excerpt included in receipt"),
            (
                "abstract",
                "Abstract included in receipt · full text not included",
            ),
            ("unknown", "Content availability not recorded"),
        ] {
            let item = crate::display_item::DisplayItem::parse_value(&json!({
            "schema_version":"ownpi.tui.item.v1", "item_id":"web", "kind":"web_search",
            "status":"completed", "visibility":"user", "tool_name":"web_search_call",
            "raw_source":"original receipt", "payload":{"web_action":{"type":action,
                "url":"https://example.org/paper", "query":"three datasets", "pattern":"three datasets"},
            "evidence":{"availability":evidence}, "transcript":"original receipt"}
        })).unwrap();
            let cell = TypedThreadItemHistoryCell::new(item, ThemePalette::default());
            let full = cell
                .display_lines(100)
                .iter()
                .map(|l| l.to_string())
                .collect::<Vec<_>>()
                .join("\n");
            assert!(full.contains(title), "{full}");
            assert!(!full.contains(expected), "{full}");
            assert!(!full.contains("Web action completed"), "{full}");
            assert_eq!(full.lines().count(), 1, "{full}");
            let details = cell
                .transcript_lines(100)
                .iter()
                .map(|l| l.to_string())
                .collect::<Vec<_>>()
                .join("\n");
            assert!(details.contains(expected), "{details}");
            assert!(details.contains(title), "{details}");
            assert!(details.contains("original receipt"), "{details}");
            assert_eq!(cell.raw_text().unwrap(), "original receipt");
            for width in [32, 40, 80, 120] {
                assert!(cell.display_lines(width).iter().all(|l| l.width() <= width));
                assert!(cell
                    .transcript_lines(width)
                    .iter()
                    .all(|l| l.width() <= width));
            }
        }
    }
}

#[test]
fn web_receipt_details_remain_available_without_a_raw_transcript() {
    let item = crate::display_item::DisplayItem::parse_value(&json!({
        "schema_version":"ownpi.tui.item.v1", "item_id":"web", "kind":"web_search",
        "status":"completed", "visibility":"user", "tool_name":"web_search_call",
        "payload":{"web_action":{"type":"open_page"}, "evidence":{"availability":"metadata_only"}}
    }))
    .unwrap();
    let cell = TypedThreadItemHistoryCell::new(item, ThemePalette::default());
    let display = cell
        .display_lines(100)
        .iter()
        .map(|l| l.to_string())
        .collect::<Vec<_>>()
        .join("\n");
    let details = cell
        .transcript_lines(100)
        .iter()
        .map(|l| l.to_string())
        .collect::<Vec<_>>()
        .join("\n");
    assert!(display.contains("Opened page"));
    assert!(display.contains("Target URL not recorded"));
    assert!(!display.contains("Page text not included"));
    assert!(details.contains("Page text not included in receipt"));
}

#[test]
fn web_errors_stay_visible_when_receipt_coverage_moves_to_details() {
    for (action, title) in [
        ("search", "Failed to search the web"),
        ("open_page", "Failed to open page"),
        ("find_in_page", "Failed to search page"),
    ] {
        let item = crate::display_item::DisplayItem::parse_value(&json!({
            "schema_version":"ownpi.tui.item.v1", "item_id":"web", "kind":"web_search",
            "status":"failed", "visibility":"user", "tool_name":"web_search_call",
            "payload":{"web_action":{"type":action, "url":"https://example.org/paper"},
                "evidence":{"availability":"metadata_only"}, "error_summary":"HTTP 403"}
        }))
        .unwrap();
        let cell = TypedThreadItemHistoryCell::new(item, ThemePalette::default());
        let display = cell
            .display_lines(100)
            .iter()
            .map(|l| l.to_string())
            .collect::<Vec<_>>()
            .join("\n");
        assert!(display.contains(title), "{display}");
        assert!(display.contains("HTTP 403"), "{display}");
        assert!(!display.contains("Page text not included"), "{display}");
    }
}
