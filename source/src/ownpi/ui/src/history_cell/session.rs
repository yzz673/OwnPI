use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use unicode_width::UnicodeWidthStr;

use super::{HistoryCell, HistoryRenderMode};
use crate::branding::APP_TAGLINE;
use crate::history_cell::base::{bordered_lines, truncate_middle};
use crate::theme::ThemePalette;

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct WelcomeSurface {
    pub(crate) app_name: String,
    pub(crate) model: String,
    pub(crate) reasoning: String,
    pub(crate) workspace_path: String,
    pub(crate) session_id: Option<String>,
    pub(crate) resumed_session: Option<ResumedSessionSurface>,
}

/// Codex `codex resume <id>` parity: a compact card shown under the welcome
/// header when the UI was opened through session resume.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct ResumedSessionSurface {
    pub(crate) session_id: String,
    pub(crate) project_root: String,
    pub(crate) latest_run_path: String,
    pub(crate) resume_hint: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct CompactSessionHeader {
    pub(crate) app_name: String,
    pub(crate) model: String,
    pub(crate) reasoning: String,
    pub(crate) workspace_path: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum SessionSurface {
    Welcome(WelcomeSurface),
    Compact(CompactSessionHeader),
}

/// Startup welcome cell owned and assembled by AppState.
#[derive(Clone, Debug)]
pub(crate) struct WelcomeHistoryCell {
    welcome: WelcomeSurface,
    theme: ThemePalette,
}

impl WelcomeHistoryCell {
    pub(crate) fn new(welcome: WelcomeSurface, theme: ThemePalette) -> Self {
        Self { welcome, theme }
    }
}

impl HistoryCell for WelcomeHistoryCell {
    fn display_lines(&self, width: usize) -> Vec<Line<'static>> {
        render_welcome_surface(&self.welcome, width, &self.theme)
    }

    fn effective_render_mode(&self, _requested: HistoryRenderMode) -> HistoryRenderMode {
        HistoryRenderMode::Rich
    }

    fn raw_lines(&self) -> Vec<Line<'static>> {
        Vec::new()
    }

    fn as_any(&self) -> &dyn std::any::Any {
        self
    }
}

pub(crate) fn render_session_surface(
    surface: &SessionSurface,
    width: usize,
    theme: &ThemePalette,
) -> Vec<Line<'static>> {
    match surface {
        SessionSurface::Welcome(welcome) => render_welcome_surface(welcome, width, theme),
        SessionSurface::Compact(header) => render_compact_session_header(header, width, theme),
    }
}

pub(crate) fn render_welcome_surface(
    welcome: &WelcomeSurface,
    width: usize,
    theme: &ThemePalette,
) -> Vec<Line<'static>> {
    // The hero is borderless and may use the full terminal width; only the
    // resume card keeps the old clamped card width.
    let mut lines = Vec::new();
    lines.extend(session_header_card_lines(welcome, width.max(1), theme));
    if let Some(resumed) = welcome.resumed_session.as_ref() {
        lines.extend(resumed_session_card_lines(
            resumed,
            width.clamp(44, 78),
            theme,
        ));
    }
    lines
}

fn resumed_session_card_lines(
    resumed: &ResumedSessionSurface,
    width: usize,
    theme: &ThemePalette,
) -> Vec<Line<'static>> {
    let body = vec![
        Line::from(vec![
            Span::styled("Resumed session ", Style::default().fg(theme.text_muted)),
            Span::styled(
                resumed.session_id.clone(),
                Style::default().fg(theme.text).add_modifier(Modifier::BOLD),
            ),
        ]),
        Line::from(vec![
            Span::styled("project: ", Style::default().fg(theme.text_muted)),
            Span::styled(
                truncate_middle(&resumed.project_root, width.saturating_sub(13)),
                Style::default().fg(theme.text),
            ),
        ]),
    ];
    bordered_lines(body, width, theme)
}

