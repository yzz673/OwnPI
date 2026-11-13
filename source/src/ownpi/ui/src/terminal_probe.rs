//! One short, best-effort startup probe for cursor, palette, and keyboard support.
//!
//! Crossterm's individual queries can each wait for seconds. OwnPI batches the
//! optional terminal queries under one 100 ms deadline before normal input
//! polling begins, matching Codex's startup ownership model.

use ratatui::layout::Position;
use std::io;
use std::time::Duration;

pub(crate) const DEFAULT_TIMEOUT: Duration = Duration::from_millis(100);

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct StartupProbe {
    pub(crate) cursor_position: Option<Position>,
    pub(crate) foreground: Option<(u8, u8, u8)>,
    pub(crate) background: Option<(u8, u8, u8)>,
    pub(crate) keyboard_enhancement_supported: Option<bool>,
    pub(crate) stray_input: String,
}

#[cfg(unix)]
pub(crate) fn startup(timeout: Duration) -> io::Result<StartupProbe> {
    imp::startup(timeout)
}

#[cfg(not(unix))]
pub(crate) fn startup(_timeout: Duration) -> io::Result<StartupProbe> {
    Ok(StartupProbe::default())
}

#[cfg(unix)]
mod imp {
    use super::*;
    use std::fs::{File, OpenOptions};
    use std::io::Write;
    use std::os::fd::{AsRawFd, FromRawFd};
    use std::time::Instant;

    struct Tty {
        reader: File,
        writer: File,
        original_flags: libc::c_int,
    }

    impl Tty {
        fn open() -> io::Result<Self> {
            let reader = dup_file(libc::STDIN_FILENO)
                .or_else(|_| OpenOptions::new().read(true).open("/dev/tty"))?;
            let writer = dup_file(libc::STDOUT_FILENO)
                .or_else(|_| OpenOptions::new().write(true).open("/dev/tty"))?;
            let fd = reader.as_raw_fd();
            let original_flags = unsafe { libc::fcntl(fd, libc::F_GETFL) };
            if original_flags == -1
                || unsafe { libc::fcntl(fd, libc::F_SETFL, original_flags | libc::O_NONBLOCK) }
                    == -1
            {
                return Err(io::Error::last_os_error());
            }
            Ok(Self {
                reader,
                writer,
                original_flags,
            })
        }

        fn read_available(&self, buffer: &mut Vec<u8>) -> io::Result<()> {
            let mut chunk = [0_u8; 256];
            loop {
                let count = unsafe {
                    libc::read(
                        self.reader.as_raw_fd(),
                        chunk.as_mut_ptr().cast::<libc::c_void>(),
                        chunk.len(),
                    )
                };
                if count > 0 {
                    buffer.extend_from_slice(&chunk[..count as usize]);
                    continue;
                }
                if count == 0 {
                    return Ok(());
                }
                let error = io::Error::last_os_error();
                if matches!(
                    error.kind(),
                    io::ErrorKind::WouldBlock | io::ErrorKind::Interrupted
                ) {
                    return Ok(());
                }
                return Err(error);
            }
        }

        fn poll_readable(&self, timeout: Duration) -> io::Result<bool> {
            let mut descriptor = libc::pollfd {
                fd: self.reader.as_raw_fd(),
                events: libc::POLLIN,
                revents: 0,
            };
            let millis = timeout.as_millis().min(libc::c_int::MAX as u128) as libc::c_int;
            let result = unsafe { libc::poll(&mut descriptor, 1, millis) };
            if result < 0 {
                Err(io::Error::last_os_error())
            } else {
                Ok(result > 0 && descriptor.revents & libc::POLLIN != 0)
            }
        }
    }

    impl Drop for Tty {
        fn drop(&mut self) {
            let _ =
                unsafe { libc::fcntl(self.reader.as_raw_fd(), libc::F_SETFL, self.original_flags) };
        }
    }

    fn dup_file(fd: libc::c_int) -> io::Result<File> {
        let duplicated = unsafe { libc::dup(fd) };
        if duplicated == -1 {
            return Err(io::Error::last_os_error());
        }
        Ok(unsafe { File::from_raw_fd(duplicated) })
    }

    pub(super) fn startup(timeout: Duration) -> io::Result<StartupProbe> {
        let mut tty = Tty::open()?;
        tty.writer
            .write_all(b"\x1b[6n\x1b]10;?\x1b\\\x1b]11;?\x1b\\\x1b[?u\x1b[c")?;
        tty.writer.flush()?;
        let deadline = Instant::now() + timeout;
        let mut buffer = Vec::new();
        loop {
            tty.read_available(&mut buffer)?;
            let probe = parse_startup_probe(&buffer);
            if probe.cursor_position.is_some()
                && probe.foreground.is_some()
                && probe.background.is_some()
                && probe.keyboard_enhancement_supported.is_some()
            {
                return Ok(probe);
            }
            let now = Instant::now();
            if now >= deadline || !tty.poll_readable(deadline.saturating_duration_since(now))? {
                return Ok(probe);
            }
        }
    }
}

