use std::path::{Path, PathBuf};

use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};

use crate::file_change::{FileChange, FileChangeCell};
use crate::theme::ThemePalette;

const RAW_DIFF_SUMMARY_WIDTH: usize = 10_000;
const DIFF_PREVIEW_MAX_BODY_LINES: usize = 13;

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct PatchHistoryCell {
    cell: FileChangeCell,
    compact_preview: bool,
}

impl PatchHistoryCell {
    pub(crate) fn item_id(&self) -> &str {
        &self.cell.event_id
    }

    pub(crate) fn new(cell: FileChangeCell) -> Self {
        Self {
            cell,
            compact_preview: false,
        }
    }

    pub(crate) fn with_compact_preview(mut self, compact: bool) -> Self {
        self.compact_preview = compact;
        self
    }

    pub(crate) fn display_lines(&self, width: usize, theme: &ThemePalette) -> Vec<Line<'static>> {
        // Only compact successful, fully known non-destructive multi-file edits.
        // Direct sessions keep their existing diff preview; details remain exact.
        if self.compact_preview && self.cell.status == "completed" {
            let rows = collect_rows(&self.cell);
            if rows.len() > 1
                && rows.iter().all(|row| {
                    matches!(
                        row.operation.as_str(),
                        "add" | "update" | "noop" | "create_directory"
                    ) && row.body_complete
                        && !row.overwrote_destination
                })
            {
                let width = width.clamp(1, 120);
                let mut lines = wrap_diff_header(
                    render_summary_header(&rows, &self.cell.status, theme),
                    width,
                );
                for (index, row) in rows.iter().enumerate() {
                    let mut header = render_child_header(row, index + 1 == rows.len(), theme);
                    header.spans.insert(
                        1,
                        Span::styled(
                            format!("{} ", single_row_verb(row)),
                            Style::default().fg(theme.text_muted),
                        ),
                    );
                    lines.extend(wrap_diff_header(header, width));
                }
                lines.extend(wrap_diff_header(
                    Line::from(Span::styled(
                        "  Diff details: ctrl + t",
                        Style::default().fg(theme.text_muted),
                    )),
                    width,
                ));
                return lines;
            }
        }
        render_file_change_lines(&self.cell, width.max(1), theme, true)
    }

    pub(crate) fn detail_lines(&self, width: usize, theme: &ThemePalette) -> Vec<Line<'static>> {
        render_file_change_lines(&self.cell, width.max(1), theme, false)
    }

    pub(crate) fn raw_lines(&self) -> Vec<Line<'static>> {
        raw_file_change_lines(&self.cell, RAW_DIFF_SUMMARY_WIDTH)
            .into_iter()
            .map(Line::from)
            .collect()
    }

    pub(crate) fn transcript_lines(&self) -> Vec<Line<'static>> {
        self.raw_lines()
    }

    pub(crate) fn raw_text(&self) -> String {
        self.transcript_lines()
            .into_iter()
            .map(|line| {
                line.spans
                    .into_iter()
                    .map(|span| span.content.into_owned())
                    .collect::<String>()
            })
            .collect::<Vec<_>>()
            .join("\n")
    }
}

#[derive(Clone)]
struct FileChangeRow {
    source_path: String,
    display_path: String,
    display_move_path: Option<String>,
    added: usize,
    removed: usize,
    change: FileChange,
    operation: String,
    patch_excerpt: String,
    body_complete: bool,
    body_omitted_bytes: usize,
    body_ref: String,
    overwrote_destination: bool,
}

#[cfg(test)]
pub(crate) fn render_file_change(
    cell: &FileChangeCell,
    width: usize,
    theme: &ThemePalette,
) -> Vec<Line<'static>> {
    render_file_change_lines(cell, width.max(1), theme, true)
}

#[cfg(test)]
pub(crate) fn render_file_change_raw_text(cell: &FileChangeCell) -> String {
    raw_file_change_lines(cell, RAW_DIFF_SUMMARY_WIDTH).join("\n")
}

fn render_file_change_lines(
    cell: &FileChangeCell,
    width: usize,
    theme: &ThemePalette,
    preview: bool,
) -> Vec<Line<'static>> {
    let width = width.min(120);
    let rows = collect_rows(cell);
    if file_change_failed(&cell.status) {
        return failure_lines(cell, theme)
            .into_iter()
            .flat_map(|line| wrap_diff_header(line, width))
            .collect();
    }
    if rows.is_empty() {
        return completed_without_changes_lines(cell, theme);
    }
    let mut lines = Vec::new();
    lines.extend(wrap_diff_header(
        render_summary_header(&rows, &cell.status, theme),
        width,
    ));
    let file_count = rows.len();
    let mut previous_had_body = false;
    for (index, row) in rows.iter().enumerate() {
        let body = if preview {
            render_change_body(row, width, theme)
        } else {
            render_change_body_mode(row, width, theme, false)
        };
        if index > 0 && (previous_had_body || !body.is_empty()) {
            lines.push(Line::from(Span::styled(
                "  │",
                Style::default().fg(theme.text_muted),
            )));
        }
        let continues = file_count > 1 && index + 1 < file_count;
        if file_count > 1 {
            lines.extend(
                wrap_diff_header(
                    render_child_header(row, index + 1 == file_count, theme),
                    width,
                )
                .into_iter()
                .enumerate()
                .map(|(row, line)| {
                    if row == 0 {
                        line
                    } else {
                        diff_display_row(line, continues, width, theme)
                    }
                }),
            );
        }
        previous_had_body = !body.is_empty();
        lines.extend(
            body.into_iter()
                .map(|line| diff_display_row(line, continues, width, theme)),
        );
    }
    lines
}

// Colored diff rows own a bounded preview, not the terminal's entire row.
// The tree gutter stays neutral and source/copy records retain their originals.
fn diff_display_row(
    mut line: Line<'static>,
    continues: bool,
    width: usize,
    theme: &ThemePalette,
) -> Line<'static> {
    let background = line.style.bg.take();
    if let Some(first) = line.spans.first_mut() {
        if let Some(rest) = first.content.strip_prefix("    ") {
            first.content = rest.to_string().into();
            line.spans.insert(
                0,
                Span::styled(
                    if continues { "  │ " } else { "    " },
                    Style::default().fg(theme.text_muted),
                ),
            );
        }
    }
    if let Some(background) = background {
        for span in line.spans.iter_mut().skip(1) {
            span.style.bg = Some(background);
        }
        let remaining = width.saturating_sub(line.width());
        if remaining > 0 {
            line.spans.push(Span::styled(
                " ".repeat(remaining),
                Style::default().bg(background),
            ));
        }
    }
    line
}

fn raw_file_change_lines(cell: &FileChangeCell, _width: usize) -> Vec<String> {
    let rows = collect_rows(cell);
    if rows.is_empty() {
        return if file_change_failed(&cell.status) {
            raw_failure_lines(cell)
        } else {
            raw_completed_without_changes_lines(cell)
        };
    }
    let mut lines = Vec::new();
    lines.push(raw_summary_header(&rows, &cell.status));
    let file_count = rows.len();
    let mut previous_had_body = false;
    for (index, row) in rows.iter().enumerate() {
        let body = raw_change_body(row);
        if index > 0 && (previous_had_body || !body.is_empty()) {
            lines.push(String::new());
        }
        if file_count > 1 {
            lines.push(format!(
                "  {} {}{}",
                if index + 1 == file_count {
                    "└"
                } else {
                    "├"
                },
                path_with_move(row),
                format!(
                    "{}{}",
                    raw_count_suffix(row),
                    if row.operation == "overwrite" {
                        if row.body_complete {
                            " · overwritten"
                        } else {
                            " · overwritten; previous content unavailable"
                        }
                    } else {
                        ""
                    }
                )
            ));
        }
        previous_had_body = !body.is_empty();
        lines.extend(body);
    }
    if file_change_failed(&cell.status) {
        lines.push(String::new());
        lines.extend(raw_failure_lines(cell));
    }
    lines
}

