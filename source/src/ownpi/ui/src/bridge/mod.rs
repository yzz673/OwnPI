pub(crate) mod command_writer;
use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{self, Receiver};

use anyhow::{Context, Result};
use serde::Deserialize;
use serde_json::Value;

pub(crate) const SCHEMA: &str = "ownpi.tui.v1";

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct BridgeEvent {
    pub(crate) schema: String,
    pub(crate) seq: u64,
    pub(crate) r#type: String,
    pub(crate) request_id: String,
    pub(crate) identity: Value,
    pub(crate) data: Value,
}

pub(crate) fn spawn_bridge(args: &crate::Args) -> Result<Child> {
    let diagnostics = std::path::Path::new(&args.app_home).join("diagnostics/tui");
    std::fs::create_dir_all(&diagnostics)?;
    let log = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(diagnostics.join(format!("bridge-{}.log", std::process::id())))?;
    bridge_command(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::from(log))
        .spawn()
        .context("Could not start the OwnPI Bridge")
}

fn bridge_command(args: &crate::Args) -> Command {
    let mut command = Command::new(&args.bridge_python);
    if args.bridge_python_isolated {
        command.arg("-I");
    }
    command.args([
        "-m",
        "ownpi.tui_bridge.server",
        "--config",
        &args.config,
        "--project-root",
        &args.project_root,
        "--app-home",
        &args.app_home,
    ]);
    command
}

#[cfg(test)]
mod launch_tests {
    use super::*;
    use clap::Parser;

    #[test]
    fn preserves_python_isolation_and_argument_boundaries() {
        for isolated in [false, true] {
            let mut raw = vec![
                "tui",
                "--bridge-python",
                "/managed home/python",
                "--config",
                "/a b/config",
                "--project-root",
                "/workspace",
                "--app-home",
                "/home",
            ];
            if isolated {
                raw.push("--bridge-python-isolated");
            }
            let args = crate::Args::parse_from(raw);
            let command = bridge_command(&args);
            assert_eq!(command.get_program(), "/managed home/python");
            let values: Vec<_> = command.get_args().collect();
            assert_eq!(values[0], if isolated { "-I" } else { "-m" });
            assert!(values.iter().any(|value| *value == "/a b/config"));
        }
    }
}

pub(crate) fn reader(stdout: std::process::ChildStdout) -> Receiver<Result<BridgeEvent, String>> {
    let (tx, rx) = mpsc::sync_channel(256);
    std::thread::spawn(move || {
        let mut expected = 1;
        for line in BufReader::new(stdout).lines() {
            let parsed = line
                .map_err(|error| error.to_string())
                .and_then(|line| {
                    serde_json::from_str::<BridgeEvent>(&line).map_err(|error| error.to_string())
                })
                .and_then(|event| {
                    if event.schema != SCHEMA || event.seq != expected {
                        Err("The Bridge protocol or transport sequence is invalid".into())
                    } else {
                        expected += 1;
                        Ok(event)
                    }
                });
            let failed = parsed.is_err();
            if tx.send(parsed).is_err() || failed {
                return;
            }
        }
        let _ = tx.send(Err(
            "The OwnPI Bridge disconnected. Inspect the run before resuming.".into(),
        ));
    });
    rx
}

// Terminal diagnostics exclude request text, clipboard content and provider credentials.
pub(crate) fn append_debug_log(kind: &str, _data: &Value) {
    use std::io::Write;
    if let Ok(path) = std::env::var("OWNPI_TUI_DIAGNOSTICS") {
        if let Ok(mut file) = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)
        {
            let _ = writeln!(file, "{kind}");
        }
    }
}

pub(crate) mod protocol {
    #[derive(Clone, Debug, Default, PartialEq, Eq)]
    pub(crate) struct SkillCatalogEntry {
        pub(crate) name: String,
        pub(crate) display_name: String,
        pub(crate) description: String,
        pub(crate) path: String,
        pub(crate) enabled: bool,
        pub(crate) removable: bool,
    }
}
