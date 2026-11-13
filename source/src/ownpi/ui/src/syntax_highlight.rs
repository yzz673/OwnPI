use std::sync::OnceLock;
use std::sync::RwLock;

use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use syntect::easy::HighlightLines;
use syntect::highlighting::{
    Color as SyntectColor, FontStyle, Style as SyntectStyle, Theme, ThemeSet,
};
use syntect::parsing::{SyntaxReference, SyntaxSet};
use syntect::util::LinesWithEndings;

const MAX_HIGHLIGHT_BYTES: usize = 512 * 1024;
const MAX_HIGHLIGHT_LINES: usize = 10_000;

static SYNTAX_SET: OnceLock<SyntaxSet> = OnceLock::new();
static THEME_SET: OnceLock<ThemeSet> = OnceLock::new();
static SELECTED_THEME: RwLock<Option<String>> = RwLock::new(None);

fn syntax_set() -> &'static SyntaxSet {
    SYNTAX_SET.get_or_init(SyntaxSet::load_defaults_newlines)
}

/// Built-in syntect themes plus user themes from `~/.neuro-agent/themes/*.tmTheme`
/// (Codex `$CODEX_HOME/themes` parity).
fn theme_set() -> &'static ThemeSet {
    THEME_SET.get_or_init(|| {
        let mut set = ThemeSet::load_defaults();
        if let Some(home) = std::env::var_os("HOME") {
            let folder = std::path::Path::new(&home)
                .join(".neuro-agent")
                .join("themes");
            if folder.is_dir() {
                if let Err(error) = set.add_from_folder(&folder) {
                    eprintln!("failed to load custom themes from {folder:?}: {error}");
                }
            }
        }
        set
    })
}

fn theme_by_name_or_default(name: &str, fallback_light: bool) -> Theme {
    let set = theme_set();
    set.themes
        .get(name)
        .cloned()
        .unwrap_or_else(|| default_theme(fallback_light))
}

fn default_theme(light: bool) -> Theme {
    let set = theme_set();
    let preferred = if light {
        ["InspiredGitHub", "base16-ocean.light"]
    } else {
        ["base16-ocean.dark", "base16-ocean.dark"]
    };
    for name in preferred {
        if let Some(theme) = set.themes.get(name) {
            return theme.clone();
        }
    }
    set.themes.values().next().cloned().unwrap_or_default()
}

fn active_theme(light_background: bool) -> Theme {
    if let Ok(selected) = SELECTED_THEME.read() {
        if let Some(name) = selected.as_deref() {
            return theme_by_name_or_default(name, light_background);
        }
    }
    default_theme(light_background)
}

pub(crate) fn available_theme_names() -> Vec<String> {
    let mut names = theme_set().themes.keys().cloned().collect::<Vec<_>>();
    names.sort();
    names
}

pub(crate) fn select_theme(name: &str) -> bool {
    if name == "default" {
        if let Ok(mut selected) = SELECTED_THEME.write() {
            *selected = None;
        }
        return true;
    }
    if !theme_set().themes.contains_key(name) {
        return false;
    }
    if let Ok(mut selected) = SELECTED_THEME.write() {
        *selected = Some(name.to_string());
    }
    true
}

fn find_syntax(lang: &str) -> Option<&'static SyntaxReference> {
    let lang = lang.trim();
    if lang.is_empty() {
        return None;
    }
    let patched = match lang {
        "csharp" | "c-sharp" => "c#",
        "golang" => "go",
        "python3" => "python",
        "shell" => "bash",
        "sh" => "bash",
        "ts" => "typescript",
        "js" => "javascript",
        _ => lang,
    };
    let syntaxes = syntax_set();
    syntaxes
        .find_syntax_by_token(patched)
        .or_else(|| syntaxes.find_syntax_by_name(patched))
        .or_else(|| {
            let lower = patched.to_ascii_lowercase();
            syntaxes
                .syntaxes()
                .iter()
                .find(|syntax| syntax.name.to_ascii_lowercase() == lower)
        })
        .or_else(|| syntaxes.find_syntax_by_extension(lang))
}

fn exceeds_highlight_limits(code: &str) -> bool {
    code.len() > MAX_HIGHLIGHT_BYTES || code.lines().count() > MAX_HIGHLIGHT_LINES
}

fn convert_color(color: SyntectColor) -> Option<Color> {
    match color.a {
        0x01 => None,
        _ => Some(Color::Rgb(color.r, color.g, color.b)),
    }
}

fn convert_style(syntect_style: SyntectStyle) -> Style {
    let mut style = Style::default();
    if let Some(fg) = convert_color(syntect_style.foreground) {
        style = style.fg(fg);
    }
    if syntect_style.font_style.contains(FontStyle::BOLD) {
        style.add_modifier |= Modifier::BOLD;
    }
    style
}

pub(crate) fn highlight_code_to_lines(
    code: &str,
    lang: &str,
    light_background: bool,
) -> Option<Vec<Line<'static>>> {
    if code.is_empty() || exceeds_highlight_limits(code) {
        return None;
    }
    let syntax = find_syntax(lang)?;
    let theme = active_theme(light_background);
    let mut highlighter = HighlightLines::new(syntax, &theme);
    let mut lines = Vec::new();
    for line in LinesWithEndings::from(code) {
        let ranges = highlighter.highlight_line(line, syntax_set()).ok()?;
        let mut spans = Vec::new();
        for (style, text) in ranges {
            let text = text.trim_end_matches(['\n', '\r']);
            if text.is_empty() {
                continue;
            }
            spans.push(Span::styled(text.to_string(), convert_style(style)));
        }
        lines.push(Line::from(spans));
    }
    Some(lines)
}

#[cfg(test)]
pub(crate) fn exceeds_highlight_limits_for_test(code: &str) -> bool {
    exceeds_highlight_limits(code)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn text(lines: &[Line<'static>]) -> Vec<String> {
        lines
            .iter()
            .map(|line| {
                line.spans
                    .iter()
                    .map(|span| span.content.as_ref())
                    .collect::<String>()
            })
            .collect()
    }

    #[test]
    fn known_language_returns_colored_spans() {
        let lines = highlight_code_to_lines("fn main() {}\n", "rust", false).expect("rust syntax");
        assert_eq!(text(&lines), vec!["fn main() {}"]);
        assert!(
            lines
                .iter()
                .flat_map(|line| line.spans.iter())
                .any(|span| span.style.fg.is_some()),
            "expected at least one highlighted span"
        );
    }

    #[test]
    fn unknown_language_falls_back_to_plain_path() {
        assert!(highlight_code_to_lines("hello\n", "xyzlang", false).is_none());
    }

    #[test]
    fn highlight_guardrails_reject_oversized_inputs() {
        assert!(exceeds_highlight_limits_for_test(
            &"x".repeat(MAX_HIGHLIGHT_BYTES + 1)
        ));
        assert!(exceeds_highlight_limits_for_test(
            &"let x = 1;\n".repeat(MAX_HIGHLIGHT_LINES + 1)
        ));
    }
}