fn collect_rows(cell: &FileChangeCell) -> Vec<FileChangeRow> {
    let cwd = PathBuf::from(cell.cwd.trim());
    let mut rows = cell
        .changes
        .entries()
        .map(|entry| {
            let change = &entry.change;
            let (calculated_added, calculated_removed) = line_counts(change);
            let added = entry.added_lines.unwrap_or(calculated_added);
            let removed = entry.removed_lines.unwrap_or(calculated_removed);
            let move_path = match change {
                FileChange::Update {
                    move_path: Some(value),
                    ..
                } => Some(display_path_for(value, &cwd)),
                FileChange::Move { move_path, .. } => Some(display_path_for(move_path, &cwd)),
                FileChange::Pending {
                    move_path: Some(value),
                } => Some(display_path_for(value, &cwd)),
                _ => None,
            };
            FileChangeRow {
                source_path: entry.path.clone(),
                display_path: display_path_for(&entry.path, &cwd),
                display_move_path: move_path,
                added,
                removed,
                change: change.clone(),
                operation: entry.operation.clone(),
                patch_excerpt: entry.patch_excerpt.clone(),
                body_complete: entry.body_complete,
                body_omitted_bytes: entry.body_omitted_bytes,
                body_ref: entry.body_ref.clone(),
                overwrote_destination: entry.overwrote_destination,
            }
        })
        .collect::<Vec<_>>();
    rows.sort_by(|left, right| left.display_path.cmp(&right.display_path));
    let mut normalized = Vec::new();
    let mut remaining = rows.as_slice();
    while let Some((first, rest)) = remaining.split_first() {
        let count = 1 + rest
            .iter()
            .take_while(|row| row.source_path == first.source_path)
            .count();
        if !file_change_failed(&cell.status) && count == 2 {
            if let Some(replacement) = replacement_row(first, &rest[0]) {
                normalized.push(replacement);
                remaining = &remaining[count..];
                continue;
            }
        }
        normalized.extend_from_slice(&remaining[..count]);
        remaining = &remaining[count..];
    }
    normalized
}

// Normalize presentation only; never discard incomplete bodies or operation receipts.
fn replacement_row(before: &FileChangeRow, after: &FileChangeRow) -> Option<FileChangeRow> {
    if !before.patch_excerpt.is_empty()
        || !after.patch_excerpt.is_empty()
        || before.display_move_path.is_some()
        || after.display_move_path.is_some()
        || !before.body_ref.is_empty()
        || !after.body_ref.is_empty()
        || before.body_omitted_bytes > 0
        || after.body_omitted_bytes > 0
    {
        return None;
    }
    let change = match (&before.change, &after.change) {
        (FileChange::Delete { content: old }, FileChange::Add { content: new })
            if before.body_complete && after.body_complete =>
        {
            FileChange::Update {
                unified_diff: diffy::create_patch(old, new).to_string(),
                move_path: None,
            }
        }
        (FileChange::Unknown, FileChange::Unknown)
            if matches!(before.operation.as_str(), "delete" | "remove")
                && matches!(after.operation.as_str(), "add" | "create")
                && before.added + before.removed + after.added + after.removed == 0 =>
        {
            FileChange::Unknown
        }
        _ => return None,
    };
    let (added, removed) = line_counts(&change);
    Some(FileChangeRow {
        change,
        added,
        removed,
        operation: "update".to_string(),
        ..before.clone()
    })
}

fn line_counts(change: &FileChange) -> (usize, usize) {
    match change {
        FileChange::Add { content } => (content.lines().count(), 0),
        FileChange::Delete { content } => (0, content.lines().count()),
        FileChange::Update { unified_diff, .. } | FileChange::Move { unified_diff, .. } => {
            calculate_add_remove_from_diff(unified_diff)
        }
        FileChange::Directory
        | FileChange::Noop
        | FileChange::Pending { .. }
        | FileChange::Unknown => (0, 0),
    }
}

fn calculate_add_remove_from_diff(diff: &str) -> (usize, usize) {
    let Ok(patch) = diffy::Patch::from_str(diff) else {
        return (0, 0);
    };
    patch
        .hunks()
        .iter()
        .flat_map(diffy::Hunk::lines)
        .fold((0, 0), |(added, removed), line| match line {
            diffy::Line::Insert(_) => (added + 1, removed),
            diffy::Line::Delete(_) => (added, removed + 1),
            diffy::Line::Context(_) => (added, removed),
        })
}

fn render_summary_header(
    rows: &[FileChangeRow],
    status: &str,
    theme: &ThemePalette,
) -> Line<'static> {
    let mut spans = vec![Span::styled(
        crate::style::activity_marker(status),
        crate::style::activity_marker_style(status, theme),
    )];
    if rows.len() == 1 {
        let row = &rows[0];
        spans.push(Span::styled(
            single_row_verb(row).to_string(),
            Style::default().fg(theme.text).add_modifier(Modifier::BOLD),
        ));
        spans.push(Span::raw(" "));
        spans.push(Span::styled(
            path_with_move(row),
            Style::default().fg(theme.text),
        ));
        if row_has_counts(row) {
            spans.push(Span::raw(" "));
            spans.extend(render_count_summary(row.added, row.removed, theme));
        }
        if row.overwrote_destination {
            spans.push(Span::styled(
                "  · replaced destination",
                Style::default().fg(theme.text_muted),
            ));
        }
        if row.operation == "overwrite" && matches!(row.change, FileChange::Unknown) {
            spans.push(Span::styled(
                "  · previous content unavailable",
                Style::default().fg(theme.text_muted),
            ));
        }
        if matches!(row.change, FileChange::Unknown) && row.operation == "file_write" {
            spans.push(Span::styled(
                "  · change details unavailable",
                Style::default().fg(theme.text_muted),
            ));
        }
        return Line::from(spans);
    }
    let added = rows.iter().map(|row| row.added).sum();
    let removed = rows.iter().map(|row| row.removed).sum();
    let all_edits = rows
        .iter()
        .all(|row| matches!(row.change, FileChange::Update { .. } | FileChange::Unknown));
    spans.push(Span::styled(
        if all_edits { "Edited" } else { "Changed" }.to_string(),
        Style::default().fg(theme.text).add_modifier(Modifier::BOLD),
    ));
    spans.push(Span::raw(format!(
        " {} {}",
        rows.len(),
        if all_edits { "files" } else { "paths" }
    )));
    if (added > 0 || removed > 0)
        && !rows
            .iter()
            .any(|row| row.operation == "overwrite" && !row.body_complete)
    {
        spans.push(Span::raw(" "));
        spans.extend(render_count_summary(added, removed, theme));
    }
    Line::from(spans)
}

fn render_child_header(row: &FileChangeRow, last: bool, theme: &ThemePalette) -> Line<'static> {
    let mut spans = vec![Span::styled(
        if last { "  └ " } else { "  ├ " },
        Style::default().fg(theme.text_muted),
    )];
    spans.push(Span::styled(
        path_with_move(row),
        Style::default().fg(theme.text),
    ));
    if row_has_counts(row) {
        spans.push(Span::raw(" "));
        spans.extend(render_count_summary(row.added, row.removed, theme));
    }
    if row.operation == "overwrite" {
        spans.push(Span::styled(
            if row.body_complete {
                " · overwritten"
            } else {
                " · overwritten; previous content unavailable"
            },
            Style::default().fg(theme.text_muted),
        ));
    }
    Line::from(spans)
}

fn render_count_summary(added: usize, removed: usize, theme: &ThemePalette) -> Vec<Span<'static>> {
    vec![
        Span::raw("("),
        Span::styled(format!("+{added}"), Style::default().fg(theme.success)),
        Span::raw(" "),
        Span::styled(format!("-{removed}"), Style::default().fg(theme.error)),
        Span::raw(")"),
    ]
}

fn render_change_body(
    row: &FileChangeRow,
    width: usize,
    theme: &ThemePalette,
) -> Vec<Line<'static>> {
    render_change_body_mode(row, width, theme, true)
}

