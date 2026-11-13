use ratatui::style::{Color, Style};
use ratatui::text::{Line, Span};
use unicode_width::UnicodeWidthStr;

use crate::theme::ThemePalette;

/// Codex user-message tint: blend the terminal background slightly toward
/// black on light themes (4%) and toward white on dark themes (12%).
fn user_message_background(theme: &ThemePalette) -> Color {
    let base = theme.surface_bg.or(theme.elevated_bg).or(theme.shadow_bg);
    let Some((red, green, blue)) = base.and_then(crate::theme::color_rgb) else {
        return base.unwrap_or(crate::style::FALLBACK_BG);
    };
    let (target, ratio) = if theme.is_light_background() {
        (0.0f32, 0.04f32)
    } else {
        (255.0f32, 0.12f32)
    };
    let blend = |channel: u8| -> u8 {
        (f32::from(channel) * (1.0 - ratio) + target * ratio)
            .round()
            .clamp(0.0, 255.0) as u8
    };
    theme.display_color(Color::Rgb(blend(red), blend(green), blend(blue)))
}

pub(crate) fn render_user_prompt(
    message: &str,
    width: usize,
    theme: &ThemePalette,
) -> Vec<Line<'static>> {
    let line_width = width.max(1);
    let wrapped = wrap_plain(message.trim_end_matches(['\r', '\n']), line_width);
    // Codex style.rs:8-44 parity: user message background is a subtle tint
    // blended from the terminal background (4% black on light, 12% on dark).
    let bg = user_message_background(theme);
    let fg = if theme.is_light_background() {
        theme.text
    } else {
        theme.display_color(crate::style::FALLBACK_FG)
    };
    let cell_style = Style::default().bg(bg);
    let text_style = Style::default().fg(fg).bg(bg);

    let mut lines = vec![filled_line(line_width, cell_style)];
    for segment in wrapped {
        lines.push(user_prompt_line(
            &segment, line_width, cell_style, text_style,
        ));
    }
    lines.push(filled_line(line_width, cell_style));
    lines
}

pub(crate) fn render_plain(text: &str, width: usize, theme: &ThemePalette) -> Vec<Line<'static>> {
    wrap_plain(text, width)
        .into_iter()
        .map(|line| Line::from(Span::styled(line, Style::default().fg(theme.text))))
        .collect()
}

#[cfg(test)]
pub(crate) fn render_agent_response(
    markdown: &str,
    width: usize,
    theme: &ThemePalette,
) -> Vec<Line<'static>> {
    render_agent_markdown_with_bullet(
        markdown,
        width,
        theme,
        None,
        crate::style::activity_marker_style("completed", theme),
    )
}

pub(crate) fn render_agent_response_with_cwd(
    markdown: &str,
    width: usize,
    theme: &ThemePalette,
    cwd: Option<&str>,
) -> Vec<Line<'static>> {
    render_agent_markdown_with_bullet(
        markdown,
        width,
        theme,
        cwd,
        crate::style::activity_marker_style("completed", theme),
    )
}

#[cfg(test)]
pub(crate) fn render_agent_live_response_with_cwd(
    markdown: &str,
    width: usize,
    theme: &ThemePalette,
    cwd: Option<&str>,
) -> Vec<Line<'static>> {
    render_agent_markdown_with_bullet(
        markdown,
        width,
        theme,
        cwd,
        crate::style::activity_marker_style("running", theme),
    )
}

fn render_agent_markdown_with_bullet(
    markdown: &str,
    width: usize,
    theme: &ThemePalette,
    cwd: Option<&str>,
    marker_style: Style,
) -> Vec<Line<'static>> {
    crate::terminal_hyperlinks::visible_lines(render_agent_markdown_hyperlinks(
        markdown,
        width,
        theme,
        cwd,
        marker_style,
    ))
}

pub(super) fn render_agent_markdown_hyperlinks(
    markdown: &str,
    width: usize,
    theme: &ThemePalette,
    cwd: Option<&str>,
    marker_style: Style,
) -> Vec<crate::terminal_hyperlinks::HyperlinkLine> {
    use crate::terminal_hyperlinks::{prefix_hyperlink_lines, HyperlinkLine};
    let Some(content_width) = crate::width::usable_content_width(width, 2) else {
        return vec![HyperlinkLine::new(Line::from(Span::styled(
            crate::style::GLYPH_AGENT_BULLET.to_string(),
            marker_style,
        )))];
    };
    let mut used_bullet = false;
    crate::markdown::render_markdown_hyperlinks(markdown, content_width, theme, cwd)
        .into_iter()
        .map(|line| {
            if line
                .line
                .spans
                .iter()
                .all(|span| span.content.trim().is_empty())
            {
                return line;
            }
            let prefix = if used_bullet {
                "  "
            } else {
                used_bullet = true;
                crate::style::GLYPH_AGENT_BULLET
            };
            prefix_hyperlink_lines(
                vec![line],
                Span::styled(prefix, marker_style),
                Span::styled("  ", marker_style),
            )
            .remove(0)
        })
        .collect()
}

