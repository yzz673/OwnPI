//! Display-only citation binding. Never infer a URL from source order or a title.
use std::collections::BTreeMap;

use pulldown_cmark::{Event, Options, Parser, Tag, TagEnd};
use serde_json::Value;

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct CitationSources(BTreeMap<String, Option<Source>>);

#[derive(Clone, Debug, PartialEq, Eq)]
struct Source {
    title: String,
    url: String,
}

fn http_url(value: &str) -> Option<String> {
    if value
        .chars()
        .any(|c| c.is_control() || c.is_whitespace() || matches!(c, '<' | '>' | '"' | '`' | '\\'))
    {
        return None;
    }
    let url = url::Url::parse(value).ok()?;
    (matches!(url.scheme(), "http" | "https")
        && url.host_str().is_some()
        && url.username().is_empty()
        && url.password().is_none())
    .then(|| value.to_owned())
}

fn provider_id(id: &str) -> bool {
    id.starts_with("turn")
        && id
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '-'))
}

fn field<'a>(v: &'a Value, name: &str) -> &'a str {
    v[name].as_str().unwrap_or_default()
}

fn scope(item: &Value, audit: bool) -> Option<(String, String)> {
    let owner = &item[if audit { "audit_context" } else { "agent_ref" }];
    let run = field(owner, "run_id");
    let id = field(owner, if audit { "audit_id" } else { "conversation_id" });
    (!run.is_empty() && !id.is_empty()).then(|| (run.to_owned(), id.to_owned()))
}

/// Built once when canonical history changes, never by scanning history per
/// rendered message or on each animation tick.
#[derive(Default, PartialEq, Eq)]
pub(crate) struct CitationCatalog {
    conversations: BTreeMap<(String, String), CitationSources>,
    audits: BTreeMap<(String, String), CitationSources>,
}

impl CitationCatalog {
    pub(crate) fn new(history: &[Value]) -> Self {
        let mut catalog = Self::default();
        for item in history {
            let mut sources = CitationSources::default();
            sources.read_record(item);
            for (audit, index) in [
                (false, &mut catalog.conversations),
                (true, &mut catalog.audits),
            ] {
                if let Some(key) = scope(item, audit) {
                    index.entry(key).or_default().merge(&sources);
                }
            }
        }
        catalog
    }

    pub(crate) fn for_item(&self, item: &Value) -> CitationSources {
        let mut sources = if let Some(key) = scope(item, false) {
            self.conversations.get(&key).cloned().unwrap_or_default()
        } else {
            scope(item, true)
                .and_then(|key| self.audits.get(&key))
                .cloned()
                .unwrap_or_default()
        };
        sources.read_record(item);
        sources
    }
}

impl CitationSources {
    #[cfg(test)]
    pub(crate) fn for_item(item: &Value, history: &[Value]) -> Self {
        CitationCatalog::new(history).for_item(item)
    }

    fn merge(&mut self, other: &Self) {
        for (id, source) in &other.0 {
            if let Some(source) = source {
                self.insert(id, source.clone());
            } else {
                self.0.insert(id.clone(), None);
            }
        }
    }

    fn read_record(&mut self, item: &Value) {
        // Only original structured records can establish an ID/URL binding.
        // Ordinary prose, bare URL lists, queries and model guesses cannot.
        if let Ok(raw) = serde_json::from_str::<Value>(field(item, "raw_source")) {
            self.collect(&raw, 0);
        }
    }

    fn insert(&mut self, id: &str, source: Source) {
        match self.0.entry(id.to_owned()) {
            std::collections::btree_map::Entry::Vacant(entry) => {
                entry.insert(Some(source));
            }
            std::collections::btree_map::Entry::Occupied(mut entry) => {
                if let Some(previous) = entry.get_mut() {
                    if previous.url != source.url {
                        *entry.get_mut() = None; // Conflicting explicit bindings stay unresolved.
                    } else if previous.title.is_empty() {
                        previous.title = source.title;
                    }
                }
            }
        }
    }

