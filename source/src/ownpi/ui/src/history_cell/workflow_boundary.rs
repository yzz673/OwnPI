use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

use crate::theme::ThemePalette;
use crate::wrapping::{adaptive_wrap_line, line_to_static, RtOptions};

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct RouterBannerModel {
    pub(crate) label: String,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct WorkflowSummaryBannerModel {
    pub(crate) stages: Vec<String>,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct StageBannerModel {
    pub(crate) ordinal: Option<String>,
    pub(crate) label: String,
    pub(crate) attempt: Option<u32>,
    pub(crate) resumed: bool,
    pub(crate) contract: String,
    pub(crate) expected_outputs: Vec<String>,
    pub(crate) carry_in_review_record_ids: Vec<String>,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct StageOutcomeBannerModel {
    pub(crate) label: String,
    pub(crate) decision: String,
    pub(crate) facts: Vec<(String, String)>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum OutcomeTone {
    Accent,
    Success,
    Warning,
    Error,
}

#[derive(Clone, Copy)]
struct BoundaryStyles {
    border: Style,
    accent: Style,
    title: Style,
    muted: Style,
    fill: Style,
    success: Style,
    warning: Style,
    error: Style,
}

impl BoundaryStyles {
    fn new(theme: &ThemePalette) -> Self {
        let background = theme.elevated_bg.or(theme.surface_bg);
        let paint = |foreground| {
            let style = Style::default().fg(foreground);
            background.map_or(style, |color| style.bg(color))
        };
        Self {
            border: paint(theme.border),
            accent: paint(theme.accent).add_modifier(Modifier::BOLD),
            title: paint(theme.text_title).add_modifier(Modifier::BOLD),
            muted: paint(theme.text_muted),
            fill: paint(theme.text),
            success: paint(theme.success).add_modifier(Modifier::BOLD),
            warning: paint(theme.warning).add_modifier(Modifier::BOLD),
            error: paint(theme.error).add_modifier(Modifier::BOLD),
        }
    }

    fn outcome(self, tone: OutcomeTone) -> Style {
        match tone {
            OutcomeTone::Accent => self.accent,
            OutcomeTone::Success => self.success,
            OutcomeTone::Warning => self.warning,
            OutcomeTone::Error => self.error,
        }
    }
}

fn fit_text(value: &str, max_width: usize) -> String {
    if max_width == 0 {
        return String::new();
    }
    if value.width() <= max_width {
        return value.to_string();
    }
    if max_width == 1 {
        return "…".to_string();
    }
    let mut output = String::new();
    let mut used = 0usize;
    for character in value.chars() {
        let character_width = character.width().unwrap_or(0);
        if used.saturating_add(character_width) > max_width - 1 {
            break;
        }
        output.push(character);
        used = used.saturating_add(character_width);
    }
    output.push('…');
    output
}

fn fill_line(mut spans: Vec<Span<'static>>, width: usize, styles: BoundaryStyles) -> Line<'static> {
    let used = spans.iter().map(|span| span.content.width()).sum::<usize>();
    if used < width {
        spans.push(Span::styled(" ".repeat(width - used), styles.fill));
    }
    Line::from(spans)
}

fn framed_content_line(
    mut content: Vec<Span<'static>>,
    width: usize,
    styles: BoundaryStyles,
) -> Line<'static> {
    let content_width = content
        .iter()
        .map(|span| span.content.width())
        .sum::<usize>();
    let padding = width.saturating_sub(content_width + 2);
    let mut spans = vec![Span::styled("│", styles.border)];
    spans.append(&mut content);
    spans.push(Span::styled(" ".repeat(padding), styles.fill));
    spans.push(Span::styled("│", styles.border));
    Line::from(spans)
}

fn framed_top(label: &str, width: usize, styles: BoundaryStyles) -> Line<'static> {
    let label = fit_text(label, width.saturating_sub(5));
    let prefix = format!("╭─ {label} ");
    let rule_width = width.saturating_sub(prefix.width() + 1);
    Line::from(vec![
        Span::styled("╭─ ", styles.border),
        Span::styled(label, styles.accent),
        Span::styled(format!(" {}", "─".repeat(rule_width)), styles.border),
        Span::styled("╮", styles.border),
    ])
}

fn framed_bottom(width: usize, styles: BoundaryStyles) -> Line<'static> {
    Line::from(vec![
        Span::styled("╰", styles.border),
        Span::styled("─".repeat(width.saturating_sub(2)), styles.border),
        Span::styled("╯", styles.border),
    ])
}

fn outcome_presentation(decision: &str) -> Option<(OutcomeTone, &'static str, String)> {
    let normalized = decision.trim().to_ascii_lowercase();
    let presentation = match normalized.as_str() {
        "accept" => (OutcomeTone::Success, "✓", "ACCEPTED".to_string()),
        "advance" => (OutcomeTone::Success, "→", "ADVANCE".to_string()),
        "pivot" => (OutcomeTone::Warning, "↪", "PIVOT".to_string()),
        "downgrade_claim" => (OutcomeTone::Warning, "↓", "DOWNGRADE CLAIM".to_string()),
        "record_negative_finding" => (
            OutcomeTone::Warning,
            "−",
            "RECORD NEGATIVE FINDING".to_string(),
        ),
        "stop" => (OutcomeTone::Error, "✗", "STOP".to_string()),
        "forced_forward_revisit_limit" => (
            OutcomeTone::Warning,
            "!",
            "FORCED FORWARD · REVISIT LIMIT".to_string(),
        ),
        "completed" => (OutcomeTone::Success, "✓", "COMPLETED".to_string()),
        "unexpected_exit" => (OutcomeTone::Error, "✗", "UNEXPECTED EXIT".to_string()),
        "no_final_reply" => (OutcomeTone::Warning, "!", "NO FINAL REPLY".to_string()),
        "advance_with_residual_findings" => (
            OutcomeTone::Warning,
            "!",
            "ADVANCED WITH RESIDUALS".to_string(),
        ),
        "clarify_human" | "paused_for_human" | "paused" => return None,
        value if value == "revisit" || value.starts_with("revisit_") => {
            (OutcomeTone::Warning, "↻", "REVISIT".to_string())
        }
        "fail" | "failed" => (OutcomeTone::Error, "✗", "FAILED".to_string()),
        "delivery failed" => (OutcomeTone::Error, "✗", "DELIVERY FAILED".to_string()),
        "blocked" => (OutcomeTone::Error, "✗", "BLOCKED".to_string()),
        "cancelled" | "canceled" => (OutcomeTone::Error, "✗", "CANCELLED".to_string()),
        _ => {
            let label = if normalized.is_empty() {
                "UNKNOWN".to_string()
            } else {
                normalized.replace('_', " ").to_uppercase()
            };
            (OutcomeTone::Accent, "⦁", label)
        }
    };
    Some(presentation)
}

pub(crate) fn render_stage_outcome_banner(
    model: &StageOutcomeBannerModel,
    width: usize,
    theme: &ThemePalette,
) -> Vec<Line<'static>> {
    let width = width.max(1);
    let styles = BoundaryStyles::new(theme);
    let Some((tone, signal, decision)) = outcome_presentation(&model.decision) else {
        return Vec::new();
    };
    let outcome_style = styles.outcome(tone);
    if width < 12 {
        return vec![fill_line(
            vec![Span::styled(
                fit_text(&format!("{signal} {decision}"), width),
                outcome_style,
            )],
            width,
            styles,
        )];
    }

    let label = if model.label.trim().is_empty() {
        "UNKNOWN STAGE".to_string()
    } else {
        model.label.trim().replace('_', " ").to_uppercase()
    };
    let inner_width = width.saturating_sub(2);
    let signal_prefix = format!("  {signal}  ");
    let decision_suffix = format!("  {decision}  ");
    let wide_required = signal_prefix.width() + label.width() + 1 + decision_suffix.width();

    let mut lines = vec![framed_top("OUTCOME", width, styles)];
    if width >= 50 && wide_required <= inner_width {
        let gap = inner_width
            .saturating_sub(signal_prefix.width() + label.width() + decision_suffix.width());
        lines.push(framed_content_line(
            vec![
                Span::styled(signal_prefix, outcome_style),
                Span::styled(label, styles.title),
                Span::styled(" ".repeat(gap), styles.fill),
                Span::styled(decision_suffix, outcome_style),
            ],
            width,
            styles,
        ));
    } else {
        let status_lines = adaptive_wrap_line(
            &Line::from(Span::styled(decision, outcome_style)),
            RtOptions::new(inner_width.max(1))
                .initial_indent(Line::from(Span::styled(signal_prefix, outcome_style)))
                .subsequent_indent(Line::from(Span::styled("     ", styles.fill))),
        )
        .iter()
        .map(line_to_static)
        .collect::<Vec<_>>();
        for line in status_lines {
            lines.push(framed_content_line(line.spans, width, styles));
        }

        let stage_lines = adaptive_wrap_line(
            &Line::from(Span::styled(label, styles.title)),
            RtOptions::new(inner_width.max(1))
                .initial_indent(Line::from(Span::styled("     ", styles.fill)))
                .subsequent_indent(Line::from(Span::styled("     ", styles.fill))),
        )
        .iter()
        .map(line_to_static)
        .collect::<Vec<_>>();
        for line in stage_lines {
            lines.push(framed_content_line(line.spans, width, styles));
        }
    }
    for (name, value) in &model.facts {
        if name.trim().is_empty() || value.trim().is_empty() {
            continue;
        }
        let fact = Line::from(vec![
            Span::styled(format!("  {} · ", name.trim()), styles.muted),
            Span::styled(value.trim().to_string(), styles.title),
        ]);
        for line in adaptive_wrap_line(
            &fact,
            RtOptions::new(inner_width.max(1)).subsequent_indent(Line::from("    ")),
        )
        .iter()
        .map(line_to_static)
        {
            lines.push(framed_content_line(line.spans, width, styles));
        }
    }
    lines.push(framed_bottom(width, styles));
    lines
}

pub(crate) fn render_router_navigation_banner(
    model: &RouterBannerModel,
    width: usize,
    theme: &ThemePalette,
) -> Vec<Line<'static>> {
    render_mode_banner(&model.label, "ROUTER", width, theme)
}