fn render_change_body_mode(
    row: &FileChangeRow,
    width: usize,
    theme: &ThemePalette,
    preview: bool,
) -> Vec<Line<'static>> {
    if !row.body_complete && row.body_omitted_bytes > 0 {
        return vec![Line::from(Span::styled(
            body_omitted_message(row),
            Style::default().fg(theme.text_muted),
        ))];
    }
    let body = diff_body_lines(row, width);
    if !preview || body.len() <= DIFF_PREVIEW_MAX_BODY_LINES {
        return body
            .iter()
            .map(|line| render_diff_body_line(line, theme))
            .collect();
    }
    let budget = (DIFF_PREVIEW_MAX_BODY_LINES - 1) / 2;
    let mut head_end = budget;
    while head_end > 0 && body[head_end].continuation {
        head_end -= 1;
    }
    if head_end == 0 {
        head_end = budget;
    }
    let mut tail_start = body.len() - budget;
    while tail_start < body.len() && body[tail_start].continuation {
        tail_start += 1;
    }
    if tail_start == body.len() {
        tail_start = body.len() - budget;
        while tail_start > head_end && body[tail_start].continuation {
            tail_start -= 1;
        }
    }
    let tail_end = (tail_start + budget).min(body.len());
    let omitted = body[head_end..tail_start]
        .iter()
        .filter(|line| !line.continuation && line.kind != DiffLineKind::HunkSeparator)
        .count();
    let partial = body[head_end].continuation || tail_end < body.len();
    let mut lines = body[..head_end]
        .iter()
        .map(|line| render_diff_body_line(line, theme))
        .collect::<Vec<_>>();
    let detail = if width < 56 {
        "ctrl + t"
    } else {
        "ctrl + t to view transcript"
    };
    let marker = format!(
        "    … +{omitted} lines{} ({detail})",
        if partial { ", more" } else { "" }
    );
    let marker = if unicode_width::UnicodeWidthStr::width(marker.as_str()) > width {
        format!("… +{omitted} (ctrl + t)")
    } else {
        marker
    };
    lines.push(Line::from(Span::styled(
        marker,
        Style::default().fg(theme.text_muted),
    )));
    lines.extend(
        body[tail_start..tail_end]
            .iter()
            .map(|line| render_diff_body_line(line, theme)),
    );
    lines
}

fn raw_change_body(row: &FileChangeRow) -> Vec<String> {
    if !row.body_complete && row.body_omitted_bytes > 0 {
        return vec![body_omitted_message(row)];
    }
    diff_body_lines(row, RAW_DIFF_SUMMARY_WIDTH)
        .into_iter()
        .map(|line| line.raw_text())
        .collect()
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum DiffLineKind {
    Insert,
    Delete,
    Context,
    HunkSeparator,
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct DiffBodyLine {
    number: Option<usize>,
    number_width: usize,
    kind: DiffLineKind,
    text: String,
    continuation: bool,
}

impl DiffBodyLine {
    fn raw_text(&self) -> String {
        if self.continuation {
            return format!(
                "    {:>width$}  {}",
                "",
                self.text,
                width = self.number_width.max(1)
            );
        }
        if self.kind == DiffLineKind::HunkSeparator {
            return format!(
                "    {:>width$}  {}",
                "",
                if self.text.is_empty() {
                    "⋮"
                } else {
                    &self.text
                },
                width = self.number_width.max(1)
            );
        }
        let sign = match self.kind {
            DiffLineKind::Insert => '+',
            DiffLineKind::Delete => '-',
            DiffLineKind::Context => ' ',
            DiffLineKind::HunkSeparator => ' ',
        };
        format!(
            "    {:>width$} {sign}{}",
            self.number
                .map(|number| number.to_string())
                .unwrap_or_default(),
            self.text,
            width = self.number_width.max(1)
        )
    }
}

fn render_diff_body_line(line: &DiffBodyLine, theme: &ThemePalette) -> Line<'static> {
    if line.kind == DiffLineKind::HunkSeparator {
        return Line::from(vec![
            Span::styled(
                format!("    {:>width$}  ", "", width = line.number_width.max(1)),
                Style::default().fg(theme.text_muted),
            ),
            Span::styled(
                if line.text.is_empty() {
                    "⋮".to_string()
                } else {
                    line.text.clone()
                },
                Style::default().fg(theme.text_muted),
            ),
        ]);
    }
    let gutter_style = Style::default().fg(theme.text_muted);
    let (sign, sign_style, text_style) = match line.kind {
        DiffLineKind::Insert => (
            "+",
            Style::default()
                .fg(theme.success)
                .add_modifier(Modifier::BOLD),
            Style::default().fg(theme.success),
        ),
        DiffLineKind::Delete => (
            "-",
            Style::default()
                .fg(theme.error)
                .add_modifier(Modifier::BOLD),
            Style::default().fg(theme.error),
        ),
        DiffLineKind::Context => (
            " ",
            Style::default().fg(theme.text_muted),
            Style::default().fg(theme.text),
        ),
        DiffLineKind::HunkSeparator => unreachable!(),
    };
    Line::from(vec![
        Span::styled(
            if line.continuation {
                format!("    {:>width$}  ", "", width = line.number_width.max(1))
            } else {
                format!(
                    "    {:>width$} ",
                    line.number
                        .map(|number| number.to_string())
                        .unwrap_or_default(),
                    width = line.number_width.max(1)
                )
            },
            gutter_style,
        ),
        Span::styled(
            if line.continuation {
                String::new()
            } else {
                sign.to_string()
            },
            sign_style,
        ),
        Span::styled(line.text.clone(), text_style),
    ])
    .style(diff_line_background(line.kind, theme))
}

fn diff_line_background(kind: DiffLineKind, theme: &ThemePalette) -> Style {
    let background = match kind {
        DiffLineKind::Insert => Some(crate::style::diff_added_bg(theme)),
        DiffLineKind::Delete => Some(crate::style::diff_removed_bg(theme)),
        _ => None,
    };
    background.map_or_else(Style::default, |bg| Style::default().bg(bg))
}

fn diff_body_lines(row: &FileChangeRow, width: usize) -> Vec<DiffBodyLine> {
    if !row.patch_excerpt.is_empty() {
        return excerpt_body_lines(&row.patch_excerpt, width);
    }
    match &row.change {
        FileChange::Add { content } => {
            let number_width = line_number_width(content.lines().count());
            content
                .lines()
                .enumerate()
                .flat_map(|(index, line)| {
                    wrap_diff_body_text(line, width, number_width)
                        .into_iter()
                        .enumerate()
                        .map(move |(wrapped_index, text)| DiffBodyLine {
                            number: Some(index + 1),
                            number_width,
                            kind: DiffLineKind::Insert,
                            text,
                            continuation: wrapped_index > 0,
                        })
                })
                .collect()
        }
        FileChange::Delete { content } => {
            let number_width = line_number_width(content.lines().count());
            content
                .lines()
                .enumerate()
                .flat_map(|(index, line)| {
                    wrap_diff_body_text(line, width, number_width)
                        .into_iter()
                        .enumerate()
                        .map(move |(wrapped_index, text)| DiffBodyLine {
                            number: Some(index + 1),
                            number_width,
                            kind: DiffLineKind::Delete,
                            text,
                            continuation: wrapped_index > 0,
                        })
                })
                .collect()
        }
        FileChange::Update { unified_diff, .. } | FileChange::Move { unified_diff, .. } => {
            update_diff_body_lines(unified_diff, width)
        }
        FileChange::Directory
        | FileChange::Noop
        | FileChange::Pending { .. }
        | FileChange::Unknown => Vec::new(),
    }
}

fn body_omitted_message(row: &FileChangeRow) -> String {
    let reference = if row.body_ref.trim().is_empty() {
        String::new()
    } else {
        format!(" · {}", row.body_ref)
    };
    format!(
        "    … body omitted ({} bytes){reference}",
        row.body_omitted_bytes
    )
}

fn update_diff_body_lines(unified_diff: &str, width: usize) -> Vec<DiffBodyLine> {
    let Ok(patch) = diffy::Patch::from_str(unified_diff) else {
        return Vec::new();
    };
    let mut max_line_number = 0usize;
    for hunk in patch.hunks() {
        let mut old_ln = hunk.old_range().start();
        let mut new_ln = hunk.new_range().start();
        for line in hunk.lines() {
            match line {
                diffy::Line::Insert(_) => {
                    max_line_number = max_line_number.max(new_ln);
                    new_ln += 1;
                }
                diffy::Line::Delete(_) => {
                    max_line_number = max_line_number.max(old_ln);
                    old_ln += 1;
                }
                diffy::Line::Context(_) => {
                    max_line_number = max_line_number.max(new_ln);
                    old_ln += 1;
                    new_ln += 1;
                }
            }
        }
    }
    let number_width = line_number_width(max_line_number);
    let mut out = Vec::new();
    for (hunk_index, hunk) in patch.hunks().iter().enumerate() {
        if hunk_index > 0 {
            out.push(DiffBodyLine {
                number: None,
                number_width,
                kind: DiffLineKind::HunkSeparator,
                text: String::new(),
                continuation: false,
            });
        }
        let mut old_ln = hunk.old_range().start();
        let mut new_ln = hunk.new_range().start();
        for line in hunk.lines() {
            match line {
                diffy::Line::Insert(text) => {
                    let raw = text.trim_end_matches('\n');
                    out.extend(
                        wrap_diff_body_text(raw, width, number_width)
                            .into_iter()
                            .enumerate()
                            .map(|(wrapped_index, text)| DiffBodyLine {
                                number: Some(new_ln),
                                number_width,
                                kind: DiffLineKind::Insert,
                                text,
                                continuation: wrapped_index > 0,
                            }),
                    );
                    new_ln += 1;
                }
                diffy::Line::Delete(text) => {
                    let raw = text.trim_end_matches('\n');
                    out.extend(
                        wrap_diff_body_text(raw, width, number_width)
                            .into_iter()
                            .enumerate()
                            .map(|(wrapped_index, text)| DiffBodyLine {
                                number: Some(old_ln),
                                number_width,
                                kind: DiffLineKind::Delete,
                                text,
                                continuation: wrapped_index > 0,
                            }),
                    );
                    old_ln += 1;
                }
                diffy::Line::Context(text) => {
                    let raw = text.trim_end_matches('\n');
                    out.extend(
                        wrap_diff_body_text(raw, width, number_width)
                            .into_iter()
                            .enumerate()
                            .map(|(wrapped_index, text)| DiffBodyLine {
                                number: Some(new_ln),
                                number_width,
                                kind: DiffLineKind::Context,
                                text,
                                continuation: wrapped_index > 0,
                            }),
                    );
                    old_ln += 1;
                    new_ln += 1;
                }
            }
        }
    }
    out
}

fn wrap_diff_body_text(text: &str, width: usize, number_width: usize) -> Vec<String> {
    let prefix_width = 4 + number_width.max(1) + 2;
    let content_width = width.saturating_sub(prefix_width).max(1);
    let text = expand_diff_tabs(text);
    let wrapped =
        crate::wrapping::word_wrap_text(&text, crate::wrapping::RtOptions::new(content_width));
    if wrapped.is_empty() {
        return vec![String::new()];
    }
    wrapped
}

// Diff content is source text, so URLs and tabs obey the same column budget as
// code and Chinese prose. Expansion affects display only, never the raw records.
fn expand_diff_tabs(text: &str) -> String {
    use unicode_segmentation::UnicodeSegmentation;
    use unicode_width::UnicodeWidthStr;
    let mut column = 0;
    let mut expanded = String::new();
    for grapheme in text.graphemes(true) {
        if grapheme == "\t" {
            let count = 8 - column % 8;
            expanded.push_str(&" ".repeat(count));
            column += count;
        } else {
            expanded.push_str(grapheme);
            column += UnicodeWidthStr::width(grapheme);
        }
    }
    expanded
}

fn wrap_diff_header(line: Line<'static>, width: usize) -> Vec<Line<'static>> {
    let wrap = |line: &Line<'static>| {
        crate::wrapping::word_wrap_line(
            line,
            crate::wrapping::RtOptions::new(width).subsequent_indent(Line::from("    ")),
        )
        .iter()
        .map(crate::wrapping::line_to_static)
        .collect::<Vec<_>>()
    };
    if let Some(start) = line.spans.iter().position(|span| span.content == "(") {
        if let Some(end) = line
            .spans
            .iter()
            .enumerate()
            .skip(start)
            .find_map(|(i, span)| (span.content == ")").then_some(i))
        {
            let count = Line::from(line.spans[start..=end].to_vec());
            if count.width() + 4 <= width {
                let mut prefix = Line::from(line.spans[..start].to_vec()).style(line.style);
                if let Some(last) = prefix.spans.last_mut() {
                    last.content = last.content.trim_end_matches(' ').to_string().into();
                }
                let mut lines = wrap(&prefix);
                let last = lines.last_mut().unwrap();
                if last.width() + 1 + count.width() <= width {
                    last.spans.push(Span::raw(" "));
                    last.spans.extend(count.spans);
                } else {
                    let mut spans = vec![Span::raw("    ")];
                    spans.extend(count.spans);
                    lines.push(Line::from(spans));
                }
                if end + 1 < line.spans.len() {
                    lines.extend(wrap(&Line::from(line.spans[end + 1..].to_vec())));
                }
                return lines;
            }
        }
    }
    wrap(&line)
}

// patch_apply uses context anchors rather than unified-diff line ranges. Show
// the signed source lines without guessing their positions in today's files.
fn excerpt_body_lines(excerpt: &str, width: usize) -> Vec<DiffBodyLine> {
    let mut out = Vec::new();
    for source in excerpt.lines() {
        let (kind, text) = if source.starts_with("@@") {
            (DiffLineKind::HunkSeparator, source)
        } else if let Some(text) = source.strip_prefix('+') {
            (DiffLineKind::Insert, text)
        } else if let Some(text) = source.strip_prefix('-') {
            (DiffLineKind::Delete, text)
        } else if let Some(text) = source.strip_prefix(' ') {
            (DiffLineKind::Context, text)
        } else {
            continue;
        };
        for (index, text) in wrap_diff_body_text(text, width, 1).into_iter().enumerate() {
            out.push(DiffBodyLine {
                number: None,
                number_width: 1,
                kind,
                text,
                continuation: index > 0,
            });
        }
    }
    out
}

fn line_number_width(max_line_number: usize) -> usize {
    max_line_number.to_string().len().max(1)
}

fn failure_lines(cell: &FileChangeCell, theme: &ThemePalette) -> Vec<Line<'static>> {
    let mut lines = vec![Line::from(Span::styled(
        format!(
            "{}{}",
            crate::style::GLYPH_FAILURE_PREFIX,
            failure_title(cell)
        ),
        Style::default()
            .fg(theme.error)
            .add_modifier(Modifier::BOLD),
    ))];
    if patch_context_mismatch(cell) {
        lines.push(Line::from(vec![
            Span::styled(
                crate::style::GLYPH_DETAIL_FIRST,
                Style::default().fg(theme.text_muted),
            ),
            Span::styled(
                "No changes applied; re-read the file before retrying".to_string(),
                Style::default().fg(theme.text_muted),
            ),
        ]));
        return lines;
    }
    for line in failure_text(cell).lines().take(8) {
        if line.trim().is_empty() {
            continue;
        }
        lines.push(Line::from(vec![
            Span::styled(
                crate::style::GLYPH_DETAIL_FIRST,
                Style::default().fg(theme.text_muted),
            ),
            Span::styled(line.to_string(), Style::default().fg(theme.text_muted)),
        ]));
    }
    lines
}

