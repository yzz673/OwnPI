use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

use crate::display_item::{ReviewCyclePayload, ReviewFindingPayload};
use crate::terminal_hyperlinks::{
    plain_hyperlink_lines, prefix_hyperlink_lines, visible_lines, word_wrap_hyperlink_line,
    HyperlinkLine,
};
use crate::theme::ThemePalette;
use crate::wrapping::{adaptive_wrap_line, line_to_static, word_wrap_line, RtOptions};

#[derive(Clone, Copy)]
struct ReviewStyles<'a> {
    palette: &'a ThemePalette,
    cwd: Option<&'a str>,
    detail: ReviewDetail,
    border: Style,
    fill: Style,
    title: Style,
    muted: Style,
    accent: Style,
    success: Style,
    warning: Style,
    error: Style,
}

impl<'a> ReviewStyles<'a> {
    fn new(theme: &'a ThemePalette) -> Self {
        let background = theme.elevated_bg.or(theme.surface_bg);
        let paint = |foreground: Color| {
            let style = Style::default().fg(foreground);
            background.map_or(style, |color| style.bg(color))
        };
        Self {
            palette: theme,
            cwd: None,
            detail: ReviewDetail::Summary,
            border: paint(theme.border),
            fill: paint(theme.text),
            title: paint(theme.text_title).add_modifier(Modifier::BOLD),
            muted: paint(theme.text_muted),
            accent: paint(theme.accent).add_modifier(Modifier::BOLD),
            success: paint(theme.success).add_modifier(Modifier::BOLD),
            warning: paint(theme.warning).add_modifier(Modifier::BOLD),
            error: paint(theme.error).add_modifier(Modifier::BOLD),
        }
    }

    fn severity(self, severity: &str) -> Style {
        match severity.trim().to_ascii_uppercase().as_str() {
            "P0" => self.error,
            "P1" => self.warning,
            "P2" => self.accent,
            _ => self.muted.add_modifier(Modifier::BOLD),
        }
    }

    fn paint_background(self, mut lines: Vec<HyperlinkLine>) -> Vec<HyperlinkLine> {
        // Apply the panel fill after layout so empty rows, hanging indents and
        // row tails receive the same background. Keep foregrounds on spans.
        for line in &mut lines {
            line.line.style.bg = self.fill.bg;
        }
        lines
    }
}

#[derive(Clone, Copy, PartialEq)]
pub(crate) enum ReviewDetail {
    Summary,
    Full,
}

#[derive(Clone, Copy)]
enum ReviewTone {
    Accent,
    Success,
    Warning,
    Error,
}

pub(crate) fn render_embedded_review(
    payload: &ReviewCyclePayload,
    width: usize,
    theme: &ThemePalette,
) -> Vec<Line<'static>> {
    visible_lines(render_embedded_review_hyperlinks(payload, width, theme))
}

fn embedded_review_body(
    payload: &ReviewCyclePayload,
    width: usize,
    styles: ReviewStyles<'_>,
) -> Vec<HyperlinkLine> {
    let (_, status, tone) = review_status(payload);
    let color = match tone {
        ReviewTone::Accent => styles.accent,
        ReviewTone::Success => styles.success,
        ReviewTone::Warning => styles.warning,
        ReviewTone::Error => styles.error,
    };
    let width = width.clamp(1, 120);
    let mut title = format!(" {status}");
    let severities = display_severities(payload);
    if !severities.is_empty()
        && !review_failed(payload)
        && !matches!(
            payload.phase.as_str(),
            "cancelled" | "canceled" | "interrupted"
        )
    {
        title.push_str(&format!(
            " · {} {}",
            severities.len(),
            if severities.len() == 1 {
                "issue"
            } else {
                "issues"
            }
        ));
        for severity in ["P0", "P1", "P2", "P3"] {
            let count = severities
                .iter()
                .filter(|value| value.eq_ignore_ascii_case(severity))
                .count();
            if count > 0 {
                title.push_str(&format!(" · {severity} ×{count}"));
            }
        }
    }
    // The enclosing audit region already owns the active REVIEWING heading.
    // Standalone review cards retain their own lifecycle label.
    let mut lines = if status == "REVIEWING" {
        Vec::new()
    } else {
        wrapped_text(&title, width, color)
    };
    if review_failed(payload) {
        lines.extend(detail_rows(
            "Cause",
            &payload.failure_cause,
            width,
            styles.error,
            styles,
        ));
        lines.extend(detail_rows(
            "Detail",
            &payload.failure_detail,
            width,
            styles.fill,
            styles,
        ));
        for stat in review_failure_stats(payload) {
            lines.extend(wrapped_text(&stat, width, styles.muted));
        }
        return lines;
    }
    if matches!(
        payload.phase.as_str(),
        "cancelled" | "canceled" | "interrupted"
    ) {
        lines.extend(wrapped_text(
            "Review stopped before feedback was submitted.",
            width,
            styles.muted,
        ));
        return lines;
    }
    if !payload.review_target_label.is_empty() {
        lines.extend(detail_rows(
            "Target",
            &payload.review_target_label,
            width,
            styles.fill,
            styles,
        ));
    }
    let mut findings = payload.findings.iter().collect::<Vec<_>>();
    findings.sort_by_key(|f| match f.severity.to_ascii_uppercase().as_str() {
        "P0" => 0,
        "P1" => 1,
        "P2" => 2,
        _ => 3,
    });
    for (index, finding) in findings.iter().enumerate() {
        lines.push(HyperlinkLine::default());
        lines.extend(structured_finding(finding, Some(index + 1), width, styles));
    }
    if payload.findings.is_empty() && payload.outcome == "no_findings" {
        lines.push(HyperlinkLine::default());
        lines.extend(wrapped_text(
            "✓ No material findings.",
            width,
            styles.success,
        ));
    } else if payload.findings.is_empty() && !payload.summary.is_empty() {
        lines.extend(markdown_feedback_body(&payload.summary, width, styles));
    }
    if !payload.overall_assessment.trim().is_empty() {
        lines.push(HyperlinkLine::default());
        lines.extend(assessment_body(&payload.overall_assessment, width, styles));
    }
    lines
}

fn markdown_feedback_body(
    text: &str,
    width: usize,
    styles: ReviewStyles<'_>,
) -> Vec<HyperlinkLine> {
    use super::review_feedback::{feedback_sections, FeedbackSection};
    let mut lines = Vec::new();
    let mut number = 0;
    for section in feedback_sections(text) {
        if !lines.is_empty() {
            lines.push(HyperlinkLine::default());
        }
        match section {
            FeedbackSection::Text(body) => lines.extend(prefix_hyperlink_lines(
                markdown_body(
                    body.trim_matches(['\r', '\n']),
                    width.saturating_sub(4).max(1),
                    styles.fill,
                    styles,
                ),
                Span::styled("    ", styles.fill),
                Span::styled("    ", styles.fill),
            )),
            FeedbackSection::Annotation {
                severity,
                title,
                references,
                body,
            } => {
                number += 1;
                // A literal Markdown annotation is a display section only.
                // The payload outcome stays "submitted"; never infer a gate.
                lines.extend(structured_finding(
                    &ReviewFindingPayload {
                        severity,
                        title,
                        evidence_refs: references,
                        description: body.trim_matches(['\r', '\n']).into(),
                        ..Default::default()
                    },
                    Some(number),
                    width,
                    styles,
                ));
            }
            FeedbackSection::Overall(body) => {
                lines.extend(assessment_body(body.trim(), width, styles))
            }
        }
    }
    lines
}

fn wrapped_text(text: &str, width: usize, style: Style) -> Vec<HyperlinkLine> {
    text.lines()
        .flat_map(|line| {
            word_wrap_hyperlink_line(
                &HyperlinkLine::new(Line::from(Span::styled(line.to_string(), style)))
                    .with_web_urls(),
                RtOptions::new(width.max(1)),
            )
        })
        .collect()
}

fn markdown_body(
    text: &str,
    width: usize,
    style: Style,
    styles: ReviewStyles<'_>,
) -> Vec<HyperlinkLine> {
    crate::markdown::render_markdown_hyperlinks_preserving_breaks(
        text,
        width.max(1),
        styles.palette,
        styles.cwd,
    )
    .into_iter()
    .map(|mut line| {
        line.line.style = Style {
            bg: style.bg,
            ..Style::default()
        };
        for span in &mut line.line.spans {
            span.style = style.patch(span.style);
        }
        line
    })
    .collect()
}

