//! Overlay routing tests: human-question interactions install into the dedicated view.
use super::*;
fn question_overlay(interaction_kind: HumanInteractionKind) -> PendingInteractionState {
    PendingInteractionState::Human(HumanInteractionState {
        interaction_id: format!("interaction:{}", interaction_kind.as_wire_str()),
        title: "Question".to_string(),
        interaction_kind,
        questions: vec![ClarificationQuestion {
            question_id: "q1".to_string(),
            header: "Header".to_string(),
            question: "Pick one.".to_string(),
            required: true,
            options: vec![PendingOption {
                id: "opt-a".to_string(),
                label: "Option A".to_string(),
                description: "First option.".to_string(),
            }],
            ..Default::default()
        }],
        ..Default::default()
    })
}

#[test]
fn human_question_installs_into_dedicated_view_not_approval_overlay() {
    let mut pane = BottomPaneState::default();

    pane.set_overlay(Some(question_overlay(HumanInteractionKind::HumanQuestion)));

    assert!(
        pane.human_question_view().is_some(),
        "human_question must install into the dedicated view state"
    );
    assert!(
        pane.approval_overlay().is_none(),
        "human_question must not occupy the generic approval overlay"
    );
    assert!(
        pane.active_human_is_question(),
        "dedicated view must report an active human question"
    );
    assert_eq!(
        pane.active_view_stack_state(),
        (Some(BottomPaneViewKind::HumanQuestion), 1)
    );
    assert!(
        matches!(
            pane.overlay(),
            Some(ActiveInteractionRef::Human(interaction))
                if interaction.interaction_id == "interaction:human_question"
        ),
        "overlay() must transparently expose the dedicated view interaction"
    );
}

#[test]
fn modal_boundary_ignores_release_events_and_accepts_repeat_events() {
    let mut interaction = match question_overlay(HumanInteractionKind::HumanQuestion) {
        PendingInteractionState::Human(interaction) => interaction,
        PendingInteractionState::Permission(_) => unreachable!(),
    };
    interaction.questions[0].options.push(PendingOption {
        id: "opt-b".to_string(),
        label: "Option B".to_string(),
        description: "Second option.".to_string(),
    });
    let mut pane = BottomPaneState::default();
    pane.set_overlay(Some(PendingInteractionState::Human(interaction)));

    let release = KeyEvent::new_with_kind(
        KeyCode::Down,
        KeyModifiers::NONE,
        crossterm::event::KeyEventKind::Release,
    );
    assert_eq!(pane.handle_key(release), InputResult::None);
    assert_eq!(
        pane.human_question_view()
            .expect("human question view")
            .human()
            .option_cursor(),
        0
    );

    let repeat = KeyEvent::new_with_kind(
        KeyCode::Down,
        KeyModifiers::NONE,
        crossterm::event::KeyEventKind::Repeat,
    );
    assert_eq!(pane.handle_key(repeat), InputResult::Edited);
    assert_eq!(
        pane.human_question_view()
            .expect("human question view")
            .human()
            .option_cursor(),
        1
    );
}

#[test]
fn human_question_jk_navigation_and_delete_match_codex_option_semantics() {
    let mut interaction = match question_overlay(HumanInteractionKind::HumanQuestion) {
        PendingInteractionState::Human(interaction) => interaction,
        PendingInteractionState::Permission(_) => unreachable!(),
    };
    interaction.questions[0].options.push(PendingOption {
        id: "opt-b".to_string(),
        label: "Option B".to_string(),
        description: "Second option.".to_string(),
    });
    let mut pane = BottomPaneState::default();
    pane.set_overlay(Some(PendingInteractionState::Human(interaction)));

    assert_eq!(
        pane.handle_key(KeyEvent::new(KeyCode::Char('j'), KeyModifiers::NONE)),
        InputResult::Edited
    );
    assert_eq!(
        pane.human_question_view()
            .expect("human question view")
            .human()
            .option_cursor(),
        1
    );
    assert_eq!(
        pane.handle_key(KeyEvent::new(KeyCode::Char(' '), KeyModifiers::NONE)),
        InputResult::Edited
    );
    assert!(pane
        .human_question_view()
        .expect("human question view")
        .human()
        .draft_for("q1")
        .has_value());

    assert_eq!(
        pane.handle_key(KeyEvent::new(KeyCode::Delete, KeyModifiers::NONE)),
        InputResult::Edited
    );
    let human = pane
        .human_question_view()
        .expect("human question view")
        .human();
    assert!(!human.draft_for("q1").has_value());
    assert_eq!(human.focus, HumanQuestionFocus::Options);

    assert_eq!(
        pane.handle_key(KeyEvent::new(KeyCode::Char('k'), KeyModifiers::NONE)),
        InputResult::Edited
    );
    assert_eq!(
        pane.human_question_view()
            .expect("human question view")
            .human()
            .option_cursor(),
        0
    );

    assert_eq!(
        pane.handle_key(KeyEvent::new(KeyCode::Tab, KeyModifiers::NONE)),
        InputResult::Edited
    );
    assert_eq!(
        pane.human_question_view()
            .expect("human question view")
            .human()
            .focus,
        HumanQuestionFocus::Notes
    );
    assert_eq!(
        pane.handle_key(KeyEvent::new(KeyCode::Down, KeyModifiers::NONE)),
        InputResult::Edited
    );
    assert_eq!(
        pane.human_question_view()
            .expect("human question view")
            .human()
            .option_cursor(),
        1
    );
}

