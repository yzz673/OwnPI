//! `@` file-popup tests.
use super::*;
#[test]
fn at_file_popup_inserts_selected_path_with_tab() {
    let mut pane = BottomPaneState::default_with_commands();
    pane.set_composer("@main");
    assert_eq!(pane.take_file_search_request().as_deref(), Some("main"));
    assert!(pane.file_visible());
    assert!(pane.file_popup_is_waiting());
    assert!(pane.selected_file_path().is_none());
    assert!(pane.apply_file_search_result("main", file_matches("main")));

    assert!(pane.file_visible());
    let selected = pane.selected_file_path().unwrap();
    assert_eq!(selected, "src/main.rs");

    let result = pane.handle_key(KeyEvent::new(KeyCode::Tab, KeyModifiers::NONE));

    assert_eq!(result, InputResult::Edited);
    assert_eq!(pane.composer(), format!("{selected} "));
}

#[test]
fn at_file_popup_arrow_keys_select_files_not_history() {
    let mut pane = BottomPaneState::default_with_commands();
    pane.record_submission("remembered prompt");
    pane.set_composer("@n");
    assert_eq!(pane.take_file_search_request().as_deref(), Some("n"));
    assert!(pane.apply_file_search_result("n", file_matches("n")));

    assert!(pane.file_popup_scroll_metrics().is_some());
    let before = pane.selected_file_path();
    let result = pane.handle_key(KeyEvent::new(KeyCode::Down, KeyModifiers::NONE));

    assert_eq!(result, InputResult::Edited);
    assert_ne!(pane.selected_file_path(), before);
    assert_eq!(pane.composer(), "@n");
}

#[test]
fn at_file_popup_keeps_and_scrolls_results_beyond_first_page() {
    let mut pane = BottomPaneState::default_with_commands();
    pane.set_composer("@file");
    assert_eq!(pane.take_file_search_request().as_deref(), Some("file"));
    let matches = (0..12)
        .map(|index| FileMatch {
            path: format!("src/file-{index:02}.rs"),
            is_dir: false,
            match_indices: None,
        })
        .collect::<Vec<_>>();
    assert!(pane.apply_file_search_result("file", matches));
    assert_eq!(pane.file_popup_scroll_metrics(), Some((12, 8, 0)));

    for _ in 0..8 {
        assert_eq!(
            pane.handle_key(KeyEvent::new(KeyCode::Down, KeyModifiers::NONE)),
            InputResult::Edited
        );
    }

    assert_eq!(pane.selected_file_path().as_deref(), Some("src/file-08.rs"));
    assert_eq!(pane.file_popup_scroll_metrics(), Some((12, 8, 1)));
    let height = pane.active_popup_height(40);
    let rendered = rendered_active_popup_lines(&pane, 40, height).join("\n");
    assert!(rendered.contains("2-9/12"), "{rendered}");

    let constrained = rendered_active_popup_lines(&pane, 40, 6).join("\n");
    assert!(constrained.contains("6-9/12"), "{constrained}");
    assert!(!constrained.contains("2-9/12"), "{constrained}");

    let narrow_content_only = rendered_active_popup_lines(&pane, 20, 3).join("\n");
    assert!(
        narrow_content_only.contains("src/file-08.rs"),
        "{narrow_content_only}"
    );
    assert!(
        !narrow_content_only.contains("/12"),
        "{narrow_content_only}"
    );
}

#[test]
fn at_file_popup_enter_inserts_and_quotes_whitespace_path() {
    let mut pane = BottomPaneState::default_with_commands();
    pane.set_composer("@space");
    assert_eq!(pane.take_file_search_request().as_deref(), Some("space"));
    assert!(pane.apply_file_search_result("space", file_matches("space")));

    assert!(pane.file_visible());
    let selected = pane.selected_file_path().unwrap();
    assert_eq!(selected, "space file.md");

    let result = pane.handle_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));

    assert_eq!(result, InputResult::Edited);
    assert_eq!(pane.composer(), format!("\"{selected}\" "));
}