pub(crate) fn render_compact_session_header(
    header: &CompactSessionHeader,
    width: usize,
    theme: &ThemePalette,
) -> Vec<Line<'static>> {
    let card_width = width.clamp(42, 72);
    bordered_lines(
        vec![
            Line::from(Span::styled(
                header.app_name.clone(),
                Style::default().fg(theme.text).add_modifier(Modifier::BOLD),
            )),
            Line::from(String::new()),
            compact_model_line(header.model.clone(), header.reasoning.clone(), theme),
            Line::from(vec![
                Span::styled("directory: ", Style::default().fg(theme.text_muted)),
                Span::styled(
                    truncate_middle(&header.workspace_path, card_width.saturating_sub(14)),
                    Style::default().fg(theme.text),
                ),
            ]),
        ],
        card_width,
        theme,
    )
}

fn compact_model_line(model: String, reasoning: String, theme: &ThemePalette) -> Line<'static> {
    let mut spans = vec![
        Span::styled("model: ", Style::default().fg(theme.text_muted)),
        Span::styled(model, Style::default().fg(theme.text)),
    ];
    if !reasoning.trim().is_empty() {
        spans.push(Span::raw(" "));
        spans.push(Span::styled(reasoning, Style::default().fg(theme.text)));
    }
    Line::from(spans)
}

/// Graphite Signal welcome hero: ANSI Shadow "block" wordmark with a baked
/// graphite gradient (tones 1..6 light->dark) and a signal-accent final row
/// (tone 7). Generated once via oh-my-logo, colors baked per row.
const HERO_WORDMARK: &[(u8, &str)] = &[
    (1, " ██████╗ ██╗    ██╗███╗   ██╗██████╗ ██╗"),
    (2, "██╔═══██╗██║    ██║████╗  ██║██╔══██╗██║"),
    (3, "██║   ██║██║ █╗ ██║██╔██╗ ██║██████╔╝██║"),
    (4, "██║   ██║██║███╗██║██║╚██╗██║██╔═══╝ ██║"),
    (5, "╚██████╔╝╚███╔███╔╝██║ ╚████║██║     ██║"),
    (7, " ╚═════╝  ╚══╝╚══╝ ╚═╝  ╚═══╝╚═╝     ╚═╝"),
];

const GRAPHITE_RAMP: [ratatui::style::Color; 6] = [
    ratatui::style::Color::Rgb(0x9a, 0xa3, 0xb2),
    ratatui::style::Color::Rgb(0x8a, 0x92, 0xa2),
    ratatui::style::Color::Rgb(0x7a, 0x82, 0x92),
    ratatui::style::Color::Rgb(0x6b, 0x72, 0x82),
    ratatui::style::Color::Rgb(0x5d, 0x64, 0x74),
    ratatui::style::Color::Rgb(0x50, 0x57, 0x66),
];

fn hero_tone_lines(rows: &[(u8, &str)], theme: &ThemePalette) -> Vec<Line<'static>> {
    let light = theme.is_light_background();
    rows.iter()
        .map(|(tone, text)| {
            let style = match tone {
                7 => Style::default().fg(theme.accent),
                t @ 1..=6 => {
                    // Dark terminals run the ramp light->dark downward; light
                    // terminals reverse it so the wordmark stays legible.
                    let idx = if light { 6 - t } else { t - 1 } as usize;
                    Style::default().fg(GRAPHITE_RAMP[idx])
                }
                _ => Style::default(),
            };
            Line::from(Span::styled((*text).to_string(), style))
        })
        .collect()
}

fn hero_rows_width(rows: &[(u8, &str)]) -> usize {
    rows.iter()
        .map(|(_, text)| UnicodeWidthStr::width(*text))
        .max()
        .unwrap_or(0)
}