/// Labels start their own row; all field bodies share the same inset at any width.
fn detail_rows(
    label: &str,
    value: &str,
    width: usize,
    style: Style,
    styles: ReviewStyles<'_>,
) -> Vec<HyperlinkLine> {
    detail_rows_with_link(label, value, None, width, "  ", style, styles)
}

fn detail_rows_with_link(
    label: &str,
    value: &str,
    target: Option<&str>,
    width: usize,
    indent: &str,
    style: Style,
    styles: ReviewStyles<'_>,
) -> Vec<HyperlinkLine> {
    if value.trim().is_empty() {
        return Vec::new();
    }
    let mut lines = Vec::new();
    if !label.is_empty() {
        lines.extend(wrapped_text(label, width, styles.muted));
    }
    if matches!(label, "Why" | "Fix") {
        let inner = width.saturating_sub(indent.width()).max(1);
        lines.extend(prefix_hyperlink_lines(
            markdown_body(value.trim_matches(['\r', '\n']), inner, style, styles),
            Span::styled(indent.to_string(), styles.muted),
            Span::styled(indent.to_string(), styles.muted),
        ));
        return lines;
    }
    let destination =
        target.and_then(|target| crate::markdown::links::destination(target, styles.cwd));
    for source in value.lines() {
        let mut line = HyperlinkLine::default();
        line.push_span(
            Span::styled(source.trim().to_string(), style),
            destination.as_deref(),
        );
        lines.extend(word_wrap_hyperlink_line(
            &line.with_web_urls(),
            RtOptions::new(width.max(1))
                .initial_indent(Line::from(Span::styled(indent.to_string(), styles.muted)))
                .subsequent_indent(Line::from(Span::styled(indent.to_string(), styles.fill))),
        ));
    }
    lines
}

pub(super) fn display_review_path(path: &str, cwd: Option<&str>) -> String {
    // A view-only relative label. The raw reference and link target never change.
    if let Some(cwd) = cwd.map(str::to_string).or_else(|| {
        std::env::current_dir()
            .ok()
            .map(|path| path.to_string_lossy().into_owned())
    }) {
        if let Some(relative) = path.strip_prefix(&format!("{}/", cwd.trim_end_matches('/'))) {
            return relative.to_string();
        }
    }
    path.to_string()
}

pub(crate) fn render_embedded_review_hyperlinks(
    payload: &ReviewCyclePayload,
    width: usize,
    theme: &ThemePalette,
) -> Vec<HyperlinkLine> {
    render_embedded_review_hyperlinks_with_cwd(payload, width, theme, None)
}

pub(crate) fn render_embedded_review_hyperlinks_with_cwd(
    payload: &ReviewCyclePayload,
    width: usize,
    theme: &ThemePalette,
    cwd: Option<&str>,
) -> Vec<HyperlinkLine> {
    render_embedded_review_hyperlinks_with_sources(payload, width, theme, cwd, &Default::default())
}

fn review_appendix(references: &str, diagnostics: &str, detail: ReviewDetail) -> String {
    [
        references,
        if detail == ReviewDetail::Full {
            diagnostics
        } else {
            ""
        },
    ]
    .into_iter()
    .filter(|text| !text.is_empty())
    .collect::<Vec<_>>()
    .join("\n\n")
}

fn numbered_review(
    payload: &ReviewCyclePayload,
    sources: &crate::citation_sources::CitationSources,
    sorted: bool,
    detail: ReviewDetail,
) -> (ReviewCyclePayload, String, String) {
    let mut payload = payload.clone();
    // Select visible content before citation numbering. Hidden explanations
    // must not leave orphan reference lists in the summary view.
    if detail == ReviewDetail::Summary {
        use super::review_feedback::{feedback_sections, FeedbackSection};
        payload.summary = feedback_sections(&payload.summary)
            .into_iter()
            .map(|section| match section {
                FeedbackSection::Text(body) => body,
                FeedbackSection::Annotation {
                    severity, title, ..
                } => format!("[{severity}] {title}"),
                FeedbackSection::Overall(body) => format!("Overall assessment: {body}"),
            })
            .collect::<Vec<_>>()
            .join("\n\n");
        for finding in &mut payload.findings {
            finding.description.clear();
            finding.remediation.clear();
        }
    }
    if sorted {
        payload
            .findings
            .sort_by_key(|f| f.severity.to_ascii_uppercase());
    }
    let mut document = crate::citation_sources::CitationDocument::new(sources.clone());
    document.reserve_labels(&payload.summary);
    document.reserve_labels(&payload.overall_assessment);
    for finding in &payload.findings {
        document.reserve_labels(&finding.description);
        document.reserve_labels(&finding.remediation);
    }
    if payload.findings.is_empty() {
        payload.summary = document.body(&payload.summary);
    } else {
        for finding in &mut payload.findings {
            // Titles use the plain renderer: preserve their Markdown formatting.
            finding.description = document.body(&finding.description);
            finding.remediation = document.body(&finding.remediation);
        }
    }
    payload.overall_assessment = document.body(&payload.overall_assessment);
    (payload, document.references(), document.diagnostics())
}

pub(crate) fn render_embedded_review_hyperlinks_with_sources(
    payload: &ReviewCyclePayload,
    width: usize,
    theme: &ThemePalette,
    cwd: Option<&str>,
    sources: &crate::citation_sources::CitationSources,
) -> Vec<HyperlinkLine> {
    render_embedded_review_hyperlinks_with_sources_detail(
        payload,
        width,
        theme,
        cwd,
        sources,
        ReviewDetail::Summary,
    )
}

pub(crate) fn render_embedded_review_hyperlinks_with_sources_detail(
    payload: &ReviewCyclePayload,
    width: usize,
    theme: &ThemePalette,
    cwd: Option<&str>,
    sources: &crate::citation_sources::CitationSources,
    detail: ReviewDetail,
) -> Vec<HyperlinkLine> {
    let styles = ReviewStyles {
        cwd,
        detail,
        ..ReviewStyles::new(theme)
    };
    let (payload, references, diagnostics) = numbered_review(payload, sources, true, detail);
    let appendix = review_appendix(&references, &diagnostics, detail);
    let mut lines = embedded_review_body(&payload, width, styles);
    if !appendix.is_empty() {
        lines.push(HyperlinkLine::default());
        lines.extend(markdown_body(
            &appendix,
            width.clamp(1, 120),
            styles.fill,
            styles,
        ));
    }
    styles.paint_background(lines)
}

fn structured_finding(
    finding: &ReviewFindingPayload,
    number: Option<usize>,
    width: usize,
    styles: ReviewStyles<'_>,
) -> Vec<HyperlinkLine> {
    let (title, title_refs) = super::review_feedback::split_title_references(&finding.title);
    let severity = finding.severity.to_ascii_uppercase();
    let number_prefix = number.map(|n| format!("{n:02}  ")).unwrap_or_default();
    let field_indent = " ".repeat(number_prefix.width());
    let field_width = width.saturating_sub(field_indent.width()).max(1);
    let prefix = format!("{number_prefix}{severity}  ");
    let mut lines = plain_hyperlink_lines(
        word_wrap_line(
            &Line::from(Span::styled(title, styles.title)),
            RtOptions::new(width.max(1))
                .initial_indent(Line::from(Span::styled(
                    prefix.clone(),
                    styles.severity(&severity),
                )))
                .subsequent_indent(Line::from(" ".repeat(prefix.width()))),
        )
        .iter()
        .map(line_to_static)
        .collect::<Vec<_>>(),
    );
    if styles.detail == ReviewDetail::Summary {
        return lines;
    }
    let evidence = finding
        .evidence_refs
        .iter()
        .chain(title_refs.iter())
        .enumerate()
        .flat_map(|(index, reference)| {
            detail_rows_with_link(
                if index == 0 { "Evidence" } else { "" },
                &display_review_path(reference, styles.cwd),
                (!reference.starts_with('§')).then_some(reference.as_str()),
                field_width,
                "",
                styles.muted,
                styles,
            )
        })
        .collect::<Vec<_>>();
    // Separate semantic fields, not individual evidence paths or wrapped rows.
    for section in [
        evidence,
        detail_rows_with_link(
            "Why",
            &finding.description,
            None,
            field_width,
            "",
            styles.fill,
            styles,
        ),
        detail_rows_with_link(
            "Fix",
            &finding.remediation,
            None,
            field_width,
            "",
            styles.title,
            styles,
        ),
    ] {
        if !section.is_empty() {
            lines.push(HyperlinkLine::default());
            // The issue number owns its own gutter. Labels and body text both
            // begin at the severity column, including wrapped continuation rows.
            lines.extend(prefix_hyperlink_lines(
                section,
                Span::styled(field_indent.clone(), styles.fill),
                Span::styled(field_indent.clone(), styles.fill),
            ));
        }
    }
    lines
}