fn raw_failure_lines(cell: &FileChangeCell) -> Vec<String> {
    let mut lines = vec![format!(
        "{}{}",
        crate::style::GLYPH_FAILURE_PREFIX,
        failure_title(cell)
    )];
    for line in failure_text(cell).lines() {
        if line.trim().is_empty() {
            continue;
        }
        lines.push(format!("{}{line}", crate::style::GLYPH_DETAIL_FIRST));
    }
    lines
}

fn failure_text(cell: &FileChangeCell) -> String {
    cell.error
        .as_deref()
        .or(cell.stderr.as_deref())
        .or(cell.stdout.as_deref())
        .unwrap_or_default()
        .trim()
        .to_string()
}

fn raw_summary_header(rows: &[FileChangeRow], status: &str) -> String {
    if rows.len() == 1 {
        let row = &rows[0];
        return format!(
            "{}{} {}{}{}",
            crate::style::activity_marker(status),
            single_row_verb(row),
            path_with_move(row),
            raw_count_suffix(row),
            if matches!(row.change, FileChange::Unknown) && row.operation == "overwrite" {
                " · previous content unavailable"
            } else if matches!(row.change, FileChange::Unknown) && row.operation == "file_write" {
                " · change details unavailable"
            } else {
                ""
            }
        );
    }
    let added = rows.iter().map(|row| row.added).sum();
    let removed = rows.iter().map(|row| row.removed).sum();
    let all_edits = rows
        .iter()
        .all(|row| matches!(row.change, FileChange::Update { .. } | FileChange::Unknown));
    let counts = if (added > 0 || removed > 0)
        && !rows
            .iter()
            .any(|row| row.operation == "overwrite" && !row.body_complete)
    {
        format!(" {}", raw_count_summary(added, removed))
    } else {
        String::new()
    };
    format!(
        "{}{} {} {}{}",
        crate::style::activity_marker(status),
        if all_edits { "Edited" } else { "Changed" },
        rows.len(),
        if all_edits { "files" } else { "paths" },
        counts
    )
}

