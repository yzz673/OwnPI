//! URL-aware Ratatui wrapping adapted from OpenAI Codex TUI at
//! ea1e60181b9aeb4cda3d05491df4d2877ff57fd5.

use std::borrow::Cow;
use std::net::Ipv6Addr;
use std::ops::Range;

use ratatui::text::{Line, Span};
use textwrap::Options;

#[allow(dead_code)]
pub(crate) fn wrap_ranges<'a, O>(text: &str, width_or_options: O) -> Vec<Range<usize>>
where
    O: Into<Options<'a>>,
{
    let opts = width_or_options.into();
    let mut lines = Vec::new();
    let mut cursor = 0usize;
    for (line_index, line) in textwrap::wrap(text, &opts).iter().enumerate() {
        match line {
            Cow::Borrowed(slice) => {
                // SAFETY: textwrap borrowed slices point into `text`.
                let start = unsafe { slice.as_ptr().offset_from(text.as_ptr()) as usize };
                let end = start + slice.len();
                let trailing_spaces = text[end..].chars().take_while(|ch| *ch == ' ').count();
                lines.push(start..end + trailing_spaces + 1);
                cursor = end + trailing_spaces;
            }
            Cow::Owned(slice) => {
                let indent = if line_index == 0 {
                    opts.initial_indent
                } else {
                    opts.subsequent_indent
                };
                let mapped = map_owned_wrapped_line_to_range(text, cursor, slice, indent);
                let trailing_spaces = text[mapped.end..]
                    .chars()
                    .take_while(|ch| *ch == ' ')
                    .count();
                lines.push(mapped.start..mapped.end + trailing_spaces + 1);
                cursor = mapped.end + trailing_spaces;
            }
        }
    }
    lines
}

pub(crate) fn wrap_ranges_trim<'a, O>(text: &str, width_or_options: O) -> Vec<Range<usize>>
where
    O: Into<Options<'a>>,
{
    let opts = width_or_options.into();
    let mut lines = Vec::new();
    let mut cursor = 0usize;
    for (line_index, line) in textwrap::wrap(text, &opts).iter().enumerate() {
        match line {
            Cow::Borrowed(slice) => {
                // SAFETY: textwrap borrowed slices point into `text`.
                let start = unsafe { slice.as_ptr().offset_from(text.as_ptr()) as usize };
                let end = start + slice.len();
                lines.push(start..end);
                cursor = end;
            }
            Cow::Owned(slice) => {
                let indent = if line_index == 0 {
                    opts.initial_indent
                } else {
                    opts.subsequent_indent
                };
                let mapped = map_owned_wrapped_line_to_range(text, cursor, slice, indent);
                lines.push(mapped.clone());
                cursor = mapped.end;
            }
        }
    }
    lines
}

fn map_owned_wrapped_line_to_range(
    text: &str,
    cursor: usize,
    wrapped: &str,
    synthetic_prefix: &str,
) -> Range<usize> {
    let wrapped = if synthetic_prefix.is_empty() {
        wrapped
    } else {
        wrapped.strip_prefix(synthetic_prefix).unwrap_or(wrapped)
    };
    let mut start = cursor;
    while start < text.len() && !wrapped.starts_with(' ') {
        let Some(ch) = text[start..].chars().next() else {
            break;
        };
        if ch != ' ' {
            break;
        }
        start += ch.len_utf8();
    }

    let mut end = start;
    let mut saw_source_char = false;
    let mut chars = wrapped.chars().peekable();
    while let Some(ch) = chars.next() {
        if end < text.len() {
            let Some(source_ch) = text[end..].chars().next() else {
                break;
            };
            if ch == source_ch {
                end += source_ch.len_utf8();
                saw_source_char = true;
                continue;
            }
        }
        if ch == '-' && chars.peek().is_none() {
            continue;
        }
        if saw_source_char {
            break;
        }
    }
    start..end
}

pub(crate) fn line_contains_url_like(line: &Line<'_>) -> bool {
    let text = line
        .spans
        .iter()
        .map(|span| span.content.as_ref())
        .collect::<String>();
    text_contains_url_like(&text)
}

