//! Clipboard backend for the UI-owned `/copy` command.
//!
//! This follows Codex's backend order: remote sessions use terminal-mediated
//! copy (tmux, then OSC 52); local sessions prefer the native clipboard and
//! fall back to terminal-mediated copy. Linux keeps a clipboard lease alive.

use base64::Engine;
use std::fmt;
use std::io::Write;
use std::process::{Command, Stdio};

const OSC52_MAX_RAW_BYTES: usize = 100_000;

#[cfg(target_os = "macos")]
static STDERR_SUPPRESSION_MUTEX: std::sync::OnceLock<std::sync::Mutex<()>> =
    std::sync::OnceLock::new();

pub(crate) struct ClipboardLease {
    #[cfg(target_os = "linux")]
    _clipboard: Option<arboard::Clipboard>,
}

impl fmt::Debug for ClipboardLease {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("ClipboardLease")
    }
}

impl ClipboardLease {
    #[cfg(target_os = "linux")]
    fn native_linux(clipboard: arboard::Clipboard) -> Self {
        Self {
            _clipboard: Some(clipboard),
        }
    }

    #[cfg(test)]
    pub(crate) fn test() -> Self {
        Self {
            #[cfg(target_os = "linux")]
            _clipboard: None,
        }
    }
}

pub(crate) fn copy_to_clipboard(text: &str) -> Result<Option<ClipboardLease>, String> {
    if text.is_empty() {
        return Err("nothing to copy".to_string());
    }
    let environment = CopyEnvironment {
        ssh_session: is_ssh_session(),
        wsl_session: is_wsl_session(),
        tmux_session: is_tmux_session(),
    };
    copy_to_clipboard_with(
        text,
        environment,
        tmux_clipboard_copy,
        |value| osc52_copy(value, environment.tmux_session),
        arboard_copy,
        wsl_clipboard_copy,
    )
}

#[derive(Clone, Copy)]
struct CopyEnvironment {
    ssh_session: bool,
    wsl_session: bool,
    tmux_session: bool,
}

fn copy_to_clipboard_with(
    text: &str,
    environment: CopyEnvironment,
    tmux_copy_fn: impl Fn(&str) -> Result<(), String>,
    osc52_copy_fn: impl Fn(&str) -> Result<(), String>,
    native_copy_fn: impl Fn(&str) -> Result<Option<ClipboardLease>, String>,
    wsl_copy_fn: impl Fn(&str) -> Result<(), String>,
) -> Result<Option<ClipboardLease>, String> {
    if environment.ssh_session {
        return terminal_clipboard_copy_with(
            text,
            environment.tmux_session,
            &tmux_copy_fn,
            &osc52_copy_fn,
        )
        .map(|()| None)
        .map_err(|error| format!("terminal clipboard copy failed over SSH: {error}"));
    }
    match native_copy_fn(text) {
        Ok(lease) => Ok(lease),
        Err(native_error) if environment.wsl_session => match wsl_copy_fn(text) {
            Ok(()) => Ok(None),
            Err(wsl_error) => terminal_clipboard_copy_with(
                text,
                environment.tmux_session,
                &tmux_copy_fn,
                &osc52_copy_fn,
            )
            .map(|()| None)
            .map_err(|terminal_error| {
                format!(
                    "native clipboard: {native_error}; WSL fallback: {wsl_error}; terminal fallback: {terminal_error}"
                )
            }),
        },
        Err(native_error) => terminal_clipboard_copy_with(
            text,
            environment.tmux_session,
            &tmux_copy_fn,
            &osc52_copy_fn,
        )
        .map(|()| None)
        .map_err(|terminal_error| {
            format!("native clipboard: {native_error}; terminal fallback: {terminal_error}")
        }),
    }
}

fn is_ssh_session() -> bool {
    std::env::var_os("SSH_TTY").is_some() || std::env::var_os("SSH_CONNECTION").is_some()
}

fn is_tmux_session() -> bool {
    std::env::var_os("TMUX").is_some() || std::env::var_os("TMUX_PANE").is_some()
}

#[cfg(target_os = "linux")]
fn is_wsl_session() -> bool {
    std::env::var_os("WSL_DISTRO_NAME").is_some()
        || std::fs::read_to_string("/proc/sys/kernel/osrelease")
            .is_ok_and(|value| value.to_ascii_lowercase().contains("microsoft"))
}

