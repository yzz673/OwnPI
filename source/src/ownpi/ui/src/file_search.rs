use std::collections::VecDeque;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::mpsc::{self, Receiver, Sender};
use std::thread;

use crate::bottom_pane::FileMatch;

const MAX_FILE_MATCHES: usize = 80;
const MAX_SCAN_DIRS: usize = 400;
const MAX_SCAN_DEPTH: usize = 6;

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct FileSearchResult {
    pub(crate) query: String,
    pub(crate) matches: Vec<FileMatch>,
}

#[derive(Clone, Debug)]
struct WorkerResult {
    token: usize,
    query: String,
    matches: Vec<FileMatch>,
}

pub(crate) struct FileSearchManager {
    root: Option<PathBuf>,
    latest_query: String,
    session_token: usize,
    tx: Sender<WorkerResult>,
    rx: Receiver<WorkerResult>,
}

impl std::fmt::Debug for FileSearchManager {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("FileSearchManager")
            .field("root", &self.root)
            .field("latest_query", &self.latest_query)
            .field("session_token", &self.session_token)
            .finish_non_exhaustive()
    }
}

impl Default for FileSearchManager {
    fn default() -> Self {
        let (tx, rx) = mpsc::channel();
        Self {
            root: None,
            latest_query: String::new(),
            session_token: 0,
            tx,
            rx,
        }
    }
}

impl FileSearchManager {
    pub(crate) fn set_root(&mut self, root: Option<String>) {
        self.root = root
            .filter(|value| !value.trim().is_empty())
            .map(PathBuf::from);
        self.latest_query.clear();
        self.session_token = self.session_token.wrapping_add(1);
    }

    pub(crate) fn on_user_query(&mut self, query: String) {
        if query == self.latest_query {
            return;
        }
        self.latest_query = query.clone();
        self.session_token = self.session_token.wrapping_add(1);
        if query.is_empty() {
            return;
        }
        let Some(root) = self.root.clone().filter(|path| path.is_dir()) else {
            return;
        };
        let tx = self.tx.clone();
        let token = self.session_token;
        thread::spawn(move || {
            let matches = scan_matches(&root, &query);
            let _ = tx.send(WorkerResult {
                token,
                query,
                matches,
            });
        });
    }

    pub(crate) fn drain_results(&mut self) -> Vec<FileSearchResult> {
        let mut accepted = Vec::new();
        while let Ok(result) = self.rx.try_recv() {
            if result.token != self.session_token
                || result.query != self.latest_query
                || self.latest_query.is_empty()
            {
                continue;
            }
            accepted.push(FileSearchResult {
                query: result.query,
                matches: result.matches,
            });
        }
        accepted
    }
}

fn scan_matches(root: &Path, query: &str) -> Vec<FileMatch> {
    let mut matches = Vec::new();
    let mut queue = VecDeque::from([(root.to_path_buf(), 0usize)]);
    let mut scanned_dirs = 0usize;

    while let Some((dir, depth)) = queue.pop_front() {
        if scanned_dirs >= MAX_SCAN_DIRS || matches.len() >= MAX_FILE_MATCHES {
            break;
        }
        scanned_dirs += 1;
        let Ok(entries) = fs::read_dir(&dir) else {
            continue;
        };
        let mut entries = entries.filter_map(Result::ok).collect::<Vec<_>>();
        entries.sort_by_key(|entry| entry.file_name());
        for entry in entries {
            let path = entry.path();
            let name = entry.file_name().to_string_lossy().to_string();
            if should_skip_entry(&name) {
                continue;
            }
            let is_dir = path.is_dir();
            let display_path = path
                .strip_prefix(root)
                .unwrap_or(path.as_path())
                .to_string_lossy()
                .to_string();
            if let Some(match_indices) = match_indices_for_query(&display_path, query) {
                matches.push(FileMatch {
                    path: display_path.clone(),
                    is_dir,
                    match_indices: Some(match_indices),
                });
                if matches.len() >= MAX_FILE_MATCHES {
                    break;
                }
            }
            if is_dir && depth < MAX_SCAN_DEPTH {
                queue.push_back((path, depth + 1));
            }
        }
    }
    matches
}

fn match_indices_for_query(text: &str, query: &str) -> Option<Vec<usize>> {
    let query_len = query.chars().count();
    if query_len == 0 {
        return None;
    }
    let char_count = text.chars().count();
    if query_len > char_count {
        return None;
    }
    let query_lower = query.to_lowercase();
    for start in 0..=char_count.saturating_sub(query_len) {
        let candidate = text.chars().skip(start).take(query_len).collect::<String>();
        if candidate.to_lowercase() == query_lower {
            return Some((start..start + query_len).collect());
        }
    }
    None
}

fn should_skip_entry(name: &str) -> bool {
    matches!(
        name,
        ".git" | "target" | "node_modules" | ".venv" | "__pycache__" | ".mypy_cache"
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_query_does_not_scan() {
        let root = temp_file_root();
        let mut manager = FileSearchManager::default();
        manager.set_root(Some(root.to_string_lossy().to_string()));

        manager.on_user_query(String::new());

        assert!(manager.drain_results().is_empty());
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn stale_result_is_rejected_by_latest_query() {
        let root = temp_file_root();
        let mut manager = FileSearchManager::default();
        manager.set_root(Some(root.to_string_lossy().to_string()));

        manager.on_user_query("main".to_string());
        manager.on_user_query("notes".to_string());
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(2);
        let mut accepted = Vec::new();
        while std::time::Instant::now() < deadline && accepted.is_empty() {
            accepted = manager.drain_results();
            std::thread::sleep(std::time::Duration::from_millis(10));
        }

        assert!(accepted.iter().all(|result| result.query == "notes"));
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn unicode_prefix_paths_report_character_match_indices() {
        let root = temp_file_root();
        std::fs::create_dir_all(root.join("数据")).unwrap();
        std::fs::write(root.join("数据").join("main.rs"), "fn main() {}\n").unwrap();

        let matches = scan_matches(&root, "main");
        let unicode_match = matches
            .iter()
            .find(|item| item.path == "数据/main.rs")
            .expect("unicode path should match query");

        assert_eq!(
            unicode_match.match_indices.as_deref(),
            Some(&[3, 4, 5, 6][..])
        );
        let _ = std::fs::remove_dir_all(root);
    }

    fn temp_file_root() -> std::path::PathBuf {
        let root = std::env::temp_dir().join(format!(
            "neuro-agent-file-search-test-{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(root.join("src")).unwrap();
        std::fs::write(root.join("src").join("main.rs"), "fn main() {}\n").unwrap();
        std::fs::write(root.join("notes.md"), "# notes\n").unwrap();
        root
    }
}