pub(crate) fn line_has_mixed_url_and_non_url_tokens(line: &Line<'_>) -> bool {
    let text = line
        .spans
        .iter()
        .map(|span| span.content.as_ref())
        .collect::<String>();
    text_has_mixed_url_and_non_url_tokens(&text)
}

pub(crate) fn text_contains_url_like(text: &str) -> bool {
    text.split_ascii_whitespace().any(is_url_like_token)
}

fn text_has_mixed_url_and_non_url_tokens(text: &str) -> bool {
    let mut saw_url = false;
    let mut saw_non_url = false;
    for raw in text.split_ascii_whitespace() {
        if is_url_like_token(raw) {
            saw_url = true;
        } else if is_substantive_non_url_token(raw) {
            saw_non_url = true;
        }
        if saw_url && saw_non_url {
            return true;
        }
    }
    false
}

fn is_url_like_token(raw: &str) -> bool {
    let token = trim_url_token(raw);
    !token.is_empty() && (is_absolute_url_like(token) || is_bare_url_like(token))
}

fn is_substantive_non_url_token(raw: &str) -> bool {
    let token = trim_url_token(raw);
    !token.is_empty()
        && !is_decorative_marker_token(raw, token)
        && token.chars().any(char::is_alphanumeric)
}

fn is_decorative_marker_token(raw: &str, token: &str) -> bool {
    let raw = raw.trim();
    crate::style::GLYPH_WRAP_LEAD_CHARS.contains(&raw)
        || (token.chars().all(|ch| ch.is_ascii_digit())
            && (raw.ends_with('.') || raw.ends_with(')')))
}

fn trim_url_token(token: &str) -> &str {
    token.trim_matches(|ch: char| {
        matches!(
            ch,
            '(' | ')'
                | '['
                | ']'
                | '{'
                | '}'
                | '<'
                | '>'
                | ','
                | '.'
                | ';'
                | ':'
                | '!'
                | '\''
                | '"'
        )
    })
}

fn is_absolute_url_like(token: &str) -> bool {
    let Some((scheme, rest)) = token.split_once("://") else {
        return false;
    };
    if !has_valid_scheme(scheme) || rest.is_empty() {
        return false;
    }
    if matches!(
        scheme.to_ascii_lowercase().as_str(),
        "http" | "https" | "ftp" | "ftps" | "ws" | "wss"
    ) {
        let authority = rest.split(['/', '?', '#']).next().unwrap_or_default();
        return is_valid_network_authority(authority);
    }
    true
}

fn is_valid_network_authority(authority: &str) -> bool {
    if authority.is_empty()
        || authority
            .chars()
            .any(|ch| ch.is_whitespace() || ch.is_control())
    {
        return false;
    }

    let host_port = if let Some((userinfo, host_port)) = authority.rsplit_once('@') {
        if userinfo.is_empty() || userinfo.contains('@') || host_port.is_empty() {
            return false;
        }
        host_port
    } else {
        authority
    };

    if let Some(ipv6) = host_port.strip_prefix('[') {
        let Some(close) = ipv6.find(']') else {
            return false;
        };
        let (address, trailer) = ipv6.split_at(close);
        if address.parse::<Ipv6Addr>().is_err() && !is_ipvfuture(address) {
            return false;
        }
        return trailer == "]" || trailer.strip_prefix("]:").is_some_and(is_valid_port);
    }

    let (host, port) = split_host_and_port(host_port);
    if host.is_empty() || port.is_some_and(|value| !is_valid_port(value)) {
        return false;
    }
    host.eq_ignore_ascii_case("localhost")
        || is_ipv4(host)
        || is_domain_name(host)
        || is_unicode_domain_name(host)
        || (!host.contains('.') && is_domain_label(host))
}

