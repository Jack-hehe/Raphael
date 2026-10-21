//! A conservative ASCII-edit fallback over known single-line ASCII/BMP CJK pasted text.
//!
//! Nothing here may panic on a bad index: the text is the user's, and a panic message would quote
//! it. Every position is checked against the text before it is used, and anything that does not
//! add up ends the reconstruction instead.

use std::ops::Range;

use handy_keys::EditEvent;

use super::corrections::{Correction, Tracker};

/// Reconstructs only the pasted region, initially selected nowhere with its caret at the end.
/// A successful ordinary paste establishes that starting position. A custom editor moving the
/// caret without exposing UIA text cannot be reconstructed reliably; unsupported input fails closed.
pub struct Keystrokes {
    state: Option<State>,
    complete: bool,
    enter: EnterKey,
}

struct State {
    text: String,
    caret: usize,
    anchor: usize,
    tracker: Tracker,
}

/// What Enter means in the field being followed.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum EnterKey {
    /// It sends the field: a chat box, a single-line input. What is there is final.
    Submit,
    /// It may accept a suggestion or add a line, and the keys alone cannot show which (a code
    /// editor: IntelliSense turns the typed word into another one). Nothing can be learned.
    Invalidate,
}

/// What one event did to the reconstruction.
enum Step {
    /// The text changed and the tracker follows it.
    Edited,
    /// Only the caret or the selection moved.
    Moved,
    /// Enter ended the edit: what is there is final.
    Submitted,
    /// The event cannot be followed; the reconstruction must be dropped.
    Lost,
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

/// The start of the character that ends at `at`; `None` at the start of the text, and when `at`
/// is not a character boundary of it.
fn previous_boundary(text: &str, at: usize) -> Option<usize> {
    text.get(..at)?
        .char_indices()
        .next_back()
        .map(|(offset, _)| offset)
}

/// The end of the character that starts at `at`; `None` at the end of the text, and when `at` is
/// not a character boundary of it.
fn next_boundary(text: &str, at: usize) -> Option<usize> {
    text.get(at..)?
        .chars()
        .next()
        .map(|character| at + character.len_utf8())
}

/// Replaces `range` of `text` with `with`, or says no (and leaves `text` alone) unless both ends
/// are character boundaries inside it.
fn replace(text: &mut String, range: Range<usize>, with: &str) -> bool {
    let fits = range.start <= range.end
        && text.is_char_boundary(range.start)
        && text.is_char_boundary(range.end);
    if fits {
        text.replace_range(range, with);
    }
    fits
}

impl State {
    fn apply(&mut self, event: EditEvent, enter: EnterKey) -> Step {
        let start = self.caret.min(self.anchor);
        let end = self.caret.max(self.anchor);
        let selected = start != end;
        match event {
            EditEvent::Ascii(byte) if (b' '..=b'~').contains(&byte) => {
                if self.text.len().saturating_sub(end - start) >= MAX_TEXT {
                    return Step::Lost;
                }
                let mut utf8 = [0; 4];
                let typed = char::from(byte).encode_utf8(&mut utf8);
                if !replace(&mut self.text, start..end, typed) {
                    return Step::Lost;
                }
                self.caret = start + 1;
                self.anchor = self.caret;
            }
            EditEvent::Backspace | EditEvent::Delete => {
                let range = if selected {
                    Some(start..end)
                } else if event == EditEvent::Backspace {
                    previous_boundary(&self.text, self.caret).map(|at| at..self.caret)
                } else {
                    next_boundary(&self.text, self.caret).map(|at| self.caret..at)
                };
                let Some(range) = range else {
                    return Step::Lost;
                };
                self.caret = range.start;
                self.anchor = self.caret;
                if !replace(&mut self.text, range, "") {
                    return Step::Lost;
                }
            }
            EditEvent::Left { shift } | EditEvent::Right { shift } => {
                let left = matches!(event, EditEvent::Left { .. });
                let next = if selected && !shift {
                    Some(if left { start } else { end })
                } else if left {
                    previous_boundary(&self.text, self.caret)
                } else {
                    next_boundary(&self.text, self.caret)
                };
                let Some(next) = next else {
                    return Step::Lost;
                };
                self.caret = next;
                if !shift {
                    self.anchor = next;
                }
                return Step::Moved;
            }
            EditEvent::Enter => {
                return match enter {
                    EnterKey::Submit => Step::Submitted,
                    EnterKey::Invalidate => Step::Lost,
                };
            }
            _ => return Step::Lost,
        }
        self.tracker = self.tracker.advanced(&self.text);
        Step::Edited
    }
}

impl Keystrokes {
    /// For a field where Enter sends.
    #[cfg(test)]
    pub fn new(pasted: &str) -> Option<Self> {
        Self::with_enter(pasted, EnterKey::Submit)
    }

