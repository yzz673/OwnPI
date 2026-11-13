#![allow(clippy::large_enum_variant)]

mod agent_provenance;
mod ansi;
mod app;
mod bottom_pane;
mod branding;
mod bridge;
mod chatwidget;
mod citation_sources;
mod clipboard_copy;
mod clipboard_image;
mod command_diff;
mod command_render;
mod display_item;
mod exit_summary;
mod file_change;
mod file_search;
mod history_cell;
mod inline_terminal;
mod insert_history;
mod keymap;
mod markdown;
mod multi_agents;
mod option_picker;
mod resize_reflow_cap;
mod runtime;
mod status;
mod style;
mod syntax_highlight;
mod terminal_hyperlinks;
mod terminal_probe;
mod theme;
mod transcript_pager;
mod view;
mod width;
mod wrapping;

use anyhow::{Context, Result};
use clap::Parser;
use crossterm::terminal::enable_raw_mode;
use ratatui::backend::CrosstermBackend;

#[derive(Parser, Debug)]
pub(crate) struct Args {
    #[arg(long)]
    bridge_python: String,
    #[arg(long)]
    bridge_python_isolated: bool,
    #[arg(long)]
    config: String,
    #[arg(long)]
    project_root: String,
    #[arg(long)]
    app_home: String,
    #[arg(long)]
    resume: Option<String>,
}

fn main() -> Result<()> {
    std::panic::set_hook(Box::new(|info| {
        let _ = runtime::restore_modes();
        eprintln!("OwnPI terminal: {info}");
    }));
    let args = Args::parse();
    let mut child = bridge::spawn_bridge(&args)?;
    let stdin = child.stdin.take().context("Missing Bridge input")?;
    let rx = bridge::reader(child.stdout.take().context("Missing Bridge output")?);
    enable_raw_mode()?;
    let probe = terminal_probe::startup(terminal_probe::DEFAULT_TIMEOUT).unwrap_or_default();
    let backend = CrosstermBackend::new(std::io::stdout());
    let mut terminal = inline_terminal::InlineTerminal::new(backend)?;
    runtime::prepare_terminal(
        &mut terminal,
        probe.cursor_position.map(|point| point.y),
        probe.keyboard_enhancement_supported,
    )?;
    let mut app = app::AppState::startup(args.project_root);
    app.theme = theme::ThemePalette::from_terminal_colors(
        probe
            .foreground
            .map(|(r, g, b)| ratatui::style::Color::Rgb(r, g, b)),
        probe
            .background
            .map(|(r, g, b)| ratatui::style::Color::Rgb(r, g, b)),
    );
    app.terminal_theme = app.theme.clone();
    for character in probe.stray_input.chars() {
        app.bottom_pane.handle_key(crossterm::event::KeyEvent::new(
            crossterm::event::KeyCode::Char(character),
            crossterm::event::KeyModifiers::NONE,
        ));
    }
    let result = runtime::run(&mut terminal, &mut app, stdin, rx, args.resume);
    // Render Main once more so the handoff boundary includes the last history
    // update, even when exiting from an alternate-screen agent or request view.
    let history_end = view::draw(&mut terminal, &mut app);
    let restore = runtime::restore_modes();
    // Leaving alternate-screen modes can restore a saved editing cursor. Set
    // the shell boundary only after that restoration, before printing stdout.
    let finish = history_end.and_then(|end| terminal.finish_inline_at(end));
    let _ = child.wait();
    result?;
    restore?;
    finish?;
    let summary = app
        .exit_summary
        .unwrap_or_else(exit_summary::ExitSummary::unconfirmed);
    println!("{}", summary.render());
    Ok(())
}
