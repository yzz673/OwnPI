//! Slash-command popup and submission classification tests.
use super::*;
#[test]
fn slash_completion_writes_prefixed_command_text() {
    let mut pane = BottomPaneState::default_with_commands();
    pane.set_command_catalog(vec![ServiceTierCommand::catalog_entry(
        "1",
        "inspect-run",
        "Inspect a run",
        "ui.local",
        "local",
    )]);
    pane.set_composer("/ins");

    assert_eq!(
        pane.selected_slash_command_text().as_deref(),
        Some("/inspect-run")
    );
    assert_eq!(
        pane.complete_selected_slash().as_deref(),
        Some("/inspect-run ")
    );
    assert_eq!(pane.composer(), "/inspect-run ");
}

#[test]
fn tab_completes_selected_slash_without_submitting() {
    let mut pane = BottomPaneState::default_with_commands();
    pane.set_command_catalog(model_catalog());
    pane.set_composer("/m");

    let result = pane.handle_key(KeyEvent::new(KeyCode::Tab, KeyModifiers::NONE));

    assert_eq!(result, InputResult::Edited);
    assert_eq!(pane.composer(), "/model ");
}

#[test]
fn tab_completes_selected_slash_after_fast_typed_prefix() {
    let mut pane = BottomPaneState::default_with_commands();
    pane.set_command_catalog(model_catalog());
    for ch in "/m".chars() {
        let _ = pane.handle_key(KeyEvent::new(KeyCode::Char(ch), KeyModifiers::NONE));
    }

    let result = pane.handle_key(KeyEvent::new(KeyCode::Tab, KeyModifiers::NONE));

    assert_eq!(result, InputResult::Edited);
    assert_eq!(pane.composer(), "/model ");
}

#[test]
fn slash_popup_arrow_keys_follow_screen_direction_without_edge_wrap() {
    let mut pane = BottomPaneState::default_with_commands();
    pane.set_command_catalog(popup_catalog());
    pane.set_composer("/");

    assert_eq!(
        pane.selected_slash_command_text().as_deref(),
        Some("/model")
    );

    let result = pane.handle_key(KeyEvent::new(KeyCode::Up, KeyModifiers::NONE));

    assert_eq!(result, InputResult::Edited);
    assert_eq!(
        pane.selected_slash_command_text().as_deref(),
        Some("/model")
    );

    let result = pane.handle_key(KeyEvent::new(KeyCode::Down, KeyModifiers::NONE));

    assert_eq!(result, InputResult::Edited);
    assert_eq!(
        pane.selected_slash_command_text().as_deref(),
        Some("/models")
    );
}

#[test]
fn slash_popup_exposes_scroll_metrics_for_long_catalogs() {
    let mut pane = BottomPaneState::default_with_commands();
    let commands = (0..12)
        .map(|index| {
            ServiceTierCommand::catalog_entry(
                format!("command-{index}"),
                format!("command-{index}"),
                format!("Command {index}"),
                "ui.surface",
                "prompt",
            )
        })
        .collect();
    pane.set_command_catalog(commands);
    pane.set_composer("/");

    assert_eq!(pane.slash_popup_scroll_metrics(), Some((12, 8, 0)));

    for _ in 0..9 {
        let _ = pane.handle_key(KeyEvent::new(KeyCode::Down, KeyModifiers::NONE));
    }

    assert_eq!(pane.slash_popup_scroll_metrics(), Some((12, 8, 2)));
}

#[test]
fn slash_popup_hides_quit_alias_in_default_list_but_exact_submit_still_works() {
    let mut pane = BottomPaneState::default_with_commands();
    let mut exit =
        ServiceTierCommand::catalog_entry("exit", "exit", "Exit OwnPI", "ui.local", "local");
    exit.aliases = vec!["quit".to_string()];
    pane.set_command_catalog(vec![exit.clone()]);

    pane.set_composer("/");
    assert_eq!(pane.slash_popup_scroll_metrics(), Some((1, 1, 0)));
    assert_eq!(pane.selected_slash_command_text().as_deref(), Some("/exit"));

    pane.set_composer("/quit");
    assert_eq!(
        pane.classify_current_submission(),
        InputResult::Command(SlashCommandSubmission {
            text: "/exit".to_string(),
            command: SlashCommandItem::ServiceTier(exit),
            args: None,
        })
    );
}