    /// Like [`Keystrokes::new`], for a field where Enter means `enter`.
    pub fn with_enter(pasted: &str, enter: EnterKey) -> Option<Self> {
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
            enter,
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
        match state.apply(event, self.enter) {
            Step::Edited | Step::Moved => {}
            Step::Submitted => self.complete = true,
            Step::Lost => self.state = None,
        }
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
    fn offsets_that_are_not_character_boundaries_find_no_neighbour() {
        let text = "A\u{4e2d}";
        // 1 and 4 are the boundaries inside and at the end; 2 and 3 split the 3-byte character.
        assert_eq!(previous_boundary(text, 1), Some(0));
        assert_eq!(previous_boundary(text, 4), Some(1));
        assert_eq!(next_boundary(text, 0), Some(1));
        assert_eq!(next_boundary(text, 1), Some(4));
        for at in [2, 3, 5, usize::MAX] {
            assert_eq!(previous_boundary(text, at), None, "{at}");
            assert_eq!(next_boundary(text, at), None, "{at}");
        }
        assert_eq!(previous_boundary(text, 0), None);
        assert_eq!(next_boundary(text, 4), None);
    }

    #[test]
    #[allow(
        clippy::reversed_empty_ranges,
        reason = "a reversed range is one of the cases"
    )]
    fn replacing_across_a_character_boundary_changes_nothing() {
        let mut text = String::from("A\u{4e2d}B");
        assert!(!replace(&mut text, 2..3, "x"));
        assert!(!replace(&mut text, 1..9, "x"));
        assert!(!replace(&mut text, 3..1, "x"));
        assert_eq!(text, "A\u{4e2d}B");
        assert!(replace(&mut text, 1..4, "x"));
        assert_eq!(text, "AxB");
    }

    #[test]
    fn a_caret_inside_a_character_ends_the_reconstruction_without_panicking() {
        let events = [
            EditEvent::Backspace,
            EditEvent::Delete,
            EditEvent::Left { shift: false },
            EditEvent::Right { shift: true },
            EditEvent::Ascii(b'x'),
        ];
        for event in events {
            let mut keys = Keystrokes::new("A\u{4e2d}B").unwrap();
            // Corrupt the position the way a logic error elsewhere might.
            let state = keys.state.as_mut().unwrap();
            state.caret = 2;
            state.anchor = 2;
            keys.apply(event);
            assert_eq!(keys.text(), None, "{event:?}");
            assert!(keys.corrections().is_empty());
        }
        for (caret, anchor) in [(99, 99), (0, 99), (99, 0)] {
            let mut keys = Keystrokes::new("abc").unwrap();
            let state = keys.state.as_mut().unwrap();
            state.caret = caret;
            state.anchor = anchor;
            keys.apply(EditEvent::Ascii(b'x'));
            assert_eq!(keys.text(), None, "{caret} {anchor}");
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
    fn enter_in_a_code_editor_may_accept_a_suggestion_so_nothing_is_learned() {
        let mut keys = Keystrokes::with_enter(PASTED, EnterKey::Invalidate).unwrap();
        for _ in 0..30 {
            keys.apply(EditEvent::Left { shift: false });
        }
        for _ in 0..6 {
            keys.apply(EditEvent::Left { shift: true });
        }
        // The user typed "Ali" and IntelliSense completed it on Enter into something else.
        typed(&mut keys, "Ali");
        keys.apply(EditEvent::Enter);
        assert_eq!(keys.text(), None);
        assert!(keys.corrections().is_empty());
        assert!(!keys.is_complete());
    }

    #[test]
    fn a_correction_that_is_not_followed_by_enter_still_counts_in_an_editor() {
        let mut keys = Keystrokes::with_enter(PASTED, EnterKey::Invalidate).unwrap();
        for _ in 0..30 {
            keys.apply(EditEvent::Left { shift: false });
        }
        for _ in 0..6 {
            keys.apply(EditEvent::Left { shift: true });
        }
        typed(&mut keys, "Alice");
        assert_eq!(
            keys.corrections(),
            [Correction {
                word: "Alice".into(),
                misheard: "Allice".into()
            }]
        );
    }

    #[test]
    fn enter_in_a_single_line_field_submits() {
        let mut keys = Keystrokes::with_enter(PASTED, EnterKey::Submit).unwrap();
        keys.apply(EditEvent::Enter);
        assert!(keys.is_complete());
        assert_eq!(keys.text(), Some(PASTED));
        // The default is the same.
        let mut keys = Keystrokes::new(PASTED).unwrap();
        keys.apply(EditEvent::Enter);
        assert!(keys.is_complete());
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

    #[test]
    fn any_sequence_of_keys_leaves_valid_text_and_never_panics() {
        // A tiny deterministic generator (xorshift): the point is to meet odd boundaries.
        let mut seed = 0x9E37_79B9_7F4A_7C15_u64;
        let mut next = move || {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            seed
        };
        let starts = [
            "abc",
            "A\u{4e2d}\u{3042}\u{ac00}z",
            "\u{4eca}\u{5929}\u{8acb}Allice\u{3002}",
        ];
        for round in 0..300 {
            for enter in [EnterKey::Submit, EnterKey::Invalidate] {
                let start = starts[round % starts.len()];
                let mut keys = Keystrokes::with_enter(start, enter).unwrap();
                for _ in 0..40 {
                    let event = match next() % 9 {
                        0 => EditEvent::Ascii(b' ' + (next() % 95) as u8),
                        1 => EditEvent::Backspace,
                        2 => EditEvent::Delete,
                        3 => EditEvent::Left {
                            shift: next() & 1 == 0,
                        },
                        4 => EditEvent::Right {
                            shift: next() & 1 == 0,
                        },
                        5 => EditEvent::Ascii(b'x'),
                        6 => EditEvent::Enter,
                        7 => EditEvent::Ascii(next() as u8),
                        _ => EditEvent::Left { shift: false },
                    };
                    keys.apply(event);
                    // Whatever was left is still a whole string, and its pairs can be asked for.
                    let _ = keys.text().map(str::len);
                    let _ = keys.corrections();
                }
            }
        }
    }
}
