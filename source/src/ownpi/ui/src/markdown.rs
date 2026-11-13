use pulldown_cmark::{CodeBlockKind, Event, HeadingLevel, Options, Parser, Tag, TagEnd};
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use unicode_width::UnicodeWidthStr;

use crate::terminal_hyperlinks::{
    prefix_hyperlink_lines, visible_lines, word_wrap_hyperlink_line, HyperlinkLine,
};
use crate::theme::ThemePalette;
use crate::wrapping::RtOptions;

mod citations;
mod code;
mod emphasis;
pub(crate) mod links;
mod table;
use table::MarkdownTable;
#[cfg(test)]
mod regression_tests;

#[derive(Debug)]
pub(crate) struct MarkdownRenderer<'a> {
    cwd: Option<String>,
    theme: &'a ThemePalette,
    width: usize,
    lines: Vec<HyperlinkLine>,
    current: HyperlinkLine,
    style_stack: Vec<Style>,
    list_stack: Vec<ListState>,
    containers: Vec<Container>,
    marker_container: Option<usize>,
    in_code_block: Option<String>,
    code_buffer: String,
    table: Option<MarkdownTable>,
    link_stack: Vec<LinkState>,
    images: Vec<ImageState>,
    pending_gap: bool,
    preserve_breaks: bool,
}

#[derive(Debug, Clone)]
struct ListState {
    next_number: Option<u64>,
}
#[derive(Debug, Clone)]
struct LinkState {
    suppress_label: bool,
    destination: Option<String>,
}
#[derive(Debug)]
struct ImageState {
    target: String,
    alt: String,
}
#[derive(Debug)]
enum Container {
    Quote,
    Item(usize),
    Footnote(usize),
}

pub(crate) fn render_markdown(
    source: &str,
    width: usize,
    theme: &ThemePalette,
) -> Vec<Line<'static>> {
    render_markdown_with_cwd(source, width, theme, None)
}

pub(crate) fn render_markdown_with_cwd(
    source: &str,
    width: usize,
    theme: &ThemePalette,
    cwd: Option<&str>,
) -> Vec<Line<'static>> {
    visible_lines(render_markdown_hyperlinks(source, width, theme, cwd))
}

pub(crate) fn render_markdown_hyperlinks(
    source: &str,
    width: usize,
    theme: &ThemePalette,
    cwd: Option<&str>,
) -> Vec<HyperlinkLine> {
    MarkdownRenderer::new(width, theme, cwd).render(source)
}

pub(crate) fn render_markdown_hyperlinks_preserving_breaks(
    source: &str,
    width: usize,
    theme: &ThemePalette,
    cwd: Option<&str>,
) -> Vec<HyperlinkLine> {
    let mut renderer = MarkdownRenderer::new(width, theme, cwd);
    renderer.preserve_breaks = true;
    renderer.render(source)
}

impl<'a> MarkdownRenderer<'a> {
    fn new(width: usize, theme: &'a ThemePalette, cwd: Option<&str>) -> Self {
        Self {
            cwd: cwd.map(str::to_string),
            theme,
            width: width.max(1),
            lines: Vec::new(),
            current: HyperlinkLine::default(),
            style_stack: vec![Style::default().fg(theme.text)],
            list_stack: Vec::new(),
            containers: Vec::new(),
            marker_container: None,
            in_code_block: None,
            code_buffer: String::new(),
            table: None,
            link_stack: Vec::new(),
            images: Vec::new(),
            pending_gap: false,
            preserve_breaks: false,
        }
    }

