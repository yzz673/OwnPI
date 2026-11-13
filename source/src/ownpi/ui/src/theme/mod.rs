use ratatui::style::Color;

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct ThemePalette {
    pub(crate) color_level: TerminalColorLevel,
    pub(crate) text: Color,
    pub(crate) text_muted: Color,
    pub(crate) text_title: Color,
    pub(crate) accent: Color,
    pub(crate) accent_soft: Color,
    pub(crate) focus: Color,
    pub(crate) border: Color,
    pub(crate) success: Color,
    pub(crate) warning: Color,
    pub(crate) error: Color,
    pub(crate) surface_bg: Option<Color>,
    pub(crate) elevated_bg: Option<Color>,
    pub(crate) shadow_bg: Option<Color>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct BottomPanePalette {
    pub(crate) input_text: Color,
    pub(crate) input_muted: Color,
    pub(crate) input_accent: Color,
    pub(crate) input_bg: Color,
}

impl Default for ThemePalette {
    fn default() -> Self {
        // Graphite Signal light brand: grayscale base, single signal orange.
        Self {
            color_level: TerminalColorLevel::TrueColor,
            text: parse_hex_color("111111"),
            text_muted: parse_hex_color("374151"),
            text_title: parse_hex_color("111111"),
            accent: parse_hex_color("d14e1f"),
            accent_soft: parse_hex_color("b8431a"),
            focus: parse_hex_color("b8431a"),
            border: parse_hex_color("94a3b8"),
            success: parse_hex_color("15803d"),
            warning: parse_hex_color("b45309"),
            error: parse_hex_color("c22833"),
            surface_bg: Some(parse_hex_color("ffffff")),
            elevated_bg: Some(parse_hex_color("f8fafc")),
            shadow_bg: Some(parse_hex_color("e2e8f0")),
        }
    }
}

impl ThemePalette {
    pub(crate) fn is_light_background(&self) -> bool {
        self.elevated_bg
            .or(self.surface_bg)
            .or(self.shadow_bg)
            .is_some_and(is_light_color)
    }

    /// Keep derived surfaces at the same capability as the terminal palette.
    pub(crate) fn display_color(&self, color: Color) -> Color {
        match color {
            Color::Rgb(r, g, b) => best_color(r, g, b, self.color_level),
            other => other,
        }
    }

    fn for_level(mut self, level: TerminalColorLevel) -> Self {
        self.color_level = level;
        self.text = self.display_color(self.text);
        self.text_muted = self.display_color(self.text_muted);
        self.text_title = self.display_color(self.text_title);
        self.accent = self.display_color(self.accent);
        self.accent_soft = self.display_color(self.accent_soft);
        self.focus = self.display_color(self.focus);
        self.border = self.display_color(self.border);
        self.success = self.display_color(self.success);
        self.warning = self.display_color(self.warning);
        self.error = self.display_color(self.error);
        self.surface_bg = self.surface_bg.map(|color| self.display_color(color));
        self.elevated_bg = self.elevated_bg.map(|color| self.display_color(color));
        self.shadow_bg = self.shadow_bg.map(|color| self.display_color(color));
        self
    }

    pub(crate) fn bottom_pane(&self) -> BottomPanePalette {
        if self.is_light_background() {
            return BottomPanePalette {
                input_text: self.text,
                input_muted: self.display_color(Color::Rgb(135, 135, 140)),
                input_accent: self.accent,
                input_bg: self.display_color(Color::Rgb(242, 242, 245)),
            };
        }
        if self.elevated_bg.or(self.surface_bg).is_some() {
            return BottomPanePalette {
                input_text: self.display_color(Color::Rgb(245, 247, 255)),
                input_muted: self.display_color(Color::Rgb(172, 180, 205)),
                input_accent: self.accent_soft,
                input_bg: self
                    .elevated_bg
                    .or(self.surface_bg)
                    .unwrap_or(Color::Rgb(31, 34, 43)),
            };
        }
        BottomPanePalette {
            input_text: self.text,
            input_muted: self.text_muted,
            input_accent: self.accent,
            input_bg: self.display_color(Color::Rgb(31, 34, 43)),
        }
    }
}

impl ThemePalette {}

fn parse_hex_color(value: &str) -> Color {
    let normalized = value.trim().trim_start_matches('#');
    if normalized.len() != 6 {
        return Color::White;
    }
    let red = u8::from_str_radix(&normalized[0..2], 16).unwrap_or(255);
    let green = u8::from_str_radix(&normalized[2..4], 16).unwrap_or(255);
    let blue = u8::from_str_radix(&normalized[4..6], 16).unwrap_or(255);
    Color::Rgb(red, green, blue)
}

fn is_light_color(color: Color) -> bool {
    let Some((red, green, blue)) = color_rgb(color) else {
        return false;
    };
    let luminance =
        (0.2126 * f64::from(red)) + (0.7152 * f64::from(green)) + (0.0722 * f64::from(blue));
    luminance > 150.0
}

/// Terminal color capability used to quantize derived palette colors
/// (Codex `StdoutColorLevel` + `best_color` parity).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum TerminalColorLevel {
    TrueColor,
    Ansi256,
    Ansi16,
}