#[test]
fn disabled_slash_popup_rows_are_visible_but_not_submittable() {
    let mut pane = BottomPaneState::default_with_commands();
    let mut sessions = ServiceTierCommand::catalog_entry(
        "sessions",
        "sessions",
        "List recent sessions",
        "ui.surface",
        "prompt",
    );
    sessions.disabled = true;
    sessions.disabled_reason = "Unavailable while running".to_string();
    sessions.shortcut = "Tab".to_string();
    sessions.tab = "commands".to_string();
    sessions.side_content = "session list".to_string();
    pane.set_command_catalog(vec![sessions]);

    pane.set_composer("/");
    assert_eq!(pane.slash_popup_scroll_metrics(), Some((1, 1, 0)));
    assert_eq!(pane.selected_slash_command_text(), None);

    pane.set_composer("/sessions");
    assert_eq!(
        pane.classify_current_submission(),
        InputResult::UnknownSlash("/sessions".to_string())
    );
}

#[test]
fn classify_large_paste_submits_expanded_text_not_placeholder() {
    let mut pane = BottomPaneState::default_with_commands();
    let large = "x".repeat(1001);

    pane.insert_paste(&large);

    assert_eq!(pane.composer(), "[Pasted Content 1001 chars]");
    assert_eq!(
        pane.classify_current_submission(),
        InputResult::SubmittedText(large)
    );
}

#[test]
fn classify_slash_leading_large_paste_as_plain_text() {
    let mut pane = BottomPaneState::default_with_commands();
    pane.set_command_catalog(vec![ServiceTierCommand::catalog_entry(
        "1",
        "run-task",
        "Run a task spec",
        "builtin.workflow",
        "workflow",
    )]);
    let large = format!("/run-task {}", "x".repeat(1001));

    pane.insert_paste(&large);

    assert_eq!(pane.composer(), "[Pasted Content 1011 chars]");
    assert_eq!(
        pane.classify_current_submission(),
        InputResult::SubmittedText(large)
    );
}

#[test]
fn classify_visible_slash_command_expands_large_paste_args() {
    let mut pane = BottomPaneState::default_with_commands();
    pane.set_command_catalog(vec![ServiceTierCommand::catalog_entry(
        "1",
        "run-task",
        "Run a task spec",
        "builtin.workflow",
        "workflow",
    )]);
    let large_arg = "x".repeat(1001);

    pane.set_composer("/run-task ");
    pane.insert_paste(&large_arg);

    assert_eq!(pane.composer(), "/run-task [Pasted Content 1001 chars]");
    assert_eq!(
        pane.classify_current_submission(),
        InputResult::CommandWithArgs(SlashCommandSubmission {
            text: format!("/run-task {large_arg}"),
            command: SlashCommandItem::ServiceTier(ServiceTierCommand::catalog_entry(
                "1",
                "run-task",
                "Run a task spec",
                "builtin.workflow",
                "workflow",
            )),
            args: Some(large_arg),
        })
    );
}

#[test]
fn slash_popup_renders_alias_hidden_disabled_shortcut_and_side_content() {
    let mut pane = BottomPaneState::default_with_commands();
    let exit = ServiceTierCommand::catalog_entry("exit", "exit", "Exit OwnPI", "ui.local", "local");
    let mut exit = exit;
    exit.tab = "General".to_string();
    let quit_alias_row =
        ServiceTierCommand::catalog_entry("quit", "quit", "Alias for exit", "ui.local", "local");
    let mut sessions = ServiceTierCommand::catalog_entry(
        "sessions",
        "sessions",
        "List recent sessions",
        "ui.surface",
        "prompt",
    );
    sessions.disabled = true;
    sessions.disabled_reason = "Unavailable while running".to_string();
    sessions.shortcut = "Ctrl+R".to_string();
    sessions.tab = "Session".to_string();
    sessions.side_content = "3 sessions".to_string();
    pane.set_command_catalog(vec![exit, quit_alias_row, sessions]);
    pane.set_composer("/");

    let wide = rendered_active_popup_lines(&pane, 80, 8).join("\n");
    assert!(wide.contains("General"), "{wide:?}");
    assert!(wide.contains("Session"), "{wide:?}");
    assert!(wide.contains("/exit"), "{wide:?}");
    assert!(wide.contains("/sessions"), "{wide:?}");
    assert!(
        !wide.contains("/quit"),
        "alias row must be hidden: {wide:?}"
    );
    assert!(wide.contains("Unavailable while running"), "{wide:?}");
    assert!(wide.contains("Ctrl+R"), "{wide:?}");
    assert!(wide.contains("3 sessions"), "{wide:?}");

    let narrow = rendered_active_popup_lines(&pane, 28, 8).join("\n");
    assert!(narrow.contains("/sessions"), "{narrow:?}");
    assert!(narrow.contains("Unavailable"), "{narrow:?}");
    assert!(
        narrow
            .lines()
            .all(|line| unicode_width::UnicodeWidthStr::width(line) <= 28),
        "{narrow:?}"
    );
    assert!(!narrow.contains("/quit"), "{narrow:?}");

    assert_eq!(pane.selected_slash_command_text().as_deref(), Some("/exit"));
    pane.select_next_slash();
    assert_eq!(
        pane.selected_slash_command_text().as_deref(),
        Some("/exit"),
        "disabled /sessions row should remain visible but not become selectable"
    );
}

