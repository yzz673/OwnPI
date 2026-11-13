//! Renderer-only audit regions. Original cells retain lifecycle and copy ownership.

use super::{HistoryCell, HistoryCellSpacingKind, HistoryRenderMode};
use crate::wrapping::RtOptions;
use crate::{
    display_item::AuditContext,
    terminal_hyperlinks::{
        plain_hyperlink_lines, prefix_hyperlink_lines, visible_lines, HyperlinkLine,
    },
    theme::ThemePalette,
};
use ratatui::{
    style::{Color, Modifier, Style},
    text::{Line, Span},
};
use std::{any::Any, collections::HashSet, sync::Arc};

// Physical URL text may wrap, but OSC 8 destinations must remain unchanged.
fn adaptive_wrap_hyperlink_lines(
    lines: &[HyperlinkLine],
    options: RtOptions<'static>,
) -> Vec<HyperlinkLine> {
    lines
        .iter()
        .flat_map(|line| {
            crate::terminal_hyperlinks::remap_wrapped_line(
                line,
                crate::wrapping::word_wrap_line(&line.line, options.clone())
                    .iter()
                    .map(crate::wrapping::line_to_static)
                    .collect(),
            )
        })
        .collect()
}

#[derive(Clone, Debug, Default)]
pub(crate) struct AuditRegionProjector {
    active: Option<AuditContext>,
    terminal_ids: HashSet<String>,
}

impl AuditRegionProjector {
    pub(crate) fn has_active_region(&self) -> bool {
        self.active.is_some()
    }

    pub(crate) fn project(
        &mut self,
        cell: Arc<dyn HistoryCell>,
        theme: &ThemePalette,
    ) -> Arc<dyn HistoryCell> {
        if !cell.is_scrollback_history_cell() {
            return cell;
        }
        if cell.display_lines(80).is_empty()
            && cell.audit_boundary().is_none()
            && cell.audit_terminal_label().is_none()
        {
            return cell; // Invisible evidence cannot open, close or space a rich region.
        }
        let context = cell.audit_context().cloned();
        if context
            .as_ref()
            .is_some_and(|audit| self.terminal_ids.contains(&audit.audit_id))
        {
            return Arc::new(AuditRegionCell {
                // Keep the current rail intact, but explicitly label the
                // original owner. Source/copy provenance is never rewritten.
                source: cell,
                late_context: context,
                context: self.active.clone(),
                opening: false,
                close: None,
                terminal_label: None,

                detail_only: false,
                theme: theme.clone(),
            });
        }
        // Unassigned provider telemetry is still available in the transcript,
        // but has no authority to close or assume ownership of an audit.
        if context.is_none()
            && self.active.is_some()
            && cell
                .as_any()
                .downcast_ref::<super::TypedThreadItemHistoryCell>()
                .is_some_and(|typed| typed.is_model_activity())
        {
            return Arc::new(AuditRegionCell {
                source: cell,
                context: self.active.clone(),
                opening: false,
                close: None,
                terminal_label: None,
                late_context: None,

                detail_only: true,
                theme: theme.clone(),
            });
        }
        let transition = self.active.as_ref() != context.as_ref();
        let close = if transition {
            self.active.take().map(|old| {
                (
                    old,
                    // A display-owner change is not a runtime handoff or a
                    // pause. Only explicit boundary/result receipts may claim
                    // lifecycle state; this merely closes a visual segment.
                    cell.audit_boundary().unwrap_or("Audit activity"),
                )
            })
        } else {
            None
        };
        let opening = transition && context.is_some();
        self.active = context.clone();
        let terminal_label = context.as_ref().and_then(|_| cell.audit_terminal_label());
        if terminal_label.is_some() {
            if let Some(audit) = &context {
                self.terminal_ids.insert(audit.audit_id.clone());
            }
            self.active = None;
        }
        if let Some((audit, _)) = &close {
            if cell.audit_boundary().is_some() {
                self.terminal_ids.insert(audit.audit_id.clone());
            }
        }
        if context.is_none() && close.is_none() {
            return cell;
        }
        Arc::new(AuditRegionCell {
            source: cell,
            context,
            opening,
            close,
            terminal_label,
            late_context: None,

            detail_only: false,
            theme: theme.clone(),
        })
    }