    fn render(mut self, source: &str) -> Vec<HyperlinkLine> {
        let mut citations = crate::citation_sources::CitationDocument::new(Default::default());
        let source = citations.body(source);
        let references = citations.references();
        let parser = Parser::new_ext(
            &source,
            Options::ENABLE_TABLES
                | Options::ENABLE_STRIKETHROUGH
                | Options::ENABLE_FOOTNOTES
                | Options::ENABLE_TASKLISTS,
        );
        for event in emphasis::cjk_strong_events(&source, parser) {
            match event {
                Event::Start(tag) => self.start_tag(tag),
                Event::End(tag) => self.end_tag(tag),
                Event::Text(text) => self.push_text(text.as_ref()),
                Event::Code(code) => self.push_span(
                    code.to_string(),
                    self.current_style()
                        .fg(crate::style::md_inline_code_color(self.theme)),
                ),
                Event::SoftBreak if !self.images.is_empty() => self.push_text(" "),
                Event::SoftBreak if self.preserve_breaks && self.table.is_none() => {
                    self.flush_current()
                }
                Event::SoftBreak => self.push_text(" "),
                Event::HardBreak if self.table.is_some() => self.push_text("\n"),
                Event::HardBreak => self.flush_current(),
                Event::Rule => {
                    self.flush_current();
                    self.ensure_gap();
                    let prefix = self.container_prefix(None);
                    let inner = self.width.saturating_sub(prefix.width()).max(1);
                    let line = HyperlinkLine::new(Line::from(Span::styled(
                        crate::style::GLYPH_RULE.repeat(inner.min(80)),
                        Style::default().fg(self.theme.border),
                    )));
                    self.lines.extend(prefix_hyperlink_lines(
                        vec![line],
                        self.prefix_span(prefix.clone()),
                        self.prefix_span(prefix),
                    ));
                }
                Event::Html(html) | Event::InlineHtml(html) => {
                    if self.table.is_some()
                        && matches!(
                            html.trim().to_ascii_lowercase().as_str(),
                            "<br>" | "<br/>" | "<br />"
                        )
                    {
                        self.push_text("\n");
                    } else {
                        self.push_text(html.as_ref());
                    }
                }
                Event::FootnoteReference(reference) => self.push_text(&format!("[^{reference}]")),
                Event::TaskListMarker(checked) => {
                    self.push_text(if checked { "[x] " } else { "[ ] " })
                }
            }
        }
        self.flush_current();
        while self
            .lines
            .last()
            .is_some_and(|line| line.line.spans.iter().all(|s| s.content.trim().is_empty()))
        {
            self.lines.pop();
        }
        if !references.is_empty() {
            self.lines.push(HyperlinkLine::default());
            // A broken/open code fence in the message must not swallow sources.
            self.lines.extend(
                MarkdownRenderer::new(self.width, self.theme, self.cwd.as_deref())
                    .render(&references),
            );
        }
        self.lines
    }