pub(crate) fn render_mode_banner(
    label: &str,
    title: &str,
    width: usize,
    theme: &ThemePalette,
) -> Vec<Line<'static>> {
    let width = width.max(1);
    let styles = BoundaryStyles::new(theme);
    if width < 12 {
        return vec![fill_line(
            vec![Span::styled(
                fit_text(&format!("◆ {title}"), width),
                styles.accent,
            )],
            width,
            styles,
        )];
    }
    let label = if label.trim().is_empty() {
        "UNKNOWN"
    } else {
        label.trim()
    };
    let label = fit_text(&label.to_uppercase(), width.saturating_sub(7));
    vec![
        framed_top(title, width, styles),
        framed_content_line(
            vec![
                Span::styled("  ◆  ", styles.accent),
                Span::styled(label, styles.title),
            ],
            width,
            styles,
        ),
        framed_bottom(width, styles),
    ]
}

pub(crate) fn render_workflow_summary_banner(
    model: &WorkflowSummaryBannerModel,
    width: usize,
    theme: &ThemePalette,
) -> Vec<Line<'static>> {
    let width = width.max(1);
    let styles = BoundaryStyles::new(theme);
    if width < 12 {
        return vec![fill_line(
            vec![Span::styled(fit_text("◆ WORKFLOW", width), styles.accent)],
            width,
            styles,
        )];
    }

    let stages = model
        .stages
        .iter()
        .map(|stage| stage.trim().replace('_', " "))
        .filter(|stage| !stage.is_empty())
        .collect::<Vec<_>>();
    let count = stages.len();
    let noun = if count == 1 { "STAGE" } else { "STAGES" };
    let inner_width = width.saturating_sub(2);
    let summary_suffix = format!("  ·  {count} {noun}");
    let summary_budget = inner_width.saturating_sub(5);
    let full_summary = format!("SELECTED{summary_suffix}");
    let mut summary = vec![Span::styled("  ◆  ", styles.accent)];
    if full_summary.width() <= summary_budget {
        summary.push(Span::styled("SELECTED", styles.title));
        summary.push(Span::styled(summary_suffix, styles.muted));
    } else {
        summary.push(Span::styled(
            fit_text(&full_summary, summary_budget),
            styles.title,
        ));
    }

    let mut lines = vec![framed_top("WORKFLOW", width, styles)];
    lines.push(framed_content_line(summary, width, styles));
    if !stages.is_empty() {
        let chain = stages.join(" → ");
        let detail_width = inner_width.saturating_sub(5).max(1);
        let chain_line = Line::from(Span::styled(chain, styles.fill));
        let wrapped = adaptive_wrap_line(&chain_line, RtOptions::new(detail_width))
            .iter()
            .map(line_to_static)
            .collect::<Vec<_>>();
        for line in wrapped {
            let mut content = vec![Span::styled("     ", styles.muted)];
            content.extend(line.spans);
            lines.push(framed_content_line(content, width, styles));
        }
    }
    lines.push(framed_bottom(width, styles));
    lines
}

