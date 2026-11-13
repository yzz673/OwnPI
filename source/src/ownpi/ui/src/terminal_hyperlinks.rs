//! Semantic terminal hyperlinks carried separately from visible TUI text.
//!
//! OSC 8 bytes are applied only while writing to the terminal. Layout and wrapping therefore
//! continue to operate on ordinary ratatui lines and exact display-column ranges.

use std::ops::Range;

use ratatui::text::{Line, Span};
use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};
use url::Url;

use crate::wrapping::{adaptive_wrap_line, line_to_static, RtOptions};

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct TerminalHyperlink {
    pub(crate) columns: Range<usize>,
    pub(crate) destination: String,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub(crate) struct HyperlinkLine {
    pub(crate) line: Line<'static>,
    pub(crate) hyperlinks: Vec<TerminalHyperlink>,
}

impl HyperlinkLine {
    pub(crate) fn new(line: Line<'static>) -> Self {
        Self {
            line,
            hyperlinks: Vec::new(),
        }
    }

    pub(crate) fn plain(line: Line<'static>) -> Self {
        Self::new(line)
    }

    pub(crate) fn push_span(&mut self, span: Span<'static>, destination: Option<&str>) {
        let start = self.line.width();
        let end = start + span.content.width();
        self.line.spans.push(span);
        if let Some(destination) = destination {
            push_link_range(self, start..end, destination);
        }
    }

    pub(crate) fn append(&mut self, other: Self) {
        let shift = self.line.width();
        self.line.spans.extend(other.line.spans);
        for mut link in other.hyperlinks {
            link.columns = link.columns.start + shift..link.columns.end + shift;
            self.hyperlinks.push(link);
        }
    }

    /// Explicit Markdown destinations take precedence over URL-looking labels.
    pub(crate) fn with_web_urls(mut self) -> Self {
        for link in web_links_in_text(&line_text(&self.line)) {
            if !self.hyperlinks.iter().any(|existing| {
                existing.columns.start < link.columns.end
                    && link.columns.start < existing.columns.end
            }) {
                self.hyperlinks.push(link);
            }
        }
        self.hyperlinks.sort_by_key(|link| link.columns.start);
        self
    }
}

impl From<Line<'static>> for HyperlinkLine {
    fn from(line: Line<'static>) -> Self {
        Self::new(line)
    }
}

impl From<Vec<Span<'static>>> for HyperlinkLine {
    fn from(spans: Vec<Span<'static>>) -> Self {
        Self::new(Line::from(spans))
    }
}

pub(crate) fn word_wrap_hyperlink_line(
    source: &HyperlinkLine,
    options: RtOptions<'static>,
) -> Vec<HyperlinkLine> {
    remap_wrapped_line(
        source,
        crate::wrapping::word_wrap_line(&source.line, options)
            .iter()
            .map(line_to_static)
            .collect(),
    )
}

pub(crate) fn visible_lines(lines: Vec<HyperlinkLine>) -> Vec<Line<'static>> {
    lines.into_iter().map(|line| line.line).collect()
}

pub(crate) fn plain_hyperlink_lines(lines: Vec<Line<'static>>) -> Vec<HyperlinkLine> {
    lines.into_iter().map(HyperlinkLine::new).collect()
}

pub(crate) fn annotate_web_urls(lines: Vec<Line<'static>>) -> Vec<HyperlinkLine> {
    lines.into_iter().map(annotate_web_urls_in_line).collect()
}

fn annotate_web_urls_in_line(line: Line<'static>) -> HyperlinkLine {
    let text = line_text(&line);
    let mut out = HyperlinkLine::new(line);
    out.hyperlinks = web_links_in_text(&text);
    out
}

pub(crate) fn prefix_hyperlink_lines(
    lines: Vec<HyperlinkLine>,
    initial_prefix: Span<'static>,
    subsequent_prefix: Span<'static>,
) -> Vec<HyperlinkLine> {
    lines
        .into_iter()
        .enumerate()
        .map(|(index, mut line)| {
            let prefix = if index == 0 {
                initial_prefix.clone()
            } else {
                subsequent_prefix.clone()
            };
            let shift = prefix.content.width();
            let mut spans = Vec::with_capacity(line.line.spans.len() + 1);
            spans.push(prefix);
            spans.extend(line.line.spans);
            line.line = Line::from(spans).style(line.line.style);
            for hyperlink in &mut line.hyperlinks {
                hyperlink.columns = hyperlink.columns.start + shift..hyperlink.columns.end + shift;
            }
            line
        })
        .collect()
}

pub(crate) fn adaptive_wrap_hyperlink_lines(
    lines: &[HyperlinkLine],
    options: RtOptions<'static>,
) -> Vec<HyperlinkLine> {
    let mut out = Vec::new();
    for (index, line) in lines.iter().enumerate() {
        let options = if index == 0 {
            options.clone()
        } else {
            options
                .clone()
                .initial_indent(options.subsequent_indent.clone())
        };
        out.extend(remap_wrapped_line(
            line,
            adaptive_wrap_line(&line.line, options)
                .into_iter()
                .map(|wrapped| line_to_static(&wrapped))
                .collect(),
        ));
    }
    out
}

