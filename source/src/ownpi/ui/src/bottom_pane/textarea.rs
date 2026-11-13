use std::cell::RefCell;
use std::ops::Range;

use crate::keymap::{DefaultKeymap, TextareaAction};
use crossterm::event::{KeyCode, KeyEvent, KeyEventKind, KeyModifiers};
use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::Style;
use unicode_segmentation::UnicodeSegmentation;
use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

const WORD_SEPARATORS: &str = "`~!@#$%^&*()-=+[{]}\\|;:'\",.<>/?";

#[derive(Debug, Default)]
pub(crate) struct TextArea {
    text: String,
    cursor_pos: usize,
    wrap_cache: RefCell<Option<WrapCache>>,
    preferred_col: Option<usize>,
    elements: Vec<TextElement>,
    next_element_id: u64,
    kill_buffer: String,
    vim_enabled: bool,
    vim_mode: VimMode,
    vim_pending_operator: Option<char>,
    vim_yank: String,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub(crate) enum VimMode {
    #[default]
    Insert,
    Normal,
    OperatorPending,
}

#[derive(Debug, Clone)]
struct TextElement {
    id: u64,
    range: Range<usize>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct TextElementSnapshot {
    pub(crate) range: Range<usize>,
    pub(crate) text: String,
}

#[derive(Debug, Clone)]
struct WrapCache {
    width: u16,
    lines: Vec<Range<usize>>,
}

#[derive(Debug, Default, Clone, Copy)]
pub(crate) struct TextAreaState {
    pub(crate) scroll: u16,
}

impl TextArea {
    pub(crate) fn text(&self) -> &str {
        &self.text
    }

    pub(crate) fn cursor(&self) -> usize {
        self.cursor_pos
    }

    pub(crate) fn is_empty(&self) -> bool {
        self.text.is_empty()
    }

    pub(crate) fn set_text(&mut self, text: impl Into<String>) {
        self.text = text.into();
        self.cursor_pos = self.text.len();
        self.elements.clear();
        self.cursor_pos = self.clamp_pos_to_nearest_boundary(self.cursor_pos);
        self.wrap_cache.replace(None);
        self.preferred_col = None;
    }

    pub(crate) fn set_text_with_element_snapshots(
        &mut self,
        text: impl Into<String>,
        elements: &[TextElementSnapshot],
    ) {
        self.text = text.into();
        self.cursor_pos = self.text.len();
        self.elements.clear();
        for element in elements {
            let start = self.clamp_pos_to_char_boundary(element.range.start.min(self.text.len()));
            let end = self.clamp_pos_to_char_boundary(element.range.end.min(self.text.len()));
            if start >= end {
                continue;
            }
            if self.text.get(start..end) == Some(element.text.as_str()) {
                self.add_element(start..end);
            }
        }
        self.cursor_pos = self.clamp_pos_to_nearest_boundary(self.cursor_pos);
        self.wrap_cache.replace(None);
        self.preferred_col = None;
    }

    pub(crate) fn clear(&mut self) {
        self.text.clear();
        self.cursor_pos = 0;
        self.elements.clear();
        self.wrap_cache.replace(None);
        self.preferred_col = None;
    }

    pub(crate) fn insert_char(&mut self, ch: char) {
        let mut buffer = [0; 4];
        self.insert_str(ch.encode_utf8(&mut buffer));
    }

    pub(crate) fn insert_str(&mut self, text: &str) {
        if text.is_empty() {
            return;
        }
        let pos = self.clamp_pos_for_insertion(self.cursor_pos);
        self.text.insert_str(pos, text);
        self.cursor_pos = pos + text.len();
        self.shift_elements(pos, 0, text.len());
        self.wrap_cache.replace(None);
        self.preferred_col = None;
    }

    pub(crate) fn replace_range(&mut self, range: Range<usize>, text: &str) {
        let range = self.expand_range_to_element_boundaries(range);
        let start = self.clamp_pos_to_char_boundary(range.start.min(self.text.len()));
        let end = self.clamp_pos_to_char_boundary(range.end.min(self.text.len()));
        if start > end {
            return;
        }
        self.text.replace_range(start..end, text);
        self.cursor_pos = start + text.len();
        self.shift_elements(start, end.saturating_sub(start), text.len());
        self.cursor_pos = self.clamp_pos_to_nearest_boundary(self.cursor_pos);
        self.wrap_cache.replace(None);
        self.preferred_col = None;
    }

    pub(crate) fn insert_element(&mut self, text: &str) {
        if text.is_empty() {
            return;
        }
        let start = self.clamp_pos_for_insertion(self.cursor_pos);
        self.text.insert_str(start, text);
        let end = start + text.len();
        self.shift_elements(start, 0, text.len());
        self.add_element(start..end);
        self.cursor_pos = end;
        self.wrap_cache.replace(None);
        self.preferred_col = None;
    }

    pub(crate) fn element_payloads(&self) -> Vec<String> {
        self.elements
            .iter()
            .filter_map(|element| self.text.get(element.range.clone()).map(str::to_string))
            .collect()
    }

