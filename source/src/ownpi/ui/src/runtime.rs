//! Terminal lifecycle and event transport; no Runtime state or recovery policy.
use std::io::{self, Write};
use std::process::ChildStdin;
use std::sync::mpsc::{Receiver, TryRecvError};
use std::time::Duration;

use anyhow::Result;
use crossterm::{
    event::{self, Event, KeyEventKind},
    execute, terminal,
};
use serde_json::json;

use crate::app::AppState;
use crate::bridge::{command_writer::CommandWriter, BridgeEvent};
use crate::inline_terminal::InlineTerminal;

pub(crate) fn prepare_terminal(
    terminal: &mut InlineTerminal,
    cursor_row: Option<u16>,
    keyboard: Option<bool>,
) -> Result<()> {
    execute!(
        terminal.backend_mut(),
        event::EnableBracketedPaste,
        event::EnableFocusChange
    )?;
    if keyboard == Some(true) {
        let _ = execute!(
            terminal.backend_mut(),
            event::PushKeyboardEnhancementFlags(
                event::KeyboardEnhancementFlags::DISAMBIGUATE_ESCAPE_CODES
                    .union(event::KeyboardEnhancementFlags::REPORT_ALTERNATE_KEYS)
            )
        );
    }
    terminal.anchor_viewport_at_cursor_preserving_scrollback(cursor_row)?;
    terminal.hide_cursor()?;
    Ok(())
}

pub(crate) fn restore_modes() -> Result<()> {
    let _ = execute!(
        io::stdout(),
        event::PopKeyboardEnhancementFlags,
        event::DisableFocusChange,
        event::DisableBracketedPaste,
        event::DisableMouseCapture,
        terminal::LeaveAlternateScreen,
        crossterm::cursor::Show
    );
    let _ = write!(io::stdout(), "\x1b[>u\x1b[>4;0m\x1b[0m");
    terminal::disable_raw_mode()?;
    io::stdout().flush()?;
    Ok(())
}

// crossterm 0.27 may spin on a vanished TTY. Closing the Bridge input triggers
// its ordinary backend stop path; this watchdog owns no execution processes.
fn hangup_watchdog(bridge_fd: i32) {
    std::thread::spawn(move || loop {
        let mut poll = libc::pollfd {
            fd: 0,
            events: libc::POLLIN,
            revents: 0,
        };
        let ready = unsafe { libc::poll(&mut poll, 1, 1000) };
        if ready > 0 && poll.revents & (libc::POLLHUP | libc::POLLERR) != 0 {
            unsafe {
                libc::close(bridge_fd);
            }
            let _ = restore_modes();
            std::process::exit(1);
        }
    });
}

pub(crate) fn run(
    terminal: &mut InlineTerminal,
    app: &mut AppState,
    pipe: ChildStdin,
    rx: Receiver<Result<BridgeEvent, String>>,
    resume: Option<String>,
) -> Result<()> {
    use std::os::fd::AsRawFd;
    hangup_watchdog(pipe.as_raw_fd());
    let writer = CommandWriter::spawn(pipe);
    writer.send(app.request("initialize", json!({})))?;
    let mut startup_resume = resume;
    let mut dirty = true;
    let mut disconnected = false;
    loop {
        if let Some(error) = writer.take_error() {
            app.disconnected(&error);
            disconnected = true;
            dirty = true;
        }
        for _ in 0..64 {
            match rx.try_recv() {
                Ok(Ok(event)) => {
                    let bootstrap = event.r#type == "bootstrap";
                    app.event(event);
                    if bootstrap {
                        if let Some(target) = startup_resume.take() {
                            writer.send(app.request("resume", json!({"target": target})))?;
                        }
                    }
                    dirty = true;
                }
                Ok(Err(message)) => {
                    app.disconnected(&message);
                    disconnected = true;
                    dirty = true;
                    break;
                }
                Err(TryRecvError::Empty) => break,
                Err(TryRecvError::Disconnected) => break,
            }
        }
        if app.edit_draft || app.editor_path.is_some() {
            if let Err(error) = edit(terminal, app) {
                app.status.set_notice(Some(format!("Editor: {error}")));
            }
            dirty = true;
        }
        if std::mem::take(&mut app.clear_terminal) {
            let screen = terminal.screen_size()?;
            terminal.clear_scrollback_and_visible_screen_ansi(screen)?;
            dirty = true;
        }
        for effect in app.terminal_effects.drain(..) {
            write!(io::stdout(), "{effect}")?;
        }
        io::stdout().flush()?;
        if app.bottom_pane.flush_pending_input_if_due() {
            dirty = true;
        }
        if app.poll_file_search() {
            dirty = true;
        }
        if app.tick_status() {
            dirty = true;
        }
        if app.status.animates() {
            app.status.tick_animation();
            dirty = true;
        }
        if dirty && !app.bottom_pane.is_in_paste_burst() {
            if app.agents.popup.is_some() || (app.agents.active.is_some() && app.pager.is_none()) {
                let screen = terminal.screen_size()?;
                app.note_layout_size(screen.width as usize, screen.height as usize);
                app.refresh_agent_pager();
                crate::view::draw_agent_view(terminal, app)?;
            } else if app.pager.is_some() {
                let screen = terminal.screen_size()?;
                app.note_layout_size(screen.width as usize, screen.height as usize);
                app.refresh_pager();
                crate::view::draw_transcript_pager(terminal, app)?;
            } else {
                crate::view::draw(terminal, app)?;
            }
            dirty = false;
        }
        if app.should_quit {
            break;
        }
        if event::poll(Duration::from_millis(16))? {
            let commands = match event::read()? {
                Event::Key(key)
                    if matches!(key.kind, KeyEventKind::Press | KeyEventKind::Repeat) =>
                {
                    app.handle_key(key)
                }
                Event::Paste(text) => {
                    if app.agents.popup.is_none()
                        && (app.agents.active.is_none()
                            || app.bottom_pane.active_bottom_view_is_visible())
                    {
                        app.bottom_pane.insert_paste(&text);
                    }
                    Vec::new()
                }
                Event::Resize(width, height) => {
                    app.note_layout_size(width as usize, height as usize);
                    app.rebuild = true;
                    Vec::new()
                }
                Event::FocusGained => {
                    app.terminal_focused = true;
                    Vec::new()
                }
                Event::FocusLost => {
                    app.terminal_focused = false;
                    Vec::new()
                }
                _ => Vec::new(),
            };
            for command in commands {
                if disconnected {
                    if command["method"] == "shutdown" {
                        app.should_quit = true;
                    } else {
                        app.command_not_sent(&command);
                        app.status.set_notice(Some(
                            "Disconnected. Restart OwnPI to use backend actions.".into(),
                        ));
                    }
                } else if let Err(error) = writer.send(command.clone()) {
                    app.command_not_sent(&command);
                    app.disconnected(&error.to_string());
                    disconnected = true;
                }
            }
            dirty = true;
        }
    }
    Ok(())
}