pub(crate) fn render_stage_chapter_banner(
    model: &StageBannerModel,
    width: usize,
    theme: &ThemePalette,
) -> Vec<Line<'static>> {
    let width = width.max(1);
    let styles = BoundaryStyles::new(theme);
    if width < 12 {
        let identity = model
            .ordinal
            .as_deref()
            .map(|ordinal| format!("◇ {ordinal}"))
            .unwrap_or_else(|| "◇ STAGE".to_string());
        return vec![fill_line(
            vec![Span::styled(fit_text(&identity, width), styles.accent)],
            width,
            styles,
        )];
    }

    let is_revisit = model.attempt.is_some_and(|attempt| attempt > 1);
    let badge = if is_revisit {
        "REVISIT".to_string()
    } else {
        model
            .ordinal
            .as_deref()
            .filter(|value| !value.trim().is_empty())
            .map(|ordinal| format!("STAGE {ordinal}"))
            .unwrap_or_else(|| "STAGE".to_string())
    };
    let signal = if model.resumed {
        "  ↻◇  "
    } else {
        "  ◇  "
    };
    let full_attempt = model
        .attempt
        .map(|attempt| format!("  ·  attempt {attempt}"))
        .unwrap_or_default();
    let compact_attempt = model
        .attempt
        .map(|attempt| format!(" · a{attempt}"))
        .unwrap_or_default();
    let inner_width = width.saturating_sub(2);
    let attempt = if full_attempt.width() + signal.width() + 4 <= inner_width {
        full_attempt
    } else {
        compact_attempt
    };
    let label_budget = inner_width.saturating_sub(signal.width() + attempt.width());
    let label = fit_text(&model.label.replace('_', " ").to_uppercase(), label_budget);
    let mut title = vec![
        Span::styled(signal.to_string(), styles.accent),
        Span::styled(label, styles.title),
    ];
    if !attempt.is_empty() {
        title.push(Span::styled(attempt, styles.muted));
    }

    let mut lines = vec![framed_top(&badge, width, styles)];
    lines.push(framed_content_line(title, width, styles));
    if width >= 50 && !model.expected_outputs.is_empty() {
        let outcomes = model
            .expected_outputs
            .iter()
            .map(|value| value.replace('_', " "))
            .collect::<Vec<_>>()
            .join(", ");
        let detail = format!("Produces: {outcomes}");
        lines.push(framed_content_line(
            vec![
                Span::styled("     ", styles.muted),
                Span::styled(
                    fit_text(&detail, inner_width.saturating_sub(5)),
                    styles.muted,
                ),
            ],
            width,
            styles,
        ));
    }
    if !model.carry_in_review_record_ids.is_empty() {
        let noun = if model.carry_in_review_record_ids.len() == 1 {
            "review"
        } else {
            "reviews"
        };
        let detail = format!(
            "Carries {} {noun}: {}",
            model.carry_in_review_record_ids.len(),
            model.carry_in_review_record_ids.join(", ")
        );
        let detail_width = inner_width.saturating_sub(5).max(1);
        for line in adaptive_wrap_line(
            &Line::from(Span::styled(detail, styles.warning)),
            RtOptions::new(detail_width),
        )
        .iter()
        .map(line_to_static)
        {
            let mut content = vec![Span::styled("     ", styles.muted)];
            content.extend(line.spans);
            lines.push(framed_content_line(content, width, styles));
        }
    }
    lines.push(framed_bottom(width, styles));
    lines
}