    /// Replaces the visible text of the element currently displaying `old` with
    /// `new`, keeping it an atomic element. Used to relabel `[Image #N]`
    /// placeholders after a sibling attachment is pruned (Codex
    /// attachment_state.rs contiguous-numbering rule).
    pub(crate) fn retag_element(&mut self, old: &str, new: &str) -> bool {
        if old == new || new.is_empty() {
            return false;
        }
        let Some(range) = self
            .elements
            .iter()
            .find(|element| self.text.get(element.range.clone()) == Some(old))
            .map(|element| element.range.clone())
        else {
            return false;
        };
        let removed = range.end.saturating_sub(range.start);
        self.text.replace_range(range.clone(), new);
        self.shift_elements(range.start, removed, new.len());
        self.add_element(range.start..range.start + new.len());
        self.cursor_pos = self.clamp_pos_to_nearest_boundary(self.cursor_pos);
        self.wrap_cache.replace(None);
        true
    }

    pub(crate) fn text_element_snapshots(&self) -> Vec<TextElementSnapshot> {
        self.elements
            .iter()
            .filter_map(|element| {
                self.text
                    .get(element.range.clone())
                    .map(|text| TextElementSnapshot {
                        range: element.range.clone(),
                        text: text.to_string(),
                    })
            })
            .collect()
    }

    pub(crate) fn set_vim_enabled(&mut self, enabled: bool) {
        self.vim_enabled = enabled;
        if !enabled {
            self.vim_mode = VimMode::Insert;
            self.vim_pending_operator = None;
        }
    }

    pub(crate) fn vim_insert_mode_active(&self) -> bool {
        self.vim_enabled && self.vim_mode == VimMode::Insert
    }

    pub(crate) fn vim_is_enabled(&self) -> bool {
        self.vim_enabled
    }

    pub(crate) fn vim_enter_normal_mode(&mut self) {
        if self.vim_enabled {
            self.vim_mode = VimMode::Normal;
            self.vim_pending_operator = None;
        }
    }

    /// Codex vim mode parity (textarea.rs:92-360): modal editing with
    /// Normal/Insert/OperatorPending states. Returns true when the key was
    /// consumed by the vim layer.
    fn handle_vim_input(&mut self, event: KeyEvent) -> bool {
        if !self.vim_enabled || !matches!(event.kind, KeyEventKind::Press | KeyEventKind::Repeat) {
            return false;
        }
        match self.vim_mode {
            VimMode::Insert => {
                if event.code == KeyCode::Esc && event.modifiers.is_empty() {
                    self.vim_enter_normal_mode();
                    return true;
                }
                false
            }
            VimMode::Normal => self.handle_vim_normal_key(event),
            VimMode::OperatorPending => self.handle_vim_operator_key(event),
        }
    }

    fn handle_vim_normal_key(&mut self, event: KeyEvent) -> bool {
        if !event.modifiers.is_empty() {
            self.vim_pending_operator = None;
            return false;
        }
        match event.code {
            KeyCode::Char('i') => {
                self.vim_mode = VimMode::Insert;
            }
            KeyCode::Char('a') => {
                self.move_cursor_right();
                self.vim_mode = VimMode::Insert;
            }
            KeyCode::Char('o') => {
                self.move_cursor_to_end_of_line();
                self.insert_str("\n");
                self.vim_mode = VimMode::Insert;
            }
            KeyCode::Char('h') | KeyCode::Left => self.move_cursor_left(),
            KeyCode::Char('l') | KeyCode::Right => self.move_cursor_right(),
            KeyCode::Char('j') | KeyCode::Down => self.move_cursor_down(),
            KeyCode::Char('k') | KeyCode::Up => self.move_cursor_up(),
            KeyCode::Char('w') => {
                // vim w = start of next word: end of current word, then skip whitespace
                self.cursor_pos = self.end_of_next_word();
                while self.cursor_pos < self.text.len()
                    && self.text[self.cursor_pos..]
                        .chars()
                        .next()
                        .is_some_and(|ch| ch.is_whitespace())
                {
                    self.move_cursor_right();
                }
            }
            KeyCode::Char('b') => {
                self.cursor_pos = self.beginning_of_previous_word();
            }
            KeyCode::Char('0') => self.move_cursor_to_beginning_of_line(),
            KeyCode::Char('$') => self.move_cursor_to_end_of_line(),
            KeyCode::Char('x') => self.delete_forward(1),
            KeyCode::Char('d') => {
                self.vim_pending_operator = Some('d');
                self.vim_mode = VimMode::OperatorPending;
            }
            KeyCode::Char('y') => {
                self.vim_pending_operator = Some('y');
                self.vim_mode = VimMode::OperatorPending;
            }
            KeyCode::Char('p') => self.vim_paste(),
            KeyCode::Esc => {
                self.vim_pending_operator = None;
            }
            _ => return false,
        }
        self.wrap_cache.replace(None);
        true
    }

    fn handle_vim_operator_key(&mut self, event: KeyEvent) -> bool {
        let operator = self.vim_pending_operator.take();
        self.vim_mode = VimMode::Normal;
        match (operator, event.code) {
            (Some('d'), KeyCode::Char('d')) => {
                self.vim_yank = self.take_current_line();
            }
            (Some('y'), KeyCode::Char('y')) => {
                self.vim_yank = self.current_line_text();
            }
            _ => {}
        }
        self.wrap_cache.replace(None);
        true
    }

    fn current_line_range(&self) -> std::ops::Range<usize> {
        let start = self.text[..self.cursor_pos]
            .rfind('\n')
            .map_or(0, |idx| idx + 1);
        let end = self.text[self.cursor_pos..]
            .find('\n')
            .map_or(self.text.len(), |idx| self.cursor_pos + idx + 1);
        start..end
    }