#[cfg(test)]
fn full_embedded(
    payload: &ReviewCyclePayload,
    width: usize,
    theme: &ThemePalette,
) -> Vec<HyperlinkLine> {
    render_embedded_review_hyperlinks_with_sources_detail(
        payload,
        width,
        theme,
        None,
        &Default::default(),
        ReviewDetail::Full,
    )
}

#[cfg(test)]
fn full_card(
    payload: &ReviewCyclePayload,
    width: usize,
    theme: &ThemePalette,
) -> Vec<HyperlinkLine> {
    render_review_cycle_card_hyperlinks_with_sources_detail(
        payload,
        width,
        theme,
        None,
        &Default::default(),
        ReviewDetail::Full,
    )
}

#[cfg(test)]
mod citation_layout_tests {
    use super::*;

    #[test]
    fn real_feedback_aligns_fields_and_keeps_unmatched_ids_in_details() {
        let original = include_str!("../../tests/fixtures/review_citations.md");
        let payload = ReviewCyclePayload {
            summary: original.into(),
            phase: "completed".into(),
            outcome: "submitted".into(),
            ..Default::default()
        };
        for width in [20, 38, 77, 78, 79, 80, 106, 120] {
            let lines = full_embedded(&payload, width, &ThemePalette::default());
            assert!(
                lines.iter().all(|l| l.line.width() <= width),
                "width={width}"
            );
            let text = lines
                .iter()
                .map(|l| l.line.to_string())
                .collect::<Vec<_>>()
                .join("\n");
            let compact: String = text.chars().filter(|c| !c.is_whitespace()).collect();
            assert!(
                text.contains("Evidence") && !compact.contains("[1][2]"),
                "{text}"
            );
            assert!(
                !text.contains("引用未关联") && !text.contains('\u{e200}'),
                "{text}"
            );
            assert!(
                text.contains("turn894074search2") && text.contains("turn894074search0"),
                "{text}"
            );
            assert!(
                text.find("引用诊断").unwrap() > text.find("Why").unwrap(),
                "{text}"
            );
            let rows: Vec<_> = text.lines().collect();
            let title = rows.iter().find(|l| l.starts_with("01  P2  The")).unwrap();
            assert!(!title.contains("map.md"), "{text}");
            for label in ["Evidence", "Why"] {
                let index = rows.iter().position(|line| line.trim() == label).unwrap();
                let column = title.find("P2").unwrap();
                assert_eq!(rows[index].find(label), Some(column), "{text}");
                assert_eq!(
                    rows[index + 1].len() - rows[index + 1].trim_start().len(),
                    column,
                    "{text}"
                );
            }
        }
        assert_eq!(payload.summary, original);
    }

    #[test]
    fn multiple_findings_share_one_reference_numbering_scope() {
        let payload = ReviewCyclePayload {
            phase: "completed".into(),
            outcome: "submitted".into(),
            findings: vec![
                ReviewFindingPayload {
                    severity: "P1".into(),
                    title: "One".into(),
                    description: "Aciteturn1search0".into(),
                    ..Default::default()
                },
                ReviewFindingPayload {
                    severity: "P2".into(),
                    title: "Two".into(),
                    description: "Bciteturn2search0 Aciteturn1search0".into(),
                    ..Default::default()
                },
            ],
            ..Default::default()
        };
        let source_record = serde_json::json!({"raw_source":serde_json::json!({"sources":[
            {"ref_id":"turn1search0", "title":"One", "url":"https://example.org/one"},
            {"ref_id":"turn2search0", "title":"Two", "url":"https://example.org/two"}
        ]}).to_string()});
        let sources = crate::citation_sources::CitationSources::for_item(&source_record, &[]);
        let (formatted, references, diagnostics) =
            numbered_review(&payload, &sources, true, ReviewDetail::Full);
        assert!(diagnostics.is_empty());
        assert!(formatted.findings[0].description.contains("\\[1\\]"));
        assert!(formatted.findings[1].description.contains("\\[2\\]"));
        assert_eq!(references.matches("One").count(), 1);
        assert_eq!(references.matches("Two").count(), 1);
    }
}

pub(crate) fn render_review_cycle_card(
    payload: &ReviewCyclePayload,
    width: usize,
    theme: &ThemePalette,
) -> Vec<Line<'static>> {
    visible_lines(render_review_cycle_card_hyperlinks(payload, width, theme))
}

pub(crate) fn render_review_cycle_card_hyperlinks(
    payload: &ReviewCyclePayload,
    width: usize,
    theme: &ThemePalette,
) -> Vec<HyperlinkLine> {
    render_review_cycle_card_hyperlinks_with_cwd(payload, width, theme, None)
}

pub(crate) fn render_review_cycle_card_hyperlinks_with_cwd(
    payload: &ReviewCyclePayload,
    width: usize,
    theme: &ThemePalette,
    cwd: Option<&str>,
) -> Vec<HyperlinkLine> {
    render_review_cycle_card_hyperlinks_with_sources(
        payload,
        width,
        theme,
        cwd,
        &Default::default(),
    )
}

pub(crate) fn render_review_cycle_card_hyperlinks_with_sources(
    payload: &ReviewCyclePayload,
    width: usize,
    theme: &ThemePalette,
    cwd: Option<&str>,
    sources: &crate::citation_sources::CitationSources,
) -> Vec<HyperlinkLine> {
    render_review_cycle_card_hyperlinks_with_sources_detail(
        payload,
        width,
        theme,
        cwd,
        sources,
        ReviewDetail::Summary,
    )
}

pub(crate) fn render_review_cycle_card_hyperlinks_with_sources_detail(
    payload: &ReviewCyclePayload,
    width: usize,
    theme: &ThemePalette,
    cwd: Option<&str>,
    sources: &crate::citation_sources::CitationSources,
    detail: ReviewDetail,
) -> Vec<HyperlinkLine> {
    let styles = ReviewStyles {
        cwd,
        detail,
        ..ReviewStyles::new(theme)
    };
    let (payload, references, diagnostics) = numbered_review(payload, sources, false, detail);
    let appendix = review_appendix(&references, &diagnostics, detail);
    let mut lines = review_cycle_card_body(&payload, width, styles);
    if !appendix.is_empty() {
        if width < 40 {
            lines.push(HyperlinkLine::default());
            lines.extend(markdown_body(&appendix, width, styles.fill, styles));
        } else {
            let bottom = lines.pop();
            lines.push(framed_content_line(Vec::new(), width, styles));
            let inset = Span::styled("  ", styles.fill);
            lines.extend(
                prefix_hyperlink_lines(
                    markdown_body(&appendix, width.saturating_sub(6), styles.fill, styles),
                    inset.clone(),
                    inset,
                )
                .into_iter()
                .map(|line| framed_content_line(line, width, styles)),
            );
            lines.extend(bottom);
        }
    }
    if width < 40 {
        styles.paint_background(lines)
    } else {
        lines
    }
}