// Parse editor arguments as words, never execute a shell expression.
fn editor_words(value: &str) -> Result<Vec<String>> {
    let mut words = Vec::new();
    let mut word = String::new();
    let mut quote = None;
    let mut escape = false;
    for ch in value.chars() {
        if escape {
            word.push(ch);
            escape = false;
            continue;
        }
        if ch == '\\' && quote != Some('\'') {
            escape = true;
            continue;
        }
        if let Some(delimiter) = quote {
            if ch == delimiter {
                quote = None;
            } else {
                word.push(ch);
            }
        } else if ch == '\'' || ch == '"' {
            quote = Some(ch);
        } else if ch.is_whitespace() {
            if !word.is_empty() {
                words.push(std::mem::take(&mut word));
            }
        } else {
            word.push(ch);
        }
    }
    anyhow::ensure!(
        quote.is_none() && !escape,
        "Invalid quoting in VISUAL/EDITOR"
    );
    if !word.is_empty() {
        words.push(word);
    }
    anyhow::ensure!(!words.is_empty(), "VISUAL/EDITOR is empty");
    Ok(words)
}

fn edit(terminal: &mut InlineTerminal, app: &mut AppState) -> Result<()> {
    use std::os::unix::fs::OpenOptionsExt;
    let draft = std::mem::take(&mut app.edit_draft);
    let editor = std::env::var("VISUAL")
        .or_else(|_| std::env::var("EDITOR"))
        .unwrap_or_else(|_| "vi".into());
    let args = editor_words(&editor)?;
    let path = if draft {
        let path = std::env::temp_dir().join(format!(
            "ownpi-draft-{}-{}.md",
            std::process::id(),
            app.request_counter
        ));
        let mut file = std::fs::OpenOptions::new()
            .create_new(true)
            .write(true)
            .mode(0o600)
            .open(&path)?;
        file.write_all(app.bottom_pane.composer_text_with_pending().as_bytes())?;
        path
    } else {
        app.editor_path
            .take()
            .ok_or_else(|| anyhow::anyhow!("Missing editor path"))?
    };
    terminal.leave_alternate_screen_if_needed()?;
    restore_modes()?;
    let result = std::process::Command::new(&args[0])
        .args(&args[1..])
        .arg(&path)
        .status();
    terminal::enable_raw_mode()?;
    prepare_terminal(terminal, None, None)?;
    if let Ok(status) = &result {
        if status.success() && draft {
            app.bottom_pane
                .set_composer(std::fs::read_to_string(&path)?.trim_end_matches('\n'));
        }
    }
    if draft {
        let _ = std::fs::remove_file(&path);
    }
    anyhow::ensure!(
        result?.success(),
        "Editor exited without confirming a saved edit"
    );
    app.rebuild = true;
    app.status.set_notice(Some(
        if draft {
            "Draft updated"
        } else {
            "Memory edited; applies to subsequent prompt assembly"
        }
        .into(),
    ));
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn editor_arguments_preserve_quotes_without_interpreting_shell_code() {
        assert_eq!(
            editor_words("'/Applications/My Editor' --wait '$(not-executed)' ").unwrap(),
            vec!["/Applications/My Editor", "--wait", "$(not-executed)"]
        );
        assert!(editor_words("editor 'unterminated").is_err());
    }
}
