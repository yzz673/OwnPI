use std::time::{Duration, Instant};

const PASTE_BURST_MIN_CHARS: u16 = 3;
const PASTE_ENTER_SUPPRESS_WINDOW: Duration = Duration::from_millis(120);
const PASTE_BURST_CHAR_INTERVAL: Duration = Duration::from_millis(8);

#[cfg(not(windows))]
const PASTE_BURST_ACTIVE_IDLE_TIMEOUT: Duration = Duration::from_millis(8);
#[cfg(windows)]
const PASTE_BURST_ACTIVE_IDLE_TIMEOUT: Duration = Duration::from_millis(60);

#[derive(Default, Debug)]
pub(crate) struct PasteBurst {
    last_plain_char_time: Option<Instant>,
    consecutive_plain_char_burst: u16,
    burst_window_until: Option<Instant>,
    buffer: String,
    active: bool,
    pending_first_char: Option<(char, Instant)>,
}

#[derive(Debug, PartialEq, Eq)]
pub(crate) enum CharDecision {
    BeginBuffer { retro_chars: u16 },
    BufferAppend,
    RetainFirstChar,
    BeginBufferFromPending,
}

#[derive(Debug, PartialEq, Eq)]
pub(crate) struct RetroGrab {
    pub(crate) start_byte: usize,
    pub(crate) grabbed: String,
}

#[derive(Debug, PartialEq, Eq)]
pub(crate) enum FlushResult {
    Paste(String),
    Typed(char),
    None,
}

impl PasteBurst {
    pub(crate) fn recommended_flush_delay() -> Duration {
        PASTE_BURST_CHAR_INTERVAL + Duration::from_millis(1)
    }

    pub(crate) fn on_plain_char(&mut self, ch: char, now: Instant) -> CharDecision {
        self.note_plain_char(now);
        if self.active {
            self.burst_window_until = Some(now + PASTE_ENTER_SUPPRESS_WINDOW);
            return CharDecision::BufferAppend;
        }
        if let Some((held, held_at)) = self.pending_first_char {
            if now.duration_since(held_at) <= PASTE_BURST_CHAR_INTERVAL {
                self.active = true;
                self.pending_first_char = None;
                self.buffer.push(held);
                self.burst_window_until = Some(now + PASTE_ENTER_SUPPRESS_WINDOW);
                return CharDecision::BeginBufferFromPending;
            }
        }
        if self.consecutive_plain_char_burst >= PASTE_BURST_MIN_CHARS {
            return CharDecision::BeginBuffer {
                retro_chars: self.consecutive_plain_char_burst.saturating_sub(1),
            };
        }
        self.pending_first_char = Some((ch, now));
        CharDecision::RetainFirstChar
    }

    pub(crate) fn on_plain_char_no_hold(&mut self, now: Instant) -> Option<CharDecision> {
        self.note_plain_char(now);
        if self.active {
            self.burst_window_until = Some(now + PASTE_ENTER_SUPPRESS_WINDOW);
            return Some(CharDecision::BufferAppend);
        }
        if self.consecutive_plain_char_burst >= PASTE_BURST_MIN_CHARS {
            Some(CharDecision::BeginBuffer {
                retro_chars: self.consecutive_plain_char_burst.saturating_sub(1),
            })
        } else {
            None
        }
    }

    pub(crate) fn append_char_to_buffer(&mut self, ch: char) {
        self.buffer.push(ch);
    }

    pub(crate) fn decide_begin_buffer(
        &mut self,
        before_cursor: &str,
        retro_chars: u16,
    ) -> Option<RetroGrab> {
        let start_byte = retro_start_index(before_cursor, retro_chars as usize)?;
        let grabbed = before_cursor[start_byte..].to_string();
        self.active = true;
        self.buffer.push_str(&grabbed);
        Some(RetroGrab {
            start_byte,
            grabbed,
        })
    }

    pub(crate) fn direct_insert_newline_should_insert(&mut self, now: Instant) -> bool {
        self.flush_pending_first_char_into_buffer();
        self.active || self.burst_window_until.is_some_and(|until| now <= until)
    }

    pub(crate) fn append_newline_if_active(&mut self, now: Instant) -> bool {
        if !self.active {
            return false;
        }
        self.buffer.push('\n');
        self.burst_window_until = Some(now + PASTE_ENTER_SUPPRESS_WINDOW);
        true
    }

