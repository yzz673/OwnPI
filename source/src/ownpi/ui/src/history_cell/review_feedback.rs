//! Layout of explicitly annotated feedback Markdown. These sections are never
//! persisted, used as gate decisions, or assigned a quality outcome.

#[derive(Debug, PartialEq)]
pub(super) enum FeedbackSection {
    Text(String),
    Annotation {
        severity: String,
        title: String,
        references: Vec<String>,
        body: String,
    },
    Overall(String),
}

fn annotation(line: &str) -> Option<(String, String, Vec<String>)> {
    let mut line = line.trim();
    if let Some(value) = line.strip_prefix("- ") {
        line = value;
    }
    if line.starts_with('#') {
        line = line.trim_start_matches('#').trim_start();
    }
    if line.starts_with("**") && line.ends_with("**") {
        line = &line[2..line.len() - 2];
    }
    let severity = ["P0", "P1", "P2", "P3"]
        .into_iter()
        .find(|value| line.starts_with(&format!("[{value}] ")))?;
    let (title, references) = split_title_references(line[5..].trim());
    Some((severity.into(), title, references))
}

pub(super) fn split_title_references(heading: &str) -> (String, Vec<String>) {
    // A suffix must consist entirely of explicit quoted references or bare
    // file:line locators. Ordinary prose and hyphens stay in the title.
    for (at, separator) in heading.match_indices(['-', '—', '–']) {
        // Require whitespace on both sides; never split filenames or prose
        // unless the entire suffix is an explicit reference list.
        if !heading[..at].ends_with(char::is_whitespace)
            || !heading[at + separator.len()..].starts_with(char::is_whitespace)
        {
            continue;
        }
        let suffix = &heading[at + separator.len()..];
        let refs: Option<Vec<_>> = suffix.split([';', '；']).map(reference).collect();
        if let Some(refs) = refs.filter(|refs| !refs.is_empty()) {
            return (heading[..at].trim().into(), refs);
        }
    }
    (heading.trim().into(), Vec::new())
}

fn reference(value: &str) -> Option<String> {
    let value = value.trim();
    // Explicit document sections may include quoted passages. Keep the whole
    // locator in details; do not interpret it as a filesystem path or citation.
    if value
        .strip_prefix('§')
        .is_some_and(|rest| rest.starts_with(|c: char| c.is_ascii_digit()))
    {
        return Some(value.into());
    }
    if value.len() > 2
        && value.starts_with('`')
        && value.ends_with('`')
        && !value[1..value.len() - 1].contains('`')
    {
        return Some(value[1..value.len() - 1].into());
    }
    let (path, location) = value.rsplit_once(':')?;
    if path.is_empty()
        || path.contains('`')
        || path.contains("://")
        || path.contains(" - ")
        || !(path.contains('/') || path.contains('\\') || path.contains('.'))
    {
        return None;
    }
    // Recognize numeric line lists/ranges, without treating prose such as
    // "time:12" or "notes.md:missing" as a source location.
    let valid = location.split(',').all(|part| {
        let range: Vec<_> = part.trim().split('-').collect();
        (1..=2).contains(&range.len())
            && range.iter().all(|n| {
                !n.is_empty()
                    && n.chars().all(|c| c.is_ascii_digit())
                    && n.parse::<u64>().is_ok_and(|line| line > 0)
            })
    });
    valid.then(|| value.into())
}

fn overall(line: &str) -> Option<&str> {
    let line = line.trim().trim_start_matches('#').trim_start();
    for label in [
        "总体判断：",
        "总体判断:",
        "总体评价：",
        "总体评价:",
        "总体评估：",
        "总体评估:",
        "Overall assessment:",
        "Overall Assessment:",
    ] {
        if let Some(value) = line.strip_prefix(label) {
            return Some(value.trim());
        }
    }
    if matches!(
        line,
        "OVERALL" | "Overall assessment" | "Overall Assessment"
    ) {
        return Some("");
    }
    None
}