fn file_change_failed(status: &str) -> bool {
    matches!(status.trim(), "failed" | "error")
}

fn raw_count_summary(added: usize, removed: usize) -> String {
    format!("(+{added} -{removed})")
}

fn single_row_verb(row: &FileChangeRow) -> &'static str {
    if row.operation == "overwrite" {
        return "Overwrote";
    }
    match &row.change {
        FileChange::Add { .. } => "Added",
        FileChange::Delete { .. } => "Deleted",
        FileChange::Update { .. } => "Edited",
        FileChange::Move { .. } if row.operation == "rename" => "Renamed",
        FileChange::Move { .. } => "Moved",
        FileChange::Directory => "Created directory",
        FileChange::Noop => "No changes ·",
        FileChange::Pending { .. } if row.operation == "rename" => "Renaming",
        FileChange::Pending { .. } if row.operation == "move" => "Moving",
        FileChange::Pending { .. } if row.operation == "create_directory" => "Creating",
        FileChange::Pending { .. } => "Editing",
        FileChange::Unknown => match row.operation.as_str() {
            "add" | "create" => "Added",
            "file_write" => "Wrote",
            "delete" | "remove" => "Deleted",
            "move" => "Moved",
            "rename" => "Renamed",
            _ => "Edited",
        },
    }
}

fn row_has_counts(row: &FileChangeRow) -> bool {
    row.added > 0 || row.removed > 0
}

fn raw_count_suffix(row: &FileChangeRow) -> String {
    if row_has_counts(row) {
        format!(" {}", raw_count_summary(row.added, row.removed))
    } else {
        String::new()
    }
}

fn failure_title(cell: &FileChangeCell) -> String {
    if patch_context_mismatch(cell) {
        let path = patch_context_path(cell);
        return if path.is_empty() {
            "Patch context changed".to_string()
        } else {
            format!("Patch context changed · {path}")
        };
    }
    match cell.tool_name.as_str() {
        "mkdir" => "Failed to create directory",
        "file_write" => "Failed to write file",
        "file_move" => "Failed to move file",
        "file_rename" => "Failed to rename file",
        "file_edit" => "Failed to edit file",
        "multi_file_edit" => "Failed to edit files",
        "batch_apply" => "Failed to apply batch",
        "notebook_edit" => "Failed to edit notebook",
        "write_task_spec" => "Failed to write task specification",
        "write_stage_semantics" => "Failed to write stage semantics",
        _ => "Failed to apply patch",
    }
    .to_string()
}

fn patch_context_mismatch(cell: &FileChangeCell) -> bool {
    cell.tool_name == "patch_apply"
        && failure_text(cell)
            .to_ascii_lowercase()
            .contains("failed to find expected lines in ")
}

fn patch_context_path(cell: &FileChangeCell) -> String {
    let text = failure_text(cell);
    let Some(first) = text.lines().next() else {
        return String::new();
    };
    let Some((_, path)) = first.split_once("Failed to find expected lines in ") else {
        return String::new();
    };
    display_path_for(path.trim_end_matches(':').trim(), Path::new(&cell.cwd))
}

fn completed_without_changes_lines(
    cell: &FileChangeCell,
    theme: &ThemePalette,
) -> Vec<Line<'static>> {
    vec![Line::from(Span::styled(
        raw_completed_without_changes_lines(cell)
            .into_iter()
            .next()
            .unwrap_or_default(),
        Style::default().fg(theme.text),
    ))]
}

fn raw_completed_without_changes_lines(cell: &FileChangeCell) -> Vec<String> {
    let label = match cell.tool_name.as_str() {
        "mkdir" => "No changes",
        "file_write" => "File write completed · change details unavailable",
        "file_move" => "Completed file move",
        "file_rename" => "Completed file rename",
        "file_edit" | "multi_file_edit" => "Completed file edit",
        "batch_apply" => "Completed batch",
        "notebook_edit" => "Completed notebook edit",
        "write_task_spec" => "Task specification saved",
        "write_stage_semantics" => "Completed stage semantics",
        _ => "Completed file change",
    };
    vec![format!(
        "{}{}",
        crate::style::activity_marker(&cell.status),
        label
    )]
}

fn path_with_move(row: &FileChangeRow) -> String {
    match &row.display_move_path {
        Some(move_path) if !move_path.trim().is_empty() => {
            format!("{} → {}", row.display_path, move_path)
        }
        _ => row.display_path.clone(),
    }
}

fn display_path_for(path: &str, cwd: &Path) -> String {
    let raw = path.trim();
    if raw.is_empty() {
        return String::new();
    }
    let path_buf = PathBuf::from(raw);
    if !cwd.as_os_str().is_empty() {
        if let Ok(relative) = path_buf.strip_prefix(cwd) {
            let text = relative.to_string_lossy().to_string();
            if !text.is_empty() {
                return text;
            }
        }
    }
    if same_git_repo(cwd, &path_buf) {
        if let Some(relative) = pathdiff::diff_paths(&path_buf, cwd) {
            return relative.to_string_lossy().to_string();
        }
    }
    if let Ok(home) = std::env::var("HOME") {
        let home_path = PathBuf::from(home);
        if let Ok(relative) = path_buf.strip_prefix(&home_path) {
            return format!("~/{}", relative.to_string_lossy());
        }
    }
    raw.to_string()
}

fn same_git_repo(left: &Path, right: &Path) -> bool {
    match (find_git_repo_root(left), find_git_repo_root(right)) {
        (Some(left), Some(right)) => left == right,
        _ => false,
    }
}