fn review_cycle_card_body(
    payload: &ReviewCyclePayload,
    width: usize,
    styles: ReviewStyles<'_>,
) -> Vec<HyperlinkLine> {
    let width = width.max(1);
    let role = match payload.review_kind.trim().to_ascii_lowercase().as_str() {
        "readiness" => "READINESS",
        "reviewer" => "REVIEWER",
        _ => "REVIEW",
    };
    let stage = payload.stage.trim().replace('_', " ").to_uppercase();
    let heading = if stage.is_empty() {
        role.to_string()
    } else {
        format!("{role} · {stage}")
    };
    let (signal, status, tone) = review_status(payload);
    let status_style = match tone {
        ReviewTone::Accent => styles.accent,
        ReviewTone::Success => styles.success,
        ReviewTone::Warning => styles.warning,
        ReviewTone::Error => styles.error,
    };

    // Below 40 columns, the frame and detail-label indentation consume most
    // of the content width.  Switch to a complete unframed projection rather
    // than truncating metadata, counts, evidence, or assessment text.
    if width < 40 {
        return render_plain_review(
            payload,
            &heading,
            signal,
            status,
            width,
            styles,
            status_style,
        );
    }

    let mut lines = vec![framed_top(&heading, status, width, styles, status_style)];
    let metadata = review_metadata(payload);
    let counts = severity_counts(payload);
    if width >= 72 && !counts.is_empty() {
        let inner_width = width.saturating_sub(2);
        let gap = inner_width.saturating_sub(metadata.width() + counts.width() + 4);
        lines.push(framed_content_line(
            vec![
                Span::styled(format!("  {metadata}"), styles.muted),
                Span::styled(" ".repeat(gap), styles.fill),
                Span::styled(counts, styles.muted),
                Span::styled("  ", styles.fill),
            ],
            width,
            styles,
        ));
    } else {
        lines.push(framed_text_line(
            &format!("  {metadata}"),
            width,
            styles.muted,
            styles,
        ));
        if !counts.is_empty() {
            lines.push(framed_text_line(
                &format!("  {counts}"),
                width,
                styles.muted,
                styles,
            ));
        }
    }

    for text in review_target_lines(payload) {
        lines.extend(render_legacy_summary(&text, width, styles));
    }

    if review_failed(payload) {
        lines.push(inset_separator(width, styles));
        lines.extend(render_failure_details(payload, width, styles, true));
        lines.push(framed_bottom(width, styles));
        return lines;
    }

    if payload.outcome == "no_findings" {
        lines.push(inset_separator(width, styles));
        lines.push(framed_text_line(
            "  ✓  No material findings.",
            width,
            styles.success,
            styles,
        ));
        if !payload.overall_assessment.trim().is_empty() {
            lines.extend(render_assessment(
                &payload.overall_assessment,
                width,
                styles,
            ));
        }
        lines.push(framed_bottom(width, styles));
        return lines;
    }

    if payload.feedback_format == "structured_v2" && !payload.findings.is_empty() {
        lines.push(inset_separator(width, styles));
        for (index, finding) in payload.findings.iter().enumerate() {
            if index > 0 {
                lines.push(framed_content_line(Vec::new(), width, styles));
            }
            lines.extend(render_finding(finding, width, styles));
        }
        if !payload.overall_assessment.trim().is_empty() {
            lines.push(inset_separator(width, styles));
            lines.extend(render_assessment(
                &payload.overall_assessment,
                width,
                styles,
            ));
        }
    } else if !payload.summary.trim().is_empty() {
        lines.push(inset_separator(width, styles));
        // Match the structured feedback inset. Reserve both margins before
        // Markdown wraps, and shift semantic hyperlinks with the visible text.
        let body = markdown_feedback_body(&payload.summary, width.saturating_sub(6), styles);
        let inset = Span::styled("  ", styles.fill);
        lines.extend(
            prefix_hyperlink_lines(body, inset.clone(), inset)
                .into_iter()
                .map(|line| framed_content_line(line, width, styles)),
        );
        if !payload.overall_assessment.trim().is_empty() {
            lines.extend(render_assessment(
                &payload.overall_assessment,
                width,
                styles,
            ));
        }
    } else {
        lines.push(inset_separator(width, styles));
        lines.push(framed_text_line(
            &format!("  {signal}  Review in progress…"),
            width,
            status_style,
            styles,
        ));
    }
    lines.push(framed_bottom(width, styles));
    lines
}

fn review_status(payload: &ReviewCyclePayload) -> (&'static str, &'static str, ReviewTone) {
    if review_failed(payload) {
        return ("✗", "FAILED", ReviewTone::Error);
    }
    if matches!(
        payload.phase.as_str(),
        "cancelled" | "canceled" | "interrupted"
    ) {
        return ("·", "CANCELLED", ReviewTone::Warning);
    }
    if payload.gate_disposition == "historical" {
        return ("·", "HISTORICAL", ReviewTone::Accent);
    }
    if payload.gate_disposition == "carried" {
        return ("!", "CARRIED", ReviewTone::Warning);
    }
    match (payload.phase.as_str(), payload.outcome.as_str()) {
        (_, "no_findings") if payload.findings.is_empty() => {
            ("✓", "NO FINDINGS", ReviewTone::Success)
        }
        ("applying_feedback", _) => ("↻", "CORRECTING", ReviewTone::Warning),
        (_, "findings") => ("!", "FINDINGS", ReviewTone::Warning),
        ("completed", _) | ("feedback_received", _) => ("·", "SUBMITTED", ReviewTone::Accent),
        ("residual", _) | (_, "residual_findings") => ("!", "RESIDUAL", ReviewTone::Warning),
        ("failed", _) | (_, "failed") => ("✗", "FAILED", ReviewTone::Error),
        _ => ("⦁", "REVIEWING", ReviewTone::Accent),
    }
}

fn review_metadata(payload: &ReviewCyclePayload) -> String {
    let mut parts = vec![format!("attempt {}", payload.attempt.max(1))];
    if payload.round > 0 && payload.max_rounds > 0 {
        parts.push(format!("round {}/{}", payload.round, payload.max_rounds));
    } else if payload.round > 0 {
        parts.push(format!("round {}", payload.round));
    }
    if payload.elapsed_ms > 0 {
        parts.push(crate::style::turn_elapsed_label(payload.elapsed_ms));
    }
    let disposition = match payload.gate_disposition.as_str() {
        "decision_basis" => "decision basis",
        "historical" => "historical",
        "carried" => "carried forward",
        "pending" => "pending gate",
        _ => "",
    };
    if !disposition.is_empty() {
        parts.push(disposition.to_string());
    }
    parts.join(" · ")
}

fn review_target_lines(_payload: &ReviewCyclePayload) -> Vec<String> {
    Vec::new()
}

fn review_failed(payload: &ReviewCyclePayload) -> bool {
    payload.phase == "failed" || payload.outcome == "failed"
}

fn review_failure_stats(payload: &ReviewCyclePayload) -> Vec<String> {
    let mut lines = Vec::new();
    if payload.provider_request_count > 0 {
        lines.push(format!(
            "Provider activity · {} {} · {} {}",
            payload.provider_request_count,
            if payload.provider_request_count == 1 {
                "request"
            } else {
                "requests"
            },
            payload.provider_retry_count,
            if payload.provider_retry_count == 1 {
                "retry"
            } else {
                "retries"
            },
        ));
    }
    if payload.final_request_attempt_count > 0 {
        lines.push(format!(
            "Final request · {} {} · {} {}",
            payload.final_request_attempt_count,
            if payload.final_request_attempt_count == 1 {
                "attempt"
            } else {
                "attempts"
            },
            payload.final_request_retry_count,
            if payload.final_request_retry_count == 1 {
                "retry"
            } else {
                "retries"
            },
        ));
    }
    lines
}

fn render_failure_details(
    payload: &ReviewCyclePayload,
    width: usize,
    styles: ReviewStyles<'_>,
    framed: bool,
) -> Vec<HyperlinkLine> {
    let cause = if payload.failure_cause.trim().is_empty() {
        "Review execution failed"
    } else {
        payload.failure_cause.trim()
    };
    let mut entries = vec![("Cause", cause.to_string())];
    if !payload.failure_detail.trim().is_empty() {
        entries.push(("Detail", payload.failure_detail.trim().to_string()));
    }
    entries.extend(
        review_failure_stats(payload)
            .into_iter()
            .map(|value| ("", value)),
    );
    entries
        .into_iter()
        .flat_map(|(label, value)| {
            if framed {
                render_labeled_detail(label, &value, width, styles.error, styles)
            } else {
                let mut lines = Vec::new();
                if label.is_empty() {
                    append_plain_wrapped(
                        &mut lines,
                        Line::from(Span::styled(value, styles.muted)),
                        width,
                    );
                } else {
                    append_plain_labeled(&mut lines, label, &value, width, styles);
                }
                lines
            }
        })
        .collect()
}

fn display_severities(payload: &ReviewCyclePayload) -> Vec<String> {
    if !payload.findings.is_empty() {
        return payload
            .findings
            .iter()
            .map(|finding| finding.severity.clone())
            .collect();
    }
    if payload.outcome == "no_findings"
        || review_failed(payload)
        || matches!(
            payload.phase.as_str(),
            "cancelled" | "canceled" | "interrupted"
        )
    {
        return Vec::new();
    }
    // Literal annotations are presentation sections only; never set a verdict
    // or create backend findings from prose or examples inside code fences.
    super::review_feedback::feedback_sections(&payload.summary)
        .into_iter()
        .filter_map(|section| match section {
            super::review_feedback::FeedbackSection::Annotation { severity, .. } => Some(severity),
            _ => None,
        })
        .collect()
}