    fn start_tag(&mut self, tag: Tag<'_>) {
        match tag {
            Tag::Paragraph => {}
            Tag::Heading { level, .. } => {
                self.flush_current();
                self.style_stack.push(heading_style(level));
            }
            Tag::BlockQuote => {
                self.flush_current();
                self.containers.push(Container::Quote);
            }
            Tag::CodeBlock(kind) => {
                self.flush_current();
                self.ensure_gap();
                self.in_code_block = Some(match kind {
                    CodeBlockKind::Fenced(info) => {
                        info.split_whitespace().next().unwrap_or("").to_string()
                    }
                    CodeBlockKind::Indented => String::new(),
                });
                self.code_buffer.clear();
            }
            Tag::List(start) => {
                self.flush_current();
                self.list_stack.push(ListState { next_number: start });
            }
            Tag::Item => {
                self.flush_current();
                self.ensure_gap();
                let marker = if let Some(number) = self
                    .list_stack
                    .last_mut()
                    .and_then(|list| list.next_number.as_mut())
                {
                    let marker = format!("{number}. ");
                    *number += 1;
                    marker
                } else {
                    "• ".to_string()
                };
                let style =
                    Style::default().fg(if list_stack_item_is_ordered(self.list_stack.last()) {
                        crate::style::md_ordered_marker_color(self.theme)
                    } else {
                        self.theme.text
                    });
                self.marker_container = Some(self.containers.len());
                self.containers.push(Container::Item(marker.width()));
                self.raw_span(marker, style, None);
            }
            Tag::FootnoteDefinition(label) => {
                self.flush_current();
                self.pending_gap = true;
                self.ensure_gap();
                let marker = format!("[^{label}]: ");
                self.marker_container = Some(self.containers.len());
                self.containers.push(Container::Footnote(marker.width()));
                self.raw_span(marker, Style::default().fg(self.theme.text_muted), None);
            }
            Tag::Emphasis => self
                .style_stack
                .push(self.current_style().add_modifier(Modifier::ITALIC)),
            Tag::Strong => self
                .style_stack
                .push(self.current_style().add_modifier(Modifier::BOLD)),
            Tag::Strikethrough => self
                .style_stack
                .push(self.current_style().add_modifier(Modifier::CROSSED_OUT)),
            Tag::Link { dest_url, .. } => {
                let display = local_link_display(dest_url.as_ref(), self.cwd.as_deref());
                let destination = links::destination(dest_url.as_ref(), self.cwd.as_deref());
                self.style_stack.push(
                    self.current_style()
                        .fg(crate::style::md_link_color(self.theme))
                        .add_modifier(Modifier::UNDERLINED),
                );
                if let Some(label) = &display {
                    self.raw_span(label.clone(), self.current_style(), destination.as_deref());
                }
                self.link_stack.push(LinkState {
                    suppress_label: display.is_some(),
                    destination,
                });
            }
            Tag::Image { dest_url, .. } => self.images.push(ImageState {
                target: dest_url.to_string(),
                alt: String::new(),
            }),
            Tag::Table(alignments) => {
                self.flush_current();
                self.ensure_gap();
                self.table = Some(MarkdownTable::new(alignments));
            }
            Tag::TableHead | Tag::TableRow | Tag::TableCell => {}
            _ => {}
        }
    }

    fn end_tag(&mut self, tag: TagEnd) {
        match tag {
            TagEnd::Paragraph => {
                self.flush_current();
                self.pending_gap = true;
            }
            TagEnd::Heading(_) => {
                self.flush_current();
                self.style_stack.pop();
            }
            TagEnd::BlockQuote => {
                self.flush_current();
                self.containers.pop();
            }
            TagEnd::CodeBlock => self.flush_code_block(),
            TagEnd::List(_) => {
                self.flush_current();
                self.list_stack.pop();
            }
            TagEnd::Item | TagEnd::FootnoteDefinition => {
                self.flush_current();
                self.containers.pop();
            }
            TagEnd::Emphasis | TagEnd::Strong | TagEnd::Strikethrough => {
                self.style_stack.pop();
            }
            TagEnd::Link => {
                self.style_stack.pop();
                self.link_stack.pop();
            }
            TagEnd::Image => {
                if let Some(image) = self.images.pop() {
                    if let Some(parent) = self.images.last_mut() {
                        parent.alt.push_str(&image.alt);
                        return;
                    }
                    self.push_text("Image · ");
                    if !image.alt.trim().is_empty() {
                        self.push_text(image.alt.trim());
                        self.push_text(" · ");
                    }
                    let display = local_link_display(&image.target, self.cwd.as_deref())
                        .unwrap_or_else(|| image.target.clone());
                    let destination = links::destination(&image.target, self.cwd.as_deref());
                    self.raw_span(
                        display,
                        self.current_style()
                            .fg(crate::style::md_link_color(self.theme))
                            .add_modifier(Modifier::UNDERLINED),
                        destination.as_deref(),
                    );
                }
            }
            TagEnd::Table => self.flush_table(),
            TagEnd::TableCell => {
                if let Some(table) = self.table.as_mut() {
                    table.end_cell();
                }
            }
            TagEnd::TableHead | TagEnd::TableRow => {
                if let Some(table) = self.table.as_mut() {
                    table.end_row();
                }
            }
            _ => {}
        }
    }

    fn push_text(&mut self, text: &str) {
        if self.in_code_block.is_some() {
            self.code_buffer.push_str(text);
            return;
        }
        self.push_span(
            citations::readable_citations(
                text,
                self.link_stack
                    .last()
                    .is_some_and(|link| link.destination.is_some()),
            )
            .into_owned(),
            self.current_style(),
        );
    }

