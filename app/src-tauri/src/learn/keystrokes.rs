//! A conservative ASCII-edit fallback over known single-line ASCII/BMP CJK pasted text.

use handy_keys::EditEvent;

use super::corrections::{Correction, Tracker};

/// Reconstructs only the pasted region, initially selected nowhere with its caret at the end.
/// A successful ordinary paste establishes that starting position. A custom editor moving the
/// caret without exposing UIA text cannot be reconstructed reliably; unsupported input fails closed.
pub struct Keystrokes {
    state: Option<State>,
    complete: bool,
}

struct State {
    text: String,
    caret: usize,
    anchor: usize,
    tracker: Tracker,
}

const MAX_TEXT: usize = 20_000;

fn known_character(character: char) -> bool {
    matches!(character,
        ' '..='~'
        | '\u{3000}'..='\u{3029}'
        | '\u{3030}'..='\u{303F}'
        | '\u{3041}'..='\u{3096}'
        | '\u{30A0}'..='\u{30FF}'
        | '\u{31F0}'..='\u{31FF}'
        | '\u{3400}'..='\u{4DBF}'
        | '\u{4E00}'..='\u{9FFF}'
        | '\u{AC00}'..='\u{D7A3}'
        | '\u{F900}'..='\u{FAFF}'
        | '\u{FF01}'..='\u{FF9D}'
    )
}

fn previous_boundary(text: &str, at: usize) -> Option<usize> {
    text[..at]
        .char_indices()
        .next_back()
        .map(|(offset, _)| offset)
}

fn next_boundary(text: &str, at: usize) -> Option<usize> {
    text[at..]
        .chars()
        .next()
        .map(|character| at + character.len_utf8())
}

impl Keystrokes {
    pub fn new(pasted: &str) -> Option<Self> {
        if pasted.is_empty() || pasted.len() > MAX_TEXT || !pasted.chars().all(known_character) {
            return None;
        }
        Some(Self {
            state: Some(State {
                text: pasted.to_owned(),
                caret: pasted.len(),
                anchor: pasted.len(),
                tracker: Tracker::new(pasted, pasted)?,
            }),
            complete: false,
        })
    }

    pub fn apply(&mut self, event: EditEvent) {
        // A late safety invalidation must also discard an already submitted draft.
        if event == EditEvent::Invalidated {
            self.state = None;
            return;
        }
        if self.complete {
            return;
        }
        let Some(state) = self.state.as_mut() else {
            return;
        };
        let start = state.caret.min(state.anchor);
        let end = state.caret.max(state.anchor);
        let selected = start != end;
        match event {
            EditEvent::Ascii(byte) if (b' '..=b'~').contains(&byte) => {
                if state.text.len() - (end - start) == MAX_TEXT {
                    self.state = None;
                    return;
                }
                state
                    .text
                    .replace_range(start..end, char::from(byte).encode_utf8(&mut [0; 4]));
                state.caret = start + 1;
                state.anchor = state.caret;
            }
            EditEvent::Backspace | EditEvent::Delete => {
                let range = if selected {
                    start..end
                } else if event == EditEvent::Backspace && state.caret > 0 {
                    previous_boundary(&state.text, state.caret).unwrap()..state.caret
                } else if event == EditEvent::Delete && state.caret < state.text.len() {
                    state.caret..next_boundary(&state.text, state.caret).unwrap()
                } else {
                    self.state = None;
                    return;
                };
                state.caret = range.start;
                state.anchor = state.caret;
                state.text.replace_range(range, "");
            }
            EditEvent::Left { shift } | EditEvent::Right { shift } => {
                let left = matches!(event, EditEvent::Left { .. });
                let next = if selected && !shift {
                    Some(if left { start } else { end })
                } else if left {
                    previous_boundary(&state.text, state.caret)
                } else {
                    next_boundary(&state.text, state.caret)
                };
                let Some(next) = next else {
                    self.state = None;
                    return;
                };
                state.caret = next;
                if !shift {
                    state.anchor = next;
                }
                return;
            }
            EditEvent::Enter => {
                self.complete = true;
                return;
            }
            _ => {
                self.state = None;
                return;
            }
        }
        state.tracker = state.tracker.advanced(&state.text);
    }

    pub fn text(&self) -> Option<&str> {
        self.state.as_ref().map(|state| state.text.as_str())
    }

    pub fn corrections(&self) -> Vec<Correction> {
        self.state
            .as_ref()
            .map(|state| state.tracker.corrections())
            .unwrap_or_default()
    }

