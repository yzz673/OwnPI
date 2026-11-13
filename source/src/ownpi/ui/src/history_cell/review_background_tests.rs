use super::audit_region::AuditRegionProjector;
use super::{HistoryCell, TypedThreadItemHistoryCell};
use crate::display_item::DisplayItem;
use crate::terminal_hyperlinks::{visible_lines, HyperlinkLine};
use crate::theme::ThemePalette;
use ratatui::buffer::Buffer;
use ratatui::layout::{Position, Rect, Size};
use ratatui::style::{Color, Style};
use serde_json::json;
use std::sync::Arc;

#[test]
fn background_pass_preserves_text_styles_and_tracks_wrapped_scroll_rows() {
    use ratatui::style::Modifier;
    use ratatui::text::{Line, Span};
    use ratatui::widgets::{Paragraph, Widget, Wrap};
    let fill = Color::Rgb(30, 36, 42);
    let lines = vec![
        Line::from("outside"),
        Line::from(Span::styled(
            "abc def ghi",
            Style::default()
                .fg(Color::Yellow)
                .add_modifier(Modifier::BOLD),
        ))
        .style(Style::default().bg(fill)),
        Line::default().style(Style::default().bg(fill)),
        Line::from("after"),
    ];
    let area = Rect::new(2, 1, 8, 3);
    for scroll in [1, 3] {
        let mut before = Buffer::empty(Rect::new(0, 0, 12, 5));
        Paragraph::new(lines.clone())
            .wrap(Wrap { trim: false })
            .scroll((scroll, 0))
            .render(area, &mut before);
        let mut after = Buffer::empty(before.area);
        super::render_styled_lines(
            lines.clone(),
            area,
            &mut after,
            Style::default(),
            scroll,
            Some(Wrap { trim: false }),
        );
        for y in 0..after.area.height {
            for x in 0..after.area.width {
                let old = before.get(x, y);
                let new = after.get(x, y);
                assert_eq!(
                    (new.symbol(), new.fg, new.modifier),
                    (old.symbol(), old.fg, old.modifier)
                );
                let painted = area.contains(Position::new(x, y)) && (scroll == 1 || y == area.y);
                assert_eq!(new.bg, if painted { fill } else { Color::Reset });
            }
        }
    }
}

fn themes() -> Vec<ThemePalette> {
    [
        (
            Some(Color::Rgb(255, 255, 255)),
            Some(Color::Rgb(248, 250, 252)),
        ),
        (Some(Color::Rgb(12, 16, 20)), Some(Color::Rgb(30, 36, 42))),
        (Some(Color::Rgb(240, 240, 240)), None),
        (None, None),
    ]
    .into_iter()
    .map(|(surface_bg, elevated_bg)| ThemePalette {
        surface_bg,
        elevated_bg,
        ..ThemePalette::default()
    })
    .collect()
}

fn findings(role: &str, theme: &ThemePalette) -> Arc<dyn HistoryCell> {
    let item = DisplayItem::parse_value(&json!({
        "schema_version": "ownpi.tui.item.v1", "kind": "review_cycle",
        "item_id": "finding-background", "status": "completed", "visibility": "user",
        "call_id": "finding-background", "tool_name": "review_cycle",
        "audit_context": {"audit_id": "audit-background", "run_id": "run-background",
            "role": role, "stage": "literature_grounding", "attempt": 1, "review_round": 1},
        "payload": {
            "review_kind": role, "phase": "feedback_received", "outcome": "findings",
            "feedback_format": "structured_v2", "declared_artifact_path": "research/paper.md",
            "overall_assessment": "Keep the complete assessment and its paragraph spacing.\n\nOVERALL\n保留独立段落。",
            "findings": [
                {"severity": "P1", "title": "Evidence mapping",
                 "evidence_refs": ["research/paper.md:11", "research/ledger.md:24"],
                 "description": "The ledger and manuscript refer to different sources. 核验引文编号与原文证据。",
                 "remediation": "Use stable citation keys and verify every manuscript location."},
                {"severity": "P2", "title": "Missing passage",
                 "evidence_refs": ["research/receipts.md:37"],
                 "description": "The quantitative detail needs a source passage.",
                 "remediation": "Add the passage or remove the number."}
            ]
        }
    }))
    .unwrap();
    let source: Arc<dyn HistoryCell> =
        Arc::new(TypedThreadItemHistoryCell::new(item, theme.clone()));
    AuditRegionProjector::project_all(&[source], theme).remove(0)
}