    fn current_line_text(&self) -> String {
        self.text
            .get(self.current_line_range())
            .unwrap_or_default()
            .to_string()
    }

    fn take_current_line(&mut self) -> String {
        let range = self.current_line_range();
        let removed = self.text.get(range.clone()).unwrap_or_default().to_string();
        self.replace_range(range, "");
        removed
    }

    fn vim_paste(&mut self) {
        if self.vim_yank.is_empty() {
            return;
        }
        let yanked = self.vim_yank.clone();
        if yanked.contains('\n') {
            self.move_cursor_to_end_of_line();
            self.insert_str("\n");
            self.insert_str(yanked.trim_end_matches('\n'));
        } else {
            self.insert_str(&yanked);
        }
    }

    pub(crate) fn input(&mut self, event: KeyEvent) {
        if !matches!(event.kind, KeyEventKind::Press | KeyEventKind::Repeat) {
            return;
        }
        if self.handle_vim_input(event) {
            return;
        }
        match DefaultKeymap::textarea_action(event) {
            Some(TextareaAction::InsertNewline) => {
                self.insert_str("\n");
                return;
            }
            Some(TextareaAction::MoveLeft) => {
                self.move_cursor_left();
                return;
            }
            Some(TextareaAction::MoveRight) => {
                self.move_cursor_right();
                return;
            }
            Some(TextareaAction::MoveUp) => {
                self.move_cursor_up();
                return;
            }
            Some(TextareaAction::MoveDown) => {
                self.move_cursor_down();
                return;
            }
            Some(TextareaAction::DeleteBackward) => {
                self.delete_backward(1);
                return;
            }
            Some(TextareaAction::DeleteForward) => {
                self.delete_forward(1);
                return;
            }
            None => {}
        }
        match event {
            KeyEvent {
                code: KeyCode::Char('h'),
                modifiers: KeyModifiers::CONTROL,
                ..
            }
            | KeyEvent {
                code: KeyCode::Char('\u{0008}' | '\u{007f}'),
                ..
            } => self.delete_backward(1),
            KeyEvent {
                code: KeyCode::Char('w'),
                modifiers: KeyModifiers::CONTROL,
                ..
            } => self.delete_backward_word(),
            KeyEvent {
                code: KeyCode::Char('d'),
                modifiers: KeyModifiers::ALT,
                ..
            } => self.delete_forward_word(),
            KeyEvent {
                code: KeyCode::Char('u'),
                modifiers: KeyModifiers::CONTROL,
                ..
            } => self.kill_to_beginning_of_line(),
            KeyEvent {
                code: KeyCode::Char('k'),
                modifiers: KeyModifiers::CONTROL,
                ..
            } => self.kill_to_end_of_line(),
            KeyEvent {
                code: KeyCode::Char('y'),
                modifiers: KeyModifiers::CONTROL,
                ..
            } => self.yank(),
            KeyEvent {
                code: KeyCode::Left,
                modifiers: KeyModifiers::ALT,
                ..
            } => self.set_cursor(self.beginning_of_previous_word()),
            KeyEvent {
                code: KeyCode::Right,
                modifiers: KeyModifiers::ALT,
                ..
            } => self.set_cursor(self.end_of_next_word()),
            KeyEvent {
                code: KeyCode::Char('a'),
                modifiers: KeyModifiers::CONTROL,
                ..
            } => self.move_cursor_to_beginning_of_line(),
            KeyEvent {
                code: KeyCode::Char('e'),
                modifiers: KeyModifiers::CONTROL,
                ..
            } => self.move_cursor_to_end_of_line(),
            KeyEvent {
                code: KeyCode::Char(ch),
                modifiers,
                ..
            } if modifiers.is_empty() || modifiers == KeyModifiers::SHIFT => {
                if !ch.is_ascii_control() {
                    self.insert_char(ch);
                }
            }
            _ => {}
        }
    }

    pub(crate) fn desired_height(&self, width: u16) -> u16 {
        self.wrapped_lines(width).len().max(1) as u16
    }

    pub(crate) fn cursor_pos(&self, area: Rect, state: TextAreaState) -> Option<(u16, u16)> {
        let lines = self.wrapped_lines(area.width.max(1));
        let effective_scroll = self.effective_scroll(area.height, &lines, state.scroll);
        let index = Self::wrapped_line_index_by_start(&lines, self.cursor_pos)?;
        let line = &lines[index];
        let col = self.text[line.start..self.cursor_pos].width() as u16;
        let row = index.saturating_sub(effective_scroll as usize) as u16;
        Some((area.x + col.min(area.width.saturating_sub(1)), area.y + row))
    }

    pub(crate) fn render(
        &self,
        area: Rect,
        buf: &mut Buffer,
        state: &mut TextAreaState,
        style: Style,
    ) {
        if area.width == 0 || area.height == 0 {
            return;
        }
        let lines = self.wrapped_lines(area.width.max(1));
        let scroll = self.effective_scroll(area.height, &lines, state.scroll);
        state.scroll = scroll;
        let start = scroll as usize;
        let end = (scroll + area.height).min(lines.len() as u16) as usize;
        for (screen_row, line) in lines[start..end].iter().enumerate() {
            let y = area.y + screen_row as u16;
            let segment = &self.text[line.clone()];
            buf.set_stringn(area.x, y, segment, area.width as usize, style);
        }
    }