    pub fn is_complete(&self) -> bool {
        self.complete
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const PASTED: &str = "Please ask Allice about the release plan today.";

    fn typed(keys: &mut Keystrokes, text: &str) {
        for byte in text.bytes() {
            keys.apply(EditEvent::Ascii(byte));
        }
    }

    #[test]
    fn shift_selection_replacement_preserves_one_whole_correction() {
        let mut keys = Keystrokes::new(PASTED).unwrap();
        for _ in 0..30 {
            keys.apply(EditEvent::Left { shift: false });
        }
        for _ in 0..6 {
            keys.apply(EditEvent::Left { shift: true });
        }
        typed(&mut keys, "Alice");
        keys.apply(EditEvent::Enter);
        assert_eq!(
            keys.text(),
            Some("Please ask Alice about the release plan today.")
        );
        assert_eq!(
            keys.corrections(),
            [Correction {
                word: "Alice".into(),
                misheard: "Allice".into()
            }]
        );
        assert!(keys.is_complete());
        typed(&mut keys, "ignored");
        assert_eq!(
            keys.text(),
            Some("Please ask Alice about the release plan today.")
        );
    }

    #[test]
    fn backspace_delete_and_arrows_reconstruct_literal_edits() {
        let mut keys = Keystrokes::new("abcd").unwrap();
        keys.apply(EditEvent::Backspace);
        keys.apply(EditEvent::Left { shift: false });
        keys.apply(EditEvent::Delete);
        typed(&mut keys, "C!");
        assert_eq!(keys.text(), Some("abC!"));
    }

    #[test]
    fn deleting_one_misheard_letter_keeps_the_whole_latin_correction() {
        for (left, deletion) in [(33, EditEvent::Backspace), (34, EditEvent::Delete)] {
            let mut keys = Keystrokes::new(PASTED).unwrap();
            for _ in 0..left {
                keys.apply(EditEvent::Left { shift: false });
            }
            keys.apply(deletion);
            assert_eq!(
                keys.text(),
                Some("Please ask Alice about the release plan today.")
            );
            assert_eq!(
                keys.corrections(),
                [Correction {
                    word: "Alice".into(),
                    misheard: "Allice".into()
                }]
            );
        }
    }

    #[test]
    fn a_late_invalidation_discards_a_completed_correction() {
        let mut keys = Keystrokes::new(PASTED).unwrap();
        for _ in 0..33 {
            keys.apply(EditEvent::Left { shift: false });
        }
        keys.apply(EditEvent::Backspace);
        keys.apply(EditEvent::Enter);
        keys.apply(EditEvent::Invalidated);
        assert_eq!(keys.text(), None);
        assert!(keys.corrections().is_empty());
    }

    #[test]
    fn unshifted_arrows_collapse_a_selection_before_moving_again() {
        let mut keys = Keystrokes::new("abcd").unwrap();
        keys.apply(EditEvent::Left { shift: true });
        keys.apply(EditEvent::Left { shift: true });
        keys.apply(EditEvent::Right { shift: false });
        keys.apply(EditEvent::Backspace);
        assert_eq!(keys.text(), Some("abc"));
    }

    #[test]
    fn selection_can_reverse_and_delete_or_backspace() {
        for event in [EditEvent::Delete, EditEvent::Backspace] {
            let mut keys = Keystrokes::new("abcd").unwrap();
            keys.apply(EditEvent::Left { shift: true });
            keys.apply(EditEvent::Left { shift: true });
            keys.apply(EditEvent::Right { shift: true });
            keys.apply(event);
            assert_eq!(keys.text(), Some("abc"));
        }
    }

    #[test]
    fn crossing_the_known_pasted_region_invalidates_the_whole_reconstruction() {
        for event in [EditEvent::Backspace, EditEvent::Left { shift: false }] {
            let mut keys = Keystrokes::new("a").unwrap();
            keys.apply(EditEvent::Left { shift: false });
            keys.apply(event);
            assert_eq!(keys.text(), None);
            assert!(keys.corrections().is_empty());
        }
        let mut keys = Keystrokes::new("a").unwrap();
        keys.apply(EditEvent::Right { shift: false });
        assert_eq!(keys.text(), None);
    }

    #[test]
    fn ime_controls_and_unknown_events_never_produce_a_correction() {
        for pasted in [
            "",
            "line\nline",
            "a\tb",
            "\u{1f600}",
            "a\u{0301}",
            "\u{3099}",
            "\u{1100}\u{1161}",
        ] {
            assert!(Keystrokes::new(pasted).is_none());
        }
        for event in [
            EditEvent::Ascii(0x80),
            EditEvent::Ascii(b'\n'),
            EditEvent::Invalidated,
        ] {
            let mut keys = Keystrokes::new(PASTED).unwrap();
            keys.apply(event);
            assert_eq!(keys.text(), None);
            assert!(keys.corrections().is_empty());
        }
    }

    #[test]
    fn known_cjk_paste_allows_ascii_corrections_using_character_boundaries() {
        let mut keys = Keystrokes::new("\u{4eca}\u{5929}\u{8acb}Allice\u{6aa2}\u{67e5}\u{767c}\u{4f48}\u{8a08}\u{756b}\u{4e26}\u{56de}\u{5831}\u{7d50}\u{679c}\u{3002}").unwrap();
        for _ in 0..12 {
            keys.apply(EditEvent::Left { shift: false });
        }
        for _ in 0..6 {
            keys.apply(EditEvent::Left { shift: true });
        }
        typed(&mut keys, "Alice");
        keys.apply(EditEvent::Enter);
        assert_eq!(
            keys.text(),
            Some(
                "\u{4eca}\u{5929}\u{8acb}Alice\u{6aa2}\u{67e5}\u{767c}\u{4f48}\u{8a08}\u{756b}\u{4e26}\u{56de}\u{5831}\u{7d50}\u{679c}\u{3002}"
            )
        );
        assert_eq!(
            keys.corrections(),
            [Correction {
                word: "Alice".into(),
                misheard: "Allice".into()
            }]
        );
    }

    #[test]
    fn backspace_and_delete_remove_one_known_bmp_character() {
        let mut keys = Keystrokes::new("A\u{4e2d}\u{3042}\u{ac00}").unwrap();
        keys.apply(EditEvent::Backspace);
        keys.apply(EditEvent::Left { shift: false });
        keys.apply(EditEvent::Delete);
        assert_eq!(keys.text(), Some("A\u{4e2d}"));
    }
}
