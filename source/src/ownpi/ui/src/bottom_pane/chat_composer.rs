use std::cell::RefCell;
use std::collections::{HashMap, VecDeque};
use std::time::Instant;

use crate::keymap::{ComposerAction, DefaultKeymap};
use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span, Text};
use ratatui::widgets::{Paragraph, Widget, Wrap};
use unicode_width::UnicodeWidthStr;

use crate::theme::{BottomPanePalette, ThemePalette};

use super::attachment::{ImageDetail, LocalImageAttachment, RemoteImageAttachment};
use super::chat_composer_history::{ChatComposerHistory, HistoryDirection, HistoryEntry};
use super::paste_burst::{CharDecision, FlushResult, PasteBurst};
use super::textarea::{TextArea, TextAreaState, TextElementSnapshot};

const LARGE_PASTE_CHAR_THRESHOLD: usize = 1000;

#[derive(Debug, Default)]
pub(crate) struct ChatComposer {
    textarea: TextArea,
    textarea_state: RefCell<TextAreaState>,
    paste_burst: PasteBurst,
    history: ChatComposerHistory,
    history_search: Option<HistorySearchSession>,
    pending_pastes: Vec<(String, String)>,
    local_images: Vec<LocalImageAttachment>,
    remote_images: Vec<RemoteImageAttachment>,
}

pub(crate) const COMPOSER_EDGE_COLS: u16 = 2;

/// The composer content area after the copy-safe accent rail and its padding:
/// every renderer and cursor calculation must shift by COMPOSER_EDGE_COLS so
/// text never overlaps the rail.
pub(crate) fn composer_inner_area(area: Rect) -> Rect {
    Rect {
        x: area.x.saturating_add(COMPOSER_EDGE_COLS),
        y: area.y,
        width: area.width.saturating_sub(COMPOSER_EDGE_COLS).max(1),
        height: area.height,
    }
}

impl ChatComposer {
    pub(crate) fn recommended_input_flush_delay() -> std::time::Duration {
        PasteBurst::recommended_flush_delay()
    }

    pub(crate) fn set_vim_enabled(&mut self, enabled: bool) {
        self.textarea.set_vim_enabled(enabled);
    }

    pub(crate) fn vim_insert_mode_active(&self) -> bool {
        self.textarea.vim_insert_mode_active()
    }

    pub(crate) fn vim_is_enabled(&self) -> bool {
        self.textarea.vim_is_enabled()
    }

    pub(crate) fn vim_enter_normal_mode(&mut self) {
        self.textarea.vim_enter_normal_mode();
    }

    pub(crate) fn text(&self) -> &str {
        self.textarea.text()
    }

    pub(crate) fn current_text_with_pending(&self) -> String {
        if self.pending_pastes.is_empty() {
            return self.textarea.text().to_string();
        }
        Self::expand_pending_pastes(
            self.textarea.text(),
            self.textarea.text_element_snapshots(),
            &self.pending_pastes,
        )
    }

    pub(crate) fn file_token(&self) -> Option<String> {
        self.current_prefixed_token('@').map(|(_, token)| token)
    }

    pub(crate) fn set_text(&mut self, value: impl Into<String>) {
        self.textarea.set_text(value.into());
        self.pending_pastes.clear();
        self.local_images.clear();
        self.remote_images.clear();
        self.reset_textarea_scroll();
        self.history.reset_navigation();
        self.history_search = None;
    }

    pub(crate) fn set_text_with_pending(
        &mut self,
        text: impl Into<String>,
        text_elements: Vec<TextElementSnapshot>,
        pending_pastes: Vec<(String, String)>,
    ) {
        self.textarea
            .set_text_with_element_snapshots(text.into(), &text_elements);
        self.pending_pastes = pending_pastes;
        self.sync_pending_pastes_with_elements();
        self.sync_image_attachments_with_elements();
        self.reset_textarea_scroll();
        self.history.reset_navigation();
        self.history_search = None;
    }

    pub(crate) fn clear(&mut self) {
        self.textarea.clear();
        self.pending_pastes.clear();
        self.local_images.clear();
        self.remote_images.clear();
        self.reset_textarea_scroll();
        self.history.reset_navigation();
        self.history_search = None;
    }

    /// Attaches a local image path: inserts an atomic `[Image #N]` placeholder
    /// into the textarea and records the attachment in the side table (Codex
    /// `attachment_state.rs:96-101`). Returns the placeholder text.
    pub(crate) fn attach_local_image(&mut self, path: std::path::PathBuf) -> String {
        self.attach_local_image_with_detail(path, ImageDetail::Auto)
    }

    pub(crate) fn attach_local_image_with_detail(
        &mut self,
        path: std::path::PathBuf,
        detail: ImageDetail,
    ) -> String {
        let placeholder = self.next_image_placeholder();
        self.textarea.insert_element(&placeholder);
        self.local_images.push(LocalImageAttachment {
            placeholder: placeholder.clone(),
            path,
            detail,
        });
        placeholder
    }

    /// Attaches a remote image URL: inserts a placeholder and records the URL
    /// (Codex remote image rows; submit sends it as a remote image item).
    pub(crate) fn attach_remote_image(&mut self, url: String) -> String {
        let placeholder = self.next_image_placeholder();
        self.textarea.insert_element(&placeholder);
        self.remote_images.push(RemoteImageAttachment {
            placeholder: placeholder.clone(),
            url,
        });
        placeholder
    }

    fn next_image_placeholder(&self) -> String {
        let index_of = |placeholder: &str| -> Option<usize> {
            placeholder
                .strip_prefix("[Image #")
                .and_then(|value| value.strip_suffix(']'))
                .and_then(|value| value.parse::<usize>().ok())
        };
        let max_index = self
            .local_images
            .iter()
            .filter_map(|attachment| index_of(&attachment.placeholder))
            .chain(
                self.remote_images
                    .iter()
                    .filter_map(|attachment| index_of(&attachment.placeholder)),
            )
            .max()
            .unwrap_or(0);
        format!("[Image #{}]", max_index + 1)
    }

    /// Read accessor for the attachment side table; exercised by attachment
    /// tests and kept for upcoming composer attachment display.
    #[allow(dead_code)]
    pub(crate) fn local_images(&self) -> &[LocalImageAttachment] {
        &self.local_images
    }

    pub(crate) fn take_local_images(&mut self) -> Vec<LocalImageAttachment> {
        std::mem::take(&mut self.local_images)
    }

    pub(crate) fn take_remote_images(&mut self) -> Vec<RemoteImageAttachment> {
        std::mem::take(&mut self.remote_images)
    }

    pub(crate) fn restore_remote_images(&mut self, images: Vec<RemoteImageAttachment>) {
        self.remote_images = images;
        self.sync_image_attachments_with_elements();
    }

    /// Restores attachments after a queued message is popped back into the
    /// composer (Alt+Up) or drafts are restored after an interrupt. The
    /// placeholders are already present in the restored text; this rebuilds
    /// the side table and prunes any orphans.
    pub(crate) fn restore_local_images(&mut self, images: Vec<LocalImageAttachment>) {
        self.local_images = images;
        self.sync_image_attachments_with_elements();
    }

    /// Prunes attachments whose placeholder no longer exists in the textarea
    /// (deleted by the user), then relabels survivors to keep numbering
    /// contiguous (Codex `attachment_state.rs:103-118,225-249`).
    fn sync_image_attachments_with_elements(&mut self) {
        if self.local_images.is_empty() {
            return;
        }
        let mut live_counts: HashMap<String, usize> = HashMap::new();
        for payload in self.textarea.element_payloads() {
            *live_counts.entry(payload).or_default() += 1;
        }
        self.local_images.retain(|attachment| {
            let Some(count) = live_counts.get_mut(&attachment.placeholder) else {
                return false;
            };
            if *count == 0 {
                return false;
            }
            *count -= 1;
            true
        });
        let mut renumbered: Vec<(String, String)> = Vec::new();
        for (index, attachment) in self.local_images.iter_mut().enumerate() {
            let expected = format!("[Image #{}]", index + 1);
            if attachment.placeholder != expected {
                renumbered.push((attachment.placeholder.clone(), expected.clone()));
                attachment.placeholder = expected;
            }
        }
        for (old, new) in renumbered {
            self.textarea.retag_element(&old, &new);
        }
    }

