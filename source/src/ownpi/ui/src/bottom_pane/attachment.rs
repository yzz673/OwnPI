use std::path::PathBuf;

/// Local counterpart of Codex `UserInput::LocalImage { path, detail }` (codex-rs/protocol/src/user_input.rs).
/// Attachment paths live in a side table; `[Image #N]` is only a visible textarea handle.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub(crate) enum ImageDetail {
    #[default]
    Auto,
    Original,
}

impl ImageDetail {
    pub(crate) fn as_str(self) -> &'static str {
        match self {
            Self::Auto => "auto",
            Self::Original => "original",
        }
    }

    pub(crate) fn parse(value: &str) -> Self {
        match value.trim().to_ascii_lowercase().as_str() {
            "original" => Self::Original,
            _ => Self::Auto,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct LocalImageAttachment {
    /// Atomic placeholder text in the textarea, such as `[Image #1]`.
    pub(crate) placeholder: String,
    pub(crate) path: PathBuf,
    pub(crate) detail: ImageDetail,
}

impl LocalImageAttachment {
    pub(crate) fn to_bridge_json(&self) -> serde_json::Value {
        serde_json::json!({
            "placeholder": self.placeholder,
            "path": self.path.to_string_lossy(),
            "detail": self.detail.as_str(),
        })
    }
}

/// Local counterpart of Codex `UserInput::Image { image_url }`: a remote image URL attachment.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct RemoteImageAttachment {
    pub(crate) placeholder: String,
    pub(crate) url: String,
}

impl RemoteImageAttachment {
    pub(crate) fn to_bridge_json(&self) -> serde_json::Value {
        serde_json::json!({
            "placeholder": self.placeholder,
            "url": self.url,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detail_bridge_names() {
        assert_eq!(ImageDetail::Auto.as_str(), "auto");
        assert_eq!(ImageDetail::Original.as_str(), "original");
    }

    #[test]
    fn attachment_serializes_to_bridge_shape() {
        let attachment = LocalImageAttachment {
            placeholder: "[Image #1]".to_string(),
            path: PathBuf::from("/tmp/a.png"),
            detail: ImageDetail::Auto,
        };
        let json = attachment.to_bridge_json();
        assert_eq!(json["placeholder"], "[Image #1]");
        assert_eq!(json["path"], "/tmp/a.png");
        assert_eq!(json["detail"], "auto");
    }
}
