//! Internal semantic keymap.
//!
//! Input handlers resolve intent here instead of coupling UI behavior and
//! footer labels to raw `KeyCode` comparisons. This first version intentionally
//! exposes only built-in bindings; public keymap configuration is out of scope.

use crossterm::event::{KeyCode, KeyEvent, KeyEventKind, KeyModifiers};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum AppAction {
    CloseOrQuit,
    OpenTranscript,
    HistorySearch,
    ClearTerminal,
    PasteImage,
    ExternalEditor,
    SubmitOverlay,
    Cancel,
    Submit,
    PreviousField,
    NextField,
    MoveUp,
    MoveDown,
    PageUp,
    PageDown,
    JumpTop,
    JumpBottom,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum ComposerAction {
    Submit,
    HistorySearch,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum TextareaAction {
    InsertNewline,
    MoveLeft,
    MoveRight,
    MoveUp,
    MoveDown,
    DeleteBackward,
    DeleteForward,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum InteractionAction {
    Previous,
    Next,
    Accept,
    Cancel,
    PageUp,
    PageDown,
    JumpTop,
    JumpBottom,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct Binding {
    code: KeyCode,
    modifiers: KeyModifiers,
    label: &'static str,
}

impl Binding {
    const fn plain(code: KeyCode, label: &'static str) -> Self {
        Self {
            code,
            modifiers: KeyModifiers::NONE,
            label,
        }
    }

    const fn ctrl(ch: char, label: &'static str) -> Self {
        Self {
            code: KeyCode::Char(ch),
            modifiers: KeyModifiers::CONTROL,
            label,
        }
    }

    const fn modified(code: KeyCode, modifiers: KeyModifiers, label: &'static str) -> Self {
        Self {
            code,
            modifiers,
            label,
        }
    }

    fn matches(self, event: KeyEvent) -> bool {
        if !matches!(event.kind, KeyEventKind::Press | KeyEventKind::Repeat) {
            return false;
        }
        normalize(self.code, self.modifiers) == normalize(event.code, event.modifiers)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct FooterLabels {
    pub(crate) commands: &'static str,
    pub(crate) submit: &'static str,
    pub(crate) history_search: &'static str,
    pub(crate) transcript: &'static str,
}

pub(crate) struct DefaultKeymap;

impl DefaultKeymap {
    pub(crate) fn app_action(event: KeyEvent) -> Option<AppAction> {
        const BINDINGS: &[(Binding, AppAction)] = &[
            (Binding::ctrl('c', "Ctrl+C"), AppAction::CloseOrQuit),
            (Binding::ctrl('t', "Ctrl+T"), AppAction::OpenTranscript),
            (Binding::ctrl('r', "Ctrl+R"), AppAction::HistorySearch),
            (Binding::ctrl('l', "Ctrl+L"), AppAction::ClearTerminal),
            (Binding::ctrl('v', "Ctrl+V"), AppAction::PasteImage),
            (Binding::ctrl('g', "Ctrl+G"), AppAction::ExternalEditor),
            (Binding::ctrl('s', "Ctrl+S"), AppAction::SubmitOverlay),
            (Binding::plain(KeyCode::Esc, "Esc"), AppAction::Cancel),
            (Binding::plain(KeyCode::Enter, "Enter"), AppAction::Submit),
            (Binding::ctrl('m', "Ctrl+M"), AppAction::Submit),
            (
                Binding::plain(KeyCode::BackTab, "Shift+Tab"),
                AppAction::PreviousField,
            ),
            (Binding::plain(KeyCode::Tab, "Tab"), AppAction::NextField),
            (Binding::plain(KeyCode::Up, "Up"), AppAction::MoveUp),
            (Binding::plain(KeyCode::Down, "Down"), AppAction::MoveDown),
            (Binding::plain(KeyCode::PageUp, "PgUp"), AppAction::PageUp),
            (
                Binding::plain(KeyCode::PageDown, "PgDn"),
                AppAction::PageDown,
            ),
            (
                Binding::modified(KeyCode::Home, KeyModifiers::CONTROL, "Ctrl+Home"),
                AppAction::JumpTop,
            ),
            (
                Binding::modified(KeyCode::End, KeyModifiers::CONTROL, "Ctrl+End"),
                AppAction::JumpBottom,
            ),
        ];
        resolve(event, BINDINGS)
    }

    pub(crate) fn composer_action(event: KeyEvent) -> Option<ComposerAction> {
        const BINDINGS: &[(Binding, ComposerAction)] = &[
            (
                Binding::plain(KeyCode::Enter, "Enter"),
                ComposerAction::Submit,
            ),
            (Binding::ctrl('r', "Ctrl+R"), ComposerAction::HistorySearch),
        ];
        resolve(event, BINDINGS)
    }

    pub(crate) fn textarea_action(event: KeyEvent) -> Option<TextareaAction> {
        const BINDINGS: &[(Binding, TextareaAction)] = &[
            (
                Binding::modified(KeyCode::Enter, KeyModifiers::SHIFT, "Shift+Enter"),
                TextareaAction::InsertNewline,
            ),
            (
                Binding::modified(KeyCode::Enter, KeyModifiers::ALT, "Alt+Enter"),
                TextareaAction::InsertNewline,
            ),
            (
                Binding::plain(KeyCode::Left, "Left"),
                TextareaAction::MoveLeft,
            ),
            (
                Binding::plain(KeyCode::Right, "Right"),
                TextareaAction::MoveRight,
            ),
            (Binding::plain(KeyCode::Up, "Up"), TextareaAction::MoveUp),
            (
                Binding::plain(KeyCode::Down, "Down"),
                TextareaAction::MoveDown,
            ),
            (
                Binding::plain(KeyCode::Backspace, "Backspace"),
                TextareaAction::DeleteBackward,
            ),
            (
                Binding::plain(KeyCode::Delete, "Delete"),
                TextareaAction::DeleteForward,
            ),
        ];
        resolve(event, BINDINGS)
    }

    pub(crate) fn interaction_action(event: KeyEvent) -> Option<InteractionAction> {
        const BINDINGS: &[(Binding, InteractionAction)] = &[
            (Binding::ctrl('p', "Ctrl+P"), InteractionAction::Previous),
            (Binding::ctrl('k', "Ctrl+K"), InteractionAction::Previous),
            (
                Binding::plain(KeyCode::Char('k'), "k"),
                InteractionAction::Previous,
            ),
            (Binding::ctrl('n', "Ctrl+N"), InteractionAction::Next),
            (Binding::ctrl('j', "Ctrl+J"), InteractionAction::Next),
            (
                Binding::plain(KeyCode::Char('j'), "j"),
                InteractionAction::Next,
            ),
            (
                Binding::plain(KeyCode::PageUp, "PgUp"),
                InteractionAction::PageUp,
            ),
            (
                Binding::plain(KeyCode::PageDown, "PgDn"),
                InteractionAction::PageDown,
            ),
            (
                Binding::plain(KeyCode::Home, "Home"),
                InteractionAction::JumpTop,
            ),
            (
                Binding::plain(KeyCode::End, "End"),
                InteractionAction::JumpBottom,
            ),
        ];
        if let Some(action) = resolve(event, BINDINGS) {
            return Some(action);
        }
        match Self::app_action(event) {
            Some(AppAction::PreviousField | AppAction::MoveUp) => Some(InteractionAction::Previous),
            Some(AppAction::NextField | AppAction::MoveDown) => Some(InteractionAction::Next),
            Some(AppAction::Submit) => Some(InteractionAction::Accept),
            Some(AppAction::Cancel) => Some(InteractionAction::Cancel),
            _ => None,
        }
    }

    pub(crate) const fn footer_labels() -> FooterLabels {
        FooterLabels {
            commands: "/",
            submit: "Enter",
            history_search: "Ctrl+R",
            transcript: "Ctrl+T",
        }
    }
}

fn resolve<T: Copy>(event: KeyEvent, bindings: &[(Binding, T)]) -> Option<T> {
    bindings
        .iter()
        .find_map(|(binding, action)| binding.matches(event).then_some(*action))
}

fn normalize(code: KeyCode, mut modifiers: KeyModifiers) -> (KeyCode, KeyModifiers) {
    let KeyCode::Char(ch) = code else {
        return (code, modifiers);
    };
    let raw = u32::from(ch);
    if modifiers.is_empty() && (0x01..=0x1a).contains(&raw) {
        let control = char::from_u32(raw - 1 + u32::from('a')).unwrap_or(ch);
        return (KeyCode::Char(control), KeyModifiers::CONTROL);
    }
    if ch.is_ascii_uppercase() {
        modifiers.insert(KeyModifiers::SHIFT);
        return (KeyCode::Char(ch.to_ascii_lowercase()), modifiers);
    }
    (code, modifiers)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn raw_control_character_matches_semantic_binding() {
        let event = KeyEvent::new(KeyCode::Char('\u{0014}'), KeyModifiers::NONE);
        assert_eq!(
            DefaultKeymap::app_action(event),
            Some(AppAction::OpenTranscript)
        );
    }

    #[test]
    fn footer_labels_come_from_default_keymap() {
        let labels = DefaultKeymap::footer_labels();
        assert_eq!(labels.transcript, "Ctrl+T");
    }

    #[test]
    fn ctrl_o_is_not_an_application_copy_shortcut() {
        let event = KeyEvent::new(KeyCode::Char('o'), KeyModifiers::CONTROL);
        assert_eq!(DefaultKeymap::app_action(event), None);
    }

    #[test]
    fn alt_r_has_no_hidden_global_action() {
        let event = KeyEvent::new(KeyCode::Char('r'), KeyModifiers::ALT);
        assert_eq!(DefaultKeymap::app_action(event), None);
    }
}