fn body_rows(lines: &[HyperlinkLine]) -> std::ops::Range<usize> {
    let start = lines
        .iter()
        .position(|line| line.line.to_string().contains("FINDINGS"))
        .unwrap();
    let end = lines
        .iter()
        .position(|line| line.line.to_string().contains("Review complete"))
        .unwrap();
    // These are deliberate layout spacers, not rows that should be removed.
    // The pager's unstyled provenance appendix is outside the Findings panel.
    let end = lines
        .iter()
        .position(|line| line.line.to_string().contains("── Recorded details"))
        .map_or(end, |details| details - 1);
    if lines[start].line.to_string().starts_with("├─") {
        assert_eq!(
            lines
                .iter()
                .filter(|line| line.line.to_string() == "├─ OVERALL")
                .count(),
            1,
            "Overall must join the audit rail exactly once, including in the pager"
        );
        assert!(
            lines
                .iter()
                .any(|line| line.line.to_string() == "│     OVERALL"),
            "Assessment text must not be mistaken for a section heading"
        );
    }
    let detailed = lines
        .iter()
        .any(|line| line.line.to_string().contains("── Recorded details"));
    let labels: &[&str] = if detailed {
        &["Evidence", "Why", "Fix", "OVERALL"]
    } else {
        &["OVERALL"]
    };
    for label in labels {
        let row = lines
            .iter()
            .position(|line| {
                line.line.to_string().contains(*label)
                    && !line.line.to_string().contains("Evidence mapping")
            })
            .unwrap();
        assert!(lines[row - 1]
            .line
            .to_string()
            .trim_matches(['│', ' '])
            .is_empty());
    }
    assert_eq!(
        lines
            .iter()
            .flat_map(|line| &line.hyperlinks)
            .any(|link| link.destination.ends_with("/research/paper.md#L11")),
        detailed
    );
    start..end
}

#[test]
fn findings_background_covers_spacers_and_row_tails_in_rendered_buffer() {
    for theme in themes() {
        let background = theme
            .elevated_bg
            .or(theme.surface_bg)
            .unwrap_or(Color::Reset);
        for role in ["readiness", "reviewer"] {
            let cell = findings(role, &theme);
            for width in [32, 40, 80, 120, 220] {
                for lines in [
                    cell.display_hyperlink_lines(width),
                    cell.transcript_hyperlink_lines(width),
                ] {
                    let rows = body_rows(&lines);
                    let pager = crate::transcript_pager::TranscriptPagerState::new(
                        visible_lines(lines.clone()),
                        width,
                    );
                    let pager_area = Rect::new(0, 0, width as u16, lines.len() as u16 + 4);
                    let mut pager_buffer = Buffer::empty(pager_area);
                    crate::transcript_pager::render_transcript_pager(
                        &pager,
                        pager_area,
                        &mut pager_buffer,
                        &theme,
                    );
                    for y in rows.clone() {
                        for x in 0..pager_area.width {
                            assert_eq!(
                                pager_buffer.get(x, y as u16 + 1).bg,
                                background,
                                "{role}, width={width}, pager cell=({x},{y})"
                            );
                        }
                    }
                    let area = Rect::new(0, 0, width as u16, lines.len() as u16);
                    let mut buffer = Buffer::empty(area);
                    super::render_styled_lines(
                        visible_lines(lines),
                        area,
                        &mut buffer,
                        Style::default().bg(theme.surface_bg.unwrap_or(Color::Reset)),
                        0,
                        None,
                    );
                    for y in rows {
                        for x in 0..area.width {
                            assert_eq!(
                                buffer.get(x, y as u16).bg,
                                background,
                                "{role}, width={width}, cell=({x},{y})"
                            );
                        }
                    }
                    assert_eq!(
                        buffer.get(0, area.height - 1).bg,
                        theme.surface_bg.unwrap_or(Color::Reset),
                        "panel fill leaked onto footer"
                    );
                }
            }
        }
    }
}

#[test]
fn findings_background_survives_scrollback_ansi_and_does_not_bleed() {
    // Isolate ANSI assertions from NO_COLOR and Crossterm's global color switch.
    const CHILD: &str = "OWNPI_TEST_FINDINGS_COLOR";
    if std::env::var_os(CHILD).is_none() {
        let result = std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "history_cell::review_background_tests::findings_background_survives_scrollback_ansi_and_does_not_bleed", "--nocapture"])
            .env_remove("NO_COLOR").env(CHILD, "1").output().unwrap();
        assert!(
            result.status.success(),
            "{}\n{}",
            String::from_utf8_lossy(&result.stdout),
            String::from_utf8_lossy(&result.stderr)
        );
        return;
    }
    for theme in themes() {
        let background = match theme.elevated_bg.or(theme.surface_bg) {
            Some(Color::Rgb(r, g, b)) => vt100::Color::Rgb(r, g, b),
            None => vt100::Color::Default,
            _ => unreachable!(),
        };
        for role in ["readiness", "reviewer"] {
            let cell = findings(role, &theme);
            for width in [32, 40, 80, 120, 220] {
                let lines = cell.display_hyperlink_lines(width);
                let rows = body_rows(&lines);
                assert_eq!(
                    lines[rows.start].line.style.bg,
                    theme.elevated_bg.or(theme.surface_bg),
                    "source row {:?}",
                    lines[rows.start]
                );
                let expected_count = lines.len();
                let height = expected_count as u16 + 4;
                let mut terminal = vt100::Parser::new(height, width as u16, 0);
                let (_, count) = crate::insert_history::insert_history_hyperlink_lines(
                    &mut terminal,
                    Rect::new(0, 0, width as u16, 2),
                    Size::new(width as u16, height),
                    Position::new(0, 0),
                    lines,
                )
                .unwrap();
                assert_eq!(count as usize, expected_count);
                for y in rows {
                    for x in 0..width as u16 {
                        let cell = terminal.screen().cell(y as u16, x).unwrap();
                        // vt100 stores default attributes in a wide continuation;
                        // its painted glyph and style belong to the leading cell.
                        let cell = if cell.is_wide_continuation() {
                            terminal.screen().cell(y as u16, x - 1).unwrap()
                        } else {
                            cell
                        };
                        assert_eq!(
                            cell.bgcolor(),
                            background,
                            "{role}, width={width}, terminal cell=({x},{y})"
                        );
                    }
                }
                assert_eq!(
                    terminal.screen().cell(count - 1, 0).unwrap().bgcolor(),
                    vt100::Color::Default,
                    "panel fill leaked onto footer"
                );
            }
        }
    }
}

