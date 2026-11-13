use std::io;
use std::io::Write;
use std::sync::Arc;

use anyhow::Result;
use crossterm::cursor::{Hide, MoveTo, Show};
use crossterm::queue;
use crossterm::style::{
    Attribute, Color as CrosstermColor, Print, ResetColor, SetAttribute, SetBackgroundColor,
    SetForegroundColor,
};
use crossterm::terminal::{Clear, ClearType, EnterAlternateScreen, LeaveAlternateScreen};
use crossterm::SynchronizedUpdate;
use ratatui::backend::{Backend, CrosstermBackend};
use ratatui::buffer::Buffer;
use ratatui::layout::{Position, Rect, Size};
use ratatui::style::{Color, Modifier};
use ratatui::text::Line;
use ratatui::widgets::Widget;
use unicode_width::UnicodeWidthStr;

#[cfg(test)]
use crate::history_cell;
use crate::history_cell::{
    history_cells_require_inter_cell_spacing, HistoryCell, HistoryRenderMode,
};
use crate::insert_history;
use crate::terminal_hyperlinks::HyperlinkLine;

#[cfg(test)]
#[path = "inline_terminal/recovery_tests.rs"]
mod recovery_tests;

pub(crate) struct InlineTerminal<W: Write = io::Stdout> {
    backend: CrosstermBackend<W>,
    viewport_area: Rect,
    screen_size: Size,
    previous_buffer: Option<Buffer>,
    cursor_position: Option<Position>,
    last_known_cursor_pos: Position,
    pending_history_cells: Vec<Arc<dyn HistoryCell>>,
    last_history_cell: Option<Arc<dyn HistoryCell>>,
    cursor_hidden: bool,
    alternate_screen_active: bool,
    history_render_mode: HistoryRenderMode,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum HistoryFlushOutcome {
    None,
    Flushed { rows: u16 },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum StreamViewportRepaint {
    IfResizeReflowRan,
    Required,
}

/// Authoritative history source used to rebuild physical terminal scrollback.
///
/// Codex establishes the final inline viewport before inserting queued history.
/// Carrying the rebuild into `draw_with_resize_reflow` preserves that ordering
/// and keeps clear, resize, history insertion, and live drawing atomic.
pub(crate) struct ScrollbackRebuild {
    lines: Vec<HyperlinkLine>,
    last_history_cell: Option<Arc<dyn HistoryCell>>,
}

impl ScrollbackRebuild {
    pub(crate) fn new(
        lines: Vec<HyperlinkLine>,
        last_history_cell: Option<Arc<dyn HistoryCell>>,
    ) -> Self {
        Self {
            lines,
            last_history_cell,
        }
    }
}

impl<W: Write> InlineTerminal<W> {
    pub(crate) fn new(backend: CrosstermBackend<W>) -> Result<Self> {
        let screen_rect = backend.size()?;
        let screen_size = sanitize_screen_size(Size::new(screen_rect.width, screen_rect.height));
        Ok(Self {
            backend,
            viewport_area: Rect::new(0, 0, screen_size.width, screen_size.height),
            screen_size,
            previous_buffer: None,
            cursor_position: None,
            last_known_cursor_pos: Position::new(0, 0),
            pending_history_cells: Vec::new(),
            last_history_cell: None,
            cursor_hidden: false,
            alternate_screen_active: false,
            history_render_mode: HistoryRenderMode::Rich,
        })
    }

    pub(crate) fn backend_mut(&mut self) -> &mut CrosstermBackend<W> {
        &mut self.backend
    }

    pub(crate) fn screen_size(&mut self) -> Result<Size> {
        self.current_screen_size()
    }

    fn current_screen_size(&mut self) -> Result<Size> {
        let screen_rect = self.backend.size()?;
        Ok(sanitize_screen_size(Size::new(
            screen_rect.width,
            screen_rect.height,
        )))
    }

    pub(crate) fn set_viewport_area(&mut self, viewport_area: Rect) {
        if viewport_area != self.viewport_area {
            self.previous_buffer = None;
        }
        self.viewport_area = viewport_area;
    }

    pub(crate) fn history_wrap_width(&self) -> usize {
        self.viewport_area.width.saturating_sub(1).max(1) as usize
    }

    pub(crate) fn insert_history_cells(&mut self, cells: Vec<Arc<dyn HistoryCell>>) {
        if cells.is_empty() {
            return;
        }
        self.pending_history_cells.extend(cells);
    }

    pub(crate) fn set_history_render_mode(&mut self, mode: HistoryRenderMode) {
        self.history_render_mode = mode;
    }

    #[cfg(test)]
    pub(crate) fn clear_pending_history_lines(&mut self) {
        self.pending_history_cells.clear();
    }

    pub(crate) fn draw_with_resize_reflow<F>(
        &mut self,
        screen: Size,
        height: u16,
        stream_repaint: Option<StreamViewportRepaint>,
        scrollback_rebuild: Option<ScrollbackRebuild>,
        render_callback: F,
    ) -> Result<HistoryFlushOutcome>
    where
        F: FnOnce(&mut InlineFrame),
    {
        io::stdout().sync_update(|_| -> Result<HistoryFlushOutcome> {
            // One geometry snapshot owns layout, clear, history insertion and
            // live drawing. A resize during this transaction belongs to the
            // next frame, not a second measurement halfway through this one.
            self.draw_at_screen_size(
                screen,
                height,
                stream_repaint,
                scrollback_rebuild,
                render_callback,
            )
        })?
    }

    fn draw_at_screen_size<F>(
        &mut self,
        screen: Size,
        height: u16,
        stream_repaint: Option<StreamViewportRepaint>,
        mut scrollback_rebuild: Option<ScrollbackRebuild>,
        render_callback: F,
    ) -> Result<HistoryFlushOutcome>
    where
        F: FnOnce(&mut InlineFrame),
    {
        let reflow = viewport_reflow(self.viewport_area, self.screen_size, screen, height);
        if scrollback_rebuild.is_none()
            && (reflow.needs_clear
                || !self.pending_history_cells.is_empty()
                || stream_repaint == Some(StreamViewportRepaint::Required))
        {
            // Clear the *painted* rectangle before adopting the next one
            // or scrolling. Otherwise a retired prompt can enter history.
            self.clear_after_position(Position::new(
                0,
                self.viewport_area.y.min(screen.height.saturating_sub(1)),
            ))?;
        }
        if let Some(rebuild) = scrollback_rebuild.as_ref() {
            crate::bridge::append_debug_log(
                "scrollback_source_rebuild",
                &serde_json::json!({
                    "line_count": rebuild.lines.len(),
                    "width": self.screen_size.width,
                }),
            );
            self.clear_scrollback_and_visible_screen_ansi(screen)?;
        }
        let needs_full_repaint = self.update_inline_viewport_for_resize_reflow(screen, height)?;
        self.finish_agent_stream_repaint(stream_repaint, needs_full_repaint);
        let flush_outcome = if let Some(rebuild) = scrollback_rebuild.take() {
            let rows = self.flush_history_lines(rebuild.lines)?;
            self.last_history_cell = rebuild.last_history_cell;
            if rows == 0 {
                HistoryFlushOutcome::None
            } else {
                HistoryFlushOutcome::Flushed { rows }
            }
        } else if !self.pending_history_cells.is_empty() {
            let pending_history_lines = self.take_pending_history_lines();
            let rows = self.flush_history_lines(pending_history_lines)?;
            HistoryFlushOutcome::Flushed { rows }
        } else {
            HistoryFlushOutcome::None
        };
        if needs_full_repaint {
            self.invalidate_viewport();
        }
        self.draw(render_callback)?;
        Ok(flush_outcome)
    }

    pub(crate) fn draw_fullscreen<F>(&mut self, render_callback: F) -> Result<()>
    where
        F: FnOnce(&mut InlineFrame),
    {
        self.enter_alternate_screen()?;
        let main_viewport_area = self.viewport_area;
        let screen = self.current_screen_size()?;
        self.viewport_area = Rect::new(0, 0, screen.width, screen.height);
        self.draw(render_callback)?;
        self.viewport_area = main_viewport_area;
        Ok(())
    }

    fn finish_agent_stream_repaint(
        &mut self,
        stream_repaint: Option<StreamViewportRepaint>,
        resize_reflow_ran: bool,
    ) {
        match stream_repaint {
            Some(StreamViewportRepaint::Required) => self.invalidate_viewport(),
            Some(StreamViewportRepaint::IfResizeReflowRan) if resize_reflow_ran => {
                self.invalidate_viewport();
            }
            Some(StreamViewportRepaint::IfResizeReflowRan) | None => {}
        }
    }

    fn update_inline_viewport_for_resize_reflow(
        &mut self,
        screen: Size,
        height: u16,
    ) -> Result<bool> {
        let reflow = viewport_reflow(self.viewport_area, self.screen_size, screen, height);
        self.screen_size = screen;

        let has_scroll_up = reflow.scroll_up.is_some();
        if let Some((region, scroll_by)) = reflow.scroll_up {
            self.scroll_region_up(region, scroll_by)?;
        }
        if reflow.area != self.viewport_area {
            let previous_area = self.viewport_area;
            crate::bridge::append_debug_log(
                "viewport_reflow",
                &serde_json::json!({
                    "from": format!("{:?}", previous_area),
                    "to": format!("{:?}", reflow.area),
                    "desired_height": height,
                    "scroll_up": has_scroll_up,
                    "needs_clear": reflow.needs_clear,
                }),
            );
            self.set_viewport_area(reflow.area);
            if reflow.needs_clear {
                self.clear_for_viewport_change(previous_area, reflow.area)?;
            }
        }

        Ok(reflow.needs_full_repaint)
    }

    fn take_pending_history_lines(&mut self) -> Vec<HyperlinkLine> {
        let wrap_width = self.history_wrap_width();
        let mut lines = Vec::new();
        let mut previous_included = self.last_history_cell.clone();
        for cell in self.pending_history_cells.drain(..) {
            if !cell.is_scrollback_history_cell() {
                continue;
            }
            if previous_included.as_ref().is_some_and(|previous| {
                history_cells_require_inter_cell_spacing(previous.as_ref(), cell.as_ref())
            }) {
                lines.push(HyperlinkLine::plain(Line::from(String::new())));
            }
            lines.extend(match self.history_render_mode {
                HistoryRenderMode::Rich => cell.display_hyperlink_lines_edged(wrap_width),
                HistoryRenderMode::Raw => {
                    cell.display_hyperlink_lines_for_mode(wrap_width, HistoryRenderMode::Raw)
                }
            });
            previous_included = Some(cell.clone());
            self.last_history_cell = Some(cell);
        }
        lines
    }

    fn flush_history_lines(&mut self, pending_history_lines: Vec<HyperlinkLine>) -> Result<u16> {
        if pending_history_lines.is_empty() {
            return Ok(0);
        }
        let screen = self.screen_size;
        let previous_viewport = self.viewport_area;
        insert_history::clear_live_viewport_before_history_insert(
            &mut self.backend,
            previous_viewport,
        )?;
        let wrap_policy = match self.history_render_mode {
            HistoryRenderMode::Rich => insert_history::HistoryLineWrapPolicy::PreWrap,
            HistoryRenderMode::Raw => insert_history::HistoryLineWrapPolicy::Terminal,
        };
        let (viewport_area, inserted_rows) =
            insert_history::insert_history_hyperlink_lines_with_wrap_policy(
                &mut self.backend,
                self.viewport_area,
                screen,
                self.last_known_cursor_pos,
                pending_history_lines,
                wrap_policy,
            )?;
        if let Some(stale_tail) =
            stale_viewport_tail_after_history_flush(previous_viewport, viewport_area)
        {
            self.clear_rows(stale_tail)?;
        }
        self.set_viewport_area(viewport_area);
        self.previous_buffer = None;
        Ok(inserted_rows)
    }

    pub(crate) fn clear_after_position(&mut self, position: Position) -> Result<()> {
        queue!(
            self.backend,
            MoveTo(position.x, position.y),
            Clear(ClearType::FromCursorDown)
        )?;
        self.previous_buffer = None;
        Ok(())
    }

    fn clear_rows(&mut self, area: Rect) -> Result<()> {
        if area.is_empty() {
            return Ok(());
        }
        for y in area.y..area.bottom() {
            queue!(
                self.backend,
                MoveTo(area.x, y),
                Clear(ClearType::UntilNewLine)
            )?;
        }
        Ok(())
    }

    fn clear_for_viewport_change(&mut self, previous_area: Rect, next_area: Rect) -> Result<()> {
        let clear_position = if previous_area.is_empty() {
            next_area.as_position()
        } else {
            Position::new(0, previous_area.y.min(next_area.y))
        };
        self.clear_after_position(clear_position)
    }

    fn scroll_region_up(&mut self, region: std::ops::Range<u16>, scroll_by: u16) -> Result<()> {
        if scroll_by == 0 || region.start >= region.end {
            return Ok(());
        }
        // Live rows were cleared by frame preparation. Full-screen scrolling
        // preserves the history leaving row zero in native scrollback; partial
        // scroll regions can discard it and a one-row region is invalid.
        queue!(
            self.backend,
            insert_history::ResetScrollRegion,
            MoveTo(0, self.screen_size.height.saturating_sub(1))
        )?;
        for _ in 0..scroll_by {
            queue!(self.backend, Print("\n"), Clear(ClearType::UntilNewLine))?;
        }
        queue!(self.backend, insert_history::ResetScrollRegion)?;
        Ok(())
    }

    pub(crate) fn clear_scrollback_and_visible_screen_ansi(&mut self, screen: Size) -> Result<()> {
        self.screen_size = screen;
        write!(self.backend, "\x1b[r\x1b[0m\x1b[H\x1b[2J\x1b[3J\x1b[H")?;
        Write::flush(&mut self.backend)?;
        self.set_viewport_area(Rect::new(0, 0, screen.width, screen.height));
        self.cursor_position = Some(Position::new(0, 0));
        self.last_known_cursor_pos = Position::new(0, 0);
        self.pending_history_cells.clear();
        self.last_history_cell = None;
        self.previous_buffer = None;
        Ok(())
    }

    /// Startup anchor: probe the cursor row and anchor the inline viewport there,
    /// preserving the user's existing scrollback (Codex-style inline viewport).
    /// Only resets attributes/scroll region — no ED2/ED3 clears. When the probe
    /// fails, anchor at the bottom row so growth scrolls prior content up into
    /// scrollback instead of repainting over it.
    pub(crate) fn anchor_viewport_at_cursor_preserving_scrollback(
        &mut self,
        cursor_row: Option<u16>,
    ) -> Result<()> {
        let screen = self.current_screen_size()?;
        self.screen_size = screen;
        write!(self.backend, "\x1b[r\x1b[0m")?;
        Write::flush(&mut self.backend)?;
        let cursor_row = cursor_row.unwrap_or_else(|| screen.height.saturating_sub(1));
        self.set_viewport_area(anchored_viewport_area(screen, cursor_row));
        self.cursor_position = None;
        self.last_known_cursor_pos =
            Position::new(0, cursor_row.min(screen.height.saturating_sub(1)));
        self.pending_history_cells.clear();
        self.last_history_cell = None;
        self.previous_buffer = None;
        Ok(())
    }

    pub(crate) fn invalidate_viewport(&mut self) {
        self.previous_buffer = None;
    }

    fn draw<F>(&mut self, render_callback: F) -> Result<()>
    where
        F: FnOnce(&mut InlineFrame),
    {
        let area = Rect::new(0, 0, self.viewport_area.width, self.viewport_area.height);
        let mut frame = InlineFrame {
            area,
            buffer: Buffer::empty(area),
            cursor_position: None,
        };
        render_callback(&mut frame);
        self.cursor_position = frame.cursor_position.map(|position| {
            Position::new(
                self.viewport_area.x.saturating_add(position.x),
                self.viewport_area.y.saturating_add(position.y),
            )
        });
        self.flush_buffer(&frame.buffer)?;
        self.previous_buffer = Some(frame.buffer);
        self.flush_cursor()?;
        Write::flush(&mut self.backend)?;
        Ok(())
    }

    pub(crate) fn hide_cursor(&mut self) -> Result<()> {
        queue!(self.backend, Hide)?;
        self.cursor_hidden = true;
        Write::flush(&mut self.backend)?;
        Ok(())
    }

    pub(crate) fn enter_alternate_screen(&mut self) -> Result<()> {
        if self.alternate_screen_active {
            return Ok(());
        }
        queue!(
            self.backend,
            EnterAlternateScreen,
            Clear(ClearType::All),
            Hide
        )?;
        // Codex alternate scroll: wheel maps to arrow keys while alt screen is
        // active so pagers scroll without enabling mouse capture (native
        // selection keeps working in the main view).
        write!(self.backend, "\x1b[?1007h")?;
        self.cursor_hidden = true;
        self.alternate_screen_active = true;
        self.previous_buffer = None;
        Write::flush(&mut self.backend)?;
        Ok(())
    }

    pub(crate) fn leave_alternate_screen_if_needed(&mut self) -> Result<()> {
        if !self.alternate_screen_active {
            return Ok(());
        }
        write!(self.backend, "\x1b[?1007l")?;
        queue!(self.backend, LeaveAlternateScreen, Hide)?;
        self.cursor_hidden = true;
        self.alternate_screen_active = false;
        self.previous_buffer = None;
        Write::flush(&mut self.backend)?;
        Ok(())
    }

    /// Relinquish the entire inline surface before stdout returns to a shell.
    /// The editing cursor may be above status/footer rows and is not an exit
    /// boundary. No lifecycle or persistence claim is made by moving it.
    pub(crate) fn finish_inline(&mut self) -> Result<()> {
        self.leave_alternate_screen_if_needed()?;
        let row = self
            .viewport_area
            .bottom()
            .saturating_sub(1)
            .min(self.screen_size.height.saturating_sub(1));
        queue!(
            self.backend,
            MoveTo(0, row),
            ResetColor,
            SetAttribute(Attribute::Reset),
            Print("\r\n"),
            Clear(ClearType::FromCursorDown),
            Show
        )?;
        Write::flush(&mut self.backend)?;
        self.cursor_hidden = false;
        self.cursor_position = None;
        Ok(())
    }

    /// Remove transient controls while keeping the rendered history above them.
    /// `history_rows` is relative to the final Main viewport, never the editing cursor.
    pub(crate) fn finish_inline_at(&mut self, history_rows: u16) -> Result<()> {
        self.leave_alternate_screen_if_needed()?;
        let history_end = self.viewport_area.y.saturating_add(history_rows);
        let row = history_end.min(self.screen_size.height.saturating_sub(1));
        queue!(
            self.backend,
            ResetColor,
            SetAttribute(Attribute::Reset),
            MoveTo(0, row)
        )?;
        if history_end >= self.screen_size.height {
            queue!(self.backend, Print("\r\n"))?;
        }
        queue!(self.backend, Clear(ClearType::FromCursorDown), Show)?;
        Write::flush(&mut self.backend)?;
        self.cursor_hidden = false;
        self.cursor_position = None;
        Ok(())
    }

    fn flush_buffer(&mut self, buffer: &Buffer) -> Result<()> {
        for y in 0..buffer.area.height {
            if self.row_matches_previous(buffer, y) {
                continue;
            }
            let row_y = self.viewport_area.y.saturating_add(y);
            let base_cell = buffer.get(0, y);
            queue!(
                self.backend,
                MoveTo(0, row_y),
                ResetColor,
                SetAttribute(Attribute::Reset)
            )?;
            if let Some(fg) = color_to_crossterm(base_cell.fg) {
                queue!(self.backend, SetForegroundColor(fg))?;
            }
            if let Some(bg) = color_to_crossterm(base_cell.bg) {
                queue!(self.backend, SetBackgroundColor(bg))?;
            }
            queue!(self.backend, Clear(ClearType::UntilNewLine))?;

            let mut active_fg = Some(base_cell.fg);
            let mut active_bg = Some(base_cell.bg);
            let mut active_modifier = Modifier::empty();
            let mut x = 0;
            let printable_width = buffer.area.width.saturating_sub(1).max(1);
            while x < printable_width {
                let cell = buffer.get(x, y);
                let symbol = cell.symbol();
                let symbol_width = UnicodeWidthStr::width(symbol).max(1) as u16;
                if should_leave_cell_cleared(cell, base_cell.bg) {
                    x = x.saturating_add(symbol_width);
                    continue;
                }

                queue!(self.backend, MoveTo(x, row_y))?;
                if cell.modifier != active_modifier {
                    queue!(self.backend, SetAttribute(Attribute::Reset))?;
                    active_modifier = cell.modifier;
                    apply_modifier(&mut self.backend, active_modifier)?;
                    active_fg = None;
                    active_bg = None;
                }
                if cell.fg != active_fg.unwrap_or(Color::Reset)
                    || cell.bg != active_bg.unwrap_or(Color::Reset)
                {
                    active_fg = Some(cell.fg);
                    active_bg = Some(cell.bg);
                    queue!(self.backend, ResetColor)?;
                    apply_modifier(&mut self.backend, active_modifier)?;
                    if let Some(fg) = color_to_crossterm(cell.fg) {
                        queue!(self.backend, SetForegroundColor(fg))?;
                    }
                    if let Some(bg) = color_to_crossterm(cell.bg) {
                        queue!(self.backend, SetBackgroundColor(bg))?;
                    }
                }
                queue!(self.backend, Print(symbol))?;
                x = x.saturating_add(symbol_width);
            }
            queue!(self.backend, ResetColor, SetAttribute(Attribute::Reset))?;
        }
        Ok(())
    }

    fn row_matches_previous(&self, buffer: &Buffer, y: u16) -> bool {
        let Some(previous) = self.previous_buffer.as_ref() else {
            return false;
        };
        if previous.area != buffer.area {
            return false;
        }
        (0..buffer.area.width).all(|x| buffer.get(x, y) == previous.get(x, y))
    }

    fn flush_cursor(&mut self) -> Result<()> {
        if let Some(cursor) = self.cursor_position {
            self.last_known_cursor_pos = cursor;
            queue!(self.backend, MoveTo(cursor.x, cursor.y), Show)?;
            self.cursor_hidden = false;
        } else if !self.cursor_hidden {
            queue!(self.backend, Hide)?;
            self.cursor_hidden = true;
        }
        Ok(())
    }
}

#[derive(Debug, PartialEq, Eq)]
struct ViewportReflow {
    area: Rect,
    scroll_up: Option<(std::ops::Range<u16>, u16)>,
    needs_clear: bool,
    needs_full_repaint: bool,
}

fn viewport_reflow(
    previous_area: Rect,
    previous_screen_size: Size,
    current_screen_size: Size,
    height: u16,
) -> ViewportReflow {
    let previous_screen_size = sanitize_screen_size(previous_screen_size);
    let current_screen_size = sanitize_screen_size(current_screen_size);
    let terminal_height_shrank = current_screen_size.height < previous_screen_size.height;

    let mut area = previous_area;
    area.height = height.min(current_screen_size.height).max(1);
    area.width = current_screen_size.width.max(1);
    let mut scroll_up = None;

    if area.bottom() > current_screen_size.height {
        let scroll_by = area.bottom().saturating_sub(current_screen_size.height);
        if !terminal_height_shrank && area.top() > 0 {
            scroll_up = Some((0..area.top(), scroll_by));
        }
        area.y = current_screen_size.height.saturating_sub(area.height);
    }

    let needs_full_repaint = area != previous_area;
    ViewportReflow {
        area,
        scroll_up,
        needs_clear: needs_full_repaint,
        needs_full_repaint,
    }
}

fn sanitize_screen_size(size: Size) -> Size {
    Size::new(size.width.max(1), size.height.max(1))
}

fn anchored_viewport_area(screen: Size, cursor_row: u16) -> Rect {
    let row = cursor_row.min(screen.height.saturating_sub(1));
    Rect::new(
        0,
        row,
        screen.width.max(1),
        screen.height.saturating_sub(row).max(1),
    )
}

#[cfg(test)]
fn target_inline_viewport_y(
    previous_area: Rect,
    current_screen_size: Size,
    viewport_height: u16,
) -> u16 {
    let reflow = viewport_reflow(
        previous_area,
        current_screen_size,
        current_screen_size,
        viewport_height,
    );
    reflow.area.y
}

#[cfg(test)]
fn clear_anchor_for_viewport_change(previous_area: Rect, next_area: Rect) -> Option<u16> {
    (previous_area != next_area).then_some(if previous_area.is_empty() {
        next_area.y
    } else {
        previous_area.y.min(next_area.y)
    })
}

fn stale_viewport_tail_after_history_flush(previous_area: Rect, next_area: Rect) -> Option<Rect> {
    if previous_area.width == 0
        || previous_area.height == 0
        || next_area.width == 0
        || next_area.height == 0
    {
        return None;
    }
    let stale_top = next_area.bottom();
    let stale_bottom = previous_area.bottom();
    if next_area.y < previous_area.y || stale_top >= stale_bottom {
        return None;
    }
    Some(Rect {
        x: previous_area.x,
        y: stale_top,
        width: previous_area.width,
        height: stale_bottom.saturating_sub(stale_top),
    })
}

pub(crate) struct InlineFrame {
    area: Rect,
    buffer: Buffer,
    cursor_position: Option<Position>,
}

impl InlineFrame {
    pub(crate) fn size(&self) -> Rect {
        self.area
    }

    pub(crate) fn buffer_mut(&mut self) -> &mut Buffer {
        &mut self.buffer
    }

    pub(crate) fn render_widget<W: Widget>(&mut self, widget: W, area: Rect) {
        widget.render(area, &mut self.buffer);
    }

    pub(crate) fn set_cursor(&mut self, x: u16, y: u16) {
        self.cursor_position = Some(Position { x, y });
    }
}

fn should_leave_cell_cleared(cell: &ratatui::buffer::Cell, base_bg: Color) -> bool {
    cell.symbol() == " " && cell.bg == base_bg && cell.modifier == Modifier::empty()
}

fn color_to_crossterm(color: Color) -> Option<CrosstermColor> {
    match color {
        Color::Reset => None,
        Color::Black => Some(CrosstermColor::Black),
        Color::Red => Some(CrosstermColor::DarkRed),
        Color::Green => Some(CrosstermColor::DarkGreen),
        Color::Yellow => Some(CrosstermColor::DarkYellow),
        Color::Blue => Some(CrosstermColor::DarkBlue),
        Color::Magenta => Some(CrosstermColor::DarkMagenta),
        Color::Cyan => Some(CrosstermColor::DarkCyan),
        Color::Gray => Some(CrosstermColor::Grey),
        Color::DarkGray => Some(CrosstermColor::DarkGrey),
        Color::LightRed => Some(CrosstermColor::Red),
        Color::LightGreen => Some(CrosstermColor::Green),
        Color::LightYellow => Some(CrosstermColor::Yellow),
        Color::LightBlue => Some(CrosstermColor::Blue),
        Color::LightMagenta => Some(CrosstermColor::Magenta),
        Color::LightCyan => Some(CrosstermColor::Cyan),
        Color::White => Some(CrosstermColor::White),
        Color::Rgb(r, g, b) => Some(CrosstermColor::Rgb { r, g, b }),
        Color::Indexed(index) => Some(CrosstermColor::AnsiValue(index)),
    }
}

fn apply_modifier<W: Write>(
    writer: &mut CrosstermBackend<W>,
    modifier: Modifier,
) -> io::Result<()> {
    if modifier.contains(Modifier::BOLD) {
        queue!(writer, SetAttribute(Attribute::Bold))?;
    }
    if modifier.contains(Modifier::DIM) {
        queue!(writer, SetAttribute(Attribute::Dim))?;
    }
    if modifier.contains(Modifier::ITALIC) {
        queue!(writer, SetAttribute(Attribute::Italic))?;
    }
    if modifier.contains(Modifier::UNDERLINED) {
        queue!(writer, SetAttribute(Attribute::Underlined))?;
    }
    if modifier.contains(Modifier::REVERSED) {
        queue!(writer, SetAttribute(Attribute::Reverse))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::any::Any;

    #[test]
    fn set_viewport_area_invalidates_previous_buffer() {
        let stdout = io::stdout();
        let mut terminal = InlineTerminal {
            backend: CrosstermBackend::new(stdout),
            viewport_area: Rect::new(0, 0, 80, 24),
            screen_size: Size::new(80, 24),
            previous_buffer: Some(Buffer::empty(Rect::new(0, 0, 80, 24))),
            cursor_position: None,
            last_known_cursor_pos: Position::new(0, 0),
            pending_history_cells: Vec::new(),
            last_history_cell: None,
            cursor_hidden: false,
            alternate_screen_active: false,
            history_render_mode: HistoryRenderMode::Rich,
        };

        terminal.set_viewport_area(Rect::new(0, 0, 80, 8));

        assert!(terminal.previous_buffer.is_none());
    }

    #[test]
    fn screen_size_query_does_not_commit_last_known_size() {
        let stdout = io::stdout();
        let mut terminal = InlineTerminal {
            backend: CrosstermBackend::new(stdout),
            viewport_area: Rect::new(0, 0, 80, 24),
            screen_size: Size::new(11, 7),
            previous_buffer: None,
            cursor_position: None,
            last_known_cursor_pos: Position::new(0, 0),
            pending_history_cells: Vec::new(),
            last_history_cell: None,
            cursor_hidden: false,
            alternate_screen_active: false,
            history_render_mode: HistoryRenderMode::Rich,
        };

        let _ = terminal.screen_size();

        assert_eq!(terminal.screen_size, Size::new(11, 7));
    }

    #[test]
    fn terminal_owns_pending_history_cells_until_clear() {
        let stdout = io::stdout();
        let mut terminal = InlineTerminal {
            backend: CrosstermBackend::new(stdout),
            viewport_area: Rect::new(0, 0, 80, 24),
            screen_size: Size::new(80, 24),
            previous_buffer: None,
            cursor_position: None,
            last_known_cursor_pos: Position::new(0, 0),
            pending_history_cells: Vec::new(),
            last_history_cell: None,
            cursor_hidden: false,
            alternate_screen_active: false,
            history_render_mode: HistoryRenderMode::Rich,
        };

        terminal.insert_history_cells(vec![
            Arc::new(TestHistoryCell {
                text: "hello",
                spacing: history_cell::HistoryCellSpacingKind::Other,
            }),
            Arc::new(TestHistoryCell {
                text: "world",
                spacing: history_cell::HistoryCellSpacingKind::Other,
            }),
        ]);

        assert_eq!(terminal.pending_history_cells.len(), 2);
        terminal.clear_pending_history_lines();
        assert!(terminal.pending_history_cells.is_empty());
    }

    #[test]
    fn scrollback_rebuild_carries_authoritative_lines_and_spacing_tail() {
        let last_history_cell: Arc<dyn HistoryCell> = Arc::new(TestHistoryCell {
            text: "Task failed before producing a final answer.",
            spacing: history_cell::HistoryCellSpacingKind::Other,
        });
        let rebuild = ScrollbackRebuild::new(
            vec![
                HyperlinkLine::plain(Line::from("Failed (33s)")),
                HyperlinkLine::plain(Line::from("Task failed before producing a final answer.")),
            ],
            Some(last_history_cell),
        );

        let lines = rebuild
            .lines
            .iter()
            .map(|line| {
                line.line
                    .spans
                    .iter()
                    .map(|span| span.content.as_ref())
                    .collect::<String>()
            })
            .collect::<Vec<_>>()
            .join("\n");
        assert_eq!(lines.matches("Failed (33s)").count(), 1);
        assert_eq!(
            lines
                .matches("Task failed before producing a final answer.")
                .count(),
            1
        );
        assert!(rebuild.last_history_cell.is_some());
    }

    #[test]
    fn required_stream_repaint_invalidates_viewport_even_without_resize() {
        let mut terminal = test_terminal();
        terminal.previous_buffer = Some(Buffer::empty(Rect::new(0, 0, 80, 24)));

        terminal.finish_agent_stream_repaint(Some(StreamViewportRepaint::Required), false);

        assert!(terminal.previous_buffer.is_none());
    }

    #[test]
    fn if_resize_stream_repaint_only_invalidates_when_resize_reflow_ran() {
        let mut terminal = test_terminal();
        terminal.previous_buffer = Some(Buffer::empty(Rect::new(0, 0, 80, 24)));

        terminal.finish_agent_stream_repaint(Some(StreamViewportRepaint::IfResizeReflowRan), false);
        assert!(terminal.previous_buffer.is_some());

        terminal.finish_agent_stream_repaint(Some(StreamViewportRepaint::IfResizeReflowRan), true);
        assert!(terminal.previous_buffer.is_none());
    }

    #[derive(Debug)]
    struct TestHistoryCell {
        text: &'static str,
        spacing: history_cell::HistoryCellSpacingKind,
    }

    impl HistoryCell for TestHistoryCell {
        fn display_lines(&self, _width: usize) -> Vec<Line<'static>> {
            vec![Line::from(self.text)]
        }

        fn raw_lines(&self) -> Vec<Line<'static>> {
            vec![Line::from(self.text)]
        }

        fn spacing_kind(&self) -> history_cell::HistoryCellSpacingKind {
            self.spacing
        }

        fn as_any(&self) -> &dyn Any {
            self
        }
    }

    #[derive(Debug)]
    struct EdgedHistoryCell;

    impl HistoryCell for EdgedHistoryCell {
        fn display_lines(&self, _width: usize) -> Vec<Line<'static>> {
            vec![Line::from("select me")]
        }

        fn edge_color(&self) -> Option<Color> {
            Some(Color::Red)
        }

        fn raw_lines(&self) -> Vec<Line<'static>> {
            vec![Line::from("select me")]
        }

