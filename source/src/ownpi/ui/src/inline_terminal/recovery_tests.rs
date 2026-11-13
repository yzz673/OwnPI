use super::*;
use crate::bottom_pane::{ApprovalOverlayState, PendingOption, PermissionInteractionState};
use crate::theme::ThemePalette;
use ratatui::text::Text;
use ratatui::widgets::Paragraph;
use std::{any::Any, cell::RefCell, rc::Rc};

#[derive(Clone)]
struct Capture(Rc<RefCell<vt100::Parser>>, Rc<RefCell<Vec<u8>>>);

impl Write for Capture {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        self.0.borrow_mut().process(bytes);
        self.1.borrow_mut().extend_from_slice(bytes);
        Ok(bytes.len())
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

#[derive(Debug)]
struct Outcome(&'static str);
impl HistoryCell for Outcome {
    fn display_lines(&self, _width: usize) -> Vec<Line<'static>> {
        vec![Line::from(self.0)]
    }
    fn raw_lines(&self) -> Vec<Line<'static>> {
        vec![Line::from(self.0)]
    }
    fn as_any(&self) -> &dyn Any {
        self
    }
}

fn terminal(width: u16, height: u16) -> (InlineTerminal<Capture>, Capture) {
    let capture = Capture(
        Rc::new(RefCell::new(vt100::Parser::new(height, width, 1000))),
        Rc::default(),
    );
    let terminal = InlineTerminal {
        backend: CrosstermBackend::new(capture.clone()),
        viewport_area: Rect::new(0, 1, width, height - 1),
        screen_size: Size::new(width, height),
        previous_buffer: None,
        cursor_position: None,
        last_known_cursor_pos: Position::new(0, 1),
        pending_history_cells: Vec::new(),
        last_history_cell: None,
        cursor_hidden: false,
        alternate_screen_active: false,
        history_render_mode: HistoryRenderMode::Rich,
    };
    (terminal, capture)
}

fn recovery() -> ApprovalOverlayState {
    ApprovalOverlayState::new(PermissionInteractionState {
        approval_kind: "provider_recovery".into(),
        title: "Provider request failed".into(),
        reason: "The provider exhausted its in-turn retries. The durable run can be resumed."
            .into(),
        request_summary: "Stage · literature_grounding".into(),
        permission_rule: "resume this durable task run".into(),
        options: vec![
            PendingOption {
                id: "retry_stage".into(),
                label: "Retry current stage".into(),
                description: "Resume the same durable run.".into(),
            },
            PendingOption {
                id: "end_task".into(),
                label: "End task".into(),
                description: "Preserve existing artifacts.".into(),
            },
        ],
        ..Default::default()
    })
}

fn all_contents(capture: &Capture) -> String {
    let mut parser = capture.0.borrow_mut();
    parser.screen_mut().set_scrollback(usize::MAX);
    let count = parser.screen().scrollback();
    let mut rows = Vec::new();
    for offset in (1..=count).rev() {
        parser.screen_mut().set_scrollback(offset);
        rows.push(
            parser
                .screen()
                .contents()
                .lines()
                .next()
                .unwrap_or("")
                .to_string(),
        );
    }
    parser.screen_mut().set_scrollback(0);
    rows.push(parser.screen().contents());
    rows.join("\n")
}

#[test]
fn shell_handoff_keeps_output_below_the_entire_inline_viewport() {
    fn seed() -> (InlineTerminal<Capture>, Capture) {
        let (mut terminal, mut capture) = terminal(80, 24);
        terminal.viewport_area = Rect::new(0, 2, 80, 18);
        capture.write_all(b"\x1b[12;1HREVIEW_RESULT_MUST_REMAIN\x1b[13;1HReview complete\x1b[15;1HFooter\x1b[11;5H").unwrap();
        (terminal, capture)
    }
    let (_, mut legacy) = seed();
    legacy
        .write_all(b"\r\nTo continue, run ownpi-tui\r\nSaving session...\r\n")
        .unwrap();
    let legacy_grid = all_contents(&legacy);
    println!(
        "Legacy byte sequence: {:?}\nLegacy grid:\n{legacy_grid}",
        legacy.1.borrow()
    );
    assert!(!legacy_grid.contains("REVIEW_RESULT_MUST_REMAIN"));

    let (mut terminal, mut capture) = seed();
    terminal.finish_inline().unwrap();
    capture
        .write_all(b"To continue, run ownpi-tui\r\nSaving session...\r\n")
        .unwrap();
    let text = all_contents(&capture);
    println!(
        "Fixed byte sequence: {:?}\nCorrect shell handoff:\n{text}",
        capture.1.borrow()
    );
    assert!(text.contains("REVIEW_RESULT_MUST_REMAIN"));
    assert!(text.find("Review complete").unwrap() < text.find("To continue").unwrap());
    assert!(text.find("Footer").unwrap() < text.find("Saving session").unwrap());
}

