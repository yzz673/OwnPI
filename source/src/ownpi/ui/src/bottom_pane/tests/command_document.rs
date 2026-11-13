use super::*;

fn document() -> CommandDocumentViewState {
    CommandDocumentViewState::new(
        "runtime_inspector".to_string(),
        "Context".to_string(),
        "/runs".to_string(),
        (0..30)
            .map(|index| {
                format!(
                    "- Run {index}: /Users/example/.neuro-agent/sessions/session-123/tasks/task/runs/run-456"
                )
            })
            .collect::<Vec<_>>()
            .join("\n"),
        "ui.surface".to_string(),
    )
}

fn render(pane: &BottomPaneState, width: u16, height: u16) -> String {
    let area = Rect::new(0, 0, width, height);
    let mut buffer = Buffer::empty(area);
    pane.render_bottom_view(area, &mut buffer, &ThemePalette::default());
    (0..height)
        .map(|y| {
            (0..width)
                .map(|x| buffer.get(x, y).symbol())
                .collect::<String>()
                .trim_end()
                .to_string()
        })
        .collect::<Vec<_>>()
        .join("\n")
}

#[test]
fn command_document_owns_keys_scrolls_and_closes() {
    let mut pane = BottomPaneState::default();
    pane.set_command_document(Some(document()));
    let before = render(&pane, 40, 18);

    assert!(pane.command_document_is_visible());
    assert!(before.contains("Context"));
    assert!(before.contains("Runtime inspector"));
    assert_eq!(
        pane.handle_key(KeyEvent::new(KeyCode::PageDown, KeyModifiers::NONE)),
        InputResult::Edited
    );
    let after = render(&pane, 40, 18);
    assert_ne!(before, after);
    assert_eq!(pane.handle_escape(), EscapeAction::CloseCommandDocument);
    assert!(!pane.command_document_is_visible());
}

#[test]
fn command_document_reflows_at_supported_widths_without_replacing_view() {
    let mut pane = BottomPaneState::default();
    pane.set_command_document(Some(document()));

    for width in [40, 80, 120] {
        let rendered = render(&pane, width, 20);
        assert!(rendered.contains("Context"), "width={width}");
        assert!(rendered.contains("/runs"), "width={width}");
        assert_eq!(
            pane.active_view_stack_state(),
            (Some(BottomPaneViewKind::CommandDocument), 1),
            "width={width}"
        );
    }
}