    #[cfg(test)]
    pub(crate) fn project_all(
        cells: &[Arc<dyn HistoryCell>],
        theme: &ThemePalette,
    ) -> Vec<Arc<dyn HistoryCell>> {
        let mut projector = Self::default();
        cells
            .iter()
            .map(|cell| projector.project(cell.clone(), theme))
            .collect()
    }
}

#[derive(Debug)]
#[cfg(test)]
pub(crate) struct AuditBoundaryCell(pub(crate) &'static str);

#[cfg(test)]
impl HistoryCell for AuditBoundaryCell {
    fn audit_boundary(&self) -> Option<&'static str> {
        Some(self.0)
    }
    fn display_lines(&self, _width: usize) -> Vec<Line<'static>> {
        Vec::new()
    }
    fn raw_lines(&self) -> Vec<Line<'static>> {
        Vec::new()
    }
    fn as_any(&self) -> &dyn Any {
        self
    }
}

#[derive(Clone, Debug)]
struct AuditRegionCell {
    source: Arc<dyn HistoryCell>,
    context: Option<AuditContext>,
    opening: bool,
    close: Option<(AuditContext, &'static str)>,
    terminal_label: Option<&'static str>,
    late_context: Option<AuditContext>,

    detail_only: bool,
    theme: ThemePalette,
}

fn accent(context: &AuditContext, theme: &ThemePalette) -> Color {
    if theme.surface_bg.is_none() && theme.elevated_bg.is_none() {
        return if context.role == "readiness" {
            Color::Cyan
        } else {
            Color::Magenta
        };
    }
    match (context.role.as_str(), theme.is_light_background()) {
        ("readiness", true) => Color::Rgb(14, 116, 144),
        ("readiness", false) => Color::Rgb(103, 232, 249),
        (_, true) => Color::Rgb(126, 34, 206),
        (_, false) => Color::Rgb(196, 181, 253),
    }
}

impl AuditRegionCell {
    fn render(&self, width: usize, transcript: bool, raw: bool) -> Vec<HyperlinkLine> {
        if self.detail_only && !transcript && !raw {
            return Vec::new();
        }
        let width = width.max(1);
        let mut lines = Vec::new();
        let text_lines = |text: String, color: Color| {
            adaptive_wrap_hyperlink_lines(
                &plain_hyperlink_lines(vec![Line::from(Span::styled(
                    text,
                    Style::default().fg(color).add_modifier(Modifier::BOLD),
                ))]),
                RtOptions::new(width),
            )
        };
        if let Some((context, label)) = &self.close {
            lines.extend(text_lines(
                format!("╰─ {label}"),
                accent(context, &self.theme),
            ));
            lines.push(HyperlinkLine::new(Line::default()));
        }
        let Some(context) = &self.context else {
            if let Some(late) = &self.late_context {
                lines.extend(text_lines(
                    format!("Late result · {} · Round {}", late.role, late.review_round),
                    self.theme.text_muted,
                ));
            }
            lines.extend(if transcript {
                self.source.transcript_hyperlink_lines(width)
            } else if raw {
                self.source
                    .display_hyperlink_lines_for_mode(width, HistoryRenderMode::Raw)
            } else {
                self.source.display_hyperlink_lines_edged(width)
            });
            return lines;
        };
        let color = accent(context, &self.theme);
        // Main reserves one physical terminal column for its scrollback gutter.
        let rail = width >= 39;
        let inner = width.saturating_sub(if rail { 2 } else { 0 }).max(1);
        if self.opening {
            let (role, purpose, state) = if context.role == "readiness" {
                ("READINESS", "Handoff check", "CHECKING")
            } else {
                ("REVIEWER", "Quality review", "REVIEWING")
            };
            lines.extend(text_lines(
                format!("{}AUDIT / {role} · {state}", if rail { "╭─ " } else { "" }),
                color,
            ));
            let metadata = format!(
                "{purpose} · {} · Attempt {} · Round {}",
                context.stage.replace('_', " "),
                context.attempt,
                context.review_round
            );
            let metadata = adaptive_wrap_hyperlink_lines(
                &plain_hyperlink_lines(vec![Line::from(Span::styled(
                    metadata,
                    Style::default().fg(self.theme.text_muted),
                ))]),
                RtOptions::new(inner),
            );
            lines.extend(if rail {
                prefix_hyperlink_lines(
                    metadata,
                    Span::styled("│ ", Style::default().fg(color)),
                    Span::styled("│ ", Style::default().fg(color)),
                )
            } else {
                metadata
            });
        }
        if rail {
            lines.push(HyperlinkLine::new(Line::from(Span::styled(
                "│",
                Style::default().fg(color),
            ))));
        }
        if let Some(late) = &self.late_context {
            let label = format!(
                "Background result · {} · {} · Attempt {} · Round {}",
                late.role.to_uppercase(),
                late.stage.replace('_', " "),
                late.attempt,
                late.review_round
            );
            let background = adaptive_wrap_hyperlink_lines(
                &plain_hyperlink_lines(vec![Line::from(Span::styled(
                    label,
                    Style::default().fg(self.theme.text_muted),
                ))]),
                RtOptions::new(inner),
            );
            lines.extend(if rail {
                prefix_hyperlink_lines(
                    background,
                    Span::styled("│ ", Style::default().fg(color)),
                    Span::styled("│ ", Style::default().fg(color)),
                )
            } else {
                background
            });
        }
        let body = if raw {
            self.source
                .display_hyperlink_lines_for_mode(inner, HistoryRenderMode::Raw)
        } else if transcript {
            self.source.transcript_hyperlink_lines(inner)
        } else {
            self.source.display_hyperlink_lines(inner)
        };
        // Renderers own wrapping and hanging indents. Only wrap exceptional
        // overlong lines; normal lines must not be flattened a second time.
        let body: Vec<HyperlinkLine> = body
            .into_iter()
            .flat_map(|line| {
                if line.line.width() > inner {
                    adaptive_wrap_hyperlink_lines(&[line], RtOptions::new(inner))
                } else {
                    vec![line]
                }
            })
            .collect();
        lines.extend(if rail {
            body.into_iter()
                .enumerate()
                .flat_map(|(index, line)| {
                    let section_heading = self.terminal_label.is_some()
                        && (index == 0
                            || super::review_cycle::is_overall_section_heading(
                                &line.line,
                                &self.theme,
                            ));
                    let prefix = Span::styled(
                        if section_heading { "├─" } else { "│ " },
                        Style::default().fg(color),
                    );
                    prefix_hyperlink_lines(vec![line], prefix.clone(), prefix)
                })
                .collect()
        } else {
            body
        });
        if let Some(label) = self.terminal_label {
            lines.extend(text_lines(
                format!("{}{label}", if rail { "╰─ " } else { "" }),
                if label == "Review failed" {
                    self.theme.error
                } else {
                    color
                },
            ));
        }
        lines
    }
}

impl HistoryCell for AuditRegionCell {
    fn audit_boundary(&self) -> Option<&'static str> {
        self.close.as_ref().map(|(_, label)| *label)
    }
    fn audit_context(&self) -> Option<&AuditContext> {
        self.context.as_ref()
    }
    fn display_lines(&self, width: usize) -> Vec<Line<'static>> {
        visible_lines(self.render(width, false, false))
    }
    fn display_hyperlink_lines(&self, width: usize) -> Vec<HyperlinkLine> {
        self.render(width, false, false)
    }
    fn display_hyperlink_lines_for_mode(
        &self,
        width: usize,
        mode: HistoryRenderMode,
    ) -> Vec<HyperlinkLine> {
        self.render(width, false, mode == HistoryRenderMode::Raw)
    }
    fn transcript_lines(&self, width: usize) -> Vec<Line<'static>> {
        visible_lines(self.render(width, true, false))
    }
    fn transcript_hyperlink_lines(&self, width: usize) -> Vec<HyperlinkLine> {
        self.render(width, true, false)
    }
    fn raw_lines(&self) -> Vec<Line<'static>> {
        self.source.raw_lines()
    }
    fn copy_text(&self) -> Option<String> {
        self.source.copy_text()
    }
    fn spacing_kind(&self) -> HistoryCellSpacingKind {
        self.source.spacing_kind()
    }

    fn is_scrollback_history_cell(&self) -> bool {
        self.source.is_scrollback_history_cell()
    }
    fn is_stream_continuation(&self) -> bool {
        self.source.is_stream_continuation()
    }
    fn as_any(&self) -> &dyn Any {
        self
    }
}