fn paint(terminal: &mut InlineTerminal<Capture>, screen: Size, height: u16, text: Text<'static>) {
    terminal
        .draw_at_screen_size(
            screen,
            height,
            Some(StreamViewportRepaint::Required),
            None,
            |frame| {
                frame.render_widget(Paragraph::new(text), frame.size());
            },
        )
        .unwrap();
}

#[test]
fn recovery_transition_preserves_outcomes_and_never_commits_old_prompt_pixels() {
    for mode in [HistoryRenderMode::Rich, HistoryRenderMode::Raw] {
        for width in [32, 80, 120, 220] {
            let theme = ThemePalette::default();
            let overlay = recovery();
            let height = overlay.desired_height(&theme, width) + 4;
            let screen = Size::new(width, height);
            let (mut terminal, mut capture) = terminal(width, height);
            terminal.set_history_render_mode(mode);
            queue!(capture, MoveTo(0, 0), Print("Earlier committed history")).unwrap();
            paint(
                &mut terminal,
                screen,
                height - 1,
                Text::from("OLD PERMISSION\nStale reason\nStale stage"),
            );
            terminal.insert_history_cells(vec![
                Arc::new(Outcome("Stage failed")),
                Arc::new(Outcome("Task failed · HTTP 429")),
            ]);
            // Grow to the full screen, leaving zero history rows, then shrink the
            // panel while another outcome is being committed.
            paint(
                &mut terminal,
                screen,
                height,
                overlay.render_text(&theme, width),
            );
            let first = all_contents(&capture);
            for expected in [
                "Earlier committed history",
                "Stage failed",
                "Task failed · HTTP 429",
                "Recovery",
            ] {
                assert!(
                    first.contains(expected),
                    "width={width}, lost {expected}: {first}"
                );
            }
            assert_eq!(first.matches("Recovery").count(), 1, "{first}");
            assert!(!first.contains("OLD PERMISSION"), "{first}");
            assert!(!first.contains("Stale reason"), "{first}");
            terminal.insert_history_cells(vec![Arc::new(Outcome("Preserved receipt"))]);
            paint(
                &mut terminal,
                screen,
                height - 2,
                overlay.render_text(&theme, width),
            );
            let second = all_contents(&capture);
            assert_eq!(second.matches("Recovery").count(), 1, "{second}");
            assert_eq!(
                second.matches("Task failed · HTTP 429").count(),
                1,
                "{second}"
            );
            assert!(second.contains("Preserved receipt"), "{second}");
            assert!(!second.contains("Permission rule"), "{second}");
            paint(&mut terminal, screen, 3, Text::from("Ready"));
            let closed = all_contents(&capture);
            assert!(!closed.contains("Recovery"), "{closed}");
            assert!(closed.contains("Stage failed"), "{closed}");
            assert!(closed.contains("Ready"), "{closed}");
        }
    }
}

