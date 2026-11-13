use std::collections::HashSet;

use super::textarea::TextElementSnapshot;

const HISTORY_LIMIT: usize = 100;

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct HistoryEntry {
    pub(crate) text: String,
    pub(crate) cursor: usize,
    pub(crate) text_elements: Vec<TextElementSnapshot>,
    pub(crate) local_image_paths: Vec<String>,
    pub(crate) remote_image_urls: Vec<String>,
    pub(crate) mention_bindings: Vec<String>,
    pub(crate) pending_pastes: Vec<(String, String)>,
}

impl HistoryEntry {
    pub(crate) fn new(text: impl Into<String>) -> Self {
        let text = text.into();
        let cursor = text.len();
        Self {
            text,
            cursor,
            text_elements: Vec::new(),
            local_image_paths: Vec::new(),
            remote_image_urls: Vec::new(),
            mention_bindings: Vec::new(),
            pending_pastes: Vec::new(),
        }
    }

    #[cfg(test)]
    pub(crate) fn with_cursor(text: impl Into<String>, cursor: usize) -> Self {
        let text = text.into();
        Self {
            cursor: cursor.min(text.len()),
            text,
            text_elements: Vec::new(),
            local_image_paths: Vec::new(),
            remote_image_urls: Vec::new(),
            mention_bindings: Vec::new(),
            pending_pastes: Vec::new(),
        }
    }