fn severity_counts(payload: &ReviewCyclePayload) -> String {
    let severities = display_severities(payload);
    if severities.is_empty() {
        return String::new();
    }
    ["P0", "P1", "P2", "P3"]
        .iter()
        .map(|severity| {
            let count = severities
                .iter()
                .filter(|value| value.eq_ignore_ascii_case(severity))
                .count();
            format!("{severity} {count}")
        })
        .collect::<Vec<_>>()
        .join("  ")
}

fn render_finding(
    finding: &ReviewFindingPayload,
    width: usize,
    styles: ReviewStyles<'_>,
) -> Vec<HyperlinkLine> {
    // Standalone and streamed Findings share the same detail layout. The
    // surrounding frame belongs to this renderer, not to the finding body.
    structured_finding(finding, None, width.saturating_sub(6).max(1), styles)
        .into_iter()
        .map(|line| {
            let line = prefix_hyperlink_lines(
                vec![line],
                Span::styled("  ", styles.fill),
                Span::styled("  ", styles.fill),
            )
            .remove(0);
            framed_content_line(line, width, styles)
        })
        .collect()
}

fn render_labeled_detail(
    label: &str,
    value: &str,
    width: usize,
    style: Style,
    styles: ReviewStyles<'_>,
) -> Vec<HyperlinkLine> {
    let inner_width = width.saturating_sub(2).max(1);
    let prefix = if label.is_empty() {
        "                 ".to_string()
    } else {
        format!("      {label} · ")
    };
    adaptive_wrap_line(
        &Line::from(Span::styled(value.trim().to_string(), style)),
        RtOptions::new(inner_width)
            .initial_indent(Line::from(Span::styled(prefix.clone(), styles.muted)))
            .subsequent_indent(Line::from(Span::styled(
                " ".repeat(prefix.width()),
                styles.fill,
            ))),
    )
    .iter()
    .map(line_to_static)
    .map(|line| framed_content_line(HyperlinkLine::new(line).with_web_urls(), width, styles))
    .collect()
}

fn render_assessment(
    assessment: &str,
    width: usize,
    styles: ReviewStyles<'_>,
) -> Vec<HyperlinkLine> {
    assessment_body(assessment, width.saturating_sub(2).max(1), styles)
        .into_iter()
        .enumerate()
        .map(|(index, line)| {
            let mut framed = framed_content_line(line, width, styles);
            if index == 0 {
                framed.line.spans[0] = Span::styled("├─", styles.border);
                // The junction consumes one more column than the normal rail.
                let padding = framed.line.spans.len() - 2;
                framed.line.spans[padding].content.to_mut().pop();
            }
            framed
        })
        .collect()
}

pub(crate) fn is_overall_section_heading(line: &Line<'_>, theme: &ThemePalette) -> bool {
    // Match the renderer-owned heading, not an occurrence of "OVERALL" in
    // assessment text (which has body styling and an inset).
    line.spans
        == [Span::styled(
            " OVERALL",
            ReviewStyles::new(theme).severity("P1"),
        )]
}

/// A rail-level heading and inset body shared by framed and streamed results.
fn assessment_body(assessment: &str, width: usize, styles: ReviewStyles<'_>) -> Vec<HyperlinkLine> {
    let width = width.clamp(1, 120);
    let inset = if width >= 38 {
        4
    } else if width >= 12 {
        2
    } else {
        0
    };
    let content_width = width.saturating_sub(inset).max(1);
    let pad = |line: HyperlinkLine| {
        prefix_hyperlink_lines(
            vec![line],
            Span::styled(" ".repeat(inset), styles.fill),
            Span::styled(" ".repeat(inset), styles.fill),
        )
        .remove(0)
    };
    let mut lines = wrapped_text(" OVERALL", width, styles.severity("P1"));
    lines.push(HyperlinkLine::default());
    lines.extend(
        markdown_body(
            assessment.trim_matches(['\r', '\n']),
            content_width,
            styles.fill,
            styles,
        )
        .into_iter()
        .map(pad),
    );
    while lines
        .last()
        .is_some_and(|line| line.line.to_string().trim().is_empty())
    {
        lines.pop();
    }
    lines.push(HyperlinkLine::default());
    lines
}

fn render_legacy_summary(
    summary: &str,
    width: usize,
    styles: ReviewStyles<'_>,
) -> Vec<HyperlinkLine> {
    let inner_width = width.saturating_sub(6).max(1);
    summary
        .trim()
        .lines()
        .flat_map(|source_line| {
            adaptive_wrap_line(
                &Line::from(Span::styled(source_line.to_string(), styles.fill)),
                RtOptions::new(inner_width),
            )
            .iter()
            .map(line_to_static)
            .collect::<Vec<_>>()
        })
        .map(|line| {
            let mut spans = vec![Span::styled("  ", styles.fill)];
            spans.extend(line.spans);
            framed_content_line(spans, width, styles)
        })
        .collect()
}

fn render_plain_review(
    payload: &ReviewCyclePayload,
    heading: &str,
    signal: &str,
    status: &str,
    width: usize,
    styles: ReviewStyles<'_>,
    status_style: Style,
) -> Vec<HyperlinkLine> {
    let mut lines = Vec::new();
    append_plain_wrapped(
        &mut lines,
        Line::from(vec![
            Span::styled(format!("{signal} "), status_style),
            Span::styled(heading.to_string(), styles.title),
            Span::styled(format!(" · {status}"), status_style),
        ]),
        width,
    );
    append_plain_wrapped(
        &mut lines,
        Line::from(Span::styled(review_metadata(payload), styles.muted)),
        width,
    );
    let counts = severity_counts(payload);
    if !counts.is_empty() {
        append_plain_wrapped(
            &mut lines,
            Line::from(Span::styled(counts, styles.muted)),
            width,
        );
    }
    for text in review_target_lines(payload) {
        append_plain_wrapped(
            &mut lines,
            Line::from(Span::styled(text, styles.muted)),
            width,
        );
    }

    if review_failed(payload) {
        lines.extend(render_failure_details(payload, width, styles, false));
        return lines;
    }

    if payload.outcome == "no_findings" {
        append_plain_wrapped(
            &mut lines,
            Line::from(Span::styled("✓ No material findings.", styles.success)),
            width,
        );
        if !payload.overall_assessment.trim().is_empty() {
            lines.push(HyperlinkLine::default());
            lines.extend(assessment_body(&payload.overall_assessment, width, styles));
        }
        return lines;
    }

    if payload.feedback_format == "structured_v2" && !payload.findings.is_empty() {
        for finding in &payload.findings {
            lines.extend(structured_finding(finding, None, width, styles));
        }
        if !payload.overall_assessment.trim().is_empty() {
            lines.push(HyperlinkLine::default());
            lines.extend(assessment_body(&payload.overall_assessment, width, styles));
        }
    } else if !payload.summary.trim().is_empty() {
        lines.extend(markdown_feedback_body(&payload.summary, width, styles));
        if !payload.overall_assessment.trim().is_empty() {
            lines.extend(assessment_body(&payload.overall_assessment, width, styles));
        }
    } else {
        append_plain_wrapped(
            &mut lines,
            Line::from(Span::styled("Review in progress…", status_style)),
            width,
        );
    }
    lines
}

fn append_plain_labeled(
    lines: &mut Vec<HyperlinkLine>,
    label: &str,
    value: &str,
    width: usize,
    styles: ReviewStyles<'_>,
) {
    if value.is_empty() {
        return;
    }
    append_plain_wrapped(
        lines,
        Line::from(vec![
            Span::styled(format!("{label} · "), styles.muted),
            Span::styled(value.to_string(), styles.fill),
        ]),
        width,
    );
}

fn append_plain_wrapped(lines: &mut Vec<HyperlinkLine>, line: Line<'static>, width: usize) {
    lines.extend(word_wrap_hyperlink_line(
        &HyperlinkLine::new(line).with_web_urls(),
        RtOptions::new(width.max(1)),
    ));
}

fn framed_top(
    heading: &str,
    status: &str,
    width: usize,
    styles: ReviewStyles<'_>,
    status_style: Style,
) -> HyperlinkLine {
    let status_budget = width.saturating_sub(12).min(status.width());
    let status = fit_text(status, status_budget);
    let heading_budget = width.saturating_sub(status.width() + 9).max(1);
    let heading = fit_text(heading, heading_budget);
    let used = 8 + heading.width() + status.width();
    let rule = "─".repeat(width.saturating_sub(used).max(1));
    HyperlinkLine::new(Line::from(vec![
        Span::styled("╭─ ", styles.border),
        Span::styled(heading, styles.accent),
        Span::styled(format!(" {rule} "), styles.border),
        Span::styled(status, status_style),
        Span::styled(" ─╮", styles.border),
    ]))
}