#[cfg(test)]
mod tests {
    use super::*;
    use ratatui::style::Color;

    fn plain(line: &Line<'static>) -> String {
        line.spans
            .iter()
            .map(|span| span.content.as_ref())
            .collect()
    }

    #[test]
    fn router_and_stage_share_a_closed_responsive_component_family() {
        let router = RouterBannerModel {
            label: "Research Workflow".to_string(),
        };
        let stage = StageBannerModel {
            ordinal: Some("02".to_string()),
            label: "package_claim_gate".to_string(),
            attempt: Some(3),
            contract: "claim_package".to_string(),
            expected_outputs: vec!["final_payload".to_string()],
            ..StageBannerModel::default()
        };
        for width in [8, 12, 24, 40, 80, 120] {
            for (kind, lines) in [
                (
                    "router",
                    render_router_navigation_banner(&router, width, &ThemePalette::default()),
                ),
                (
                    "stage",
                    render_stage_chapter_banner(&stage, width, &ThemePalette::default()),
                ),
            ] {
                assert!(
                    lines.iter().all(|line| line.width() == width),
                    "{kind} width={width}: {:?}",
                    lines.iter().map(Line::width).collect::<Vec<_>>()
                );
                if width >= 12 {
                    let rendered = lines.iter().map(plain).collect::<Vec<_>>().join("\n");
                    assert!(rendered.lines().next().expect("top").ends_with('╮'));
                    assert!(rendered.lines().last().expect("bottom").ends_with('╯'));
                }
            }
        }
    }