    pub(crate) fn with_rich_metadata(
        text: impl Into<String>,
        text_elements: Vec<TextElementSnapshot>,
        local_image_paths: Vec<String>,
        remote_image_urls: Vec<String>,
        mention_bindings: Vec<String>,
        pending_pastes: Vec<(String, String)>,
    ) -> Self {
        let text = text.into();
        Self {
            cursor: text.len(),
            text,
            text_elements,
            local_image_paths,
            remote_image_urls,
            mention_bindings,
            pending_pastes,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum HistoryDirection {
    Older,
    Newer,
}

#[derive(Debug, Default)]
pub(crate) struct ChatComposerHistory {
    persistent_history: Vec<HistoryEntry>,
    persistent_log_id: u64,
    local_history: Vec<HistoryEntry>,
    history_cursor: Option<isize>,
    last_history_text: Option<String>,
}

impl ChatComposerHistory {
    #[cfg(test)]
    pub(crate) fn latest_entry_for_test(&self) -> Option<HistoryEntry> {
        self.local_history.last().cloned()
    }

    pub(crate) fn set_persistent_history(&mut self, entries: Vec<HistoryEntry>, log_id: u64) {
        if self.persistent_log_id == log_id && self.persistent_history == entries {
            return;
        }
        self.persistent_history = entries;
        self.persistent_log_id = log_id;
        self.reset_navigation();
    }

    #[cfg(test)]
    pub(crate) fn apply_persistent_history_response(
        &mut self,
        log_id: u64,
        offset: usize,
        entry: HistoryEntry,
    ) -> bool {
        if log_id != self.persistent_log_id || offset > self.persistent_history.len() {
            return false;
        }
        if offset == self.persistent_history.len() {
            self.persistent_history.push(entry);
        } else {
            self.persistent_history[offset] = entry;
        }
        self.reset_navigation();
        true
    }

    pub(crate) fn record_local_submission(&mut self, entry: HistoryEntry) {
        let raw_text = entry.text;
        let text = raw_text.trim();
        if text.is_empty() {
            return;
        }

        self.reset_navigation();
        let trim_start = raw_text.find(text).unwrap_or(0);
        let trim_end = trim_start + text.len();
        let cursor = entry.cursor.saturating_sub(trim_start).min(text.len());
        let text_elements = entry
            .text_elements
            .into_iter()
            .filter_map(|mut element| {
                if element.range.start < trim_start || element.range.end > trim_end {
                    return None;
                }
                element.range.start -= trim_start;
                element.range.end -= trim_start;
                Some(element)
            })
            .collect();
        let mut entry = HistoryEntry {
            text: raw_text[trim_start..trim_end].to_string(),
            cursor,
            text_elements,
            local_image_paths: entry.local_image_paths,
            remote_image_urls: entry.remote_image_urls,
            mention_bindings: entry.mention_bindings,
            pending_pastes: entry.pending_pastes,
        };
        entry.cursor = entry.cursor.min(entry.text.len());
        if self.local_history.last().is_some_and(|prev| prev == &entry) {
            return;
        }
        self.local_history.push(entry);
        if self.local_history.len() > HISTORY_LIMIT {
            let overflow = self.local_history.len().saturating_sub(HISTORY_LIMIT);
            self.local_history.drain(0..overflow);
        }
    }

    pub(crate) fn reset_navigation(&mut self) {
        self.history_cursor = None;
        self.last_history_text = None;
    }

    pub(crate) fn should_handle_navigation(&self, text: &str, cursor: usize) -> bool {
        if self.combined_len() == 0 {
            return false;
        }
        if text.is_empty() {
            return true;
        }
        if cursor != 0 && cursor != text.len() {
            return false;
        }
        matches!(&self.last_history_text, Some(previous) if previous == text)
    }

    pub(crate) fn navigate_up(&mut self) -> Option<HistoryEntry> {
        let len = self.combined_len();
        if len == 0 {
            return None;
        }

        let next = match self.history_cursor {
            None => len as isize - 1,
            Some(0) => return None,
            Some(index) => index - 1,
        };
        self.history_cursor = Some(next);
        self.entry_at(next as usize)
    }

    pub(crate) fn navigate_down(&mut self) -> Option<HistoryEntry> {
        let len = self.combined_len();
        if len == 0 {
            return None;
        }
        let index = self.history_cursor?;
        let next = index + 1;
        if next as usize >= len {
            self.reset_navigation();
            return Some(HistoryEntry::new(String::new()));
        }
        self.history_cursor = Some(next);
        self.entry_at(next as usize)
    }

    pub(crate) fn search(
        &mut self,
        query: &str,
        direction: HistoryDirection,
        current_match_text: Option<&str>,
    ) -> Option<HistoryEntry> {
        if query.is_empty() || self.combined_len() == 0 {
            return None;
        }

        let query = query.to_lowercase();
        let mut seen = HashSet::new();
        let matches = self
            .combined_entries()
            .into_iter()
            .rev()
            .filter(|entry| {
                let inserted = seen.insert(entry.text.clone());
                inserted && entry.text.to_lowercase().contains(&query)
            })
            .collect::<Vec<_>>();

        if matches.is_empty() {
            return None;
        }

        let selected = current_match_text
            .and_then(|text| matches.iter().position(|entry| entry.text == text))
            .unwrap_or(usize::MAX);
        let next_index = match direction {
            HistoryDirection::Older => {
                if selected == usize::MAX {
                    0
                } else {
                    (selected + 1).min(matches.len().saturating_sub(1))
                }
            }
            HistoryDirection::Newer => {
                if selected == usize::MAX {
                    matches.len().saturating_sub(1)
                } else {
                    selected.saturating_sub(1)
                }
            }
        };
        let entry = matches.get(next_index).cloned();
        if let Some(entry) = &entry {
            self.last_history_text = Some(entry.text.clone());
        }
        entry
    }

    fn entry_at(&mut self, index: usize) -> Option<HistoryEntry> {
        let entry = if index < self.persistent_history.len() {
            self.persistent_history.get(index).cloned()
        } else {
            self.local_history
                .get(index.saturating_sub(self.persistent_history.len()))
                .cloned()
        };
        if let Some(entry) = &entry {
            self.last_history_text = Some(entry.text.clone());
        }
        entry
    }

    fn combined_len(&self) -> usize {
        self.persistent_history.len() + self.local_history.len()
    }

    fn combined_entries(&self) -> Vec<HistoryEntry> {
        self.persistent_history
            .iter()
            .chain(self.local_history.iter())
            .cloned()
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn navigation_requires_history() {
        let history = ChatComposerHistory::default();
        assert!(!history.should_handle_navigation("", 0));
    }

    #[test]
    fn records_and_walks_local_history() {
        let mut history = ChatComposerHistory::default();
        history.record_local_submission(HistoryEntry::new("first"));
        history.record_local_submission(HistoryEntry::new("second"));

        assert_eq!(history.navigate_up().unwrap().text, "second");
        assert_eq!(history.navigate_up().unwrap().text, "first");
        assert!(history.navigate_up().is_none());
        assert_eq!(history.navigate_down().unwrap().text, "second");
        assert_eq!(history.navigate_down().unwrap().text, "");
    }

    #[test]
    fn persistent_history_is_recalled_before_local_history_when_older() {
        let mut history = ChatComposerHistory::default();
        history.set_persistent_history(
            vec![
                HistoryEntry::new("persisted first"),
                HistoryEntry::new("persisted second"),
            ],
            10,
        );
        history.record_local_submission(HistoryEntry::new("local newest"));

        assert_eq!(history.navigate_up().unwrap().text, "local newest");
        assert_eq!(history.navigate_up().unwrap().text, "persisted second");
        assert_eq!(history.navigate_up().unwrap().text, "persisted first");
        assert!(history.navigate_up().is_none());
    }

    #[test]
    fn stale_persistent_history_response_is_ignored() {
        let mut history = ChatComposerHistory::default();
        history.set_persistent_history(vec![HistoryEntry::new("old")], 22);

        assert!(!history.apply_persistent_history_response(21, 0, HistoryEntry::new("stale")));
        assert_eq!(history.navigate_up().unwrap().text, "old");
    }

    #[test]
    fn persistent_history_search_uses_exact_text_deduplication() {
        let mut history = ChatComposerHistory::default();
        history.set_persistent_history(
            vec![
                HistoryEntry::new("cargo check"),
                HistoryEntry::new("cargo check"),
                HistoryEntry::new("cargo test"),
            ],
            5,
        );

        assert_eq!(
            history
                .search("cargo", HistoryDirection::Older, None)
                .unwrap()
                .text,
            "cargo test"
        );
        assert_eq!(
            history
                .search("cargo", HistoryDirection::Older, Some("cargo test"))
                .unwrap()
                .text,
            "cargo check"
        );
    }

    #[test]
    fn non_empty_navigation_requires_recalled_text_at_boundary() {
        let mut history = ChatComposerHistory::default();
        history.record_local_submission(HistoryEntry::new("hello"));
        let recalled = history.navigate_up().unwrap();

        assert!(history.should_handle_navigation(&recalled.text, recalled.text.len()));
        assert!(history.should_handle_navigation(&recalled.text, 0));
        assert!(!history.should_handle_navigation("hello edited", 12));
        assert!(!history.should_handle_navigation(&recalled.text, 2));
    }

    #[test]
    fn adjacent_duplicates_are_collapsed() {
        let mut history = ChatComposerHistory::default();
        history.record_local_submission(HistoryEntry::new("hello"));
        history.record_local_submission(HistoryEntry::new("hello"));

        assert_eq!(history.navigate_up().unwrap().text, "hello");
        assert!(history.navigate_up().is_none());
    }

    #[test]
    fn records_cursor_with_rich_history_entry() {
        let mut history = ChatComposerHistory::default();
        history.record_local_submission(HistoryEntry::with_cursor("hello world", "hello".len()));

        let recalled = history.navigate_up().unwrap();

        assert_eq!(recalled.text, "hello world");
        assert_eq!(recalled.cursor, "hello".len());
    }

    #[test]
    fn trimmed_history_entry_preserves_relative_cursor() {
        let mut history = ChatComposerHistory::default();
        history.record_local_submission(HistoryEntry::with_cursor("  hello world  ", 7));

        let recalled = history.navigate_up().unwrap();

        assert_eq!(recalled.text, "hello world");
        assert_eq!(recalled.cursor, 5);
    }

    #[test]
    fn rich_history_entry_metadata_is_preserved() {
        let mut history = ChatComposerHistory::default();
        history.record_local_submission(HistoryEntry::with_rich_metadata(
            "prompt [Image #1] [Pasted Content 12 chars]",
            vec![TextElementSnapshot {
                range: "prompt ".len().."prompt [Image #1]".len(),
                text: "[Image #1]".to_string(),
            }],
            vec!["/tmp/image.png".to_string()],
            vec!["https://example.test/image.png".to_string()],
            vec!["@tool".to_string()],
            vec![(
                "[Pasted Content 12 chars]".to_string(),
                "full pasted text".to_string(),
            )],
        ));

        let recalled = history.navigate_up().unwrap();

        assert_eq!(
            recalled.text_elements,
            vec![TextElementSnapshot {
                range: "prompt ".len().."prompt [Image #1]".len(),
                text: "[Image #1]".to_string(),
            }]
        );
        assert_eq!(recalled.local_image_paths, vec!["/tmp/image.png"]);
        assert_eq!(
            recalled.remote_image_urls,
            vec!["https://example.test/image.png"]
        );
        assert_eq!(recalled.mention_bindings, vec!["@tool"]);
        assert_eq!(
            recalled.pending_pastes,
            vec![(
                "[Pasted Content 12 chars]".to_string(),
                "full pasted text".to_string()
            )]
        );
    }
}