/// Reattach source hyperlink ranges after visible-text wrapping has split a line.
pub(crate) fn remap_wrapped_line(
    source: &HyperlinkLine,
    wrapped: Vec<Line<'static>>,
) -> Vec<HyperlinkLine> {
    let mut out = plain_hyperlink_lines(wrapped);
    let source_text = line_text(&source.line);
    let mut source_byte = 0usize;
    let mut source_column = 0usize;
    for (index, line) in out.iter_mut().enumerate() {
        if index > 0 {
            let trimmed = source_text[source_byte..].trim_start_matches(char::is_whitespace);
            let skipped = source_text[source_byte..].len() - trimmed.len();
            source_column += source_text[source_byte..source_byte + skipped].width();
            source_byte += skipped;
        }

        let rendered = line_text(&line.line);
        let remaining = &source_text[source_byte..];
        let Some(rendered_start) = longest_suffix_matching_prefix(&rendered, remaining) else {
            continue;
        };
        let mapped = &rendered[rendered_start..];
        let mut output_column = rendered[..rendered_start].width();
        for ch in mapped.chars() {
            let width = ch.width().unwrap_or(0);
            if let Some(link) = source
                .hyperlinks
                .iter()
                .find(|link| link.columns.contains(&source_column))
            {
                push_link_range(
                    line,
                    output_column..output_column + width,
                    &link.destination,
                );
            }
            source_column += width;
            output_column += width;
        }
        source_byte += mapped.len();
    }
    out
}

fn line_text(line: &Line<'_>) -> String {
    line.spans
        .iter()
        .map(|span| span.content.as_ref())
        .collect()
}

fn longest_suffix_matching_prefix(rendered: &str, source: &str) -> Option<usize> {
    rendered
        .char_indices()
        .map(|(index, _)| index)
        .chain(std::iter::once(rendered.len()))
        .find(|index| source.starts_with(&rendered[*index..]) && *index < rendered.len())
}

fn push_link_range(line: &mut HyperlinkLine, range: Range<usize>, destination: &str) {
    if range.is_empty() {
        return;
    }
    if let Some(previous) = line.hyperlinks.last_mut() {
        if previous.destination == destination && previous.columns.end == range.start {
            previous.columns.end = range.end;
            return;
        }
    }
    line.hyperlinks.push(TerminalHyperlink {
        columns: range,
        destination: destination.to_string(),
    });
}

pub(crate) fn web_links_in_text(text: &str) -> Vec<TerminalHyperlink> {
    let mut links = Vec::new();
    let mut search_from = 0usize;
    for raw_token in text.split_ascii_whitespace() {
        let Some(relative_start) = text[search_from..].find(raw_token) else {
            continue;
        };
        let raw_start = search_from + relative_start;
        search_from = raw_start + raw_token.len();
        let trimmed_start = raw_token
            .find(|ch: char| !is_leading_punctuation(ch))
            .unwrap_or(raw_token.len());
        let trimmed_end = trailing_url_end(&raw_token[trimmed_start..]) + trimmed_start;
        if trimmed_start >= trimmed_end {
            continue;
        }
        let candidate = &raw_token[trimmed_start..trimmed_end];
        let Some(destination) = web_destination(candidate) else {
            continue;
        };
        let start = text[..raw_start + trimmed_start].width();
        let end = start + candidate.width();
        links.push(TerminalHyperlink {
            columns: start..end,
            destination,
        });
    }
    links
}

fn is_leading_punctuation(ch: char) -> bool {
    matches!(
        ch,
        '(' | ')' | '[' | ']' | '{' | '}' | '<' | '>' | ',' | '.' | ';' | '!' | '\'' | '"'
    )
}

fn trailing_url_end(candidate: &str) -> usize {
    let mut end = candidate.len();
    while end > 0 {
        let remaining = &candidate[..end];
        let Some(ch) = remaining.chars().next_back() else {
            break;
        };
        let trim = matches!(ch, ',' | '.' | ';' | '!' | '\'' | '"')
            || matches!(ch, ')' | ']' | '}' | '>')
                && has_unmatched_closing_delimiter(remaining, ch);
        if !trim {
            break;
        }
        end -= ch.len_utf8();
    }
    end
}

fn has_unmatched_closing_delimiter(candidate: &str, closing: char) -> bool {
    let opening = match closing {
        ')' => '(',
        ']' => '[',
        '}' => '{',
        '>' => '<',
        _ => return false,
    };
    candidate.chars().filter(|ch| *ch == closing).count()
        > candidate.chars().filter(|ch| *ch == opening).count()
}