fn inset_separator(width: usize, styles: ReviewStyles<'_>) -> HyperlinkLine {
    let inner = width.saturating_sub(2);
    let rule = if inner > 4 {
        format!("  {}  ", "╌".repeat(inner - 4))
    } else {
        " ".repeat(inner)
    };
    framed_content_line(vec![Span::styled(rule, styles.border)], width, styles)
}

fn framed_text_line(
    text: &str,
    width: usize,
    style: Style,
    styles: ReviewStyles<'_>,
) -> HyperlinkLine {
    let inner_width = width.saturating_sub(3);
    framed_content_line(
        vec![Span::styled(fit_text(text, inner_width), style)],
        width,
        styles,
    )
}

fn framed_content_line(
    content: impl Into<HyperlinkLine>,
    width: usize,
    styles: ReviewStyles<'_>,
) -> HyperlinkLine {
    let content = content.into();
    let inner = width.saturating_sub(2);
    let padding = inner.saturating_sub(content.line.width());
    let mut line = HyperlinkLine::new(Line::from(Span::styled("│", styles.border)));
    line.append(content);
    line.push_span(Span::styled(" ".repeat(padding), styles.fill), None);
    line.push_span(Span::styled("│", styles.border), None);
    line
}

fn framed_bottom(width: usize, styles: ReviewStyles<'_>) -> HyperlinkLine {
    HyperlinkLine::new(Line::from(vec![
        Span::styled("╰", styles.border),
        Span::styled("─".repeat(width.saturating_sub(2)), styles.border),
        Span::styled("╯", styles.border),
    ]))
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

#[cfg(test)]
mod tests {
    use super::*;

    fn plain<T: Into<HyperlinkLine>>(lines: Vec<T>) -> String {
        lines
            .into_iter()
            .map(|line| {
                line.into()
                    .line
                    .spans
                    .into_iter()
                    .map(|span| span.content.into_owned())
                    .collect::<String>()
            })
            .collect::<Vec<_>>()
            .join("\n")
    }

    fn finding(severity: &str, title: &str) -> ReviewFindingPayload {
        ReviewFindingPayload {
            severity: severity.to_string(),
            title: title.to_string(),
            description: "This explanation is intentionally long enough to require compact presentation on narrow terminals.".to_string(),
            evidence_refs: vec![
                "/Users/example/project/output/report.md:42".to_string(),
                "matrix:E-17".to_string(),
            ],
            display_evidence_refs: vec![
                "output/report.md:42".to_string(),
                "matrix:E-17".to_string(),
            ],
            remediation: "Add a precise claim-to-evidence mapping and rerun validation."
                .to_string(),
        }
    }

    fn payload() -> ReviewCyclePayload {
        ReviewCyclePayload {
            stage: "output_synthesis".to_string(),
            attempt: 1,
            review_kind: "reviewer".to_string(),
            round: 1,
            max_rounds: 2,
            phase: "feedback_received".to_string(),
            outcome: "findings".to_string(),
            feedback_format: "structured_v2".to_string(),
            findings: vec![
                finding("P0", "Do not publish an invalid result"),
                finding("P1", "Restore the missing evidence chain"),
                finding("P2", "Clarify the evaluation boundary"),
                finding("P3", "Tighten the low-impact wording"),
            ],
            overall_assessment: "The result remains blocked until the evidence chain is restored."
                .to_string(),
            ..ReviewCyclePayload::default()
        }
    }

    #[test]
    fn actual_feedback_keeps_evidence_and_overall_in_the_existing_review_layout() {
        let payload = ReviewCyclePayload {
            phase: "completed".into(),
            outcome: "submitted".into(),
            summary: include_str!("../../tests/fixtures/method_review_feedback.md").into(),
            ..Default::default()
        };
        for width in [40, 80, 120] {
            let lines = visible_lines(full_embedded(&payload, width, &ThemePalette::default()));
            assert!(lines.iter().all(|line| line.width() <= width));
            let text = plain(lines);
            assert_eq!(text.matches("Evidence").count(), 2, "{text}");
            let compact: String = text.chars().filter(|c| !c.is_whitespace()).collect();
            for reference in [
                "literature_grounding/evidence_20261023.md:38",
                "method_design/跨被试语音解码_方法设计.md:45",
                "literature_grounding/source_evidence.md:14",
                "method_design/plan.md:58-63",
            ] {
                assert!(compact.contains(reference), "Missing {reference}: {text}");
            }
            assert!(
                text.contains("OVERALL") && text.contains("SUBMITTED"),
                "{text}"
            );
            assert!(
                !text.contains("NO FINDINGS") && !text.contains("ACCEPTED"),
                "{text}"
            );
        }
    }

    #[test]
    fn markdown_feedback_restores_numbered_annotations_and_overall_without_changing_verdict() {
        let payload=ReviewCyclePayload {phase:"completed".into(), outcome:"submitted".into(),
            summary:"[P2] Evidence mismatch - `notes.md:8`; `sources.md:11`\nThe recorded access levels disagree.\n\n[P3] Citation scope\n请核对引用边界。\n\n总体判断：需要澄清两处记录。".into(), ..Default::default()};
        for width in [40, 80, 120] {
            let lines = render_embedded_review(&payload, width, &ThemePalette::default());
            let text = plain(lines.clone());
            assert!(text.contains("01  P2") && text.contains("02  P3"), "{text}");
            assert!(
                text.contains("Evidence") && text.contains("OVERALL"),
                "{text}"
            );
            assert!(
                text.contains("SUBMITTED") && !text.contains("NO FINDINGS"),
                "{text}"
            );
            assert!(
                text.contains("2 issues") && text.contains("P2 ×1") && text.contains("P3 ×1"),
                "{text}"
            );
            assert!(!text.contains("Fix"), "No remedy may be invented");
            assert!(lines.iter().all(|line| line.width() <= width));
            let card = plain(render_review_cycle_card(
                &payload,
                width,
                &ThemePalette::default(),
            ));
            assert!(
                !card.contains("No material findings"),
                "Completed feedback is not a no-findings verdict: {card}"
            );
        }
    }

    #[test]
    fn annotation_counts_ignore_code_and_preserve_structured_finding_precedence() {
        let mut value = ReviewCyclePayload { phase:"completed".into(), outcome:"submitted".into(),
            summary:"[P2] First issue\n正文。\n\n[P2] Second issue\nMore evidence.\n\n```text\n[P1] example only\n```".into(),
            ..Default::default() };
        let text = plain(render_embedded_review(&value, 80, &ThemePalette::default()));
        assert!(text.contains("2 issues · P2 ×2"), "{text}");
        assert!(!text.contains("P1 ×1"), "{text}");
        assert_eq!(value.outcome, "submitted");
        value.findings = vec![finding("P1", "Canonical structured finding")];
        let text = plain(render_embedded_review(&value, 80, &ThemePalette::default()));
        assert!(text.contains("1 issue · P1 ×1"), "{text}");
        assert!(!text.contains("P2 ×2"), "{text}");
    }

    #[test]
    fn review_prose_findings_and_overall_render_markdown_tables() {
        let table = "| Source | Protocol |\n| --- | --- |\n| **EEG** | `LOSO` |";
        let mut value = ReviewCyclePayload { phase:"completed".into(), outcome:"submitted".into(),
            summary:format!("**Evidence summary**\n\n{table}"), overall_assessment:format!("**Assessment**\n\n{table}\n\nSource: https://example.org/a/very/long/evidence/locator\n\n```text\nvery_long_code_identifier_that_stays_inside_the_review\n```"), ..Default::default() };
        for structured in [false, true] {
            if structured {
                let mut issue = finding("P2", "Evidence boundary");
                issue.description = format!("Check **protocol**.\n\n{table}");
                value.findings = vec![issue];
                value.feedback_format = "structured_v2".into();
            }
            for width in [20, 32, 40, 80, 120] {
                let lines = render_embedded_review(&value, width, &ThemePalette::default());
                let text = plain(lines.clone());
                assert!(
                    !text.contains("**") && !text.contains("`LOSO`") && !text.contains("| ---"),
                    "{text}"
                );
                assert!(text.contains("EEG") && text.contains("LOSO"), "{text}");
                assert!(
                    lines.iter().all(|line| line.width() <= width),
                    "{width}: {text}"
                );
                let card = render_review_cycle_card(&value, width, &ThemePalette::default());
                let text = plain(card.clone());
                assert!(
                    !text.contains("**") && !text.contains("`LOSO`") && !text.contains("| ---"),
                    "{text}"
                );
                assert!(
                    text.contains("EEG")
                        && text.contains("LOSO")
                        && !text.contains("No material findings"),
                    "{text}"
                );
                assert!(
                    card.iter().all(|line| line.width() <= width),
                    "{width}: {text}"
                );
            }
        }
    }

    #[test]
    fn finding_fields_have_one_spacer_and_evidence_paths_stay_together() {
        let mut item = finding("P2", "Citation closure");
        item.evidence_refs = vec![
            "paper.md:87".into(),
            "paper.md:121".into(),
            "paper.md:123".into(),
        ];
        let theme = ThemePalette::default();
        let styles = ReviewStyles {
            detail: ReviewDetail::Full,
            ..ReviewStyles::new(&theme)
        };
        for width in [20, 32, 40, 60, 80, 120] {
            let lines = visible_lines(structured_finding(&item, Some(1), width, styles));
            assert!(lines.iter().all(|line| line.width() <= width));
            let rows = lines.iter().map(ToString::to_string).collect::<Vec<_>>();
            for label in ["Evidence", "Why", "Fix"] {
                let at = rows
                    .iter()
                    .position(|row| row.trim_start().starts_with(label))
                    .unwrap();
                let column = rows[0].find("P2").unwrap();
                assert_eq!(
                    rows[at].find(label),
                    Some(column),
                    "{width}: label must align with severity"
                );
                assert_eq!(
                    rows[at + 1].len() - rows[at + 1].trim_start().len(),
                    column,
                    "{width}: body must align with severity"
                );
                assert!(
                    rows[at - 1].trim().is_empty(),
                    "{width}: missing spacer before {label}"
                );
                assert!(
                    !rows[at - 2].trim().is_empty(),
                    "{width}: duplicate spacer before {label}"
                );
            }
            let evidence_end = rows
                .iter()
                .position(|row| row.contains("paper.md:123"))
                .unwrap();
            assert!(rows[evidence_end - 1].contains("paper.md:121"));
            assert!(rows[evidence_end - 2].contains("paper.md:87"));
        }
        item.description.clear();
        item.evidence_refs.clear();
        let rows = plain(structured_finding(&item, Some(1), 80, styles));
        assert!(!rows.contains("Why"));
        assert!(!rows.contains("Evidence"));
        assert!(!rows.contains("\n\n\n"));
    }

    #[test]
    fn overall_has_a_rail_level_heading_and_consistent_cjk_wrapping() {
        let assessment = "主稿、证据说明、BibTeX 和范围声明均已持久化，任务分类、证据边界和预印本状态也写得清楚。处理上述文内引用闭合问题后，可进入后续审阅。\n\n保留这段独立说明及完整内容。";
        let theme = ThemePalette::default();
        let styles = ReviewStyles::new(&theme);
        for width in [20, 32, 40, 60, 80, 120, 220] {
            let lines = visible_lines(assessment_body(assessment, width, styles));
            assert!(lines.iter().all(|line| line.width() <= width.min(120)));
            let rows = lines.iter().map(ToString::to_string).collect::<Vec<_>>();
            let indent = if width >= 38 { "    " } else { "  " };
            assert_eq!(rows[0], " OVERALL");
            assert!(rows[1].trim().is_empty());
            assert!(rows[2..]
                .iter()
                .filter(|row| !row.trim().is_empty())
                .all(|row| row.starts_with(indent)));
            assert!(
                rows[2..].iter().any(|row| row.trim().is_empty()),
                "Paragraph break lost"
            );
            let content: String = rows[2..]
                .join("")
                .chars()
                .filter(|character| !character.is_whitespace())
                .collect();
            let expected: String = assessment
                .chars()
                .filter(|character| !character.is_whitespace())
                .collect();
            assert_eq!(content, expected);
            if width >= 40 {
                let framed = visible_lines(render_assessment(assessment, width, styles));
                assert!(framed[0].to_string().starts_with("├─ OVERALL"));
                assert!(framed.iter().all(|line| line.width() == width));
            }
        }
    }

    #[test]
    fn no_findings_keeps_assessment_without_retired_workspace_binding() {
        let mut payload = payload();
        payload.findings.clear();
        payload.outcome = "no_findings".to_string();
        payload.review_target = "/run/current/core-round-002/trajectory.jsonl".to_string();
        payload.overall_assessment = "Only the recorded scope was considered.".to_string();
        for width in [19, 40, 80, 120, 220] {
            let lines = render_review_cycle_card(&payload, width, &ThemePalette::default());
            assert!(
                lines.iter().all(|line| line.width() <= width),
                "{width}: {lines:?}"
            );
            let compact: String = plain(lines)
                .chars()
                .filter(|c| !c.is_whitespace() && !"│─╌╭╮╰╯".contains(*c))
                .collect();
            assert!(compact.contains("NOFINDINGS"));
            assert!(!compact.contains(&payload.review_target));
            assert!(compact.contains("Onlytherecordedscopewasconsidered."));
            assert!(!compact.contains("ArtifactbindingnotverifiedbyUI"));
        }
    }

    #[test]
    fn structured_card_preserves_every_finding_title_at_supported_widths() {
        let payload = payload();
        for width in [8, 19, 20, 40, 80, 120, 190] {
            let lines = render_review_cycle_card(&payload, width, &ThemePalette::default());
            assert!(lines.iter().all(|line| line.width() <= width));
            let rendered = plain(lines);
            let compact = rendered
                .chars()
                .filter(|character| {
                    !character.is_whitespace()
                        && !matches!(character, '│' | '╭' | '╮' | '╰' | '╯' | '─' | '╌')
                })
                .collect::<String>();
            for title in [
                "Do not publish an invalid result",
                "Restore the missing evidence chain",
                "Clarify the evaluation boundary",
                "Tighten the low-impact wording",
            ] {
                let expected = title
                    .chars()
                    .filter(|character| !character.is_whitespace())
                    .collect::<String>();
                assert!(compact.contains(&expected), "width={width}\n{rendered}");
            }
            assert!(compact.contains("P01"), "{rendered}");
            assert!(compact.contains("P31"), "{rendered}");
            assert!(!rendered.contains("Evidence"), "{rendered}");
            assert!(!compact.contains("output/report.md:42"), "{rendered}");
            assert!(!compact.contains("matrix:E-17"), "{rendered}");
            assert!(!rendered.contains("Why"), "{rendered}");
            assert!(!rendered.contains("Fix"), "{rendered}");
            assert!(
                !rendered.contains("Ctrl+T for complete review text"),
                "{rendered}"
            );
        }
    }

    #[test]
    fn structured_card_preserves_full_evidence_why_fix_and_overall_text() {
        let mut payload = payload();
        payload.findings = vec![ReviewFindingPayload {
            severity: "P1".to_string(),
            title: "证据链必须完整展示".to_string(),
            description: "第一段原因包含中文细节，第二段继续解释为何历史卡片不能只展示摘要。\n这句位于换行之后，也必须在主时间线中完整可见。".to_string(),
            evidence_refs: vec![
                "/Users/example/project/very/long/path/to/report.md:77-79".to_string(),
                "receipt:claim-support:verifiable-evidence-42".to_string(),
            ],
            display_evidence_refs: vec![
                "report.md:77-79".to_string(),
                "receipt:claim-support:verifiable-evidence-42".to_string(),
            ],
            remediation: "先恢复逐条证据映射，再执行复审；这段修复建议不能被省略或替换成省略号。"
                .to_string(),
        }];
        payload.overall_assessment =
            "总体结论包含完整风险边界，并明确说明下一阶段不得把残余发现误标为通过。".to_string();

        for width in [40, 80] {
            let rendered = plain(full_card(&payload, width, &ThemePalette::default()));
            let compact = rendered
                .chars()
                .filter(|character| {
                    !character.is_whitespace()
                        && !matches!(character, '│' | '╭' | '╮' | '╰' | '╯' | '─' | '╌')
                })
                .collect::<String>();
            for expected in [
                "report.md:77-79",
                "receipt:claim-support:verifiable-evidence-42",
                "第一段原因包含中文细节",
                "这句位于换行之后，也必须在主时间线中完整可见。",
                "这段修复建议不能被省略或替换成省略号。",
                "总体结论包含完整风险边界",
            ] {
                let expected = expected
                    .chars()
                    .filter(|character| !character.is_whitespace())
                    .collect::<String>();
                assert!(compact.contains(&expected), "width={width}\n{rendered}");
            }
            assert!(
                !rendered.contains("Ctrl+T for complete review text"),
                "width={width}\n{rendered}"
            );
        }
    }

    #[test]
    fn lifecycle_states_have_distinct_visible_labels() {
        for (phase, outcome, expected) in [
            ("reviewing", "pending", "REVIEWING"),
            ("feedback_received", "findings", "FINDINGS"),
            ("applying_feedback", "findings", "CORRECTING"),
            ("residual", "residual_findings", "RESIDUAL"),
            ("failed", "failed", "FAILED"),
        ] {
            let mut payload = payload();
            payload.phase = phase.to_string();
            payload.outcome = outcome.to_string();
            let rendered = plain(render_review_cycle_card(
                &payload,
                80,
                &ThemePalette::default(),
            ));
            assert!(rendered.contains(expected), "{rendered}");
        }
    }

    #[test]
    fn failed_review_renders_readable_cause_and_request_local_retry_totals() {
        let mut payload = payload();
        payload.phase = "failed".to_string();
        payload.outcome = "failed".to_string();
        payload.findings.clear();
        payload.feedback_format.clear();
        payload.terminal_source = "bridge_synthesized".to_string();
        payload.failure_cause = "Provider unavailable".to_string();
        payload.failure_detail = "Server overloaded".to_string();
        payload.provider_request_count = 4;
        payload.provider_retry_count = 8;
        payload.final_request_attempt_count = 1;
        payload.final_request_retry_count = 0;

        for width in [20, 40, 80, 120] {
            let rendered = plain(render_review_cycle_card(
                &payload,
                width,
                &ThemePalette::default(),
            ));
            let compact = rendered
                .chars()
                .filter(|character| {
                    !character.is_whitespace()
                        && !matches!(character, '│' | '╭' | '╮' | '╰' | '╯' | '─' | '╌')
                })
                .collect::<String>();
            assert!(compact.contains("FAILED"), "width={width}\n{rendered}");
            assert!(
                compact.contains("Providerunavailable"),
                "width={width}\n{rendered}"
            );
            assert!(
                compact.contains("Serveroverloaded"),
                "width={width}\n{rendered}"
            );
            assert!(compact.contains("4requests"), "width={width}\n{rendered}");
            assert!(compact.contains("8retries"), "width={width}\n{rendered}");
            assert!(compact.contains("1attempt"), "width={width}\n{rendered}");
            assert!(!rendered.contains("Review in progress"), "{rendered}");
            assert!(
                !rendered.contains("Recorded target unavailable"),
                "{rendered}"
            );
            assert!(
                !rendered.contains("Artifact binding not verified"),
                "{rendered}"
            );
        }
    }

    #[test]
    fn submitted_markdown_is_indented_without_inventing_a_verdict() {
        let value = ReviewCyclePayload {
            review_kind: "analysis".into(),
            stage: "analysis_decision".into(),
            phase: "completed".into(),
            outcome: "submitted".into(),
            attempt: 1,
            round: 1,
            summary: "No findings.".into(),
            ..Default::default()
        };
        for width in [20, 39, 40, 80, 116, 120, 220] {
            let lines = render_review_cycle_card(&value, width, &ThemePalette::default());
            let text = plain(lines.clone());
            assert!(
                lines.iter().all(|line| line.width() <= width),
                "{width}: {text}"
            );
            assert!(
                text.contains("SUBMITTED") && !text.contains("✓") && !text.contains("ACCEPTED"),
                "{text}"
            );
            if width >= 40 {
                let body = lines
                    .iter()
                    .find(|line| line.to_string().contains("No findings."))
                    .unwrap()
                    .to_string();
                assert!(
                    body.starts_with("│      No findings.") && body.ends_with("  │"),
                    "{width}: {body}"
                );
            } else {
                assert!(
                    text.lines().any(|line| line == "    No findings."),
                    "{text}"
                );
            }
            let embedded = render_embedded_review(&value, width, &ThemePalette::default());
            assert!(embedded
                .iter()
                .any(|line| line.to_string() == "    No findings."));
            assert!(embedded.iter().all(|line| line.width() <= width));
        }
        assert_eq!(value.outcome, "submitted");
        assert_eq!(value.summary, "No findings.");
    }

    #[test]
    fn markdown_summary_wraps_inside_both_insets_and_preserves_paragraphs() {
        let body = "核对跨被试划分与完整证据，保留每一条评审说明。".repeat(6);
        let value = ReviewCyclePayload {
            phase: "completed".into(),
            outcome: "submitted".into(),
            summary: format!("{body}\n\n保留第二段。"),
            ..Default::default()
        };
        for width in [40, 80, 116, 120, 220] {
            let lines = render_review_cycle_card(&value, width, &ThemePalette::default());
            let rows = lines.iter().map(ToString::to_string).collect::<Vec<_>>();
            let start = rows.iter().position(|row| row.contains('╌')).unwrap() + 1;
            let content = &rows[start..rows.len() - 1];
            assert!(
                content
                    .iter()
                    .all(|row| row.starts_with("│  ") && row.ends_with("  │")),
                "{width}: {rows:?}"
            );
            assert!(
                lines.iter().all(|line| line.width() == width),
                "{width}: {rows:?}"
            );
            let actual: String = content
                .iter()
                .flat_map(|row| row.chars())
                .filter(|c| !c.is_whitespace() && *c != '│')
                .collect();
            let expected: String = value
                .summary
                .chars()
                .filter(|c| !c.is_whitespace())
                .collect();
            assert_eq!(actual, expected);
            assert!(
                content
                    .iter()
                    .any(|row| row.trim_matches(['│', ' ']).is_empty()),
                "Paragraph break lost"
            );
        }
    }

    #[test]
    fn framed_markdown_insets_preserve_links_lists_and_annotation_sections() {
        let destination = "https://example.org/evidence/original?split=held-out&subject=2";
        let value = ReviewCyclePayload {
            phase: "completed".into(), outcome: "submitted".into(),
            summary: format!("[P2] Evidence boundary\n核对[原文证据]({destination})。\n\n- 保留第一项\n  - 保留嵌套项\n\n总体判断：尚需核对。"),
            ..Default::default()
        };
        for width in [40, 80, 116, 120] {
            let lines = full_card(&value, width, &ThemePalette::default());
            let text = plain(lines.clone());
            assert!(
                text.contains("Evidence boundary") && text.contains("OVERALL"),
                "{text}"
            );
            assert!(
                lines.iter().all(|row| row.line.width() == width),
                "{width}: {text}"
            );
            let links: Vec<_> = lines.iter().flat_map(|row| &row.hyperlinks).collect();
            assert!(links.iter().any(|link| link.destination == destination));
            assert!(
                links
                    .iter()
                    .all(|link| link.columns.start >= 3 && link.columns.end <= width - 3),
                "{links:?}"
            );
            let start = lines
                .iter()
                .position(|row| row.line.to_string().contains('╌'))
                .unwrap()
                + 1;
            for row in &lines[start..lines.len() - 1] {
                let text = row.line.to_string();
                assert!(text.starts_with("│  ") && text.ends_with("  │"), "{text}");
            }
        }
    }

    #[test]
    fn review_separator_matches_frame_width_in_ansi256_light_and_dark_themes() {
        for light in [true, false] {
            let theme = ThemePalette::from_terminal_colors_for_level(
                Some(if light {
                    Color::Rgb(17, 17, 17)
                } else {
                    Color::Rgb(238, 238, 238)
                }),
                Some(if light {
                    Color::Rgb(255, 255, 255)
                } else {
                    Color::Rgb(17, 17, 17)
                }),
                crate::theme::TerminalColorLevel::Ansi256,
            );
            for width in [40, 80, 116, 120, 220] {
                let row = inset_separator(width, ReviewStyles::new(&theme));
                let text = row.line.to_string();
                assert_eq!(row.line.width(), width, "{text}");
                assert!(text.starts_with("│  ") && text.ends_with("  │"));
                assert_eq!(text.matches('╌').count(), width - 6);
                assert!(row
                    .line
                    .spans
                    .iter()
                    .all(|span| span.style.bg == theme.elevated_bg));
            }
        }
    }
}
