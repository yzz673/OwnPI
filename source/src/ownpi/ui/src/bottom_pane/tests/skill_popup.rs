//! `$` skill-popup tests.
use super::*;
#[test]
fn dollar_skill_popup_filters_highlights_and_inserts_selected_skill() {
    let mut pane = BottomPaneState::default_with_commands();
    pane.set_skill_catalog(skill_catalog());
    pane.set_composer("$exp");

    assert!(pane.skill_visible());
    assert_eq!(
        pane.selected_skill_name().as_deref(),
        Some("expression-skill")
    );
    assert_eq!(pane.skill_popup_scroll_metrics(), Some((1, 1, 0)));
    assert_eq!(
        rendered_active_popup_lines(&pane, 44, 1),
        vec!["  Expression Skill  Communication defaults"]
    );

    let result = pane.handle_key(KeyEvent::new(KeyCode::Tab, KeyModifiers::NONE));

    assert_eq!(result, InputResult::Edited);
    assert_eq!(pane.composer(), "$expression-skill ");
    assert!(!pane.skill_visible());
}

#[test]
fn dollar_skill_popup_arrow_keys_select_skills_not_history() {
    let mut pane = BottomPaneState::default_with_commands();
    pane.record_submission("remembered prompt");
    pane.set_skill_catalog(skill_catalog());
    pane.set_composer("$");

    assert!(pane.skill_visible());
    let before = pane.selected_skill_name();
    let result = pane.handle_key(KeyEvent::new(KeyCode::Down, KeyModifiers::NONE));

    assert_eq!(result, InputResult::Edited);
    assert_ne!(pane.selected_skill_name(), before);
    assert_eq!(pane.composer(), "$");
}

#[test]
fn dollar_skill_popup_escape_dismisses_without_clearing_composer() {
    let mut pane = BottomPaneState::default_with_commands();
    pane.set_skill_catalog(skill_catalog());
    pane.set_composer("$exp");

    assert!(pane.skill_visible());
    let result = pane.handle_key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE));

    assert_eq!(result, InputResult::Edited);
    assert_eq!(pane.composer(), "$exp");
    assert!(!pane.skill_visible());
}

#[test]
fn dollar_skill_popup_reserves_chrome_and_paints_the_full_surface() {
    let mut pane = BottomPaneState::default_with_commands();
    pane.set_skill_catalog(skill_catalog());
    pane.set_composer("$");

    let theme = ThemePalette::default();
    for width in [20, 40, 80, 120] {
        let height = pane.active_popup_height(width);
        let area = Rect::new(0, 0, width, height);
        let mut buffer = Buffer::empty(area);
        pane.render_active_popup(area, &mut buffer, &theme);

        assert_eq!(buffer.get(0, 0).bg, theme.bottom_pane().input_bg);
        assert_eq!(
            buffer.get(area.width - 1, area.height - 1).bg,
            theme.bottom_pane().input_bg
        );
        assert_eq!(
            height,
            pane.skill_popup_scroll_metrics()
                .map(|(_, visible, _)| visible as u16)
                .unwrap_or_default()
                + 1
                + super::super::skill_popup::skill_popup_hint_height(
                    width.saturating_sub(4).max(1),
                )
        );
        let rendered = (0..height)
            .map(|y| {
                (0..area.width)
                    .map(|x| buffer.get(x, y).symbol())
                    .collect::<String>()
            })
            .collect::<Vec<_>>()
            .join("\n");
        assert!(rendered.contains("Enter"));
        assert!(rendered.contains("close"));
    }
}

#[test]
fn dollar_skill_popup_caps_long_names_to_keep_descriptions_visible() {
    let mut pane = BottomPaneState::default_with_commands();
    pane.set_skill_catalog(skill_catalog());
    pane.set_composer("$");

    let lines = rendered_active_popup_lines(&pane, 20, pane.active_popup_height(20));
    assert!(lines.iter().any(|line| line.contains("Cod")), "{lines:?}");
    assert!(lines.iter().any(|line| line.contains("Com")), "{lines:?}");
    assert!(
        lines
            .iter()
            .all(|line| unicode_width::UnicodeWidthStr::width(line.as_str()) <= 20),
        "{lines:?}"
    );
}