#[test]
fn human_question_option_scroll_state_tracks_cursor_without_full_repaint() {
    let mut interaction = match question_overlay(HumanInteractionKind::HumanQuestion) {
        PendingInteractionState::Human(interaction) => interaction,
        PendingInteractionState::Permission(_) => unreachable!(),
    };
    interaction.questions[0].options = (1..=12)
        .map(|index| PendingOption {
            id: format!("opt-{index}"),
            label: format!("Option {index}"),
            description: format!("Description {index}"),
        })
        .collect();
    let mut pane = BottomPaneState::default();
    pane.set_overlay(Some(PendingInteractionState::Human(interaction)));

    for _ in 0..9 {
        assert_eq!(
            pane.handle_key(KeyEvent::new(KeyCode::Down, KeyModifiers::NONE)),
            InputResult::Edited
        );
    }
    let human = pane
        .human_question_view()
        .expect("human question view")
        .human();
    assert_eq!(human.option_cursor(), 9);
    assert_eq!(human.option_scroll_top(), 2);
}

#[test]
fn request_user_input_kind_also_routes_to_dedicated_view() {
    let mut pane = BottomPaneState::default();

    pane.set_overlay(Some(question_overlay(
        HumanInteractionKind::RequestUserInput,
    )));

    assert!(pane.human_question_view().is_some());
    assert!(pane.approval_overlay().is_none());
    assert!(pane.active_human_is_question());
}

#[test]
fn human_clarification_uses_the_dedicated_human_question_state_machine() {
    let mut pane = BottomPaneState::default();

    pane.set_overlay(Some(question_overlay(
        HumanInteractionKind::HumanClarification,
    )));

    assert!(pane.human_question_view().is_some());
    assert!(pane.approval_overlay().is_none());
    assert!(pane.human_question_bottom_view_is_visible());
    assert!(pane.active_human_is_question());
}

#[test]
fn plan_approval_uses_the_dedicated_human_question_state_machine() {
    let mut pane = BottomPaneState::default();

    pane.set_overlay(Some(question_overlay(HumanInteractionKind::PlanApproval)));

    assert!(pane.human_question_view().is_some());
    assert!(pane.approval_overlay().is_none());
    assert!(pane.human_question_bottom_view_is_visible());
    assert!(pane.active_human_is_question());
}

#[test]
fn permission_interaction_uses_generic_approval_overlay() {
    let mut pane = BottomPaneState::default();

    pane.set_overlay(Some(PendingInteractionState::Permission(
        PermissionInteractionState {
            interaction_id: "interaction:permission".to_string(),
            ..Default::default()
        },
    )));

    assert!(pane.human_question_view().is_none());
    assert!(pane.approval_overlay().is_some());
    assert_eq!(
        pane.active_view_stack_state(),
        (Some(BottomPaneViewKind::Permission), 1)
    );
    assert!(
        matches!(
            pane.overlay(),
            Some(ActiveInteractionRef::Permission(interaction))
                if interaction.interaction_id == "interaction:permission"
        ),
        "overlay() must expose the permission interaction"
    );
}