    #[test]
    fn workflow_summary_is_closed_responsive_and_keeps_the_complete_chain() {
        let model = WorkflowSummaryBannerModel {
            stages: vec![
                "literature_grounding".to_string(),
                "implementation_execution".to_string(),
                "analysis_decision".to_string(),
                "output_synthesis".to_string(),
            ],
        };
        for width in [8, 12, 24, 48, 80, 120] {
            let lines = render_workflow_summary_banner(&model, width, &ThemePalette::default());
            assert!(
                lines.iter().all(|line| line.width() == width),
                "width={width}: {:?}",
                lines.iter().map(Line::width).collect::<Vec<_>>()
            );
            let rendered = lines.iter().map(plain).collect::<Vec<_>>().join("\n");
            if width < 12 {
                assert!(rendered.contains("WORKFLOW") || rendered.contains('…'));
                continue;
            }
            let top = rendered.lines().next().expect("top");
            assert!(top.starts_with("╭─ ") && (top.contains("WORKFLOW") || top.contains('…')));
            assert!(rendered.lines().last().expect("bottom").ends_with('╯'));
            assert_eq!(rendered.matches('→').count(), 3, "{rendered}");
            let compact_chain = lines
                .iter()
                .skip(2)
                .take(lines.len().saturating_sub(3))
                .map(plain)
                .flat_map(|line| {
                    line.chars()
                        .skip(1)
                        .take(width.saturating_sub(2))
                        .collect::<String>()
                        .chars()
                        .filter(|character| !character.is_whitespace())
                        .collect::<String>()
                        .chars()
                        .collect::<Vec<_>>()
                })
                .collect::<String>();
            assert_eq!(
                compact_chain,
                "literaturegrounding→implementationexecution→analysisdecision→outputsynthesis",
                "width={width}: {rendered}"
            );
        }
    }

    #[test]
    fn workflow_summary_uses_singular_stage_count() {
        let rendered = render_workflow_summary_banner(
            &WorkflowSummaryBannerModel {
                stages: vec!["output_synthesis".to_string()],
            },
            48,
            &ThemePalette::default(),
        )
        .iter()
        .map(plain)
        .collect::<Vec<_>>()
        .join("\n");

        assert!(rendered.contains("SELECTED  ·  1 STAGE"), "{rendered}");
        assert!(!rendered.contains("1 STAGES"), "{rendered}");
    }