fn wrap_plain(text: &str, width: usize) -> Vec<String> {
    let mut lines = Vec::new();
    for line in text.lines() {
        let wrapped = crate::wrapping::adaptive_wrap_text(line, width.max(1));
        if wrapped.is_empty() {
            lines.push(String::new());
        } else {
            lines.extend(wrapped);
        }
    }
    if lines.is_empty() && text.is_empty() {
        lines.push(String::new());
    }
    lines
}

fn filled_line(width: usize, style: Style) -> Line<'static> {
    Line::from(Span::styled(" ".repeat(width), style)).style(style)
}

fn user_prompt_line(
    segment: &str,
    width: usize,
    cell_style: Style,
    text_style: Style,
) -> Line<'static> {
    let padding = width.saturating_sub(UnicodeWidthStr::width(segment));
    Line::from(vec![
        Span::styled(segment.to_string(), text_style),
        Span::styled(" ".repeat(padding), cell_style),
    ])
    .style(cell_style)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn plain(line: &Line<'_>) -> String {
        line.spans
            .iter()
            .map(|span| span.content.as_ref())
            .collect::<String>()
    }

    #[test]
    fn user_prompt_renders_with_gray_background_padding() {
        let lines = render_user_prompt("hello", 80, &ThemePalette::default());

        assert_eq!(lines.len(), 3);
        assert_eq!(lines[1].spans[0].content.as_ref(), "hello");
        assert_eq!(lines[1].spans.len(), 2);
        assert!(lines.iter().all(|line| line.style.bg.is_some()));
        assert!(!lines
            .iter()
            .map(plain)
            .any(|line| line.contains('\u{00a0}')));
    }

    #[test]
    fn user_prompt_preserves_literal_gutter_characters() {
        let lines = render_user_prompt("▎ ▎   多组实验\n普通文本", 80, &ThemePalette::default());
        let rendered = lines.iter().map(plain).collect::<Vec<_>>().join("\n");

        assert!(rendered.contains("▎ ▎   多组实验"), "{rendered}");
        assert!(rendered.contains("多组实验"), "{rendered}");
        assert!(rendered.contains("普通文本"), "{rendered}");
    }
}

#[cfg(test)]
mod user_tint_tests {
    use super::*;

    #[test]
    fn light_theme_tints_background_four_percent_toward_black() {
        let theme = ThemePalette::default();

        let bg = user_message_background(&theme);

        assert_eq!(bg, Color::Rgb(245, 245, 245));
    }

    #[test]
    fn dark_theme_tints_background_twelve_percent_toward_white() {
        let theme = ThemePalette::from_terminal_colors_for_level(
            Some(Color::Rgb(240, 240, 240)),
            Some(Color::Rgb(0, 0, 0)),
            crate::theme::TerminalColorLevel::TrueColor,
        );

        let bg = user_message_background(&theme);

        assert_eq!(bg, Color::Rgb(31, 31, 31));
    }

    #[test]
    fn ansi256_white_prompt_has_subtle_gray_tint_and_dark_text() {
        let theme = ThemePalette::from_terminal_colors_for_level(
            Some(Color::Rgb(17, 17, 17)),
            Some(Color::Rgb(255, 255, 255)),
            crate::theme::TerminalColorLevel::Ansi256,
        );
        assert_eq!(user_message_background(&theme), Color::Indexed(255));
        for width in [40, 80, 120] {
            let lines = render_user_prompt("帮我调研 cross Subject neural decoding", width, &theme);
            for line in lines {
                for span in line.spans {
                    assert_eq!(span.style.bg, Some(Color::Indexed(255)));
                    if !span.content.trim().is_empty() {
                        assert_eq!(span.style.fg, Some(theme.text));
                    }
                }
            }
        }
    }

    #[test]
    fn ansi256_dark_prompt_keeps_white_tint_without_rgb_output() {
        let theme = ThemePalette::from_terminal_colors_for_level(
            Some(Color::Rgb(238, 238, 238)),
            Some(Color::Rgb(0, 0, 0)),
            crate::theme::TerminalColorLevel::Ansi256,
        );
        assert_eq!(user_message_background(&theme), Color::Indexed(234));
        let lines = render_user_prompt("中文 draft", 40, &theme);
        assert!(lines
            .iter()
            .flat_map(|l| &l.spans)
            .all(|s| matches!(s.style.bg, Some(Color::Indexed(16..=255)))));
        assert!(lines
            .iter()
            .flat_map(|l| &l.spans)
            .filter_map(|s| s.style.fg)
            .all(|color| matches!(color, Color::Indexed(16..=255))));
    }
}