#[cfg(not(target_os = "linux"))]
fn is_wsl_session() -> bool {
    false
}

fn terminal_clipboard_copy_with(
    text: &str,
    tmux: bool,
    tmux_copy_fn: &impl Fn(&str) -> Result<(), String>,
    osc52_copy_fn: &impl Fn(&str) -> Result<(), String>,
) -> Result<(), String> {
    if tmux {
        match tmux_copy_fn(text) {
            Ok(()) => return Ok(()),
            Err(tmux_error) => {
                return osc52_copy_fn(text).map_err(|osc_error| {
                    format!("tmux clipboard: {tmux_error}; OSC 52 fallback: {osc_error}")
                });
            }
        }
    }
    osc52_copy_fn(text)
}

#[cfg(all(not(target_os = "android"), not(target_os = "linux")))]
fn arboard_copy(text: &str) -> Result<Option<ClipboardLease>, String> {
    #[cfg(target_os = "macos")]
    let _stderr_lock = STDERR_SUPPRESSION_MUTEX
        .get_or_init(|| std::sync::Mutex::new(()))
        .lock()
        .map_err(|_| "stderr suppression lock poisoned".to_string())?;
    let _guard = SuppressStderr::new();
    let mut clipboard =
        arboard::Clipboard::new().map_err(|error| format!("clipboard unavailable: {error}"))?;
    clipboard
        .set_text(text)
        .map_err(|error| format!("failed to set clipboard text: {error}"))?;
    Ok(None)
}

#[cfg(target_os = "linux")]
fn arboard_copy(text: &str) -> Result<Option<ClipboardLease>, String> {
    let _guard = SuppressStderr::new();
    let mut clipboard =
        arboard::Clipboard::new().map_err(|error| format!("clipboard unavailable: {error}"))?;
    clipboard
        .set_text(text)
        .map_err(|error| format!("failed to set clipboard text: {error}"))?;
    Ok(Some(ClipboardLease::native_linux(clipboard)))
}

#[cfg(target_os = "android")]
fn arboard_copy(_text: &str) -> Result<Option<ClipboardLease>, String> {
    Err("native clipboard unavailable on Android".to_string())
}

#[cfg(target_os = "linux")]
fn wsl_clipboard_copy(text: &str) -> Result<(), String> {
    pipe_to_command(
        "powershell.exe",
        &[
            "-NoProfile",
            "-Command",
            "[Console]::InputEncoding = [System.Text.Encoding]::UTF8; $ErrorActionPreference = 'Stop'; $text = [Console]::In.ReadToEnd(); Set-Clipboard -Value $text",
        ],
        text,
    )
}

#[cfg(not(target_os = "linux"))]
fn wsl_clipboard_copy(_text: &str) -> Result<(), String> {
    Err("WSL clipboard fallback unavailable on this platform".to_string())
}

fn tmux_clipboard_copy(text: &str) -> Result<(), String> {
    tmux_clipboard_copy_ready(
        || tmux_command_output(&["show-options", "-gv", "set-clipboard"]),
        || tmux_command_output(&["info"]),
    )?;
    pipe_to_command("tmux", &["load-buffer", "-w", "-"], text)
}

fn tmux_clipboard_copy_ready(
    set_clipboard_fn: impl FnOnce() -> Result<String, String>,
    tmux_info_fn: impl FnOnce() -> Result<String, String>,
) -> Result<(), String> {
    if set_clipboard_fn()?.trim() == "off" {
        return Err("tmux clipboard forwarding is disabled".to_string());
    }
    if tmux_info_fn()?
        .lines()
        .any(|line| line.contains("Ms: [missing]"))
    {
        return Err("tmux clipboard forwarding is unavailable: missing Ms capability".to_string());
    }
    Ok(())
}

fn tmux_command_output(args: &[&str]) -> Result<String, String> {
    let output = Command::new("tmux")
        .args(args)
        .output()
        .map_err(|error| format!("failed to spawn tmux: {error}"))?;
    if output.status.success() {
        String::from_utf8(output.stdout)
            .map_err(|error| format!("tmux output was not UTF-8: {error}"))
    } else {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
        if stderr.is_empty() {
            Err(format!("tmux exited with {}", output.status))
        } else {
            Err(format!("tmux failed: {stderr}"))
        }
    }
}