    pub(crate) fn insert_paste(&mut self, pasted: &str) {
        self.flush_paste_burst();
        self.insert_normalized_paste(pasted);
    }

    pub(crate) fn insert_file_completion(&mut self, path: &str) {
        let Some((range, _token)) = self.current_prefixed_token('@') else {
            return;
        };
        let inserted = super::file_popup::normalize_inserted_path(path);
        self.textarea.replace_range(range, &format!("{inserted} "));
        self.sync_pending_pastes_with_elements();
        self.sync_image_attachments_with_elements();
        self.history.reset_navigation();
        self.history_search = None;
    }

    pub(crate) fn handle_key(&mut self, key: KeyEvent) -> bool {
        let now = Instant::now();
        match self.paste_burst.flush_if_due(now) {
            FlushResult::Paste(text) => self.insert_normalized_paste(&text),
            FlushResult::Typed(ch) => self.textarea.insert_char(ch),
            FlushResult::None => {}
        }

        if self.history_search.is_some() {
            return self.handle_history_search_key(key);
        }

        match key {
            KeyEvent {
                code: KeyCode::Enter,
                modifiers,
                ..
            } if modifiers.is_empty()
                && DefaultKeymap::composer_action(key) == Some(ComposerAction::Submit) =>
            {
                let mut draft_text = self.textarea.text().to_string();
                if let Some(buffered) = self.paste_burst.preview_buffered_text() {
                    draft_text.push_str(&buffered);
                }
                let first_line = draft_text.lines().next().unwrap_or("");
                let in_slash_context = first_line.trim_start().starts_with('/');
                if in_slash_context {
                    self.flush_paste_burst();
                    return false;
                }
                if self.paste_burst.append_newline_if_active(now) {
                    return true;
                }
                if self.paste_burst.direct_insert_newline_should_insert(now) {
                    self.textarea.insert_str("\n");
                    return true;
                }
                self.flush_paste_burst();
                false
            }
            KeyEvent {
                code: KeyCode::Char('j'),
                modifiers: KeyModifiers::CONTROL,
                ..
            } => {
                self.flush_paste_burst();
                self.textarea.insert_str("\n");
                true
            }
            KeyEvent {
                code: KeyCode::Char('m'),
                modifiers: KeyModifiers::CONTROL,
                ..
            } => {
                self.flush_paste_burst();
                false
            }
            KeyEvent {
                code: KeyCode::Char('\u{0008}' | '\u{007f}'),
                ..
            } => self.handle_basic_key(key),
            KeyEvent {
                code: KeyCode::Char(ch),
                modifiers,
                ..
            } if modifiers.is_empty() || modifiers == KeyModifiers::SHIFT => {
                if ch.is_ascii() && !ch.is_ascii_control() {
                    match self.paste_burst.on_plain_char(ch, now) {
                        CharDecision::RetainFirstChar => false,
                        CharDecision::BeginBufferFromPending | CharDecision::BufferAppend => {
                            self.paste_burst.append_char_to_buffer(ch);
                            false
                        }
                        CharDecision::BeginBuffer { retro_chars } => {
                            if let Some(grab) = self
                                .paste_burst
                                .decide_begin_buffer(self.textarea.text(), retro_chars)
                            {
                                let cursor = self.textarea.cursor();
                                self.textarea.replace_range(grab.start_byte..cursor, "");
                                self.paste_burst.append_char_to_buffer(ch);
                                true
                            } else {
                                self.textarea.insert_char(ch);
                                true
                            }
                        }
                    }
                } else if !ch.is_ascii_control() {
                    if let Some(decision) = self.paste_burst.on_plain_char_no_hold(now) {
                        match decision {
                            CharDecision::BufferAppend => {
                                self.paste_burst.append_char_to_buffer(ch);
                                false
                            }
                            CharDecision::BeginBuffer { retro_chars } => {
                                if let Some(grab) = self
                                    .paste_burst
                                    .decide_begin_buffer(self.textarea.text(), retro_chars)
                                {
                                    let cursor = self.textarea.cursor();
                                    self.textarea.replace_range(grab.start_byte..cursor, "");
                                    self.paste_burst.append_char_to_buffer(ch);
                                    true
                                } else {
                                    self.textarea.insert_char(ch);
                                    true
                                }
                            }
                            CharDecision::RetainFirstChar
                            | CharDecision::BeginBufferFromPending => {
                                self.textarea.insert_char(ch);
                                true
                            }
                        }
                    } else {
                        self.textarea.insert_char(ch);
                        true
                    }
                } else {
                    false
                }
            }
            KeyEvent {
                code: KeyCode::Char('r'),
                modifiers: KeyModifiers::CONTROL,
                ..
            } => {
                self.begin_history_search();
                true
            }
            KeyEvent {
                code: KeyCode::Up, ..
            } => {
                if self.navigate_history(HistoryDirection::Older) {
                    true
                } else {
                    self.handle_basic_key(key)
                }
            }
            KeyEvent {
                code: KeyCode::Down,
                ..
            } => {
                if self.navigate_history(HistoryDirection::Newer) {
                    true
                } else {
                    self.handle_basic_key(key)
                }
            }
            _ => self.handle_basic_key(key),
        }
    }

    pub(crate) fn flush_paste_burst(&mut self) {
        if let Some(text) = self.paste_burst.flush_before_modified_input() {
            self.insert_normalized_paste(&text);
        }
    }

    pub(crate) fn flush_paste_burst_if_due(&mut self) -> bool {
        match self.paste_burst.flush_if_due(Instant::now()) {
            FlushResult::Paste(text) => {
                self.insert_normalized_paste(&text);
                true
            }
            FlushResult::Typed(ch) => {
                self.textarea.insert_char(ch);
                true
            }
            FlushResult::None => false,
        }
    }

    pub(crate) fn is_in_paste_burst(&self) -> bool {
        self.paste_burst.is_active()
    }

    pub(crate) fn record_submission(&mut self, text: &str) {
        self.history
            .record_local_submission(HistoryEntry::new(text));
    }

    pub(crate) fn record_current_submission(&mut self) {
        self.history
            .record_local_submission(HistoryEntry::with_rich_metadata(
                self.textarea.text().to_string(),
                self.textarea.text_element_snapshots(),
                self.local_images
                    .iter()
                    .map(|attachment| attachment.path.to_string_lossy().to_string())
                    .collect(),
                Vec::new(),
                Vec::new(),
                self.pending_pastes.clone(),
            ));
    }

    pub(crate) fn has_pending_pastes(&self) -> bool {
        !self.pending_pastes.is_empty()
    }

    #[allow(dead_code)]
    pub(crate) fn pending_pastes(&self) -> Vec<(String, String)> {
        self.pending_pastes.clone()
    }

    #[allow(dead_code)]
    pub(crate) fn text_elements(&self) -> Vec<TextElementSnapshot> {
        self.textarea.text_element_snapshots()
    }

    pub(crate) fn set_persistent_history(&mut self, entries: Vec<String>, log_id: u64) {
        self.history
            .set_persistent_history(entries.into_iter().map(HistoryEntry::new).collect(), log_id);
    }

    pub(crate) fn desired_height(&self, width: u16) -> u16 {
        self.textarea
            .desired_height(width.saturating_sub(COMPOSER_EDGE_COLS).max(1))
            .saturating_add(u16::from(self.history_search.is_some()))
    }

