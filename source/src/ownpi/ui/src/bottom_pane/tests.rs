//! Tests for the bottom pane state machine.
pub(super) use super::*;
mod command_document;
mod file_popup;
mod option_picker;
mod overlay_routing;
mod skill_popup;
mod slash_commands;
fn rendered_active_popup_lines(pane: &BottomPaneState, width: u16, height: u16) -> Vec<String> {
    let area = Rect::new(0, 0, width, height);
    let mut buffer = Buffer::empty(area);
    pane.render_active_popup(area, &mut buffer, &ThemePalette::default());
    (0..height)
        .map(|y| {
            let mut line = String::new();
            for x in 0..width {
                line.push_str(buffer.get(x, y).symbol());
            }
            line.trim_end().to_string()
        })
        .filter(|line| !line.is_empty())
        .collect()
}

fn model_catalog() -> Vec<ServiceTierCommand> {
    popup_catalog()
}

fn file_matches(query: &str) -> Vec<FileMatch> {
    match query {
        "main" => vec![FileMatch {
            path: "src/main.rs".to_string(),
            is_dir: false,
            match_indices: Some(vec![4, 5, 6, 7]),
        }],
        "space" => vec![FileMatch {
            path: "space file.md".to_string(),
            is_dir: false,
            match_indices: Some(vec![0, 1, 2, 3, 4]),
        }],
        _ => vec![
            FileMatch {
                path: "notes.md".to_string(),
                is_dir: false,
                match_indices: Some(vec![0]),
            },
            FileMatch {
                path: "src/main.rs".to_string(),
                is_dir: false,
                match_indices: Some(vec![6]),
            },
        ],
    }
}

fn skill_catalog() -> Vec<crate::bridge::protocol::SkillCatalogEntry> {
    vec![
        crate::bridge::protocol::SkillCatalogEntry {
            name: "expression-skill".to_string(),
            display_name: "Expression Skill".to_string(),
            description: "Communication defaults".to_string(),
            path: "/Users/me/.codex/skills/expression-skill/SKILL.md".to_string(),
            enabled: true,
            removable: true,
        },
        crate::bridge::protocol::SkillCatalogEntry {
            name: "daily-coding".to_string(),
            display_name: "Daily Coding".to_string(),
            description: "Coding checklist".to_string(),
            path: "/Users/me/.codex/skills/daily-coding/SKILL.md".to_string(),
            enabled: true,
            removable: true,
        },
        crate::bridge::protocol::SkillCatalogEntry {
            name: "disabled".to_string(),
            display_name: "Disabled".to_string(),
            description: "Hidden".to_string(),
            path: "/tmp/SKILL.md".to_string(),
            enabled: false,
            removable: false,
        },
    ]
}

fn popup_catalog() -> Vec<ServiceTierCommand> {
    vec![
        ServiceTierCommand::catalog_entry("model", "model", "Pick model", "ui.popup", "prompt"),
        ServiceTierCommand::catalog_entry(
            "models",
            "models",
            "List models",
            "ui.surface",
            "prompt",
        ),
        ServiceTierCommand::catalog_entry(
            "reasoning",
            "reasoning",
            "Pick reasoning",
            "ui.popup",
            "prompt",
        ),
        ServiceTierCommand::catalog_entry("mode", "mode", "Switch UI mode", "ui.popup", "prompt"),
    ]
}