fn is_ipvfuture(host: &str) -> bool {
    let Some((version, address)) = host
        .strip_prefix(['v', 'V'])
        .and_then(|value| value.split_once('.'))
    else {
        return false;
    };
    !version.is_empty()
        && version.chars().all(|ch| ch.is_ascii_hexdigit())
        && !address.is_empty()
        && address.chars().all(|ch| {
            ch.is_ascii_alphanumeric()
                || matches!(
                    ch,
                    '-' | '.'
                        | '_'
                        | '~'
                        | '!'
                        | '$'
                        | '&'
                        | '\''
                        | '('
                        | ')'
                        | '*'
                        | '+'
                        | ','
                        | ';'
                        | '='
                        | ':'
                )
        })
}

fn has_valid_scheme(scheme: &str) -> bool {
    let mut chars = scheme.chars();
    let Some(first) = chars.next() else {
        return false;
    };
    first.is_ascii_alphabetic()
        && chars.all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '+' | '-' | '.'))
}

fn is_bare_url_like(token: &str) -> bool {
    let (host_port, has_trailer) = split_host_port_and_trailer(token);
    if host_port.is_empty() || (!has_trailer && !host_port.to_ascii_lowercase().starts_with("www."))
    {
        return false;
    }
    let (host, port) = split_host_and_port(host_port);
    if host.is_empty() || port.is_some_and(|value| !is_valid_port(value)) {
        return false;
    }
    host.eq_ignore_ascii_case("localhost") || is_ipv4(host) || is_domain_name(host)
}

fn split_host_port_and_trailer(token: &str) -> (&str, bool) {
    token
        .find(['/', '?', '#'])
        .map_or((token, false), |index| (&token[..index], true))
}

fn split_host_and_port(host_port: &str) -> (&str, Option<&str>) {
    if host_port.starts_with('[') {
        return (host_port, None);
    }
    if let Some((host, port)) = host_port.rsplit_once(':') {
        if !host.is_empty() && !port.is_empty() && port.chars().all(|ch| ch.is_ascii_digit()) {
            return (host, Some(port));
        }
    }
    (host_port, None)
}

fn is_valid_port(port: &str) -> bool {
    !port.is_empty()
        && port.len() <= 5
        && port.chars().all(|ch| ch.is_ascii_digit())
        && port.parse::<u16>().is_ok()
}

fn is_ipv4(host: &str) -> bool {
    let parts = host.split('.').collect::<Vec<_>>();
    parts.len() == 4
        && parts
            .iter()
            .all(|part| !part.is_empty() && part.parse::<u8>().is_ok())
}

fn is_domain_name(host: &str) -> bool {
    let host = host.to_ascii_lowercase();
    if !host.contains('.') {
        return false;
    }
    let mut labels = host.split('.');
    let Some(tld) = labels.next_back() else {
        return false;
    };
    (2..=63).contains(&tld.len())
        && tld.chars().all(|ch| ch.is_ascii_alphabetic())
        && labels.all(is_domain_label)
}

fn is_unicode_domain_name(host: &str) -> bool {
    if host.is_ascii() || !host.contains('.') {
        return false;
    }
    let labels = host.split('.').collect::<Vec<_>>();
    let Some(tld) = labels.last() else {
        return false;
    };
    tld.chars().count() >= 2
        && tld.chars().all(char::is_alphabetic)
        && labels.iter().all(|label| {
            !label.is_empty()
                && !label.starts_with('-')
                && !label.ends_with('-')
                && label.chars().all(|ch| ch.is_alphanumeric() || ch == '-')
        })
}

fn is_domain_label(label: &str) -> bool {
    if label.is_empty() || label.len() > 63 {
        return false;
    }
    let Some(first) = label.chars().next() else {
        return false;
    };
    let Some(last) = label.chars().next_back() else {
        return false;
    };
    first.is_ascii_alphanumeric()
        && last.is_ascii_alphanumeric()
        && label
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || ch == '-')
}

fn split_non_url_word(word: &str) -> Vec<usize> {
    if is_url_like_token(word) {
        Vec::new()
    } else {
        word.char_indices()
            .skip(1)
            .map(|(index, _)| index)
            .collect()
    }
}

