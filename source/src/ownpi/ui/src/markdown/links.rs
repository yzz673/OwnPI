use std::path::{Path, PathBuf};

/// Resolve display links without touching the filesystem or executing a URL.
/// OSC 8 only receives supported schemes and never control characters.
pub(crate) fn destination(target: &str, cwd: Option<&str>) -> Option<String> {
    if target.is_empty() || target.chars().any(char::is_control) || target.starts_with('#') {
        return None;
    }
    if target.starts_with("//") {
        return url::Url::parse(&format!("https:{target}"))
            .ok()
            .map(|url| url.to_string());
    }
    let has_line = target.rsplit_once(':').is_some_and(|(path, line)| {
        !path.contains(':')
            && !line.is_empty()
            && line.chars().all(|ch| {
                ch.is_ascii_digit() || ch.is_ascii_whitespace() || matches!(ch, '-' | ',')
            })
    });
    if !has_line {
        if let Ok(url) = url::Url::parse(target) {
            return matches!(url.scheme(), "https" | "http" | "mailto" | "file")
                .then(|| url.to_string());
        }
    }
    let (path, fragment) = target
        .split_once('#')
        .map_or((target, None), |(p, f)| (p, Some(f)));
    let (path, location) = path
        .rsplit_once(':')
        .filter(|(_, line)| {
            !line.is_empty()
                && line.chars().all(|ch| {
                    ch.is_ascii_digit() || ch.is_ascii_whitespace() || matches!(ch, '-' | ',')
                })
        })
        .map_or((path, None), |(p, l)| (p, Some(l)));
    if !looks_local(path) {
        return None;
    }
    let mut url = if let Some(relative) = path.strip_prefix("~/") {
        url::Url::from_directory_path(PathBuf::from(std::env::var_os("HOME")?))
            .ok()?
            .join(relative)
            .ok()?
    } else if Path::new(path).is_absolute() {
        url::Url::parse("file:///").ok()?.join(path).ok()?
    } else {
        let root = cwd
            .map(PathBuf::from)
            .or_else(|| std::env::current_dir().ok())?;
        url::Url::from_directory_path(root).ok()?.join(path).ok()?
    };
    if let Some(fragment) = fragment {
        url.set_fragment(Some(fragment));
    } else if let Some(location) = location {
        let location: String = location
            .chars()
            .filter(|c| !c.is_ascii_whitespace())
            .collect();
        url.set_fragment(Some(&format!("L{location}")));
    }
    Some(url.to_string())
}

fn looks_local(path: &str) -> bool {
    path.starts_with('/')
        || path.starts_with("./")
        || path.starts_with("../")
        || path.starts_with("~/")
        || path.contains('/')
        || (!path.contains(':') && Path::new(path).extension().is_some())
}

pub(super) fn display(target: &str, cwd: Option<&str>) -> Option<String> {
    if target.starts_with("//") {
        return None;
    }
    let decoded = url::Url::parse(target)
        .ok()
        .filter(|url| url.scheme() == "file")
        .and_then(|url| url.to_file_path().ok())
        .map(|path| path.to_string_lossy().into_owned());
    let target = decoded.as_deref().unwrap_or(target);
    if !looks_local(target) || url::Url::parse(target).is_ok() {
        return None;
    }
    let without_fragment = target.split('#').next().unwrap_or(target);
    if let Some(cwd) = cwd {
        if let Some(relative) = without_fragment
            .strip_prefix(cwd.trim_end_matches('/'))
            .and_then(|rest| rest.strip_prefix('/'))
            .filter(|rest| !rest.is_empty())
        {
            return Some(relative.to_string());
        }
    }
    Some(target.to_string())
}