        fn as_any(&self) -> &dyn Any {
            self
        }
    }

    fn test_terminal() -> InlineTerminal {
        InlineTerminal {
            backend: CrosstermBackend::new(io::stdout()),
            viewport_area: Rect::new(0, 0, 80, 24),
            screen_size: Size::new(80, 24),
            previous_buffer: None,
            cursor_position: None,
            last_known_cursor_pos: Position::new(0, 0),
            pending_history_cells: Vec::new(),
            last_history_cell: None,
            cursor_hidden: false,
            alternate_screen_active: false,
            history_render_mode: HistoryRenderMode::Rich,
        }
    }

    fn plain_lines(lines: Vec<HyperlinkLine>) -> Vec<String> {
        lines
            .into_iter()
            .map(|line| {
                line.line
                    .spans
                    .into_iter()
                    .map(|span| span.content.into_owned())
                    .collect::<String>()
            })
            .collect()
    }

    #[test]
    fn terminal_history_cell_spacing_is_preserved_across_flushes() {
        let mut terminal = test_terminal();

        terminal.insert_history_cells(vec![Arc::new(TestHistoryCell {
            text: "• Ran pre-run",
            spacing: history_cell::HistoryCellSpacingKind::Other,
        })]);
        assert_eq!(
            plain_lines(terminal.take_pending_history_lines()),
            vec!["• Ran pre-run"]
        );

        terminal.insert_history_cells(vec![Arc::new(TestHistoryCell {
            text: "• Explored (2)",
            spacing: history_cell::HistoryCellSpacingKind::Other,
        })]);
        assert_eq!(
            plain_lines(terminal.take_pending_history_lines()),
            vec!["", "• Explored (2)"],
            "terminal, not AppState, owns cross-frame history spacing"
        );
    }

    #[test]
    fn raw_history_mode_removes_decorative_rail_from_physical_scrollback() {
        let mut terminal = test_terminal();
        terminal.insert_history_cells(vec![Arc::new(EdgedHistoryCell)]);
        assert_eq!(
            plain_lines(terminal.take_pending_history_lines()),
            vec!["▏ select me"]
        );

        terminal.set_history_render_mode(HistoryRenderMode::Raw);
        terminal.pending_history_cells = vec![Arc::new(EdgedHistoryCell)];
        terminal.last_history_cell = None;
        assert_eq!(
            plain_lines(terminal.take_pending_history_lines()),
            vec!["select me"],
            "raw physical history must contain only source text"
        );
    }

    #[test]
    fn terminal_history_cell_spacing_does_not_add_blank_before_user_prompt() {
        let mut terminal = test_terminal();

        terminal.insert_history_cells(vec![Arc::new(TestHistoryCell {
            text: "• 你好",
            spacing: history_cell::HistoryCellSpacingKind::Other,
        })]);
        let _ = terminal.take_pending_history_lines();

        terminal.insert_history_cells(vec![Arc::new(TestHistoryCell {
            text: "› follow-up",
            spacing: history_cell::HistoryCellSpacingKind::User,
        })]);
        assert_eq!(
            plain_lines(terminal.take_pending_history_lines()),
            vec!["› follow-up"],
            "user prompt spacing must match Codex-style compact prompt insertion"
        );
    }

    #[test]
    fn terminal_adds_blank_between_user_prompt_and_task_header_across_flushes() {
        let mut terminal = test_terminal();

        terminal.insert_history_cells(vec![Arc::new(TestHistoryCell {
            text: "hello",
            spacing: history_cell::HistoryCellSpacingKind::User,
        })]);
        let _ = terminal.take_pending_history_lines();

        terminal.insert_history_cells(vec![Arc::new(TestHistoryCell {
            text: "⦁ Interpreted Task",
            spacing: history_cell::HistoryCellSpacingKind::TaskHeader,
        })]);
        assert_eq!(
            plain_lines(terminal.take_pending_history_lines()),
            vec!["", "⦁ Interpreted Task"]
        );
    }

    #[test]
    fn zero_sized_screen_is_sanitized_before_viewport_reflow() {
        let previous_area = Rect::new(0, 0, 0, 0);
        let previous_screen = Size::new(0, 0);
        let current_screen = Size::new(0, 0);
        let reflow = viewport_reflow(previous_area, previous_screen, current_screen, 0);

        assert_eq!(reflow.area, Rect::new(0, 0, 1, 1));
        assert_eq!(reflow.scroll_up, None);
        assert!(reflow.needs_full_repaint);
    }

    #[test]
    fn startup_anchor_uses_cursor_row_and_clamps_to_screen() {
        let screen = Size::new(100, 30);

        assert_eq!(
            anchored_viewport_area(screen, 20),
            Rect::new(0, 20, 100, 10)
        );
        assert_eq!(anchored_viewport_area(screen, 0), Rect::new(0, 0, 100, 30));
        assert_eq!(anchored_viewport_area(screen, 29), Rect::new(0, 29, 100, 1));
        assert_eq!(
            anchored_viewport_area(screen, 999),
            Rect::new(0, 29, 100, 1),
            "probe garbage must clamp to the last row"
        );
    }

    #[test]
    fn history_flush_clears_old_viewport_tail_when_live_area_shrinks() {
        let stale = stale_viewport_tail_after_history_flush(
            Rect::new(0, 0, 80, 10),
            Rect::new(0, 2, 80, 4),
        );

        assert_eq!(stale, Some(Rect::new(0, 6, 80, 4)));
    }

    #[test]
    fn history_flush_does_not_clear_when_new_viewport_covers_old_tail() {
        let stale =
            stale_viewport_tail_after_history_flush(Rect::new(0, 0, 80, 6), Rect::new(0, 2, 80, 6));

        assert_eq!(stale, None);
    }

    #[test]
    fn history_flush_does_not_clear_above_new_viewport() {
        let stale =
            stale_viewport_tail_after_history_flush(Rect::new(0, 4, 80, 8), Rect::new(0, 2, 80, 6));

        assert_eq!(stale, None);
    }

    #[test]
    fn viewport_alignment_stays_put_when_height_fits() {
        let previous_area = Rect::new(0, 0, 80, 16);
        let current_screen = Size::new(80, 24);
        assert_eq!(
            target_inline_viewport_y(previous_area, current_screen, 8),
            0
        );
        assert_eq!(
            target_inline_viewport_y(previous_area, current_screen, 20),
            0
        );
    }

    #[test]
    fn viewport_height_growth_scrolls_history_before_bottom_aligning() {
        let previous_area = Rect::new(0, 20, 80, 4);
        let current_screen = Size::new(80, 24);
        let reflow = viewport_reflow(previous_area, current_screen, current_screen, 5);

        assert_eq!(reflow.area, Rect::new(0, 19, 80, 5));
        assert_eq!(reflow.scroll_up, Some((0..20, 1)));
        assert!(reflow.needs_clear);
    }

    #[test]
    fn viewport_screen_growth_preserves_inline_anchor() {
        let previous_area = Rect::new(0, 16, 80, 8);
        let previous_screen = Size::new(80, 24);
        let current_screen = Size::new(80, 30);
        let reflow = viewport_reflow(previous_area, previous_screen, current_screen, 8);

        assert_eq!(
            reflow.area,
            Rect::new(0, 16, 80, 8),
            "terminal growth must not bottom-pin the live chat viewport and leave blank rows"
        );
        assert_eq!(reflow.scroll_up, None);
        assert!(!reflow.needs_clear);
        assert!(!reflow.needs_full_repaint);
    }

    #[test]
    fn viewport_growth_clear_is_safe_after_scroll_up() {
        let previous_area = Rect::new(0, 20, 80, 4);
        let next_area = Rect::new(0, 19, 80, 5);

        assert_eq!(
            clear_anchor_for_viewport_change(previous_area, next_area),
            Some(19)
        );
    }

    #[test]
    fn viewport_change_clears_from_earliest_live_boundary() {
        let previous_area = Rect::new(0, 18, 80, 6);
        let next_area = Rect::new(0, 20, 80, 4);

        assert_eq!(
            clear_anchor_for_viewport_change(previous_area, next_area),
            Some(18)
        );
    }

    #[test]
    fn startup_viewport_shrink_does_not_force_bottom_alignment() {
        let previous_area = Rect::new(0, 0, 80, 24);
        let screen = Size::new(80, 24);
        let reflow = viewport_reflow(previous_area, screen, screen, 5);

        assert_eq!(reflow.area, Rect::new(0, 0, 80, 5));
        assert_eq!(reflow.scroll_up, None);
        assert!(reflow.needs_clear);
    }

    #[test]
    fn viewport_shrink_after_history_flush_keeps_top_anchor() {
        let previous_area = Rect::new(0, 18, 80, 6);
        let screen = Size::new(80, 24);
        let reflow = viewport_reflow(previous_area, screen, screen, 4);

        assert_eq!(reflow.area, Rect::new(0, 18, 80, 4));
        assert_eq!(reflow.scroll_up, None);
        assert!(reflow.needs_clear);
    }

    #[test]
    fn viewport_shrink_from_bottom_does_not_keep_composer_pinned_to_screen_bottom() {
        let previous_area = Rect::new(0, 16, 80, 8);
        let screen = Size::new(80, 24);
        let reflow = viewport_reflow(previous_area, screen, screen, 4);

        assert_eq!(
            reflow.area,
            Rect::new(0, 16, 80, 4),
            "resize reflow must not invent a new history anchor while shrinking the live viewport"
        );
        assert_eq!(reflow.scroll_up, None);
        assert!(reflow.needs_clear);
    }

    #[test]
    fn resize_reflow_has_no_extra_history_boundary_anchor() {
        let previous_area = Rect::new(0, 18, 112, 6);
        let screen = Size::new(112, 24);
        let reflow = viewport_reflow(previous_area, screen, screen, 6);

        assert_eq!(
            reflow.area, previous_area,
            "terminal resize reflow must not use an app-level history boundary anchor"
        );
        assert_eq!(reflow.scroll_up, None);
        assert!(!reflow.needs_clear);
        assert!(!reflow.needs_full_repaint);
    }
}

#[cfg(test)]
mod color_pipeline_probe_tests {
    use ratatui::buffer::Buffer;
    use ratatui::layout::Rect;
    use ratatui::style::{Color, Style};
    use ratatui::text::{Line, Span};
    use ratatui::widgets::{Paragraph, Widget};

    #[test]
    fn paragraph_span_colors_reach_buffer_cells() {
        let area = Rect::new(0, 0, 20, 1);
        let mut buffer = Buffer::empty(area);
        let line = Line::from(Span::styled(
            "hello",
            Style::default().fg(Color::Rgb(1, 2, 3)),
        ));
        Paragraph::new(vec![line]).render(area, &mut buffer);

        assert_eq!(buffer.get(0, 0).fg, Color::Rgb(1, 2, 3));
    }
}