fn pipe_to_command(binary: &str, args: &[&str], text: &str) -> Result<(), String> {
    let mut child = Command::new(binary)
        .args(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| format!("failed to spawn {binary}: {error}"))?;

    let Some(mut stdin) = child.stdin.take() else {
        let _ = child.kill();
        let _ = child.wait();
        return Err(format!("failed to open {binary} stdin"));
    };
    if let Err(error) = stdin.write_all(text.as_bytes()) {
        let _ = child.kill();
        let _ = child.wait();
        return Err(format!("failed to write to {binary}: {error}"));
    }
    drop(stdin);

    let output = child
        .wait_with_output()
        .map_err(|error| format!("failed to wait for {binary}: {error}"))?;
    if output.status.success() {
        Ok(())
    } else {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
        if stderr.is_empty() {
            Err(format!("{binary} exited with {}", output.status))
        } else {
            Err(format!("{binary} failed: {stderr}"))
        }
    }
}

fn osc52_copy(text: &str, tmux: bool) -> Result<(), String> {
    let sequence = osc52_sequence(text, tmux)?;
    #[cfg(unix)]
    if let Ok(tty) = std::fs::OpenOptions::new().write(true).open("/dev/tty") {
        if write_osc52_to_writer(tty, &sequence).is_ok() {
            return Ok(());
        }
    }
    write_osc52_to_writer(std::io::stdout().lock(), &sequence)
}

fn write_osc52_to_writer(mut writer: impl Write, sequence: &str) -> Result<(), String> {
    writer
        .write_all(sequence.as_bytes())
        .map_err(|error| format!("failed to write OSC 52: {error}"))?;
    writer
        .flush()
        .map_err(|error| format!("failed to flush OSC 52: {error}"))
}

fn osc52_sequence(text: &str, tmux: bool) -> Result<String, String> {
    if text.len() > OSC52_MAX_RAW_BYTES {
        return Err(format!(
            "OSC 52 payload too large ({} bytes; max {OSC52_MAX_RAW_BYTES})",
            text.len()
        ));
    }
    let encoded = base64::engine::general_purpose::STANDARD.encode(text.as_bytes());
    if tmux {
        Ok(format!("\x1bPtmux;\x1b\x1b]52;c;{encoded}\x07\x1b\\"))
    } else {
        Ok(format!("\x1b]52;c;{encoded}\x07"))
    }
}

#[cfg(target_os = "macos")]
struct SuppressStderr {
    saved_fd: Option<libc::c_int>,
}

#[cfg(target_os = "macos")]
impl SuppressStderr {
    fn new() -> Self {
        unsafe {
            let saved = libc::dup(2);
            if saved < 0 {
                return Self { saved_fd: None };
            }
            let devnull = libc::open(c"/dev/null".as_ptr(), libc::O_WRONLY);
            if devnull < 0 || libc::dup2(devnull, 2) < 0 {
                libc::close(saved);
                if devnull >= 0 {
                    libc::close(devnull);
                }
                return Self { saved_fd: None };
            }
            libc::close(devnull);
            Self {
                saved_fd: Some(saved),
            }
        }
    }
}

#[cfg(target_os = "macos")]
impl Drop for SuppressStderr {
    fn drop(&mut self) {
        if let Some(saved) = self.saved_fd {
            unsafe {
                libc::dup2(saved, 2);
                libc::close(saved);
            }
        }
    }
}

#[cfg(not(target_os = "macos"))]
struct SuppressStderr;