    pub(crate) fn render_masked(
        &self,
        area: Rect,
        buf: &mut Buffer,
        state: &mut TextAreaState,
        style: Style,
        mask_char: char,
    ) {
        if area.width == 0 || area.height == 0 {
            return;
        }
        let lines = self.wrapped_lines(area.width.max(1));
        let scroll = self.effective_scroll(area.height, &lines, state.scroll);
        state.scroll = scroll;
        let start = scroll as usize;
        let end = (scroll + area.height).min(lines.len() as u16) as usize;
        for (screen_row, line) in lines[start..end].iter().enumerate() {
            let y = area.y + screen_row as u16;
            let display_width = self.text[line.clone()].width();
            let masked = mask_char.to_string().repeat(display_width);
            buf.set_stringn(area.x, y, masked, area.width as usize, style);
        }
    }

    pub(crate) fn set_cursor(&mut self, pos: usize) {
        self.cursor_pos = self.clamp_pos_to_nearest_boundary(pos.min(self.text.len()));
        self.preferred_col = None;
    }

    pub(crate) fn move_cursor_left(&mut self) {
        self.cursor_pos = self.prev_atomic_boundary(self.cursor_pos);
        self.preferred_col = None;
    }

    pub(crate) fn move_cursor_right(&mut self) {
        self.cursor_pos = self.next_atomic_boundary(self.cursor_pos);
        self.preferred_col = None;
    }

    pub(crate) fn move_cursor_up(&mut self) {
        if let Some((target_col, maybe_line)) = {
            let cache_ref = self.wrap_cache.borrow();
            if let Some(cache) = cache_ref.as_ref() {
                let lines = &cache.lines;
                if let Some(index) = Self::wrapped_line_index_by_start(lines, self.cursor_pos) {
                    let current = &lines[index];
                    let target_col = self
                        .preferred_col
                        .unwrap_or_else(|| self.text[current.start..self.cursor_pos].width());
                    if index > 0 {
                        let previous = &lines[index - 1];
                        Some((target_col, Some((previous.start, previous.end))))
                    } else {
                        Some((target_col, None))
                    }
                } else {
                    None
                }
            } else {
                None
            }
        } {
            match maybe_line {
                Some((line_start, line_end)) => {
                    if self.preferred_col.is_none() {
                        self.preferred_col = Some(target_col);
                    }
                    self.move_to_display_col_on_line(line_start, line_end, target_col);
                    return;
                }
                None => {
                    self.cursor_pos = 0;
                    self.preferred_col = None;
                    return;
                }
            }
        }

        self.move_cursor_vertical(-1);
    }

    pub(crate) fn move_cursor_down(&mut self) {
        if let Some((target_col, maybe_line)) = {
            let cache_ref = self.wrap_cache.borrow();
            if let Some(cache) = cache_ref.as_ref() {
                let lines = &cache.lines;
                if let Some(index) = Self::wrapped_line_index_by_start(lines, self.cursor_pos) {
                    let current = &lines[index];
                    let target_col = self
                        .preferred_col
                        .unwrap_or_else(|| self.text[current.start..self.cursor_pos].width());
                    if index + 1 < lines.len() {
                        let next = &lines[index + 1];
                        Some((target_col, Some((next.start, next.end))))
                    } else {
                        Some((target_col, None))
                    }
                } else {
                    None
                }
            } else {
                None
            }
        } {
            match maybe_line {
                Some((line_start, line_end)) => {
                    if self.preferred_col.is_none() {
                        self.preferred_col = Some(target_col);
                    }
                    self.move_to_display_col_on_line(line_start, line_end, target_col);
                    return;
                }
                None => {
                    self.cursor_pos = self.text.len();
                    self.preferred_col = None;
                    return;
                }
            }
        }

        self.move_cursor_vertical(1);
    }

    pub(crate) fn delete_backward(&mut self, count: usize) {
        for _ in 0..count {
            if self.cursor_pos == 0 {
                return;
            }
            let end = self.clamp_pos_to_char_boundary(self.cursor_pos);
            let start = self.prev_atomic_boundary(end);
            self.replace_range(start..end, "");
        }
        self.wrap_cache.replace(None);
        self.preferred_col = None;
    }

    pub(crate) fn delete_forward(&mut self, count: usize) {
        for _ in 0..count {
            let start = self.clamp_pos_to_char_boundary(self.cursor_pos);
            if start >= self.text.len() {
                self.cursor_pos = self.text.len();
                return;
            }
            let end = self.next_atomic_boundary(start);
            self.replace_range(start..end, "");
        }
        self.wrap_cache.replace(None);
        self.preferred_col = None;
    }

    pub(crate) fn delete_backward_word(&mut self) {
        let start = self.beginning_of_previous_word();
        if start < self.cursor_pos {
            self.kill_buffer = self.text[start..self.cursor_pos].to_string();
            self.replace_range(start..self.cursor_pos, "");
        }
    }

    pub(crate) fn delete_forward_word(&mut self) {
        let end = self.end_of_next_word();
        if end > self.cursor_pos {
            self.kill_buffer = self.text[self.cursor_pos..end].to_string();
            self.replace_range(self.cursor_pos..end, "");
        }
    }

    fn kill_to_beginning_of_line(&mut self) {
        let start = self.beginning_of_current_line();
        if start < self.cursor_pos {
            self.kill_buffer = self.text[start..self.cursor_pos].to_string();
            self.replace_range(start..self.cursor_pos, "");
        }
    }