pub(crate) fn url_preserving_wrap_options<'a>(opts: RtOptions<'a>) -> RtOptions<'a> {
    opts.word_separator(textwrap::WordSeparator::AsciiSpace)
        .word_splitter(textwrap::WordSplitter::Custom(split_non_url_word))
        .break_words(false)
}

pub(crate) fn adaptive_wrap_line<'a>(line: &'a Line<'a>, base: RtOptions<'a>) -> Vec<Line<'a>> {
    let selected = if line_contains_url_like(line) {
        url_preserving_wrap_options(base)
    } else {
        base
    };
    word_wrap_line(line, selected)
}

#[derive(Debug, Clone)]
pub(crate) struct RtOptions<'a> {
    pub(crate) width: usize,
    line_ending: textwrap::LineEnding,
    initial_indent: Line<'a>,
    pub(crate) subsequent_indent: Line<'a>,
    break_words: bool,
    wrap_algorithm: textwrap::WrapAlgorithm,
    word_separator: textwrap::WordSeparator,
    word_splitter: textwrap::WordSplitter,
}

impl From<usize> for RtOptions<'_> {
    fn from(width: usize) -> Self {
        Self::new(width)
    }
}

impl<'a> RtOptions<'a> {
    pub(crate) fn new(width: usize) -> Self {
        Self {
            width,
            line_ending: textwrap::LineEnding::LF,
            initial_indent: Line::default(),
            subsequent_indent: Line::default(),
            break_words: true,
            wrap_algorithm: textwrap::WrapAlgorithm::FirstFit,
            word_separator: textwrap::WordSeparator::new(),
            word_splitter: textwrap::WordSplitter::HyphenSplitter,
        }
    }

    pub(crate) fn initial_indent(self, initial_indent: Line<'a>) -> Self {
        Self {
            initial_indent,
            ..self
        }
    }

    pub(crate) fn subsequent_indent(self, subsequent_indent: Line<'a>) -> Self {
        Self {
            subsequent_indent,
            ..self
        }
    }

    pub(crate) fn break_words(self, break_words: bool) -> Self {
        Self {
            break_words,
            ..self
        }
    }

    pub(crate) fn wrap_algorithm(self, wrap_algorithm: textwrap::WrapAlgorithm) -> Self {
        Self {
            wrap_algorithm,
            ..self
        }
    }

    pub(crate) fn word_separator(self, word_separator: textwrap::WordSeparator) -> Self {
        Self {
            word_separator,
            ..self
        }
    }

    pub(crate) fn word_splitter(self, word_splitter: textwrap::WordSplitter) -> Self {
        Self {
            word_splitter,
            ..self
        }
    }
}

pub(crate) fn word_wrap_line<'a, O>(line: &'a Line<'a>, width_or_options: O) -> Vec<Line<'a>>
where
    O: Into<RtOptions<'a>>,
{
    let mut flat = String::new();
    let mut span_bounds = Vec::new();
    let mut offset = 0usize;
    for span in &line.spans {
        let start = offset;
        flat.push_str(span.content.as_ref());
        offset += span.content.len();
        span_bounds.push((start..offset, span.style));
    }

    let rt_opts = width_or_options.into();
    let opts = Options::new(rt_opts.width)
        .line_ending(rt_opts.line_ending)
        .break_words(rt_opts.break_words)
        .wrap_algorithm(rt_opts.wrap_algorithm)
        .word_separator(rt_opts.word_separator)
        .word_splitter(rt_opts.word_splitter);
    let initial_width = opts
        .width
        .saturating_sub(rt_opts.initial_indent.width())
        .max(1);
    let initial = wrap_ranges_trim(&flat, opts.clone().width(initial_width));
    let Some(first_range) = initial.first() else {
        return vec![rt_opts.initial_indent.style(line.style)];
    };

    let mut output = Vec::new();
    let mut first = rt_opts.initial_indent.clone().style(line.style);
    first.spans.extend(
        slice_line_spans(line, &span_bounds, first_range)
            .spans
            .into_iter()
            .map(|span| span.patch_style(line.style)),
    );
    output.push(first);

    let base = first_range.end;
    let skipped_spaces = flat[base..].chars().take_while(|ch| *ch == ' ').count();
    let base = base + skipped_spaces;
    let subsequent_width = opts
        .width
        .saturating_sub(rt_opts.subsequent_indent.width())
        .max(1);
    for range in wrap_ranges_trim(&flat[base..], opts.width(subsequent_width)) {
        if range.is_empty() {
            continue;
        }
        let mut wrapped = rt_opts.subsequent_indent.clone().style(line.style);
        let source_range = (range.start + base)..(range.end + base);
        wrapped.spans.extend(
            slice_line_spans(line, &span_bounds, &source_range)
                .spans
                .into_iter()
                .map(|span| span.patch_style(line.style)),
        );
        output.push(wrapped);
    }
    output
}

