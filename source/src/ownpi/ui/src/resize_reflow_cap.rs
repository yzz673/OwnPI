//! Explicit row caps for source-backed scrollback replay.

/// Full replay is the default. `Some(0)` is retained as an explicit unbounded
/// spelling, while a positive value is an intentional user-selected cap.
pub(crate) fn resize_reflow_max_rows(configured: Option<usize>) -> Option<usize> {
    match configured {
        Some(0) => None,
        Some(rows) => Some(rows),
        None => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn explicit_cap_and_disabled_cap_are_unambiguous() {
        assert_eq!(resize_reflow_max_rows(Some(42)), Some(42));
        assert_eq!(resize_reflow_max_rows(Some(0)), None);
    }

    #[test]
    fn default_reflow_is_unbounded() {
        assert_eq!(resize_reflow_max_rows(None), None);
    }
}