    fn kill_to_end_of_line(&mut self) {
        let end = self.end_of_current_line();
        if end > self.cursor_pos {
            self.kill_buffer = self.text[self.cursor_pos..end].to_string();
            self.replace_range(self.cursor_pos..end, "");
        } else if self.cursor_pos < self.text.len() {
            self.kill_buffer = "\n".to_string();
            self.replace_range(self.cursor_pos..self.cursor_pos + 1, "");
        }
    }

    fn yank(&mut self) {
        if !self.kill_buffer.is_empty() {
            let killed = self.kill_buffer.clone();
            self.insert_str(&killed);
        }
    }

    fn move_cursor_to_beginning_of_line(&mut self) {
        self.cursor_pos = self.beginning_of_current_line();
        self.preferred_col = None;
    }

    fn move_cursor_to_end_of_line(&mut self) {
        self.cursor_pos = self.end_of_current_line();
        self.preferred_col = None;
    }

    fn move_cursor_vertical(&mut self, delta: isize) {
        let current_col = self.preferred_col.unwrap_or_else(|| {
            self.text[self.beginning_of_current_line()..self.cursor_pos].width()
        });
        let current_line_start = self.beginning_of_current_line();
        let target_line_start = if delta < 0 {
            if current_line_start == 0 {
                return;
            }
            self.beginning_of_line(current_line_start.saturating_sub(1))
        } else {
            let current_line_end = self.end_of_current_line();
            if current_line_end >= self.text.len() {
                return;
            }
            current_line_end + 1
        };
        let target_line_end = self.end_of_line(target_line_start);
        self.move_to_display_col_on_line(target_line_start, target_line_end, current_col);
        self.preferred_col = Some(current_col);
    }

    fn move_to_display_col_on_line(
        &mut self,
        line_start: usize,
        line_end: usize,
        target_col: usize,
    ) {
        let mut width_so_far = 0usize;
        for (offset, grapheme) in self.text[line_start..line_end].grapheme_indices(true) {
            width_so_far += grapheme.width();
            if width_so_far > target_col {
                self.cursor_pos = self.clamp_pos_to_nearest_boundary(line_start + offset);
                return;
            }
        }
        self.cursor_pos = self.clamp_pos_to_nearest_boundary(line_end);
    }

    fn wrapped_lines(&self, width: u16) -> Vec<Range<usize>> {
        let width = width.max(1);
        if let Some(cache) = self.wrap_cache.borrow().as_ref() {
            if cache.width == width {
                return cache.lines.clone();
            }
        }
        let lines = self.compute_wrapped_lines(width as usize);
        self.wrap_cache.replace(Some(WrapCache {
            width,
            lines: lines.clone(),
        }));
        lines
    }

    fn compute_wrapped_lines(&self, width: usize) -> Vec<Range<usize>> {
        if self.text.is_empty() {
            return std::iter::once(0..0).collect();
        }
        let options = textwrap::Options::new(width.max(1))
            .break_words(true)
            .wrap_algorithm(textwrap::WrapAlgorithm::FirstFit);
        let mut ranges = Vec::new();
        let mut offset = 0usize;
        for line in self.text.split_inclusive('\n') {
            let has_newline = line.ends_with('\n');
            let visible = line.strip_suffix('\n').unwrap_or(line);
            if visible.is_empty() {
                ranges.push(offset..offset);
            } else {
                let mut cursor = 0usize;
                for local in crate::wrapping::wrap_ranges_trim(visible, options.clone()) {
                    ranges.push((offset + cursor)..(offset + local.end));
                    cursor = local.end;
                }
            }
            offset += line.len();
            if has_newline && offset == self.text.len() {
                ranges.push(offset..offset);
            }
        }
        ranges
    }

    fn effective_scroll(&self, height: u16, lines: &[Range<usize>], requested_scroll: u16) -> u16 {
        let total_lines = lines.len() as u16;
        if height == 0 || height >= total_lines {
            return 0;
        }
        let cursor_line = Self::wrapped_line_index_by_start(lines, self.cursor_pos).unwrap_or(0);
        let max_scroll = total_lines.saturating_sub(height);
        let mut scroll = requested_scroll.min(max_scroll);
        let cursor_line = cursor_line as u16;
        if cursor_line < scroll {
            scroll = cursor_line;
        } else if cursor_line >= scroll + height {
            scroll = cursor_line + 1 - height;
        }
        scroll
    }

    fn wrapped_line_index_by_start(lines: &[Range<usize>], pos: usize) -> Option<usize> {
        let index = lines.partition_point(|range| range.start <= pos);
        if index == 0 {
            None
        } else {
            Some(index - 1)
        }
    }

    fn beginning_of_line(&self, pos: usize) -> usize {
        self.text[..pos]
            .rfind('\n')
            .map(|index| index + 1)
            .unwrap_or(0)
    }

    fn beginning_of_current_line(&self) -> usize {
        self.beginning_of_line(self.cursor_pos)
    }

    fn end_of_line(&self, pos: usize) -> usize {
        self.text[pos..]
            .find('\n')
            .map(|index| pos + index)
            .unwrap_or(self.text.len())
    }

    fn end_of_current_line(&self) -> usize {
        self.end_of_line(self.cursor_pos)
    }