pub(crate) fn web_destination(destination: &str) -> Option<String> {
    let safe_destination = destination
        .chars()
        .filter(|ch| !ch.is_control())
        .collect::<String>();
    let authority = safe_destination
        .strip_prefix("https://")
        .or_else(|| safe_destination.strip_prefix("http://"))?;
    if authority.is_empty() || authority.starts_with('/') {
        return None;
    }
    let parsed = Url::parse(&safe_destination).ok()?;
    matches!(parsed.scheme(), "http" | "https")
        .then(|| parsed.host_str())
        .flatten()?;
    Some(safe_destination)
}

pub(crate) fn decorate_spans(line: &HyperlinkLine) -> Vec<Span<'static>> {
    if line.hyperlinks.is_empty() {
        return line.line.spans.clone();
    }

    let mut out = Vec::new();
    let mut column = 0usize;
    let mut link_index = 0usize;
    let mut active_link_index = None;
    let mut active_destination: Option<String> = None;
    for span in &line.line.spans {
        for ch in span.content.chars() {
            let width = ch.width().unwrap_or(0);
            while line
                .hyperlinks
                .get(link_index)
                .is_some_and(|link| link.columns.end <= column)
            {
                link_index += 1;
            }
            let selected_link_index = line
                .hyperlinks
                .get(link_index)
                .and_then(|link| link.columns.contains(&column).then_some(link_index));
            if active_link_index != selected_link_index {
                if active_destination.is_some() {
                    append_to_last_span(&mut out, "\x1b]8;;\x07");
                }
                active_destination = selected_link_index
                    .and_then(|index| terminal_destination(&line.hyperlinks[index].destination));
                if let Some(destination) = active_destination.as_ref() {
                    push_styled_content(
                        &mut out,
                        &format!("\x1b]8;;{destination}\x07"),
                        span.style,
                    );
                }
                active_link_index = selected_link_index;
            }
            push_styled_content(&mut out, &ch.to_string(), span.style);
            column += width;
        }
    }
    if active_destination.is_some() {
        append_to_last_span(&mut out, "\x1b]8;;\x07");
    }
    out
}

fn terminal_destination(destination: &str) -> Option<String> {
    if destination.chars().any(char::is_control) {
        return None;
    }
    let parsed = Url::parse(destination).ok()?;
    match parsed.scheme() {
        "http" | "https" => web_destination(destination),
        "file" if parsed.path().starts_with('/') => Some(parsed.to_string()),
        "mailto" if !parsed.path().is_empty() => Some(parsed.to_string()),
        _ => None,
    }
}

fn push_styled_content(out: &mut Vec<Span<'static>>, content: &str, style: ratatui::style::Style) {
    if let Some(last) = out.last_mut() {
        if last.style == style {
            last.content.to_mut().push_str(content);
            return;
        }
    }
    out.push(Span::styled(content.to_string(), style));
}

fn append_to_last_span(out: &mut [Span<'static>], content: &str) {
    if let Some(last) = out.last_mut() {
        last.content.to_mut().push_str(content);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn discovers_only_exact_web_url_ranges() {
        let links = web_links_in_text(
            "docs https://a.example and https://b.example/path, not mailto:a@example.com",
        );
        assert_eq!(links.len(), 2);
        assert_eq!(links[0].columns, 5..22);
        assert_eq!(links[0].destination, "https://a.example");
        assert_eq!(links[1].destination, "https://b.example/path");
    }

    #[test]
    fn destination_strips_controls_and_rejects_non_web_urls() {
        assert_eq!(
            web_destination("https://example.com/\u{7}safe").as_deref(),
            Some("https://example.com/safe")
        );
        assert_eq!(web_destination("mailto:a@example.com"), None);
        assert_eq!(web_destination("https:///missing-host"), None);
    }

    #[test]
    fn explicit_file_and_mail_targets_reach_osc8_without_allowing_control_sequences() {
        for target in [
            "file:///workspace/report.md#L12",
            "mailto:reader@example.org",
            "https://example.org/paper",
        ] {
            let mut line = HyperlinkLine::default();
            line.push_span(Span::raw("source"), Some(target));
            let bytes = decorate_spans(&line)
                .iter()
                .map(|span| span.content.as_ref())
                .collect::<String>();
            assert!(
                bytes.contains(&format!("\x1b]8;;{target}\x07")),
                "{bytes:?}"
            );
        }
        for target in [
            "javascript:alert(1)",
            "data:text/plain,hello",
            "file:///tmp/\u{1b}bad",
        ] {
            assert!(terminal_destination(target).is_none());
        }
    }

    #[test]
    fn wrapped_ranges_remain_attached_to_their_own_destination() {
        let source = annotate_web_urls(vec![Line::from(
            "docs https://a.example and https://b.example/path",
        )])
        .remove(0);
        let wrapped = remap_wrapped_line(
            &source,
            vec![
                Line::from("docs"),
                Line::from("https://a.example"),
                Line::from("and"),
                Line::from("https://b.example/path"),
            ],
        );
        assert_eq!(wrapped[1].hyperlinks[0].destination, "https://a.example");
        assert_eq!(
            wrapped[3].hyperlinks[0].destination,
            "https://b.example/path"
        );
    }
}