    pub(crate) fn flush_if_due(&mut self, now: Instant) -> FlushResult {
        if let Some((ch, held_at)) = self.pending_first_char {
            if now.duration_since(held_at) > PASTE_BURST_CHAR_INTERVAL {
                self.pending_first_char = None;
                return FlushResult::Typed(ch);
            }
        }
        if self.active
            && self
                .last_plain_char_time
                .is_some_and(|last| now.duration_since(last) > PASTE_BURST_ACTIVE_IDLE_TIMEOUT)
        {
            return self.flush_buffer();
        }
        FlushResult::None
    }

    pub(crate) fn flush_before_modified_input(&mut self) -> Option<String> {
        self.flush_pending_first_char_into_buffer();
        match self.flush_buffer() {
            FlushResult::Paste(text) => Some(text),
            FlushResult::Typed(ch) => Some(ch.to_string()),
            FlushResult::None => None,
        }
    }

    pub(crate) fn clear_window_after_non_char(&mut self) {
        self.last_plain_char_time = None;
        self.consecutive_plain_char_burst = 0;
        self.burst_window_until = None;
    }

    pub(crate) fn preview_buffered_text(&self) -> Option<String> {
        let mut preview = String::new();
        if let Some((ch, _)) = self.pending_first_char {
            preview.push(ch);
        }
        if !self.buffer.is_empty() {
            preview.push_str(&self.buffer);
        }
        (!preview.is_empty()).then_some(preview)
    }

    pub(crate) fn is_active(&self) -> bool {
        self.active || !self.buffer.is_empty() || self.pending_first_char.is_some()
    }

    fn flush_buffer(&mut self) -> FlushResult {
        self.active = false;
        self.last_plain_char_time = None;
        self.consecutive_plain_char_burst = 0;
        if self.buffer.is_empty() {
            return FlushResult::None;
        }
        FlushResult::Paste(std::mem::take(&mut self.buffer))
    }

    fn note_plain_char(&mut self, now: Instant) {
        if self
            .last_plain_char_time
            .is_some_and(|last| now.duration_since(last) <= PASTE_BURST_CHAR_INTERVAL)
        {
            self.consecutive_plain_char_burst = self.consecutive_plain_char_burst.saturating_add(1);
        } else {
            self.consecutive_plain_char_burst = 1;
            self.pending_first_char = None;
            self.active = false;
            self.buffer.clear();
        }
        self.last_plain_char_time = Some(now);
    }

    fn flush_pending_first_char_into_buffer(&mut self) {
        if let Some((ch, _)) = self.pending_first_char.take() {
            self.buffer.push(ch);
        }
    }
}

fn retro_start_index(text: &str, retro_chars: usize) -> Option<usize> {
    if retro_chars == 0 {
        return Some(text.len());
    }
    text.char_indices()
        .rev()
        .nth(retro_chars.saturating_sub(1))
        .map(|(index, _)| index)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn holds_first_char_then_flushes_as_typed() {
        let mut burst = PasteBurst::default();
        let now = Instant::now();
        assert_eq!(burst.on_plain_char('a', now), CharDecision::RetainFirstChar);
        assert_eq!(
            burst.flush_if_due(now + PasteBurst::recommended_flush_delay()),
            FlushResult::Typed('a')
        );
    }

    #[test]
    fn buffers_fast_chars_as_paste() {
        let mut burst = PasteBurst::default();
        let now = Instant::now();
        assert_eq!(burst.on_plain_char('a', now), CharDecision::RetainFirstChar);
        assert_eq!(
            burst.on_plain_char('b', now + Duration::from_millis(1)),
            CharDecision::BeginBufferFromPending
        );
        burst.append_char_to_buffer('b');
        assert_eq!(
            burst.flush_if_due(now + Duration::from_millis(20)),
            FlushResult::Paste("ab".to_string())
        );
    }

    #[test]
    fn active_while_holding_or_buffering_burst_text() {
        let mut burst = PasteBurst::default();
        let now = Instant::now();

        assert_eq!(burst.on_plain_char('1', now), CharDecision::RetainFirstChar);
        assert!(burst.is_active());
        assert_eq!(
            burst.flush_if_due(now + PasteBurst::recommended_flush_delay()),
            FlushResult::Typed('1')
        );
        assert!(!burst.is_active());

        assert_eq!(burst.on_plain_char('1', now), CharDecision::RetainFirstChar);
        assert_eq!(
            burst.on_plain_char('2', now + Duration::from_millis(1)),
            CharDecision::BeginBufferFromPending
        );
        burst.append_char_to_buffer('2');
        assert!(burst.is_active());
    }
}