    fn beginning_of_previous_word(&self) -> usize {
        if let Some(element) = self.element_ending_at_or_containing(self.cursor_pos) {
            return element.range.start;
        }
        let mut pos = self.cursor_pos;
        while pos > 0 {
            let prev = self.prev_atomic_boundary(pos);
            let text = &self.text[prev..pos];
            if !text.chars().all(char::is_whitespace) {
                break;
            }
            pos = prev;
        }
        while pos > 0 {
            let prev = self.prev_atomic_boundary(pos);
            if self.element_ending_at_or_containing(pos).is_some() {
                return prev;
            }
            let ch = self.text[prev..pos].chars().next().unwrap_or('\0');
            if ch.is_whitespace() || is_word_separator(ch) {
                break;
            }
            pos = prev;
        }
        pos
    }

    fn end_of_next_word(&self) -> usize {
        if let Some(element) = self.element_starting_at_or_containing(self.cursor_pos) {
            return element.range.end;
        }
        let mut pos = self.cursor_pos;
        while pos < self.text.len() {
            let next = self.next_atomic_boundary(pos);
            let ch = self.text[pos..next].chars().next().unwrap_or('\0');
            if !ch.is_whitespace() {
                break;
            }
            pos = next;
            if let Some(element) = self.element_starting_at_or_containing(pos) {
                return element.range.end;
            }
        }
        while pos < self.text.len() {
            let next = self.next_atomic_boundary(pos);
            if let Some(element) = self.element_starting_at_or_containing(pos) {
                return element.range.end;
            }
            let ch = self.text[pos..next].chars().next().unwrap_or('\0');
            if ch.is_whitespace() || is_word_separator(ch) {
                break;
            }
            pos = next;
        }
        pos
    }

    fn previous_grapheme_boundary(&self, pos: usize) -> usize {
        let pos = self.clamp_pos_to_char_boundary(pos);
        if pos == 0 {
            return 0;
        }
        self.text[..pos]
            .grapheme_indices(true)
            .next_back()
            .map(|(index, _)| index)
            .unwrap_or(0)
    }

    fn next_grapheme_boundary(&self, pos: usize) -> usize {
        let pos = self.clamp_pos_to_char_boundary(pos);
        if pos >= self.text.len() {
            return self.text.len();
        }
        self.text[pos..]
            .grapheme_indices(true)
            .nth(1)
            .map(|(index, _)| pos + index)
            .unwrap_or(self.text.len())
    }

    fn clamp_pos_for_insertion(&self, pos: usize) -> usize {
        let pos = self.clamp_pos_to_char_boundary(pos.min(self.text.len()));
        if let Some(element) = self.element_containing(pos) {
            let dist_start = pos.saturating_sub(element.range.start);
            let dist_end = element.range.end.saturating_sub(pos);
            if dist_start <= dist_end {
                element.range.start
            } else {
                element.range.end
            }
        } else {
            pos
        }
    }

    fn clamp_pos_to_char_boundary(&self, pos: usize) -> usize {
        let mut cursor = pos.min(self.text.len());
        while cursor > 0 && !self.text.is_char_boundary(cursor) {
            cursor -= 1;
        }
        cursor
    }

    fn clamp_pos_to_nearest_boundary(&self, pos: usize) -> usize {
        let pos = self.clamp_pos_to_char_boundary(pos);
        if let Some(element) = self.element_containing(pos) {
            let dist_start = pos.saturating_sub(element.range.start);
            let dist_end = element.range.end.saturating_sub(pos);
            if dist_start <= dist_end {
                element.range.start
            } else {
                element.range.end
            }
        } else {
            pos
        }
    }

    fn add_element(&mut self, range: Range<usize>) -> u64 {
        let id = self.next_element_id;
        self.next_element_id = self.next_element_id.saturating_add(1);
        self.elements.push(TextElement { id, range });
        self.elements
            .sort_by_key(|element| (element.range.start, element.id));
        id
    }

    fn element_containing(&self, pos: usize) -> Option<&TextElement> {
        self.elements
            .iter()
            .find(|element| pos > element.range.start && pos < element.range.end)
    }

    fn element_starting_at_or_containing(&self, pos: usize) -> Option<&TextElement> {
        self.elements
            .iter()
            .find(|element| pos >= element.range.start && pos < element.range.end)
    }

    fn element_ending_at_or_containing(&self, pos: usize) -> Option<&TextElement> {
        self.elements
            .iter()
            .find(|element| pos > element.range.start && pos <= element.range.end)
    }

    fn expand_range_to_element_boundaries(&self, mut range: Range<usize>) -> Range<usize> {
        range.start = self.clamp_pos_to_char_boundary(range.start.min(self.text.len()));
        range.end = self.clamp_pos_to_char_boundary(range.end.min(self.text.len()));
        loop {
            let mut changed = false;
            for element in &self.elements {
                if element.range.start < range.end && element.range.end > range.start {
                    let start = range.start.min(element.range.start);
                    let end = range.end.max(element.range.end);
                    if start != range.start || end != range.end {
                        range.start = start;
                        range.end = end;
                        changed = true;
                    }
                }
            }
            if !changed {
                break;
            }
        }
        range
    }

    fn shift_elements(&mut self, at: usize, removed: usize, inserted: usize) {
        let end = at + removed;
        let diff = inserted as isize - removed as isize;
        self.elements
            .retain(|element| !(element.range.start >= at && element.range.end <= end));
        for element in &mut self.elements {
            if element.range.end <= at {
                continue;
            }
            if element.range.start >= end {
                element.range.start = element.range.start.saturating_add_signed(diff);
                element.range.end = element.range.end.saturating_add_signed(diff);
            } else {
                element.range.start = at.min(element.range.start);
                element.range.end = at + inserted.max(element.range.end.saturating_sub(end));
            }
        }
        self.elements
            .sort_by_key(|element| (element.range.start, element.id));
    }

