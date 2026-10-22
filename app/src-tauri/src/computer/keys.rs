//! The keys a step may press, from an allowlist: the same rules as the core's
//! (`raphael_core.computer.safety`), checked again here because this is the side that presses.
//!
//! - No Windows key at all (Win+L locks, Win+R runs, Win+D hides everything).
//! - Nothing that switches or closes windows, or opens the system's own: Alt+Tab, Alt+F4,
//!   Alt+Space, Alt+Esc, Ctrl+Esc, Ctrl+Shift+Esc, Ctrl+F4. Ctrl and Alt are never held together
//!   (that is AltGr, or the system's: Ctrl+Alt+Del).
//! - No bare letter or digit, nor one with Shift alone: that is typing, which `type` does.
//!
//! A chord is written `Ctrl+Shift+Tab`: modifiers first, then one key, any case.
//!
//! Text that a step types where no field takes it as its value goes as Unicode characters, in
//! batches ([`typing`]) between which the run's ticket is checked again; a line break is Enter.

/// A key a chord may end in.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Key {
    Enter,
    Tab,
    Escape,
    Backspace,
    Delete,
    Space,
    Insert,
    Up,
    Down,
    Left,
    Right,
    Home,
    End,
    PageUp,
    PageDown,
    /// F1 to F12.
    F(u8),
    /// A letter (lower case) or a digit.
    Char(char),
}

impl Key {
    fn parse(name: &str) -> Option<Key> {
        let key = match name {
            "enter" | "return" => Key::Enter,
            "tab" => Key::Tab,
            "escape" | "esc" => Key::Escape,
            "backspace" | "bksp" => Key::Backspace,
            "delete" | "del" => Key::Delete,
            "space" => Key::Space,
            "insert" => Key::Insert,
            "up" | "arrowup" => Key::Up,
            "down" | "arrowdown" => Key::Down,
            "left" | "arrowleft" => Key::Left,
            "right" | "arrowright" => Key::Right,
            "home" => Key::Home,
            "end" => Key::End,
            "pageup" | "pgup" | "page_up" => Key::PageUp,
            "pagedown" | "pgdn" | "page_down" => Key::PageDown,
            _ => {
                if let Some(number) = name.strip_prefix('f')
                    && let Ok(n) = number.parse::<u8>()
                    && (1..=12).contains(&n)
                    && number == n.to_string()
                {
                    return Some(Key::F(n));
                }
                let mut chars = name.chars();
                match (chars.next(), chars.next()) {
                    (Some(c), None) if c.is_ascii_lowercase() || c.is_ascii_digit() => Key::Char(c),
                    _ => return None,
                }
            }
        };
        Some(key)
    }

    /// The Windows virtual-key code, and whether it is an extended key.
    pub fn virtual_key(self) -> (u16, bool) {
        match self {
            Key::Enter => (0x0D, false),
            Key::Tab => (0x09, false),
            Key::Escape => (0x1B, false),
            Key::Backspace => (0x08, false),
            Key::Delete => (0x2E, true),
            Key::Space => (0x20, false),
            Key::Insert => (0x2D, true),
            Key::Up => (0x26, true),
            Key::Down => (0x28, true),
            Key::Left => (0x25, true),
            Key::Right => (0x27, true),
            Key::Home => (0x24, true),
            Key::End => (0x23, true),
            Key::PageUp => (0x21, true),
            Key::PageDown => (0x22, true),
            Key::F(n) => (0x70 + u16::from(n) - 1, false),
            Key::Char(c) => (c.to_ascii_uppercase() as u16, false),
        }
    }
}

/// A chord of the allowlist.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Chord {
    pub ctrl: bool,
    pub shift: bool,
    pub alt: bool,
    pub key: Key,
}

impl Chord {
    /// Whether the chord only moves away from the element with the focus (Tab, Shift+Tab, Esc):
    /// it enters, presses and sends nothing there.
    pub fn moves_away(self) -> bool {
        !self.ctrl
            && !self.alt
            && match self.key {
                Key::Tab => true,
                Key::Escape => !self.shift,
                _ => false,
            }
    }

    /// Whether the chord, pressed away from a text field, may delete what is selected there:
    /// Delete or Backspace with any modifiers but Alt (a file in File Explorer, a mail in a list,
    /// a row, a slide), and Ctrl+D alone (File Explorer, Outlook). In a field whose text can be
    /// edited they only edit it. The core's rule, word for word
    /// (`raphael_core.computer.safety.deletes_items`, which a test of the core holds to this).
    pub fn deletes_items(self) -> bool {
        match self.key {
            Key::Delete | Key::Backspace => !self.alt,
            Key::Char('d') => self.ctrl && !self.shift && !self.alt,
            _ => false,
        }
    }
}

