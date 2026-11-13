use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};

/// Codex `ansi_escape_line` parity: parse SGR sequences in command output into
/// styled spans; all other escape sequences are dropped so they cannot corrupt
/// the viewport.
pub(crate) fn ansi_escape_line(text: &str, base: Style) -> Line<'static> {
    let segments = parse_ansi_segments(text, base);
    Line::from(
        segments
            .into_iter()
            .map(|(content, style)| Span::styled(content, style))
            .collect::<Vec<_>>(),
    )
}

fn parse_ansi_segments(text: &str, base: Style) -> Vec<(String, Style)> {
    let mut segments: Vec<(String, Style)> = Vec::new();
    let mut style = base;
    let mut current = String::new();
    let mut chars = text.chars().peekable();
    while let Some(ch) = chars.next() {
        if ch != '\x1b' {
            current.push(ch);
            continue;
        }
        if chars.next() != Some('[') {
            continue;
        }
        let mut params = String::new();
        let mut final_byte = None;
        for next in chars.by_ref() {
            if next.is_ascii_alphabetic() {
                final_byte = Some(next);
                break;
            }
            if matches!(next, '0'..='9' | ';' | '?') {
                params.push(next);
                continue;
            }
            // Unknown intermediate byte: abandon the sequence entirely.
            final_byte = None;
            break;
        }
        if !current.is_empty() {
            segments.push((std::mem::take(&mut current), style));
        }
        if final_byte == Some('m') {
            style = apply_sgr(style, base, &params);
        }
    }
    if !current.is_empty() {
        segments.push((current, style));
    }
    if segments.is_empty() {
        segments.push((String::new(), base));
    }
    segments
}

fn apply_sgr(current: Style, base: Style, params: &str) -> Style {
    let mut style = current;
    let codes: Vec<&str> = params.split(';').collect();
    if params.is_empty() {
        return base;
    }
    let mut index = 0;
    while index < codes.len() {
        let code = codes[index];
        index += 1;
        match code {
            "" | "0" => style = base,
            "1" => style = style.add_modifier(Modifier::BOLD),
            "2" => style = style.add_modifier(Modifier::DIM),
            "3" => style = style.add_modifier(Modifier::ITALIC),
            "4" => style = style.add_modifier(Modifier::UNDERLINED),
            "7" => style = style.add_modifier(Modifier::REVERSED),
            "22" => {
                style = style
                    .remove_modifier(Modifier::BOLD)
                    .remove_modifier(Modifier::DIM)
            }
            "23" => style = style.remove_modifier(Modifier::ITALIC),
            "24" => style = style.remove_modifier(Modifier::UNDERLINED),
            "27" => style = style.remove_modifier(Modifier::REVERSED),
            "39" => style = style.fg(base.fg.unwrap_or(Color::Reset)),
            "49" => style = style.bg(base.bg.unwrap_or(Color::Reset)),
            "38" => {
                if let Some((color, consumed)) = parse_extended_color(&codes[index..]) {
                    style = style.fg(color);
                    index += consumed;
                }
            }
            "48" => {
                if let Some((color, consumed)) = parse_extended_color(&codes[index..]) {
                    style = style.bg(color);
                    index += consumed;
                }
            }
            _ => {
                if let Ok(value) = code.parse::<u8>() {
                    style = apply_standard_color(style, value);
                }
            }
        }
    }
    style
}

fn parse_extended_color(codes: &[&str]) -> Option<(Color, usize)> {
    match codes.first().copied() {
        Some("5") => {
            let index = codes.get(1)?.parse::<u8>().ok()?;
            Some((Color::Indexed(index), 2))
        }
        Some("2") => {
            let red = codes.get(1)?.parse::<u8>().ok()?;
            let green = codes.get(2)?.parse::<u8>().ok()?;
            let blue = codes.get(3)?.parse::<u8>().ok()?;
            Some((Color::Rgb(red, green, blue), 4))
        }
        _ => None,
    }
}

fn apply_standard_color(style: Style, code: u8) -> Style {
    const FG: [Color; 8] = [
        Color::Black,
        Color::Red,
        Color::Green,
        Color::Yellow,
        Color::Blue,
        Color::Magenta,
        Color::Cyan,
        Color::Gray,
    ];
    const BG: [Color; 8] = [
        Color::Black,
        Color::Red,
        Color::Green,
        Color::Yellow,
        Color::Blue,
        Color::Magenta,
        Color::Cyan,
        Color::Gray,
    ];
    const FG_BRIGHT: [Color; 8] = [
        Color::DarkGray,
        Color::LightRed,
        Color::LightGreen,
        Color::LightYellow,
        Color::LightBlue,
        Color::LightMagenta,
        Color::LightCyan,
        Color::White,
    ];
    const BG_BRIGHT: [Color; 8] = [
        Color::DarkGray,
        Color::LightRed,
        Color::LightGreen,
        Color::LightYellow,
        Color::LightBlue,
        Color::LightMagenta,
        Color::LightCyan,
        Color::White,
    ];
    match code {
        30..=37 => style.fg(FG[(code - 30) as usize]),
        40..=47 => style.bg(BG[(code - 40) as usize]),
        90..=97 => style.fg(FG_BRIGHT[(code - 90) as usize]),
        100..=107 => style.bg(BG_BRIGHT[(code - 100) as usize]),
        _ => style,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn segment_texts(line: &Line<'static>) -> Vec<(String, Option<Color>)> {
        line.spans
            .iter()
            .map(|span| (span.content.to_string(), span.style.fg))
            .collect()
    }

    #[test]
    fn sgr_color_codes_become_styled_spans() {
        let line = ansi_escape_line("plain \x1b[31mred\x1b[0mnormal", Style::default());

        let segments = segment_texts(&line);
        assert_eq!(
            segments,
            vec![
                ("plain ".to_string(), None),
                ("red".to_string(), Some(Color::Red)),
                ("normal".to_string(), None),
            ]
        );
    }

    #[test]
    fn extended_rgb_color_is_parsed() {
        let line = ansi_escape_line("\x1b[38;2;10;20;30mx", Style::default());

        assert_eq!(line.spans[0].style.fg, Some(Color::Rgb(10, 20, 30)));
    }

    #[test]
    fn non_sgr_sequences_are_dropped() {
        let line = ansi_escape_line("a\x1b[2Kb\x1b[?25lc", Style::default());

        let text: String = line
            .spans
            .iter()
            .map(|span| span.content.to_string())
            .collect();
        assert_eq!(text, "abc");
    }

    #[test]
    fn bright_and_bold_modifiers_apply() {
        let line = ansi_escape_line("\x1b[1;92mhi", Style::default());

        assert_eq!(line.spans[0].style.fg, Some(Color::LightGreen));
        assert!(line.spans[0].style.add_modifier.contains(Modifier::BOLD));
    }
}
