//! Display-only fallback for provider citation IDs without a URL binding.
//! The owning history cell retains the original text for copy/raw inspection.
use std::borrow::Cow;

pub(super) fn readable_citations(text: &str, linked: bool) -> Cow<'_, str> {
    const START: &str = "\u{e200}cite\u{e202}";
    const END: char = '\u{e201}';
    if !text.contains('\u{e200}') {
        return Cow::Borrowed(text);
    }
    let label = if linked {
        "[来源]"
    } else {
        "[引用未关联]"
    };
    let mut result = String::new();
    let mut rest = text;
    while let Some(index) = rest.find('\u{e200}') {
        result.push_str(&rest[..index]);
        let marker = &rest[index..];
        if START.starts_with(marker) {
            // A streamed prefix has no displayable source identity yet.
            result.push_str(label);
            rest = "";
            break;
        }
        if !marker.starts_with(START) {
            result.push('\u{e200}');
            rest = &marker['\u{e200}'.len_utf8()..];
            continue;
        }
        let tail = &marker[START.len()..];
        // Streaming can end halfway through a citation. Never discard ordinary
        // prose following an incomplete marker's whitespace boundary.
        let stop = tail
            .find(|ch: char| ch == END || ch.is_whitespace())
            .unwrap_or(tail.len());
        result.push_str(label);
        rest = &tail[stop..];
        if rest.starts_with(END) {
            rest = &rest[END.len_utf8()..];
        }
    }
    result.push_str(rest);
    Cow::Owned(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn groups_keep_surrounding_text_and_do_not_invent_links() {
        let text = "结论citeturn1view0turn2search1。后续citeturn3view2结尾";
        assert_eq!(
            readable_citations(text, false),
            "结论[引用未关联]。后续[引用未关联]结尾"
        );
    }

    #[test]
    fn partial_stream_marker_keeps_text_after_a_whitespace_boundary() {
        assert_eq!(
            readable_citations("结论citeturn123vi", false),
            "结论[引用未关联]"
        );
        assert_eq!(
            readable_citations("结论citeturn123vi 后续正文", false),
            "结论[引用未关联] 后续正文"
        );
        assert_eq!(readable_citations("结论ci", false), "结论[引用未关联]");
        assert_eq!(
            readable_citations("ordinary turn123view0 text", false),
            "ordinary turn123view0 text"
        );
    }
}
