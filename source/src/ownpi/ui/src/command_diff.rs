//! Read-only repository diff used by the command document.
use std::path::Path;
use std::process::{Command, Output};

fn git(root: &Path, args: &[&str]) -> std::io::Result<Output> {
    Command::new("git").arg("-C").arg(root).args(args).output()
}

pub(crate) fn working_tree_diff(root: &Path) -> String {
    let head = git(root, &["rev-parse", "--verify", "HEAD"]);
    let has_head = head.as_ref().is_ok_and(|value| value.status.success());
    let diff = git(
        root,
        if has_head {
            &["diff", "--no-ext-diff", "HEAD", "--"]
        } else {
            &["diff", "--no-ext-diff", "--"]
        },
    );
    let staged = (!has_head).then(|| git(root, &["diff", "--no-ext-diff", "--cached", "--"]));
    let untracked = git(root, &["ls-files", "--others", "--exclude-standard"]);
    let mut text = String::new();
    for (index, output) in [Some(diff), staged, Some(untracked)]
        .into_iter()
        .enumerate()
        .filter_map(|(i, v)| v.map(|value| (i, value)))
    {
        match output {
            Ok(output) if output.status.success() => {
                let chunk = String::from_utf8_lossy(&output.stdout);
                if !chunk.trim().is_empty() {
                    if !text.is_empty() {
                        text.push('\n');
                    }
                    if index == 2 {
                        text.push_str("# Untracked files\n");
                    }
                    text.push_str(&chunk);
                }
            }
            Ok(output) => {
                return format!(
                    "git diff failed: {}",
                    String::from_utf8_lossy(&output.stderr).trim()
                )
            }
            Err(error) => return format!("git diff failed: {error}"),
        }
    }
    if text.trim().is_empty() {
        "Working tree clean.".into()
    } else {
        format!("```diff\n{text}\n```")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};
    static NEXT: AtomicU64 = AtomicU64::new(0);
    #[test]
    fn includes_staged_unstaged_untracked_and_unborn_repositories() {
        let root = std::env::temp_dir().join(format!(
            "ownpi-command-diff-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir_all(&root).unwrap();
        assert!(git(&root, &["init", "-q"]).unwrap().status.success());
        std::fs::write(root.join("report.md"), "unborn-stage-marker\n").unwrap();
        git(&root, &["add", "report.md"]).unwrap();
        std::fs::write(root.join("untracked.md"), "not staged").unwrap();
        let unborn = working_tree_diff(&root);
        assert!(
            unborn.contains("unborn-stage-marker") && unborn.contains("untracked.md"),
            "{unborn}"
        );
        assert!(git(
            &root,
            &[
                "-c",
                "user.name=Menu Test",
                "-c",
                "user.email=menu@localhost",
                "commit",
                "-qm",
                "fixture"
            ]
        )
        .unwrap()
        .status
        .success());
        std::fs::write(root.join("report.md"), "staged-marker\n").unwrap();
        git(&root, &["add", "report.md"]).unwrap();
        std::fs::write(root.join("report.md"), "staged-marker\nunstaged-marker\n").unwrap();
        let result = working_tree_diff(&root);
        assert!(
            result.contains("staged-marker")
                && result.contains("unstaged-marker")
                && result.contains("untracked.md"),
            "{result}"
        );
        std::fs::remove_dir_all(root).unwrap();
    }
}
