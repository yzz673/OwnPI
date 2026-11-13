#[allow(dead_code)]
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) enum FileChange {
    Add {
        content: String,
    },
    Delete {
        content: String,
    },
    Update {
        unified_diff: String,
        move_path: Option<String>,
    },
    Move {
        unified_diff: String,
        move_path: String,
    },
    Directory,
    Noop,
    Pending {
        move_path: Option<String>,
    },
    #[default]
    Unknown,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct FileChangeEntry {
    pub(crate) path: String,
    pub(crate) change: FileChange,
    pub(crate) operation: String,
    pub(crate) patch_excerpt: String,
    pub(crate) added_lines: Option<usize>,
    pub(crate) removed_lines: Option<usize>,
    pub(crate) reason: String,
    pub(crate) body_complete: bool,
    pub(crate) body_omitted_bytes: usize,
    pub(crate) body_ref: String,
    pub(crate) overwrote_destination: bool,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct FileChanges(Vec<FileChangeEntry>);

#[cfg_attr(not(test), allow(dead_code))]
impl FileChanges {
    pub(crate) fn insert(&mut self, path: String, change: FileChange) {
        self.0.push(FileChangeEntry {
            operation: default_file_change_operation(&change).to_string(),
            path,
            change,
            body_complete: true,
            ..Default::default()
        });
    }

    pub(crate) fn push(&mut self, entry: FileChangeEntry) {
        self.0.push(entry);
    }

    pub(crate) fn get(&self, path: &str) -> Option<&FileChange> {
        self.0
            .iter()
            .rev()
            .find(|entry| entry.path == path)
            .map(|entry| &entry.change)
    }

    pub(crate) fn entries(&self) -> impl Iterator<Item = &FileChangeEntry> {
        self.0.iter()
    }

    pub(crate) fn is_empty(&self) -> bool {
        self.0.is_empty()
    }

    pub(crate) fn len(&self) -> usize {
        self.0.len()
    }
}

#[cfg_attr(not(test), allow(dead_code))]
fn default_file_change_operation(change: &FileChange) -> &'static str {
    match change {
        FileChange::Add { .. } => "add",
        FileChange::Delete { .. } => "delete",
        FileChange::Update { .. } => "update",
        FileChange::Move { .. } => "move",
        FileChange::Directory => "create_directory",
        FileChange::Noop => "noop",
        FileChange::Pending { .. } => "pending",
        FileChange::Unknown => "unknown",
    }
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct FileChangeCell {
    pub(crate) event_id: String,
    pub(crate) tool_name: String,
    pub(crate) status: String,
    pub(crate) cwd: String,
    pub(crate) changes: FileChanges,
    pub(crate) stdout: Option<String>,
    pub(crate) stderr: Option<String>,
    pub(crate) error: Option<String>,
    pub(crate) auto_approved: Option<bool>,
}