#[test]
fn at_file_popup_escape_dismisses_current_token_only() {
    let mut pane = BottomPaneState::default_with_commands();
    pane.set_composer("@main");
    assert_eq!(pane.take_file_search_request().as_deref(), Some("main"));

    assert!(pane.file_visible());
    let result = pane.handle_key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE));

    assert_eq!(result, InputResult::Edited);
    assert_eq!(pane.composer(), "@main");
    assert!(!pane.file_visible());

    pane.set_composer("@note");
    assert!(pane.file_visible());
}

#[test]
fn at_file_popup_empty_token_shows_prompt_without_matches() {
    let mut pane = BottomPaneState::default_with_commands();
    pane.set_composer("@");

    assert_eq!(pane.take_file_search_request().as_deref(), Some(""));
    assert!(pane.file_visible());
    assert!(pane.file_popup_is_empty_prompt());
    assert_eq!(pane.selected_file_path(), None);
}

#[test]
fn at_file_popup_renders_loading_empty_and_no_match_states() {
    let mut pane = BottomPaneState::default_with_commands();
    pane.set_composer("@main");
    assert_eq!(pane.take_file_search_request().as_deref(), Some("main"));

    assert_eq!(
        rendered_active_popup_lines(&pane, 32, 1),
        vec!["  loading..."]
    );

    pane.set_composer("@");
    assert_eq!(pane.take_file_search_request().as_deref(), Some(""));
    assert_eq!(
        rendered_active_popup_lines(&pane, 32, 1),
        vec!["  type to search files"]
    );

    pane.set_composer("@missing");
    assert_eq!(pane.take_file_search_request().as_deref(), Some("missing"));
    assert!(pane.apply_file_search_result("missing", Vec::new()));
    assert!(pane.file_visible());
    assert_eq!(pane.selected_file_path(), None);
    assert_eq!(
        rendered_active_popup_lines(&pane, 32, 1),
        vec!["  no matches"]
    );
}

#[test]
fn at_file_popup_switching_query_clears_old_rows_while_loading() {
    let mut pane = BottomPaneState::default_with_commands();
    pane.set_composer("@main");
    assert_eq!(pane.take_file_search_request().as_deref(), Some("main"));
    assert!(pane.apply_file_search_result("main", file_matches("main")));
    assert_eq!(
        rendered_active_popup_lines(&pane, 32, 1),
        vec!["  src/main.rs"]
    );

    pane.set_composer("@note");
    assert_eq!(pane.take_file_search_request().as_deref(), Some("note"));

    assert!(pane.file_popup_is_waiting());
    assert_eq!(pane.selected_file_path(), None);
    assert_eq!(
        rendered_active_popup_lines(&pane, 32, 1),
        vec!["  loading..."]
    );
}

#[test]
fn at_file_popup_ignores_stale_result_for_old_query() {
    let mut pane = BottomPaneState::default_with_commands();
    pane.set_composer("@ma");
    assert_eq!(pane.take_file_search_request().as_deref(), Some("ma"));
    pane.set_composer("@main");
    assert_eq!(pane.take_file_search_request().as_deref(), Some("main"));

    assert!(!pane.apply_file_search_result("ma", file_matches("main")));
    assert_eq!(pane.selected_file_path(), None);

    assert!(pane.apply_file_search_result("main", file_matches("main")));
    assert_eq!(pane.selected_file_path().as_deref(), Some("src/main.rs"));
}

#[test]
fn at_file_popup_ignores_result_after_current_token_is_removed() {
    let mut pane = BottomPaneState::default_with_commands();
    pane.set_composer("@main");
    assert_eq!(pane.take_file_search_request().as_deref(), Some("main"));

    pane.set_composer("plain text");
    assert_eq!(pane.take_file_search_request().as_deref(), Some(""));

    assert!(!pane.apply_file_search_result("main", file_matches("main")));
    assert!(!pane.file_visible());
    assert_eq!(pane.selected_file_path(), None);
}