#[cfg(not(target_os = "macos"))]
impl SuppressStderr {
    fn new() -> Self {
        Self
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::Cell;

    #[test]
    fn osc52_sequence_supports_plain_and_tmux_terminals() {
        assert_eq!(
            osc52_sequence("hello", false).expect("plain sequence"),
            "\x1b]52;c;aGVsbG8=\x07"
        );
        assert_eq!(
            osc52_sequence("hello", true).expect("tmux sequence"),
            "\x1bPtmux;\x1b\x1b]52;c;aGVsbG8=\x07\x1b\\"
        );
    }

    #[test]
    fn osc52_rejects_oversized_payload() {
        assert!(osc52_sequence(&"x".repeat(OSC52_MAX_RAW_BYTES + 1), false).is_err());
    }

    #[test]
    fn test_lease_is_debuggable() {
        assert_eq!(format!("{:?}", ClipboardLease::test()), "ClipboardLease");
    }

    #[test]
    fn remote_tmux_falls_back_when_forwarding_is_not_ready() {
        let tmux_calls = Cell::new(0);
        let osc_calls = Cell::new(0);
        let result = copy_to_clipboard_with(
            "hello",
            CopyEnvironment {
                ssh_session: true,
                wsl_session: false,
                tmux_session: true,
            },
            |_| {
                tmux_calls.set(tmux_calls.get() + 1);
                Err("missing Ms".to_string())
            },
            |_| {
                osc_calls.set(osc_calls.get() + 1);
                Ok(())
            },
            |_| panic!("remote copy must not use native clipboard"),
            |_| panic!("remote copy must not use WSL clipboard"),
        );
        assert!(matches!(result, Ok(None)));
        assert_eq!(tmux_calls.get(), 1);
        assert_eq!(osc_calls.get(), 1);
    }

    #[test]
    fn ssh_without_tmux_uses_osc52_without_touching_local_clipboards() {
        let osc_calls = Cell::new(0);
        let result = copy_to_clipboard_with(
            "hello",
            CopyEnvironment {
                ssh_session: true,
                wsl_session: false,
                tmux_session: false,
            },
            |_| panic!("plain SSH must not invoke tmux"),
            |_| {
                osc_calls.set(osc_calls.get() + 1);
                Ok(())
            },
            |_| panic!("SSH must not use the native clipboard"),
            |_| panic!("SSH must not use the WSL clipboard"),
        );

        assert!(matches!(result, Ok(None)));
        assert_eq!(osc_calls.get(), 1);
    }

    #[test]
    fn wsl_uses_powershell_fallback_before_terminal_copy() {
        let wsl_calls = Cell::new(0);
        let result = copy_to_clipboard_with(
            "hello",
            CopyEnvironment {
                ssh_session: false,
                wsl_session: true,
                tmux_session: false,
            },
            |_| panic!("non-tmux WSL must not invoke tmux"),
            |_| panic!("successful WSL copy must not reach OSC 52"),
            |_| Err("no local display".to_string()),
            |_| {
                wsl_calls.set(wsl_calls.get() + 1);
                Ok(())
            },
        );

        assert!(matches!(result, Ok(None)));
        assert_eq!(wsl_calls.get(), 1);
    }

    #[test]
    fn local_native_failure_falls_back_to_terminal_copy() {
        let osc_calls = Cell::new(0);
        let result = copy_to_clipboard_with(
            "hello",
            CopyEnvironment {
                ssh_session: false,
                wsl_session: false,
                tmux_session: false,
            },
            |_| panic!("non-tmux local copy must not invoke tmux"),
            |_| {
                osc_calls.set(osc_calls.get() + 1);
                Ok(())
            },
            |_| Err("clipboard unavailable".to_string()),
            |_| panic!("non-WSL local copy must not use PowerShell"),
        );

        assert!(matches!(result, Ok(None)));
        assert_eq!(osc_calls.get(), 1);
    }

    #[test]
    fn tmux_readiness_requires_forwarding_and_ms_capability() {
        assert!(tmux_clipboard_copy_ready(
            || Ok("external\n".to_string()),
            || Ok("193: Ms: (string) \\033]52;%p1%s;%p2%s\\a\n".to_string()),
        )
        .is_ok());
        assert_eq!(
            tmux_clipboard_copy_ready(|| Ok("off\n".to_string()), || Ok(String::new()),),
            Err("tmux clipboard forwarding is disabled".to_string())
        );
        assert_eq!(
            tmux_clipboard_copy_ready(
                || Ok("on\n".to_string()),
                || Ok("193: Ms: [missing]\n".to_string()),
            ),
            Err("tmux clipboard forwarding is unavailable: missing Ms capability".to_string())
        );
    }
}
