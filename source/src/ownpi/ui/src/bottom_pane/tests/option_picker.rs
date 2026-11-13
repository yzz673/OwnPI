//! Option-picker numeric selection tests.
use super::*;
#[test]
fn option_picker_number_key_jumps_and_accepts() {
    let mut pane = BottomPaneState::default();
    pane.set_option_picker(Some(crate::option_picker::OptionPickerState::new(
        "model".to_string(),
        "Pick model".to_string(),
        None,
        vec!["alpha".to_string(), "beta".to_string(), "gamma".to_string()],
        vec![],
        vec![],
        None,
        "/model".to_string(),
    )));

    let result = pane.handle_key(KeyEvent::new(KeyCode::Char('3'), KeyModifiers::NONE));

    assert_eq!(
        result,
        InputResult::OptionPicked("/model gamma".to_string())
    );
    assert!(!pane.option_picker_is_visible());
}

#[test]
fn option_picker_number_key_out_of_range_is_ignored() {
    let mut pane = BottomPaneState::default();
    pane.set_option_picker(Some(crate::option_picker::OptionPickerState::new(
        "model".to_string(),
        "Pick model".to_string(),
        None,
        vec!["alpha".to_string()],
        vec![],
        vec![],
        None,
        "/model".to_string(),
    )));

    let result = pane.handle_key(KeyEvent::new(KeyCode::Char('9'), KeyModifiers::NONE));

    assert_eq!(result, InputResult::None);
    assert!(pane.option_picker_is_visible());
}

#[test]
fn option_picker_end_and_page_navigation_use_shared_scroll_state() {
    let mut pane = BottomPaneState::default();
    let options = (1..=20)
        .map(|index| format!("option-{index}"))
        .collect::<Vec<_>>();
    pane.set_option_picker(Some(crate::option_picker::OptionPickerState::new(
        "model".to_string(),
        "Pick model".to_string(),
        None,
        options,
        vec![],
        vec![],
        None,
        "/model".to_string(),
    )));

    assert_eq!(
        pane.handle_key(KeyEvent::new(KeyCode::PageDown, KeyModifiers::NONE)),
        InputResult::Edited
    );
    assert_eq!(
        pane.handle_key(KeyEvent::new(KeyCode::End, KeyModifiers::NONE)),
        InputResult::Edited
    );
    assert_eq!(
        pane.handle_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE)),
        InputResult::OptionPicked("/model option-20".to_string())
    );
}