#[test]
fn recovery_same_height_replacement_and_resize_leave_one_live_title() {
    let (mut terminal, capture) = terminal(120, 40);
    let overlay = recovery();
    let theme = ThemePalette::default();
    paint(
        &mut terminal,
        Size::new(120, 40),
        20,
        Text::from("OLD OVERLAY\nOld selection"),
    );
    paint(
        &mut terminal,
        Size::new(120, 40),
        20,
        overlay.render_text(&theme, 120),
    );
    for (width, height) in [(80, 32), (32, 40), (220, 50), (120, 40)] {
        capture.0.borrow_mut().screen_mut().set_size(height, width);
        paint(
            &mut terminal,
            Size::new(width, height),
            overlay.desired_height(&theme, width),
            overlay.render_text(&theme, width),
        );
        let text = all_contents(&capture);
        assert_eq!(text.matches("Recovery").count(), 1, "{text}");
        assert!(!text.contains("OLD OVERLAY"), "{text}");
    }
}

#[test]
fn recovery_rebuild_and_incremental_redraw_share_the_same_geometry() {
    let (mut terminal, mut capture) = terminal(80, 24);
    let screen = Size::new(80, 24);
    let theme = ThemePalette::default();
    let mut overlay = recovery();
    queue!(capture, MoveTo(0, 0), Print("STALE PHYSICAL HISTORY")).unwrap();
    paint(&mut terminal, screen, 16, Text::from("OLD PROMPT"));
    terminal.insert_history_cells(vec![Arc::new(Outcome("Task failed"))]);
    let height = overlay.desired_height(&theme, 80);
    let rebuild = ScrollbackRebuild::new(
        vec![
            HyperlinkLine::plain(Line::from("Authoritative history")),
            HyperlinkLine::plain(Line::from("Task failed")),
        ],
        Some(Arc::new(Outcome("Task failed"))),
    );
    terminal
        .draw_at_screen_size(screen, height, None, Some(rebuild), |frame| {
            frame.render_widget(
                Paragraph::new(overlay.render_text(&theme, 80)),
                frame.size(),
            );
        })
        .unwrap();
    for _ in 0..3 {
        // No forced clear: exercise the normal diff-render path too.
        terminal
            .draw_at_screen_size(screen, height, None, None, |frame| {
                frame.render_widget(
                    Paragraph::new(overlay.render_text(&theme, 80)),
                    frame.size(),
                );
            })
            .unwrap();
    }
    overlay.move_down();
    terminal
        .draw_at_screen_size(screen, height, None, None, |frame| {
            frame.render_widget(
                Paragraph::new(overlay.render_text(&theme, 80)),
                frame.size(),
            );
        })
        .unwrap();
    let contents = all_contents(&capture);
    for expected in [
        "Authoritative history",
        "Task failed",
        "Recovery",
        "› 2. End task",
    ] {
        assert_eq!(contents.matches(expected).count(), 1, "{contents}");
    }
    assert!(!contents.contains("STALE PHYSICAL"), "{contents}");
    assert!(!contents.contains("OLD PROMPT"), "{contents}");
}

#[test]
fn exit_handoff_removes_stopping_and_composer_without_erasing_review_history() {
    for width in [40, 80, 120] {
        let (mut terminal, mut capture) = terminal(width, 24);
        terminal.viewport_area = Rect::new(0, 2, width, 18);
        capture.write_all(b"\x1b[12;1HREVIEW_RESULT_MUST_REMAIN\x1b[13;1HReview complete\x1b[15;1HStopping active work before exit\x1b[18;1HMessage OwnPI\x1b[20;1HEsc stopping\x1b[18;3H").unwrap();
        // Mode restoration can move the cursor to the terminal's saved origin.
        // The handoff must set its own history boundary afterwards.
        capture.write_all(b"\x1b[1;1H").unwrap();
        terminal.finish_inline_at(11).unwrap();
        capture.write_all(b"Run run-one - stopped\r\nTo resume this run:\r\n  ownpi-tui --resume run-one\r\nSHELL_PROMPT").unwrap();
        let text = all_contents(&capture);
        assert!(text.contains("REVIEW_RESULT_MUST_REMAIN"));
        assert!(text.contains("Review complete\nRun run-one"));
        assert!(text.contains("ownpi-tui --resume run-one"));
        assert!(text.contains("SHELL_PROMPT"));
        assert!(!text.contains("Stopping"));
        assert!(!text.contains("Message OwnPI"));
        assert!(!text.contains("Esc stopping"));
    }
}