impl TerminalColorLevel {
    pub(crate) fn detect() -> Self {
        let colorterm = std::env::var("COLORTERM")
            .unwrap_or_default()
            .to_ascii_lowercase();
        if colorterm.contains("truecolor") || colorterm.contains("24bit") {
            return Self::TrueColor;
        }
        let term = std::env::var("TERM")
            .unwrap_or_default()
            .to_ascii_lowercase();
        if term.contains("256color") {
            return Self::Ansi256;
        }
        Self::Ansi16
    }
}

/// Returns the closest displayable color for the detected terminal level
/// (truecolor passthrough; xterm-256 nearest; ansi16 falls back to default).
pub(crate) fn best_color(red: u8, green: u8, blue: u8, level: TerminalColorLevel) -> Color {
    match level {
        TerminalColorLevel::TrueColor => Color::Rgb(red, green, blue),
        TerminalColorLevel::Ansi256 => Color::Indexed(nearest_xterm256(red, green, blue)),
        TerminalColorLevel::Ansi16 => Color::Reset,
    }
}

fn nearest_xterm256(red: u8, green: u8, blue: u8) -> u8 {
    let mut best_index = 16u8;
    let mut best_distance = u32::MAX;
    for (index, (r, g, b)) in xterm256_palette() {
        let distance = {
            let dr = i32::from(red) - i32::from(r);
            let dg = i32::from(green) - i32::from(g);
            let db = i32::from(blue) - i32::from(b);
            (dr * dr + dg * dg + db * db) as u32
        };
        if distance < best_distance {
            best_distance = distance;
            best_index = index;
        }
    }
    best_index
}

fn xterm256_palette() -> Vec<(u8, (u8, u8, u8))> {
    let mut palette = Vec::with_capacity(240);
    // ANSI 0..15 belong to the user's terminal theme. Even an exact RGB
    // match there is unsafe (e.g. slot 15 can be dark on a white terminal).
    const LEVELS: [u8; 6] = [0, 95, 135, 175, 215, 255];
    for r in LEVELS {
        for g in LEVELS {
            for b in LEVELS {
                let index = 16
                    + 36 * LEVELS.iter().position(|v| *v == r).unwrap() as u16
                    + 6 * LEVELS.iter().position(|v| *v == g).unwrap() as u16
                    + LEVELS.iter().position(|v| *v == b).unwrap() as u16;
                palette.push((index as u8, (r, g, b)));
            }
        }
    }
    for gray in 0..24u8 {
        let level = 8 + gray * 10;
        palette.push((232 + gray, (level, level, level)));
    }
    palette
}

/// RGB is known only for truecolor and the fixed xterm extension (16..255).
/// Never infer luminance from user-configurable ANSI slots or default colors.
pub(crate) fn color_rgb(color: Color) -> Option<(u8, u8, u8)> {
    match color {
        Color::Rgb(r, g, b) => Some((r, g, b)),
        Color::Indexed(index @ 16..=231) => {
            const LEVELS: [u8; 6] = [0, 95, 135, 175, 215, 255];
            let cube = usize::from(index - 16);
            Some((LEVELS[cube / 36], LEVELS[(cube / 6) % 6], LEVELS[cube % 6]))
        }
        Color::Indexed(index @ 232..=255) => {
            let gray = 8 + (index - 232) * 10;
            Some((gray, gray, gray))
        }
        _ => None,
    }
}

impl ThemePalette {
    /// Derive the palette from the terminal's own default fg/bg colors
    /// (OSC 10/11 probe). Falls back to the built-in light/dark pair when the
    /// probe fails. The Graphite Signal brand colors (signal orange + functional
    /// success/warning/error) are kept; text/muted/border derive from the probed
    /// foreground so the UI follows the user's terminal theme.
    pub(crate) fn from_terminal_colors(
        foreground: Option<Color>,
        background: Option<Color>,
    ) -> Self {
        Self::from_terminal_colors_for_level(foreground, background, TerminalColorLevel::detect())
    }

    /// Graphite Signal dark-terminal brand colors (light brand = Default).
    fn dark_brand() -> (Color, Color, Color, Color, Color) {
        (
            parse_hex_color("ff6b4a"), // accent / signal
            parse_hex_color("ff8563"), // accent_soft / focus
            parse_hex_color("3fb96f"), // success
            parse_hex_color("d9a13b"), // warning
            parse_hex_color("f44d5e"), // error
        )
    }