/// Spaced-caps wordmark derived from the app name ("OwnPI" becomes
/// "O W N P I").
fn wordmark(app_name: &str) -> String {
    app_name
        .chars()
        .map(|ch| {
            if ch == '-' || ch == ' ' {
                "  ".to_string()
            } else {
                format!("{} ", ch.to_ascii_uppercase())
            }
        })
        .collect::<String>()
        .trim_end()
        .to_string()
}

fn welcome_info_lines(welcome: &WelcomeSurface, theme: &ThemePalette) -> Vec<Line<'static>> {
    let label_width = "directory:".len();
    let version_label = format!("{:<label_width$} ", "version:");
    let model_label = format!("{:<label_width$} ", "model:");
    let directory_label = format!("{:<label_width$} ", "directory:");
    let mut lines = vec![
        Line::from(vec![
            Span::styled(version_label, Style::default().fg(theme.text_muted)),
            Span::styled(env!("CARGO_PKG_VERSION"), Style::default().fg(theme.text)),
        ]),
        Line::from(vec![
            Span::styled(model_label, Style::default().fg(theme.text_muted)),
            Span::styled(welcome.model.clone(), Style::default().fg(theme.text)),
            Span::raw(" "),
            Span::styled(welcome.reasoning.clone(), Style::default().fg(theme.text)),
            Span::raw("   "),
            Span::styled("/model", Style::default().fg(theme.accent)),
            Span::styled(" to change", Style::default().fg(theme.text_muted)),
        ]),
        Line::from(vec![
            Span::styled(directory_label, Style::default().fg(theme.text_muted)),
            Span::styled(
                welcome.workspace_path.clone(),
                Style::default().fg(theme.text),
            ),
        ]),
    ];
    if let Some(session_id) = welcome
        .session_id
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        let session_label = format!("{:<label_width$} ", "session:");
        lines.push(Line::from(vec![
            Span::styled(session_label, Style::default().fg(theme.text_muted)),
            Span::styled(session_id.to_string(), Style::default().fg(theme.text)),
        ]));
    }
    lines
}