    pub(crate) fn cursor_pos(&self, area: Rect) -> Option<(u16, u16)> {
        // The textarea and its cursor live COMPOSER_EDGE_COLS to the right of
        // the copy-safe accent rail.
        let area = composer_inner_area(area);
        if let Some(search) = self.history_search.as_ref() {
            let query_col = UnicodeWidthStr::width("  reverse-i-search: ")
                .saturating_add(UnicodeWidthStr::width(search.query.as_str()));
            return Some((
                area.x + (query_col as u16).min(area.width.saturating_sub(1)),
                area.y
                    + self
                        .textarea
                        .desired_height(area.width)
                        .min(area.height.saturating_sub(1)),
            ));
        }
        let textarea_area = textarea_rect_for_area(area);
        self.textarea
            .cursor_pos(textarea_area, *self.textarea_state.borrow())
    }

    pub(crate) fn render(
        &self,
        area: Rect,
        buf: &mut Buffer,
        _theme: &ThemePalette,
        palette: BottomPanePalette,
    ) {
        self.render_with_placeholder(area, buf, palette, crate::branding::COMPOSER_PLACEHOLDER);
    }

    pub(crate) fn render_with_placeholder(
        &self,
        area: Rect,
        buf: &mut Buffer,
        palette: BottomPanePalette,
        placeholder_text: &str,
    ) {
        self.render_with_placeholder_and_mask(area, buf, palette, placeholder_text, None);
    }

    pub(crate) fn render_with_masked_placeholder(
        &self,
        area: Rect,
        buf: &mut Buffer,
        palette: BottomPanePalette,
        placeholder_text: &str,
        mask_char: char,
    ) {
        self.render_with_placeholder_and_mask(
            area,
            buf,
            palette,
            placeholder_text,
            Some(mask_char),
        );
    }

    fn render_with_placeholder_and_mask(
        &self,
        area: Rect,
        buf: &mut Buffer,
        palette: BottomPanePalette,
        placeholder_text: &str,
        mask_char: Option<char>,
    ) {
        if area.width == 0 || area.height == 0 {
            return;
        }
        let area = composer_inner_area(area);
        if self.history_search.is_some() {
            let widget =
                Paragraph::new(self.render_text_with_placeholder(palette, placeholder_text))
                    .style(Style::default().fg(palette.input_text))
                    .wrap(Wrap { trim: false });
            Widget::render(widget, area, buf);
            return;
        }
        let muted_style = Style::default().fg(palette.input_muted);
        let textarea_area = textarea_rect_for_area(area);
        if self.textarea.is_empty() {
            let placeholder = Span::styled(placeholder_text.to_string(), muted_style);
            buf.set_span(
                textarea_area.x,
                textarea_area.y,
                &placeholder,
                textarea_area.width,
            );
            return;
        }
        let mut state = self.textarea_state.borrow_mut();
        let style = Style::default().fg(palette.input_text);
        if let Some(mask_char) = mask_char {
            self.textarea
                .render_masked(textarea_area, buf, &mut state, style, mask_char);
        } else {
            self.textarea.render(textarea_area, buf, &mut state, style);
        }
    }