pub(super) fn feedback_sections(text: &str) -> Vec<FeedbackSection> {
    let mut result = Vec::new();
    let mut current = FeedbackSection::Text(String::new());
    let mut fence: Option<&str> = None;
    for line in text.lines() {
        let trimmed = line.trim_start();
        let marker = if trimmed.starts_with("```") {
            Some("```")
        } else if trimmed.starts_with("~~~") {
            Some("~~~")
        } else {
            None
        };
        let protected = fence.is_some() || marker.is_some();
        if let Some(marker) = marker {
            if fence == Some(marker) {
                fence = None;
            } else if fence.is_none() {
                fence = Some(marker);
            }
        }
        if !protected {
            let next = if let Some((severity, title, references)) = annotation(line) {
                Some(FeedbackSection::Annotation {
                    severity,
                    title,
                    references,
                    body: String::new(),
                })
            } else {
                overall(line).map(|body| FeedbackSection::Overall(body.into()))
            };
            if let Some(next) = next {
                result.push(current);
                current = next;
                continue;
            }
        }
        let body = match &mut current {
            FeedbackSection::Text(body)
            | FeedbackSection::Overall(body)
            | FeedbackSection::Annotation { body, .. } => body,
        };
        if !body.is_empty() {
            body.push('\n');
        }
        body.push_str(line);
    }
    result.push(current);
    result
        .into_iter()
        .filter(|section| !matches!(section,FeedbackSection::Text(body) if body.trim().is_empty()))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reference_suffix_accepts_unicode_dashes_but_not_ordinary_prose() {
        for separator in [" - ", " — ", " – ", "\t—\t"] {
            let text = format!("[P2] Cross-subject EEG{separator}`map.md:30-35, 63-87`; `sources.md:3-27`\nExplanation");
            let sections = feedback_sections(&text);
            assert!(
                matches!(&sections[0], FeedbackSection::Annotation { title, references, .. }
                if title == "Cross-subject EEG" && references.len() == 2),
                "{sections:?}"
            );
        }
        let sections =
            feedback_sections("[P2] Cross-subject EEG — an adjacent evidence line\nBody");
        assert!(
            matches!(&sections[0], FeedbackSection::Annotation { title, references, .. }
            if title.ends_with("evidence line") && references.is_empty())
        );
    }

    #[test]
    fn actual_chinese_feedback_separates_bare_locators_and_overall() {
        let sections = feedback_sections(include_str!(
            "../../tests/fixtures/method_review_feedback.md"
        ));
        assert_eq!(sections.len(), 3, "{sections:?}");
        assert!(
            matches!(&sections[0], FeedbackSection::Annotation { title, references, body, .. }
            if !title.contains(".md:") && references.len() == 3 && !body.contains("总体评估："))
        );
        assert!(
            matches!(&sections[1], FeedbackSection::Annotation { references, body, .. }
            if references == &["method_design/plan.md:58-63"] && !body.contains("总体评估："))
        );
        assert!(
            matches!(&sections[2], FeedbackSection::Overall(body) if body.starts_with("方法设计"))
        );
    }

    #[test]
    fn locators_support_mixed_quotes_ranges_and_hyphens_in_titles() {
        let sections =
            feedback_sections("[P2] A - B - notes/证据.md:2,8；`src/main.rs:12-15`\nBody.");
        assert!(
            matches!(&sections[0], FeedbackSection::Annotation { title, references, .. }
            if title == "A - B" && references == &["notes/证据.md:2,8", "src/main.rs:12-15"])
        );
        for suffix in [
            "ordinary prose",
            "time:12",
            "notes.md:missing",
            "notes.md:2；unverified prose",
        ] {
            let heading = format!("[P3] Keep title - {suffix}");
            let sections = feedback_sections(&heading);
            assert!(
                matches!(&sections[0], FeedbackSection::Annotation { title, references, .. }
                if title == &heading[5..] && references.is_empty()),
                "{sections:?}"
            );
        }
    }

    #[test]
    fn overall_variants_remain_separate_without_interpreting_outcomes() {
        for label in [
            "总体评估：",
            "总体评估:",
            "总体评价:",
            "Overall assessment:",
        ] {
            let sections = feedback_sections(&format!(
                "[P2] One\nOriginal finding.\n\n{label} Original assessment."
            ));
            assert!(
                matches!(&sections[1], FeedbackSection::Overall(body) if body == "Original assessment.")
            );
        }
    }
    #[test]
    fn code_examples_and_unmarked_prose_are_not_findings() {
        let source="Keep the original.\n```text\n[P2] An example, not a review heading\n总体判断：inside code\n```\n\nOrdinary overall discussion.";
        assert_eq!(
            feedback_sections(source),
            vec![FeedbackSection::Text(source.into())]
        );
    }
    #[test]
    fn annotations_preserve_order_and_only_explicit_reference_syntax() {
        let sections=feedback_sections("[P3] A - ordinary prose\nText.\n\n[P2] B - `one.md:2`; `two.md:3`\nBody.\n\n总体判断：Original assessment.");
        assert!(
            matches!(&sections[0],FeedbackSection::Annotation{severity,title,references,..} if severity=="P3" && title=="A - ordinary prose" && references.is_empty())
        );
        assert!(
            matches!(&sections[1],FeedbackSection::Annotation{references,..} if references==&["one.md:2","two.md:3"])
        );
        assert_eq!(
            sections[2],
            FeedbackSection::Overall("Original assessment.".into())
        );
    }
}