    fn push_span(&mut self, text: String, style: Style) {
        if let Some(image) = self.images.last_mut() {
            image.alt.push_str(&text);
            return;
        }
        if self
            .link_stack
            .last()
            .is_some_and(|link| link.suppress_label)
        {
            return;
        }
        let destination = self
            .link_stack
            .last()
            .and_then(|link| link.destination.clone());
        self.raw_span(text, style, destination.as_deref());
    }

    fn raw_span(&mut self, text: String, style: Style, destination: Option<&str>) {
        if text.is_empty() {
            return;
        }
        if let Some(table) = self.table.as_mut() {
            table.push(text, style, destination);
            return;
        }
        self.ensure_gap();
        self.current
            .push_span(Span::styled(text, style), destination);
    }

    fn ensure_gap(&mut self) {
        if self.pending_gap
            && self.current.line.spans.is_empty()
            && !self.lines.is_empty()
            && self
                .lines
                .last()
                .is_some_and(|line| !line.line.spans.iter().all(|s| s.content.trim().is_empty()))
        {
            self.lines.push(HyperlinkLine::default());
        }
        self.pending_gap = false;
    }

    fn container_prefix(&self, skip: Option<usize>) -> String {
        let mut prefix = String::new();
        for (index, container) in self.containers.iter().enumerate() {
            if skip == Some(index) {
                continue;
            }
            match container {
                Container::Quote => prefix.push_str(crate::style::GLYPH_MD_QUOTE_PREFIX),
                Container::Item(width) | Container::Footnote(width) => {
                    prefix.push_str(&" ".repeat(*width))
                }
            }
        }
        // Deep nesting still reserves space for readable content at narrow widths.
        prefix.truncate(self.width.saturating_sub(4));
        prefix
    }

    fn prefix_span(&self, prefix: String) -> Span<'static> {
        Span::styled(
            prefix,
            Style::default().fg(crate::style::md_quote_color(self.theme)),
        )
    }

    fn flush_current(&mut self) {
        if self.current.line.spans.is_empty() {
            return;
        }
        let line = std::mem::take(&mut self.current).with_web_urls();
        let marker = self.marker_container.take();
        let initial = self.container_prefix(marker);
        let continuation = self.container_prefix(None);
        self.lines.extend(word_wrap_hyperlink_line(
            &line,
            RtOptions::new(self.width)
                .initial_indent(Line::from(self.prefix_span(initial)))
                .subsequent_indent(Line::from(self.prefix_span(continuation))),
        ));
    }

    fn flush_code_block(&mut self) {
        let language = self.in_code_block.take().unwrap_or_default();
        let label = if language.trim().is_empty() {
            "code"
        } else {
            &language
        };
        let prefix = self.container_prefix(None);
        let inner = self.width.saturating_sub(prefix.width()).max(1);
        let style = Style::default().fg(self.theme.text_muted);
        let fence = |text: String| {
            HyperlinkLine::new(Line::from(Span::styled(
                text,
                style.add_modifier(Modifier::BOLD),
            )))
        };
        let mut lines =
            word_wrap_hyperlink_line(&fence(format!("``` {label}")), RtOptions::new(inner));
        let code = std::mem::take(&mut self.code_buffer).replace('\t', "    ");
        let highlighted = crate::syntax_highlight::highlight_code_to_lines(
            &code,
            label,
            self.theme.is_light_background(),
        )
        .unwrap_or_else(|| plain_code_lines(&code, Style::default().fg(self.theme.text)));
        for line in highlighted {
            lines.extend(
                code::wrap_row(line, inner, style)
                    .into_iter()
                    .map(HyperlinkLine::new),
            );
        }
        lines.extend(word_wrap_hyperlink_line(
            &fence("```".to_string()),
            RtOptions::new(inner),
        ));
        self.lines.extend(prefix_hyperlink_lines(
            lines,
            self.prefix_span(prefix.clone()),
            self.prefix_span(prefix),
        ));
        self.pending_gap = true;
    }

    fn flush_table(&mut self) {
        if let Some(table) = self.table.take() {
            let prefix = self.container_prefix(None);
            let inner = self.width.saturating_sub(prefix.width()).max(1);
            self.lines.extend(prefix_hyperlink_lines(
                table.render(inner, self.theme),
                self.prefix_span(prefix.clone()),
                self.prefix_span(prefix),
            ));
            self.pending_gap = true;
        }
    }

    fn current_style(&self) -> Style {
        *self
            .style_stack
            .last()
            .unwrap_or(&Style::default().fg(self.theme.text))
    }
}

