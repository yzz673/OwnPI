//! Width guards adapted from OpenAI Codex TUI at
//! ea1e60181b9aeb4cda3d05491df4d2877ff57fd5.

pub(crate) fn usable_content_width(total_width: usize, reserved_cols: usize) -> Option<usize> {
    total_width
        .checked_sub(reserved_cols)
        .filter(|remaining| *remaining > 0)
}

#[cfg(test)]
pub(crate) fn usable_content_width_u16(total_width: u16, reserved_cols: u16) -> Option<usize> {
    usable_content_width(usize::from(total_width), usize::from(reserved_cols))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reserved_width_returns_none_instead_of_unstable_zero_width() {
        assert_eq!(usable_content_width(0, 0), None);
        assert_eq!(usable_content_width(2, 2), None);
        assert_eq!(usable_content_width(3, 4), None);
        assert_eq!(usable_content_width(5, 4), Some(1));
        assert_eq!(usable_content_width_u16(5, 4), Some(1));
    }
}