    #[cfg(test)]
    pub(crate) fn render_text(
        &self,
        _theme: &ThemePalette,
        palette: BottomPanePalette,
    ) -> Text<'static> {
        self.render_text_with_placeholder(palette, crate::branding::COMPOSER_PLACEHOLDER)
    }

    fn render_text_with_placeholder(
        &self,
        palette: BottomPanePalette,
        placeholder_text: &str,
    ) -> Text<'static> {
        let muted_style = Style::default().fg(palette.input_muted);
        if let Some(search) = self.history_search.as_ref() {
            return self.render_history_search_text(search, palette);
        }
        if self.textarea.is_empty() {
            return Text::from(Line::from(Span::styled(
                placeholder_text.to_string(),
                muted_style,
            )));
        }
        let mut lines = Vec::new();
        for segment in self.textarea.text().split('\n') {
            lines.push(Line::from(Span::styled(
                segment.to_string(),
                Style::default().fg(palette.input_text),
            )));
        }
        Text::from(lines)
    }

    fn handle_basic_key(&mut self, key: KeyEvent) -> bool {
        if let Some(text) = self.paste_burst.flush_before_modified_input() {
            self.insert_normalized_paste(&text);
        }
        self.paste_burst.clear_window_after_non_char();
        self.textarea.input(key);
        self.sync_pending_pastes_with_elements();
        self.sync_image_attachments_with_elements();
        self.history.reset_navigation();
        true
    }

    fn navigate_history(&mut self, direction: HistoryDirection) -> bool {
        if !self
            .history
            .should_handle_navigation(self.textarea.text(), self.textarea.cursor())
        {
            return false;
        }
        let entry = match direction {
            HistoryDirection::Older => self.history.navigate_up(),
            HistoryDirection::Newer => self.history.navigate_down(),
        };
        if let Some(entry) = entry {
            self.apply_history_entry(entry);
            return true;
        }
        false
    }

    fn apply_history_entry(&mut self, entry: HistoryEntry) {
        self.pending_pastes = entry.pending_pastes;
        // History recall restores attachments alongside their placeholders;
        // numbering was contiguous at submission time (Codex history recall
        // restores local+remote image metadata).
        self.local_images = entry
            .local_image_paths
            .iter()
            .enumerate()
            .map(|(index, path)| LocalImageAttachment {
                placeholder: format!("[Image #{}]", index + 1),
                path: std::path::PathBuf::from(path),
                detail: ImageDetail::Auto,
            })
            .collect();
        self.textarea
            .set_text_with_element_snapshots(entry.text, &entry.text_elements);
        self.textarea.set_cursor(self.textarea.text().len());
        self.sync_image_attachments_with_elements();
        self.reset_textarea_scroll();
    }

    fn begin_history_search(&mut self) {
        self.flush_paste_burst();
        self.history_search = Some(HistorySearchSession {
            original_text: self.textarea.text().to_string(),
            original_text_elements: self.textarea.text_element_snapshots(),
            original_pending_pastes: self.pending_pastes.clone(),
            query: String::new(),
            status: HistorySearchStatus::Idle,
            current_match_text: None,
        });
    }

    fn handle_history_search_key(&mut self, key: KeyEvent) -> bool {
        match key {
            KeyEvent {
                code: KeyCode::Char('r'),
                modifiers: KeyModifiers::CONTROL,
                ..
            }
            | KeyEvent {
                code: KeyCode::Up, ..
            } => {
                self.search_history_in_direction(HistoryDirection::Older);
                true
            }
            KeyEvent {
                code: KeyCode::Char('s'),
                modifiers: KeyModifiers::CONTROL,
                ..
            }
            | KeyEvent {
                code: KeyCode::Down,
                ..
            } => {
                self.search_history_in_direction(HistoryDirection::Newer);
                true
            }
            KeyEvent {
                code: KeyCode::Esc, ..
            }
            | KeyEvent {
                code: KeyCode::Char('c'),
                modifiers: KeyModifiers::CONTROL,
                ..
            } => {
                self.cancel_history_search();
                true
            }
            KeyEvent {
                code: KeyCode::Enter,
                modifiers,
                ..
            } if modifiers.is_empty() => {
                if self
                    .history_search
                    .as_ref()
                    .is_some_and(|search| search.status == HistorySearchStatus::Match)
                {
                    self.history_search = None;
                    self.textarea.set_cursor(self.textarea.text().len());
                }
                true
            }
            KeyEvent {
                code: KeyCode::Backspace,
                ..
            }
            | KeyEvent {
                code: KeyCode::Char('h'),
                modifiers: KeyModifiers::CONTROL,
                ..
            } => {
                if let Some(search) = self.history_search.as_mut() {
                    search.query.pop();
                }
                self.restart_history_search();
                true
            }
            KeyEvent {
                code: KeyCode::Char('u'),
                modifiers: KeyModifiers::CONTROL,
                ..
            } => {
                if let Some(search) = self.history_search.as_mut() {
                    search.query.clear();
                }
                self.restart_history_search();
                true
            }
            KeyEvent {
                code: KeyCode::Char(ch),
                modifiers,
                ..
            } if modifiers.is_empty() || modifiers == KeyModifiers::SHIFT => {
                if !ch.is_ascii_control() {
                    if let Some(search) = self.history_search.as_mut() {
                        search.query.push(ch);
                    }
                    self.restart_history_search();
                }
                true
            }
            _ => true,
        }
    }

    fn restart_history_search(&mut self) {
        let Some(search) = self.history_search.as_mut() else {
            return;
        };
        let original_text = search.original_text.clone();
        let query = search.query.clone();
        search.current_match_text = None;
        if query.is_empty() {
            search.status = HistorySearchStatus::Idle;
            self.pending_pastes = search.original_pending_pastes.clone();
            self.textarea
                .set_text_with_element_snapshots(original_text, &search.original_text_elements);
            self.sync_image_attachments_with_elements();
            self.reset_textarea_scroll();
            return;
        }
        search.status = HistorySearchStatus::Searching;
        self.search_history_in_direction(HistoryDirection::Older);
    }

    fn search_history_in_direction(&mut self, direction: HistoryDirection) {
        let Some((
            query,
            current_match,
            original_text,
            original_text_elements,
            original_pending_pastes,
        )) = self.history_search.as_ref().map(|search| {
            (
                search.query.clone(),
                search.current_match_text.clone(),
                search.original_text.clone(),
                search.original_text_elements.clone(),
                search.original_pending_pastes.clone(),
            )
        })
        else {
            return;
        };
        if query.is_empty() {
            if let Some(search) = self.history_search.as_mut() {
                search.status = HistorySearchStatus::Idle;
            }
            self.pending_pastes = original_pending_pastes;
            self.textarea
                .set_text_with_element_snapshots(original_text, &original_text_elements);
            self.reset_textarea_scroll();
            return;
        };
        match self
            .history
            .search(&query, direction, current_match.as_deref())
        {
            Some(entry) => {
                if let Some(search) = self.history_search.as_mut() {
                    search.status = HistorySearchStatus::Match;
                    search.current_match_text = Some(entry.text.clone());
                }
                self.apply_history_entry(entry);
            }
            None => {
                if let Some(search) = self.history_search.as_mut() {
                    search.status = HistorySearchStatus::NoMatch;
                    search.current_match_text = None;
                }
                self.pending_pastes = original_pending_pastes;
                self.textarea
                    .set_text_with_element_snapshots(original_text, &original_text_elements);
                self.reset_textarea_scroll();
            }
        }
    }

    fn cancel_history_search(&mut self) {
        if let Some(search) = self.history_search.take() {
            self.pending_pastes = search.original_pending_pastes;
            self.textarea.set_text_with_element_snapshots(
                search.original_text,
                &search.original_text_elements,
            );
            self.sync_image_attachments_with_elements();
            self.reset_textarea_scroll();
        }
    }

    fn render_history_search_text(
        &self,
        search: &HistorySearchSession,
        palette: BottomPanePalette,
    ) -> Text<'static> {
        let prompt_style = Style::default().fg(palette.input_accent);
        let muted_style = Style::default().fg(palette.input_muted);
        let status = match search.status {
            HistorySearchStatus::Idle => "",
            HistorySearchStatus::Searching => "  searching",
            HistorySearchStatus::Match => "  Enter accept · Esc cancel",
            HistorySearchStatus::NoMatch => "  no match",
        };
        let mut lines = self.render_text_with_search_highlight(&search.query, palette);
        lines.push(Line::from(vec![
            Span::styled("  reverse-i-search: ", muted_style),
            Span::styled(search.query.clone(), prompt_style),
            Span::styled(status, muted_style),
        ]));
        Text::from(lines)
    }

    fn render_text_with_search_highlight(
        &self,
        query: &str,
        palette: BottomPanePalette,
    ) -> Vec<Line<'static>> {
        self.textarea
            .text()
            .split('\n')
            .map(|segment| Line::from(highlight_spans(segment, query, palette)))
            .collect()
    }

    fn current_prefixed_token(&self, prefix: char) -> Option<(std::ops::Range<usize>, String)> {
        let text = self.textarea.text();
        let cursor = clamp_to_char_boundary(text, self.textarea.cursor());
        let before = &text[..cursor];
        let after = &text[cursor..];
        let start = before
            .char_indices()
            .rfind(|(_, ch)| ch.is_whitespace())
            .map(|(idx, ch)| idx + ch.len_utf8())
            .unwrap_or(0);
        let end = cursor
            + after
                .char_indices()
                .find(|(_, ch)| ch.is_whitespace())
                .map(|(idx, _)| idx)
                .unwrap_or(after.len());
        let token = &text[start..end];
        let rest = token.strip_prefix(prefix)?;
        Some((start..end, rest.to_string()))
    }

    fn insert_normalized_paste(&mut self, pasted: &str) {
        let normalized = normalize_paste_newlines(pasted);
        let char_count = normalized.chars().count();
        if char_count > LARGE_PASTE_CHAR_THRESHOLD {
            let placeholder = self.next_large_paste_placeholder(char_count);
            self.textarea.insert_element(&placeholder);
            self.pending_pastes.push((placeholder, normalized));
        } else if image_paste_enabled() && looks_like_remote_image_url(&normalized) {
            self.attach_remote_image(normalized.trim().to_string());
        } else if image_paste_enabled() && looks_like_image_path(&normalized) {
            // Codex chat_composer.rs:892-913 — a small paste that looks like an
            // image path attaches the image instead of inserting literal text.
            self.attach_local_image(std::path::PathBuf::from(normalized.trim()));
        } else {
            self.textarea.insert_str(&normalized);
        }
        self.sync_pending_pastes_with_elements();
        self.sync_image_attachments_with_elements();
    }

    fn next_large_paste_placeholder(&self, char_count: usize) -> String {
        let base = format!("[Pasted Content {char_count} chars]");
        let prefix = format!("{base} #");
        let mut max_suffix = 0usize;
        for (placeholder, _) in &self.pending_pastes {
            if placeholder == &base {
                max_suffix = max_suffix.max(1);
                continue;
            }
            if let Some(suffix) = placeholder.strip_prefix(&prefix) {
                if let Ok(value) = suffix.parse::<usize>() {
                    max_suffix = max_suffix.max(value);
                }
            }
        }
        if max_suffix == 0 {
            base
        } else {
            format!("{base} #{}", max_suffix + 1)
        }
    }

    fn sync_pending_pastes_with_elements(&mut self) {
        if self.pending_pastes.is_empty() {
            return;
        }
        let mut live_counts: HashMap<String, usize> = HashMap::new();
        for payload in self.textarea.element_payloads() {
            *live_counts.entry(payload).or_default() += 1;
        }
        self.pending_pastes.retain(|(placeholder, _)| {
            let Some(count) = live_counts.get_mut(placeholder) else {
                return false;
            };
            if *count == 0 {
                return false;
            }
            *count -= 1;
            true
        });
    }

    fn expand_pending_pastes(
        text: &str,
        mut elements: Vec<TextElementSnapshot>,
        pending_pastes: &[(String, String)],
    ) -> String {
        if pending_pastes.is_empty() || elements.is_empty() {
            return text.to_string();
        }
        let mut pending_by_placeholder: HashMap<&str, VecDeque<&str>> = HashMap::new();
        for (placeholder, actual) in pending_pastes {
            pending_by_placeholder
                .entry(placeholder.as_str())
                .or_default()
                .push_back(actual.as_str());
        }
        elements.sort_by_key(|element| element.range.start);
        let mut rebuilt = String::with_capacity(text.len());
        let mut cursor = 0usize;
        for element in elements {
            let start = element.range.start.min(text.len());
            let end = element.range.end.min(text.len());
            if start > end {
                continue;
            }
            if start > cursor {
                rebuilt.push_str(&text[cursor..start]);
            }
            if let Some(actual) = pending_by_placeholder
                .get_mut(element.text.as_str())
                .and_then(VecDeque::pop_front)
            {
                rebuilt.push_str(actual);
            } else {
                rebuilt.push_str(&text[start..end]);
            }
            cursor = end;
        }
        if cursor < text.len() {
            rebuilt.push_str(&text[cursor..]);
        }
        rebuilt
    }

    fn reset_textarea_scroll(&mut self) {
        self.textarea_state.replace(TextAreaState::default());
    }
}

