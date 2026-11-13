use anyhow::Result;
use ratatui::style::Style;
use ratatui::widgets::Block;

use crate::app::AppState;
use crate::bottom_pane::footer::{footer_height_for_surface_popup, FooterContext, FooterProps};
use crate::chatwidget::{chat_desired_height, ChatWidget};
use crate::inline_terminal::{InlineTerminal, ScrollbackRebuild};

pub(crate) fn draw(terminal: &mut InlineTerminal, app: &mut AppState) -> Result<u16> {
    terminal.leave_alternate_screen_if_needed()?;
    terminal.set_history_render_mode(app.history_render_mode());
    let screen = terminal.screen_size()?;
    app.note_layout_size(screen.width as usize, screen.height as usize);
    let scrollback_rebuild = if app.take_due_transcript_reflow(std::time::Instant::now()) {
        // One coalesced source-backed repair owns resize, snapshot,
        // rollback, and stream-consolidation replay. Its lines remain unflushed
        // until the final live viewport is established in the synchronized draw.
        let _ = app.drain_pending_history_cells();
        Some(ScrollbackRebuild::new(
            app.render_scrollback_tail(screen.width.saturating_sub(1).max(1) as usize),
            app.last_committed_scrollback_cell(),
        ))
    } else {
        let pending_history = if should_defer_scrollback_history_flush(app) {
            Vec::new()
        } else {
            app.drain_pending_history_cells()
        };
        terminal.insert_history_cells(pending_history);
        None
    };
    let reflow_will_run = scrollback_rebuild.is_some();
    let footer_props = footer_props_for(app);
    let footer_rows = footer_height_for_surface_popup(app.bottom_pane.surface_popup_is_visible());
    let desired_height = chat_desired_height(app, screen.width, footer_rows)
        .min(screen.height)
        .max(1);
    let mut history_end = 0;
    terminal.draw_with_resize_reflow(
        screen,
        desired_height,
        None,
        scrollback_rebuild,
        |frame| {
            let size = frame.size();
            let base_bg = app.theme.surface_bg.or(app.theme.shadow_bg);
            frame.render_widget(
                Block::default().style(match base_bg {
                    Some(color) => Style::default().bg(color),
                    None => Style::default(),
                }),
                size,
            );
            let chat_widget = ChatWidget::new(app, footer_props.clone(), footer_rows);
            let chat_result = chat_widget.render(size, frame.buffer_mut());
            history_end = chat_result.history_area.bottom();
            if let Some((cursor_x, cursor_y)) = chat_result.cursor {
                frame.set_cursor(cursor_x, cursor_y);
            }
        },
    )?;
    if reflow_will_run {
        app.finish_resize_reflow(screen.width as usize);
    }
    Ok(history_end)
}

pub(crate) fn draw_transcript_pager(
    terminal: &mut InlineTerminal,
    app: &mut AppState,
) -> Result<()> {
    let Some(pager) = app.transcript_pager() else {
        return Ok(());
    };
    terminal.draw_fullscreen(|frame| {
        let size = frame.size();
        crate::transcript_pager::render_transcript_pager(
            pager,
            size,
            frame.buffer_mut(),
            &app.theme,
        );
    })
}

fn footer_props_for(app: &AppState) -> FooterProps {
    let execution = app.turn_execution_context.as_ref();
    FooterProps {
        model: app
            .workspace
            .model
            .as_deref()
            .unwrap_or("<no model>")
            .into(),
        directory: app
            .workspace
            .project_root
            .as_deref()
            .unwrap_or("<no workspace>")
            .into(),
        permission: app.current_permission_label().into(),
        mode: execution
            .map(|context| context.mode_label.split(" · ").next().unwrap_or_default())
            .filter(|label| !label.is_empty())
            .unwrap_or_else(|| match app.workspace.runtime_profile.as_str() {
                "task" => "Task",
                "research" => "Research",
                "auto" => "Auto",
                _ => "",
            })
            .to_string(),
        stage: execution
            .map(|context| context.stage_label.replace('_', " "))
            .unwrap_or_default(),
        context: app.context_meter_enabled.then(|| FooterContext {
            percent_left: app.token_usage().and_then(|usage| usage.percent_left()),
            tokens_used: app.token_usage().and_then(|usage| usage.used_tokens()),
            is_estimate: app.token_usage().is_some_and(|usage| usage.is_estimate),
        }),
    }
}