    fn prev_atomic_boundary(&self, pos: usize) -> usize {
        let pos = self.clamp_pos_to_char_boundary(pos);
        if pos == 0 {
            return 0;
        }
        if let Some(element) = self.element_ending_at_or_containing(pos) {
            return element.range.start;
        }
        let boundary = self.previous_grapheme_boundary(pos);
        self.element_containing(boundary)
            .map(|element| element.range.start)
            .unwrap_or(boundary)
    }

    fn next_atomic_boundary(&self, pos: usize) -> usize {
        let pos = self.clamp_pos_to_char_boundary(pos);
        if pos >= self.text.len() {
            return self.text.len();
        }
        if let Some(element) = self.element_starting_at_or_containing(pos) {
            return element.range.end;
        }
        let boundary = self.next_grapheme_boundary(pos);
        self.element_containing(boundary)
            .map(|element| element.range.end)
            .unwrap_or(boundary)
    }
}

fn is_word_separator(ch: char) -> bool {
    WORD_SEPARATORS.contains(ch)
}

#[allow(dead_code)]
fn char_width(ch: char) -> usize {
    ch.width().unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn edits_at_cursor_and_preserves_utf8() {
        let mut area = TextArea::default();
        area.set_text("a👍b");
        area.move_cursor_left();
        area.move_cursor_left();
        assert_eq!(area.cursor(), "a".len());

        area.delete_forward(1);
        area.insert_str("中");

        assert_eq!(area.text(), "a中b");
    }

    #[test]
    fn kill_and_yank_line_end() {
        let mut area = TextArea::default();
        area.set_text("hello world");
        area.set_cursor("hello".len());

        area.input(KeyEvent::new(KeyCode::Char('k'), KeyModifiers::CONTROL));
        assert_eq!(area.text(), "hello");
        area.input(KeyEvent::new(KeyCode::Char('y'), KeyModifiers::CONTROL));
        assert_eq!(area.text(), "hello world");
    }

    #[test]
    fn cursor_uses_wrapped_position() {
        let mut area = TextArea::default();
        area.set_text("123456789");
        let rect = Rect {
            x: 2,
            y: 10,
            width: 5,
            height: 3,
        };

        assert_eq!(
            area.cursor_pos(rect, TextAreaState::default()),
            Some((6, 11))
        );
    }

    #[test]
    fn cursor_scrolls_into_small_viewport() {
        let mut area = TextArea::default();
        area.set_text("one\ntwo\nthree\nfour");
        let rect = Rect {
            x: 0,
            y: 0,
            width: 20,
            height: 2,
        };

        assert_eq!(
            area.cursor_pos(rect, TextAreaState::default()),
            Some((4, 1))
        );
    }

    #[test]
    fn delete_forward_deletes_atomic_element_at_left_edge() {
        let mut area = TextArea::default();
        area.insert_str("a");
        area.insert_element("[Pasted Content 1001 chars]");
        area.insert_str("b");
        let element_start = "a".len();

        area.set_cursor(element_start);
        area.delete_forward(1);

        assert_eq!(area.text(), "ab");
        assert!(area.element_payloads().is_empty());
        assert_eq!(area.cursor(), element_start);
    }

    #[test]
    fn cursor_does_not_land_inside_atomic_element() {
        let mut area = TextArea::default();
        area.insert_str("a");
        area.insert_element("[Pasted Content 1001 chars]");
        area.insert_str("b");

        area.set_cursor("a[Pasted".len());

        assert_eq!(area.cursor(), "a".len());
    }

    #[test]
    fn wrapped_navigation_across_visual_lines() {
        let mut area = TextArea::default();
        area.set_text("abcdefghij");
        let _ = area.desired_height(4);

        area.set_cursor(0);
        area.move_cursor_down();
        assert_eq!(area.cursor(), 4);

        area.set_cursor(4);
        assert_eq!(
            area.cursor_pos(
                Rect {
                    x: 0,
                    y: 0,
                    width: 4,
                    height: 10,
                },
                TextAreaState::default(),
            ),
            Some((0, 1)),
        );

        area.set_cursor(6);
        area.move_cursor_up();
        assert_eq!(area.cursor(), 2);

        area.move_cursor_down();
        assert_eq!(area.cursor(), 6);

        area.move_cursor_down();
        assert_eq!(area.cursor(), area.text().len());
    }

    #[test]
    fn wrapped_navigation_with_newlines_and_spaces() {
        let mut area = TextArea::default();
        area.set_text("word1  word2\nword3");
        let _ = area.desired_height(6);

        let start_word2 = area.text().find("word2").unwrap();
        area.set_cursor(start_word2 + 1);

        area.move_cursor_up();
        assert_eq!(area.cursor(), 3);

        area.move_cursor_down();
        assert_eq!(area.cursor(), start_word2 + 1);

        area.move_cursor_down();
        let start_word3 = area.text().find("word3").unwrap();
        assert!(area.cursor() >= start_word3);
        assert!(area.cursor() <= start_word3 + "word3".len());
    }

    #[test]
    fn wrapped_navigation_with_wide_graphemes() {
        let mut area = TextArea::default();
        area.set_text("👍👍👍👍");
        let _ = area.desired_height(3);

        area.set_cursor("👍👍".len());
        area.move_cursor_down();
        assert!(area.cursor() >= "👍👍".len());

        area.move_cursor_up();
        assert_eq!(area.cursor(), "👍👍".len());
    }

    #[test]
    fn wrapped_navigation_does_not_enter_atomic_element() {
        let mut area = TextArea::default();
        area.insert_str("ab");
        let element_text = "[Pasted Content 1001 chars]";
        let element_start = area.text().len();
        area.insert_element(element_text);
        let element_end = element_start + element_text.len();
        area.insert_str("cd");
        let _ = area.desired_height(6);

        area.set_cursor(0);
        for _ in 0..10 {
            area.move_cursor_down();
            assert!(
                area.cursor() <= element_start || area.cursor() >= element_end,
                "cursor {} landed inside atomic element {}..{}",
                area.cursor(),
                element_start,
                element_end
            );
        }
    }

    #[test]
    fn wrapped_ranges_stay_on_utf8_boundaries_for_long_path_with_cjk_suffix() {
        let mut area = TextArea::default();
        area.set_text(
            "请你对/Users/gaoruizhang/Zotero/storage/WX9SP45E/Islam\\ 等\\ -\\ 2021\\ -\\ Global\\ Pooling,\\ More\\ than\\ Meets\\ the\\ Eye\\ Position\\ Information\\ is\\ Encoded\\ Channel-Wise\\ in\\ CNNs.pdf这",
        );

        for width in 1..=120 {
            let lines = area.wrapped_lines(width);
            assert!(!lines.is_empty());
            for line in &lines {
                assert!(
                    area.text().is_char_boundary(line.start),
                    "width={width} invalid start in {line:?}"
                );
                assert!(
                    area.text().is_char_boundary(line.end),
                    "width={width} invalid end in {line:?}"
                );
                assert!(line.start <= line.end);
                assert!(line.end <= area.text().len());
            }

            let rect = Rect {
                x: 0,
                y: 0,
                width,
                height: lines.len() as u16,
            };
            let mut buf = Buffer::empty(rect);
            let mut state = TextAreaState::default();
            area.render(rect, &mut buf, &mut state, Style::default());
            let cursor = area
                .cursor_pos(rect, state)
                .expect("cursor should stay visible");
            assert!(cursor.0 < width);
            assert!(cursor.1 < rect.height);
        }
    }
}

#[cfg(test)]
mod vim_tests {
    use super::*;
    use crossterm::event::{KeyCode, KeyEvent, KeyEventKind, KeyEventState, KeyModifiers};