    fn collect(&mut self, value: &Value, depth: usize) {
        if depth > 32 {
            return;
        }
        match value {
            Value::Object(object) => {
                let id = ["ref_id", "citation_id", "source_id", "id"]
                    .into_iter()
                    .map(|key| field(value, key))
                    .find(|id| provider_id(id));
                if let (Some(id), Some(url)) = (id, http_url(field(value, "url"))) {
                    self.insert(
                        id,
                        Source {
                            title: field(value, "title").to_owned(),
                            url,
                        },
                    );
                }
                // Provider annotations can bind an exact marker via an exact
                // text span. Do not guess whether numeric offsets are bytes,
                // characters or UTF-16 units.
                if let (Some(text), Some(annotations)) = (
                    object.get("text").and_then(Value::as_str),
                    object.get("annotations").and_then(Value::as_array),
                ) {
                    for annotation in annotations {
                        if let (Some(quote), Some(url)) = (
                            annotation["text"].as_str(),
                            http_url(field(annotation, "url")),
                        ) {
                            let ids = marker_ids(quote);
                            if text.contains(quote) && ids.len() == 1 {
                                for id in ids {
                                    self.insert(
                                        &id,
                                        Source {
                                            title: field(annotation, "title").to_owned(),
                                            url: url.clone(),
                                        },
                                    );
                                }
                            }
                        }
                    }
                }
                for (key, child) in object {
                    if !matches!(key.as_str(), "arguments" | "instructions" | "input") {
                        self.collect(child, depth + 1);
                    }
                }
            }
            Value::Array(values) => {
                for child in values {
                    self.collect(child, depth + 1);
                }
            }
            _ => {}
        }
    }
}

const START: &str = "\u{e200}cite\u{e202}";
const END: char = '\u{e201}';

fn marker_ids(text: &str) -> Vec<String> {
    text.split(START)
        .skip(1)
        .filter_map(|part| part.split_once(END))
        .flat_map(|(ids, _)| ids.split('\u{e202}'))
        .filter(|id| provider_id(id))
        .map(str::to_owned)
        .collect()
}

fn escape(text: &str) -> String {
    let mut out = String::new();
    for c in text.chars() {
        if matches!(
            c,
            '\\' | '[' | ']' | '*' | '_' | '`' | '<' | '>' | '#' | '|'
        ) {
            out.push('\\');
        }
        if c.is_control() {
            out.push(' ');
        } else {
            out.push(c);
        }
    }
    out
}

/// One numbering scope per complete message or review (not per Why paragraph).
pub(crate) struct CitationDocument {
    sources: CitationSources,
    entries: Vec<(String, Source)>,
    unresolved: Vec<String>,
    number_offset: usize,
}

impl CitationDocument {
    pub(crate) fn new(sources: CitationSources) -> Self {
        Self {
            sources,
            entries: Vec::new(),
            unresolved: Vec::new(),
            number_offset: 0,
        }
    }

    pub(crate) fn reserve_labels(&mut self, text: &str) {
        if !self.entries.is_empty() {
            return;
        }
        for suffix in text.split('[').skip(1) {
            if let Some((label, _)) = suffix.split_once(']') {
                if label.chars().all(|c| c.is_ascii_digit()) {
                    if let Ok(number) = label.parse::<u32>() {
                        self.number_offset = self.number_offset.max(number as usize);
                    }
                }
            }
        }
    }