fn normalize_paste_newlines(pasted: &str) -> String {
    pasted.replace("\r\n", "\n").replace('\r', "\n")
}

/// Local equivalent of Codex `image_paste_enabled` (chat_composer.rs:319-325).
/// Enabled by default; set OWNPI_IMAGE_PASTE=0 to disable.
fn image_paste_enabled() -> bool {
    std::env::var("OWNPI_IMAGE_PASTE")
        .map(|value| value != "0")
        .unwrap_or(true)
}

const IMAGE_PASTE_EXTENSIONS: [&str; 6] = ["png", "jpg", "jpeg", "gif", "webp", "bmp"];

/// A small paste attaches an image only when it is a single existing file
/// path with a known image extension (Codex `handle_paste_image_path`).
/// Dimension/format validation happens later at serialization time (PIL).
fn looks_like_remote_image_url(pasted: &str) -> bool {
    let candidate = pasted.trim();
    if candidate.contains('\n') || candidate.chars().any(char::is_whitespace) {
        return false;
    }
    let is_http = candidate.starts_with("http://") || candidate.starts_with("https://");
    if !is_http {
        return false;
    }
    let without_query = candidate
        .split(['?', '#'])
        .next()
        .unwrap_or(candidate)
        .to_ascii_lowercase();
    IMAGE_PASTE_EXTENSIONS
        .iter()
        .any(|extension| without_query.ends_with(&format!(".{extension}")))
}

fn looks_like_image_path(pasted: &str) -> bool {
    let candidate = pasted.trim();
    if candidate.is_empty()
        || candidate.contains('\n')
        || candidate.chars().any(char::is_whitespace)
    {
        return false;
    }
    let expanded = expand_tilde(candidate);
    let path = std::path::Path::new(&expanded);
    if !path.is_file() {
        return false;
    }
    path.extension()
        .and_then(|extension| extension.to_str())
        .map(|extension| {
            IMAGE_PASTE_EXTENSIONS
                .iter()
                .any(|known| extension.eq_ignore_ascii_case(known))
        })
        .unwrap_or(false)
}

fn expand_tilde(candidate: &str) -> String {
    if candidate == "~" || candidate.starts_with("~/") {
        if let Ok(home) = std::env::var("HOME") {
            return format!("{home}{}", &candidate[1..]);
        }
    }
    candidate.to_string()
}

fn textarea_rect_for_area(area: Rect) -> Rect {
    Rect {
        x: area.x,
        y: area.y,
        width: area.width.max(1),
        height: area.height,
    }
}

fn highlight_spans(segment: &str, query: &str, palette: BottomPanePalette) -> Vec<Span<'static>> {
    if query.is_empty() || segment.is_empty() {
        return vec![Span::styled(
            segment.to_string(),
            Style::default().fg(palette.input_text),
        )];
    }
    let lower_segment = segment.to_lowercase();
    let lower_query = query.to_lowercase();
    let mut ranges = Vec::new();
    let mut search_from = 0usize;
    while search_from <= lower_segment.len() {
        let Some(relative_start) = lower_segment[search_from..].find(&lower_query) else {
            break;
        };
        let start = search_from + relative_start;
        let end = start.saturating_add(lower_query.len()).min(segment.len());
        if !segment.is_char_boundary(start) || !segment.is_char_boundary(end) {
            return vec![Span::styled(
                segment.to_string(),
                Style::default().fg(palette.input_text),
            )];
        }
        ranges.push(start..end);
        search_from = end.max(start + 1);
    }
    if ranges.is_empty() {
        return vec![Span::styled(
            segment.to_string(),
            Style::default().fg(palette.input_text),
        )];
    }
    let mut spans = Vec::new();
    let mut cursor = 0usize;
    for range in ranges {
        if range.start > cursor {
            spans.push(Span::styled(
                segment[cursor..range.start].to_string(),
                Style::default().fg(palette.input_text),
            ));
        }
        spans.push(Span::styled(
            segment[range.clone()].to_string(),
            Style::default()
                .fg(palette.input_accent)
                .add_modifier(Modifier::BOLD),
        ));
        cursor = range.end;
    }
    if cursor < segment.len() {
        spans.push(Span::styled(
            segment[cursor..].to_string(),
            Style::default().fg(palette.input_text),
        ));
    }
    spans
}

#[derive(Clone, Debug)]
struct HistorySearchSession {
    original_text: String,
    original_text_elements: Vec<TextElementSnapshot>,
    original_pending_pastes: Vec<(String, String)>,
    query: String,
    status: HistorySearchStatus,
    current_match_text: Option<String>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum HistorySearchStatus {
    Idle,
    Searching,
    Match,
    NoMatch,
}

fn clamp_to_char_boundary(text: &str, pos: usize) -> usize {
    let mut cursor = pos.min(text.len());
    while cursor > 0 && !text.is_char_boundary(cursor) {
        cursor -= 1;
    }
    cursor
}

#[cfg(test)]
mod tests {
    use super::*;
    use crossterm::event::KeyEvent;
    use ratatui::buffer::Buffer;
    use ratatui::style::Color;

    fn palette() -> BottomPanePalette {
        BottomPanePalette {
            input_text: Color::White,
            input_muted: Color::DarkGray,
            input_accent: Color::Green,
            input_bg: Color::Black,
        }
    }

    #[test]
    fn supports_long_paste_then_backspace() {
        let mut composer = ChatComposer::default();
        composer.insert_paste("first\nsecond");
        composer.handle_key(KeyEvent::new(KeyCode::Char('x'), KeyModifiers::NONE));
        composer.flush_paste_burst();
        composer.handle_key(KeyEvent::new(KeyCode::Backspace, KeyModifiers::NONE));

        assert_eq!(composer.text(), "first\nsecond");
    }

    #[test]
    fn paste_normalizes_cr_and_crlf_newlines() {
        let mut composer = ChatComposer::default();
        composer.insert_paste("first\r\nsecond\rthird");

        assert_eq!(composer.text(), "first\nsecond\nthird");
    }

    #[test]
    fn large_paste_uses_placeholder_and_expands_current_text() {
        let mut composer = ChatComposer::default();
        let large = "x".repeat(LARGE_PASTE_CHAR_THRESHOLD + 1);

        composer.insert_paste(&large);

        let placeholder = format!("[Pasted Content {} chars]", large.chars().count());
        assert_eq!(composer.text(), placeholder);
        assert_eq!(composer.pending_pastes.len(), 1);
        assert_eq!(composer.pending_pastes[0], (placeholder, large.clone()));
        assert_eq!(composer.current_text_with_pending(), large);
    }

    #[test]
    fn paste_at_large_threshold_inserts_direct_text() {
        let mut composer = ChatComposer::default();
        let text = "x".repeat(LARGE_PASTE_CHAR_THRESHOLD);

        composer.insert_paste(&text);

        assert_eq!(composer.text(), text);
        assert!(composer.pending_pastes.is_empty());
    }