    #[test]
    fn stage_outcome_is_a_distinct_responsive_panel_without_handoff_details() {
        let model = StageOutcomeBannerModel {
            label: "literature_grounding".to_string(),
            decision: "accept".to_string(),
            facts: Vec::new(),
        };
        for width in [8, 12, 24, 40, 80, 120] {
            let lines = render_stage_outcome_banner(&model, width, &ThemePalette::default());
            assert!(
                lines.iter().all(|line| line.width() == width),
                "width={width}: {:?}",
                lines.iter().map(Line::width).collect::<Vec<_>>()
            );
            let rendered = lines.iter().map(plain).collect::<Vec<_>>().join("\n");
            assert!(!rendered.contains("NEXT"), "{rendered}");
            assert!(!rendered.contains("analysis decision"), "{rendered}");
            if width < 12 {
                assert!(rendered.contains('✓') || rendered.contains('…'));
                continue;
            }
            assert!(rendered.starts_with("╭─ OUTCOME "), "{rendered}");
            assert!(rendered.lines().last().expect("bottom").ends_with('╯'));
            let semantic_text = rendered
                .chars()
                .filter(|character| character.is_alphanumeric())
                .collect::<String>();
            assert!(semantic_text.contains("ACCEPTED"), "{rendered}");
            assert!(semantic_text.contains("LITERATUREGROUNDING"), "{rendered}");
        }
    }

    #[test]
    fn stage_outcome_maps_decisions_to_text_glyphs_and_semantic_colors() {
        let theme = ThemePalette::default();
        for (decision, glyph, label, color) in [
            ("accept", "✓", "ACCEPTED", theme.success),
            (
                "advance_with_residual_findings",
                "!",
                "ADVANCED WITH RESIDUALS",
                theme.warning,
            ),
            (
                "revisit_literature_grounding",
                "↻",
                "REVISIT",
                theme.warning,
            ),
            (
                "forced_forward_revisit_limit",
                "!",
                "FORCED FORWARD · REVISIT LIMIT",
                theme.warning,
            ),
            ("fail", "✗", "FAILED", theme.error),
            ("novel_decision", "⦁", "NOVEL DECISION", theme.accent),
        ] {
            let lines = render_stage_outcome_banner(
                &StageOutcomeBannerModel {
                    label: "analysis_decision".to_string(),
                    decision: decision.to_string(),
                    facts: Vec::new(),
                },
                80,
                &theme,
            );
            let rendered = lines.iter().map(plain).collect::<Vec<_>>().join("\n");
            assert!(rendered.contains(glyph), "decision={decision}: {rendered}");
            assert!(rendered.contains(label), "decision={decision}: {rendered}");
            let semantic_spans = lines
                .iter()
                .flat_map(|line| line.spans.iter())
                .filter(|span| span.content.contains(glyph) || span.content.contains(label))
                .collect::<Vec<_>>();
            assert!(!semantic_spans.is_empty(), "decision={decision}");
            assert!(
                semantic_spans
                    .iter()
                    .all(|span| span.style.fg == Some(color)),
                "decision={decision}: {semantic_spans:?}"
            );
        }
    }

    #[test]
    fn interaction_pauses_do_not_render_as_stage_outcomes() {
        for decision in ["clarify_human", "paused_for_human", "paused"] {
            assert!(render_stage_outcome_banner(
                &StageOutcomeBannerModel {
                    label: "literature_grounding".to_string(),
                    decision: decision.to_string(),
                    facts: Vec::new(),
                },
                80,
                &ThemePalette::default(),
            )
            .is_empty());
        }
    }

    #[test]
    fn stage_primary_banner_hides_internal_contract_vocabulary() {
        let rendered = render_stage_chapter_banner(
            &StageBannerModel {
                ordinal: Some("01".to_string()),
                label: "literature_grounding".to_string(),
                attempt: Some(1),
                contract: "literature_grounding".to_string(),
                expected_outputs: vec!["grounding_report".to_string()],
                resumed: false,
                carry_in_review_record_ids: Vec::new(),
            },
            100,
            &ThemePalette::default(),
        )
        .iter()
        .map(plain)
        .collect::<Vec<_>>()
        .join("\n");

        assert!(
            rendered.contains("Produces: grounding report"),
            "{rendered}"
        );
        assert!(
            !rendered.contains("contract literature_grounding"),
            "{rendered}"
        );
        assert!(
            !rendered.contains("expected grounding_report"),
            "{rendered}"
        );
    }