fn should_defer_scrollback_history_flush(app: &AppState) -> bool {
    app.bottom_pane.active_bottom_view_is_visible()
}

pub(crate) fn draw_agent_view(terminal: &mut InlineTerminal, app: &mut AppState) -> Result<()> {
    terminal.draw_fullscreen(|frame| {
        render_agent_view(app, frame.size(), frame.buffer_mut());
    })
}

pub(crate) fn render_agent_view(
    app: &AppState,
    area: ratatui::layout::Rect,
    buf: &mut ratatui::buffer::Buffer,
) {
    use ratatui::style::Modifier;
    use ratatui::text::{Line, Span, Text};
    use ratatui::widgets::{Clear, Paragraph, Widget};
    Clear.render(area, buf);
    if let Some(popup) = &app.agents.popup {
        let rows = app.agents.popup_rows();
        let pending = app.agents.requests.len();
        let title = if popup.kind == crate::multi_agents::PopupKind::Agents {
            if pending == 0 {
                "Subagents".to_string()
            } else {
                format!(
                    "Subagents · {pending} pending request{}",
                    if pending == 1 { "" } else { "s" }
                )
            }
        } else {
            format!("Pending requests · {pending}")
        };
        let selected = rows
            .iter()
            .position(|(id, _, _)| *id == popup.selected)
            .unwrap_or(0);
        let capacity = (area.height.saturating_sub(4) as usize
            / if popup.kind == crate::multi_agents::PopupKind::Agents {
                4
            } else {
                3
            })
        .max(1);
        let start = selected.saturating_sub(capacity.saturating_sub(1));
        let mut lines = vec![
            Line::from(Span::styled(
                title,
                Style::default()
                    .fg(app.theme.accent)
                    .add_modifier(Modifier::BOLD),
            )),
            Line::default(),
        ];
        if rows.is_empty() {
            lines.push(Line::from("No pending requests."));
        }
        for (index, (id, name, description)) in rows.iter().enumerate().skip(start).take(capacity) {
            let style = if index == selected {
                Style::default()
                    .fg(app.theme.accent)
                    .add_modifier(Modifier::BOLD)
            } else {
                Style::default().fg(app.theme.text)
            };
            let name = crate::multi_agents::preview(name, area.width.saturating_sub(3) as usize);
            lines.push(Line::from(Span::styled(
                format!("{} {name}", if index == selected { "›" } else { " " }),
                style,
            )));
            if popup.kind == crate::multi_agents::PopupKind::Agents {
                if let Some((_, task)) = app.agents.tasks().iter().find(|(key, _)| key == id) {
                    let pending = task["request_ids"].as_array().map_or(0, Vec::len);
                    let status = if pending == 0 {
                        app.agents.status(task)
                    } else {
                        format!(
                            "{} · {pending} pending request{}",
                            app.agents.status(task),
                            if pending == 1 { "" } else { "s" }
                        )
                    };
                    lines.push(Line::from(Span::styled(
                        format!(
                            "  {}",
                            crate::multi_agents::preview(
                                &status,
                                area.width.saturating_sub(3) as usize
                            )
                        ),
                        Style::default().fg(app.theme.text),
                    )));
                } else {
                    lines.push(Line::default());
                }
            }
            lines.push(Line::from(Span::styled(
                format!(
                    "  {}",
                    crate::multi_agents::preview(
                        description,
                        area.width.saturating_sub(3) as usize
                    )
                ),
                Style::default().fg(app.theme.text_muted),
            )));
            lines.push(Line::default());
        }
        Paragraph::new(Text::from(lines)).render(
            ratatui::layout::Rect::new(area.x, area.y, area.width, area.height.saturating_sub(1)),
            buf,
        );
        Paragraph::new("↑/↓ Select · Enter Open · Esc Return").render(
            ratatui::layout::Rect::new(area.x, area.bottom().saturating_sub(1), area.width, 1),
            buf,
        );
    } else {
        let overlay_height = app
            .bottom_pane
            .bottom_view_height(area.width, &app.theme)
            .map(|h| h.saturating_add(1).min(area.height))
            .unwrap_or(0);
        let content = ratatui::layout::Rect::new(
            area.x,
            area.y,
            area.width,
            area.height.saturating_sub(overlay_height),
        );
        crate::transcript_pager::render_transcript_pager(
            &app.agents.pager,
            content,
            buf,
            &app.theme,
        );
        if overlay_height > 0 {
            let overlay = ratatui::layout::Rect::new(
                area.x,
                area.bottom() - overlay_height,
                area.width,
                overlay_height,
            );
            app.bottom_pane.render_bottom_view(overlay, buf, &app.theme);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bridge::BridgeEvent;
    use serde_json::json;

    fn mode_event(app: &mut AppState, kind: &str, data: serde_json::Value) {
        app.event(BridgeEvent {
            schema: crate::bridge::SCHEMA.into(),
            seq: 0,
            r#type: kind.into(),
            request_id: String::new(),
            identity: json!({}),
            data,
        });
    }

    #[test]
    fn startup_mode_uses_configuration_before_a_task_exists() {
        for (profile, label) in [("research", "Research"), ("task", "Task"), ("auto", "Auto")] {
            let mut app = AppState::default();
            mode_event(
                &mut app,
                "bootstrap",
                json!({"config":{"runtime_profile":profile}}),
            );
            assert_eq!(footer_props_for(&app).mode, label);
            mode_event(
                &mut app,
                "snapshot",
                json!({
                    "items":[], "capabilities":{"mode":"none"}, "status":{"status":"idle"}
                }),
            );
            let props = footer_props_for(&app);
            assert_eq!(props.mode, label);
            assert!(props.stage.is_empty());
            assert!(props.context.unwrap().tokens_used.is_none());
        }
    }

    #[test]
    fn startup_mode_tracks_setting_changes_without_starting_a_task() {
        let mut app = AppState::default();
        for (profile, label) in [
            ("research", "Research"),
            ("task", "Task"),
            ("research", "Research"),
        ] {
            mode_event(
                &mut app,
                "configuration",
                json!({"config":{"runtime_profile":profile}}),
            );
            assert_eq!(footer_props_for(&app).mode, label);
            assert!(!app.active);
        }
    }

    #[test]
    fn existing_run_mode_overrides_new_task_configuration_until_cleared() {
        for (mode, label, stage) in [
            ("research", "Research", "literature_grounding"),
            ("direct", "Task", "direct_qa"),
        ] {
            let mut app = AppState::default();
            mode_event(
                &mut app,
                "bootstrap",
                json!({"config":{"runtime_profile":"auto"}}),
            );
            // A resumed run may have capabilities before its route card arrives.
            mode_event(
                &mut app,
                "snapshot",
                json!({
                    "items":[], "capabilities":{"mode":mode},
                    "status":{"status":"in_progress", "current_stage":stage, "owner_active":true}
                }),
            );
            assert_eq!(footer_props_for(&app).mode, label);
            mode_event(
                &mut app,
                "configuration",
                json!({"config":{"runtime_profile":"task"}}),
            );
            assert_eq!(footer_props_for(&app).mode, label);
            mode_event(
                &mut app,
                "snapshot",
                json!({
                    "items":[], "capabilities":{"mode":"none"}, "status":{"status":"idle"}
                }),
            );
            assert_eq!(footer_props_for(&app).mode, "Task");
            assert!(footer_props_for(&app).stage.is_empty());
        }
    }

    #[test]
    fn active_research_footer_has_only_six_fields_on_their_assigned_rows() {
        let mut app = AppState::default();
        app.workspace.model = Some("gpt-5.6-terra".into());
        app.workspace.reasoning_effort = Some("medium".into());
        app.workspace.project_root = Some(std::env::var("HOME").unwrap());
        app.event(BridgeEvent {
            schema: crate::bridge::SCHEMA.into(),
            seq: 1,
            r#type: "snapshot".into(),
            request_id: String::new(),
            identity: json!({}),
            data: json!({"items":[],"worker_active":true,"execution_permissions":"full_access"}),
        });
        app.agents.requests = vec![json!({"session_id":"pending-request"})];
        app.turn_execution_context = Some(crate::app::TurnExecutionContext {
            mode_label: "Research".into(),
            stage_label: "literature_grounding".into(),
            stage_attempt: 1,
        });
        let area = ratatui::layout::Rect::new(0, 0, 180, 2);
        let mut buffer = ratatui::buffer::Buffer::empty(area);
        crate::bottom_pane::footer::render_footer_from_props(
            area,
            &mut buffer,
            &footer_props_for(&app),
            app.theme.bottom_pane(),
        );
        let rows = (0..2)
            .map(|y| {
                (0..180)
                    .map(|x| buffer.get(x, y).symbol())
                    .collect::<String>()
                    .trim()
                    .to_string()
            })
            .collect::<Vec<_>>();
        assert_eq!(rows[0], "gpt-5.6-terra · Permission: Full access · ~");
        assert_eq!(
            rows[1],
            "Mode: Research · Stage: literature grounding · Context --"
        );
    }

    #[test]
    fn production_footer_keeps_context_with_subagents_and_honors_the_enabled_setting() {
        let mut app = AppState::default();
        app.agents.batches = vec![json!({"tasks":[]})];
        app.event(BridgeEvent {
            schema: crate::bridge::SCHEMA.into(), seq: 1, r#type: "snapshot".into(),
            request_id: String::new(), identity: json!({}),
            data: json!({"usage":{"provider_prompt_tokens":144585,"is_estimate":false},"status":{"status":"in_progress"}}),
        });
        app.agents.batches = vec![json!({"tasks":[]})];
        app.agents.requests = vec![json!({"session_id":"request-one"})];
        let props = footer_props_for(&app);
        assert!(matches!(
            props.context,
            Some(FooterContext {
                tokens_used: Some(144585),
                percent_left: None,
                ..
            })
        ));
        app.context_meter_enabled = false;
        assert!(footer_props_for(&app).context.is_none());
    }
    #[test]
    fn production_footer_displays_each_permission_at_wide_and_narrow_widths() {
        for (preset, label) in [
            ("ask_for_approval", "Ask for approval"),
            ("approve_for_me", "Approve for me"),
            ("full_access", "Full access"),
        ] {
            let mut app = AppState::default();
            app.event(BridgeEvent {
                schema: crate::bridge::SCHEMA.into(),
                seq: 1,
                r#type: "configuration".into(),
                request_id: String::new(),
                identity: json!({}),
                data: json!({"config":{"permissions":preset,"model":"test-model"}}),
            });
            let props = footer_props_for(&app);
            for width in [24, 40, 80, 140] {
                let height = crate::bottom_pane::footer::FOOTER_ROWS;
                let area = ratatui::layout::Rect::new(0, 0, width, height);
                let mut buffer = ratatui::buffer::Buffer::empty(area);
                crate::bottom_pane::footer::render_footer_from_props(
                    area,
                    &mut buffer,
                    &props,
                    app.theme.bottom_pane(),
                );
                let rendered = (0..height)
                    .map(|y| {
                        (0..width)
                            .map(|x| buffer.get(x, y).symbol().to_string())
                            .collect::<String>()
                            .trim()
                            .to_string()
                    })
                    .collect::<Vec<_>>()
                    .join(" ");
                if width >= 40 {
                    assert!(rendered.contains(label), "{width}: {rendered}");
                }
                assert_eq!(height, 2);
                assert!(rendered.contains("Permission:"), "{width}: {rendered}");
            }
        }
    }
}