    #[test]
    fn duplicate_large_paste_placeholders_get_suffixes_and_expand_in_order() {
        let mut composer = ChatComposer::default();
        let first = "x".repeat(LARGE_PASTE_CHAR_THRESHOLD + 4);
        let second = "y".repeat(LARGE_PASTE_CHAR_THRESHOLD + 4);

        composer.insert_paste(&first);
        composer.insert_paste(" and ");
        composer.insert_paste(&second);

        let base = format!("[Pasted Content {} chars]", first.chars().count());
        let suffixed = format!("{base} #2");
        assert_eq!(composer.pending_pastes[0].0, base);
        assert_eq!(composer.pending_pastes[1].0, suffixed);
        assert_eq!(
            composer.text(),
            format!(
                "{} and {}",
                composer.pending_pastes[0].0, composer.pending_pastes[1].0
            )
        );
        assert_eq!(
            composer.current_text_with_pending(),
            format!("{first} and {second}")
        );
    }

    #[test]
    fn paste_burst_flush_normalizes_cr_newlines() {
        let mut composer = ChatComposer::default();
        let now = Instant::now();
        assert_eq!(
            composer.paste_burst.on_plain_char('a', now),
            CharDecision::RetainFirstChar
        );
        assert_eq!(
            composer
                .paste_burst
                .on_plain_char('b', now + PasteBurst::recommended_flush_delay() / 2),
            CharDecision::BeginBufferFromPending
        );
        composer.paste_burst.append_char_to_buffer('b');
        composer.paste_burst.append_char_to_buffer('\r');
        composer.paste_burst.append_char_to_buffer('c');

        composer.flush_paste_burst();

        assert_eq!(composer.text(), "ab\nc");
    }

    #[test]
    fn fast_key_paste_burst_enter_preserves_line_order() {
        let mut composer = ChatComposer::default();

        for key in [
            KeyEvent::new(KeyCode::Char('a'), KeyModifiers::NONE),
            KeyEvent::new(KeyCode::Char('b'), KeyModifiers::NONE),
            KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE),
            KeyEvent::new(KeyCode::Char('c'), KeyModifiers::NONE),
        ] {
            let _ = composer.handle_key(key);
        }
        composer.flush_paste_burst();

        assert_eq!(composer.text(), "ab\nc");
    }

    #[test]
    fn fast_ascii_digits_are_transient_paste_burst_until_flush() {
        let mut composer = ChatComposer::default();

        let _ = composer.handle_key(KeyEvent::new(KeyCode::Char('1'), KeyModifiers::NONE));
        assert!(composer.is_in_paste_burst());
        let _ = composer.handle_key(KeyEvent::new(KeyCode::Char('2'), KeyModifiers::NONE));
        assert!(composer.is_in_paste_burst());
        assert_eq!(composer.text(), "");

        std::thread::sleep(PasteBurst::recommended_flush_delay());
        assert!(composer.flush_paste_burst_if_due());

        assert!(!composer.is_in_paste_burst());
        assert_eq!(composer.text(), "12");
    }

    #[test]
    fn fast_key_paste_burst_large_flush_inserts_placeholder() {
        let mut composer = ChatComposer::default();
        let count = LARGE_PASTE_CHAR_THRESHOLD + 1;

        for _ in 0..count {
            let _ = composer.handle_key(KeyEvent::new(KeyCode::Char('x'), KeyModifiers::NONE));
        }
        composer.flush_paste_burst();

        let placeholder = format!("[Pasted Content {count} chars]");
        assert_eq!(composer.text(), placeholder);
        assert_eq!(composer.pending_pastes.len(), 1);
        assert_eq!(composer.current_text_with_pending(), "x".repeat(count));
    }

    #[test]
    fn deleting_large_paste_placeholder_drops_pending_payload() {
        let mut composer = ChatComposer::default();
        let large = "x".repeat(LARGE_PASTE_CHAR_THRESHOLD + 1);
        composer.insert_paste(&large);

        composer.handle_key(KeyEvent::new(KeyCode::Backspace, KeyModifiers::NONE));

        assert_eq!(composer.text(), "");
        assert!(composer.pending_pastes.is_empty());
        assert_eq!(composer.current_text_with_pending(), "");
    }

    #[test]
    fn deleting_one_duplicate_large_paste_placeholder_drops_only_that_payload() {
        let mut composer = ChatComposer::default();
        let first = "x".repeat(LARGE_PASTE_CHAR_THRESHOLD + 4);
        let second = "y".repeat(LARGE_PASTE_CHAR_THRESHOLD + 4);
        composer.insert_paste(&first);
        composer.insert_paste(" and ");
        composer.insert_paste(&second);
        let base = format!("[Pasted Content {} chars]", first.chars().count());
        let suffixed = format!("{base} #2");

        composer.textarea.set_cursor(base.len());
        composer.handle_key(KeyEvent::new(KeyCode::Backspace, KeyModifiers::NONE));

        assert_eq!(composer.text(), format!(" and {suffixed}"));
        assert_eq!(composer.pending_pastes, vec![(suffixed, second.clone())]);
        assert_eq!(
            composer.current_text_with_pending(),
            format!(" and {second}")
        );
    }

    #[test]
    fn history_recall_preserves_large_paste_payload() {
        let mut composer = ChatComposer::default();
        let large = "x".repeat(LARGE_PASTE_CHAR_THRESHOLD + 1);
        composer.insert_paste(&large);
        let visible = composer.text().to_string();
        composer.record_current_submission();
        composer.clear();

        composer.handle_key(KeyEvent::new(KeyCode::Up, KeyModifiers::NONE));

        assert_eq!(composer.text(), visible);
        assert_eq!(composer.current_text_with_pending(), large);
    }

    #[test]
    fn history_recall_uses_element_range_not_first_matching_placeholder_text() {
        let mut composer = ChatComposer::default();
        let large = "x".repeat(LARGE_PASTE_CHAR_THRESHOLD + 1);
        let placeholder = format!("[Pasted Content {} chars]", large.chars().count());
        composer.insert_paste(&format!("{placeholder} typed "));
        composer.insert_paste(&large);
        let visible = composer.text().to_string();
        composer.record_current_submission();
        composer.clear();

        composer.handle_key(KeyEvent::new(KeyCode::Up, KeyModifiers::NONE));

        assert_eq!(composer.text(), visible);
        assert_eq!(
            composer.current_text_with_pending(),
            format!("{placeholder} typed {large}")
        );
    }

    #[test]
    fn backspace_after_multiline_paste_removes_trailing_newline() {
        let mut composer = ChatComposer::default();
        composer.insert_paste("first\nsecond\n");

        composer.handle_key(KeyEvent::new(KeyCode::Backspace, KeyModifiers::NONE));

        assert_eq!(composer.text(), "first\nsecond");
        assert_eq!(composer.textarea.cursor(), "first\nsecond".len());
    }

    #[test]
    fn delete_control_chars_after_multiline_paste_remove_trailing_newline() {
        for code in [KeyCode::Char('\u{007f}'), KeyCode::Char('\u{0008}')] {
            let mut composer = ChatComposer::default();
            composer.insert_paste("first\nsecond\n");

            composer.handle_key(KeyEvent::new(code, KeyModifiers::NONE));

            assert_eq!(composer.text(), "first\nsecond");
            assert_eq!(composer.textarea.cursor(), "first\nsecond".len());
        }
    }

    #[test]
    fn records_and_recalls_history() {
        let mut composer = ChatComposer::default();
        composer.record_submission("hello");
        composer.handle_key(KeyEvent::new(KeyCode::Up, KeyModifiers::NONE));

        assert_eq!(composer.text(), "hello");
    }