/// A piece of typed text, as it is sent: characters (UTF-16 units, a few at a time), or a line
/// break, which is Enter.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Typed {
    Units(Vec<u16>),
    Enter,
}

/// The most UTF-16 units sent in one batch: the run's ticket is checked between batches, so a
/// run that is stopped halfway through a long text types no more of it.
pub const TYPED_BATCH_UNITS: usize = 64;

/// `text` as the batches it is typed in. A line break (`\r\n`, `\r` or `\n`) is Enter; a
/// character beyond the basic plane is never split between two batches.
pub fn typing(text: &str) -> Vec<Typed> {
    let mut batches = Vec::new();
    let mut units: Vec<u16> = Vec::new();
    let flush = |units: &mut Vec<u16>, batches: &mut Vec<Typed>| {
        if !units.is_empty() {
            batches.push(Typed::Units(std::mem::take(units)));
        }
    };
    let normal = text.replace("\r\n", "\n").replace('\r', "\n");
    for c in normal.chars() {
        if c == '\n' {
            flush(&mut units, &mut batches);
            batches.push(Typed::Enter);
            continue;
        }
        let mut buffer = [0u16; 2];
        let encoded = c.encode_utf16(&mut buffer);
        if units.len() + encoded.len() > TYPED_BATCH_UNITS {
            flush(&mut units, &mut batches);
        }
        units.extend_from_slice(encoded);
    }
    flush(&mut units, &mut batches);
    batches
}

/// Why a chord is not pressed.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Refused {
    /// Not a chord.
    Bad,
    /// It holds the Windows key.
    Windows,
    /// It switches or closes windows, or is the system's.
    System,
    /// Letters with no modifier, or with Shift alone: typing.
    Typing,
}

/// The longest a chord may be written, in characters.
const MAX_CHARS: usize = 40;

/// Reads a chord and checks it against the allowlist.
pub fn parse_chord(text: &str) -> Result<Chord, Refused> {
    if text.trim().is_empty() || text.chars().count() > MAX_CHARS {
        return Err(Refused::Bad);
    }
    let parts: Vec<String> = text
        .split('+')
        .map(|part| part.trim().to_ascii_lowercase())
        .collect();
    if parts.iter().any(|part| {
        matches!(
            part.as_str(),
            "win" | "windows" | "meta" | "super" | "cmd" | "command" | "lwin" | "rwin"
        )
    }) {
        return Err(Refused::Windows);
    }
    let (key, modifiers) = parts.split_last().ok_or(Refused::Bad)?;
    let mut chord = Chord {
        ctrl: false,
        shift: false,
        alt: false,
        key: Key::parse(key).ok_or(Refused::Bad)?,
    };
    for modifier in modifiers {
        let held = match modifier.as_str() {
            "ctrl" | "control" | "ctl" => &mut chord.ctrl,
            "shift" => &mut chord.shift,
            "alt" | "option" => &mut chord.alt,
            _ => return Err(Refused::Bad),
        };
        if *held {
            return Err(Refused::Bad);
        }
        *held = true;
    }
    check(chord)?;
    Ok(chord)
}