fn parse_startup_probe(buffer: &[u8]) -> StartupProbe {
    let keyboard_response = find_csi(buffer, b'u').filter(|payload| payload.starts_with(b"?"));
    let device_attributes = find_csi(buffer, b'c').filter(|payload| payload.starts_with(b"?"));
    StartupProbe {
        cursor_position: parse_cursor_position(buffer),
        foreground: parse_osc_color(buffer, 10),
        background: parse_osc_color(buffer, 11),
        keyboard_enhancement_supported: if keyboard_response.is_some() {
            Some(true)
        } else if device_attributes.is_some() {
            Some(false)
        } else {
            None
        },
        stray_input: extract_stray_input(buffer),
    }
}

fn find_csi(buffer: &[u8], terminator: u8) -> Option<&[u8]> {
    let mut index = 0;
    while index + 2 < buffer.len() {
        if buffer[index..].starts_with(b"\x1b[") {
            let start = index + 2;
            for (relative_end, byte) in buffer[start..].iter().copied().enumerate() {
                if (0x40..=0x7e).contains(&byte) {
                    if byte == terminator {
                        return Some(&buffer[start..start + relative_end]);
                    }
                    break;
                }
            }
        }
        index += 1;
    }
    None
}

fn parse_cursor_position(buffer: &[u8]) -> Option<Position> {
    let payload = find_csi(buffer, b'R')?;
    let payload = std::str::from_utf8(payload).ok()?;
    let (row, column) = payload.split_once(';')?;
    Some(Position::new(
        column.parse::<u16>().ok()?.saturating_sub(1),
        row.parse::<u16>().ok()?.saturating_sub(1),
    ))
}

fn parse_osc_color(buffer: &[u8], slot: u8) -> Option<(u8, u8, u8)> {
    let marker = format!("\x1b]{slot};rgb:");
    let start = buffer
        .windows(marker.len())
        .position(|part| part == marker.as_bytes())?
        + marker.len();
    let text = std::str::from_utf8(&buffer[start..]).ok()?;
    let mut parts = text.split('/');
    Some((
        parse_hex_component(parts.next()?)?,
        parse_hex_component(parts.next()?)?,
        parse_hex_component(parts.next()?)?,
    ))
}

fn parse_hex_component(value: &str) -> Option<u8> {
    let hex = value
        .chars()
        .take_while(|character| character.is_ascii_hexdigit())
        .take(4)
        .collect::<String>();
    let raw = u32::from_str_radix(&hex, 16).ok()?;
    Some(match hex.len() {
        1 => (raw * 17) as u8,
        2 => raw as u8,
        length => (raw >> (4 * (length - 2))) as u8,
    })
}

fn extract_stray_input(buffer: &[u8]) -> String {
    let mut plain = Vec::new();
    let mut index = 0;
    while index < buffer.len() {
        if buffer[index] != 0x1b {
            plain.push(buffer[index]);
            index += 1;
            continue;
        }
        if buffer.get(index + 1) == Some(&b'[') {
            index += 2;
            while index < buffer.len() {
                let byte = buffer[index];
                index += 1;
                if (0x40..=0x7e).contains(&byte) {
                    break;
                }
            }
        } else if buffer.get(index + 1) == Some(&b']') {
            index += 2;
            while index < buffer.len() {
                if buffer[index] == 0x07 {
                    index += 1;
                    break;
                }
                if buffer[index..].starts_with(b"\x1b\\") {
                    index += 2;
                    break;
                }
                index += 1;
            }
        } else {
            index += 2;
        }
    }
    String::from_utf8_lossy(&plain).into_owned()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_batched_responses_and_preserves_plain_input() {
        let probe = parse_startup_probe(
            b"hi\x1b[20;10R\x1b]10;rgb:ffff/0000/8000\x07\x1b]11;rgb:1111/2222/3333\x1b\\\x1b[?1u\x1b[?62;c",
        );
        assert_eq!(probe.cursor_position, Some(Position::new(9, 19)));
        assert_eq!(probe.foreground, Some((255, 0, 128)));
        assert_eq!(probe.background, Some((17, 34, 51)));
        assert_eq!(probe.keyboard_enhancement_supported, Some(true));
        assert_eq!(probe.stray_input, "hi");
    }

    #[test]
    fn device_attributes_without_keyboard_flags_is_unsupported() {
        let probe = parse_startup_probe(b"\x1b[?62;c");
        assert_eq!(probe.keyboard_enhancement_supported, Some(false));
    }
}