#[allow(dead_code)]
pub(crate) fn adaptive_wrap_lines<'a, I>(lines: I, options: RtOptions<'a>) -> Vec<Line<'static>>
where
    I: IntoIterator<Item = &'a Line<'a>>,
{
    let mut output = Vec::new();
    for (index, line) in lines.into_iter().enumerate() {
        let selected = if index == 0 {
            options.clone()
        } else {
            options
                .clone()
                .initial_indent(options.subsequent_indent.clone())
        };
        output.extend(
            adaptive_wrap_line(line, selected)
                .iter()
                .map(line_to_static),
        );
    }
    output
}

pub(crate) fn adaptive_wrap_text(text: &str, width: usize) -> Vec<String> {
    wrap_text_with(text, RtOptions::new(width.max(1)), true)
}

pub(crate) fn adaptive_wrap_text_with(text: &str, options: RtOptions<'_>) -> Vec<String> {
    wrap_text_with(text, options, true)
}

pub(crate) fn word_wrap_text(text: &str, options: RtOptions<'_>) -> Vec<String> {
    wrap_text_with(text, options, false)
}

fn wrap_text_with(text: &str, options: RtOptions<'_>, adaptive: bool) -> Vec<String> {
    let source_lines = if text.is_empty() {
        vec![""]
    } else {
        text.lines().collect::<Vec<_>>()
    };
    let mut output = Vec::new();
    for source in source_lines {
        let line = Line::from(source.to_string());
        let wrapped = if adaptive {
            adaptive_wrap_line(&line, options.clone())
        } else {
            word_wrap_line(&line, options.clone())
        };
        if wrapped.is_empty() {
            output.push(String::new());
        } else {
            output.extend(wrapped.iter().map(line_plain_text));
        }
    }
    output
}

pub(crate) fn line_plain_text(line: &Line<'_>) -> String {
    line.spans
        .iter()
        .map(|span| span.content.as_ref())
        .collect()
}