    #[test]
    fn walks_history_with_up_and_down() {
        let mut composer = ChatComposer::default();
        composer.record_submission("first");
        composer.record_submission("second");

        composer.handle_key(KeyEvent::new(KeyCode::Up, KeyModifiers::NONE));
        assert_eq!(composer.text(), "second");
        composer.handle_key(KeyEvent::new(KeyCode::Up, KeyModifiers::NONE));
        assert_eq!(composer.text(), "first");
        composer.handle_key(KeyEvent::new(KeyCode::Down, KeyModifiers::NONE));
        assert_eq!(composer.text(), "second");
        composer.handle_key(KeyEvent::new(KeyCode::Down, KeyModifiers::NONE));
        assert_eq!(composer.text(), "");
    }

    #[test]
    fn multiline_draft_up_does_not_recall_history() {
        let mut composer = ChatComposer::default();
        composer.record_submission("remembered");
        composer.insert_paste("first\nsecond");

        composer.handle_key(KeyEvent::new(KeyCode::Up, KeyModifiers::NONE));

        assert_eq!(composer.text(), "first\nsecond");
    }

    #[test]
    fn edited_recalled_text_stops_history_navigation() {
        let mut composer = ChatComposer::default();
        composer.record_submission("first");
        composer.record_submission("second");
        composer.handle_key(KeyEvent::new(KeyCode::Up, KeyModifiers::NONE));
        composer.handle_key(KeyEvent::new(KeyCode::Char('x'), KeyModifiers::NONE));
        composer.flush_paste_burst();

        composer.handle_key(KeyEvent::new(KeyCode::Up, KeyModifiers::NONE));

        assert_eq!(composer.text(), "secondx");
    }

    #[test]
    fn ctrl_r_search_opens_without_replacing_draft() {
        let mut composer = ChatComposer::default();
        composer.record_submission("hello");
        composer.insert_paste("draft");

        composer.handle_key(KeyEvent::new(KeyCode::Char('r'), KeyModifiers::CONTROL));

        assert_eq!(composer.text(), "draft");
    }

    #[test]
    fn ctrl_r_search_previews_accepts_and_cancels_match() {
        let mut composer = ChatComposer::default();
        composer.record_submission("hello world");
        composer.insert_paste("draft");

        composer.handle_key(KeyEvent::new(KeyCode::Char('r'), KeyModifiers::CONTROL));
        composer.handle_key(KeyEvent::new(KeyCode::Char('h'), KeyModifiers::NONE));
        assert_eq!(composer.text(), "hello world");
        composer.handle_key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE));
        assert_eq!(composer.text(), "draft");

        composer.handle_key(KeyEvent::new(KeyCode::Char('r'), KeyModifiers::CONTROL));
        composer.handle_key(KeyEvent::new(KeyCode::Char('h'), KeyModifiers::NONE));
        composer.handle_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
        assert_eq!(composer.text(), "hello world");
    }

    #[test]
    fn flushes_single_typed_char_after_hold_window() {
        let mut composer = ChatComposer::default();
        composer.handle_key(KeyEvent::new(KeyCode::Char('a'), KeyModifiers::NONE));
        std::thread::sleep(PasteBurst::recommended_flush_delay());

        assert!(composer.flush_paste_burst_if_due());
        assert_eq!(composer.text(), "a");
    }

    #[test]
    fn cursor_position_accounts_for_edge_bar() {
        let mut composer = ChatComposer::default();
        composer.insert_paste("/");
        let position = composer.cursor_pos(Rect {
            x: 2,
            y: 10,
            width: 20,
            height: 2,
        });

        // 2 (outer x) + 2 (edge bar) + 1 (typed char)
        assert_eq!(position, Some((5, 10)));
    }

    #[test]
    fn cursor_position_scrolls_to_multiline_paste_end() {
        let mut composer = ChatComposer::default();
        composer.insert_paste("one\ntwo\nthree\nfour");
        let area = Rect {
            x: 0,
            y: 0,
            width: 20,
            height: 2,
        };
        let mut buf = Buffer::empty(area);

        composer.render(area, &mut buf, &ThemePalette::default(), palette());

        // 2 (edge bar) + visible tail of the paste
        assert_eq!(composer.cursor_pos(area), Some((6, 1)));
        let rendered = (0..area.height)
            .map(|y| {
                (0..area.width)
                    .map(|x| buf.get(x, y).symbol())
                    .collect::<String>()
            })
            .collect::<Vec<_>>()
            .join("\n");
        assert!(!rendered.contains("one"), "rendered={rendered:?}");
        assert!(rendered.contains("three"), "rendered={rendered:?}");
        assert!(rendered.contains("four"), "rendered={rendered:?}");
    }

    #[test]
    fn cursor_position_uses_wrapped_textarea_width() {
        let mut composer = ChatComposer::default();
        composer.insert_paste("123456789");
        let area = Rect {
            x: 0,
            y: 0,
            width: 7,
            height: 4,
        };

        // inner width 5 after the edge bar; 9 chars wrap to 2 rows
        assert_eq!(composer.cursor_pos(area), Some((6, 1)));
    }

    #[test]
    fn ctrl_j_inserts_newline() {
        let mut composer = ChatComposer::default();
        composer.insert_paste("hello");

        let action = composer.handle_key(KeyEvent::new(KeyCode::Char('j'), KeyModifiers::CONTROL));

        assert!(action);
        assert_eq!(composer.text(), "hello\n");
    }

    #[test]
    fn ctrl_j_inserts_newline_after_flushing_fast_typed_burst() {
        let mut composer = ChatComposer::default();
        for ch in ['h', 'e', 'l', 'l', 'o'] {
            let _ = composer.handle_key(KeyEvent::new(KeyCode::Char(ch), KeyModifiers::NONE));
        }

        let action = composer.handle_key(KeyEvent::new(KeyCode::Char('j'), KeyModifiers::CONTROL));

        assert!(action);
        assert_eq!(composer.text(), "hello\n");
    }

    #[test]
    fn enter_submits_single_line_slash_command_even_during_fast_burst() {
        let mut composer = ChatComposer::default();
        for ch in "/run-task temp/live_specs/live_missing_dataset_resume.yaml".chars() {
            let _ = composer.handle_key(KeyEvent::new(KeyCode::Char(ch), KeyModifiers::NONE));
        }

        let action = composer.handle_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));

        assert!(!action);
        assert_eq!(
            composer.text(),
            "/run-task temp/live_specs/live_missing_dataset_resume.yaml"
        );
    }

    #[test]
    fn expired_paste_burst_is_not_dropped_on_next_key() {
        let mut composer = ChatComposer::default();
        for ch in "/run-task temp/live_specs/live_missing_dataset_r".chars() {
            let _ = composer.handle_key(KeyEvent::new(KeyCode::Char(ch), KeyModifiers::NONE));
        }
        std::thread::sleep(PasteBurst::recommended_flush_delay());
        for ch in "esume.yaml".chars() {
            let _ = composer.handle_key(KeyEvent::new(KeyCode::Char(ch), KeyModifiers::NONE));
        }

        let action = composer.handle_key(KeyEvent::new(KeyCode::Char('j'), KeyModifiers::CONTROL));

        assert!(action);
        assert_eq!(
            composer.text(),
            "/run-task temp/live_specs/live_missing_dataset_resume.yaml\n"
        );
    }

    #[test]
    fn history_search_highlights_all_matching_text() {
        let mut composer = ChatComposer::default();
        composer.record_submission("cargo check cargo test");

        composer.handle_key(KeyEvent::new(KeyCode::Char('r'), KeyModifiers::CONTROL));
        composer.handle_key(KeyEvent::new(KeyCode::Char('c'), KeyModifiers::NONE));
        composer.handle_key(KeyEvent::new(KeyCode::Char('a'), KeyModifiers::NONE));

        let rendered = composer.render_text(&ThemePalette::default(), palette());
        let first_line = &rendered.lines[0];

        assert_eq!(first_line.spans[0].content, "ca");
        assert_eq!(first_line.spans[0].style.fg, Some(Color::Green));
        assert!(first_line.spans[0]
            .style
            .add_modifier
            .contains(Modifier::BOLD));
        assert_eq!(first_line.spans[1].content, "rgo check ");
        assert_eq!(first_line.spans[2].content, "ca");
        assert_eq!(first_line.spans[2].style.fg, Some(Color::Green));
        assert!(first_line.spans[2]
            .style
            .add_modifier
            .contains(Modifier::BOLD));
        assert_eq!(first_line.spans[3].content, "rgo test");
    }

    #[test]
    fn history_search_highlights_unicode_query_on_character_boundaries() {
        let mut composer = ChatComposer::default();
        composer.record_submission("分析数据 数据质量");

        composer.handle_key(KeyEvent::new(KeyCode::Char('r'), KeyModifiers::CONTROL));
        composer.handle_key(KeyEvent::new(KeyCode::Char('数'), KeyModifiers::NONE));
        composer.handle_key(KeyEvent::new(KeyCode::Char('据'), KeyModifiers::NONE));

        let rendered = composer.render_text(&ThemePalette::default(), palette());
        let first_line = &rendered.lines[0];

        assert_eq!(first_line.spans[0].content, "分析");
        assert_eq!(first_line.spans[1].content, "数据");
        assert_eq!(first_line.spans[1].style.fg, Some(Color::Green));
        assert!(first_line.spans[1]
            .style
            .add_modifier
            .contains(Modifier::BOLD));
        assert_eq!(first_line.spans[2].content, " ");
        assert_eq!(first_line.spans[3].content, "数据");
        assert_eq!(first_line.spans[3].style.fg, Some(Color::Green));
        assert!(first_line.spans[3]
            .style
            .add_modifier
            .contains(Modifier::BOLD));
        assert_eq!(first_line.spans[4].content, "质量");
    }

    #[test]
    fn applying_history_entry_moves_cursor_to_end() {
        let mut composer = ChatComposer::default();

        composer.apply_history_entry(HistoryEntry::with_cursor("hello world", "hello".len()));

        assert_eq!(composer.text(), "hello world");
        assert_eq!(composer.textarea.cursor(), "hello world".len());
    }
}

