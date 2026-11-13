//! Final shell output from confirmed Bridge facts, outside the live TUI surface.
use serde_json::Value;

#[derive(Debug)]
pub(crate) struct ExitSummary {
    status: String,
    run_id: String,
    run_dir: String,
    resume_command: String,
    recovery_reason: String,
    confirmed: bool,
}

impl ExitSummary {
    pub(crate) fn confirmed(data: &Value) -> Self {
        let string = |value: &Value| value.as_str().unwrap_or_default().to_owned();
        Self {
            status: string(&data["status"]["status"]),
            run_id: string(&data["status"]["run_id"]),
            run_dir: string(&data["status"]["run_dir"]),
            resume_command: string(&data["resume_command"]),
            recovery_reason: string(&data["recovery_reason"]),
            confirmed: true,
        }
    }

    pub(crate) fn unconfirmed() -> Self {
        Self {
            status: String::new(),
            run_id: String::new(),
            run_dir: String::new(),
            resume_command: String::new(),
            recovery_reason: String::new(),
            confirmed: false,
        }
    }

    pub(crate) fn render(&self) -> String {
        if !self.confirmed {
            return "OwnPI exited after losing the Bridge connection.\nRun state and recovery were not confirmed.\nStart ownpi-tui and use /runs to inspect recorded runs.".into();
        }
        if self.status == "idle" && self.run_id.is_empty() {
            return "OwnPI exited. No run was opened.\nTo resume a previous run, start ownpi-tui and use /resume.".into();
        }
        let mut lines = vec![if self.run_id.is_empty() {
            "OwnPI exited.".to_owned()
        } else {
            format!("Run {} · {}", self.run_id, self.status.replace('_', " "))
        }];
        if !self.resume_command.is_empty() {
            lines.push(if self.status == "completed" {
                "To reopen this completed run:".into()
            } else if self.status == "idle" {
                "To reopen this session and continue the conversation:".into()
            } else {
                "To resume this run:".into()
            });
            lines.push(format!("  {}", self.resume_command));
        } else {
            lines.push(if self.recovery_reason.is_empty() {
                "Resume unavailable: no recovery entry was confirmed.".into()
            } else {
                format!("Resume unavailable: {}", self.recovery_reason)
            });
            if !self.run_dir.is_empty() {
                lines.push(format!("Records: {}", self.run_dir));
            }
        }
        lines.join("\n")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn stopped_run_prints_the_exact_confirmed_resume_command() {
        let command = "ownpi-tui --config '/custom config.yaml' --app-home /home --project-root /work --resume run-one";
        let summary = ExitSummary::confirmed(
            &json!({"status":{"status":"stopped","run_id":"run-one"},"resume_command":command}),
        )
        .render();
        assert!(summary.starts_with("Run run-one · stopped\nTo resume this run:\n  "));
        assert!(summary.ends_with(command));
        assert!(!summary.contains("saved"));
    }

    #[test]
    fn idle_completed_and_unrecoverable_exits_have_distinct_instructions() {
        let idle = ExitSummary::confirmed(&json!({"status":{"status":"idle"}})).render();
        assert!(idle.contains("No run was opened"));
        assert!(idle.contains("/resume"));
        assert!(!idle.contains("--resume"));
        let completed = ExitSummary::confirmed(&json!({"status":{"status":"completed"},"resume_command":"ownpi-tui --resume run-done"})).render();
        assert!(completed.contains("reopen this completed run"));
        let missing = ExitSummary::confirmed(&json!({"status":{"status":"stopped","run_id":"run-early","run_dir":"/records"},"recovery_reason":"Admission has no stage resume entry"})).render();
        assert!(missing.contains("Resume unavailable: Admission has no stage resume entry"));
        assert!(missing.contains("Records: /records"));
        assert!(!missing.contains("To resume this run"));
    }

    #[test]
    fn idle_direct_session_can_be_reopened_for_followup() {
        let text =
            ExitSummary::confirmed(&json!({"status":{"status":"idle","run_id":"direct-one"},
            "resume_command":"ownpi-tui --resume direct-one"}))
            .render();
        assert!(text.contains("continue the conversation"));
        assert!(text.contains("ownpi-tui --resume direct-one"));
        assert!(!text.contains("No run was opened"));
    }

    #[test]
    fn disconnected_exit_does_not_claim_a_recoverable_or_completed_run() {
        let summary = ExitSummary::unconfirmed().render();
        assert!(summary.contains("not confirmed"));
        assert!(summary.contains("/runs"));
        assert!(!summary.contains("--resume"));
    }
}