    fn key(code: KeyCode) -> KeyEvent {
        KeyEvent {
            code,
            modifiers: KeyModifiers::NONE,
            kind: KeyEventKind::Press,
            state: KeyEventState::NONE,
        }
    }

    fn vim_area(text: &str) -> TextArea {
        let mut area = TextArea::default();
        area.set_vim_enabled(true);
        area.set_text(text.to_string());
        area.vim_enter_normal_mode();
        area
    }

    #[test]
    fn normal_hjkl_moves_cursor() {
        let mut area = vim_area("hello\nworld");

        let end = area.cursor_pos;
        area.input(key(KeyCode::Char('h')));
        assert_eq!(area.cursor_pos, end - 1);
        area.input(key(KeyCode::Char('l')));
        assert_eq!(area.cursor_pos, end);
    }

    #[test]
    fn normal_word_motions() {
        let mut area = vim_area("foo bar baz");
        area.cursor_pos = 0;

        area.input(key(KeyCode::Char('w')));
        assert!(area.cursor_pos > 3);
        area.input(key(KeyCode::Char('b')));
        assert!(area.cursor_pos < 4);
    }

    #[test]
    fn normal_x_deletes_forward() {
        let mut area = vim_area("abc");
        area.cursor_pos = 0;

        area.input(key(KeyCode::Char('x')));

        assert_eq!(area.text(), "bc");
    }

    #[test]
    fn dd_cuts_line_into_yank_and_p_reinserts() {
        let mut area = vim_area("first\nsecond");
        area.cursor_pos = 0;

        area.input(key(KeyCode::Char('d')));
        area.input(key(KeyCode::Char('d')));
        assert_eq!(area.text(), "second");

        area.input(key(KeyCode::Char('p')));
        assert!(area.text().contains("first"));
    }

    #[test]
    fn i_a_o_enter_insert_and_esc_returns_to_normal() {
        let mut area = vim_area("abc");

        area.input(key(KeyCode::Char('i')));
        assert_eq!(area.vim_mode, VimMode::Insert);
        area.input(key(KeyCode::Esc));
        assert_eq!(area.vim_mode, VimMode::Normal);

        area.input(key(KeyCode::Char('a')));
        assert_eq!(area.vim_mode, VimMode::Insert);
        area.input(key(KeyCode::Esc));

        let before = area.text().len();
        area.input(key(KeyCode::Char('o')));
        assert_eq!(area.vim_mode, VimMode::Insert);
        assert!(area.text().len() > before);
    }

    #[test]
    fn insert_mode_chars_still_insert() {
        let mut area = vim_area("abc");
        area.input(key(KeyCode::Char('i')));

        area.input(key(KeyCode::Char('z')));

        assert!(area.text().contains('z'));
    }
}
