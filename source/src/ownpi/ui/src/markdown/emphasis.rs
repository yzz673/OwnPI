//! Rendering-only compatibility for `**总结：**正文` emitted by models.
//!
//! CommonMark leaves these delimiters literal because the closing punctuation
//! touches a word. Convert only unused, unescaped double-star text tokens;
//! code, destinations, and emphasis already recognized by the parser stay intact.

use pulldown_cmark::{Event, Parser, Tag, TagEnd};

pub(super) fn cjk_strong_events<'a>(source: &str, parser: Parser<'a>) -> Vec<Event<'a>> {
    let events: Vec<_> = parser.into_offset_iter().collect();
    let mut replacements = vec![None; events.len()];
    let mut omitted = vec![false; events.len()];
    // A pair must open and close in the same parser container, so it cannot
    // cross paragraph/table-cell boundaries or improperly nest around a link.
    let mut openers: Vec<Option<(usize, usize)>> = vec![None];
    let mut verbatim = false;
    let mut index = 0;
    while index < events.len() {
        match &events[index].0 {
            Event::Start(tag) => {
                if matches!(tag, Tag::CodeBlock(_) | Tag::HtmlBlock) {
                    verbatim = true;
                }
                openers.push(None);
            }
            Event::End(tag) => {
                if matches!(tag, TagEnd::CodeBlock | TagEnd::HtmlBlock) {
                    verbatim = false;
                }
                openers.pop();
            }
            Event::HardBreak | Event::Html(_) | Event::InlineHtml(_) => {
                *openers.last_mut().unwrap() = None;
            }
            Event::Text(_) if !verbatim => {
                if let Some(end_index) = double_star(source, &events, index) {
                    let start = events[index].1.start;
                    let end = events[end_index].1.end;
                    let before = source[..start].chars().next_back();
                    let after = source[end..].chars().next();
                    let opener = openers.last_mut().unwrap();
                    if before.is_some_and(is_cjk_closing_punctuation)
                        && after.is_some_and(char::is_alphanumeric)
                        && opener.is_some()
                    {
                        let (open_index, open_end) = opener.take().unwrap();
                        replacements[open_index] = Some(true);
                        replacements[index] = Some(false);
                        omitted[open_index + 1..=open_end].fill(true);
                        omitted[index + 1..=end_index].fill(true);
                    } else if after.is_some_and(|ch| !ch.is_whitespace()) {
                        *opener = Some((index, end_index));
                    } else {
                        *opener = None;
                    }
                    index = end_index;
                }
            }
            _ => {}
        }
        index += 1;
    }
    events
        .into_iter()
        .enumerate()
        .filter_map(|(index, (event, _))| {
            if omitted[index] {
                None
            } else {
                Some(match replacements[index] {
                    Some(true) => Event::Start(Tag::Strong),
                    Some(false) => Event::End(TagEnd::Strong),
                    None => event,
                })
            }
        })
        .collect()
}

fn double_star(
    source: &str,
    events: &[(Event<'_>, std::ops::Range<usize>)],
    index: usize,
) -> Option<usize> {
    let (Event::Text(text), range) = &events[index] else {
        return None;
    };
    let end_index = if text.as_ref() == "**" && &source[range.clone()] == "**" {
        index
    } else if text.as_ref() == "*" && &source[range.clone()] == "*" {
        let (Event::Text(next), next_range) = events.get(index + 1)? else {
            return None;
        };
        if next.as_ref() != "*"
            || next_range.start != range.end
            || &source[next_range.clone()] != "*"
        {
            return None;
        }
        index + 1
    } else {
        return None;
    };
    let before = &source[..range.start];
    let after = &source[events[end_index].1.end..];
    if before.ends_with('*')
        || after.starts_with('*')
        || before
            .bytes()
            .rev()
            .take_while(|byte| *byte == b'\\')
            .count()
            % 2
            == 1
    {
        return None;
    }
    Some(end_index)
}

fn is_cjk_closing_punctuation(ch: char) -> bool {
    "，。！？：；、）》」』】〕〉〗〙〛”’".contains(ch)
}