#[cfg(test)]
mod image_attachment_tests {
    use super::*;
    use std::io::Write;

    fn temp_image_path(name: &str) -> std::path::PathBuf {
        let path = std::env::temp_dir().join(name);
        let mut file = std::fs::File::create(&path).expect("create temp image");
        file.write_all(b"\x89PNG\r\n\x1a\nfake").expect("write");
        path
    }

    #[test]
    fn attach_local_image_inserts_atomic_placeholder_and_records_path() {
        let mut composer = ChatComposer::default();
        let path = temp_image_path("neuro_attach_1.png");

        let placeholder = composer.attach_local_image(path.clone());

        assert_eq!(placeholder, "[Image #1]");
        assert_eq!(composer.text(), "[Image #1]");
        assert_eq!(composer.local_images().len(), 1);
        assert_eq!(composer.local_images()[0].path, path);
        assert_eq!(
            composer
                .textarea
                .text_element_snapshots()
                .iter()
                .map(|snapshot| snapshot.text.as_str())
                .collect::<Vec<_>>(),
            vec!["[Image #1]"]
        );
    }

    #[test]
    fn deleting_placeholder_prunes_and_renumbers_attachments() {
        let mut composer = ChatComposer::default();
        let first = temp_image_path("neuro_attach_2a.png");
        let second = temp_image_path("neuro_attach_2b.png");
        composer.attach_local_image(first);
        composer.attach_local_image(second.clone());
        assert_eq!(composer.text(), "[Image #1][Image #2]");

        // Delete the first placeholder atomically (cursor starts after both).
        composer.textarea.set_cursor("[Image #1]".len());
        composer
            .textarea
            .input(KeyEvent::new(KeyCode::Backspace, KeyModifiers::NONE));
        composer.sync_image_attachments_with_elements();

        // Codex contiguous-numbering rule: the survivor is relabeled to #1.
        assert_eq!(composer.text(), "[Image #1]");
        assert_eq!(composer.local_images().len(), 1);
        assert_eq!(composer.local_images()[0].path, second);
        assert_eq!(composer.local_images()[0].placeholder, "[Image #1]");
    }

    #[test]
    fn record_current_submission_stores_local_image_paths() {
        let mut composer = ChatComposer::default();
        let path = temp_image_path("neuro_attach_3.png");
        composer.attach_local_image(path.clone());

        composer.record_current_submission();

        let entry = composer
            .history
            .latest_entry_for_test()
            .expect("history entry");
        assert_eq!(
            entry.local_image_paths,
            vec![path.to_string_lossy().to_string()]
        );
        assert!(entry.text.contains("[Image #1]"));
    }

    #[test]
    fn history_recall_restores_image_attachments() {
        let mut composer = ChatComposer::default();
        let path = temp_image_path("neuro_attach_4.png");
        composer.attach_local_image(path.clone());
        composer.record_current_submission();
        let entry = composer
            .history
            .latest_entry_for_test()
            .expect("history entry");
        let mut restored = ChatComposer::default();

        restored.apply_history_entry(entry);

        assert_eq!(restored.local_images().len(), 1);
        assert_eq!(restored.local_images()[0].path, path);
        assert_eq!(restored.local_images()[0].placeholder, "[Image #1]");
    }

    #[test]
    fn paste_of_image_path_attaches_instead_of_inserting_text() {
        let mut composer = ChatComposer::default();
        let path = temp_image_path("neuro_attach_5.png");

        composer.insert_paste(&path.to_string_lossy());

        assert_eq!(composer.text(), "[Image #1]");
        assert_eq!(composer.local_images().len(), 1);
    }

    #[test]
    fn paste_of_missing_image_path_falls_back_to_plain_text() {
        let mut composer = ChatComposer::default();

        composer.insert_paste("/definitely/missing/image.png");

        assert_eq!(composer.text(), "/definitely/missing/image.png");
        assert!(composer.local_images().is_empty());
    }

    #[test]
    fn paste_of_non_image_text_is_unchanged() {
        let mut composer = ChatComposer::default();

        composer.insert_paste("hello world");

        assert_eq!(composer.text(), "hello world");
        assert!(composer.local_images().is_empty());
    }
}

#[cfg(test)]
mod remote_image_tests {
    use super::*;

    #[test]
    fn remote_url_paste_attaches_remote_image() {
        let mut composer = ChatComposer::default();

        composer.insert_paste("https://example.com/diagram.png");

        assert_eq!(composer.text(), "[Image #1]");
        assert_eq!(composer.remote_images.len(), 1);
        assert_eq!(
            composer.remote_images[0].url,
            "https://example.com/diagram.png"
        );
        assert!(composer.local_images().is_empty());
    }

    #[test]
    fn remote_and_local_share_contiguous_numbering() {
        let mut composer = ChatComposer::default();
        let path = std::env::temp_dir().join("neuro_remote_mix.png");
        std::fs::write(&path, b"\x89PNG\r\n\x1a\nfake").expect("write");

        composer.attach_local_image(path);
        composer.insert_paste("https://example.com/photo.jpg");

        assert_eq!(composer.text(), "[Image #1][Image #2]");
        assert_eq!(composer.remote_images[0].placeholder, "[Image #2]");
    }

    #[test]
    fn non_image_url_is_plain_text() {
        let mut composer = ChatComposer::default();

        composer.insert_paste("https://example.com/article");

        assert_eq!(composer.text(), "https://example.com/article");
        assert!(composer.remote_images.is_empty());
    }
}