    pub(crate) fn body(&mut self, text: &str) -> String {
        if !text.contains(START) {
            return text.to_owned();
        }
        self.reserve_labels(text);
        let mut edits = Vec::new();
        let mut link: Option<String> = None;
        let mut code = false;
        for (event, range) in Parser::new_ext(text, Options::ENABLE_TABLES).into_offset_iter() {
            match event {
                Event::Start(Tag::CodeBlock(_)) => code = true,
                Event::End(TagEnd::CodeBlock) => code = false,
                Event::Start(Tag::Link { dest_url, .. }) => link = http_url(&dest_url),
                Event::End(TagEnd::Link) => link = None,
                Event::Text(_) if !code => {
                    let slice = &text[range.clone()];
                    let mut cursor = 0;
                    while let Some(at) = slice[cursor..].find(START) {
                        let begin = cursor + at;
                        let content = begin + START.len();
                        let Some(end) = slice[content..].find(END).map(|n| content + n) else {
                            break;
                        };
                        let ids: Vec<_> = slice[content..end].split('\u{e202}').collect();
                        if ids.iter().all(|id| provider_id(id)) {
                            let mut replacement = String::new();
                            for id in ids {
                                let source = link
                                    .as_ref()
                                    .map(|url| Source {
                                        title: String::new(),
                                        url: url.clone(),
                                    })
                                    .or_else(|| self.sources.0.get(id).cloned().flatten());
                                let Some(source) = source else {
                                    if !self.unresolved.iter().any(|key| key == id) {
                                        self.unresolved.push(id.to_owned());
                                    }
                                    // An opaque ID without a source is a diagnostic,
                                    // not a reference. It must not consume a number.
                                    continue;
                                };
                                let number = self
                                    .entries
                                    .iter()
                                    .position(|(key, _)| key == id)
                                    .map(|n| n + 1)
                                    .unwrap_or_else(|| {
                                        self.entries.push((id.to_owned(), source));
                                        self.entries.len()
                                    });
                                let label = format!("\\[{}\\]", number + self.number_offset);
                                if link.is_none() {
                                    let source = &self.entries[number - 1].1;
                                    replacement.push_str(&format!(
                                        "[{label}](<{}>)",
                                        source.url.replace('&', "&amp;")
                                    ));
                                } else {
                                    replacement.push_str(&label);
                                }
                            }
                            edits.push((
                                range.start + begin..range.start + end + END.len_utf8(),
                                replacement,
                            ));
                        }
                        cursor = end + END.len_utf8();
                    }
                }
                _ => {}
            }
        }
        let mut result = text.to_owned();
        for (range, replacement) in edits.into_iter().rev() {
            result.replace_range(range, &replacement);
        }
        result
    }

    pub(crate) fn references(&self) -> String {
        self.entries
            .iter()
            .enumerate()
            .map(|(n, (_, source))| {
                let title = if source.title.is_empty() {
                    String::new()
                } else {
                    format!("{} — ", escape(&source.title))
                };
                let url = source.url.replace('&', "&amp;");
                format!(
                    "\\[{}\\] {title}[{}](<{url}>)",
                    n + 1 + self.number_offset,
                    escape(&url)
                )
            })
            .collect::<Vec<_>>()
            .join("\n\n")
    }

    /// Detail-only diagnostics. Never append these as numbered bibliography.
    pub(crate) fn diagnostics(&self) -> String {
        let rows = self
            .unresolved
            .iter()
            .filter(|id| !self.entries.iter().any(|(known, _)| known == *id))
            .map(|id| {
                let reason = if self.sources.0.get(id).is_some_and(Option::is_none) {
                    "引用来源记录冲突"
                } else {
                    "引用 ID 未匹配"
                };
                format!("- {reason}：{}", escape(id))
            })
            .collect::<Vec<_>>();
        if rows.is_empty() {
            String::new()
        } else {
            format!("引用诊断\n\n{}", rows.join("\n"))
        }
    }