pub(crate) fn line_to_static(line: &Line<'_>) -> Line<'static> {
    Line {
        style: line.style,
        alignment: line.alignment,
        spans: line
            .spans
            .iter()
            .map(|span| Span {
                style: span.style,
                content: Cow::Owned(span.content.to_string()),
            })
            .collect(),
    }
}

fn slice_line_spans<'a>(
    original: &'a Line<'a>,
    span_bounds: &[(Range<usize>, ratatui::style::Style)],
    target: &Range<usize>,
) -> Line<'a> {
    let mut spans = Vec::new();
    for (index, (range, style)) in span_bounds.iter().enumerate() {
        if range.end <= target.start {
            continue;
        }
        if range.start >= target.end {
            break;
        }
        let start = target.start.max(range.start);
        let end = target.end.min(range.end);
        if end > start {
            let content = original.spans[index].content.as_ref();
            spans.push(Span {
                style: *style,
                content: Cow::Borrowed(&content[start - range.start..end - range.start]),
            });
        }
    }
    Line {
        style: original.style,
        alignment: original.alignment,
        spans,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ratatui::style::{Color, Stylize};
    use unicode_width::UnicodeWidthStr;

    fn text(line: &Line<'_>) -> String {
        line.spans
            .iter()
            .map(|span| span.content.as_ref())
            .collect()
    }

    #[test]
    fn codex_parity_wraps_words_and_preserves_styles() {
        let line = Line::from(vec!["hello ".red(), "world".into()]);
        let output = word_wrap_line(&line, 6);
        assert_eq!(
            output.iter().map(text).collect::<Vec<_>>(),
            ["hello", "world"]
        );
        assert_eq!(output[0].spans[0].style.fg, Some(Color::Red));
        assert_eq!(output[1].spans[0].style.fg, None);
    }

    #[test]
    fn codex_parity_applies_initial_and_subsequent_indents() {
        let line = Line::from("hello world foo");
        let output = word_wrap_line(
            &line,
            RtOptions::new(8)
                .initial_indent(Line::from("- "))
                .subsequent_indent(Line::from("  ")),
        );
        assert_eq!(
            output.iter().map(text).collect::<Vec<_>>(),
            ["- hello", "  world", "  foo"]
        );
    }

    #[test]
    fn codex_parity_uses_display_width_for_wide_unicode() {
        let line = Line::from("😀😀😀");
        let output = word_wrap_line(&line, 4);
        assert_eq!(output.iter().map(text).collect::<Vec<_>>(), ["😀😀", "😀"]);
    }

    #[test]
    fn codex_parity_keeps_long_url_intact() {
        let source = "example.test/a-very-long-path-with-many-segments-and-query?x=1&y=2";
        let line = Line::from(source);
        let output = adaptive_wrap_line(&line, RtOptions::new(20));
        assert_eq!(output.len(), 1);
        assert_eq!(text(&output[0]), source);
    }

    #[test]
    fn codex_parity_recognizes_url_forms_and_rejects_paths() {
        for value in [
            "https://example.com/a/b",
            "ftp://host/path",
            "example.test/path#frag",
            "localhost:3000/api",
            "127.0.0.1:8080/health",
            "(https://example.com/path)",
        ] {
            assert!(text_contains_url_like(value), "{value}");
        }
        for value in ["src/main.rs", "foo/bar", "localhost:99999/path"] {
            assert!(!text_contains_url_like(value), "{value}");
        }
    }

    #[test]
    fn mixed_url_line_wraps_non_url_text() {
        let line =
            Line::from("see https://ex.com a_very_long_token_without_spaces_to_force_wrapping");
        let output = adaptive_wrap_line(&line, RtOptions::new(24));
        assert!(output.len() > 2);
        assert!(output
            .iter()
            .map(text)
            .any(|line| line.contains("https://ex.com")));
    }

    #[test]
    fn cjk_after_ascii_identifier_uses_remaining_width() {
        let line = Line::from("在一个隔离的 direct_qa 阶段适合回答清晰直接的问题并继续执行");
        let output = word_wrap_line(&line, 30);
        assert_eq!(text(&output[0]), "在一个隔离的 direct_qa 阶段适");
    }

    #[test]
    fn wrap_ranges_stay_on_utf8_boundaries() {
        let source = "目录/非常长的文件名/继续";
        for range in wrap_ranges_trim(source, Options::new(6)) {
            assert!(source.is_char_boundary(range.start));
            assert!(source.is_char_boundary(range.end));
        }
    }

    #[test]
    fn ordinary_output_respects_extremely_narrow_widths() {
        for width in [1, 2, 3, 8] {
            let line = Line::from("abcdefghijk");
            let output = word_wrap_line(&line, width);
            assert_eq!(output.iter().map(text).collect::<String>(), "abcdefghijk");
            assert!(output.iter().all(|line| line.width() <= width));
        }

        for width in [2, 3, 8] {
            let line = Line::from("中😀abc");
            let output = word_wrap_line(&line, width);
            assert_eq!(output.iter().map(text).collect::<String>(), "中😀abc");
            assert!(output.iter().all(|line| line.width() <= width));
        }
    }

    #[test]
    fn combining_marks_stay_attached_and_zero_width() {
        let source = "e\u{301}e\u{301}e\u{301}";
        let line = Line::from(source);
        let output = word_wrap_line(&line, 1);

        assert_eq!(output.iter().map(text).collect::<String>(), source);
        assert!(output.iter().all(|line| line.width() <= 1));
        assert!(output.iter().all(|line| !text(line).starts_with('\u{301}')));
        assert_eq!(UnicodeWidthStr::width(source), 3);
    }

    #[test]
    fn styles_survive_splits_across_adjacent_spans() {
        let line = Line::from(vec!["ab".red(), "cd".blue()]);
        let output = word_wrap_line(&line, 3);

        assert_eq!(output.iter().map(text).collect::<Vec<_>>(), ["abc", "d"]);
        assert_eq!(output[0].spans.len(), 2);
        assert_eq!(output[0].spans[0].style.fg, Some(Color::Red));
        assert_eq!(output[0].spans[1].style.fg, Some(Color::Blue));
        assert_eq!(output[1].spans[0].style.fg, Some(Color::Blue));
    }

    #[test]
    fn invalid_network_authorities_are_not_treated_as_urls() {
        for value in [
            "https://:443/path",
            "https://@/path",
            "https://example.com:99999/path",
            "https://bad_host/path",
        ] {
            assert!(!text_contains_url_like(value), "{value}");
        }
    }

    #[test]
    fn url_classification_works_across_spans_and_ignores_markers() {
        let split = Line::from(vec![
            "see ".into(),
            "https://example.com/a/very/long/path".cyan(),
            " for details".into(),
        ]);
        assert!(line_contains_url_like(&split));
        assert!(line_has_mixed_url_and_non_url_tokens(&split));
        assert!(!line_has_mixed_url_and_non_url_tokens(&Line::from(
            "  │ https://example.com/path"
        )));
        assert!(!line_has_mixed_url_and_non_url_tokens(&Line::from(
            "1. https://example.com/path"
        )));
    }

    #[test]
    fn valid_network_and_custom_authorities_remain_urls() {
        for value in [
            "https://user:pass@example.com/path",
            "https://[::1]:8080/path",
            "https://[v1.fe80::]/path",
            "https://例子.测试/a-very-long-path",
            "myapp://open/some/path",
        ] {
            assert!(text_contains_url_like(value), "{value}");
        }
    }

    #[test]
    fn owned_range_mapping_returns_only_the_matching_source_prefix() {
        assert_eq!(
            map_owned_wrapped_line_to_range("hello world", 0, "helloX", ""),
            0..5
        );
    }

    #[test]
    fn owned_range_mapping_does_not_consume_matching_indent_bytes() {
        let source = "- item one and some more words";
        assert_eq!(
            map_owned_wrapped_line_to_range(source, 0, "- - item one", "- "),
            0..10
        );
    }

    #[test]
    fn wrap_ranges_reconstruct_source_with_non_space_indents() {
        let source = "The quick brown fox jumps over the lazy dog";
        let options = || {
            Options::new(12)
                .initial_indent("* ")
                .subsequent_indent("  ")
        };
        assert!(textwrap::wrap(source, options())
            .iter()
            .any(|line| matches!(line, Cow::Owned(_))));

        let mut rebuilt = String::new();
        let mut cursor = 0usize;
        for range in wrap_ranges(source, options()) {
            let start = range.start.max(cursor).min(source.len());
            let end = range.end.min(source.len());
            if start < end {
                rebuilt.push_str(&source[start..end]);
            }
            cursor = cursor.max(end);
        }
        assert_eq!(rebuilt, source);
    }

    #[test]
    fn wrap_ranges_reconstruct_source_when_indent_matches_source_prefix() {
        let source = "- first item is long enough to wrap around";
        let options = || {
            Options::new(16)
                .initial_indent("- ")
                .subsequent_indent("- ")
        };
        let mut rebuilt = String::new();
        let mut cursor = 0usize;
        for range in wrap_ranges(source, options()) {
            let start = range.start.max(cursor).min(source.len());
            let end = range.end.min(source.len());
            if start < end {
                rebuilt.push_str(&source[start..end]);
            }
            cursor = cursor.max(end);
        }
        assert_eq!(rebuilt, source);
    }
}
