pub(crate) const APP_NAME: &str = "OwnPI";
pub(crate) const APP_NAME_LOWER: &str = "ownpi";
pub(crate) const APP_TAGLINE: &str = "Your own principal investigator";
pub(crate) const TUI_BINARY_NAME: &str = "ownpi-tui";
pub(crate) const COMPOSER_PLACEHOLDER: &str = "Message OwnPI";

pub(crate) fn display_app_name(value: Option<&str>) -> String {
    match value.map(str::trim).filter(|value| !value.is_empty()) {
        None | Some("Neuro-Agent") | Some("Neuroagent") | Some("Neuro Agent") => {
            APP_NAME.to_string()
        }
        Some(value) => value.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn legacy_brand_names_render_as_ownpi() {
        assert_eq!(display_app_name(None), APP_NAME);
        assert_eq!(display_app_name(Some("Neuro-Agent")), APP_NAME);
        assert_eq!(display_app_name(Some("Neuroagent")), APP_NAME);
        assert_eq!(display_app_name(Some("Custom Product")), "Custom Product");
    }
}