/// Whether a chord may be pressed (see the module).
pub fn check(chord: Chord) -> Result<(), Refused> {
    let system = match chord.key {
        Key::Tab | Key::Space => chord.alt,
        Key::F(4) | Key::Escape => chord.alt || chord.ctrl,
        _ => false,
    };
    if system || (chord.ctrl && chord.alt) {
        return Err(Refused::System);
    }
    if matches!(chord.key, Key::Char(_)) && !chord.ctrl && !chord.alt {
        return Err(Refused::Typing);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_chords_of_the_allowlist_are_read() {
        let ctrl_s = parse_chord("Ctrl+S").unwrap();
        assert!(ctrl_s.ctrl && !ctrl_s.shift && !ctrl_s.alt);
        assert_eq!(ctrl_s.key, Key::Char('s'));
        let back = parse_chord("ctrl+shift+tab").unwrap();
        assert!(back.ctrl && back.shift && back.key == Key::Tab);
        for text in [
            "Enter",
            "Tab",
            "Shift+Tab",
            "Escape",
            "Esc",
            "PageDown",
            "F5",
            "F12",
            "Alt+F",
            "Ctrl+1",
            "Up",
            "Shift+Delete",
            "Ctrl+Enter",
            "Backspace",
        ] {
            assert!(parse_chord(text).is_ok(), "{text}");
        }
    }

    #[test]
    fn the_windows_key_is_never_pressed() {
        for text in ["Win+L", "win+r", "Meta+D", "Ctrl+Win+Right", "Super"] {
            assert_eq!(parse_chord(text), Err(Refused::Windows), "{text}");
        }
    }

    #[test]
    fn chords_that_switch_close_or_belong_to_the_system_are_refused() {
        for text in [
            "Alt+Tab",
            "Alt+Shift+Tab",
            "Alt+F4",
            "Ctrl+F4",
            "Alt+Space",
            "Alt+Esc",
            "Ctrl+Esc",
            "Ctrl+Shift+Esc",
            "Ctrl+Alt+Delete",
            "Ctrl+Alt+T",
        ] {
            assert_eq!(parse_chord(text), Err(Refused::System), "{text}");
        }
    }

    #[test]
    fn letters_without_ctrl_or_alt_are_typing() {
        for text in ["a", "Shift+A", "7", "shift+1"] {
            assert_eq!(parse_chord(text), Err(Refused::Typing), "{text}");
        }
    }

    #[test]
    fn what_is_no_chord_is_refused() {
        let long = "ctrl+".repeat(10) + "s";
        for text in [
            "",
            "  ",
            "ctrl+",
            "ctrl",
            "ctrl+ctrl+s",
            "F13",
            "F0",
            "F05",
            "hyper+s",
            "ab",
            "PrintScreen",
            &long,
            "Ｓ",
        ] {
            assert_eq!(parse_chord(text), Err(Refused::Bad), "{text:?}");
        }
    }

    #[test]
    fn keys_have_their_virtual_key_codes() {
        assert_eq!(Key::Char('s').virtual_key(), (0x53, false));
        assert_eq!(Key::Char('1').virtual_key(), (0x31, false));
        assert_eq!(Key::F(1).virtual_key(), (0x70, false));
        assert_eq!(Key::F(12).virtual_key(), (0x7B, false));
        assert_eq!(Key::Delete.virtual_key(), (0x2E, true));
        assert_eq!(Key::Enter.virtual_key(), (0x0D, false));
    }

    #[test]
    fn only_tab_shift_tab_and_esc_move_away() {
        for text in ["Tab", "Shift+Tab", "Esc"] {
            assert!(parse_chord(text).unwrap().moves_away(), "{text}");
        }
        for text in [
            "Enter",
            "Space",
            "Ctrl+V",
            "Ctrl+Tab",
            "Backspace",
            "Shift+Esc",
        ] {
            assert!(!parse_chord(text).unwrap().moves_away(), "{text}");
        }
    }

    #[test]
    fn delete_backspace_and_ctrl_d_may_delete_what_is_selected() {
        for text in [
            "Delete",
            "Ctrl+Delete",
            "Shift+Delete",
            "Ctrl+Shift+Delete",
            "Backspace",
            "Ctrl+Backspace",
            "Shift+Backspace",
            "Ctrl+D",
        ] {
            assert!(parse_chord(text).unwrap().deletes_items(), "{text}");
        }
        for text in [
            "Alt+Delete",
            "Alt+Backspace",
            "Ctrl+Shift+D",
            "Alt+D",
            "Enter",
            "Ctrl+X",
            "Left",
        ] {
            assert!(!parse_chord(text).unwrap().deletes_items(), "{text}");
        }
    }

    #[test]
    fn text_is_typed_in_batches_and_a_line_break_is_enter() {
        let units = |text: &str| Typed::Units(text.encode_utf16().collect());
        assert_eq!(typing("七"), [units("七")]);
        for text in ["一\n二", "一\r\n二", "一\r二"] {
            assert_eq!(
                typing(text),
                [units("一"), Typed::Enter, units("二")],
                "{text:?}"
            );
        }
        assert_eq!(typing("\n"), [Typed::Enter]);
        // a long text goes in batches, and a character beyond the basic plane is never split
        let long = format!("{}𠮷", "a".repeat(TYPED_BATCH_UNITS - 1));
        let batches = typing(&long);
        assert_eq!(batches.len(), 2);
        assert_eq!(batches[1], units("𠮷"));
        for batch in &batches {
            if let Typed::Units(units) = batch {
                assert!(units.len() <= TYPED_BATCH_UNITS);
            }
        }
    }
}