fn find_git_repo_root(path: &Path) -> Option<PathBuf> {
    let mut current = if path.is_file() {
        path.parent()?.to_path_buf()
    } else {
        path.to_path_buf()
    };
    loop {
        if current.join(".git").exists() {
            return Some(current);
        }
        if !current.pop() {
            return None;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::file_change::{FileChangeEntry, FileChanges};

    fn plain(lines: Vec<Line<'static>>) -> String {
        lines
            .into_iter()
            .map(|line| {
                line.spans
                    .into_iter()
                    .map(|span| span.content.into_owned())
                    .collect::<String>()
            })
            .collect::<Vec<_>>()
            .join("\n")
    }

    fn reported_patch() -> FileChangeCell {
        let value =
            serde_json::from_str(include_str!("../../tests/fixtures/changed_patch.json")).unwrap();
        let crate::display_item::DisplayItem::FileChange(_, payload) =
            crate::display_item::DisplayItem::parse_value(&value).unwrap()
        else {
            panic!("file change")
        };
        FileChangeCell {
            cwd: payload.cwd,
            changes: payload.changes,
            status: "completed".into(),
            ..Default::default()
        }
    }

    #[test]
    fn research_multi_file_summary_keeps_paths_counts_and_exact_details() {
        let mut cell = FileChangeCell {
            status: "completed".into(),
            cwd: "/repo".into(),
            ..Default::default()
        };
        for index in 0..4 {
            cell.changes.insert(
                format!("/repo/file-{index}.md"),
                FileChange::Add {
                    content: (0..30)
                        .map(|line| format!("UNIQUE_BODY_{index}_{line}\n"))
                        .collect(),
                },
            );
        }
        let normal = PatchHistoryCell::new(cell.clone());
        let compact = PatchHistoryCell::new(cell).with_compact_preview(true);
        assert_eq!(normal.raw_text(), compact.raw_text());
        for width in [40, 80, 120] {
            let lines = compact.display_lines(width, &ThemePalette::default());
            assert!(lines.len() <= 10 && lines.iter().all(|line| line.width() <= width));
            let text = plain(lines);
            for index in 0..4 {
                assert!(text.contains(&format!("file-{index}.md")));
            }
            assert!(
                text.contains("+120") && text.contains("ctrl + t") && !text.contains("UNIQUE_BODY")
            );
            assert_eq!(text.matches("Added").count(), 4);
            assert!(plain(normal.display_lines(width, &ThemePalette::default()))
                .contains("UNIQUE_BODY"));
            assert!(plain(compact.detail_lines(width, &ThemePalette::default()))
                .contains("UNIQUE_BODY_3_29"));
        }
    }

    #[test]
    fn research_compaction_does_not_hide_deletion_failure_or_missing_content() {
        for case in ["delete", "failure", "incomplete", "overwrite"] {
            let mut cell = reported_patch();
            match case {
                "delete" => cell.changes.insert(
                    "/repo/deleted.md".into(),
                    FileChange::Delete {
                        content: "DELETED_CONTENT\n".into(),
                    },
                ),
                "failure" => {
                    cell.status = "failed".into();
                    cell.error = Some("Permission denied".into());
                }
                _ => cell.changes.push(FileChangeEntry {
                    path: "/repo/unknown.md".into(),
                    operation: "update".into(),
                    body_complete: case != "incomplete",
                    overwrote_destination: case == "overwrite",
                    ..Default::default()
                }),
            }
            let normal = PatchHistoryCell::new(cell.clone());
            let compact = PatchHistoryCell::new(cell).with_compact_preview(true);
            for width in [40, 80, 120] {
                assert_eq!(
                    normal.display_lines(width, &ThemePalette::default()),
                    compact.display_lines(width, &ThemePalette::default()),
                    "{case}"
                );
            }
        }
    }

    #[test]
    fn failed_pending_target_has_one_terminal_card() {
        let mut changes = FileChanges::default();
        changes.insert(
            "/repo/plan.md".into(),
            FileChange::Pending { move_path: None },
        );
        let cell = FileChangeCell {
            tool_name: "patch_apply".into(),
            cwd: "/repo".into(),
            status: "failed".into(),
            stderr: Some("Failed to find expected lines in /repo/plan.md:\nold".into()),
            changes,
            ..Default::default()
        };
        for width in [40, 80, 120] {
            let text = plain(render_file_change(&cell, width, &ThemePalette::default()));
            assert_eq!(text.matches("✗").count(), 1, "{text}");
            assert!(
                !text.contains("Editing") && text.contains("Patch context changed"),
                "{text}"
            );
            assert!(text.contains("No changes applied"), "{text}");
        }
    }

    #[test]
    fn changed_preview_is_bounded_and_keeps_the_multi_file_tree_connected() {
        let lines = render_file_change(&reported_patch(), 190, &ThemePalette::default());
        assert!(lines.iter().all(|line| line.width() <= 120));
        assert!(lines
            .iter()
            .any(|line| line.to_string().starts_with("  │") && line.to_string().contains("+")));
        assert!(lines
            .iter()
            .filter(|line| line.spans.iter().any(|span| span.style.bg.is_some()))
            .all(|line| line.style.bg.is_none()));
        let text = plain(lines);
        assert!(text.contains("+- [x] 检索核实核心文献"));
    }

    #[test]
    fn changed_full_details_keep_every_source_line_with_bounded_width() {
        let patch = PatchHistoryCell::new(reported_patch());
        for width in [40, 80, 120] {
            let lines = patch.detail_lines(width, &ThemePalette::default());
            assert!(lines.iter().all(|line| line.width() <= width));
            let text = plain(lines);
            assert!(text.contains("+- [x] 检索核实核心文献"));
            assert!(text.contains("129 +6."));
            assert!(!text.contains("ctrl + t"));
        }
    }

    #[test]
    fn changed_reported_patch_keeps_update_excerpt_without_inventing_line_numbers() {
        let lines = render_file_change(&reported_patch(), 120, &ThemePalette::default());
        let text = plain(lines);
        assert!(text.contains("(+150 -5)"), "{text}");
        assert!(
            text.contains("+- [x] 检索核实核心文献"),
            "update body disappeared: {text}"
        );
        assert!(
            text.contains("@@"),
            "unnumbered hunk boundary must remain explicit"
        );
    }

    #[test]
    fn changed_headers_and_diff_rows_fit_cjk_and_long_paths() {
        for width in [40, 80, 120] {
            let lines = render_file_change(&reported_patch(), width, &ThemePalette::default());
            assert!(
                lines
                    .iter()
                    .any(|line| line.to_string().contains("(+5 -5)")),
                "Keep change counts together"
            );
            for line in &lines {
                assert!(line.width() <= width, "width {width}: {line:?}");
            }
            let out = std::env::var("OWNPI_CHANGED_ACCEPTANCE_OUTPUT");
            if let Ok(out) = out {
                std::fs::write(
                    std::path::Path::new(&out).join(format!("changed-{width}.txt")),
                    plain(lines),
                )
                .unwrap();
            }
        }
    }

    #[test]
    fn changed_preview_counts_source_lines_and_never_starts_tail_in_a_continuation() {
        let row = collect_rows(&reported_patch()).remove(0);
        let theme = ThemePalette::default();
        for width in [40, 80, 120, 180] {
            let body = render_change_body(&row, width, &theme);
            assert!(body.len() <= DIFF_PREVIEW_MAX_BODY_LINES);
            let marker = body
                .iter()
                .position(|line| line.to_string().contains("ctrl + t"))
                .unwrap();
            let tail = body[marker + 1].to_string();
            assert!(
                tail.trim_start().chars().next().unwrap().is_ascii_digit(),
                "tail lost source identity: {tail:?}"
            );
            let omitted: usize = body[marker]
                .to_string()
                .split('+')
                .nth(1)
                .unwrap()
                .split_whitespace()
                .next()
                .unwrap()
                .parse()
                .unwrap();
            assert!(
                omitted < 129,
                "count must describe source lines, not wrapped screen rows: {omitted}"
            );
        }
    }

    #[test]
    fn changed_diff_long_url_tabs_and_cjk_remain_within_width() {
        let mut cell = FileChangeCell {
            cwd: "/repo".into(),
            status: "completed".into(),
            ..Default::default()
        };
        cell.changes.insert(
            "/repo/x.md".into(),
            FileChange::Add {
                content:
                    "中\thttps://example.org/very/long/path/without/a/break/very/long/very/long"
                        .into(),
            },
        );
        for width in [40, 80] {
            let lines = render_file_change(&cell, width, &ThemePalette::default());
            assert!(
                lines
                    .iter()
                    .all(|line| line.width() <= width && !line.to_string().contains('\t')),
                "{lines:?}"
            );
        }
    }

    #[test]
    fn renders_single_update_summary_with_counts() {
        let mut changes = FileChanges::default();
        changes.insert(
            "/repo/src/lib.rs".to_string(),
            FileChange::Update {
                unified_diff:
                    "--- a/src/lib.rs\n+++ b/src/lib.rs\n@@ -1 +1,2 @@\n-old\n+new\n+line\n"
                        .to_string(),
                move_path: None,
            },
        );
        let cell = FileChangeCell {
            cwd: "/repo".to_string(),
            changes,
            status: "completed".to_string(),
            ..Default::default()
        };

        let rendered = plain(render_file_change(&cell, 80, &ThemePalette::default()));

        assert!(rendered.contains("⦁ Edited src/lib.rs (+2 -1)"));
        assert!(rendered.contains("    1 -old"));
        assert!(rendered.contains("    1 +new"));
        assert!(rendered.contains("    2 +line"));
    }

    #[test]
    fn legacy_unknown_file_change_never_claims_generic_changed() {
        let mut changes = FileChanges::default();
        changes.insert("/repo/report.md".to_string(), FileChange::Unknown);
        let cell = FileChangeCell {
            cwd: "/repo".to_string(),
            changes,
            status: "completed".to_string(),
            ..Default::default()
        };

        let raw = render_file_change_raw_text(&cell);

        assert_eq!(raw, "⦁ Edited report.md");
        assert!(!raw.contains("Changed"));
    }

    #[test]
    fn unknown_file_write_keeps_target_without_claiming_add_or_edit() {
        let mut changes = FileChanges::default();
        changes.push(FileChangeEntry {
            path: "/repo/report.md".to_string(),
            change: FileChange::Unknown,
            operation: "file_write".to_string(),
            ..Default::default()
        });
        let cell = FileChangeCell {
            cwd: "/repo".to_string(),
            changes,
            status: "completed".to_string(),
            ..Default::default()
        };

        let raw = render_file_change_raw_text(&cell);

        assert_eq!(raw, "⦁ Wrote report.md · change details unavailable");
        assert!(!raw.contains("Added"));
        assert!(!raw.contains("Edited"));
    }

    #[test]
    fn replacement_path_list_is_compact_in_display_and_transcript() {
        let mut changes = FileChanges::default();
        for path in ["/repo/a.md", "/repo/b.md"] {
            for operation in ["delete", "add"] {
                changes.push(FileChangeEntry {
                    path: path.to_string(),
                    operation: operation.to_string(),
                    change: FileChange::Unknown,
                    ..Default::default()
                });
            }
        }
        let cell = FileChangeCell {
            cwd: "/repo".to_string(),
            changes,
            status: "completed".to_string(),
            ..Default::default()
        };
        let expected = "⦁ Edited 2 files\n  ├ a.md\n  └ b.md";
        assert_eq!(render_file_change_raw_text(&cell), expected);
        assert_eq!(
            plain(render_file_change(&cell, 80, &ThemePalette::default())),
            expected
        );
        assert_eq!(cell.changes.len(), 4);
    }

    #[test]
    fn replacement_diff_is_net_and_failed_operations_are_preserved() {
        let mut changes = FileChanges::default();
        changes.insert(
            "/repo/a.md".to_string(),
            FileChange::Delete {
                content: "same\nold\n".to_string(),
            },
        );
        changes.insert(
            "/repo/a.md".to_string(),
            FileChange::Add {
                content: "same\nnew\n".to_string(),
            },
        );
        let mut cell = FileChangeCell {
            cwd: "/repo".to_string(),
            changes,
            status: "completed".to_string(),
            ..Default::default()
        };
        let raw = render_file_change_raw_text(&cell);
        assert!(raw.starts_with("⦁ Edited a.md (+1 -1)"), "{raw}");
        assert!(raw.contains("-old"));
        assert!(raw.contains("+new"));
        cell.status = "failed".to_string();
        assert_eq!(collect_rows(&cell).len(), 2);
    }

    #[test]
    fn incomplete_replacement_bodies_are_not_discarded() {
        let mut changes = FileChanges::default();
        for change in [
            FileChange::Delete {
                content: "old".to_string(),
            },
            FileChange::Add {
                content: "new".to_string(),
            },
        ] {
            changes.push(FileChangeEntry {
                path: "/repo/a.md".to_string(),
                change,
                body_omitted_bytes: 20,
                body_complete: false,
                ..Default::default()
            });
        }
        let cell = FileChangeCell {
            changes,
            status: "completed".to_string(),
            ..Default::default()
        };
        assert_eq!(collect_rows(&cell).len(), 2);
        assert!(render_file_change_raw_text(&cell).contains("a.md"));
    }

    #[test]
    fn raw_text_renders_multiple_files_and_relative_paths() {
        let mut changes = FileChanges::default();
        changes.insert(
            "/repo/b.txt".to_string(),
            FileChange::Delete {
                content: "gone\n".to_string(),
            },
        );
        changes.insert(
            "/repo/a.txt".to_string(),
            FileChange::Add {
                content: "one\ntwo\n".to_string(),
            },
        );
        let cell = FileChangeCell {
            cwd: "/repo".to_string(),
            changes,
            status: "completed".to_string(),
            ..Default::default()
        };

        let raw = render_file_change_raw_text(&cell);
        let rendered = plain(render_file_change(&cell, 80, &ThemePalette::default()));

        assert!(raw.contains("⦁ Changed 2 paths (+2 -1)"));
        assert!(raw.contains("  ├ a.txt (+2 -0)"));
        assert!(raw.contains("  └ b.txt (+0 -1)"));
        assert!(!raw.contains("  Added "));
        assert!(!raw.contains("  Deleted "));
        assert!(raw.find("  ├ a.txt").unwrap() < raw.find("  └ b.txt").unwrap());
        assert!(rendered.contains("  ├ a.txt (+2 -0)"));
        assert!(rendered.contains("  └ b.txt (+0 -1)"));
        assert!(raw.contains("    1 +one"));
        assert!(raw.contains("    1 -gone"));
    }

    #[test]
    fn large_diff_previews_real_head_and_tail_while_raw_keeps_every_line() {
        let content = (1..=30)
            .map(|line| format!("added line {line}"))
            .collect::<Vec<_>>()
            .join("\n");
        let mut changes = FileChanges::default();
        changes.insert(
            "/repo/large.txt".to_string(),
            FileChange::Add {
                content: content.clone(),
            },
        );
        let cell = FileChangeCell {
            cwd: "/repo".to_string(),
            changes,
            status: "completed".to_string(),
            ..Default::default()
        };

        let display = plain(render_file_change(&cell, 100, &ThemePalette::default()));
        let raw = render_file_change_raw_text(&cell);

        assert!(display.contains("+added line 1"), "{display}");
        assert!(display.contains("+added line 30"), "{display}");
        assert!(
            display.contains("… +18 lines (ctrl + t to view transcript)"),
            "{display}"
        );
        assert!(!display.contains("+added line 15"), "{display}");
        assert!(raw.contains("+added line 15"), "{raw}");
    }

    #[test]
    fn failed_patch_uses_codex_failure_title_in_display_and_raw_text() {
        let cell = FileChangeCell {
            status: "failed".to_string(),
            stderr: Some("patch rejected".to_string()),
            ..Default::default()
        };

        let rendered = plain(render_file_change(&cell, 80, &ThemePalette::default()));
        let raw = render_file_change_raw_text(&cell);

        assert!(rendered.contains("✗ Failed to apply patch"));
        assert!(rendered.contains("patch rejected"));
        assert!(raw.contains("✗ Failed to apply patch"));
        assert!(raw.contains("patch rejected"));
    }

    #[test]
    fn context_mismatch_is_compact_in_history_but_lossless_in_transcript() {
        let error = concat!(
            "Failed to find expected lines in /repo/PLAN.md:\n",
            "# Old heading\n",
            "stale body that must not appear in the primary history"
        );
        let cell = FileChangeCell {
            tool_name: "patch_apply".to_string(),
            status: "failed".to_string(),
            cwd: "/repo".to_string(),
            stderr: Some(error.to_string()),
            ..Default::default()
        };

        let rendered = plain(render_file_change(&cell, 100, &ThemePalette::default()));
        let raw = render_file_change_raw_text(&cell);

        assert!(
            rendered.contains("✗ Patch context changed · PLAN.md"),
            "{rendered}"
        );
        assert!(rendered.contains("No changes applied; re-read the file before retrying"));
        assert!(!rendered.contains("# Old heading"), "{rendered}");
        assert!(raw.contains("# Old heading"), "{raw}");
        assert!(raw.contains("stale body"), "{raw}");
    }

    #[test]
    fn completed_file_write_without_evidence_is_explicitly_unknown() {
        let cell = FileChangeCell {
            tool_name: "file_write".to_string(),
            status: "completed".to_string(),
            ..Default::default()
        };

        let raw = render_file_change_raw_text(&cell);

        assert_eq!(
            raw,
            format!(
                "{}File write completed · change details unavailable",
                crate::style::GLYPH_ACTION
            )
        );
        assert!(!raw.contains("Completed file write"));
    }

    #[test]
    fn unparsable_update_diff_does_not_fabricate_zero_counts() {
        let mut changes = FileChanges::default();
        changes.insert(
            "/repo/src/lib.rs".to_string(),
            FileChange::Update {
                unified_diff: "+not a valid unified diff\n-just raw lines\n".to_string(),
                move_path: None,
            },
        );
        let cell = FileChangeCell {
            cwd: "/repo".to_string(),
            changes,
            status: "completed".to_string(),
            ..Default::default()
        };

        let raw = PatchHistoryCell::new(cell).raw_text();

        assert!(raw.contains("⦁ Edited src/lib.rs"));
        assert!(!raw.contains("(+0 -0)"));
    }

    #[test]
    fn semantic_file_changes_render_truthful_operation_labels() {
        let cases = [
            (
                "file_move",
                FileChangeEntry {
                    path: "/repo/old.md".to_string(),
                    change: FileChange::Move {
                        unified_diff: String::new(),
                        move_path: "/repo/new.md".to_string(),
                    },
                    operation: "move".to_string(),
                    body_complete: true,
                    ..Default::default()
                },
                "⦁ Moved old.md → new.md",
            ),
            (
                "file_rename",
                FileChangeEntry {
                    path: "/repo/old.md".to_string(),
                    change: FileChange::Move {
                        unified_diff: String::new(),
                        move_path: "/repo/new.md".to_string(),
                    },
                    operation: "rename".to_string(),
                    body_complete: true,
                    ..Default::default()
                },
                "⦁ Renamed old.md → new.md",
            ),
            (
                "mkdir",
                FileChangeEntry {
                    path: "/repo/reports".to_string(),
                    change: FileChange::Directory,
                    operation: "create_directory".to_string(),
                    body_complete: true,
                    ..Default::default()
                },
                "⦁ Created directory reports",
            ),
            (
                "file_write",
                FileChangeEntry {
                    path: "/repo/same.md".to_string(),
                    change: FileChange::Noop,
                    operation: "file_write".to_string(),
                    reason: "content_unchanged".to_string(),
                    body_complete: true,
                    ..Default::default()
                },
                "⦁ No changes · same.md",
            ),
        ];

        for (tool_name, entry, expected) in cases {
            let mut changes = FileChanges::default();
            changes.push(entry);
            let raw = render_file_change_raw_text(&FileChangeCell {
                tool_name: tool_name.to_string(),
                cwd: "/repo".to_string(),
                status: "completed".to_string(),
                changes,
                ..Default::default()
            });
            assert!(raw.contains(expected), "{raw}");
            assert!(!raw.contains("(+0 -0)"), "{raw}");
        }
    }

    #[test]
    fn omitted_body_keeps_authoritative_counts_and_explains_absence() {
        let mut changes = FileChanges::default();
        changes.push(FileChangeEntry {
            path: "/repo/large.md".to_string(),
            change: FileChange::Add {
                content: String::new(),
            },
            operation: "file_write".to_string(),
            added_lines: Some(3200),
            removed_lines: Some(0),
            body_complete: false,
            body_omitted_bytes: 1_200_000,
            body_ref: "/repo/large.md".to_string(),
            ..Default::default()
        });
        let raw = render_file_change_raw_text(&FileChangeCell {
            tool_name: "file_write".to_string(),
            cwd: "/repo".to_string(),
            status: "completed".to_string(),
            changes,
            ..Default::default()
        });

        assert!(raw.contains("⦁ Added large.md (+3200 -0)"), "{raw}");
        assert!(raw.contains("body omitted"), "{raw}");
        assert!(raw.contains("1200000 bytes"), "{raw}");
    }

    #[test]
    fn empty_non_patch_failure_uses_the_tool_specific_title() {
        let raw = render_file_change_raw_text(&FileChangeCell {
            tool_name: "file_rename".to_string(),
            status: "failed".to_string(),
            error: Some("destination exists".to_string()),
            ..Default::default()
        });

        assert!(raw.contains("✗ Failed to rename file"), "{raw}");
        assert!(!raw.contains("Failed to apply patch"), "{raw}");
    }

    #[test]
    fn stage_semantics_failure_keeps_structural_diagnostics_visible() {
        let error = [
            "invalid_stage_semantics_heading_order",
            "The first level-one heading must exactly match `# Shared Stage Meaning`.",
            "Expected H1 order: # Shared Stage Meaning → # Core Semantic View",
            "Actual first H1: # Wrong Heading",
        ]
        .join("\n");
        let cell = FileChangeCell {
            tool_name: "write_stage_semantics".to_string(),
            status: "failed".to_string(),
            error: Some(error),
            ..Default::default()
        };

        let rendered = plain(render_file_change(&cell, 120, &ThemePalette::default()));
        let raw = render_file_change_raw_text(&cell);

        for expected in [
            "✗ Failed to write stage semantics",
            "invalid_stage_semantics_heading_order",
            "Expected H1 order: # Shared Stage Meaning",
            "Actual first H1: # Wrong Heading",
        ] {
            assert!(rendered.contains(expected), "{rendered}");
            assert!(raw.contains(expected), "{raw}");
        }
    }

    #[test]
    fn display_paths_cover_relative_cwd_home_and_moves() {
        let home = std::env::var("HOME").unwrap_or_else(|_| "/Users/example".to_string());
        assert_eq!(
            display_path_for("relative/file.rs", Path::new("/repo")),
            "relative/file.rs"
        );
        assert_eq!(
            display_path_for("/repo/src/lib.rs", Path::new("/repo")),
            "src/lib.rs"
        );
        assert_eq!(
            display_path_for(&format!("{home}/project/src/lib.rs"), Path::new("/repo")),
            "~/project/src/lib.rs"
        );

        let mut changes = FileChanges::default();
        changes.insert(
            "/repo/src/old.rs".to_string(),
            FileChange::Update {
                unified_diff: "--- a/src/old.rs\n+++ b/src/new.rs\n@@ -1 +1 @@\n-old\n+new\n"
                    .to_string(),
                move_path: Some("/repo/src/new.rs".to_string()),
            },
        );
        let cell = FileChangeCell {
            cwd: "/repo".to_string(),
            changes,
            status: "completed".to_string(),
            ..Default::default()
        };

        let raw = PatchHistoryCell::new(cell).raw_text();

        assert!(raw.contains("src/old.rs → src/new.rs"));
    }

    #[test]
    fn same_repo_paths_are_relative_to_cwd() {
        let root =
            std::env::temp_dir().join(format!("neuro-agent-ui-diff-root-{}", std::process::id()));
        let cwd = root.join("packages/app");
        let path = root.join("packages/lib/src/lib.rs");
        std::fs::create_dir_all(root.join(".git")).unwrap();
        std::fs::create_dir_all(&cwd).unwrap();
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();

        let display = display_path_for(&path.to_string_lossy(), &cwd);

        assert_eq!(display, "../lib/src/lib.rs");
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn multi_hunk_update_inserts_codex_style_hunk_separator() {
        let mut changes = FileChanges::default();
        changes.insert(
            "/repo/src/lib.rs".to_string(),
            FileChange::Update {
                unified_diff:
                    "--- a/src/lib.rs\n+++ b/src/lib.rs\n@@ -1,1 +1,1 @@\n-old\n+new\n@@ -10,1 +10,1 @@\n-left\n+right\n"
                        .to_string(),
                move_path: None,
            },
        );
        let cell = FileChangeCell {
            cwd: "/repo".to_string(),
            changes,
            status: "completed".to_string(),
            ..Default::default()
        };

        let raw = PatchHistoryCell::new(cell).raw_text();

        assert!(raw.contains("⋮"), "raw diff output: {raw}");
        assert!(raw.contains("    10 -left"));
        assert!(raw.contains("    10 +right"));
    }

    #[test]
    fn narrow_diff_body_wraps_content_and_styles_backgrounds() {
        let mut changes = FileChanges::default();
        changes.insert(
            "/repo/src/lib.rs".to_string(),
            FileChange::Add {
                content: "abcdefghijklmnopqrstuvwxyz".to_string(),
            },
        );
        let cell = FileChangeCell {
            cwd: "/repo".to_string(),
            changes,
            status: "completed".to_string(),
            ..Default::default()
        };
        let patch = PatchHistoryCell::new(cell);

        let rendered = patch.display_lines(16, &ThemePalette::default());

        let bg_lines = rendered
            .iter()
            .filter(|line| line.spans.iter().any(|span| span.style.bg.is_some()))
            .collect::<Vec<_>>();
        let signed_lines = bg_lines
            .iter()
            .filter(|line| {
                line.spans
                    .iter()
                    .map(|span| span.content.as_ref())
                    .collect::<String>()
                    .contains("+")
            })
            .count();
        assert!(bg_lines.len() > 1, "diff line should wrap with backgrounds");
        assert_eq!(signed_lines, 1, "continuation rows must not repeat + sign");
    }
    #[test]
    fn historical_overwrite_is_explicit_without_fabricating_deleted_line_counts() {
        let mut changes = FileChanges::default();
        changes.push(FileChangeEntry {
            path: "/repo/plan.md".into(),
            change: FileChange::Unknown,
            operation: "overwrite".into(),
            body_complete: false,
            ..Default::default()
        });
        let mut cell = FileChangeCell {
            cwd: "/repo".into(),
            changes,
            status: "completed".into(),
            ..Default::default()
        };
        for multiple in [false, true] {
            if multiple {
                cell.changes.insert(
                    "/repo/new.md".into(),
                    FileChange::Add {
                        content: "new\n".into(),
                    },
                );
            }
            let raw = render_file_change_raw_text(&cell);
            assert!(raw.contains("previous content unavailable"), "{raw}");
            assert!(
                !raw.contains("Added plan.md") && !raw.contains("plan.md (+"),
                "{raw}"
            );
            assert!(!raw.lines().next().unwrap().contains("(+"), "{raw}");
            for width in [40, 120] {
                let shown = render_file_change(&cell, width, &ThemePalette::default())
                    .iter()
                    .map(crate::wrapping::line_plain_text)
                    .collect::<Vec<_>>()
                    .join("\n");
                let compact = shown.split_whitespace().collect::<String>();
                assert!(compact.contains("previouscontentunavailable"), "{shown}");
                assert!(!shown.contains("Added plan.md"), "{shown}");
            }
        }
    }
}