#[test]
fn modal_transitions_keep_exactly_one_active_input_owner() {
    let mut pane = BottomPaneState::default();
    pane.set_selection_view(Some(SelectionViewState::new(
        "test.selection".to_string(),
        "Select".to_string(),
        None,
        vec![SelectionViewRow::default()],
        None,
        None,
        None,
    )));
    assert_eq!(
        pane.active_view_stack_state(),
        (Some(BottomPaneViewKind::Selection), 1)
    );

    pane.set_option_picker(Some(OptionPickerState::new(
        "model".to_string(),
        "Pick".to_string(),
        None,
        vec!["A".to_string()],
        vec!["a".to_string()],
        vec!["Option A".to_string()],
        None,
        "/model".to_string(),
    )));
    assert_eq!(
        pane.active_view_stack_state(),
        (Some(BottomPaneViewKind::OptionPicker), 1)
    );

    pane.set_overlay(Some(question_overlay(HumanInteractionKind::HumanQuestion)));
    assert_eq!(
        pane.active_view_stack_state(),
        (Some(BottomPaneViewKind::HumanQuestion), 1)
    );
    assert!(pane.terminal_title_requires_action());

    pane.clear_overlay();
    assert_eq!(pane.active_view_stack_state(), (None, 0));
}

#[test]
fn picker_replaces_command_document_without_stale_render_owner() {
    let mut pane = BottomPaneState::default();
    pane.set_command_document(Some(CommandDocumentViewState::new(
        "command_catalog".to_string(),
        "Help".to_string(),
        "/commands".to_string(),
        "# Help".to_string(),
        "ui.surface".to_string(),
    )));
    pane.set_option_picker(Some(OptionPickerState::new(
        "model".to_string(),
        "Model".to_string(),
        None,
        vec!["gpt-5.5".to_string()],
        Vec::new(),
        Vec::new(),
        None,
        "/model".to_string(),
    )));

    assert!(!pane.command_document_is_visible());
    assert!(!pane.command_document_is_visible());
    assert_eq!(
        pane.active_view_stack_state(),
        (Some(BottomPaneViewKind::OptionPicker), 1)
    );
}

#[test]
fn ordinary_picker_cannot_steal_input_from_active_interaction_view() {
    let mut pane = BottomPaneState::default();
    pane.set_overlay(Some(question_overlay(HumanInteractionKind::HumanQuestion)));

    pane.set_selection_view(Some(SelectionViewState::new(
        "late.selection".to_string(),
        "Late picker".to_string(),
        None,
        Vec::new(),
        None,
        None,
        None,
    )));
    pane.set_option_picker(Some(OptionPickerState::new(
        "model".to_string(),
        "Late model picker".to_string(),
        None,
        vec!["A".to_string()],
        vec!["a".to_string()],
        Vec::new(),
        None,
        "/model".to_string(),
    )));

    assert_eq!(
        pane.active_view_stack_state(),
        (Some(BottomPaneViewKind::HumanQuestion), 1)
    );
    assert!(pane.human_question_bottom_view_is_visible());
    assert!(!pane.selection_view_is_visible());
    assert!(!pane.option_picker_is_visible());
}

#[test]
fn late_command_views_cannot_replace_human_question_or_permission_owners() {
    for interaction in [
        question_overlay(HumanInteractionKind::HumanQuestion),
        PendingInteractionState::Permission(PermissionInteractionState {
            interaction_id: "interaction:permission".to_string(),
            ..Default::default()
        }),
    ] {
        let mut pane = BottomPaneState::default();
        pane.set_overlay(Some(interaction));
        let owner_before = pane.active_view_stack_state();

        pane.set_command_browser(Some(CommandBrowserViewState::new(vec![
            ServiceTierCommand::catalog_entry("help", "help", "Show help", "ui.surface", "prompt"),
        ])));

        assert_eq!(pane.active_view_stack_state(), owner_before);
        assert!(pane.overlay().is_some());
        assert!(!pane.command_browser_is_visible());
    }
}

#[test]
fn empty_picker_payloads_do_not_claim_input_ownership() {
    let mut pane = BottomPaneState::default();
    pane.set_selection_view(Some(SelectionViewState::new(
        "empty.selection".to_string(),
        "Nothing here".to_string(),
        None,
        Vec::new(),
        None,
        None,
        None,
    )));
    assert_eq!(pane.active_view_stack_state(), (None, 0));
    assert!(pane.no_modal_or_popup_active());

    pane.set_option_picker(Some(OptionPickerState::new(
        "empty.options".to_string(),
        "Nothing here".to_string(),
        None,
        Vec::new(),
        Vec::new(),
        Vec::new(),
        None,
        "/model".to_string(),
    )));
    assert_eq!(pane.active_view_stack_state(), (None, 0));
    assert!(pane.no_modal_or_popup_active());
}