fn session_header_card_lines(
    welcome: &WelcomeSurface,
    width: usize,
    theme: &ThemePalette,
) -> Vec<Line<'static>> {
    let mut lines = vec![Line::from(String::new())];

    // Centered hero: stacked wordmark, falling back to spaced caps on very
    // narrow terminals. Tagline centers too; info rows stay plain (no box)
    // and center as one left-aligned block.
    let stacked_width = hero_rows_width(HERO_WORDMARK);
    let info = welcome_info_lines(welcome, theme);
    if stacked_width + 2 <= width {
        lines.extend(hero_tone_lines(HERO_WORDMARK, theme));
    } else {
        lines.push(Line::from(Span::styled(
            wordmark(&welcome.app_name),
            Style::default().fg(theme.text).add_modifier(Modifier::BOLD),
        )));
    }
    lines.push(Line::from(Span::styled(
        APP_TAGLINE,
        Style::default().fg(theme.text),
    )));
    lines.push(Line::from(String::new()));
    lines.extend(info);
    lines.push(Line::from(String::new()));
    lines
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::theme::ThemePalette;

    #[test]
    fn resumed_session_card_renders_under_welcome() {
        let welcome = WelcomeSurface {
            app_name: "OwnPI".to_string(),
            model: "gpt".to_string(),
            reasoning: "high".to_string(),
            workspace_path: "/repo".to_string(),
            session_id: None,
            resumed_session: Some(ResumedSessionSurface {
                session_id: "session-abc123".to_string(),
                project_root: "/repo".to_string(),
                latest_run_path: "/sessions/session-abc123/tasks/t/runs/r1".to_string(),
                resume_hint: "/runs".to_string(),
            }),
        };
        let lines = render_welcome_surface(&welcome, 80, &ThemePalette::default());
        let rendered = lines
            .into_iter()
            .map(|line| {
                line.spans
                    .into_iter()
                    .map(|span| span.content.into_owned())
                    .collect::<String>()
            })
            .collect::<Vec<_>>()
            .join("\n");
        assert!(
            rendered.contains("Resumed session session-abc123"),
            "{rendered}"
        );
        assert!(rendered.contains("project:"), "{rendered}");
        assert!(!rendered.contains("continue:"), "{rendered}");
        assert!(!rendered.contains("continue:"), "{rendered}");
    }

    #[test]
    fn resumed_session_card_omits_resume_hint_even_when_present() {
        let long_hint = "/runs".to_string();
        let welcome = WelcomeSurface {
            app_name: "OwnPI".to_string(),
            model: "gpt".to_string(),
            reasoning: "high".to_string(),
            workspace_path: "/repo".to_string(),
            session_id: None,
            resumed_session: Some(ResumedSessionSurface {
                session_id: "session-abc123".to_string(),
                project_root: "/repo".to_string(),
                latest_run_path: String::new(),
                resume_hint: long_hint.clone(),
            }),
        };
        let card_width = 80usize.clamp(44, 78);
        let lines = render_welcome_surface(&welcome, 80, &ThemePalette::default());
        for line in &lines {
            // The borderless hero (wordmark + instrument rules) may use the
            // full terminal width; only the resume card stays clamped.
            assert!(
                crate::history_cell::base::line_width(line) <= 80,
                "line exceeds render width 80 (card clamp {card_width}): {line:?}"
            );
        }
        let rendered = lines
            .into_iter()
            .map(|line| {
                line.spans
                    .into_iter()
                    .map(|span| span.content.into_owned())
                    .collect::<String>()
            })
            .collect::<Vec<_>>()
            .join("\n");
        assert!(!rendered.contains("continue:"), "{rendered}");
        assert!(!rendered.contains(&long_hint), "{rendered}");
    }

    #[test]
    fn no_resumed_session_no_card() {
        let welcome = WelcomeSurface {
            app_name: "OwnPI".to_string(),
            model: "gpt".to_string(),
            reasoning: String::new(),
            workspace_path: "/repo".to_string(),
            session_id: None,
            resumed_session: None,
        };
        let lines = render_welcome_surface(&welcome, 80, &ThemePalette::default());
        let rendered = lines
            .into_iter()
            .map(|line| {
                line.spans
                    .into_iter()
                    .map(|span| span.content.into_owned())
                    .collect::<String>()
            })
            .collect::<Vec<_>>()
            .join("\n");
        assert!(!rendered.contains("Resumed session"), "{rendered}");
    }

    #[test]
    fn welcome_is_visual_chrome_without_becoming_source_text() {
        let cell = WelcomeHistoryCell::new(
            WelcomeSurface {
                app_name: "OwnPI".to_string(),
                model: "gpt-5.6-terra".to_string(),
                reasoning: "high".to_string(),
                workspace_path: "~/repo".to_string(),
                session_id: Some("session-1".to_string()),
                resumed_session: None,
            },
            ThemePalette::default(),
        );

        let rendered = cell
            .display_lines(80)
            .into_iter()
            .map(|line| {
                line.spans
                    .into_iter()
                    .map(|span| span.content.into_owned())
                    .collect::<String>()
            })
            .collect::<Vec<_>>()
            .join("\n");

        assert!(rendered.contains("██████╗ ██╗    ██╗"), "{rendered}");
        assert!(rendered.contains("Your own principal investigator"));
        assert!(rendered.contains("version:   0.2.0"), "{rendered}");
        assert!(
            !rendered.lines().any(|line| line.trim() == "v0.2.0"),
            "the hero must not own a standalone version row: {rendered}"
        );
        assert!(
            rendered.find("Your own principal investigator") < rendered.find("version:   0.2.0")
                && rendered.find("version:   0.2.0") < rendered.find("model:"),
            "version metadata must be the first row above model: {rendered}"
        );
        assert!(cell.raw_text().is_none());
    }
}