    #[cfg(test)]
    pub(crate) fn complete(&mut self, text: &str) -> String {
        let mut result = self.body(text);
        let references = self.references();
        if !references.is_empty() {
            result.push_str("\n\n");
            result.push_str(&references);
        }
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn record(conversation: &str, url: &str) -> Value {
        json!({"agent_ref":{"run_id":"run","conversation_id":conversation},
            "raw_source":json!({"sources":[{"ref_id":"turn1search0","title":"EEG study","url":url}]}).to_string()})
    }

    #[test]
    fn unsafe_urls_are_not_links_and_manual_numbers_are_not_reused() {
        for url in [
            "javascript:alert(1)",
            "file:///tmp/data",
            "https://name:secret@example.org/a",
            "https://example.org/\u{1b}",
        ] {
            let item = record("reviewer", url);
            let sources = CitationSources::for_item(&item, &[]);
            let mut doc = CitationDocument::new(sources);
            let text = doc.complete("Already [1]. Nextciteturn1search0");
            assert_eq!(text, "Already [1]. Next");
            assert!(doc.diagnostics().contains("turn1search0"));
            assert!(!text.contains(url), "{text}");
        }
    }

    #[test]
    fn unknown_ids_do_not_consume_numbers_and_diagnostics_are_deduplicated() {
        let known = record("reviewer", "https://example.org/known");
        let mut doc = CitationDocument::new(CitationSources::for_item(&known, &[]));
        let body = doc.complete("Manual [1]. Aciteturn9view0turn1search0 Bciteturn9view0");
        assert!(body.contains("\\[2\\]"), "{body}");
        assert!(!body.contains("\\[3\\]") && !body.contains("turn9view0"));
        assert!(body.contains("EEG study") && body.contains("https://example.org/known"));
        assert_eq!(doc.diagnostics().matches("turn9view0").count(), 1);
        assert!(!doc.diagnostics().contains("[1]"));
    }

    #[test]
    fn audit_result_uses_only_its_own_audit_and_run() {
        let mut web = record("reviewer", "https://example.org/a");
        web["audit_context"] = json!({"run_id":"run", "audit_id":"round-1"});
        let target = json!({"audit_context":{"run_id":"run", "audit_id":"round-1"}});
        assert!(CitationSources::for_item(&target, &[web.clone()])
            .0
            .contains_key("turn1search0"));
        web["audit_context"]["run_id"] = json!("other-run");
        assert!(CitationSources::for_item(&target, &[web]).0.is_empty());
    }

    #[test]
    fn explicit_sources_are_scoped_and_conflicts_do_not_guess() {
        let item = json!({"agent_ref":{"run_id":"run","conversation_id":"reviewer"}});
        let sources = CitationSources::for_item(
            &item,
            &[
                record("reviewer", "https://example.org/a"),
                record("other", "https://example.org/b"),
            ],
        );
        let mut doc = CitationDocument::new(sources);
        let body = doc.complete("Aciteturn1search0turn2view0 Bciteturn1search0");
        assert!(body.contains("EEG study"));
        assert!(body.contains("https://example.org/a"));
        assert!(!body.contains("https://example.org/b"));
        assert_eq!(doc.entries.len(), 1);
        assert!(!body.contains("turn2view0"));
        assert!(doc.diagnostics().contains("turn2view0"));
        let sources = CitationSources::for_item(
            &item,
            &[
                record("reviewer", "https://example.org/a"),
                record("reviewer", "https://example.org/b"),
            ],
        );
        assert!(sources.0["turn1search0"].is_none());
        let mut conflicting = CitationDocument::new(sources);
        assert_eq!(conflicting.complete("Aciteturn1search0"), "A");
        assert!(conflicting.diagnostics().contains("引用来源记录冲突"));
    }

    #[test]
    fn code_and_partial_markers_stay_literal_and_linked_markers_keep_urls() {
        let mut doc = CitationDocument::new(Default::default());
        let marker = "citeturn1search0";
        assert_eq!(doc.body(&format!("`{marker}`")), format!("`{marker}`"));
        assert_eq!(
            doc.body(&format!("```text\n{marker}\n```")),
            format!("```text\n{marker}\n```")
        );
        assert_eq!(doc.body("partial citeturn1"), "partial citeturn1");
        assert!(doc
            .complete(&format!("[{marker}](https://example.org/a)"))
            .contains("https://example.org/a"));
    }
}