#[test]
fn slash_popup_range_counts_commands_after_section_headers_take_rows() {
    let mut pane = BottomPaneState::default_with_commands();
    let commands = (0..12)
        .map(|index| {
            let mut command = ServiceTierCommand::catalog_entry(
                format!("command-{index}"),
                format!("command-{index}"),
                format!("Command {index}"),
                "ui.surface",
                "prompt",
            );
            command.tab = match index {
                0..=3 => "General",
                4..=7 => "Session",
                _ => "Workflow",
            }
            .to_string();
            command
        })
        .collect::<Vec<_>>();
    pane.set_command_catalog(commands);
    pane.set_composer("/");

    let height = pane.active_popup_height(40);
    let initial = rendered_active_popup_lines(&pane, 40, height).join("\n");
    assert!(initial.contains("1-6/12"), "{initial}");
    assert!(!initial.contains("1-8/12"), "{initial}");

    for _ in 0..8 {
        pane.select_next_slash();
    }
    let scrolled = rendered_active_popup_lines(&pane, 40, height).join("\n");
    assert!(scrolled.contains("4-9/12"), "{scrolled}");
    assert!(scrolled.contains("/command-8"), "{scrolled}");
}

#[test]
fn classify_partial_popup_selection_as_bare_slash_command() {
    let mut pane = BottomPaneState::default_with_commands();
    pane.set_command_catalog(model_catalog());
    pane.set_composer("/m");

    assert_eq!(
        pane.classify_current_submission(),
        InputResult::Command(SlashCommandSubmission {
            text: "/model".to_string(),
            command: SlashCommandItem::ServiceTier(ServiceTierCommand::catalog_entry(
                "model",
                "model",
                "Pick model",
                "ui.popup",
                "prompt",
            )),
            args: None,
        })
    );
}

#[test]
fn classify_inline_slash_command_keeps_args() {
    let mut pane = BottomPaneState::default_with_commands();
    pane.set_command_catalog(vec![ServiceTierCommand::catalog_entry(
        "1",
        "run-task",
        "Run a task spec",
        "builtin.workflow",
        "workflow",
    )]);
    pane.set_composer("/run-task temp/demo.yaml");

    assert_eq!(
        pane.classify_current_submission(),
        InputResult::CommandWithArgs(SlashCommandSubmission {
            text: "/run-task temp/demo.yaml".to_string(),
            command: SlashCommandItem::ServiceTier(ServiceTierCommand::catalog_entry(
                "1",
                "run-task",
                "Run a task spec",
                "builtin.workflow",
                "workflow",
            )),
            args: Some("temp/demo.yaml".to_string()),
        })
    );
}

#[test]
fn surface_commands_are_available_without_working_state() {
    let mut pane = BottomPaneState::default_with_commands();
    pane.set_command_catalog(vec![ServiceTierCommand::catalog_entry(
        "1",
        "models",
        "List configured LLM profiles and candidate models",
        "ui.surface",
        "prompt",
    )]);
    pane.set_composer("/models");

    assert_eq!(
        pane.classify_current_submission(),
        InputResult::Command(SlashCommandSubmission {
            text: "/models".to_string(),
            command: SlashCommandItem::ServiceTier(ServiceTierCommand::catalog_entry(
                "1",
                "models",
                "List configured LLM profiles and candidate models",
                "ui.surface",
                "prompt",
            )),
            args: None,
        })
    );
}

#[test]
fn classify_leading_absolute_path_as_plain_prompt() {
    let mut pane = BottomPaneState::default_with_commands();
    pane.set_command_catalog(model_catalog());
    let prompt = "/Users/gaoruizhang/Downloads/wuhan_data 请你对这个里面的数据进行EDA";
    pane.set_composer(prompt);

    assert_eq!(
        pane.classify_current_submission(),
        InputResult::SubmittedText(prompt.to_string())
    );
}

#[test]
fn slash_escape_dismisses_popup_without_clearing_draft() {
    let mut pane = BottomPaneState::default_with_commands();
    pane.set_command_catalog(model_catalog());
    pane.set_composer("/m");

    assert!(pane.slash_visible());
    assert_eq!(pane.handle_escape(), EscapeAction::CloseSlashPopup);

    assert_eq!(pane.composer(), "/m");
    assert!(!pane.slash_visible());

    pane.set_composer("/mo");
    assert_eq!(pane.composer(), "/mo");
    assert!(pane.slash_visible());
}