    #[test]
    fn stage_banner_shows_every_carried_review_identity_without_truncation() {
        let model = StageBannerModel {
            ordinal: Some("02".to_string()),
            label: "research_framing".to_string(),
            attempt: Some(1),
            carry_in_review_record_ids: vec![
                "feedback:literature_grounding:a1:reviewer:r2:first".to_string(),
                "feedback:literature_grounding:a1:readiness:r2:second".to_string(),
            ],
            ..StageBannerModel::default()
        };

        for width in [40, 80] {
            let rendered = render_stage_chapter_banner(&model, width, &ThemePalette::default())
                .iter()
                .map(plain)
                .collect::<Vec<_>>()
                .join("\n");
            let dense = rendered
                .chars()
                .filter(|character| {
                    !character.is_whitespace()
                        && !matches!(character, '│' | '╭' | '╮' | '╰' | '╯' | '─' | '╌')
                })
                .collect::<String>();
            assert!(dense.contains("Carries2reviews:"), "{rendered}");
            assert!(
                dense.contains("feedback:literature_grounding:a1:reviewer:r2:first"),
                "{rendered}"
            );
            assert!(
                dense.contains("feedback:literature_grounding:a1:readiness:r2:second"),
                "{rendered}"
            );
        }
    }

    #[test]
    fn second_attempt_uses_revisit_identity_instead_of_repeating_stage_number() {
        let rendered = render_stage_chapter_banner(
            &StageBannerModel {
                ordinal: Some("01".to_string()),
                label: "literature_grounding".to_string(),
                attempt: Some(2),
                ..StageBannerModel::default()
            },
            64,
            &ThemePalette::default(),
        )
        .iter()
        .map(plain)
        .collect::<Vec<_>>()
        .join("\n");
        assert!(rendered.contains("REVISIT"));
        assert!(!rendered.contains("STAGE 01"));
        assert!(rendered.contains("attempt 2"));
    }

    #[test]
    fn boundary_family_uses_theme_surface_and_semantic_hierarchy() {
        let themes = [
            ThemePalette::default(),
            ThemePalette::from_terminal_colors_for_level(
                Some(Color::Rgb(0xee, 0xee, 0xee)),
                Some(Color::Rgb(0x11, 0x11, 0x11)),
                crate::theme::TerminalColorLevel::TrueColor,
            ),
        ];
        for theme in themes {
            let router = render_router_navigation_banner(
                &RouterBannerModel {
                    label: "Direct Chat".to_string(),
                },
                48,
                &theme,
            );
            let stage = render_stage_chapter_banner(
                &StageBannerModel {
                    ordinal: Some("01".to_string()),
                    label: "literature_grounding".to_string(),
                    attempt: Some(1),
                    ..StageBannerModel::default()
                },
                48,
                &theme,
            );
            assert!(plain(&router[1]).contains('◆'));
            assert!(plain(&stage[1]).contains('◇'));
            for line in router.iter().chain(stage.iter()) {
                for span in &line.spans {
                    assert_eq!(span.style.bg, theme.elevated_bg.or(theme.surface_bg));
                }
            }
        }
    }

    #[test]
    fn ansi256_white_boundaries_keep_readable_titles_and_fixed_light_backgrounds() {
        let theme = ThemePalette::from_terminal_colors_for_level(
            Some(Color::Rgb(17, 17, 17)),
            Some(Color::Rgb(255, 255, 255)),
            crate::theme::TerminalColorLevel::Ansi256,
        );
        assert!(theme.is_light_background());
        for width in [40, 80, 120] {
            let families = [
                render_router_navigation_banner(
                    &RouterBannerModel {
                        label: "Research".into(),
                    },
                    width,
                    &theme,
                ),
                render_workflow_summary_banner(
                    &WorkflowSummaryBannerModel {
                        stages: vec!["literature_grounding".into(), "output_synthesis".into()],
                    },
                    width,
                    &theme,
                ),
                render_stage_chapter_banner(
                    &StageBannerModel {
                        label: "文献调研 literature grounding".into(),
                        attempt: Some(1),
                        ..Default::default()
                    },
                    width,
                    &theme,
                ),
            ];
            for lines in families {
                assert!(lines
                    .iter()
                    .flat_map(|l| &l.spans)
                    .all(|s| s.style.bg == Some(Color::Indexed(231))));
                assert!(lines
                    .iter()
                    .flat_map(|l| &l.spans)
                    .any(|s| s.style.fg == Some(theme.text_title)));
                assert!(lines.iter().map(plain).all(|text| text.width() <= width));
            }
        }
    }
}