fn submitted_review(theme: &ThemePalette) -> Vec<ratatui::text::Line<'static>> {
    super::review_cycle::render_review_cycle_card(
        &crate::display_item::ReviewCyclePayload {
            review_kind: "review".into(),
            stage: "analysis_decision".into(),
            phase: "submitted".into(),
            attempt: 1,
            round: 1,
            outcome: "no_findings".into(),
            ..Default::default()
        },
        120,
        theme,
    )
}

#[test]
fn framed_review_background_stops_at_border_in_wider_pager() {
    for theme in themes() {
        let fill = theme
            .elevated_bg
            .or(theme.surface_bg)
            .unwrap_or(Color::Reset);
        let surface = theme.surface_bg.unwrap_or(Color::Reset);
        for width in [160, 220] {
            let lines = submitted_review(&theme);
            assert!(lines.iter().all(|line| line.width() == 120));
            let area = Rect::new(0, 0, width, lines.len() as u16);
            let mut buffer = Buffer::empty(area);
            super::render_styled_lines(
                lines.clone(),
                area,
                &mut buffer,
                Style::default().bg(surface),
                0,
                None,
            );
            for y in 0..area.height {
                assert_eq!(buffer.get(119, y).bg, fill);
                for x in 120..width {
                    assert_eq!(
                        buffer.get(x, y).bg,
                        surface,
                        "review fill outside border at {x},{y}"
                    );
                }
            }
            let pager = crate::transcript_pager::TranscriptPagerState::new(lines, width as usize);
            let pager_area = Rect::new(0, 0, width, area.height + 4);
            let mut buffer = Buffer::empty(pager_area);
            crate::transcript_pager::render_transcript_pager(
                &pager,
                pager_area,
                &mut buffer,
                &theme,
            );
            for y in 1..=area.height {
                assert_eq!(buffer.get(119, y).bg, fill);
                for x in 120..width {
                    assert_eq!(buffer.get(x, y).bg, Color::Reset);
                }
            }
        }
    }
}

#[test]
fn framed_review_background_stops_at_border_in_wider_scrollback() {
    const CHILD: &str = "OWNPI_TEST_BOUNDED_REVIEW_COLOR";
    if std::env::var_os(CHILD).is_none() {
        let result=std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact","history_cell::review_background_tests::framed_review_background_stops_at_border_in_wider_scrollback","--nocapture"])
            .env_remove("NO_COLOR").env(CHILD,"1").output().unwrap();
        assert!(
            result.status.success(),
            "{}",
            String::from_utf8_lossy(&result.stdout)
        );
        return;
    }
    for theme in themes() {
        let fill = match theme.elevated_bg.or(theme.surface_bg) {
            Some(Color::Rgb(r, g, b)) => vt100::Color::Rgb(r, g, b),
            None => vt100::Color::Default,
            _ => unreachable!(),
        };
        for width in [160, 220] {
            let lines = crate::terminal_hyperlinks::plain_hyperlink_lines(submitted_review(&theme));
            let height = lines.len() as u16 + 4;
            let mut terminal = vt100::Parser::new(height, width, 0);
            let mut ansi = Vec::new();
            let (_, count) = crate::insert_history::insert_history_hyperlink_lines(
                &mut ansi,
                Rect::new(0, 0, width, 2),
                Size::new(width, height),
                Position::new(0, 0),
                lines,
            )
            .unwrap();
            terminal.process(&ansi);
            if width == 220 && theme.surface_bg == Some(Color::Rgb(255, 255, 255)) {
                if let Ok(folder) = std::env::var("OWNPI_REVIEW_BACKGROUND_OUTPUT") {
                    std::fs::write(
                        std::path::Path::new(&folder).join("review-120-in-220.ansi"),
                        &ansi,
                    )
                    .unwrap();
                }
            }
            for y in 0..count {
                assert_eq!(terminal.screen().cell(y, 119).unwrap().bgcolor(), fill);
                for x in 120..width {
                    assert_eq!(
                        terminal.screen().cell(y, x).unwrap().bgcolor(),
                        vt100::Color::Default,
                        "review fill outside border at {x},{y}"
                    );
                }
            }
            assert_eq!(
                terminal.screen().cell(count, 0).unwrap().bgcolor(),
                vt100::Color::Default
            );
        }
    }
}