    pub(crate) fn from_terminal_colors_for_level(
        foreground: Option<Color>,
        background: Option<Color>,
        level: TerminalColorLevel,
    ) -> Self {
        let default = Self::default();
        let Some(bg) = background else {
            return default.for_level(level);
        };
        let light = is_light_color(bg);
        let fg = foreground.unwrap_or(if light {
            Color::Rgb(0x11, 0x11, 0x11)
        } else {
            Color::Rgb(0xf5, 0xf7, 0xff)
        });
        let (text_muted, border, surface_bg, elevated_bg, shadow_bg) = if light {
            (
                Color::Rgb(0x37, 0x41, 0x51),
                Color::Rgb(0x94, 0xa3, 0xb8),
                Some(bg),
                Some(Color::Rgb(0xf8, 0xfa, 0xfc)),
                Some(Color::Rgb(0xe2, 0xe8, 0xf0)),
            )
        } else {
            (
                Color::Rgb(0xac, 0xb4, 0xcd),
                Color::Rgb(0x55, 0x5c, 0x72),
                Some(bg),
                Some(Color::Rgb(0x1f, 0x22, 0x2b)),
                Some(Color::Rgb(0x2a, 0x2e, 0x3a)),
            )
        };
        let text = fg;
        let (accent, accent_soft, success, warning, error) = if light {
            (
                default.accent,
                default.accent_soft,
                default.success,
                default.warning,
                default.error,
            )
        } else {
            Self::dark_brand()
        };
        let focus = accent_soft;
        Self {
            color_level: level,
            text,
            text_muted,
            // Graphite Signal: titles are bold text, not a separate hue.
            text_title: text,
            accent,
            accent_soft,
            focus,
            border,
            success,
            warning,
            error,
            surface_bg,
            elevated_bg,
            shadow_bg,
        }
        .for_level(level)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_theme_uses_light_surface() {
        let theme = ThemePalette::default();

        assert_eq!(theme.surface_bg, Some(Color::Rgb(255, 255, 255)));
        assert!(theme.is_light_background());
    }

    #[test]
    fn terminal_dark_background_derives_light_text() {
        let theme = ThemePalette::from_terminal_colors_for_level(
            Some(Color::Rgb(0xee, 0xee, 0xee)),
            Some(Color::Rgb(0x11, 0x11, 0x11)),
            TerminalColorLevel::TrueColor,
        );

        assert!(!theme.is_light_background());
        assert_eq!(theme.text, Color::Rgb(0xee, 0xee, 0xee));
        // Dark terminals get the Graphite Signal dark brand, not the light default.
        assert_eq!(theme.accent, Color::Rgb(0xff, 0x6b, 0x4a));
        assert_eq!(theme.accent_soft, Color::Rgb(0xff, 0x85, 0x63));
        assert_eq!(theme.focus, theme.accent_soft);
        assert_eq!(theme.error, Color::Rgb(0xf4, 0x4d, 0x5e));
        // Titles follow the derived text color.
        assert_eq!(theme.text_title, theme.text);
    }

    #[test]
    fn terminal_light_background_derives_dark_text() {
        let theme = ThemePalette::from_terminal_colors_for_level(
            Some(Color::Rgb(0x22, 0x22, 0x22)),
            Some(Color::Rgb(0xff, 0xff, 0xff)),
            TerminalColorLevel::TrueColor,
        );

        assert!(theme.is_light_background());
        assert_eq!(theme.text, Color::Rgb(0x22, 0x22, 0x22));
        assert_eq!(theme.accent, Color::Rgb(0xd1, 0x4e, 0x1f));
        assert_eq!(theme.text_title, theme.text);
    }

    #[test]
    fn missing_background_falls_back_to_default_theme() {
        let theme = ThemePalette::from_terminal_colors_for_level(
            Some(Color::Rgb(1, 2, 3)),
            None,
            TerminalColorLevel::TrueColor,
        );

        assert_eq!(theme, ThemePalette::default());
    }

    #[test]
    fn best_color_passthrough_truecolor_and_quantizes_for_ansi256() {
        assert_eq!(
            best_color(1, 2, 3, TerminalColorLevel::TrueColor),
            Color::Rgb(1, 2, 3)
        );
        // Exact matches use the fixed cube, never configurable ANSI slots.
        assert_eq!(
            best_color(255, 0, 0, TerminalColorLevel::Ansi256),
            Color::Indexed(196)
        );
        // non-exact colors map to the nearest cube/gray entry
        let quantized = best_color(200, 30, 30, TerminalColorLevel::Ansi256);
        assert!(matches!(
            quantized,
            Color::Indexed(160) | Color::Indexed(124) | Color::Indexed(167) | Color::Indexed(131)
        ));
        assert_eq!(
            best_color(9, 9, 9, TerminalColorLevel::Ansi16),
            Color::Reset
        );
    }
    fn terminal_theme(level: TerminalColorLevel, light: bool) -> ThemePalette {
        let (fg, bg) = if light { (17, 255) } else { (238, 17) };
        ThemePalette::from_terminal_colors_for_level(
            Some(Color::Rgb(fg, fg, fg)),
            Some(Color::Rgb(bg, bg, bg)),
            level,
        )
    }

    #[test]
    fn ansi256_white_theme_stays_light_with_fixed_surfaces() {
        let theme = terminal_theme(TerminalColorLevel::Ansi256, true);
        assert!(theme.is_light_background());
        assert_eq!(theme.surface_bg, Some(Color::Indexed(231)));
        assert_eq!(theme.elevated_bg, Some(Color::Indexed(231)));
        assert_eq!(theme.text, Color::Indexed(233));
        assert!(!is_light_color(theme.text));
    }

    #[test]
    fn ansi256_dark_theme_stays_dark_with_light_text() {
        let theme = terminal_theme(TerminalColorLevel::Ansi256, false);
        assert!(!theme.is_light_background());
        assert!(is_light_color(theme.text));
        assert!(!is_light_color(theme.elevated_bg.unwrap()));
    }

    #[test]
    fn ansi256_palette_never_uses_configurable_ansi_slots() {
        for light in [true, false] {
            let t = terminal_theme(TerminalColorLevel::Ansi256, light);
            for color in [
                t.text,
                t.text_title,
                t.text_muted,
                t.border,
                t.accent,
                t.accent_soft,
                t.focus,
                t.success,
                t.warning,
                t.error,
                t.surface_bg.unwrap(),
                t.elevated_bg.unwrap(),
                t.shadow_bg.unwrap(),
            ] {
                assert!(matches!(color, Color::Indexed(16..=255)), "{color:?}");
            }
        }
        for (r, g, b) in [
            (0, 0, 0),
            (255, 255, 255),
            (255, 0, 0),
            (0, 255, 0),
            (0, 0, 255),
            (248, 250, 252),
            (192, 192, 192),
            (128, 128, 128),
        ] {
            assert!(matches!(
                best_color(r, g, b, TerminalColorLevel::Ansi256),
                Color::Indexed(16..=255)
            ));
        }
    }

    #[test]
    fn indexed_rgb_and_luminance_cover_fixed_cube_and_gray_ramp() {
        for (index, rgb) in xterm256_palette() {
            assert_eq!(color_rgb(Color::Indexed(index)), Some(rgb));
            assert_eq!(
                is_light_color(Color::Indexed(index)),
                is_light_color(Color::Rgb(rgb.0, rgb.1, rgb.2))
            );
        }
        for index in 0..16 {
            assert_eq!(color_rgb(Color::Indexed(index)), None);
        }
        assert_eq!(color_rgb(Color::Reset), None);
    }

    #[test]
    fn ansi256_composer_uses_light_gray_and_dark_text_on_white() {
        let theme = terminal_theme(TerminalColorLevel::Ansi256, true);
        let pane = theme.bottom_pane();
        assert_eq!(pane.input_text, theme.text);
        assert_eq!(pane.input_bg, Color::Indexed(255));
        assert!(is_light_color(pane.input_bg));
        assert!(!is_light_color(pane.input_text));
        assert!(matches!(pane.input_muted, Color::Indexed(16..=255)));
    }

    #[test]
    fn ansi256_dark_composer_keeps_dark_surface_and_light_text() {
        let pane = terminal_theme(TerminalColorLevel::Ansi256, false).bottom_pane();
        assert!(!is_light_color(pane.input_bg));
        assert!(is_light_color(pane.input_text));
    }

    #[test]
    fn missing_probe_still_respects_ansi256_capability() {
        let theme =
            ThemePalette::from_terminal_colors_for_level(None, None, TerminalColorLevel::Ansi256);
        assert!(theme.is_light_background());
        assert_eq!(theme.surface_bg, Some(Color::Indexed(231)));
        assert!(matches!(
            theme.bottom_pane().input_bg,
            Color::Indexed(16..=255)
        ));
    }

    #[test]
    fn ansi16_fallback_uses_terminal_default_instead_of_hardcoded_surfaces() {
        for bg in [
            None,
            Some(Color::Rgb(255, 255, 255)),
            Some(Color::Rgb(17, 17, 17)),
        ] {
            let theme =
                ThemePalette::from_terminal_colors_for_level(None, bg, TerminalColorLevel::Ansi16);
            assert_eq!(theme.surface_bg, Some(Color::Reset));
            assert_eq!(theme.text, Color::Reset);
            assert_eq!(theme.bottom_pane().input_bg, Color::Reset);
            assert_eq!(theme.bottom_pane().input_text, Color::Reset);
        }
    }
}