fn local_link_display(target: &str, cwd: Option<&str>) -> Option<String> {
    links::display(target, cwd)
}

fn heading_style(level: HeadingLevel) -> Style {
    match level {
        HeadingLevel::H1 => Style::default().add_modifier(Modifier::BOLD | Modifier::UNDERLINED),
        HeadingLevel::H2 => Style::default().add_modifier(Modifier::BOLD),
        HeadingLevel::H3 => Style::default().add_modifier(Modifier::BOLD | Modifier::ITALIC),
        HeadingLevel::H4 | HeadingLevel::H5 | HeadingLevel::H6 => {
            Style::default().add_modifier(Modifier::ITALIC)
        }
    }
}

fn list_stack_item_is_ordered(list: Option<&ListState>) -> bool {
    list.and_then(|item| item.next_number).is_some()
}

fn plain_code_lines(code: &str, style: Style) -> Vec<Line<'static>> {
    if code.is_empty() {
        return Vec::new();
    }
    let lines: Vec<Line<'static>> = code
        .lines()
        .map(|line| Line::from(Span::styled(line.to_string(), style)))
        .collect();
    if lines.is_empty() {
        vec![Line::from(String::new())]
    } else {
        lines
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn plain(lines: &[Line<'static>]) -> String {
        lines
            .iter()
            .map(|line| {
                line.spans
                    .iter()
                    .map(|span| span.content.as_ref())
                    .collect::<String>()
            })
            .collect::<Vec<_>>()
            .join("\n")
    }

    fn span_style_for_text<'a>(lines: &'a [Line<'static>], needle: &str) -> Option<Style> {
        lines
            .iter()
            .flat_map(|line| line.spans.iter())
            .find(|span| span.content.as_ref() == needle)
            .map(|span| span.style)
    }

    fn line_texts(lines: &[Line<'static>]) -> Vec<String> {
        lines
            .iter()
            .map(|line| {
                line.spans
                    .iter()
                    .map(|span| span.content.as_ref())
                    .collect::<String>()
            })
            .collect()
    }

    fn line_containing<'a>(lines: &'a [Line<'static>], needle: &str) -> &'a Line<'static> {
        lines
            .iter()
            .find(|line| {
                line.spans
                    .iter()
                    .map(|span| span.content.as_ref())
                    .collect::<String>()
                    .contains(needle)
            })
            .expect("line containing needle")
    }

    #[test]
    fn renders_common_markdown_shapes() {
        let rendered = render_markdown(
            "# Title\n\n- one\n- two\n\n```rust\nlet x = 1;\n```\n\n| A | B |\n| - | - |\n| 1 | 2 |",
            80,
            &ThemePalette::default(),
        );
        let text = plain(&rendered);

        assert!(text.contains("Title"));
        assert!(!text.contains("# Title"));
        assert!(text.contains("• one"));
        assert!(text.contains("``` rust"));
        assert!(
            text.contains('┌') && text.contains('┬') && text.contains('┘'),
            "{text}"
        );
    }

    #[test]
    fn table_cells_keep_inline_code_emphasis_links_and_alignment() {
        let theme = ThemePalette::default();
        let lines = render_markdown("| Source | Protocol | Score |\n| :--- | :---: | ---: |\n| **EEG** | `LOSO` | 96.7 |\n| [MEG](https://example.org) | cross subject | 7 |", 80, &theme);
        let text = plain(&lines);
        assert!(text.contains('┌') && text.contains('│'), "{text}");
        for value in ["Source", "Protocol", "Score", "EEG", "LOSO", "MEG", "96.7"] {
            assert!(text.contains(value), "{value}: {text}");
        }
        assert!(!text.contains('`') && !text.contains("**"), "{text}");
        let code = span_style_for_text(&lines, "LOSO").expect("code inside table");
        assert_eq!(code.fg, Some(theme.text_muted));
        assert!(span_style_for_text(&lines, "EEG")
            .unwrap()
            .add_modifier
            .contains(Modifier::BOLD));
        for line in &lines {
            if line.spans.iter().any(|span| span.content.contains("LOSO")) {
                assert!(
                    line.spans.iter().any(|span| span.content.contains('│')),
                    "Code must stay in its row: {line:?}"
                );
            }
        }
        let score_col = lines
            .iter()
            .filter(|line| {
                line.spans
                    .iter()
                    .any(|span| span.content == "96.7" || span.content == "7")
            })
            .map(|line| {
                line.spans
                    .iter()
                    .map(|span| span.content.as_ref())
                    .collect::<String>()
            })
            .collect::<Vec<_>>();
        assert_eq!(score_col.len(), 2);
        assert_eq!(
            score_col[0].rfind('7'),
            score_col[1].rfind('7'),
            "Right-aligned values must end in the same column"
        );
        assert_eq!(
            score_col[0].trim_end_matches('│').trim_end().chars().last(),
            Some('7')
        );
        assert_eq!(
            score_col[1].trim_end_matches('│').trim_end().chars().last(),
            Some('7')
        );
    }

    #[test]
    fn table_wraps_cjk_urls_empty_cells_and_many_columns_without_losing_values() {
        let source = "| 来源 | 协议 | Evidence | 结果 |\n| --- | --- | --- | --- |\n| 中文证据 | 跨被试评估与校准条件 | https://example.org/a/very/long/source | 96.7 |\n| MEG | | `evidence.md` | 0 |";
        for width in [12, 20, 40, 80, 120] {
            let lines = render_markdown(source, width, &ThemePalette::default());
            let text = plain(&lines);
            assert!(
                lines.iter().all(|line| line.width() <= width),
                "width {width}: {text}"
            );
            let compact = text
                .chars()
                .filter(|c| !c.is_whitespace() && !"│─┌┐└┘┬┴┼├┤:".contains(*c))
                .collect::<String>();
            for value in ["中文证据", "跨被试评估与校准条件", "evidence.md", "96.7"] {
                assert!(compact.contains(value), "width {width}, {value}: {text}");
            }
            assert!(!text.contains("| ---"), "{text}");
        }
    }

    #[test]
    fn partial_streamed_table_is_bounded_and_fenced_table_remains_literal() {
        let theme = ThemePalette::default();
        let source = "| A | B |\n| --- | --- |\n| 中文 | `code` |\n| last | cell |";
        for width in [20, 40, 80] {
            for end in source
                .char_indices()
                .map(|(i, _)| i)
                .chain(std::iter::once(source.len()))
            {
                let lines = render_markdown(&source[..end], width, &theme);
                assert!(
                    lines.iter().all(|line| line.width() <= width),
                    "{end}: {}",
                    plain(&lines)
                );
            }
        }
        let literal = plain(&render_markdown(
            "```text\n| A | B |\n| --- | --- |\n```",
            80,
            &theme,
        ));
        assert!(
            literal.contains("| --- | --- |") && !literal.contains('┌'),
            "{literal}"
        );
    }

    #[test]
    fn quoted_table_keeps_cell_breaks_and_escaped_pipes_inside_the_quote() {
        let source = "> | 参数 | 值 |\n> | --- | --- |\n> | x\\|y | 第一行<br>第二行 |";
        for width in [20, 40, 80] {
            let lines = render_markdown(source, width, &ThemePalette::default());
            let text = plain(&lines);
            assert!(lines.iter().all(|line| line.width() <= width), "{text}");
            assert!(
                lines.iter().all(|line| line
                    .spans
                    .first()
                    .is_some_and(|span| span.content.starts_with("> "))),
                "{text}"
            );
            assert!(
                text.contains("x|y") && text.contains("第一行") && text.contains("第二行"),
                "{text}"
            );
            assert!(!text.contains("<br>"), "{text}");
        }
    }

    #[test]
    fn live_and_committed_assistant_tables_share_layout_with_reserved_gutter() {
        let source="**Evidence**\n\n| 来源 | Protocol | Score |\n| --- | --- | ---: |\n| EEG | `LOSO` | 96.7 |\n| MEG | calibrated | 7 |";
        let theme = ThemePalette::default();
        for width in [20, 40, 80, 120] {
            let committed = crate::history_cell::render_agent_response(source, width, &theme);
            let live = crate::history_cell::render_agent_live_response_with_cwd(
                source, width, &theme, None,
            );
            assert_eq!(plain(&committed), plain(&live));
            assert!(
                committed.iter().all(|line| line.width() <= width),
                "{}",
                plain(&committed)
            );
            assert!(!plain(&committed).contains('`'));
        }
    }

    #[test]
    fn ordinary_markdown_styles_do_not_use_theme_purple_accents() {
        let theme = ThemePalette::default();
        let rendered = render_markdown(
            "# Title\n\n- unordered\n\n1. ordered\n\n`code` [link](https://example.com)\n\n> quote",
            80,
            &theme,
        );

        let heading_text = span_style_for_text(&rendered, "Title").expect("heading text");
        assert!(heading_text
            .add_modifier
            .contains(Modifier::BOLD | Modifier::UNDERLINED));
        assert_eq!(heading_text.fg, None);

        let unordered = span_style_for_text(&rendered, "• ").expect("unordered marker");
        assert_eq!(unordered.fg, Some(theme.text));

        let ordered = span_style_for_text(&rendered, "1. ").expect("ordered marker");
        assert_eq!(ordered.fg, Some(theme.text));

        let inline_code = span_style_for_text(&rendered, "code").expect("inline code");
        assert_eq!(inline_code.fg, Some(theme.text_muted));

        let link = span_style_for_text(&rendered, "link").expect("link");
        assert_eq!(link.fg, Some(theme.accent));
        assert!(link.add_modifier.contains(Modifier::UNDERLINED));

        let quote_bar = span_style_for_text(&rendered, "> ").expect("blockquote prefix");
        assert_eq!(quote_bar.fg, Some(theme.border));

        // Graphite Signal: only links carry the signal accent; every other
        // markdown element stays grayscale (text / muted / border).
        for style in [heading_text, unordered, ordered, inline_code, quote_bar] {
            assert_ne!(style.fg, Some(theme.accent));
            assert_ne!(style.fg, Some(theme.accent_soft));
            assert_ne!(style.fg, Some(theme.focus));
        }
    }

    #[test]
    fn wrapping_preserves_span_styles_across_lines() {
        let rendered = render_markdown("**Bold** normal words", 8, &ThemePalette::default());

        let bold = span_style_for_text(&rendered, "Bold").expect("bold span");
        assert!(bold.add_modifier.contains(Modifier::BOLD));

        let normal = span_style_for_text(&rendered, "normal").expect("normal span");
        assert!(!normal.add_modifier.contains(Modifier::BOLD));
    }

    #[test]
    fn wrapped_urls_keep_full_click_targets_without_overflow() {
        let url = "https://example.com/some/really-long/path-name";
        let rendered = render_markdown_hyperlinks(
            &format!("See {url} for details"),
            12,
            &ThemePalette::default(),
            None,
        );
        assert!(rendered.iter().all(|line| line.line.width() <= 12));
        assert!(rendered
            .iter()
            .flat_map(|line| &line.hyperlinks)
            .any(|link| link.destination == url));
    }

    #[test]
    fn oversized_cjk_token_fills_remaining_line_after_ascii_token() {
        let rendered = render_markdown(
            "在一个隔离的 direct_qa 阶段适合回答清晰直接的问题并继续执行",
            30,
            &ThemePalette::default(),
        );
        let lines = line_texts(&rendered);

        assert_eq!(lines[0], "在一个隔离的 direct_qa 阶段适");
        assert_eq!(UnicodeWidthStr::width(lines[0].as_str()), 29);
    }

    #[test]
    fn ordinary_markdown_respects_widths_below_eight_columns() {
        for width in 1..8 {
            let rendered = render_markdown("abcdefghijk", width, &ThemePalette::default());
            assert!(
                rendered.iter().all(|line| line.width() <= width),
                "width={width} rendered={:?}",
                line_texts(&rendered)
            );
        }
    }

    #[test]
    fn local_file_links_render_the_target_path() {
        let rendered = render_markdown(
            "[source file](/tmp/project/src/main.rs:12)",
            80,
            &ThemePalette::default(),
        );
        let text = plain(&rendered);

        assert!(text.contains("/tmp/project/src/main.rs:12"));
        assert!(!text.contains("source file"));
    }

    #[test]
    fn fenced_code_block_known_language_uses_syntax_colors() {
        let rendered = render_markdown("```rust\nfn main() {}\n```", 80, &ThemePalette::default());
        let code_line = line_containing(&rendered, "fn main");

        assert_eq!(
            code_line
                .spans
                .iter()
                .map(|span| span.content.as_ref())
                .collect::<String>(),
            "  fn main() {}"
        );
        assert!(
            code_line
                .spans
                .iter()
                .any(|span| span.style.fg.is_some() && span.content.as_ref() != "  "),
            "expected syntax-colored code spans: {code_line:?}"
        );
    }

    #[test]
    fn fenced_code_block_unknown_language_uses_plain_code_style() {
        let theme = ThemePalette::default();
        let rendered = render_markdown("```xyzlang\nhello world\n```", 80, &theme);
        let code_line = line_containing(&rendered, "hello world");

        assert_eq!(
            code_line
                .spans
                .iter()
                .map(|span| span.content.as_ref())
                .collect::<String>(),
            "  hello world"
        );
        let content_span = code_line
            .spans
            .iter()
            .find(|span| span.content.as_ref() == "hello world")
            .expect("plain code content span");
        assert_eq!(content_span.style.fg, Some(theme.text));
    }
}

#[cfg(test)]
mod cwd_link_tests {
    use super::*;

    fn line_texts(lines: &[Line<'static>]) -> Vec<String> {
        lines
            .iter()
            .map(|line| {
                line.spans
                    .iter()
                    .map(|span| span.content.as_ref())
                    .collect::<String>()
            })
            .collect()
    }

    #[test]
    fn absolute_link_inside_cwd_is_relativized() {
        let lines = render_markdown_with_cwd(
            "[label](/repo/src/main.rs)",
            80,
            &ThemePalette::default(),
            Some("/repo"),
        );

        assert!(line_texts(&lines)
            .iter()
            .any(|line| line.contains("src/main.rs")));
        assert!(!line_texts(&lines)
            .iter()
            .any(|line| line.contains("/repo/src/main.rs")));
    }

    #[test]
    fn absolute_link_outside_cwd_stays_absolute() {
        let lines = render_markdown_with_cwd(
            "[label](/other/src/main.rs)",
            80,
            &ThemePalette::default(),
            Some("/repo"),
        );

        assert!(line_texts(&lines)
            .iter()
            .any(|line| line.contains("/other/src/main.rs")));
    }

    #[test]
    fn relative_link_is_unchanged() {
        let lines = render_markdown_with_cwd(
            "[label](./docs/guide.md)",
            80,
            &ThemePalette::default(),
            Some("/repo"),
        );

        assert!(line_texts(&lines)
            .iter()
            .any(|line| line.contains("./docs/guide.md")));
    }
}
